"""Volcengine bidirectional streaming ASR for one 16 kHz PCM utterance."""
from __future__ import annotations
import asyncio
import gzip
import json
import struct
import uuid
import websockets


def _frame(kind: int, flags: int, serial: int, payload: bytes) -> bytes:
    data = gzip.compress(payload)
    return bytes((0x11, (kind << 4) | flags, (serial << 4) | 1, 0)) + struct.pack('>I', len(data)) + data


def _parse(frame: bytes):
    if len(frame) < 8:
        raise ValueError('short ASR frame')
    pos = (frame[0] & 15) * 4
    kind, flags = frame[1] >> 4, frame[1] & 15
    serial, compression = frame[2] >> 4, frame[2] & 15
    if kind == 15:
        code = struct.unpack_from('>I', frame, pos)[0] if pos + 4 <= len(frame) else 0
        raise RuntimeError(f'流式语音识别服务错误码 {code}')
    if kind != 9:
        return '', bool(flags & 2)
    if flags & 1:
        pos += 4
    if pos + 4 > len(frame):
        raise ValueError('invalid ASR frame')
    size = struct.unpack_from('>I', frame, pos)[0]
    pos += 4
    if size > 1048576 or pos + size > len(frame):
        raise ValueError('invalid ASR payload')
    body = frame[pos:pos + size]
    if compression == 1:
        body = gzip.decompress(body)
    if serial != 1:
        return '', bool(flags & 2)
    value = json.loads(body.decode('utf-8'))
    result = value.get('result', {}) if isinstance(value, dict) else {}
    text = result.get('text', '') if isinstance(result, dict) else ''
    return text.strip() if isinstance(text, str) else '', bool(flags & 2)


class VolcStream:
    def __init__(self, app_id, token, resource, url, on_partial):
        self.app_id, self.token, self.resource, self.url = app_id, token, resource, url
        self.on_partial = on_partial
        self.queue = asyncio.Queue(maxsize=64)
        self.task = asyncio.create_task(self._run())

    def feed(self, data: bytes):
        if self.task.done():
            raise RuntimeError('stream ended')
        self.queue.put_nowait(data)

    async def finish(self):
        if not self.task.done():
            await asyncio.wait_for(self.queue.put(None), timeout=2)
        return await asyncio.wait_for(self.task, timeout=20)

    async def cancel(self):
        if not self.task.done():
            self.task.cancel()
        try:
            await self.task
        except (asyncio.CancelledError, Exception):
            pass

    async def _run(self):
        headers = {'X-Api-App-Key': self.app_id, 'X-Api-Access-Key': self.token,
                   'X-Api-Resource-Id': self.resource, 'X-Api-Request-Id': str(uuid.uuid4()),
                   'X-Api-Sequence': '-1'}
        start = {'user': {'uid': 'pocketclaw'}, 'audio': {'format': 'pcm', 'rate': 16000, 'bits': 16, 'channel': 1},
                 'request': {'model_name': 'bigmodel', 'enable_itn': True, 'enable_punc': True}}
        async with websockets.connect(self.url, additional_headers=headers, open_timeout=8, max_size=1048576, proxy=None) as ws:
            await ws.send(_frame(1, 0, 1, json.dumps(start).encode()))
            _parse(await asyncio.wait_for(ws.recv(), timeout=8))
            async def sender():
                buffered = bytearray()
                while True:
                    data = await self.queue.get()
                    if data is None:
                        await ws.send(_frame(2, 2, 0, bytes(buffered)))
                        return
                    buffered.extend(data)
                    while len(buffered) >= 6400:
                        await ws.send(_frame(2, 0, 0, bytes(buffered[:6400])))
                        del buffered[:6400]
            async def reader():
                latest = ''
                while True:
                    text, last = _parse(await ws.recv())
                    if text and text != latest:
                        latest = text
                        await self.on_partial(text)
                    if last:
                        return latest
            send_task = asyncio.create_task(sender())
            read_task = asyncio.create_task(reader())
            try:
                done, _ = await asyncio.wait((send_task, read_task), return_when=asyncio.FIRST_COMPLETED)
                if read_task in done and not send_task.done():
                    await read_task
                    raise RuntimeError('流式语音服务提前结束')
                await send_task
                return await read_task
            finally:
                for task in (send_task, read_task):
                    if not task.done(): task.cancel()
                await asyncio.gather(send_task, read_task, return_exceptions=True)
