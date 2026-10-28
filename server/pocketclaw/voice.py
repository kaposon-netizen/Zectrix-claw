"""语音接入层：STT（PCM→文本）/ TTS（文本→PCM），可插拔 + 离线兜底。

具备「零 API Key 离线也能完整运行」的自包含设计哲学：
  · 未配置真实语音服务 → 走 OfflineSTT/OfflineTTS，端到端仍可自测（配模拟器）。
  · 配了 env（POCKETCLAW_STT_* / POCKETCLAW_TTS_*）→ 走真实供应商（预留接口，支持 OpenAI 兼容接口）。

STT 约定：吃裸 PCM16LE mono 16k（bridge 攒完一段 PTT 后整段送）。
TTS 约定：吐裸 PCM16LE mono（sample_rate 由 provider 决定，默认 16k），bridge 分帧下发。

OfflineSTT 的说明（重要）：离线环境无法真的转写语音。为让「设备模拟器→服务端→回屏」
这条链路能真实跑通并断言，OfflineSTT 支持一个 **debug 旁路**：若本段 PTT 附带了
hint 文本（模拟器通过 `text`/`hint_text` 传入），直接把它当识别结果返回。真机上没有
hint，则返回一句明确的占位提示，促使接真实 STT。
"""
from __future__ import annotations

import math
import os
import struct
from dataclasses import dataclass
from typing import Optional, Protocol

from pocketclaw.protocol import AUDIO_SAMPLE_RATE


class STTProvider(Protocol):
    def transcribe(self, pcm: bytes, *, hint: Optional[str] = None) -> str: ...


class TTSProvider(Protocol):
    sample_rate: int
    def synthesize(self, text: str) -> bytes: ...


# ── 离线兜底实现 ─────────────────────────────────────────────
class OfflineSTT:
    """离线 STT：debug 旁路优先返回 hint；否则给占位提示。"""

    name = "offline"

    async def transcribe(self, pcm: bytes, *, hint: Optional[str] = None) -> str:
        if hint:
            return hint.strip()
        secs = len(pcm) / (AUDIO_SAMPLE_RATE * 2) if pcm else 0.0
        if secs < 0.2:
            return ""
        return "（离线语音识别未配置：请在家长端配置 STT 后再用语音）"


class OfflineTTS:
    """离线 TTS：不发声内容，合成一段与文本长度成正比的低幅提示音（PCM16）。

    真机上会听到「嘟」的应答音，证明链路通了；接真实 TTS 后替换。"""

    name = "offline"
    sample_rate = AUDIO_SAMPLE_RATE

    async def synthesize(self, text: str) -> bytes:
        n_chars = max(1, len(text or ""))
        dur = min(2.5, 0.12 + 0.03 * n_chars)     # 0.12~2.5s
        n = int(self.sample_rate * dur)
        freq, amp = 440.0, 1800                    # A4，低幅（不刺耳）
        out = bytearray()
        for i in range(n):
            # 头尾各 5ms 淡入淡出，避免爆音
            env = min(1.0, i / (self.sample_rate * 0.005),
                      (n - i) / (self.sample_rate * 0.005))
            s = int(amp * env * math.sin(2 * math.pi * freq * (i / self.sample_rate)))
            out += struct.pack("<h", s)
        return bytes(out)


# ── 真实供应商占位（OpenAI 兼容 / Coze / 火山），支持 OpenAI 兼容接口 ──
@dataclass
class _RemoteConfig:
    endpoint: str = ""
    api_key: str = ""
    model: str = ""
    voice: str = ""


class RemoteSTT:
    """OpenAI-compatible multipart transcription of 16 kHz mono PCM."""

    name = "remote"

    def __init__(self, cfg: _RemoteConfig):
        self.cfg = cfg

    async def transcribe(self, pcm: bytes, *, hint: Optional[str] = None) -> str:
        if not pcm:
            return (hint or "").strip()
        import httpx
        headers = {"Authorization": f"Bearer {self.cfg.api_key}"} if self.cfg.api_key else {}
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(self.cfg.endpoint, headers=headers,
                data={"model": self.cfg.model or "whisper-1"},
                files={"file": ("speech.wav", _pcm16_to_wav(pcm), "audio/wav")})
            response.raise_for_status()
            result = response.json()
        return str(result.get("text", "")).strip()


class RemoteTTS:
    """OpenAI-compatible speech API: 24 kHz PCM converted for the 16 kHz device."""
    name = "remote"
    sample_rate = AUDIO_SAMPLE_RATE

    def __init__(self, cfg: _RemoteConfig):
        self.cfg = cfg

    async def synthesize(self, text: str) -> bytes:
        import httpx
        headers = {"Authorization": f"Bearer {self.cfg.api_key}"} if self.cfg.api_key else {}
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(self.cfg.endpoint, headers=headers, json={
                "model": self.cfg.model or "tts-1", "voice": self.cfg.voice or "alloy",
                "input": text, "response_format": "pcm",
            })
            response.raise_for_status()
            pcm = response.content
        if len(pcm) % 2:
            raise ValueError("语音服务返回了不完整的 PCM 样本")
        samples = struct.unpack("<" + "h" * (len(pcm) // 2), pcm)
        # Linear interpolation at positions 0, 1.5, 3, ... (24 kHz -> 16 kHz).
        out = bytearray()
        for i in range((len(samples) * 2) // 3):
            pos = i * 3
            left = pos // 2
            value = samples[left] if pos % 2 == 0 or left + 1 >= len(samples) else (samples[left] + samples[left + 1]) // 2
            out.extend(struct.pack("<h", value))
        return bytes(out)


# ── 工厂：按 env 选供应商，缺省离线 ──
def _pcm16_to_wav(pcm: bytes, sample_rate: int = 16000) -> bytes:
    """裸 PCM16LE mono → WAV 字节（补 44 字节头，供 faster-whisper/PyAV 解码）。"""
    import struct
    n = len(pcm)
    return (b"RIFF" + struct.pack("<I", 36 + n) + b"WAVE"
            + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16)
            + b"data" + struct.pack("<I", n) + pcm)


_shared_asr = None


def _get_local_asr():
    """懒加载本地 faster-whisper（pocketclaw 复用主链路同款 small int8）。"""
    global _shared_asr
    if _shared_asr is None:
        try:
            from agent.local_models import LocalASR, asr_available
            _shared_asr = LocalASR("small") if asr_available() else False
        except Exception:
            _shared_asr = False
    return _shared_asr or None


class NativeWhisperSTT:
    """服务端本地 faster-whisper 转写 pocketclaw 的裸 PCM16@16000。"""

    name = "whisper-local"

    def __init__(self, asr):
        self.asr = asr

    async def transcribe(self, pcm: bytes, *, hint: Optional[str] = None) -> str:
        if not pcm:
            return (hint or "").strip()
        try:
            wav = _pcm16_to_wav(pcm, AUDIO_SAMPLE_RATE)
            text = self.asr.transcribe_bytes(wav, suffix=".wav", language="zh")
            return (text or "").strip()
        except Exception:
            return ""


class CloudASR:
    """复用主链路 voice.transcribe（云端火山 ASR，异步）。裸 PCM16 → WAV 送云端。"""

    name = "cloud-volc"

    def __init__(self, voice):
        self.voice = voice

    async def transcribe(self, pcm: bytes, *, hint: Optional[str] = None) -> str:
        if not pcm:
            return (hint or "").strip()
        try:
            wav = _pcm16_to_wav(pcm, AUDIO_SAMPLE_RATE)
            r = await self.voice.transcribe(wav, filename="pocketclaw.wav",
                                            content_type="audio/wav")
            if isinstance(r, dict):
                return (r.get("text") or "").strip()
            return ""
        except Exception:
            return ""



class VolcFlashSTT:
    """火山录音文件极速识别，接收设备 PCM 并封装成 WAV。"""
    name = "volc-flash"

    def __init__(self, app_id: str, token: str, resource_id: str, url: str):
        self.app_id, self.token, self.resource_id, self.url = app_id, token, resource_id, url

    async def transcribe(self, pcm: bytes, *, hint: Optional[str] = None) -> str:
        if not pcm:
            return (hint or "").strip()
        import base64
        import uuid
        import httpx
        headers = {"X-Api-App-Key": self.app_id, "X-Api-Access-Key": self.token,
                   "X-Api-Resource-Id": self.resource_id, "X-Api-Request-Id": str(uuid.uuid4()),
                   "X-Api-Sequence": "-1"}
        payload = {"user": {"uid": "pocketclaw"},
                   "audio": {"format": "wav", "rate": 16000, "bits": 16, "channel": 1,
                             "data": base64.b64encode(_pcm16_to_wav(pcm)).decode("ascii")},
                   "request": {"model_name": "bigmodel", "enable_itn": True, "enable_punc": True}}
        async with httpx.AsyncClient(timeout=12.0) as client:
            response = await client.post(self.url, headers=headers, json=payload)
            response.raise_for_status()
            status = response.headers.get("X-Api-Status-Code", "")
            if status and status != "20000000":
                raise RuntimeError("语音识别服务返回失败状态")
            result = response.json().get("result", {})
        if isinstance(result, dict):
            text = result.get("text", "")
            return text.strip() if isinstance(text, str) else ""
        return ""

class VolcStreamingSTT(VolcFlashSTT):
    """Realtime ASR with recorded-audio fallback when the stream fails."""
    name = "volc-streaming"
    supports_streaming = True

    def start_stream(self, on_partial):
        from pocketclaw.streaming_stt import VolcStream
        return VolcStream(self.app_id, self.token,
            os.environ.get("POCKETCLAW_VOLC_STREAM_RESOURCE_ID", "volc.seedasr.sauc.duration"),
            os.environ.get("POCKETCLAW_VOLC_STREAM_URL", "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"), on_partial)


def make_stt(voice=None) -> STTProvider:
    if os.environ.get("POCKETCLAW_STT_ENDPOINT"):
        return RemoteSTT(_RemoteConfig(
            endpoint=os.environ["POCKETCLAW_STT_ENDPOINT"],
            api_key=os.environ.get("POCKETCLAW_STT_KEY", ""),
            model=os.environ.get("POCKETCLAW_STT_MODEL", ""),
        ))
    app_id = os.environ.get("VOLC_ASR_APP_ID", "").strip()
    token = os.environ.get("VOLC_ASR_ACCESS_TOKEN", "").strip()
    if app_id and token:
        provider = VolcStreamingSTT if os.environ.get("POCKETCLAW_VOLC_ASR_STREAMING") == "1" else VolcFlashSTT
        return provider(app_id, token,
            os.environ.get("POCKETCLAW_VOLC_ASR_RESOURCE_ID", "volc.bigasr.auc_turbo"),
            os.environ.get("POCKETCLAW_VOLC_ASR_URL", "https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash"))
    # 优先：复用平板同款云端火山 ASR（voice.transcribe，异步、快）
    if voice is not None:
        return CloudASR(voice)
    return OfflineSTT()


class NativeVolcTTS:
    """服务端原生火山 seed-tts-2.0，直接出 PCM16@16000（设备裸播，真语音）。

    复用主链路同一套 tts_stream 凭证；请求 format=pcm 免解码/重采样。境内模型，合红线。"""

    name = "volc-seed"
    sample_rate = 16000
    _ENDPOINT = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"

    def __init__(self, app_id: str, token: str, voice_id: str, resource_id: str = "seed-tts-2.0"):
        self.app_id = app_id
        self.token = token
        self.voice_id = voice_id
        self.resource_id = resource_id
        self._client = None
        self._client_loop = None

    def _get_client(self):
        import asyncio
        import httpx
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if self._client is None or self._client.is_closed or self._client_loop is not loop:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=6.0, read=15.0, write=10.0, pool=5.0),
                trust_env=False,
            )
            self._client_loop = loop
        return self._client

    async def synthesize(self, text: str) -> bytes:
        """整段版（保留给单词发音等需要完整 PCM 的场景）。对话朗读请用 synthesize_stream。"""
        out = bytearray()
        async for chunk in self.synthesize_stream(text):
            out.extend(chunk)
        return bytes(out)

    async def synthesize_stream(self, text: str):
        """**流式**：火山那边每吐一块 PCM 就 yield 一块，不等整段合成完。

        实测（pocketclaw 便利贴）：整段版首声要等 6.6s（文字 4.5s 就出来了，白等 2 秒多）；
        流式版第一块到了就能出声，首声压到 ~2s。设备侧本来就有 200ms 预缓冲 + 1s 抖动缓冲。
        复用长连接池（Keep-Alive），省去句间重复 TCP + TLS 握手耗时。
        """
        import base64
        import json as _json
        import uuid
        headers = {
            "X-Api-App-Id": self.app_id,
            "X-Api-Access-Key": self.token,
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-Request-Id": str(uuid.uuid4()),
        }
        body = {
            "user": {"uid": "pocketclaw"},
            "req_params": {
                "text": (text or "")[:600],
                "speaker": self.voice_id,
                "audio_params": {"format": "pcm", "sample_rate": 16000},
                "additions": _json.dumps({"enable_language_detector": True}),
            },
        }
        try:
            c = self._get_client()
            got_audio = False
            async with c.stream("POST", self._ENDPOINT, headers=headers, json=body) as r:
                if r.status_code != 200:
                    raise RuntimeError("语音合成服务请求失败")
                async for raw in r.aiter_lines():
                    line = raw.strip()
                    if not line or line.startswith("event:"):
                        continue
                    if line.startswith("data:"):
                        line = line[5:].strip()
                    try:
                        obj = _json.loads(line)
                    except (ValueError, _json.JSONDecodeError):
                        continue
                    data = obj.get("data")
                    if data:
                        try:
                            chunk = base64.b64decode(data, validate=True)
                            if chunk:
                                got_audio = True
                                yield chunk   # 到一块发一块 → 首声快
                        except Exception:
                            raise RuntimeError("语音合成服务返回无效音频") from None
                    code = obj.get("code")
                    if code == 20000000:
                        break
                    if code not in (None, 0, 20000000):
                        raise RuntimeError("语音合成服务返回失败状态")
            if not got_audio:
                raise RuntimeError("语音合成服务未返回音频")
        except Exception as exc:
            if self._client is not None:
                try:
                    await self._client.aclose()
                except Exception:
                    pass
                self._client = None
            raise RuntimeError("语音合成暂时不可用") from exc


def make_tts() -> TTSProvider:
    if os.environ.get("POCKETCLAW_TTS_ENDPOINT"):
        return RemoteTTS(_RemoteConfig(
            endpoint=os.environ["POCKETCLAW_TTS_ENDPOINT"],
            api_key=os.environ.get("POCKETCLAW_TTS_KEY", ""),
            voice=os.environ.get("POCKETCLAW_TTS_VOICE", ""),
            model=os.environ.get("POCKETCLAW_TTS_MODEL", ""),
        ))
    app_id = os.environ.get("VOLC_TTS_APP_ID", "").strip()
    token = os.environ.get("VOLC_TTS_ACCESS_TOKEN", "").strip()
    if app_id and token:
        return NativeVolcTTS(app_id, token,
            os.environ.get("VOLC_TTS_VOICE", "zh_male_tiancaitongsheng_uranus_bigtts"),
            os.environ.get("VOLC_TTS_RESOURCE_ID", "seed-tts-2.0"))
    return OfflineTTS()
