import struct

import httpx
import pytest

from pocketclaw import voice


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_remote_speech_contract(monkeypatch):
    requests = []

    async def handler(request):
        requests.append(request)
        if request.url.path.endswith('/transcriptions'):
            assert b'RIFF' in request.content
            return httpx.Response(200, json={'text': '你好'})
        assert b'"response_format":"pcm"' in request.content
        return httpx.Response(200, content=struct.pack('<hhhhhh', 0, 1000, 2000, 3000, 4000, 5000))

    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    stt = voice.RemoteSTT(voice._RemoteConfig(endpoint='https://example.test/transcriptions', api_key='key', model='asr'))
    tts = voice.RemoteTTS(voice._RemoteConfig(endpoint='https://example.test/speech', api_key='key', model='tts', voice='voice'))
    assert await stt.transcribe(b'\x00\x00' * 32) == '你好'
    result = await tts.synthesize('你好')
    assert struct.unpack('<hhhh', result) == (0, 1500, 3000, 4500)
    assert all(r.headers['authorization'] == 'Bearer key' for r in requests)


@pytest.mark.anyio
async def test_remote_voice_errors_are_not_silent(monkeypatch):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: real_client(transport=httpx.MockTransport(lambda request: httpx.Response(503)), **kwargs))
    tts = voice.RemoteTTS(voice._RemoteConfig(endpoint='https://example.test/speech'))
    with pytest.raises(httpx.HTTPStatusError):
        await tts.synthesize('你好')


@pytest.mark.anyio
async def test_ptt_too_long_is_discarded():
    import json
    from unittest.mock import AsyncMock
    from pocketclaw.bridge import PocketClawSession
    from pocketclaw import protocol
    send = AsyncMock()
    stt = AsyncMock()
    session = PocketClawSession(None, send_json=send, send_bin=AsyncMock(), stt=stt,
                               tts=AsyncMock(), registry=AsyncMock(), notes=AsyncMock())
    session.helloed = True
    await session.on_control({'kind': protocol.PTT_START})
    await session.on_binary(b'\x00' * (45 * protocol.AUDIO_SAMPLE_RATE * 2 + 1))
    assert not session._recording and not session._ptt
    assert json.loads(send.await_args.args[0])['code'] == 'audio_too_long'
    await session.on_control({'kind': protocol.PTT_STOP})
    stt.transcribe.assert_not_awaited()
    await session.close()


@pytest.mark.anyio
async def test_streamed_audio_can_be_interrupted_before_provider_finishes():
    import asyncio
    import json
    from unittest.mock import AsyncMock
    from pocketclaw.bridge import PocketClawSession
    from pocketclaw import protocol
    first_sent = asyncio.Event()
    provider_cancelled = asyncio.Event()
    sent = []

    class StreamingTTS:
        sample_rate = 16000
        async def synthesize_stream(self, text):
            try:
                yield b'\x01\x00' * 960
                await asyncio.Event().wait()
            finally:
                provider_cancelled.set()

    async def send_json(frame):
        sent.append(json.loads(frame)['kind'])

    async def send_bin(frame):
        sent.append('audio')
        first_sent.set()

    session = PocketClawSession(None, send_json=send_json, send_bin=send_bin, stt=AsyncMock(),
                               tts=StreamingTTS(), registry=AsyncMock(), notes=AsyncMock())
    await session._speak('测试')
    await asyncio.wait_for(first_sent.wait(), 1)
    assert sent[:2] == ['tts_start', 'audio']
    await session._abort_speech(reason='test', send_stop=True)
    assert provider_cancelled.is_set()
    assert sent[-1] == 'tts_stop'
    assert 'tts_end' not in sent
    await session.close()


@pytest.mark.anyio
async def test_wake_no_speech_cancels_recording_without_transcription():
    from unittest.mock import AsyncMock
    from pocketclaw.bridge import PocketClawSession
    from pocketclaw import protocol
    stt = AsyncMock()
    session = PocketClawSession(None, send_json=AsyncMock(), send_bin=AsyncMock(), stt=stt,
                               tts=AsyncMock(), registry=AsyncMock(), notes=AsyncMock())
    session.helloed = True
    await session.on_control({'kind': protocol.PTT_START})
    await session.on_binary(b'\x00\x00' * 8000)
    await session.on_control({'kind': 'ptt_cancel'})
    await session.on_control({'kind': protocol.PTT_STOP})
    assert not session._recording and not session._ptt
    stt.transcribe.assert_not_awaited()
    await session.close()


@pytest.mark.anyio
async def test_incremental_stt_feeds_audio_and_uses_final_without_batch():
    import asyncio
    from unittest.mock import AsyncMock
    from pocketclaw.bridge import PocketClawSession
    from pocketclaw import protocol
    class Stream:
        def __init__(self): self.chunks = []; self.cancelled = False
        def feed(self, data): self.chunks.append(data)
        async def finish(self): return '流式识别结果'
        async def cancel(self): self.cancelled = True
    class STT:
        supports_streaming = True
        def __init__(self): self.stream = Stream(); self.transcribe = AsyncMock()
        def start_stream(self, callback): self.callback = callback; return self.stream
    stt = STT()
    session = PocketClawSession(None, send_json=AsyncMock(), send_bin=AsyncMock(), stt=stt,
                               tts=AsyncMock(), registry=AsyncMock(), notes=AsyncMock())
    session.helloed = True
    session.mode = 'chat'
    session._on_utterance = AsyncMock()
    await session.on_control({'kind': protocol.PTT_START})
    await session.on_binary(b'\x00\x00' * 100)
    await stt.callback('中间结果')
    assert any('stt_partial' in str(call.args[0]) for call in session.send_json.await_args_list)
    await session.on_control({'kind': protocol.PTT_STOP})
    if session._turn_task:
        await session._turn_task
    assert stt.stream.chunks == [b'\x00\x00' * 100]
    assert stt.stream.cancelled
    assert session._on_utterance.await_args.kwargs['transcript'] == '流式识别结果'
    stt.transcribe.assert_not_awaited()
    await session.close()
