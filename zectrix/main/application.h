#ifndef _APPLICATION_H_
#define _APPLICATION_H_

#include <atomic>
#include <functional>
#include <memory>
#include <string>
#include <string_view>
#include <vector>

#include "freertos/FreeRTOS.h"
#include "freertos/stream_buffer.h"

class WebSocket;
class AudioCodec;

#include "audio_service.h"
#include "device_state.h"
#include "focus_page.h"

class Application {
public:
    static Application& GetInstance() {
        static Application instance;
        return instance;
    }

    Application(const Application&) = delete;
    Application& operator=(const Application&) = delete;

    void Initialize();
    void Run();

    DeviceState GetDeviceState() const { return state_.load(std::memory_order_acquire); }
    bool SetDeviceState(DeviceState state);

    void Schedule(std::function<void()>&& callback);
    void PlaySound(const std::string_view& sound);
    void PlaySound(const std::string_view& sound, int duration_ms);
    void MuteSound();
    void StopSound();
    bool CanEnterSleepMode() const;
    void EnterDeepSleep();
    void AbortSpeaking(const char* reason = nullptr);

    AudioService& GetAudioService() { return audio_service_; }

    // L2b：PTT（板子按键回调调用，需 public）
    void StartListening();   // 按下 → 开始录音上行（播报中按 = 打断再问）
    void StopListening();    // 松开 → 停止录音,等回复

    // L4：上/下键翻页（问题+回复超过一屏时看全，本地处理不发服务端）
    void PageUp();
    void PageDown();

    // L5：NFC 贴卡与按键（发给服务端，驱动模式交互）
    void SendNfc(const std::string& uid, const std::string& ndef);
    void SendButton(const std::string& name);

    // 碰一碰交友等系统流程的整屏提示（先退出单词/轮播本地模式再整屏渲染）
    void ShowSystemPage(const std::string& title, const std::vector<std::string>& body);

private:
    Application();
    ~Application();

    void SendHello();
    void SendScreenSnapshot(const std::string& request_id);
    void SendHeartbeat();                             // 定期心跳保活 + 电量上报 (跨网NAT保活)
    void LoadServerUrlsFromNvs();                      // 从 NVS 读取局域网与远程备用服务器地址
    void OnControlFrame(const char* data, size_t len);
    void OnAudioData(const char* data, size_t len);   // L2c：收 TTS PCM16 入抖动缓冲
    void SendControl(const char* kind);               // L2b：发控制帧
    void MicTask();                                   // L2b：录音上行任务
    void AudioPlayTask();                             // L2c：专用播放任务（抖动缓冲→I2S，抗卡顿）
    void ResetTransferState(const char* why, bool drain_audio = true);  // 加固：清半截传输态(word/card)，可选排空音频缓冲

    // 单词卡端上缓存：文本 + 发音 PCM 全缓存到设备，上/下键本地翻页 + 本地发音，不走服务端
    struct WordEntry {
        std::string w, m, e, ec;      // word / meaning / example(英) / example_cn
        int16_t* audio = nullptr;     // 缓存发音 PCM（16k mono，放 PSRAM）
        size_t audio_samples = 0;
    };
    // 随机卡片轮播（古诗/现代诗/名言/名画）：整副缓存到设备，本地 1 分钟自动切 + 上/下键切
    struct CardEntry {
        std::string type;             // poem/quote/painting
        std::string title, footer;
        std::vector<std::string> lines;
        uint8_t* bitmap = nullptr;    // 名画位图（400×300 1bpp，PSRAM）
        size_t bitmap_len = 0;
    };
    // 花火番茄钟（focus_page.cc）：服务端只发开始/停止，**秒与画面都在本地**——
    // 断网、服务端重启都不影响这一轮；引线形状每次开始时本地随机生成。
    void OnFocusStart(int total_s, int today_min, bool candle);
    void OnFocusStop(bool silent);
    void FocusSetPaused(bool paused);
    void FocusTick();                                  // 主循环每秒调
    void FocusRender(bool done);
    int64_t FocusLeftUs() const;                       // 剩余微秒（暂停/运行统一入口）
    bool FocusActive() const { return focus_active_; }

    void OnCardPack(const char* json, size_t len);    // 收卡片包 → 缓存 + 进轮播模式
    void OnCardImageBegin(int idx);                   // 开始接收某张名画位图
    void OnCardImageEnd();
    void CacheCardImageChunk(const char* data, size_t len);
    void RenderCardLocal();                            // 本地渲染当前卡（文字或名画）
    void CardStep(int delta);                          // 切卡；越过末张 → 向服务端要下一批
    void CarouselTick();                               // 主循环每秒调：到 60s 自动切下一张

    void OnWordPack(const char* json, size_t len);    // 收单词包（文本）→ 缓存 + 进本地单词模式
    void OnWordAudioBegin(int idx);                   // 开始接收某词的发音缓存
    void OnWordAudioEnd();                            // 结束
    void CacheAudioChunk(const char* data, size_t len);  // 把二进制帧存进当前词的音频缓存
    void RenderWordLocal();                           // 本地渲染当前单词卡
    void PlayWordAudio();                             // 本地播放当前词发音（喂抖动缓冲）
    void WordStepLocal(int delta);                    // 上/下键本地翻词
    // L4：墨水屏聊天页渲染（走 CustomLcdDisplay::DrawTexts，真上屏）。
    // 整段内容存入 page_lines_，按屏高分页；上/下键翻页。
    void RenderPage(const std::string& title, const std::vector<std::string>& heard,
                    const std::vector<std::string>& body, bool force_full = true);
    void RenderCurrentPage();   // 渲染 page_lines_ 的当前页 + 翻页提示

    std::atomic<bool> wake_enabled_{false};
    std::atomic<bool> wake_recording_{false};
    std::atomic<DeviceState> state_{kDeviceStateUnknown};
    AudioService audio_service_;
    std::unique_ptr<WebSocket> ws_;
    AudioCodec* codec_ = nullptr;                     // L2b/c：裸 PCM16 直连 codec
    StreamBufferHandle_t audio_rb_ = nullptr;         // L2c：TTS 播放抖动缓冲（网络收→缓冲→播放解耦）
    std::atomic<bool> tts_ending_{false};             // L2c：收到 tts_end，播放任务放完缓冲即收尾
    std::vector<std::string> page_lines_;             // L4：当前整屏内容全文（供翻页）
    int page_offset_ = 0;                             // L4：当前页顶行下标
    bool page_force_full_ = true;                     // L4：当前屏是否强制全刷（菜单选择等局部刷新置 false）
    std::string current_mode_;                        // L4：当前页 mode（word/note/task/chat），上下键据此分流
    std::vector<WordEntry> word_cache_;               // 单词卡端上缓存
    bool word_mode_ = false;                          // 处于本地单词翻页模式
    int  word_idx_ = 0;                               // 当前词下标
    int  word_audio_target_ = -1;                     // 正在接收音频缓存的词下标；-1=无
    std::vector<CardEntry> card_cache_;               // 轮播卡片缓存
    bool carousel_mode_ = false;                      // 处于卡片轮播模式
    int  card_idx_ = 0;
    int64_t card_switch_ms_ = 0;                      // 上次切卡时刻（ms），到 60s 自动切
    int  card_image_target_ = -1;                     // 正在接收位图的卡下标；-1=无
    // ── 花火番茄钟本地状态 ──
    focus::Fuse focus_fuse_;                          // 本轮引线（开始时随机生成；蜡烛模式不用）
    focus::Style focus_style_ = focus::Style::Spark;  // 火花（默认）/ 蜡烛，由服务端 focus_start 指定
    bool focus_active_ = false;
    bool focus_paused_ = false;
    int  focus_total_s_ = 0;
    int  focus_left_s_ = 0;                           // 由截止时间戳算出的剩余秒（缓存，供渲染用）
    // 用**绝对截止时间戳**而不是逐秒递减：tick 抖动、动画帧耗时都不会让计时走偏，
    // 而且燃点位置能按亚秒精度连续推进（火花才像真的在烧，不是一秒跳一格）。
    int64_t focus_deadline_us_ = 0;                   // 运行中有效
    int64_t focus_remain_us_ = 0;                     // 暂停时有效
    int  focus_today_min_ = 0;                        // 今日已专注（服务端给，跨设备一致）
    int  focus_last_drawn_min_ = -1;                  // 上次上屏时的剩余分钟，变了才重绘（墨水屏刷得慢）
    int  focus_last_drawn_left_ = -1;
    int64_t focus_last_anim_ms_ = 0;                  // 上一动画帧时刻（蜡烛限帧用）
    int  focus_anim_frames_ = 0;                      // 本轮已出的动画帧数
    int64_t ptt_press_start_us_ = 0;                  // PTT 按下时刻（用于区分长按说话与短按确认）
    bool ptt_pressing_ = false;                       // 主键当前是否正被物理按下
    bool ptt_active_ = false;                         // 是否已达到长按阈值并处于录音状态
    std::string device_auth_token_;  // Optional provisioned device token, never logged
    std::string server_url_primary_;                  // 主服务器地址 (默认局域网)
    std::string server_url_backup_;                   // 备用远程服务器地址 (跨Wi-Fi公网/隧道)
    int64_t last_heartbeat_ms_ = 0;                   // 上次发送心跳时刻 (ms)
};

#endif  // _APPLICATION_H_
