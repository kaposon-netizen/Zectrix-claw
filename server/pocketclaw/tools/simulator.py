"""PocketClaw 设备模拟器：不用真机，端到端验证服务端 T1 语音链路。

它扮演一台便利贴：连 ws://<server>/pocketclaw，走 hello → 按住说话 → 收 stt/display/tts。
语音用 debug 旁路：ptt_start 里带 `text` 当识别结果（离线 STT 直接采用），
所以无需真麦克风即可跑通「说一句 → 墨水屏出内容 → 收到 TTS 音频」。

用法：
  # 先起服务端：  uvicorn app:app --port 8000
  python -m pocketclaw.tools.simulator --url ws://127.0.0.1:8000/pocketclaw \
      --say "这道解方程不会" --say "记一下 我想养只小龙虾" --say "加单词 apple 苹果" --say "背单词"

  # 交互模式（回车逐句说话，Ctrl-C 退出）：
  python -m pocketclaw.tools.simulator -i

依赖：websockets（venv 已装）。TTS 音频只统计字节，不播放。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

try:
    import websockets
except ImportError:                     # pragma: no cover
    print("需要 websockets：pip install websockets", file=sys.stderr)
    raise

DEVICE_ID = "sim-note-01"
DEVICE_MAC = "44:1B:F6:C4:D8:B0"        # 与真机一致，便于绑定演示


def _render_page(page: dict) -> str:
    """把 DisplayPage 画成一个 ASCII 的 400×300 墨水屏预览。"""
    W = 40
    top = "┌" + "─" * W + "┐"
    bot = "└" + "─" * W + "┘"
    lines = []
    title = page.get("title") or ("💬 便利贴" if page.get("mode") == "chat" else page.get("mode", ""))
    if page.get("heard"):
        lines.append("你说：" + " ".join(page["heard"]))
    if title:
        lines.append("【%s】" % title)
    if page.get("banner"):
        lines.append("⚠ " + page["banner"])
    for b in page.get("body", []):
        lines.append(b)
    if page.get("chips"):
        lines.append("  ".join("[%s]" % c for c in page["chips"]))
    if page.get("truncated"):
        lines.append("…(更多见台灯屏)")
    foot = page.get("footer", {})
    if foot.get("battery") is not None:
        lines.append("🔋 %d%%" % foot["battery"])
    body = "\n".join("│ " + ln[:W - 2].ljust(W - 2) + " │" for ln in lines[:12])
    return "\n".join([top, body, bot])


async def _recv_until_idle(ws, timeout=8.0):
    """收帧直到一轮结束（tts_end 或超时）。返回收到的 kind 序列。"""
    kinds, tts_bytes = [], 0
    while True:
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
        except asyncio.TimeoutError:
            break
        if isinstance(msg, (bytes, bytearray)):
            tts_bytes += len(msg)
            kinds.append("audio")
            continue
        d = json.loads(msg)
        k = d.get("kind")
        kinds.append(k)
        if k == "stt":
            print("  🎙  识别：", d["text"])
        elif k == "display":
            print(_render_page(d["page"]))
        elif k == "hello_ack":
            print("  ✓ 握手：child=%s sync=%ss" % (d["child_id"], d["sync_period_s"]))
        elif k == "error":
            print("  ✗ 错误：", d.get("message"))
        elif k == "tts_end":
            if tts_bytes:
                secs = tts_bytes / (16000 * 2)
                print("  🔊 收到 TTS 音频 %d 字节（~%.1fs）" % (tts_bytes, secs))
            break
    return kinds


async def _say(ws, text: str):
    print("\n──────── 我按住说话：%r ────────" % text)
    await ws.send(json.dumps({"kind": "ptt_start", "text": text}))
    await ws.send(b"\x00\x00" * 1600)        # 0.1s 静音 PCM 占位（真机是真录音）
    await ws.send(json.dumps({"kind": "ptt_stop"}))
    await _recv_until_idle(ws)


async def _nfc(ws, ndef: str):
    print("\n──────── 我贴了一张 NFC 卡：%s ────────" % ndef)
    await ws.send(json.dumps({"kind": "nfc", "uid": "sim-" + ndef, "ndef": ndef}))
    await _recv_until_idle(ws)


async def _button(ws, name: str):
    print("\n──────── 我按了物理键：%s ────────" % name)
    await ws.send(json.dumps({"kind": "button", "name": name}))
    await _recv_until_idle(ws)


async def _demo(ws):
    """一遍跑通五个功能：#1 语音单词卡 · #8 语音速记 · #11 作业清单 · #18/#19 NFC 卡。"""
    await _say(ws, "光合作用是什么意思")          # #1 语音单词卡（查词→出卡→入复习盒）
    await _say(ws, "记一下 我想周末去看恐龙展")     # #8 语音速记
    await _say(ws, "加任务 背10个单词")            # #11 作业清单
    await _say(ws, "加任务 口算20题")
    await _button(ws, "todo")                     # 物理键看清单
    await _say(ws, "完成 背10个单词")              # 勾掉一项
    await _nfc(ws, "subject:数学")                # #18 学科卡
    await _nfc(ws, "word:tiger")                  # #19 单词收集卡
    await _say(ws, "背单词")                       # 复习（每日一词）


async def run(url: str, says, interactive: bool, demo: bool = False):
    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({"kind": "hello", "device_id": DEVICE_ID,
                                  "mac": DEVICE_MAC, "fw": "sim-0.1", "battery": 82}))
        await _recv_until_idle(ws, timeout=3.0)
        if demo:
            await _demo(ws)
        for s in says:
            await _say(ws, s)
        if interactive:
            print("\n交互模式：输入要说的话，回车发送；Ctrl-C 退出。")
            loop = asyncio.get_event_loop()
            while True:
                try:
                    text = await loop.run_in_executor(None, input, "🗣  ")
                except (EOFError, KeyboardInterrupt):
                    break
                if text.strip():
                    await _say(ws, text.strip())


def main():
    ap = argparse.ArgumentParser(description="PocketClaw 设备模拟器")
    ap.add_argument("--url", default="ws://127.0.0.1:8000/pocketclaw")
    ap.add_argument("--say", action="append", default=[], help="要说的一句话（可多次）")
    ap.add_argument("--demo", action="store_true", help="跑一遍五大功能演示（#1/#8/#11/#18/#19）")
    ap.add_argument("-i", "--interactive", action="store_true")
    a = ap.parse_args()
    says = a.say or ([] if (a.interactive or a.demo) else
                     ["这道解方程不会", "记一下 我想养只小龙虾", "加单词 apple 苹果", "背单词"])
    try:
        asyncio.run(run(a.url, says, a.interactive, a.demo))
    except (ConnectionRefusedError, OSError) as e:
        print("连不上 %s —— 先起服务端：uvicorn app:app --port 8000\n(%s)" % (a.url, e), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
