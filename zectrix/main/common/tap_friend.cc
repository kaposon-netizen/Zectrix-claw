#include "tap_friend.h"

#include <cstring>
#include <string>
#include <vector>

#include <esp_log.h>
#include <esp_mac.h>
#include <esp_timer.h>
#include <esp_http_client.h>

#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <freertos/semphr.h>

#include <nimble/nimble_port.h>
#include <nimble/nimble_port_freertos.h>
#include <host/ble_hs.h>
#include <host/ble_gap.h>

#include "cJSON.h"
#include "application.h"

namespace {

constexpr char kTag[] = "TapFriend";

// 与 application.cc 的 kServerUrl 同一台服务端（ws→http 同 host:port）。
constexpr char kApiBase[] = "http://SimonYs-MacBook-Air.local:8000";

// 广播魔数 + 厂商自定义 Company ID（0xFFFF = 测试保留值）
constexpr uint8_t kMagic[4] = {'K', 'V', 'T', '1'};
constexpr uint16_t kCompanyId = 0xFFFF;
constexpr size_t kNoncePrefixLen = 16;   // 广播里带 nonce 前 16 字节

constexpr int kRssiClose = -50;          // 「贴近碰一碰」判据
constexpr int kRssiSamplesNeeded = 3;

// ── 扫描共享态（NimBLE host task 写，会话任务读，用互斥保护） ──
SemaphoreHandle_t g_mutex = nullptr;
char g_own_prefix[kNoncePrefixLen + 1] = {0};  // 自己的 nonce 前缀（忽略自己的回声）
char g_seen_nonce[kNoncePrefixLen + 1] = {0};  // 锁定的对方 nonce 前缀
std::vector<int> g_rssi;                       // 该对方的合格 RSSI 采样
bool g_ble_inited = false;
volatile bool g_ble_synced = false;
uint8_t g_own_addr_type = 0;

// ── HTTP：POST JSON，返回响应体（空串=失败） ──
std::string HttpPostJson(const char* path, cJSON* body_json) {
    char* body = cJSON_PrintUnformatted(body_json);
    if (!body) return "";
    std::string url = std::string(kApiBase) + path;
    esp_http_client_config_t cfg = {};
    cfg.url = url.c_str();
    cfg.method = HTTP_METHOD_POST;
    cfg.timeout_ms = 8000;
    esp_http_client_handle_t cli = esp_http_client_init(&cfg);
    std::string resp;
    if (cli) {
        esp_http_client_set_header(cli, "Content-Type", "application/json");
        size_t blen = strlen(body);
        if (esp_http_client_open(cli, blen) == ESP_OK) {
            esp_http_client_write(cli, body, blen);
            esp_http_client_fetch_headers(cli);
            char buf[512];
            int n;
            while ((n = esp_http_client_read(cli, buf, sizeof(buf))) > 0) {
                resp.append(buf, n);
            }
            int status = esp_http_client_get_status_code(cli);
            ESP_LOGI(kTag, "POST %s → %d, %d 字节", path, status, (int)resp.size());
            if (status < 200 || status >= 300) resp.clear();
        } else {
            ESP_LOGW(kTag, "POST %s 连接失败", path);
        }
        esp_http_client_cleanup(cli);
    }
    cJSON_free(body);
    return resp;
}

std::string DeviceIdMac() {
    uint8_t mac[6] = {0};
    esp_read_mac(mac, ESP_MAC_WIFI_STA);
    char s[18];
    snprintf(s, sizeof(s), "%02x:%02x:%02x:%02x:%02x:%02x",
             mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    return s;
}

// ── 墨水屏整屏提示（少刷：一个阶段一屏，不做动画/倒计时刷屏） ──
void Show(const std::string& title, const std::vector<std::string>& body) {
    Application::GetInstance().ShowSystemPage(title, body);
}

// ── NimBLE ──
void OnBleSync() {
    ble_hs_id_infer_auto(0, &g_own_addr_type);
    g_ble_synced = true;
}

void BleHostTask(void* param) {
    nimble_port_run();               // 直到 nimble_port_stop()
    nimble_port_freertos_deinit();
}

bool EnsureBle() {
    if (g_ble_inited) return true;
    if (nimble_port_init() != 0) {
        ESP_LOGE(kTag, "nimble_port_init 失败");
        return false;
    }
    ble_hs_cfg.sync_cb = OnBleSync;
    nimble_port_freertos_init(BleHostTask);
    // 等 host 与 controller 同步（最多 5s）
    for (int i = 0; i < 100 && !g_ble_synced; ++i) vTaskDelay(pdMS_TO_TICKS(50));
    if (!g_ble_synced) { ESP_LOGE(kTag, "BLE host 未同步"); return false; }
    g_ble_inited = true;
    return true;
}

// 组广播包：Flags + MfgData(CompanyID + "KVT1" + nonce前缀)
bool StartAdv(const std::string& nonce) {
    uint8_t payload[31];
    size_t nlen = nonce.size() < kNoncePrefixLen ? nonce.size() : kNoncePrefixLen;
    size_t i = 0;
    payload[i++] = 2; payload[i++] = 0x01; payload[i++] = 0x06;             // Flags
    payload[i++] = (uint8_t)(1 + 2 + sizeof(kMagic) + nlen);                // MfgData 长度
    payload[i++] = 0xFF;                                                    // AD type
    payload[i++] = kCompanyId & 0xFF; payload[i++] = kCompanyId >> 8;
    memcpy(payload + i, kMagic, sizeof(kMagic)); i += sizeof(kMagic);
    memcpy(payload + i, nonce.data(), nlen); i += nlen;

    int rc = ble_gap_adv_set_data(payload, i);
    if (rc != 0) { ESP_LOGE(kTag, "adv_set_data rc=%d", rc); return false; }

    ble_gap_adv_params adv = {};
    adv.conn_mode = BLE_GAP_CONN_MODE_NON;
    adv.disc_mode = BLE_GAP_DISC_MODE_GEN;
    adv.itvl_min = 0x50;   // 50ms：碰一碰要快，且只开 90s，耗电可接受
    adv.itvl_max = 0x60;
    rc = ble_gap_adv_start(g_own_addr_type, nullptr, BLE_HS_FOREVER, &adv, nullptr, nullptr);
    if (rc != 0) { ESP_LOGE(kTag, "adv_start rc=%d", rc); return false; }
    return true;
}

int OnScanEvent(struct ble_gap_event* event, void* arg) {
    if (event->type != BLE_GAP_EVENT_DISC) return 0;
    const auto& d = event->disc;
    // 手动扫 AD 结构找厂商自定义数据（不依赖 fields 解析对 0xFF 的截断行为）
    const uint8_t* p = d.data;
    int remain = d.length_data;
    while (remain >= 2) {
        uint8_t len = p[0];
        if (len == 0 || len + 1 > remain) break;
        uint8_t type = p[1];
        if (type == 0xFF && len >= (uint8_t)(1 + 2 + sizeof(kMagic) + 1)) {
            const uint8_t* mfg = p + 2;
            size_t mfg_len = len - 1;
            uint16_t cid = mfg[0] | (mfg[1] << 8);
            if (cid == kCompanyId && memcmp(mfg + 2, kMagic, sizeof(kMagic)) == 0) {
                size_t nlen = mfg_len - 2 - sizeof(kMagic);
                if (nlen > kNoncePrefixLen) nlen = kNoncePrefixLen;
                char nonce[kNoncePrefixLen + 1] = {0};
                memcpy(nonce, mfg + 2 + sizeof(kMagic), nlen);
                if (strcmp(nonce, g_own_prefix) != 0 && d.rssi >= kRssiClose) {
                    xSemaphoreTake(g_mutex, portMAX_DELAY);
                    if (g_seen_nonce[0] == 0) {
                        strncpy(g_seen_nonce, nonce, kNoncePrefixLen);
                        ESP_LOGI(kTag, "锁定对方 nonce=%s rssi=%d", nonce, d.rssi);
                    }
                    if (strcmp(g_seen_nonce, nonce) == 0 &&
                        (int)g_rssi.size() < kRssiSamplesNeeded) {
                        g_rssi.push_back(d.rssi);
                    }
                    xSemaphoreGive(g_mutex);
                }
            }
        }
        p += len + 1;
        remain -= len + 1;
    }
    return 0;
}

bool StartScan() {
    ble_gap_disc_params scan = {};
    scan.passive = 1;
    scan.itvl = 0x60;      // 60ms 间隔
    scan.window = 0x50;    // 50ms 窗口（高占空比，只开 90s）
    scan.filter_duplicates = 0;   // 要多个 RSSI 采样，不能去重
    int rc = ble_gap_disc(g_own_addr_type, BLE_HS_FOREVER, &scan, OnScanEvent, nullptr);
    if (rc != 0) { ESP_LOGE(kTag, "gap_disc rc=%d", rc); return false; }
    return true;
}

void StopBleActivity() {
    ble_gap_disc_cancel();
    ble_gap_adv_stop();
}

// 服务端拒绝原因 → 孩子能看懂的一句话
std::vector<std::string> ReasonLines(const std::string& reason) {
    if (reason == "not_close_enough") return {"还差一点点距离～", "把两台小机器贴在一起再试一次吧"};
    if (reason == "same_family")      return {"你们本来就是一家人呀，", "不用再加好友啦"};
    if (reason == "expired")          return {"这次碰一碰过期啦，", "长按上键再来一次吧"};
    if (reason == "already_friends")  return {"你们已经是好朋友啦！"};
    return {"这次没碰成功（" + (reason.empty() ? std::string("未知原因") : reason) + "），", "休息一下再试试吧"};
}

}  // namespace

bool TapFriend::ConsumeConfirm() {
    if (!awaiting_confirm_.load(std::memory_order_acquire)) return false;
    confirmed_.store(true, std::memory_order_release);
    return true;
}

void TapFriend::StartSession() {
    bool expected = false;
    if (!active_.compare_exchange_strong(expected, true)) {
        ESP_LOGI(kTag, "碰一碰已在进行中，忽略重复触发");
        return;
    }
    awaiting_confirm_.store(false);
    confirmed_.store(false);
    if (!g_mutex) g_mutex = xSemaphoreCreateMutex();
    // 独立任务跑会话：HTTP + BLE 等待都不能在按键回调里做
    if (xTaskCreate(SessionTaskEntry, "tap_friend", 6144, this, 4, nullptr) != pdPASS) {
        active_.store(false);
    }
}

void TapFriend::SessionTaskEntry(void* arg) {
    static_cast<TapFriend*>(arg)->SessionTask();
    vTaskDelete(nullptr);
}

void TapFriend::SessionTask() {
    bool ble_running = false;
    do {
        Show("碰一碰交友", {"正在准备…"});

        // 1) tap/start
        cJSON* j = cJSON_CreateObject();
        // child_id 留空：服务端按 device_id 的绑定关系解析当前孩子
        cJSON_AddStringToObject(j, "child_id", "");
        cJSON_AddStringToObject(j, "device_id", DeviceIdMac().c_str());
        cJSON_AddStringToObject(j, "method", "ble");
        std::string resp = HttpPostJson("/api/plaza/tap/start", j);
        cJSON_Delete(j);
        if (resp.empty()) { Show("碰一碰交友", {"网络好像开小差了，", "等一会儿再试试吧"}); break; }
        cJSON* r = cJSON_Parse(resp.c_str());
        std::string tap_id, nonce;
        int ttl_s = 90;
        if (r) {
            const cJSON* v;
            if ((v = cJSON_GetObjectItem(r, "tap_id")) && cJSON_IsString(v)) tap_id = v->valuestring;
            if ((v = cJSON_GetObjectItem(r, "nonce")) && cJSON_IsString(v)) nonce = v->valuestring;
            if ((v = cJSON_GetObjectItem(r, "ttl_s")) && cJSON_IsNumber(v)) ttl_s = v->valueint;
            cJSON_Delete(r);
        }
        if (tap_id.empty() || nonce.empty()) { Show("碰一碰交友", {"服务器没准备好，", "等一会儿再试试吧"}); break; }

        // 2) BLE 广播 + 扫描（按需开启）
        xSemaphoreTake(g_mutex, portMAX_DELAY);
        memset(g_own_prefix, 0, sizeof(g_own_prefix));
        strncpy(g_own_prefix, nonce.c_str(), kNoncePrefixLen);
        g_seen_nonce[0] = 0;
        g_rssi.clear();
        xSemaphoreGive(g_mutex);

        if (!EnsureBle() || !StartAdv(nonce) || !StartScan()) {
            StopBleActivity();
            Show("碰一碰交友", {"蓝牙没打开成功，", "重启一下再试试吧"});
            break;
        }
        ble_running = true;
        Show("碰一碰交友", {"把我和小伙伴的机器", "背对背贴在一起～", "", "（90 秒内有效）"});

        // 3) 等采样够 → report → 等确认 → confirm；全程 ttl 超时
        const int64_t deadline = esp_timer_get_time() / 1000 + (int64_t)ttl_s * 1000;
        enum { SCANNING, CONFIRMING, DONE } phase = SCANNING;
        while (esp_timer_get_time() / 1000 < deadline && phase != DONE) {
            vTaskDelay(pdMS_TO_TICKS(200));
            if (phase == SCANNING) {
                std::string seen;
                std::vector<int> rssi;
                xSemaphoreTake(g_mutex, portMAX_DELAY);
                if ((int)g_rssi.size() >= kRssiSamplesNeeded) { seen = g_seen_nonce; rssi = g_rssi; }
                xSemaphoreGive(g_mutex);
                if (seen.empty()) continue;

                cJSON* rep = cJSON_CreateObject();
                cJSON_AddStringToObject(rep, "tap_id", tap_id.c_str());
                cJSON_AddStringToObject(rep, "seen_nonce", seen.c_str());
                cJSON* arr = cJSON_CreateIntArray(rssi.data(), kRssiSamplesNeeded);
                cJSON_AddItemToObject(rep, "rssi", arr);
                std::string rr = HttpPostJson("/api/plaza/tap/report", rep);
                cJSON_Delete(rep);

                bool ok = false;
                std::string reason;
                if (cJSON* pr = cJSON_Parse(rr.c_str())) {
                    const cJSON* v;
                    if ((v = cJSON_GetObjectItem(pr, "ok"))) ok = cJSON_IsTrue(v);
                    if ((v = cJSON_GetObjectItem(pr, "reason")) && cJSON_IsString(v)) reason = v->valuestring;
                    cJSON_Delete(pr);
                }
                if (ok) {
                    phase = CONFIRMING;
                    confirmed_.store(false);
                    awaiting_confirm_.store(true, std::memory_order_release);
                    Show("碰上啦！", {"和对方碰上了！", "要成为好友吗？", "", "按一下主键 = 好呀"});
                } else if (reason == "not_close_enough") {
                    // 不判死：清采样重扫，孩子把设备贴得更近还有机会
                    xSemaphoreTake(g_mutex, portMAX_DELAY);
                    g_seen_nonce[0] = 0;
                    g_rssi.clear();
                    xSemaphoreGive(g_mutex);
                    Show("碰一碰交友", {"再贴近一点点～", "把两台机器碰在一起"});
                } else {
                    Show("碰一碰交友", ReasonLines(reason));
                    phase = DONE;
                }
            } else if (phase == CONFIRMING) {
                if (confirmed_.load(std::memory_order_acquire)) {
                    awaiting_confirm_.store(false);
                    cJSON* cf = cJSON_CreateObject();
                    cJSON_AddStringToObject(cf, "tap_id", tap_id.c_str());
                    std::string cr = HttpPostJson("/api/plaza/tap/confirm", cf);
                    cJSON_Delete(cf);
                    if (!cr.empty()) {
                        Show("碰一碰交友", {"太好啦！", "等爸爸妈妈点头哦～"});
                    } else {
                        Show("碰一碰交友", {"网络开小差了，", "刚才的确认没送到，再试一次吧"});
                    }
                    phase = DONE;
                }
            }
        }
        if (phase != DONE) {
            Show("碰一碰交友", {"这次没碰到小伙伴，", "下次把机器贴在一起再试试吧"});
        }
    } while (false);

    if (ble_running) StopBleActivity();   // 会话结束即关广播+扫描（省电）
    awaiting_confirm_.store(false);
    active_.store(false, std::memory_order_release);
    ESP_LOGI(kTag, "碰一碰会话结束");
}
