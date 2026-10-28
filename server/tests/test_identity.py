import pytest
from pocketclaw.identity import IdentityStore


def test_family_sessions_and_device_credentials_are_isolated(tmp_path):
    path = tmp_path / 'identities.db'
    store = IdentityStore(str(path))
    family_a, child_a = store.create_family('family-a', 'a long password a', 'A')
    family_b, child_b = store.create_family('family-b', 'a long password b', 'B')
    assert store.login('family-a', 'wrong') is None
    assert store.login('missing', 'wrong') is None
    token = store.login('family-a', 'a long password a')
    assert store.session_family(token) == family_a
    assert store.owns_child(family_a, child_a) and not store.owns_child(family_a, child_b)
    device_token = store.issue_device(family_a, child_a, 'device-a')
    assert store.authenticate_device('device-a', device_token) == (family_a, child_a)
    assert store.authenticate_device('device-a', 'wrong') is None
    with pytest.raises(PermissionError): store.issue_device(family_b, child_b, 'device-a')
    with pytest.raises(ValueError): store.issue_device(family_a, child_b, 'device-b')
    replacement = store.issue_device(family_a, child_a, 'device-a')
    assert store.authenticate_device('device-a', device_token) is None
    assert store.authenticate_device('device-a', replacement) == (family_a, child_a)
    store.revoke_session(token)
    store.revoke_device(family_a, 'device-a')
    assert store.session_family(token) is None
    assert store.authenticate_device('device-a', replacement) is None
    content = path.read_bytes()
    assert token.encode() not in content and device_token.encode() not in content
    assert b'a long password a' not in content
    assert path.stat().st_mode & 0o777 == 0o600


def test_expired_session_is_rejected():
    store = IdentityStore(':memory:')
    store.create_family('family', 'long enough password', 'child')
    token = store.login('family', 'long enough password', ttl=-1)
    assert store.session_family(token) is None


def test_concurrent_device_claim_has_one_owner(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    path = str(tmp_path / 'identities.db')
    seed = IdentityStore(path)
    family_a, child_a = seed.create_family('a', 'password for family a', 'A')
    family_b, child_b = seed.create_family('b', 'password for family b', 'B')
    a, b = IdentityStore(path), IdentityStore(path)
    def claim(store, family, child):
        try:
            return store.issue_device(family, child, 'same-device')
        except PermissionError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(claim, a, family_a, child_a)
        second = pool.submit(claim, b, family_b, child_b)
        results = [first.result(), second.result()]
    assert sum(bool(result) for result in results) == 1


def test_device_handshake_requires_credential_and_ignores_self_report(monkeypatch):
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
    from app import create_app
    monkeypatch.setenv('POCKETCLAW_DEVICE_AUTH', 'required')
    app = create_app()
    store = app.state.identities
    family, child = store.create_family('handshake-family', 'safe family password', 'child')
    token = store.issue_device(family, child, 'authenticated-device')
    with TestClient(app) as client:
        with client.websocket_connect('/pocketclaw') as ws:
            with pytest.raises(WebSocketDisconnect): ws.receive_json()
        with client.websocket_connect('/pocketclaw', headers={'Authorization': 'Bearer wrong'}) as ws:
            ws.send_json({'kind': 'hello', 'device_id': 'authenticated-device'})
            with pytest.raises(WebSocketDisconnect): ws.receive_json()
        with client.websocket_connect('/pocketclaw', headers={'Authorization': 'Bearer ' + token}) as ws:
            ws.send_json({'kind': 'hello', 'device_id': 'another-device'})
            with pytest.raises(WebSocketDisconnect): ws.receive_json()
        with client.websocket_connect('/pocketclaw', headers={'Authorization': 'Bearer ' + token}) as ws:
            ws.send_json({'kind': 'hello', 'device_id': 'authenticated-device', 'child_id': 'someone-else'})
            assert ws.receive_json()['child_id'] == child
            ws.send_json({'kind': 'nfc', 'uid': 'unsafe', 'ndef': 'child:someone-else'})
            assert ws.receive_json()['code'] == 'nfc_unavailable'
            store.revoke_device(family, 'authenticated-device')
            ws.send_json({'kind': 'heartbeat'})
            with pytest.raises(WebSocketDisconnect): ws.receive_json()
