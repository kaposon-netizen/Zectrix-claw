import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from app import create_app
from pocketclaw.bridge import PocketClawSession
from pocketclaw.multitenant import MultiTenantApp
from pocketclaw.notes import NotesStore
from runtime import StandaloneRuntime

PROFILE = {'name': '小明', 'grade': 3, 'gender': '男', 'interests': '恐龙、画画'}


def test_profile_validation_and_persistence(tmp_path):
    app = create_app(data_dir=tmp_path)
    with TestClient(app) as client:
        assert client.get('/api/pocketclaw/mp/child_profile').json()['profile']['grade'] == 0
        response = client.put('/api/pocketclaw/mp/child_profile', json=PROFILE)
        assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
        assert client.put('/api/pocketclaw/mp/child_profile', json={**PROFILE, 'grade': 7}).status_code == 422
        assert client.put('/api/pocketclaw/mp/child_profile', json={**PROFILE, 'gender': 'invalid'}).status_code == 422
        assert client.put('/api/pocketclaw/mp/child_profile', json={**PROFILE, 'interests': 'x' * 201}).status_code == 422
    assert NotesStore(str(tmp_path / 'pocketclaw_notes.json')).get_child_profile('kid') == PROFILE
    with TestClient(create_app(data_dir=tmp_path)) as client:
        assert client.get('/api/pocketclaw/mp/child_profile').json()['profile'] == PROFILE
        assert client.put('/api/pocketclaw/mp/child_profile', json={}).json()['profile'] == {'name': '', 'grade': 0, 'gender': '', 'interests': ''}


def test_profiles_cannot_be_read_or_written_across_families(tmp_path):
    server = MultiTenantApp(tmp_path, create_app, allow_insecure=True)
    _, a_id = server.store.create_family('profile-a', 'profile a password', 'child A')
    _, b_id = server.store.create_family('profile-b', 'profile b password', 'child B')
    with TestClient(server) as client:
        def login(name, password):
            token = client.post('/api/session/login', json={'name': name, 'password': password}).json()['token']
            return {'Authorization': 'Bearer ' + token}
        a, b = login('profile-a', 'profile a password'), login('profile-b', 'profile b password')
        assert client.put('/api/pocketclaw/mp/child_profile', headers=a, json=PROFILE).status_code == 200
        assert client.get('/api/pocketclaw/mp/child_profile', headers=b).json()['profile']['name'] == ''
        assert client.get('/api/pocketclaw/mp/child_profile', headers=b, params={'child_id': a_id}).status_code == 403
        assert client.put('/api/pocketclaw/mp/child_profile', headers=b, params={'child_id': a_id}, json=PROFILE).status_code == 403
        assert client.get('/api/pocketclaw/mp/child_profile', headers=a, params={'child_id': b_id}).status_code == 403
        assert client.get('/api/pocketclaw/mp/child_profile').status_code == 401


@pytest.mark.asyncio
async def test_saved_profile_reaches_model_on_each_conversation(tmp_path):
    calls = []
    class LLM:
        async def complete(self, sys_prompt, user_prompt, **kwargs):
            calls.append((sys_prompt, user_prompt))
            return '你好。'
    class SilentTTS:
        sample_rate = 16000
        async def synthesize_stream(self, text):
            if False:
                yield b''
    notes = NotesStore(str(tmp_path / 'notes.json'))
    notes.set_child_profile('kid-a', PROFILE)
    notes.set_child_profile('kid-b', {**PROFILE, 'name': '小红', 'interests': '音乐'})
    session = PocketClawSession(StandaloneRuntime(LLM()), send_json=AsyncMock(), send_bin=AsyncMock(),
                               stt=AsyncMock(), tts=SilentTTS(), registry=AsyncMock(strict=False), notes=notes)
    session.mode = 'chat'; session.child_id = 'kid-a'
    try:
        await session._chat('讲一个故事', heard='讲一个故事', now=1)
        sys_prompt, prompt = calls[-1]
        assert all(value in prompt for value in ('小明', '恐龙、画画', '3', '男'))
        assert '不要根据性别推断' in sys_prompt
        notes.set_child_profile('kid-a', {**PROFILE, 'interests': '航天'})
        await session._chat('再讲一个', heard='再讲一个', now=2)
        assert '航天' in calls[-1][1] and '恐龙、画画' not in calls[-1][1]
        session.child_id = 'kid-b'
        session._history.clear()
        await session._chat('你好', heard='你好', now=3)
        assert '小红' in calls[-1][1] and '小明' not in calls[-1][1]
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_switching_child_clears_conversation_history():
    registry = AsyncMock(strict=False)
    registry.on_nfc = lambda *_: {'kind': 'child', 'child_id': 'kid-b'}
    session = PocketClawSession(None, send_json=AsyncMock(), send_bin=AsyncMock(), stt=AsyncMock(),
                                tts=AsyncMock(), registry=registry, notes=NotesStore(':memory:'))
    session.child_id = 'kid-a'
    session._history = ['孩子：私人内容']
    session._skill_lock = 'english'
    try:
        await session._on_nfc({'uid': 'card'}, 1)
        assert session.child_id == 'kid-b'
        assert session._history == [] and session._skill_lock is None
    finally:
        await session.close()

def test_parent_profile_reaches_authenticated_device_dialogue(tmp_path):
    import threading
    server = MultiTenantApp(tmp_path, create_app, allow_insecure=True)
    family, child = server.store.create_family('device-profile', 'device profile password', 'child')
    event = threading.Event()
    captured = []
    async def complete_stream(system, prompt, callback, max_tokens=150):
        captured.append((system, prompt))
        event.set()
        await callback('你好。')
        return '你好。'
    tenant = server._family_app(family)
    tenant.state.runtime.llm.complete_stream = complete_stream
    with TestClient(server) as client:
        login = client.post('/api/session/login', json={'name': 'device-profile', 'password': 'device profile password'}).json()
        headers = {'Authorization': 'Bearer ' + login['token']}
        assert client.put('/api/pocketclaw/mp/child_profile', headers=headers, params={'child_id': child}, json=PROFILE).status_code == 200
        issued = client.post('/api/session/devices', headers=headers, json={'child_id': child, 'device_id': 'profile-device'}).json()
        with client.websocket_connect('/pocketclaw', headers={'Authorization': 'Bearer ' + issued['device_token']}) as ws:
            ws.send_json({'kind': 'hello', 'device_id': 'profile-device'})
            assert ws.receive_json()['child_id'] == child
            assert client.post('/api/pocketclaw/mp/mode', headers=headers, json={'mode': 'chat'}).json()['pushed'] == 1
            ws.send_json({'kind': 'text', 'text': '请给我讲个故事'})
            assert event.wait(5), 'model did not receive the dialogue'
            assert '小明' in captured[0][1] and '恐龙、画画' in captured[0][1]

@pytest.mark.asyncio
async def test_chat_tts_failure_reports_error_without_leaking_task(tmp_path):
    class LLM:
        async def complete(self, *_args, **_kwargs): return '你好。'
    class BrokenTTS:
        sample_rate = 16000
        async def synthesize_stream(self, text):
            raise RuntimeError('provider failure')
            yield b''
    sent = AsyncMock()
    session = PocketClawSession(StandaloneRuntime(LLM()), send_json=sent, send_bin=AsyncMock(), stt=AsyncMock(),
        tts=BrokenTTS(), registry=AsyncMock(strict=False), notes=NotesStore(str(tmp_path/'notes.json')))
    session.mode = 'chat'
    try:
        await session._chat('你好', heard='你好', now=1)
        if session._speech_task: await session._speech_task
        assert any('tts_unavailable' in str(call.args[0]) for call in sent.await_args_list)
    finally:
        await session.close()
