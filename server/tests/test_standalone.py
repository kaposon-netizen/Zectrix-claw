import json
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient


def test_device_management_does_not_depend_on_external_auth(monkeypatch):
    monkeypatch.setenv('POCKETCLAW_ADMIN_TOKEN', 'test-local-admin-token')
    from app import create_app
    app = create_app()
    with TestClient(app) as client:
        assert client.get('/api/pocketclaw/devices').status_code == 401
        headers = {'X-Admin-Token': 'test-local-admin-token'}
        assert client.get('/api/pocketclaw/devices', headers=headers).status_code == 200
        response = client.post('/api/pocketclaw/devices/example/bind', json={'child_id': 'kid'}, headers=headers)
        assert response.status_code == 200
        assert response.json()['device']['child_id'] == 'kid'


def test_corrupt_registry_stays_read_only_without_external_memory(tmp_path):
    from pocketclaw.device_registry import DeviceRegistry
    path = tmp_path / 'devices.json'
    path.write_text('{broken')
    registry = DeviceRegistry(str(path), strict=True)
    assert registry.degraded
    assert not path.exists()
    assert list(tmp_path.glob('devices.json.corrupt-*'))[0].read_text() == '{broken'
    assert DeviceRegistry(str(path), strict=True).degraded


@pytest.mark.anyio
async def test_volc_asr_request_and_tts_factory(monkeypatch):
    from pocketclaw.voice import VolcFlashSTT, NativeVolcTTS, make_tts
    real_client = httpx.AsyncClient
    def handler(request):
        data = json.loads(request.content)
        assert data['audio']['format'] == 'wav'
        assert request.headers['X-Api-Resource-Id'] == 'resource'
        return httpx.Response(200, json={'result': {'text': '你好'}})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    stt = VolcFlashSTT('app', 'token', 'resource', 'https://example.test/asr')
    assert await stt.transcribe(b'\0\0' * 20) == '你好'
    monkeypatch.delenv('POCKETCLAW_TTS_ENDPOINT', raising=False)
    monkeypatch.setenv('VOLC_TTS_APP_ID', 'app')
    monkeypatch.setenv('VOLC_TTS_ACCESS_TOKEN', 'token')
    assert isinstance(make_tts(), NativeVolcTTS)


@pytest.fixture
def anyio_backend():
    return 'asyncio'


def test_ocr_failure_never_generates_sample_data(monkeypatch):
    from app import app
    from pocketclaw.reading import ReadingStore
    llm = app.state.runtime.llm
    monkeypatch.setattr(llm, 'complete', AsyncMock(side_effect=RuntimeError('unavailable')))
    count = len(ReadingStore().articles)
    with TestClient(app) as client:
        assert client.post('/api/pocketclaw/mp/ocr_words', json={'image': 'aW1hZ2U=', 'push': False}).status_code == 503
        assert client.post('/api/pocketclaw/mp/ocr_reading', json={'image': 'aW1hZ2U=', 'push': False}).status_code == 503
    assert len(ReadingStore().articles) == count
