"""PocketClaw 设备⇄服务端 线协议（T1 · 极简 PCM16-over-WS）。

参照社区已在同板验证的 vibecoding-plus（PTT → 16k 单声道 PCM16 走 WebSocket），
控制消息采用统一的 JSON `kind` 判别，规范化各端事件路由。

分帧极简，直接借 WebSocket 自带的「文本帧 / 二进制帧」区分：
  · **文本帧** = 控制消息（UTF-8 JSON，带 `kind`）。
  · **二进制帧** = 裸音频 PCM16LE，单声道 16kHz（PTT 上行 / TTS 下行都用它）。
无自定义二进制头 → 设备端零解析负担，服务端拿到裸 PCM 直接送 STT。

墨水屏刷新慢 → **不做逐字流式**：识别文本、AI 回复都按「整屏」下发一次 `display`。

────────────────────────────────────────────────────────────────────────
设备 → 服务端（device→server）
  hello        {device_id, mac, fw, child_id?, battery?, features?}   建链握手
  ptt_start    {sample_rate?}                                          按住说话开始
  <binary>     PCM16LE mono                                            语音数据（可多帧）
  ptt_stop     {}                                                      松手 → 触发一轮
  nfc          {uid, ndef?}                                            贴卡（换孩子/功能卡）
  button       {name: todo|confirm|up|down}                            物理键
  note         {text}                                                  记录想法（文本直录）
  heartbeat    {battery?, charging?}                                   保活/上报电量
  text         {text}                                                  调试用文本问答通道

服务端 → 设备（server→device）
  hello_ack    {session, child_id, sync_period_s, display:{w,h,color}} 握手回执
  stt          {text}                                                  识别结果（先上屏）
  display      {page:{...DisplayPage}}                                 整屏渲染指令
  tts_start    {sample_rate}                                           语音回复开始
  <binary>     PCM16LE mono                                            TTS 音频（可多帧）
  tts_end      {}                                                      语音回复结束
  error        {code, message}                                         错误
  sleep        {wake_in_s}                                             指示设备深睡到点唤醒
────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from pocketclaw.display import screen_page, screen_text   # 上屏字形清洗（display 不反向 import 本模块）

# ── 音频规格（设备与服务端必须一致）──
AUDIO_SAMPLE_RATE = 16000          # Hz，config.h: AUDIO_INPUT_SAMPLE_RATE
AUDIO_CHANNELS = 1                 # 单声道（ES8311 单麦）
AUDIO_SAMPLE_WIDTH = 2             # int16 → 2 bytes（PCM16LE）
AUDIO_FRAME_MS = 60               # 每帧建议时长（与上游 server_frame_duration 对齐）
BYTES_PER_SECOND = AUDIO_SAMPLE_RATE * AUDIO_CHANNELS * AUDIO_SAMPLE_WIDTH

# ── 控制消息 kind（避免拼写漂移用常量）──
FOCUS_DONE = "focus_done"
FOCUS_STATE = "focus_state"
# device → server
HELLO = "hello"
PTT_START = "ptt_start"
PTT_STOP = "ptt_stop"
NFC = "nfc"
BUTTON = "button"
NOTE = "note"
HEARTBEAT = "heartbeat"
TEXT = "text"
# server → device
HELLO_ACK = "hello_ack"
STT = "stt"
DISPLAY = "display"
TTS_START = "tts_start"
TTS_END = "tts_end"
TTS_STOP = "tts_stop"
ERROR = "error"
SLEEP = "sleep"

DEVICE_KINDS = {HELLO, PTT_START, PTT_STOP, NFC, BUTTON, NOTE, HEARTBEAT, TEXT}
SCHEMA_VERSION = "pc-0.1"


@dataclass
class Hello:
    """设备握手信息（从 hello 控制帧解析）。"""
    device_id: str = ""
    mac: str = ""
    fw: str = ""
    child_id: str = ""
    battery: Optional[int] = None
    features: Tuple[str, ...] = ()

    @classmethod
    def parse(cls, msg: Dict[str, Any]) -> "Hello":
        return cls(
            device_id=str(msg.get("device_id") or ""),
            mac=_norm_mac(msg.get("mac") or ""),
            fw=str(msg.get("fw") or ""),
            child_id=str(msg.get("child_id") or ""),
            battery=_as_int(msg.get("battery")),
            features=tuple(str(f) for f in (msg.get("features") or [])),
        )


def _as_int(v: Any) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _norm_mac(mac: str) -> str:
    """统一 MAC 为大写冒号格式（官方云与本地绑定都按 MAC 认领设备）。"""
    hexs = "".join(c for c in str(mac) if c in "0123456789abcdefABCDEF").upper()
    if len(hexs) == 12:
        return ":".join(hexs[i:i + 2] for i in range(0, 12, 2))
    return str(mac).upper().strip()


# ── 解析（服务端收）──
def parse_control(raw: str) -> Optional[Dict[str, Any]]:
    """把一条文本控制帧解析成 dict；非法 JSON / 无 kind → None（不崩、前向兼容）。"""
    try:
        msg = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(msg, dict) or not msg.get("kind"):
        return None
    return msg


# ── 构造（服务端发）──
def _frame(kind: str, **fields: Any) -> str:
    return json.dumps({"kind": kind, "v": SCHEMA_VERSION, **fields}, ensure_ascii=False)


def hello_ack(session: str, child_id: str, *, sync_period_s: int = 3600,
              display_w: int = 400, display_h: int = 300, color: str = "bw") -> str:
    return _frame(HELLO_ACK, session=session, child_id=child_id,
                  sync_period_s=sync_period_s,
                  display={"w": display_w, "h": display_h, "color": color})


def stt_result(text: str) -> str:
    """识别文本先上屏（固件自加「你说：」前缀、24px 思源 slim 渲染）→ 同样按字库清洗。"""
    return _frame(STT, text=screen_text(text))


def display_page(page: Dict[str, Any]) -> str:
    """整屏出站前最后一道字库清洗（display.screen_page，幂等）：display.py 的渲染器已先洗过，这里兜住
    display.py 之外拼出来的页（focus.py 花火屏、将来新加的页），一页都不许带表外字形上屏。"""
    return _frame(DISPLAY, page=screen_page(page))


def tts_start(sample_rate: int = AUDIO_SAMPLE_RATE) -> str:
    return _frame(TTS_START, sample_rate=sample_rate)


def tts_end() -> str:
    return _frame(TTS_END)


def tts_stop() -> str:
    """立即打断/停止播报：通知设备立即关闭功放输出并清空抖动缓冲。"""
    return _frame(TTS_STOP)


def error(code: str, message: str = "") -> str:
    return _frame(ERROR, code=code, message=message)


def focus_start(total_s: int, today_min: int = 0, mode: str = "pomodoro",
                style: str = "spark") -> str:
    """开一轮番茄钟：**秒与画面都在设备本地**，服务端只发这一帧（设备 features 含 focus_local 时）。

    style：spark=火花引线（默认）/ candle=蜡烛。孩子不说「蜡烛」就一直是火花。
    """
    return _frame("focus_start", total_s=int(total_s), today_min=int(today_min),
                  mode=mode, style=style)


def focus_stop() -> str:
    return _frame("focus_stop")


def focus_pause(paused: bool) -> str:
    return _frame("focus_pause", paused=bool(paused))


def sleep(wake_in_s: int) -> str:
    return _frame(SLEEP, wake_in_s=wake_in_s)


# ── 音频工具 ──
def pcm_duration_s(pcm: bytes) -> float:
    """裸 PCM16LE mono 的时长（秒）。"""
    if not pcm:
        return 0.0
    return len(pcm) / float(BYTES_PER_SECOND)


def frame_pcm(pcm: bytes, frame_ms: int = AUDIO_FRAME_MS):
    """把整段 PCM 切成 frame_ms 的块，便于按帧下发（生成器）。"""
    step = max(1, int(BYTES_PER_SECOND * frame_ms / 1000))
    step -= step % AUDIO_SAMPLE_WIDTH  # 对齐到样本边界
    for i in range(0, len(pcm), step):
        yield pcm[i:i + step]
