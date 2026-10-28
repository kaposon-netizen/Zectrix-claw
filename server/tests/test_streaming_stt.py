import asyncio
import json
import pytest
from pocketclaw.streaming_stt import VolcStream, _frame


@pytest.fixture
def anyio_backend(): return 'asyncio'


@pytest.mark.anyio
async def test_provider_streams_audio_before_end_and_returns_final(monkeypatch):
    import pocketclaw.streaming_stt as streaming
    sent_audio = asyncio.Event()
    class Socket:
        def __init__(self): self.messages = asyncio.Queue()
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def send(self, frame):
            kind, flags = frame[1] >> 4, frame[1] & 15
            if kind == 1:
                await self.messages.put(_frame(9, 0, 1, b'{}'))
            elif kind == 2 and flags == 0:
                sent_audio.set()
                await self.messages.put(_frame(9, 0, 1, json.dumps({'result': {'text': '中间'}}).encode()))
            elif kind == 2:
                await self.messages.put(_frame(9, 2, 1, json.dumps({'result': {'text': '最终'}}).encode()))
        async def recv(self): return await self.messages.get()
    monkeypatch.setattr(streaming.websockets, 'connect', lambda *args, **kwargs: Socket())
    partial = []
    async def on_partial(text): partial.append(text)
    stream = VolcStream('app', 'token', 'resource', 'wss://example.test', on_partial)
    stream.feed(bytes(6400))
    await asyncio.wait_for(sent_audio.wait(), 1)
    assert await stream.finish() == '最终'
    assert partial == ['中间', '最终']
    await stream.cancel()
