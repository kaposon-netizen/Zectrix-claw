#ifndef TAP_FRIEND_H_
#define TAP_FRIEND_H_

// BLE 碰一碰交友（Note 4 Tap）。按需模式：
//   上键长按触发 → POST /api/plaza/tap/start 拿 nonce → BLE 广播自己的 nonce 前缀
//   + 扫描对方广播（≥3 个 RSSI 采样且都 ≥ -50dBm 才算「贴在一起」）→ report →
//   主键确认 → confirm → 「等爸爸妈妈点头哦」。全程 90 秒（服务端 ttl）超时。
// BLE 广播/扫描只在会话期间开启，会话结束即关（省电）；不触碰语音链路。
//
// 广播格式（Legacy ADV，≤31 字节）：
//   AD1: Flags (0x06)
//   AD2: Manufacturer Specific Data, Company ID 0xFFFF（测试用），
//        载荷 = 魔数 "KVT1"(4B) + nonce 前 16 字节（ASCII，原样）。
// 扫描侧按魔数过滤，取出 nonce 前缀作为 seen_nonce 上报（服务端按前缀匹配）。

#include <atomic>

class TapFriend {
public:
    static TapFriend& GetInstance() {
        static TapFriend instance;
        return instance;
    }
    TapFriend(const TapFriend&) = delete;
    TapFriend& operator=(const TapFriend&) = delete;

    void StartSession();      // 触发碰一碰；已在进行中则忽略
    bool IsActive() const { return active_.load(std::memory_order_acquire); }

    // 等待确认阶段按下主键 → 吃掉这次按键当作「好呀」。
    // 返回 true 表示按键已被消费，调用方（PTT）不要再处理。
    bool ConsumeConfirm();

private:
    TapFriend() = default;
    void SessionTask();
    static void SessionTaskEntry(void* arg);

    std::atomic<bool> active_{false};
    std::atomic<bool> awaiting_confirm_{false};
    std::atomic<bool> confirmed_{false};
};

#endif  // TAP_FRIEND_H_
