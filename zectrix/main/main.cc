#include <esp_log.h>
#include <esp_err.h>
#include <nvs.h>
#include <nvs_flash.h>
#include <driver/gpio.h>
#include <esp_event.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <esp_mac.h>
#include <fcntl.h>
#include <unistd.h>
#include <cstdio>
#include <cstring>

#include "application.h"
#include "system_info.h"

#define TAG "main"

#if CONFIG_POCKETCLAW_USB_PROVISION
// Physical-console only, available in explicitly enabled provisioning builds.
static void ProvisionDeviceToken() {
    uint8_t mac[6] = {};
    ESP_ERROR_CHECK(esp_read_mac(mac, ESP_MAC_WIFI_STA));
    printf("PCPROV1 READY %02x:%02x:%02x:%02x:%02x:%02x\n", mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    fflush(stdout);
    int old_flags = fcntl(STDIN_FILENO, F_GETFL, 0);
    if (old_flags < 0 || fcntl(STDIN_FILENO, F_SETFL, old_flags | O_NONBLOCK) < 0) {
        printf("PCPROV1 ERROR console\n"); fflush(stdout); return;
    }
    char token[128] = {};
    size_t used = 0;
    bool complete = false, valid = true;
    for (int ticks = 0; ticks < 600 && !complete; ++ticks) {
        char c;
        while (read(STDIN_FILENO, &c, 1) == 1) {
            if (c == '\n') { complete = true; break; }
            if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
                  (c >= '0' && c <= '9') || c == '-' || c == '_') || used >= sizeof(token) - 1) {
                valid = false;
            } else { token[used++] = c; }
        }
        if (!complete) vTaskDelay(pdMS_TO_TICKS(100));
    }
    fcntl(STDIN_FILENO, F_SETFL, old_flags);
    esp_err_t result = ESP_FAIL;
    if (complete && valid && used >= 32) {
        nvs_handle_t nvs;
        result = nvs_open("pocket", NVS_READWRITE, &nvs);
        if (result == ESP_OK) {
            result = nvs_set_str(nvs, "device_token", token);
            if (result == ESP_OK) result = nvs_commit(nvs);
            nvs_close(nvs);
        }
    }
    explicit_bzero(token, sizeof(token));
    printf("PCPROV1 %s\n", result == ESP_OK ? "OK" : "ERROR"); fflush(stdout);
}
#endif

static void LogNvsStats() {
    nvs_stats_t stats = {};
    esp_err_t ret = nvs_get_stats(nullptr, &stats);
    if (ret != ESP_OK) {
        ESP_LOGW(TAG, "NVS stats read failed: %s", esp_err_to_name(ret));
        return;
    }
    uint32_t used = stats.used_entries;
    uint32_t total = stats.total_entries;
    uint32_t free_entries = stats.free_entries;
    uint32_t percent = (total > 0) ? (used * 100U / total) : 0;
    ESP_LOGI(TAG, "NVS stats: used=%u free=%u total=%u (%u%%) namespaces=%u",
             used, free_entries, total, percent, stats.namespace_count);
}

extern "C" void app_main(void)
{
    // Initialize NVS flash for WiFi configuration
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_LOGW(TAG, "Erasing NVS flash to fix corruption");
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);
    LogNvsStats();

#if CONFIG_POCKETCLAW_USB_PROVISION
    ProvisionDeviceToken();
#endif

    // Initialize and run the application
    auto& app = Application::GetInstance();
    app.Initialize();
    app.Run();  // This function runs the main event loop and never returns
}
