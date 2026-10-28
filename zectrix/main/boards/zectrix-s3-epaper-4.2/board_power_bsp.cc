#include <stdio.h>
#include <driver/gpio.h>
#include <freertos/FreeRTOS.h>
#include "board_power_bsp.h"
#include "charge_status.h"

void BoardPowerBsp::PowerLedTask(void *arg) {
    auto* self = static_cast<BoardPowerBsp*>(arg);
    gpio_config_t gpio_conf = {};
    gpio_conf.intr_type     = GPIO_INTR_DISABLE;
    gpio_conf.mode          = GPIO_MODE_OUTPUT;
    gpio_conf.pin_bit_mask  = (0x1ULL << GPIO_NUM_3);
    gpio_conf.pull_down_en  = GPIO_PULLDOWN_DISABLE;
    gpio_conf.pull_up_en    = GPIO_PULLUP_ENABLE;
    ESP_ERROR_CHECK_WITHOUT_ABORT(gpio_config(&gpio_conf));
    for (;;) {
        if (self->led_override_enabled_.load(std::memory_order_relaxed)) {
            const bool blink = self->led_override_blink_.load(std::memory_order_relaxed);
            if (blink) {
                const bool phase = !self->led_override_phase_.load(std::memory_order_relaxed);
                self->led_override_phase_.store(phase, std::memory_order_relaxed);
                gpio_hold_dis((gpio_num_t)GPIO_NUM_3);
                gpio_set_level(GPIO_NUM_3, phase ? 0 : 1);
                gpio_hold_en((gpio_num_t)GPIO_NUM_3);
                vTaskDelay(pdMS_TO_TICKS(500));
                continue;
            }
            gpio_hold_dis((gpio_num_t)GPIO_NUM_3);
            gpio_set_level(GPIO_NUM_3, 1);
            gpio_hold_en((gpio_num_t)GPIO_NUM_3);
            vTaskDelay(pdMS_TO_TICKS(500));
            continue;
        }

        // 指示灯常关：这台是常插 USB 的开发/自用机，充电态的周期闪烁很扰人，
        // 直接熄灭（本板 LED 低电平点亮，故 1=灭）。工厂 override 分支仍保留在上方，需要时可点亮/闪烁。
        gpio_hold_dis((gpio_num_t)GPIO_NUM_3);
        gpio_set_level(GPIO_NUM_3, 1);
        gpio_hold_en((gpio_num_t)GPIO_NUM_3);
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}

BoardPowerBsp::BoardPowerBsp(int epdPowerPin, int audioPowerPin, int audioAmpPin, int vbatPowerPin,
                             ChargeStatus* charge_status)
    : epdPowerPin_(epdPowerPin),
      audioPowerPin_(audioPowerPin),
      audioAmpPin_(audioAmpPin),
      vbatPowerPin_(vbatPowerPin),
      charge_status_(charge_status) {
    gpio_config_t gpio_conf = {};
    gpio_conf.intr_type     = GPIO_INTR_DISABLE;
    gpio_conf.mode          = GPIO_MODE_OUTPUT;
    gpio_conf.pin_bit_mask  = (0x1ULL << epdPowerPin_) | (0x1ULL << audioPowerPin_) | (0x1ULL << audioAmpPin_) | (0x1ULL << vbatPowerPin_);
    gpio_conf.pull_down_en  = GPIO_PULLDOWN_DISABLE;
    gpio_conf.pull_up_en    = GPIO_PULLUP_ENABLE;
    ESP_ERROR_CHECK_WITHOUT_ABORT(gpio_config(&gpio_conf));
    xTaskCreatePinnedToCore(PowerLedTask, "PowerLedTask", 3 * 1024, this, 2, NULL, 0);
}

BoardPowerBsp::~BoardPowerBsp() {
}

void BoardPowerBsp::PowerEpdOn() {
    gpio_hold_dis((gpio_num_t) epdPowerPin_);
    gpio_set_level((gpio_num_t) epdPowerPin_, 1);
    gpio_hold_en((gpio_num_t)epdPowerPin_);
}

void BoardPowerBsp::PowerEpdOff() {
    gpio_hold_dis((gpio_num_t) epdPowerPin_);
    gpio_set_level((gpio_num_t) epdPowerPin_, 0);
    gpio_hold_en((gpio_num_t)epdPowerPin_);
}

void BoardPowerBsp::PowerAmpOn() {
    gpio_hold_dis((gpio_num_t)audioAmpPin_);
    gpio_set_level((gpio_num_t) audioAmpPin_, 1);
    gpio_hold_en((gpio_num_t)audioAmpPin_);
}

void BoardPowerBsp::PowerAmpOff() {
    gpio_hold_dis((gpio_num_t)audioAmpPin_);
    gpio_set_level((gpio_num_t) audioAmpPin_, 0);
    gpio_hold_en((gpio_num_t)audioAmpPin_);
}

void BoardPowerBsp::PowerAudioOn() {
    gpio_hold_dis((gpio_num_t)audioPowerPin_);
    gpio_set_level((gpio_num_t) audioPowerPin_, 1);
    gpio_hold_en((gpio_num_t)audioPowerPin_);
}

void BoardPowerBsp::PowerAudioOff() {
    gpio_hold_dis((gpio_num_t)audioPowerPin_);
    gpio_set_level((gpio_num_t) audioPowerPin_, 0);
    gpio_hold_en((gpio_num_t)audioPowerPin_);
}

void BoardPowerBsp::VbatPowerOn() {
    gpio_hold_dis((gpio_num_t)vbatPowerPin_);
    gpio_set_level((gpio_num_t) vbatPowerPin_, 1);
    gpio_hold_en((gpio_num_t)vbatPowerPin_);
}

void BoardPowerBsp::VbatPowerOff() {
    gpio_hold_dis((gpio_num_t)vbatPowerPin_);
    gpio_set_level((gpio_num_t) vbatPowerPin_, 0);
    gpio_hold_en((gpio_num_t)vbatPowerPin_);
}

void BoardPowerBsp::SetFactoryLedOverride(bool enabled, bool blink) {
    led_override_enabled_.store(enabled, std::memory_order_relaxed);
    led_override_blink_.store(blink, std::memory_order_relaxed);
    led_override_phase_.store(false, std::memory_order_relaxed);
}
