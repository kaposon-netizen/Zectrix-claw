import io
import pytest
from PIL import Image
from pocketclaw.screen_snapshot import framebuffer_png


def test_framebuffer_formats_preserve_pixel_order():
    one_bit = bytes([0b10000000]) + bytes(14999)
    image = Image.open(io.BytesIO(framebuffer_png(one_bit, 1)))
    assert image.size == (400, 300)
    assert image.getpixel((0, 0)) == 255
    assert image.getpixel((1, 0)) == 0
    four_bit = bytes([0xF1]) + bytes(59999)
    image = Image.open(io.BytesIO(framebuffer_png(four_bit, 4)))
    assert image.getpixel((0, 0)) == 255
    assert image.getpixel((1, 0)) == 17


@pytest.mark.parametrize('data,bits', [(b'', 1), (b'', 4), (b'123', 8)])
def test_reject_invalid_framebuffer(data, bits):
    with pytest.raises(ValueError):
        framebuffer_png(data, bits)


def test_device_snapshot_over_websocket_and_http(monkeypatch):
    import base64
    from concurrent.futures import ThreadPoolExecutor
    from fastapi.testclient import TestClient
    from app import create_app
    monkeypatch.setenv('POCKETCLAW_ADMIN_TOKEN', 'snapshot-test-token')
    app = create_app()
    with TestClient(app) as client:
        assert client.post('/api/pocketclaw/mp/screen_snapshot', json={}).status_code == 401
        with client.websocket_connect('/pocketclaw') as ws:
            ws.send_json({'kind': 'hello', 'device_id': 'snapshot-test', 'features': ['screen_capture']})
            assert ws.receive_json()['kind'] == 'hello_ack'
            with ThreadPoolExecutor(max_workers=1) as executor:
                pending = executor.submit(client.post, '/api/pocketclaw/mp/screen_snapshot',
                    json={'child_id': 'kid', 'device_id': 'snapshot-test'}, headers={'X-Admin-Token': 'snapshot-test-token'})
                request = ws.receive_json()
                assert request['kind'] == 'screen_snapshot_request'
                raw = bytes([0x80]) + bytes(14999)
                encoded = base64.b64encode(raw).decode('ascii')
                ws.send_json({'kind': 'screen_snapshot_chunk', 'request_id': request['request_id'],
                              'bits': 1, 'count': 1, 'index': 0, 'data': encoded})
                result = pending.result(timeout=5)
                assert result.status_code == 200
                image = Image.open(io.BytesIO(base64.b64decode(result.json()['image'])))
                assert image.getpixel((0, 0)) == 255 and image.getpixel((1, 0)) == 0


@pytest.mark.anyio
async def test_snapshot_rejects_mismatched_and_oversized_chunks():
    import asyncio
    import json
    from unittest.mock import AsyncMock
    from pocketclaw.bridge import PocketClawSession
    outgoing = asyncio.Queue()
    async def send(frame): await outgoing.put(json.loads(frame))
    session = PocketClawSession(None, send_json=send, send_bin=AsyncMock(), stt=AsyncMock(),
                                tts=AsyncMock(), registry=AsyncMock(), notes=AsyncMock())
    session._features = ('screen_capture',)
    capture = asyncio.create_task(session.request_screen_snapshot())
    req = await outgoing.get()
    session._accept_screen_chunk({'request_id': 'wrong', 'bits': 1, 'count': 1, 'index': 0, 'data': 'a'})
    assert not capture.done()
    session._accept_screen_chunk({'request_id': req['request_id'], 'bits': 1, 'count': 1, 'index': 0, 'data': 'a' * 20001})
    with pytest.raises(RuntimeError):
        await capture
    await session.close()


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.mark.anyio
async def test_four_bit_frame_is_reassembled_in_order():
    import asyncio
    import base64
    import json
    from unittest.mock import AsyncMock
    from pocketclaw.bridge import PocketClawSession
    outgoing = asyncio.Queue()
    async def send(frame): await outgoing.put(json.loads(frame))
    session = PocketClawSession(None, send_json=send, send_bin=AsyncMock(), stt=AsyncMock(),
                               tts=AsyncMock(), registry=AsyncMock(), notes=AsyncMock())
    session._features = ('screen_capture',)
    capture = asyncio.create_task(session.request_screen_snapshot())
    request = await outgoing.get()
    raw = bytes([0xf1]) + bytes(59999)
    encoded = base64.b64encode(raw).decode('ascii')
    for index in (3, 1, 2, 0):
        session._accept_screen_chunk({'request_id': request['request_id'], 'bits': 4,
            'count': 4, 'index': index, 'data': encoded[index*20000:(index+1)*20000]})
    image = Image.open(io.BytesIO(await capture))
    assert image.getpixel((0, 0)) == 255 and image.getpixel((1, 0)) == 17
    await session.close()
