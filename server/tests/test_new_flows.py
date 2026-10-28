import asyncio
import json
import os
import pty
import select
import threading
import time
import httpx
import pytest
from fastapi.testclient import TestClient
from app import create_app
from pocketclaw.multitenant import MultiTenantApp
from runtime import StandaloneLLM, StandaloneRuntime
from provision_device import configure

@pytest.mark.asyncio
async def test_llm_streams_before_provider_finishes(monkeypatch):
    first = asyncio.Event()
    events = []
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield ('data: ' + json.dumps({'choices': [{'delta': {'content': '第一句。'}}]}, ensure_ascii=False) + '\n\n').encode()
            await asyncio.wait_for(first.wait(), 1)
            yield ('data: ' + json.dumps({'choices': [{'delta': {'content': '第二句。'}}]}, ensure_ascii=False) + '\n\ndata: [DONE]\n\n').encode()
    def handler(request):
        assert json.loads(request.content)['stream'] is True
        return httpx.Response(200, stream=Stream())
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr('runtime.httpx.AsyncClient', lambda **kwargs: client)
    async def on_delta(delta):
        events.append(delta)
        first.set()
    llm = StandaloneLLM(llm_mode='online', api_key='fake', base_url='https://model.test')
    reply = await StandaloneRuntime(llm=llm).turn('hello', stream_cb=on_delta)
    assert events == ['第一句。', '第二句。'] and reply['text'] == '第一句。第二句。'

@pytest.mark.asyncio
async def test_partial_stream_failure_does_not_repeat(monkeypatch):
    class Broken(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"Already spoken."}}]}\n\n'
            raise httpx.ReadError('disconnected')
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Broken())))
    monkeypatch.setattr('runtime.httpx.AsyncClient', lambda **kwargs: client)
    llm = StandaloneLLM(llm_mode='online', api_key='fake', base_url='https://model.test')
    events = []
    assert await llm.complete_stream('system', 'prompt', events.append) == 'Already spoken.'
    assert events == ['Already spoken.']

def test_family_web_login_and_isolation(tmp_path):
    server = MultiTenantApp(tmp_path, create_app)
    server.store.create_family('family-web-a', 'password for web a', 'child A')
    server.store.create_family('family-web-b', 'password for web b', 'child B')
    with TestClient(server, base_url='https://testserver') as client:
        portal = client.get('/pocketclaw/parent')
        assert portal.status_code == 200 and '家庭登录' in portal.text
        assert portal.headers['cache-control'] == 'no-store'
        assert "frame-ancestors 'none'" in portal.headers['content-security-policy']
        assert 'localStorage' not in portal.text and 'innerHTML' not in portal.text
        def login(name, password):
            return {'Authorization': 'Bearer ' + client.post('/api/session/login', json={'name': name, 'password': password}).json()['token']}
        a, b = login('family-web-a', 'password for web a'), login('family-web-b', 'password for web b')
        assert client.post('/api/pocketclaw/mp/tasks', headers=a, json={'text': 'private web task'}).status_code == 200
        assert 'private web task' not in client.get('/api/pocketclaw/mp/tasks', headers=b).text
        assert client.post('/api/session/logout', headers=a).status_code == 200
        assert client.get('/api/pocketclaw/mp/tasks', headers=a).status_code == 401

def test_usb_provision_console_and_identity_match():
    master, slave = pty.openpty()
    path = os.ttyname(slave)
    token = 'A' * 43
    seen = []
    def device():
        time.sleep(.1)
        os.write(master, b'PCPROV1 READY 00:11:22:33:44:55\n')
        ready, _, _ = select.select([master], [], [], 3)
        if ready:
            seen.append(os.read(master, 256))
            os.write(master, b'PCPROV1 OK\n')
    thread = threading.Thread(target=device); thread.start()
    try:
        configure(path, '00:11:22:33:44:55', token, timeout=3)
        thread.join(timeout=4)
        assert seen == [(token + '\n').encode()]
    finally:
        os.close(master); os.close(slave)

def test_usb_provision_does_not_send_to_wrong_device():
    master, slave = pty.openpty()
    def device():
        time.sleep(.1)
        os.write(master, b'PCPROV1 READY aa:bb:cc:dd:ee:ff\n')
    thread = threading.Thread(target=device); thread.start()
    try:
        with pytest.raises(ValueError, match='不一致'):
            configure(os.ttyname(slave), '00:11:22:33:44:55', 'B' * 43, timeout=3)
        assert not select.select([master], [], [], .1)[0]
    finally:
        thread.join(); os.close(master); os.close(slave)

@pytest.mark.asyncio
async def test_model_sentence_is_spoken_before_generation_ends(monkeypatch, tmp_path):
    import time
    from unittest.mock import AsyncMock
    from pocketclaw.bridge import PocketClawSession
    from pocketclaw.notes import NotesStore
    first_audio = asyncio.Event()
    complete = asyncio.Event()
    spoken = []
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield ('data: ' + json.dumps({'choices': [{'delta': {'content': '先说第一句。'}}]}, ensure_ascii=False) + '\n\n').encode()
            await asyncio.wait_for(first_audio.wait(), 2)
            complete.set()
            yield ('data: ' + json.dumps({'choices': [{'delta': {'content': '再说第二句。'}}]}, ensure_ascii=False) + '\n\ndata: [DONE]\n\n').encode()
    class TTS:
        sample_rate = 16000
        async def synthesize_stream(self, text):
            spoken.append(text)
            yield bytes(1920)
    real_client = httpx.AsyncClient
    monkeypatch.setattr('runtime.httpx.AsyncClient', lambda **kw: real_client(transport=httpx.MockTransport(lambda req: httpx.Response(200, stream=Stream()))))
    async def send_audio(data):
        assert not complete.is_set() or len(spoken) > 1
        first_audio.set()
    runtime = StandaloneRuntime(StandaloneLLM(llm_mode='online', api_key='fake', base_url='https://model.test'))
    session = PocketClawSession(runtime, send_json=AsyncMock(), send_bin=send_audio, stt=AsyncMock(),
        tts=TTS(), registry=AsyncMock(), notes=NotesStore(path=str(tmp_path / 'notes.json')))
    session.child_id = 'kid'; session.mode = 'chat'
    try:
        await asyncio.wait_for(session._chat('你好', heard='你好', now=time.time()), 3)
        if session._speech_task:
            await asyncio.wait_for(session._speech_task, 2)
        assert first_audio.is_set() and complete.is_set()
        assert spoken == ['先说第一句。', '再说第二句。']
    finally:
        await session.close()

def test_parent_portal_javascript_compiles(tmp_path):
    import re
    import shutil
    import subprocess
    from pocketclaw.endpoint import _HTML_PARENT_PORTAL
    node = shutil.which('node')
    if not node:
        pytest.skip('JavaScript syntax check requires Node.js')
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', _HTML_PARENT_PORTAL, re.S)
    assert scripts
    for index, script in enumerate(scripts):
        path = tmp_path / f'portal-{index}.js'
        path.write_text(script, encoding='utf-8')
        subprocess.run([node, '--check', str(path)], check=True, capture_output=True)
