#include "application.h"

#include "board.h"
#include "display.h"

#include <esp_log.h>
#include <esp_mac.h>
#include <esp_heap_caps.h>
#include <esp_system.h>
#include <esp_timer.h>
#include <esp_sleep.h>
#include <nvs.h>
#include <nvs_flash.h>
#include <mbedtls/base64.h>
#include <algorithm>
#include <cstdio>
#include <cstring>

#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

#include "cJSON.h"
#include "network_interface.h"
#include "web_socket.h"
#include "wifi_manager.h"
#include "audio_codec.h"
#include "audio/wake_endpoint.h"
#if CONFIG_POCKETCLAW_WAKE_WORD
#include "model_path.h"
#include "esp_wn_models.h"
#endif
#include "display.h"
#include "common/tap_friend.h"
#include "focus_page.h"
#include "common/sleep_manager.h"
#include <vector>

namespace {

constexpr char kTag[] = "Application";
// 编译期默认地址：当 NVS 未手动配置时兜底使用（推荐 Mac Mini 家庭服务器固定 mDNS 或局域网静态 IP）
constexpr char kDefaultServerUrlLan[] = "ws://macmini.local:8000/pocketclaw";
constexpr char kDefaultServerUrlWan[] = "";  // 可在配网 AP 网页(/advanced)或 NVS 设置远程公网/隧道地址

}  // namespace

Application::Application() = default;

Application::~Application() = default;

void Application::Initialize() {
    auto& board = Board::GetInstance();
    SetDeviceState(kDeviceStateStarting);

    codec_ = board.GetAudioCodec();
    if (codec_ == nullptr) {
        ESP_LOGE(kTag, "Audio codec is null");
        SetDeviceState(kDeviceStateFatalError);
        return;
    }
    codec_->Start();                 // L2b/c：直接管 codec（裸 PCM16，绕开 Opus AudioService）
    codec_->EnableInput(false);
    codec_->EnableOutput(false);
    codec_->SetOutputVolume(85);
    codec_->SetInputGain(30.0f);     // ES8311 麦克风硬件增益提高到 +30dB，避免识别为静音

    // L2c：TTS 播放抖动缓冲（约 1s @16k/16bit=32KB）。网络收帧只管入缓冲，
    // 专用播放任务出缓冲喂 I2S —— 把「收」和「播」解耦，抗 WiFi 抖动/墨水屏刷新抢占。
    audio_rb_ = xStreamBufferCreate(32768, 1);
    if (audio_rb_ == nullptr) {
        ESP_LOGE(kTag, "音频抖动缓冲创建失败");
    }

    Display* display = board.GetDisplay();
    if (display != nullptr) {
        display->UpdateStatusBar(true);
    }

    SetDeviceState(kDeviceStateIdle);
    board.StartNetwork();   // L1：不再进工厂测试，改为连 WiFi
}

void Application::Run() {
    auto& board = Board::GetInstance();
    auto& wifi = WifiManager::GetInstance();

    // 建 WebSocket 对象一次；断线后复用同一对象重连（Connect 内部会重建 tcp_，
    // 复用对象可避免与 MicTask 竞争裸指针）。回调也只绑一次。
    ws_.reset(board.GetNetwork()->CreateWebSocket().release());
    if (!ws_) {
        ESP_LOGE(kTag, "CreateWebSocket 返回空（网络后端未就绪？）");
        while (true) { vTaskDelay(portMAX_DELAY); }
    }
    ws_->OnConnected([this]() {
        ESP_LOGI(kTag, "WS 已连，发 hello");
        SetDeviceState(kDeviceStateIdle);   // 连上待命
        SendHello();
        RenderPage("小龙虾便利贴", {}, {"我在啦，按住中间键说话吧"});
    });
    ws_->OnData([this](const char* data, size_t len, bool binary) {
        if (binary) {
            OnAudioData(data, len);
        } else {
            OnControlFrame(data, len);
        }
        (void)len;
    });
    ws_->OnDisconnected([this]() {
        ESP_LOGW(kTag, "WS 断开 → 自动重连");
        if (codec_) { codec_->EnableInput(false); codec_->EnableOutput(false); }
        wake_recording_.store(false);
        ResetTransferState("ws_disconnect");   // 关键：清掉半截传输态，否则重连后音频会被永久吞掉
        SetDeviceState(kDeviceStateConnecting);
    });
    ws_->OnError([](int e) { ESP_LOGE(kTag, "WS 错误 %d", e); });

    // L2b：录音上行任务只建一次（内部按连接状态自守，断线期间空转）
    xTaskCreate([](void* arg) { static_cast<Application*>(arg)->MicTask(); },
                "ptt_mic", 8192, this, 5, nullptr);

    // L2c：TTS 播放任务。钉在 core 1、优先级 6（高于墨水屏刷新任务 prio 3）——
    // 这样播报时哪怕在整屏刷新，音频也能抢到 CPU 及时喂 DMA，不再卡顿。
    xTaskCreatePinnedToCore([](void* arg) { static_cast<Application*>(arg)->AudioPlayTask(); },
                            "audio_play", 4096, this, 6, nullptr, 1);

    // 加载局域网与远程备用伴学服务地址（支持家庭内网与跨Wi-Fi/移动热点双端点智能故障转移）
    LoadServerUrlsFromNvs();

    std::vector<std::string> target_urls;
    if (!server_url_primary_.empty()) {
        target_urls.push_back(server_url_primary_);
    }
    if (!server_url_backup_.empty() && server_url_backup_ != server_url_primary_) {
        target_urls.push_back(server_url_backup_);
    }
    if (target_urls.empty()) {
        target_urls.push_back(kDefaultServerUrlLan);
    }
    size_t url_idx = 0;
    int backoff_ms = 1000;
    int fail_count = 0;
    last_heartbeat_ms_ = esp_timer_get_time() / 1000;

    // 主循环：保持 WiFi + WS 常连。任一断开自动重连，指数退避封顶 15s。
    // 在家庭局域网与跨 Wi-Fi / 外网公网（DDNS/frp/Cloudflare隧道）双端点间智能轮转。
    while (true) {
        if (!wifi.IsConnected()) {
            ESP_LOGW(kTag, "WiFi 未连，等待…");
            SetDeviceState(kDeviceStateConnecting);
            while (!wifi.IsConnected()) { vTaskDelay(pdMS_TO_TICKS(500)); }
            ESP_LOGI(kTag, "WiFi 已连 IP=%s", wifi.GetIpAddress().c_str());
            // 切换 Wi-Fi 后优先重试主地址（如放学回到家中 Wi-Fi 时秒切内网）
            url_idx = 0;
        }
        if (!ws_->IsConnected()) {
            SetDeviceState(kDeviceStateConnecting);
            ws_->Close();   // 释放上次残留连接，避免弱信号下握手超时把栈卡死

            const std::string& current_url = target_urls[url_idx];
            ESP_LOGI(kTag, "连接伴学服务端 [%zu/%zu]: %s",
                     url_idx + 1, target_urls.size(), current_url.c_str());

            if (!device_auth_token_.empty()) {
                // Never send a provisioned bearer token over plaintext WebSocket.
                if (current_url.rfind("wss://", 0) != 0) {
                    ESP_LOGE(kTag, "设备凭据需要 WSS；跳过非加密服务器地址");
                    if (target_urls.size() > 1) url_idx = (url_idx + 1) % target_urls.size();
                    vTaskDelay(pdMS_TO_TICKS(backoff_ms));
                    continue;
                }
                std::string auth = "Bearer " + device_auth_token_;
                ws_->SetHeader("Authorization", auth.c_str());
            }
            if (ws_->Connect(current_url.c_str())) {   // 同步：返回 true 即握手完成、hello 已发
                ESP_LOGI(kTag, "伴学服务连接成功！(%s)", current_url.c_str());
                backoff_ms = 1000;
                fail_count = 0;
                last_heartbeat_ms_ = esp_timer_get_time() / 1000;
            } else {
                fail_count++;
                ESP_LOGW(kTag, "WS 连接失败 [%zu/%zu](第 %d 次: %s)",
                         url_idx + 1, target_urls.size(), fail_count, current_url.c_str());

                // 智能故障转移：若配置了多个地址（如局域网 + 远程公网/隧道），立即轮换候选地址
                if (target_urls.size() > 1) {
                    url_idx = (url_idx + 1) % target_urls.size();
                }

                // 自愈：只有当所有候选地址在多轮循环中均无法连接（累计 10 次）才自动重启
                if (fail_count >= 10) {
                    ESP_LOGE(kTag, "所有伴学服务端连续 %d 次连接失败 → 自愈重启设备", fail_count);
                    vTaskDelay(pdMS_TO_TICKS(500));
                    esp_restart();
                }
                vTaskDelay(pdMS_TO_TICKS(backoff_ms));
                backoff_ms = (backoff_ms < 15000) ? backoff_ms * 2 : 15000;
                continue;
            }
        }

        // 连通状态：每 20 秒发送一次心跳保活 + 电量上报 (抗外网/移动热点 NAT 超时丢连接)
        int64_t now_ms = esp_timer_get_time() / 1000;
        if (now_ms - last_heartbeat_ms_ >= 20000) {
            last_heartbeat_ms_ = now_ms;
            SendHeartbeat();
        }

        CarouselTick();   // 卡片轮播：到点（60s）自动切下一张
        FocusTick();      // 花火番茄钟：本地跑秒 + 局部动画帧 + 到点自绘绽放屏
        // 花火进行中跑快节拍，好让「屏一空闲就推下一帧」真的贴着刷新极限；其余时候 1 秒一拍省电
        vTaskDelay(pdMS_TO_TICKS(focus_active_ ? 120 : 1000));
    }
}

void Application::SendNfc(const std::string& uid, const std::string& ndef) {
    if (!ws_ || !ws_->IsConnected()) return;
    cJSON* j = cJSON_CreateObject();
    cJSON_AddStringToObject(j, "kind", "nfc");
    cJSON_AddStringToObject(j, "uid", uid.c_str());
    if (!ndef.empty()) cJSON_AddStringToObject(j, "ndef", ndef.c_str());
    char* out = cJSON_PrintUnformatted(j);
    if (out) { ws_->Send(std::string(out)); cJSON_free(out); }
    cJSON_Delete(j);
    ESP_LOGI(kTag, "→ nfc uid=%s ndef=%s", uid.c_str(), ndef.c_str());
}

void Application::SendButton(const std::string& name) {
    if (!ws_ || !ws_->IsConnected()) return;
    cJSON* j = cJSON_CreateObject();
    cJSON_AddStringToObject(j, "kind", "button");
    cJSON_AddStringToObject(j, "name", name.c_str());
    char* out = cJSON_PrintUnformatted(j);
    if (out) { ws_->Send(std::string(out)); cJSON_free(out); }
    cJSON_Delete(j);
    ESP_LOGI(kTag, "→ button name=%s", name.c_str());
}

void Application::SendControl(const char* kind) {
    if (!ws_) return;
    cJSON* j = cJSON_CreateObject();
    cJSON_AddStringToObject(j, "kind", kind);
    if (strcmp(kind, "ptt_start") == 0) {
        cJSON_AddNumberToObject(j, "sample_rate", 16000);
    }
    char* out = cJSON_PrintUnformatted(j);
    if (out) { ws_->Send(std::string(out)); cJSON_free(out); }
    cJSON_Delete(j);
}

void Application::StartListening() {
    AbortSpeaking("ptt_press");
    ptt_press_start_us_ = esp_timer_get_time();
    ptt_pressing_ = true;
    ptt_active_ = false;

    // 碰一碰交友等待确认阶段：主键按下 = 「好呀」，吃掉本次按键，不进 PTT
    if (TapFriend::GetInstance().ConsumeConfirm()) {
        ESP_LOGI(kTag, "主键被碰一碰确认消费，不进 PTT");
        ptt_pressing_ = false;
        return;
    }
}

void Application::StopListening() {
    if (!ptt_pressing_) return;
    ptt_pressing_ = false;
    
    int64_t duration_ms = (esp_timer_get_time() - ptt_press_start_us_) / 1000;
    if (wake_recording_.exchange(false)) {
        SetDeviceState(kDeviceStateIdle);
        if (codec_) codec_->EnableInput(false);
        SendControl("ptt_stop");
        return;
    }
    if (ptt_active_) {
        // 曾真正触发长按 PTT 录音态，松开时正常送 ptt_stop
        ptt_active_ = false;
        SetDeviceState(kDeviceStateIdle);
        if (codec_) codec_->EnableInput(false);
        SendControl("ptt_stop");
        ESP_LOGI(kTag, "PTT 长按松开(%lld ms) → 停止录音,等回复", duration_ms);
    } else {
        // 短按（< 350ms）：未触发录音，向服务端发送纯净的 confirm 按钮事件（打勾/听写下一词/口算看答案）
        AbortSpeaking("confirm_click");
        ESP_LOGI(kTag, "主键短按(%lld ms) → 发送 confirm 按键事件", duration_ms);
        SendButton("confirm");
    }
}

void Application::MicTask() {
#if CONFIG_POCKETCLAW_WAKE_WORD
    srmodel_list_t* models = esp_srmodel_init("model");
    const char* model_name = models ? esp_srmodel_filter(models, ESP_WN_PREFIX, "nihaoxiaozhi") : nullptr;
    const esp_wn_iface_t* wake = model_name ? esp_wn_handle_from_name(model_name) : nullptr;
    model_iface_data_t* wake_model = wake ? wake->create(model_name, DET_MODE_90) : nullptr;
    int wake_samples = wake_model ? wake->get_samp_chunksize(wake_model) : 0;
    if (wake_model && wake_samples > 0 && wake_samples <= 4096 &&
        wake->get_samp_rate(wake_model) == 16000 && wake->get_channel_num(wake_model) == 1) {
        wake_enabled_.store(true);
        ESP_LOGI(kTag, "本地唤醒已启用：你好小智");
    } else {
        ESP_LOGW(kTag, "唤醒模型不可用，保留按键说话");
        if (wake_model) wake->destroy(wake_model);
        wake_model = nullptr;
    }
    WakeEndpoint endpoint;
    std::vector<int16_t> wake_buf(wake_enabled_.load() ? wake_samples : 0);
#endif
    std::vector<int16_t> buf(960);   // 60ms @16kHz = 960 样本 = 1920 字节
    while (true) {
        // 延迟长按检测：按住超过 350ms 才真正进入 PTT 录音态并通知服务端
        if (ptt_pressing_ && !ptt_active_) {
            int64_t hold_ms = (esp_timer_get_time() - ptt_press_start_us_) / 1000;
            if (hold_ms >= 350) {
                if (ws_ && ws_->IsConnected() && codec_) {
                    const DeviceState st = GetDeviceState();
                    if (st == kDeviceStateIdle || st == kDeviceStateSpeaking) {
                        if (st == kDeviceStateSpeaking) {
                            ESP_LOGI(kTag, "⏹ 播报中被打断 → 停播、转录音");
                        }
                        codec_->EnableOutput(false);
                        codec_->EnableInput(true);
                        SetDeviceState(kDeviceStateListening);
                        SendControl("ptt_start");
                        ptt_active_ = true;
                        ESP_LOGI(kTag, "PTT 触发(按住 %lld ms) → 开始录音", hold_ms);
                    }
                }
            }
        }

#if CONFIG_POCKETCLAW_WAKE_WORD
        if (wake_recording_.load() && GetDeviceState() == kDeviceStateListening && codec_ && ws_ && ws_->IsConnected()) {
            buf.assign(960, 0);
            if (codec_->InputData(buf) && !buf.empty() && wake_recording_.load()) {
                ws_->Send(buf.data(), buf.size() * sizeof(int16_t), true);
                const auto result = endpoint.Feed(buf.data(), buf.size(), esp_timer_get_time() / 1000);
                if (result != WakeEndpoint::Result::Continue && wake_recording_.exchange(false)) {
                    codec_->EnableInput(false);
                    SetDeviceState(kDeviceStateIdle);
                    SendControl(result == WakeEndpoint::Result::Complete ? "ptt_stop" : "ptt_cancel");
                }
            }
            continue;
        }
        if (wake_enabled_.load() && !ptt_pressing_ && !ptt_active_ &&
            GetDeviceState() == kDeviceStateIdle && codec_ && ws_ && ws_->IsConnected()) {
            codec_->EnableInput(true);
            if (codec_->InputData(wake_buf) && wake->detect(wake_model, wake_buf.data()) == WAKENET_DETECTED) {
                if (!ptt_pressing_ && GetDeviceState() == kDeviceStateIdle && ws_->IsConnected()) {
                    endpoint.Start(esp_timer_get_time() / 1000);
                    wake_recording_.store(true);
                    SetDeviceState(kDeviceStateListening);
                    SendControl("ptt_start");
                }
            }
            continue;
        }
#endif
        if (ptt_active_ && GetDeviceState() == kDeviceStateListening && codec_ && ws_ && ws_->IsConnected()) {
            buf.assign(960, 0);
            if (codec_->InputData(buf) && !buf.empty()) {
                ws_->Send(buf.data(), buf.size() * sizeof(int16_t), true /*binary*/);
            }
        } else {
            vTaskDelay(pdMS_TO_TICKS(20));
        }
    }
}

void Application::OnAudioData(const char* data, size_t len) {
    if (len < 2) return;
    // 名画位图缓存：这段二进制是某张名画，存进卡缓存而非播放
    if (card_image_target_ >= 0) { CacheCardImageChunk(data, len); return; }
    // 单词卡发音缓存：这段二进制是某词的发音，存进缓存而非播放
    if (word_audio_target_ >= 0) { CacheAudioChunk(data, len); return; }
    if (!codec_ || !audio_rb_) return;
    // 打断后：正在录新问题，丢弃仍在途的残余 TTS 帧，别抢回播放
    if (GetDeviceState() == kDeviceStateListening) return;
    if (GetDeviceState() != kDeviceStateSpeaking) {
        codec_->EnableInput(false);
        codec_->EnableOutput(true);
        tts_ending_.store(false);
        SetDeviceState(kDeviceStateSpeaking);
        ESP_LOGI(kTag, "▶ TTS 首帧入缓冲（%d 字节）", (int)len);
    }
    // 只入抖动缓冲，真正喂 I2S 交给 AudioPlayTask（解耦收/播，抗卡顿）。
    // 缓冲满时给 200ms 背压（服务端已按实时节奏发，正常到不了满）。
    xStreamBufferSend(audio_rb_, data, len, pdMS_TO_TICKS(200));
}

/* 清掉「半截传输态」——加固点（2026-08-27）。
   现象来源：`OnAudioData` 开头有两道分流守卫
     if (card_image_target_ >= 0) { CacheCardImageChunk(...); return; }
     if (word_audio_target_  >= 0) { CacheAudioChunk(...);     return; }
   一旦「单词发音/卡片位图」传到一半断连（begin 收到、end 没收到），标志就永久停在 >=0，
   此后**所有 TTS 音频都会被当成缓存数据吞掉**，表现为「有文字、永远没声音」。
   断连/重连/收尾时统一调用本函数复位，并排空抖动缓冲，避免把上一轮残音带到下一轮。 */
void Application::ResetTransferState(const char* why, bool drain_audio) {
    if (word_audio_target_ >= 0 || card_image_target_ >= 0) {
        ESP_LOGW(kTag, "清理半截传输态(%s)：word_audio_target=%d card_image_target=%d",
                 why ? why : "-", word_audio_target_, card_image_target_);
    }
    word_audio_target_ = -1;
    card_image_target_ = -1;
    if (drain_audio) {
        tts_ending_.store(false);
        if (audio_rb_) {                  // 排空残余，避免下一轮播到上一轮的尾巴
            xStreamBufferReset(audio_rb_);
        }
    }
}

void Application::AudioPlayTask() {
    std::vector<int16_t> buf(1024);           // 每次最多取 2048 字节
    const size_t kPrebufferBytes = 6400;      // ~200ms @16k/16bit：起播前先攒够，抗抖动
    const int64_t kSpeakStallMs = 8000;       // 进 Speaking 后等音频的上限，超时自救回待命
    int64_t wait_start_ms = 0;                // 本轮开始等音频的时刻（0=没在等）
    bool started = false;
    while (true) {
        if (!audio_rb_) { vTaskDelay(pdMS_TO_TICKS(20)); continue; }
        if (GetDeviceState() != kDeviceStateSpeaking) {
            // 非播报态（含打断）：排空残余缓冲、复位起播门
            size_t n;
            while ((n = xStreamBufferReceive(audio_rb_, buf.data(), buf.size() * 2, 0)) > 0) {}
            started = false;
            wait_start_ms = 0;
            vTaskDelay(pdMS_TO_TICKS(10));
            continue;
        }
        const size_t avail = xStreamBufferBytesAvailable(audio_rb_);
        if (!started) {
            // 预缓冲：攒够 kPrebufferBytes 或已收到 tts_end 再起播
            if (avail < kPrebufferBytes && !tts_ending_.load()) {
                // 卡死兜底（加固 2026-08-27）：进了 Speaking 却迟迟等不到音频——多半是
                // 服务端中途断了、或半截传输态把音频吞了。干等会永远停在 Speaking，
                // 表现为「有文字没声音、且之后每一轮都不出声」。超时就自救回待命。
                if (wait_start_ms == 0) wait_start_ms = esp_timer_get_time() / 1000;
                else if ((esp_timer_get_time() / 1000) - wait_start_ms > kSpeakStallMs) {
                    ESP_LOGW(kTag, "⚠ 等音频超时(%dms，缓冲 %d 字节) → 自救回待命",
                             (int)kSpeakStallMs, (int)avail);
                    ResetTransferState("speak_stall");
                    if (codec_) codec_->EnableOutput(false);
                    SetDeviceState(kDeviceStateIdle);
                    wait_start_ms = 0;
                }
                vTaskDelay(pdMS_TO_TICKS(5));
                continue;
            }
            started = true;
            wait_start_ms = 0;
            ESP_LOGI(kTag, "▶ 起播（预缓冲 %d 字节）", (int)avail);
        }
        const size_t got = xStreamBufferReceive(audio_rb_, buf.data(), buf.size() * 2,
                                                pdMS_TO_TICKS(20));
        if (got >= 2) {
            std::vector<int16_t> pcm(buf.begin(), buf.begin() + got / 2);
            codec_->OutputData(pcm);          // 阻塞写 I2S；DMA 满则自然让出 CPU
        } else if (tts_ending_.load() && xStreamBufferBytesAvailable(audio_rb_) == 0) {
            // 缓冲放完 + 服务端已发 tts_end → 收尾
            codec_->EnableOutput(false);
            SetDeviceState(kDeviceStateIdle);
            tts_ending_.store(false);
            started = false;
            ESP_LOGI(kTag, "TTS 播放完毕 → 回到待命");
        }
    }
}

void Application::SendHello() {
    uint8_t mac[6] = {0};
    esp_read_mac(mac, ESP_MAC_WIFI_STA);
    char macstr[18];
    snprintf(macstr, sizeof(macstr), "%02x:%02x:%02x:%02x:%02x:%02x",
             mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    cJSON* j = cJSON_CreateObject();
    cJSON_AddStringToObject(j, "kind", "hello");
    // 声明本地能力：花火番茄钟的秒与画面都在设备本地跑，服务端只发开始/停止、不必每分钟推屏
    cJSON* feats = cJSON_CreateArray();
    cJSON_AddItemToArray(feats, cJSON_CreateString("focus_local"));
    cJSON_AddItemToArray(feats, cJSON_CreateString("screen_capture"));
    cJSON_AddItemToObject(j, "features", feats);
    cJSON_AddStringToObject(j, "device_id", macstr);
    cJSON_AddStringToObject(j, "mac", macstr);
    cJSON_AddStringToObject(j, "fw", "pocketclaw-l2");
    char* out = cJSON_PrintUnformatted(j);
    if (out && ws_) {
        ws_->Send(std::string(out));
        ESP_LOGI(kTag, "→ hello: %s", out);
    }
    if (out) cJSON_free(out);
    cJSON_Delete(j);
}

void Application::SendHeartbeat() {
    if (!ws_ || !ws_->IsConnected()) return;
    auto& board = Board::GetInstance();
    int level = 0;
    bool chg = false, dis = false;
    board.GetBatteryLevel(level, chg, dis);

    cJSON* j = cJSON_CreateObject();
    cJSON_AddStringToObject(j, "kind", "heartbeat");
    cJSON_AddNumberToObject(j, "battery", level);
    char* out = cJSON_PrintUnformatted(j);
    if (out) {
        ws_->Send(std::string(out));
        cJSON_free(out);
    }
    cJSON_Delete(j);
    ws_->Ping();  // 维持底层 TCP / WebSocket NAT 活跃状态，防止移动热点与反代断链
}

void Application::LoadServerUrlsFromNvs() {
    server_url_primary_ = "";
    server_url_backup_ = "";

    nvs_handle_t nvs;
    esp_err_t err = nvs_open("wifi", NVS_READONLY, &nvs);
    if (err == ESP_OK) {
        char url_buf[256] = {0};
        size_t size = sizeof(url_buf);
        if (nvs_get_str(nvs, "server_url", url_buf, &size) == ESP_OK && url_buf[0] != '\0') {
            server_url_primary_ = url_buf;
        }
        memset(url_buf, 0, sizeof(url_buf));
        size = sizeof(url_buf);
        if (nvs_get_str(nvs, "backup_url", url_buf, &size) == ESP_OK && url_buf[0] != '\0') {
            server_url_backup_ = url_buf;
        }
        nvs_close(nvs);
    }

    device_auth_token_.clear();
    nvs_handle_t identity_nvs;
    if (nvs_open("pocket", NVS_READONLY, &identity_nvs) == ESP_OK) {
        char token[256] = {0};
        size_t token_size = sizeof(token);
        if (nvs_get_str(identity_nvs, "device_token", token, &token_size) == ESP_OK) {
            device_auth_token_ = token;
        }
        memset(token, 0, sizeof(token));
        nvs_close(identity_nvs);
    }

    if (server_url_primary_.empty()) {
        server_url_primary_ = kDefaultServerUrlLan;
    }
    if (server_url_backup_.empty() && kDefaultServerUrlWan[0] != '\0') {
        server_url_backup_ = kDefaultServerUrlWan;
    }

    ESP_LOGI(kTag, "伴学服务地址加载完成: 主地址(LAN)=%s, 备用地址(WAN)=%s",
             server_url_primary_.c_str(),
             server_url_backup_.empty() ? "(未配置)" : server_url_backup_.c_str());
}

void Application::SendScreenSnapshot(const std::string& request_id) {
    if (!ws_ || !ws_->IsConnected()) return;
    std::vector<uint8_t> frame;
    int bpp = 0;
    Display* display = Board::GetInstance().GetDisplay();
    if (GetDeviceState() == kDeviceStateListening || !display || !display->CopyVisibleFrame(frame, bpp)) {
        cJSON* err = cJSON_CreateObject();
        cJSON_AddStringToObject(err, "kind", "screen_snapshot_error");
        cJSON_AddStringToObject(err, "request_id", request_id.c_str());
        char* msg = cJSON_PrintUnformatted(err);
        if (msg) { ws_->Send(std::string(msg)); cJSON_free(msg); }
        cJSON_Delete(err);
        return;
    }
    size_t capacity = 4 * ((frame.size() + 2) / 3) + 1, written = 0;
    std::vector<unsigned char> encoded(capacity);
    if (mbedtls_base64_encode(encoded.data(), encoded.size(), &written, frame.data(), frame.size()) != 0) return;
    constexpr size_t chunk_size = 20000;
    const int count = (written + chunk_size - 1) / chunk_size;
    for (int index = 0; index < count; ++index) {
        const size_t offset = static_cast<size_t>(index) * chunk_size;
        const size_t length = std::min(chunk_size, written - offset);
        std::string part(reinterpret_cast<char*>(encoded.data() + offset), length);
        cJSON* msg = cJSON_CreateObject();
        cJSON_AddStringToObject(msg, "kind", "screen_snapshot_chunk");
        cJSON_AddStringToObject(msg, "request_id", request_id.c_str());
        cJSON_AddNumberToObject(msg, "bits", bpp);
        cJSON_AddNumberToObject(msg, "count", count);
        cJSON_AddNumberToObject(msg, "index", index);
        cJSON_AddStringToObject(msg, "data", part.c_str());
        char* payload = cJSON_PrintUnformatted(msg);
        bool sent = payload && ws_->Send(std::string(payload));
        if (payload) cJSON_free(payload);
        cJSON_Delete(msg);
        if (!sent) break;
    }
}

void Application::OnControlFrame(const char* data, size_t len) {
    if (len <= 512) {
        std::string s(data, len);
        ESP_LOGI(kTag, "← 控制帧: %s", s.c_str());
    } else {
        std::string s(data, 256);
        ESP_LOGI(kTag, "← 控制帧 (len=%u): %s ...", (unsigned)len, s.c_str());
    }
    cJSON* root = cJSON_ParseWithLength(data, len);
    if (!root) { ESP_LOGW(kTag, "控制帧非法 JSON"); return; }
    const cJSON* kind = cJSON_GetObjectItem(root, "kind");
    if (cJSON_IsString(kind)) {
        const char* k = kind->valuestring;
        ESP_LOGI(kTag, "  kind=%s", k);
        if (strcmp(k, "screen_snapshot_request") == 0) {
            const cJSON* id = cJSON_GetObjectItem(root, "request_id");
            if (cJSON_IsString(id) && strlen(id->valuestring) == 32 &&
                strspn(id->valuestring, "0123456789abcdef") == 32) {
                SendScreenSnapshot(id->valuestring);
            }
        } else if (strcmp(k, "tts_start") == 0) {
            if (GetDeviceState() == kDeviceStateListening) {
                ESP_LOGI(kTag, "打断中，忽略残余 tts_start");   // 被打断那一轮的尾音
            } else {
                // 开播前先清半截传输态：否则上一次没收尾的 word/card 缓存会把本轮 TTS 全吞掉
                ResetTransferState("tts_start", /*drain_audio=*/false);
                if (codec_) { codec_->EnableInput(false); codec_->EnableOutput(true); }
                tts_ending_.store(false);
                SetDeviceState(kDeviceStateSpeaking);
            }
        } else if (strcmp(k, "tts_end") == 0) {
            if (GetDeviceState() == kDeviceStateListening) {
                ESP_LOGI(kTag, "打断中，忽略残余 tts_end");
            } else {
                // 只置「收尾中」——缓冲里可能还有没放完的音频，交给 AudioPlayTask 放完再收尾，
                // 绝不在这里立刻关输出/回待命（否则会把尾巴截断）。
                tts_ending_.store(true);
                ESP_LOGI(kTag, "收到 tts_end → 放完缓冲即收尾");
            }
        } else if (strcmp(k, "tts_stop") == 0 || strcmp(k, "abort") == 0) {
            AbortSpeaking("server_tts_stop");
        } else if (strcmp(k, "stt_partial") == 0) {
            const cJSON* t = cJSON_GetObjectItem(root, "text");
            if (GetDeviceState() == kDeviceStateListening && current_mode_ == "chat" &&
                cJSON_IsString(t) && t->valuestring[0]) {
                RenderPage("正在听", {std::string("你说：") + t->valuestring}, {}, false);
            }
        } else if (strcmp(k, "stt") == 0) {
            // 识别文本先上屏 + 「思考中…」，给孩子即时反馈（整屏渲染，DrawTexts 真上屏）
            const cJSON* t = cJSON_GetObjectItem(root, "text");
            if (cJSON_IsString(t) && t->valuestring[0]) {
                // stt 即时占位帧：服务端只发裸文本，这里自加「你说：」前缀
                current_mode_ = "";
                word_mode_ = false;
                carousel_mode_ = false;   // 新一轮语音 → 退出本地卡片/单词模式
                RenderPage("", {std::string("你说：") + t->valuestring}, {"思考中…"});
            }
        } else if (strcmp(k, "display") == 0) {
            // L4：page.title + page.heard(全文) + page.body → 分页整屏渲染
            const cJSON* page = cJSON_GetObjectItem(root, "page");
            std::string title;
            std::vector<std::string> heard, body;
            if (page) {
                const cJSON* jmode = cJSON_GetObjectItem(page, "mode");
                current_mode_ = (cJSON_IsString(jmode)) ? jmode->valuestring : "";
                if (current_mode_ != "word") word_mode_ = false;   // 离开单词卡 → 退出本地翻页
                carousel_mode_ = false;                            // 服务端下发新页 → 退出卡片轮播

                const cJSON* jb64_4bpp = cJSON_GetObjectItem(page, "raw_4bpp_b64");
                if (cJSON_IsString(jb64_4bpp) && jb64_4bpp->valuestring && jb64_4bpp->valuestring[0]) {
                    Display* disp = Board::GetInstance().GetDisplay();
                    if (disp) {
                        size_t b64_len = strlen(jb64_4bpp->valuestring);
                        size_t out_len = 0;
                        uint8_t* raw = (uint8_t*)heap_caps_malloc(60000, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
                        if (raw) {
                            int ret = mbedtls_base64_decode(raw, 60000, &out_len,
                                                           (const unsigned char*)jb64_4bpp->valuestring, b64_len);
                            if (ret == 0 && out_len >= 60000) {
                                ESP_LOGI(kTag, "16灰阶全屏高清渲染: %u 字节, 4bpp 多波形刷新 (mode=%s)",
                                         (unsigned)out_len, current_mode_.c_str());
                                disp->WriteRaw4bpp(raw, 60000);
                                heap_caps_free(raw);
                                cJSON_Delete(root);
                                return;
                            }
                            heap_caps_free(raw);
                        }
                    }
                }

                const cJSON* jb64 = cJSON_GetObjectItem(page, "raw_1bpp_b64");
                if (cJSON_IsString(jb64) && jb64->valuestring && jb64->valuestring[0]) {
                    Display* disp = Board::GetInstance().GetDisplay();
                    if (disp) {
                        size_t b64_len = strlen(jb64->valuestring);
                        size_t out_len = 0;
                        uint8_t* raw = (uint8_t*)heap_caps_malloc(15000, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
                        if (raw) {
                            int ret = mbedtls_base64_decode(raw, 15000, &out_len,
                                                           (const unsigned char*)jb64->valuestring, b64_len);
                            if (ret == 0 && out_len >= 15000) {
                                ESP_LOGI(kTag, "名画全屏位图渲染: %u 字节, 墨水屏全屏急刷 (mode=%s)",
                                         (unsigned)out_len, current_mode_.c_str());
                                disp->WriteRaw1bpp(0, 0, 400, 300, raw, 15000);
                                disp->RequestUrgentFullRefresh();
                                heap_caps_free(raw);
                                cJSON_Delete(root);
                                return;
                            }
                            heap_caps_free(raw);
                        }
                    }
                }

                const cJSON* jt = cJSON_GetObjectItem(page, "title");
                if (cJSON_IsString(jt)) title = jt->valuestring;
                const cJSON* jh = cJSON_GetObjectItem(page, "heard");
                if (cJSON_IsArray(jh)) {
                    int nh = cJSON_GetArraySize(jh);
                    for (int i = 0; i < nh; ++i) {
                        const cJSON* hl = cJSON_GetArrayItem(jh, i);
                        if (cJSON_IsString(hl)) heard.push_back(hl->valuestring);
                    }
                }
                const cJSON* jbanner = cJSON_GetObjectItem(page, "banner");
                if (cJSON_IsString(jbanner) && jbanner->valuestring[0]) {
                    body.push_back(std::string("⚠ ") + jbanner->valuestring);
                }
                const cJSON* jbody = cJSON_GetObjectItem(page, "body");
                if (cJSON_IsArray(jbody)) {
                    int nb = cJSON_GetArraySize(jbody);
                    for (int i = 0; i < nb; ++i) {
                        const cJSON* line = cJSON_GetArrayItem(jbody, i);
                        if (cJSON_IsString(line)) body.push_back(line->valuestring);
                    }
                }
                // 局部刷新支持：菜单模式默认局刷；或服务端显式下发 partial: true
                bool force_full = true;
                const cJSON* jpartial = cJSON_GetObjectItem(page, "partial");
                if (!jpartial) jpartial = cJSON_GetObjectItem(root, "partial");
                if (cJSON_IsBool(jpartial)) {
                    force_full = !cJSON_IsTrue(jpartial);
                } else if (current_mode_ == "menu") {
                    force_full = false;
                }
                RenderPage(title, heard, body, force_full);
            } else {
                RenderPage(title, heard, body);
            }
        } else if (strcmp(k, "focus_start") == 0) {
            const cJSON* jt = cJSON_GetObjectItem(root, "total_s");
            const cJSON* jd = cJSON_GetObjectItem(root, "today_min");
            const cJSON* js = cJSON_GetObjectItem(root, "style");
            const bool candle = cJSON_IsString(js) && strcmp(js->valuestring, "candle") == 0;
            OnFocusStart(cJSON_IsNumber(jt) ? jt->valueint : 1500,
                         cJSON_IsNumber(jd) ? jd->valueint : 0, candle);
        } else if (strcmp(k, "focus_stop") == 0) {
            OnFocusStop(true);
        } else if (strcmp(k, "focus_pause") == 0) {
            const cJSON* jp = cJSON_GetObjectItem(root, "paused");
            FocusSetPaused(!cJSON_IsBool(jp) || cJSON_IsTrue(jp));
        } else if (strcmp(k, "card_pack") == 0) {
            OnCardPack(data, len);                 // 随机卡片轮播：收整副卡
        } else if (strcmp(k, "card_image") == 0) {
            const cJSON* ji = cJSON_GetObjectItem(root, "idx");
            OnCardImageBegin(cJSON_IsNumber(ji) ? ji->valueint : -1);
        } else if (strcmp(k, "card_image_end") == 0) {
            OnCardImageEnd();
        } else if (strcmp(k, "word_pack") == 0) {
            OnWordPack(data, len);                 // 单词卡端上缓存：收文本包
        } else if (strcmp(k, "word_audio") == 0) {
            const cJSON* ji = cJSON_GetObjectItem(root, "idx");
            OnWordAudioBegin(cJSON_IsNumber(ji) ? ji->valueint : -1);
        } else if (strcmp(k, "word_audio_end") == 0) {
            OnWordAudioEnd();
        } else if (strcmp(k, "enter_sleep") == 0 || strcmp(k, "sleep") == 0) {
            if (CanEnterSleepMode()) {
                EnterDeepSleep();
            } else {
                ESP_LOGW(kTag, "当前设备繁忙，拒绝进入深度睡眠");
            }
        }
    }
    cJSON_Delete(root);
}

namespace {
// 400×300 墨水屏分页参数：正文统一 24px 思源黑体（大字号），逐行等距摆放，翻页数学简单可靠。
constexpr int kTopMargin    = 12;  // 顶边距 12px
constexpr int kLeftMargin   = 10;
constexpr int kBodySize     = 24;  // ≥20 → CustomLcdDisplay 选 24px 思源黑体（大字号）
constexpr int kLineStep     = 31;  // 行距 31px（24px字形 + 7px透气间距）
constexpr int kLinesPerPage = 9;   // 每屏容纳 9 行（第 0 行为标题，第 1~8 行为 8 行饱满正文）
constexpr int kFooterY      = 264; // 翻页提示行（底部，小字 16px）
}  // namespace

void Application::ShowSystemPage(const std::string& title, const std::vector<std::string>& body) {
    // 系统流程整屏提示：退出单词卡/轮播本地模式，避免上下键分流到旧模式
    current_mode_ = "";
    word_mode_ = false;
    carousel_mode_ = false;
    RenderPage(title, {}, body);
}

void Application::RenderPage(const std::string& title, const std::vector<std::string>& heard,
                            const std::vector<std::string>& body, bool force_full) {
    page_force_full_ = force_full;
    // 把 标题 + 「你说：」全文 + 「小龙虾说：」正文 摊平成一条可翻页的行流。
    // heard/body 都已由服务端带好前缀并按屏宽折行，这里逐行原样上屏，不再二次加前缀。
    page_lines_.clear();
    if (!title.empty()) page_lines_.push_back(title);
    for (const auto& h : heard) page_lines_.push_back(h);
    // 我说的 与 小龙虾说的 之间空一行，视觉分隔
    if (!heard.empty() && !body.empty()) page_lines_.push_back("");
    for (const auto& b : body) page_lines_.push_back(b);
    page_offset_ = 0;
    // 自动寻找包含光标(> 开头)或反色选中行(\x02 开头)的行，自动切到对应页
    for (size_t i = 0; i < page_lines_.size(); ++i) {
        const std::string& l = page_lines_[i];
        if (!l.empty() && ((unsigned char)l[0] == 0x02 || l.rfind("> ", 0) == 0)) {
            page_offset_ = (static_cast<int>(i) / kLinesPerPage) * kLinesPerPage;
            break;
        }
    }
    RenderCurrentPage();
}

void Application::RenderCurrentPage() {
    Display* disp = Board::GetInstance().GetDisplay();
    if (!disp) return;

    const int total = static_cast<int>(page_lines_.size());
    if (total == 0) return;
    if (page_offset_ < 0) page_offset_ = 0;
    if (page_offset_ >= total) page_offset_ = (total / kLinesPerPage) * kLinesPerPage;

    std::vector<Display::TextItem> items;
    int shown = 0;
    for (int i = page_offset_; i < total && shown < kLinesPerPage; ++i, ++shown) {
        // 不可见哨兵 0x01 开头 = 完成的想法 → 划删除线（去掉哨兵再渲染）
        // 不可见哨兵 0x02 开头 = 选中的句子/项 → 黑底白字高亮反色
        std::string line = page_lines_[i];
        bool strike = false;
        bool inverted = false;
        if (!line.empty() && (unsigned char)line[0] == 0x01) {
            strike = true;
            line = line.substr(1);
        } else if (!line.empty() && (unsigned char)line[0] == 0x02) {
            if (line.find('\x03') == std::string::npos) {
                // 仅行首有 0x02，且无 0x03：整行反色（如功能导航菜单、打卡、倒计时天数大字）
                inverted = true;
                line = line.substr(1);
            }
            // 若含有 0x03，则是行内精准反色（如英语阅读段落流式选句），保留 0x02 和 0x03 由 render_text_to_buffer 逐字渲染
        }
        items.push_back({line, kLeftMargin, kTopMargin + shown * kLineStep, kBodySize, strike, inverted});
    }

    // 底部翻页提示：仅当超过一屏且非英文阅读、非功能菜单模式时显示「第 X/Y 页 · ▲上 ▼下」（小字 16px，不喧宾夺主）
    // 英文阅读与功能菜单模式不显示最下面这行页数（页码和上下键已放在首行标题，避免侵占正文区域）
    if (total > kLinesPerPage && current_mode_ != "reading" && current_mode_ != "menu") {
        const int page_no = page_offset_ / kLinesPerPage + 1;
        const int page_cnt = (total + kLinesPerPage - 1) / kLinesPerPage;
        char foot[64];
        snprintf(foot, sizeof(foot), "第 %d/%d 页  上键向上  下键向下", page_no, page_cnt);
        items.push_back({std::string(foot), kLeftMargin, kFooterY, 16});
    }

    disp->DrawTexts(items, true /*clear=整屏白底*/);
    if (page_force_full_) {
        disp->RequestUrgentFullRefresh();   // 强制全刷，消残影
    } else {
        disp->RequestUrgentRefresh();       // 局部刷新：菜单光标移动/选句等小差异无需闪烁整屏
    }
    ESP_LOGI(kTag, "RenderCurrentPage: 行 %d..%d / 共 %d (force_full=%d)", page_offset_, page_offset_ + shown, total, (int)page_force_full_);
}

void Application::PageDown() {
    AbortSpeaking("page_down");
    // 功能菜单与各交互模式优先上报服务端进行按键动作分发，不被本地状态拦截
    if (current_mode_ == "menu" || current_mode_ == "reading" || current_mode_ == "vocab" ||
        current_mode_ == "dictation" || current_mode_ == "arith" || current_mode_ == "recite" ||
        current_mode_ == "habit" || current_mode_ == "timetable" || current_mode_ == "mistakes" ||
        current_mode_ == "idiom" || current_mode_ == "guess" || current_mode_ == "feynman" || current_mode_ == "verify" ||
        current_mode_ == "feihua" || current_mode_ == "hanzi" || current_mode_ == "screensaver") {
        SendButton("down");
        return;
    }
    // 花火进行中：下键 = 把花火屏叫回来（聊天/清单把它盖住之后）
    if (focus_active_) { FocusRender(false); return; }
    // 卡片轮播模式：下键 = 下一张（本地，重置 1 分钟计时）
    if (carousel_mode_) { CardStep(+1); return; }
    // 单词卡模式：下键 = 下一张（本地缓存翻页 + 本地发音，不走服务端）
    if (word_mode_) { WordStepLocal(+1); return; }

    const int total = static_cast<int>(page_lines_.size());
    if (total > kLinesPerPage && page_offset_ + kLinesPerPage < total) {
        page_offset_ += kLinesPerPage;
        RenderCurrentPage();
        return;
    }
    // 否则向服务端上报 down 按键事件
    SendButton("down");
}

void Application::PageUp() {
    AbortSpeaking("page_up");
    if (current_mode_ == "menu" || current_mode_ == "reading" || current_mode_ == "vocab" ||
        current_mode_ == "dictation" || current_mode_ == "arith" || current_mode_ == "recite" ||
        current_mode_ == "habit" || current_mode_ == "timetable" || current_mode_ == "mistakes" ||
        current_mode_ == "idiom" || current_mode_ == "guess" || current_mode_ == "feynman" || current_mode_ == "verify" ||
        current_mode_ == "feihua" || current_mode_ == "hanzi" || current_mode_ == "screensaver") {
        SendButton("up");
        return;
    }
    // 花火进行中：上键 = 暂停 / 继续（屏底提示「上键暂停」）
    if (focus_active_) { FocusSetPaused(!focus_paused_); return; }
    // 卡片轮播模式：上键 = 上一张
    if (carousel_mode_) { CardStep(-1); return; }
    // 单词卡模式：上键 = 上一张
    if (word_mode_) { WordStepLocal(-1); return; }

    if (page_lines_.size() > kLinesPerPage && page_offset_ > 0) {
        page_offset_ -= kLinesPerPage;
        if (page_offset_ < 0) page_offset_ = 0;
        RenderCurrentPage();
        return;
    }
    // 否则向服务端上报 up 按键事件（生字词重听、上一页等）
    SendButton("up");
}

// ── 单词卡端上缓存 ──
void Application::OnWordPack(const char* json, size_t len) {
    cJSON* root = cJSON_ParseWithLength(json, len);
    if (!root) return;
    const cJSON* words = cJSON_GetObjectItem(root, "words");
    if (cJSON_IsArray(words)) {
        for (auto& e : word_cache_) if (e.audio) heap_caps_free(e.audio);
        word_cache_.clear();
        int n = cJSON_GetArraySize(words);
        for (int i = 0; i < n; ++i) {
            const cJSON* w = cJSON_GetArrayItem(words, i);
            WordEntry e;
            const cJSON* v;
            if ((v = cJSON_GetObjectItem(w, "w")) && cJSON_IsString(v)) e.w = v->valuestring;
            if ((v = cJSON_GetObjectItem(w, "m")) && cJSON_IsString(v)) e.m = v->valuestring;
            if ((v = cJSON_GetObjectItem(w, "e")) && cJSON_IsString(v)) e.e = v->valuestring;
            if ((v = cJSON_GetObjectItem(w, "ec")) && cJSON_IsString(v)) e.ec = v->valuestring;
            word_cache_.push_back(std::move(e));
        }
        word_mode_ = !word_cache_.empty();
        word_idx_ = 0;
        word_audio_target_ = -1;
        ESP_LOGI(kTag, "单词包缓存：%d 词，进本地翻页模式", (int)word_cache_.size());
        if (word_mode_) RenderWordLocal();
    }
    cJSON_Delete(root);
}

void Application::OnWordAudioBegin(int idx) {
    if (idx < 0 || idx >= (int)word_cache_.size()) { word_audio_target_ = -1; return; }
    WordEntry& e = word_cache_[idx];
    if (e.audio) { heap_caps_free(e.audio); e.audio = nullptr; e.audio_samples = 0; }
    word_audio_target_ = idx;
}

void Application::OnWordAudioEnd() {
    const int done = word_audio_target_;
    word_audio_target_ = -1;
    // 若刚缓存好的正是当前显示词且此刻空闲 → 自动播一次（首张卡的发音）
    if (done == word_idx_ && GetDeviceState() != kDeviceStateSpeaking) PlayWordAudio();
}

void Application::CacheAudioChunk(const char* data, size_t len) {
    if (word_audio_target_ < 0 || word_audio_target_ >= (int)word_cache_.size()) return;
    WordEntry& e = word_cache_[word_audio_target_];
    size_t add = len / 2;
    int16_t* p = (int16_t*)heap_caps_realloc(e.audio, (e.audio_samples + add) * sizeof(int16_t), MALLOC_CAP_SPIRAM);
    if (!p) return;
    e.audio = p;
    memcpy(e.audio + e.audio_samples, data, add * sizeof(int16_t));
    e.audio_samples += add;
}

void Application::RenderWordLocal() {
    if (word_idx_ < 0 || word_idx_ >= (int)word_cache_.size()) return;
    const WordEntry& e = word_cache_[word_idx_];
    char title[48];
    snprintf(title, sizeof(title), "单词卡（%d/%d）", word_idx_ + 1, (int)word_cache_.size());
    std::vector<std::string> body;
    body.push_back(e.w);
    body.push_back("");
    body.push_back(e.m.empty() ? std::string("（想想它的意思）") : e.m);
    if (!e.e.empty()) {
        body.push_back("");
        body.push_back("例：" + e.e);
        if (!e.ec.empty()) body.push_back(std::string("　　") + e.ec);
    }
    current_mode_ = "word";
    RenderPage(title, {}, body);
    PlayWordAudio();
}

void Application::PlayWordAudio() {
    if (!codec_ || !audio_rb_) return;
    if (word_idx_ < 0 || word_idx_ >= (int)word_cache_.size()) return;
    const WordEntry& e = word_cache_[word_idx_];
    if (!e.audio || e.audio_samples == 0) return;   // 还没缓存到发音 → 静默（等音频到再自动播）
    codec_->EnableInput(false);
    codec_->EnableOutput(true);
    tts_ending_.store(false);
    SetDeviceState(kDeviceStateSpeaking);
    const char* p = reinterpret_cast<const char*>(e.audio);
    const size_t total = e.audio_samples * sizeof(int16_t);
    const size_t CH = 1920;
    for (size_t off = 0; off < total; off += CH) {
        size_t n = (total - off < CH) ? (total - off) : CH;
        xStreamBufferSend(audio_rb_, p + off, n, pdMS_TO_TICKS(200));
    }
    tts_ending_.store(true);
}

void Application::WordStepLocal(int delta) {
    if (word_cache_.empty()) return;
    int n = static_cast<int>(word_cache_.size());
    int ni = word_idx_ + delta;
    if (ni >= n) { SendControl("word_more"); return; }   // 越过末词 → 向服务端要下一批（新随机 30）
    if (ni < 0) ni = 0;                                   // 首词前 → 停在首词
    if (ni == word_idx_) return;
    word_idx_ = ni;
    RenderWordLocal();
}

// ── 随机卡片轮播（文字卡 + 名画） ──
void Application::OnCardPack(const char* json, size_t len) {
    cJSON* root = cJSON_ParseWithLength(json, len);
    if (!root) return;
    const cJSON* arr = cJSON_GetObjectItem(root, "cards");
    if (cJSON_IsArray(arr)) {
        for (auto& e : card_cache_) if (e.bitmap) heap_caps_free(e.bitmap);
        card_cache_.clear();
        int n = cJSON_GetArraySize(arr);
        for (int i = 0; i < n; ++i) {
            const cJSON* c = cJSON_GetArrayItem(arr, i);
            CardEntry e;
            const cJSON* v;
            if ((v = cJSON_GetObjectItem(c, "type")) && cJSON_IsString(v)) e.type = v->valuestring;
            if ((v = cJSON_GetObjectItem(c, "title")) && cJSON_IsString(v)) e.title = v->valuestring;
            if ((v = cJSON_GetObjectItem(c, "footer")) && cJSON_IsString(v)) e.footer = v->valuestring;
            const cJSON* lines = cJSON_GetObjectItem(c, "lines");
            if (cJSON_IsArray(lines)) {
                int m = cJSON_GetArraySize(lines);
                for (int j = 0; j < m; ++j) {
                    const cJSON* l = cJSON_GetArrayItem(lines, j);
                    if (cJSON_IsString(l)) e.lines.push_back(l->valuestring);
                }
            }
            card_cache_.push_back(std::move(e));
        }
        word_mode_ = false;
        carousel_mode_ = !card_cache_.empty();
        card_idx_ = 0;
        card_image_target_ = -1;
        card_switch_ms_ = esp_timer_get_time() / 1000;
        ESP_LOGI(kTag, "卡片轮播：缓存 %d 张，进轮播模式", (int)card_cache_.size());
        if (carousel_mode_) RenderCardLocal();
    }
    cJSON_Delete(root);
}

void Application::OnCardImageBegin(int idx) {
    if (idx < 0 || idx >= (int)card_cache_.size()) { card_image_target_ = -1; return; }
    CardEntry& e = card_cache_[idx];
    if (e.bitmap) { heap_caps_free(e.bitmap); e.bitmap = nullptr; e.bitmap_len = 0; }
    card_image_target_ = idx;
}

void Application::OnCardImageEnd() {
    const int done = card_image_target_;
    card_image_target_ = -1;
    if (done == card_idx_) RenderCardLocal();   // 刚缓存好的正是当前卡 → 补渲染
}

void Application::CacheCardImageChunk(const char* data, size_t len) {
    if (card_image_target_ < 0 || card_image_target_ >= (int)card_cache_.size()) return;
    CardEntry& e = card_cache_[card_image_target_];
    uint8_t* p = (uint8_t*)heap_caps_realloc(e.bitmap, e.bitmap_len + len, MALLOC_CAP_SPIRAM);
    if (!p) return;
    e.bitmap = p;
    memcpy(e.bitmap + e.bitmap_len, data, len);
    e.bitmap_len += len;
}

void Application::RenderCardLocal() {
    if (card_idx_ < 0 || card_idx_ >= (int)card_cache_.size()) return;
    const CardEntry& c = card_cache_[card_idx_];
    current_mode_ = "card";
    // 名画：整屏位图（优先 400×300 4bpp 16灰阶 60000字节，兼容 1bpp 15000字节）。位图还没到 → 先显示标题占位
    if (c.type == "painting") {
        if (c.bitmap && c.bitmap_len >= 60000) {
            Display* disp = Board::GetInstance().GetDisplay();
            if (disp) {
                disp->WriteRaw4bpp(c.bitmap, 60000);
            }
            return;
        }
        if (c.bitmap && c.bitmap_len >= (size_t)(50 * 300)) {
            Display* disp = Board::GetInstance().GetDisplay();
            if (disp) {
                disp->WriteRaw1bpp(0, 0, 400, 300, c.bitmap, c.bitmap_len);
                disp->RequestUrgentFullRefresh();
            }
            return;
        }
        RenderPage(c.title, {}, {"名画加载中…", c.footer});
        return;
    }
    // 文字卡：标题 + 正文 + 落款
    std::vector<std::string> body = c.lines;
    if (!c.footer.empty()) { body.push_back(""); body.push_back(c.footer); }
    RenderPage(c.title, {}, body);
}

void Application::CardStep(int delta) {
    if (card_cache_.empty()) return;
    int n = static_cast<int>(card_cache_.size());
    int ni = card_idx_ + delta;
    if (ni >= n) { SendControl("card_more"); return; }   // 越过末张 → 向服务端要下一批（新一副）
    if (ni < 0) ni = 0;                                   // 首张前 → 停在首张
    card_idx_ = ni;
    card_switch_ms_ = esp_timer_get_time() / 1000;
    RenderCardLocal();
}

void Application::CarouselTick() {
    if (!carousel_mode_ || card_cache_.empty()) return;
    int64_t now = esp_timer_get_time() / 1000;
    if (now - card_switch_ms_ >= 60000) {   // 每 1 分钟自动切下一张
        card_switch_ms_ = now;              // 先更新，避免 card_more 期间反复触发
        CardStep(+1);
    }
}

// ── 花火番茄钟：本地跑秒 + 本地绘屏 ──────────────────────────────────────────
// 墨水屏一次全刷几百毫秒，**不能逐秒刷**：只在「剩余分钟数变了」时重绘，最后一分钟每 15 秒重绘一次
// （燃点向前挪一截、火星换个样子），到点绘绽放屏并上报服务端记成长证据。
void Application::OnFocusStart(int total_s, int today_min, bool candle) {
    if (total_s < 30) total_s = 30;
    if (total_s > 3600) total_s = 3600;
    focus_style_ = candle ? focus::Style::Candle : focus::Style::Spark;
    focus::GenerateFuse(focus_fuse_, 0);      // seed=0 → esp_random()：每轮引线都不一样
    focus_active_ = true;
    focus_paused_ = false;
    focus_total_s_ = total_s;
    focus_left_s_ = total_s;
    focus_deadline_us_ = esp_timer_get_time() + (int64_t)total_s * 1000000;
    focus_remain_us_ = 0;
    focus_today_min_ = today_min;
    // 花火动画靠连续局部刷新：期间放宽「连续局部刷 N 次后强制全刷」的上限，
    // 否则每 10 帧就闪一次白。分钟跳变时本来就会整屏重绘（那次是全刷），残影有机会清掉。
    {
        Display* d = Board::GetInstance().GetDisplay();
        // 连续局部刷多少次后强制一次全刷（全刷才真正消残影）。
        // 火花：燃点在走，残影被新内容盖掉 → 120 次一清，基本看不到闪白。
        // 蜡烛：火苗原地闪，残影就地累积（真机实测火苗上方会留一片灰） → 24 次一清，
        //       配合 2.5 秒一帧 ≈ 每分钟清一次，闪白不密，残影也压得住。
        if (d) d->SetPartialRunLimit(candle ? 24 : 120);
    }
    focus_last_drawn_min_ = -1;
    focus_last_drawn_left_ = -1;
    focus_last_anim_ms_ = 0;
    focus_anim_frames_ = 0;
    carousel_mode_ = false;
    word_mode_ = false;
    current_mode_ = "focus";
    ESP_LOGI(kTag, "%s番茄钟开始：%d 秒，今日已专注 %d 分钟",
             candle ? "蜡烛" : "花火", total_s, today_min);
    FocusRender(false);
}

void Application::OnFocusStop(bool silent) {
    if (!focus_active_) return;
    focus_active_ = false;
    focus_paused_ = false;
    current_mode_ = "";
    {
        Display* d = Board::GetInstance().GetDisplay();
        if (d) d->SetPartialRunLimit(10);      // 恢复常规消残影节奏
    }
    ESP_LOGI(kTag, "花火番茄钟结束（%s）", silent ? "服务端取消" : "到点");
}

int64_t Application::FocusLeftUs() const {
    if (!focus_active_) return 0;
    if (focus_paused_) return focus_remain_us_;
    const int64_t left = focus_deadline_us_ - esp_timer_get_time();
    return left > 0 ? left : 0;
}

void Application::FocusSetPaused(bool paused) {
    if (!focus_active_ || focus_paused_ == paused) return;
    if (paused) {
        focus_remain_us_ = FocusLeftUs();
    } else {
        focus_deadline_us_ = esp_timer_get_time() + focus_remain_us_;
    }
    focus_paused_ = paused;
    ESP_LOGI(kTag, "花火%s", paused ? "暂停" : "继续");
    if (ws_ && ws_->IsConnected()) {
        cJSON* j = cJSON_CreateObject();
        cJSON_AddStringToObject(j, "kind", "focus_state");
        cJSON_AddBoolToObject(j, "paused", paused);
        cJSON_AddNumberToObject(j, "left_s", focus_left_s_);
        char* out = cJSON_PrintUnformatted(j);
        if (out) { ws_->Send(std::string(out)); cJSON_free(out); }
        cJSON_Delete(j);
    }
    FocusRender(false);
}

void Application::FocusTick() {
    if (!focus_active_) return;
    const int64_t left_us = FocusLeftUs();
    focus_left_s_ = (int)((left_us + 999999) / 1000000);

    if (!focus_paused_ && left_us <= 0) {
        focus_left_s_ = 0;
        focus_today_min_ += focus_total_s_ / 60;
        FocusRender(true);                       // 先上屏：孩子立刻看到花火绽放
        if (ws_ && ws_->IsConnected()) {
            cJSON* j = cJSON_CreateObject();
            cJSON_AddStringToObject(j, "kind", "focus_done");
            cJSON_AddNumberToObject(j, "total_s", focus_total_s_);
            char* out = cJSON_PrintUnformatted(j);
            if (out) { ws_->Send(std::string(out)); cJSON_free(out); }
            cJSON_Delete(j);
        }
        OnFocusStop(false);
        return;
    }

    // 当处于功能导航菜单等非专注画面时，暂停本地引线重绘与局部动画帧，避免覆盖菜单界面
    if (current_mode_ != "focus") return;

    // 分钟数变了 → 整屏重绘（数字/进度条/文案都得跟着变，这一次是全刷，顺带消残影）
    const int left_min = (focus_left_s_ + 59) / 60;
    if (left_min != focus_last_drawn_min_) { FocusRender(false); return; }
    if (focus_paused_) return;                   // 暂停：火花停在原地，不再出动画帧

    // 火花一直在烧：屏一空闲就推下一帧（只重绘引线区，局部刷新）。
    // 不排队——上一帧还没刷完就跳过这一次，免得动画帧堆积把整机拖慢。
    Display* disp = Board::GetInstance().GetDisplay();
    if (!disp) return;
    if (disp->IsRefreshBusy()) return;      // 上一帧还在刷 → 跳过这一拍，不排队堆积
    const int64_t total_us = (int64_t)focus_total_s_ * 1000000;
    float prog = total_us > 0 ? 1.f - (float)((double)left_us / (double)total_us) : 1.f;
    if (prog < 0.f) prog = 0.f;
    if (prog > 1.f) prog = 1.f;
    // 动画限帧（火花与蜡烛）：不贴着墨水屏极限连续翻转，免得同一片微胶囊疲劳烙出残影
    const int64_t now_ms = esp_timer_get_time() / 1000;
    if (now_ms - focus_last_anim_ms_ < focus::kCandleFrameMs) return;
    focus_last_anim_ms_ = now_ms;
    focus::RenderAnimFrame(disp, focus_fuse_, focus_style_, prog);
    // 真实出帧统计（此前统计点在限帧之前，数出来的是 tick 不是帧）：每 20 帧一条
    if (++focus_anim_frames_ % 20 == 0)
        ESP_LOGI(kTag, "%s动画：已出 %d 帧", focus_style_ == focus::Style::Candle ? "蜡烛" : "花火",
                 focus_anim_frames_);
}

void Application::FocusRender(bool done) {
    Display* disp = Board::GetInstance().GetDisplay();
    if (!disp) return;
    current_mode_ = "focus";
    focus_last_drawn_min_ = (focus_left_s_ + 59) / 60;
    focus_last_drawn_left_ = focus_left_s_;
    focus::RenderScreen(disp, focus_fuse_, focus_style_, focus_left_s_, focus_total_s_,
                        focus_paused_, focus_today_min_, done);
}

bool Application::SetDeviceState(DeviceState state) {
    const DeviceState old_state = state_.exchange(state, std::memory_order_acq_rel);
    ESP_LOGI(kTag, "State %d -> %d", old_state, state);
    return true;
}

void Application::Schedule(std::function<void()>&& callback) {
    if (callback) {
        callback();
    }
}

void Application::PlaySound(const std::string_view& sound) {
    audio_service_.PlaySound(sound);
}

void Application::PlaySound(const std::string_view& sound, int duration_ms) {
    audio_service_.PlaySound(sound, duration_ms);
}

void Application::MuteSound() {
    audio_service_.MuteOutput();
}

void Application::StopSound() {
    audio_service_.ResetDecoder();
}

void Application::AbortSpeaking(const char* reason) {
    if (GetDeviceState() == kDeviceStateSpeaking || tts_ending_.load()) {
        ESP_LOGI(kTag, "⏹ 立即打断/停止播报 (原因: %s)", reason ? reason : "user_action");
        if (codec_) codec_->EnableOutput(false);
        tts_ending_.store(false);
        ResetTransferState(reason, /*drain_audio=*/true);
        SetDeviceState(kDeviceStateIdle);
    }
}

bool Application::CanEnterSleepMode() const {
    // A continuously listening wake model cannot operate in deep sleep.
    if (wake_enabled_.load() && ws_ && ws_->IsConnected()) return false;
    // 1. 如果正在交互（录音或播报中），严禁休眠
    if (GetDeviceState() == kDeviceStateListening ||
        GetDeviceState() == kDeviceStateSpeaking) {
        return false;
    }
    // 2. 如果本地专注倒计时（番茄钟/花火）处于运行状态，不允许休眠
    if (focus_active_) {
        return false;
    }
    // 3. 如果 TTS 尾音仍在等待播放完成
    if (tts_ending_.load()) {
        return false;
    }
    // 4. 如果墨水屏正处于硬件刷新状态
    const uint32_t disp_mask = (1U << (uint32_t)SleepBusySrc::Display);
    if ((SleepManager::GetInstance().GetBusyMask() & disp_mask) != 0) {
        return false;
    }
    return true;
}

void Application::EnterDeepSleep() {
    ESP_LOGI(kTag, "【睡眠接管】准备进入深度睡眠...");
    const uint32_t disp_mask = (1U << (uint32_t)SleepBusySrc::Display);
    while ((SleepManager::GetInstance().GetBusyMask() & disp_mask) != 0) {
        vTaskDelay(pdMS_TO_TICKS(50));
    }
    // 配置 GPIO 唤醒：
    // GPIO 0: BOOT / 主按键 (低电平唤醒)
    esp_sleep_enable_ext0_wakeup(GPIO_NUM_0, 0);
    // GPIO 18 (Down 按键) 与 GPIO 5 (RTC 闹钟唤醒中断)
    const uint64_t ext1_mask = (1ULL << GPIO_NUM_18) | (1ULL << GPIO_NUM_5);
    esp_sleep_enable_ext1_wakeup(ext1_mask, ESP_EXT1_WAKEUP_ANY_LOW);
    ESP_LOGI(kTag, "【睡眠接管】GPIO 0/18/5 唤醒配置完成，切入 Deep Sleep");
    esp_deep_sleep_start();
}
