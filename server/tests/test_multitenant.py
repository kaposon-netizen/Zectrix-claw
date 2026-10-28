from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
import pytest
from app import create_app
from pocketclaw.multitenant import MultiTenantApp


def test_family_data_and_device_are_isolated(tmp_path):
    server = MultiTenantApp(tmp_path, create_app, allow_insecure=True)
    family_a, child_a = server.store.create_family('A-family', 'family a password', 'A')
    family_b, child_b = server.store.create_family('B-family', 'family b password', 'B')
    with TestClient(server) as client:
        def login(name, password):
            result = client.post('/api/session/login', json={'name': name, 'password': password})
            assert result.status_code == 200
            return {'Authorization': 'Bearer ' + result.json()['token']}
        assert client.get('/api/pocketclaw/mp/tasks').status_code == 401
        a, b = login('A-family', 'family a password'), login('B-family', 'family b password')
        assert client.post('/api/pocketclaw/mp/tasks', headers=a, json={'text': 'A private task'}).status_code == 200
        assert client.post('/api/pocketclaw/mp/tasks', headers=b, json={'text': 'B private task'}).status_code == 200
        tasks_a = client.get('/api/pocketclaw/mp/tasks', headers=a).json()['tasks']
        tasks_b = client.get('/api/pocketclaw/mp/tasks', headers=b).json()['tasks']
        assert any(x['text'] == 'A private task' for x in tasks_a)
        assert not any(x['text'] == 'A private task' for x in tasks_b)
        assert not any(x['text'] == 'B private task' for x in tasks_a)
        # Editing a built-in reading article must not mutate another family's preset.
        original = client.get('/api/pocketclaw/mp/reading', headers=b).json()['articles'][0]
        changed = client.put('/api/pocketclaw/mp/reading/custom/' + original['id'], headers=a, json={'title': 'A only title'})
        assert changed.status_code == 200 and changed.json()['ok']
        other = client.get('/api/pocketclaw/mp/reading', headers=b).json()['articles'][0]
        assert other['title'] == original['title']
        assert client.get('/api/pocketclaw/mp/settings', headers=a).status_code == 403
        assert client.post('/api/pocketclaw/mp/settings/test_llm', headers=a, json={'base_url': 'https://example.test'}).status_code == 403
        assert client.get('/api/pocketclaw/mp/tasks', headers=b, params={'child_id': child_a}).status_code == 403
        assert client.post('/api/pocketclaw/mp/tasks', headers=b, json={'text': 'not allowed', 'child_id': child_a}).status_code == 403
        assert client.post('/api/pocketclaw/mp/timetable', headers=b, json={'child_id': child_a, 'timetable': {}}).status_code == 403
        assert client.post('/api/pocketclaw/mp/reading/custom', headers=b, json={'child_id':child_a,'title':'unauthorized','sentences':[]}).status_code == 403
        assert client.post('/api/session/devices', headers=b, json={'child_id': child_a, 'device_id': 'device-a'}).status_code == 403
        issued = client.post('/api/session/devices', headers=a, json={'child_id': child_a, 'device_id': 'device-a'})
        assert issued.status_code == 200
        token = issued.json()['device_token']
        with client.websocket_connect('/pocketclaw', headers={'Authorization': 'Bearer ' + token}) as ws:
            ws.send_json({'kind': 'hello', 'device_id': 'device-a', 'child_id': child_b})
            assert ws.receive_json()['child_id'] == child_a
            assert client.get('/api/pocketclaw/mp/overview', headers=a).json()['online'] is True
            assert client.get('/api/pocketclaw/mp/overview', headers=b).json()['online'] is False
            assert client.post('/api/session/devices/revoke', headers=b, json={'child_id': child_b, 'device_id': 'device-a'}).json()['ok'] is False
            assert client.post('/api/session/devices/revoke', headers=a, json={'child_id': child_a, 'device_id': 'device-a'}).json()['ok'] is True
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect('/pocketclaw', headers={'Authorization': 'Bearer wrong'}): pass
        assert client.post('/api/session/logout', headers=a).status_code == 200
        assert client.get('/api/pocketclaw/mp/tasks', headers=a).status_code == 401


def test_family_mode_requires_tls(tmp_path):
    server = MultiTenantApp(tmp_path, create_app)
    with TestClient(server) as client:
        assert client.post('/api/session/login', json={'name': 'a', 'password': 'anything'}).status_code == 403
        server.store.create_family('secure-family', 'secure password here', 'child')
        result = client.post('https://testserver/api/session/login', json={'name': 'secure-family', 'password': 'secure password here'})
        assert result.status_code == 200
        assert result.headers['cache-control'] == 'no-store'


def test_model_keys_are_not_returned_by_settings(monkeypatch):
    from fastapi.testclient import TestClient
    from app import create_app
    monkeypatch.setenv('OPENAI_API_KEY', 'test-sensitive-model-key')
    app = create_app()
    with TestClient(app) as client:
        get = client.get('/api/pocketclaw/mp/settings')
        assert get.status_code == 200
        assert 'test-sensitive-model-key' not in get.text
        saved = client.post('/api/pocketclaw/mp/settings', json={'llm': {'mode': 'online'}})
        assert saved.status_code == 200
        assert 'test-sensitive-model-key' not in saved.text


def test_family_screen_capture_only_targets_own_device(tmp_path):
    import base64
    from concurrent.futures import ThreadPoolExecutor
    server = MultiTenantApp(tmp_path, create_app, allow_insecure=True)
    family_a, child_a = server.store.create_family('screen-A', 'password for screen A', 'A')
    family_b, child_b = server.store.create_family('screen-B', 'password for screen B', 'B')
    with TestClient(server) as client:
        def login(name, password):
            token = client.post('/api/session/login', json={'name': name, 'password': password}).json()['token']
            return {'Authorization': 'Bearer ' + token}
        a = login('screen-A', 'password for screen A')
        b = login('screen-B', 'password for screen B')
        token = client.post('/api/session/devices', headers=a, json={'child_id': child_a, 'device_id': 'screen-device'}).json()['device_token']
        with client.websocket_connect('/pocketclaw', headers={'Authorization': 'Bearer ' + token}) as ws:
            ws.send_json({'kind': 'hello', 'device_id': 'screen-device', 'features': ['screen_capture']})
            assert ws.receive_json()['child_id'] == child_a
            assert client.post('/api/pocketclaw/mp/screen_snapshot', headers=b, json={'device_id': 'screen-device'}).status_code == 404
            with ThreadPoolExecutor(max_workers=1) as pool:
                result = pool.submit(client.post, '/api/pocketclaw/mp/screen_snapshot', headers=a, json={'device_id': 'screen-device'})
                request = ws.receive_json()
                assert request['kind'] == 'screen_snapshot_request'
                ws.send_json({'kind': 'screen_snapshot_chunk', 'request_id': request['request_id'],
                              'bits': 1, 'count': 1, 'index': 0, 'data': base64.b64encode(bytes(15000)).decode()})
                assert result.result(timeout=5).status_code == 200


def test_tls_family_login_and_authenticated_device(tmp_path):
    server = MultiTenantApp(tmp_path, create_app)
    family, child = server.store.create_family('tls-family', 'secure family password', 'child')
    with TestClient(server, base_url='https://testserver') as client:
        result = client.post('/api/session/login', json={'name': 'tls-family', 'password': 'secure family password'})
        headers = {'Authorization': 'Bearer ' + result.json()['token']}
        issued = client.post('/api/session/devices', headers=headers, json={'child_id': child, 'device_id': 'tls-device'})
        with client.websocket_connect('wss://testserver/pocketclaw', headers={'Authorization': 'Bearer ' + issued.json()['device_token']}) as ws:
            ws.send_json({'kind': 'hello', 'device_id': 'tls-device'})
            assert ws.receive_json()['child_id'] == child


def test_family_photo_preview_week_and_device_e2e(tmp_path):
    import base64
    import io
    from unittest.mock import AsyncMock
    from PIL import Image
    server = MultiTenantApp(tmp_path, create_app, allow_insecure=True)
    family_a, child_a = server.store.create_family('family-photo-a', 'photo test password a', 'A')
    _, child_b = server.store.create_family('family-photo-b', 'photo test password b', 'B')
    with TestClient(server) as client:
        def login(name, password):
            data = client.post('/api/session/login', json={'name':name, 'password':password}).json()
            return {'Authorization': 'Bearer ' + data['token']}
        a = login('family-photo-a', 'photo test password a')
        b = login('family-photo-b', 'photo test password b')
        portal = client.get('/pocketclaw/parent')
        assert portal.status_code == 200 and '学习内容录入' in portal.text and '识别课表' in portal.text
        app = server._family_app(family_a)
        app.state.runtime.llm.ocr_mode = 'online'
        app.state.runtime.llm._call_api = AsyncMock(return_value='{"title":"春游", "body":"带水壶", "extra":"上海"}')
        picture = io.BytesIO(); Image.new('RGB', (3, 3), 'white').save(picture, 'PNG')
        image = base64.b64encode(picture.getvalue()).decode()
        preview = client.post('/api/pocketclaw/mp/ocr_text', headers=a, json={'image':image, 'target':'小学新鲜事'}).json()['fields']
        assert not any(x['title']=='春游' for x in client.get('/api/pocketclaw/mp/news',headers=a).json()['items'])
        saved = client.post('/api/pocketclaw/mp/news',headers=a,json={'child_id':child_a,'title':preview['title'],'summary':preview['body'],'region':preview['extra']})
        assert saved.json()['ok']
        assert not any(x['title']=='春游' for x in client.get('/api/pocketclaw/mp/news',headers=b).json()['items'])
        app.state.runtime.llm._call_api = AsyncMock(return_value='{"days":[{"day":1,"courses":["语文","数学"]}]}')
        days = client.post('/api/pocketclaw/mp/ocr_text', headers=a, json={'image':image, 'target':'课表'}).json()['fields']['days']
        week = client.get('/api/pocketclaw/mp/timetable',headers=a,params={'child_id':child_a}).json()['timetable']
        week['1']['courses'][:2] = days[0]['courses']
        assert client.post('/api/pocketclaw/mp/timetable',headers=b,json={'child_id':child_a,'timetable':week}).status_code == 403
        assert client.post('/api/pocketclaw/mp/timetable',headers=a,json={'child_id':child_a,'timetable':week}).json()['ok']
        issued = client.post('/api/session/devices', headers=a, json={'child_id':child_a,'device_id':'photo-device'}).json()['device_token']
        with client.websocket_connect('/pocketclaw', headers={'Authorization':'Bearer '+issued}) as ws:
            ws.send_json({'kind':'hello','device_id':'photo-device'})
            assert ws.receive_json()['child_id'] == child_a
            assert client.post('/api/pocketclaw/mp/mode',headers=a,json={'child_id':child_a,'mode':'timetable'}).json()['pushed'] == 1
            frames=[ws.receive_json() for _ in range(2)]
            page=next(frame['page'] for frame in frames if frame['kind']=='display')
            assert '语文' in page['body'][0] and '数学' in page['body'][0]
        assert client.get('/api/pocketclaw/mp/timetable',headers=b,params={'child_id':child_a}).status_code == 403


def test_family_extended_workflows_and_isolation(tmp_path):
    server = MultiTenantApp(tmp_path, create_app, allow_insecure=True)
    _, a_child = server.store.create_family('more-a', 'more-a-test-password', 'A')
    _, b_child = server.store.create_family('more-b', 'more-b-test-password', 'B')
    with TestClient(server) as client:
        def login(name, password):
            return {'Authorization':'Bearer '+client.post('/api/session/login',json={'name':name,'password':password}).json()['token']}
        a=login('more-a','more-a-test-password'); b=login('more-b','more-b-test-password')
        def post(path, data):
            return client.post('/api/pocketclaw/mp/'+path,headers=a,json={'child_id':a_child,**data})
        for path,payload in [('arith/custom',{'expr':'3+4','ans':'7'}),('facts/custom',{'kw1':'鱼','kw2':'水','is_true':True}),('feihua/custom',{'keyword':'春','verse':'春眠不觉晓'}),('stars/wishes',{'title':'家庭测试心愿','stars_needed':5}),('screensaver/countdown',{'title':'家庭测试倒计时','target_date':'2027-01-01'}),('message',{'text':'家庭测试留言'})]:
            result=post(path,payload)
            assert result.status_code==200 and result.json()['ok'], path
        assert any(x['expr']=='3+4' for x in client.get('/api/pocketclaw/mp/arith/custom',headers=a).json()['items'])
        assert not any(x['expr']=='3+4' for x in client.get('/api/pocketclaw/mp/arith/custom',headers=b).json()['items'])
        assert client.post('/api/pocketclaw/mp/stars/add',headers=b,json={'child_id':a_child,'count':2}).status_code==403
        menu=client.get('/api/pocketclaw/mp/menu',headers=a).json()['items']
        order=[item['mode'] for item in reversed(menu)]
        assert post('menu/order',{'order':order}).json()['ok']
        assert [item['mode'] for item in client.get('/api/pocketclaw/mp/menu',headers=a).json()['items']]==order
        assert client.post('/api/pocketclaw/mp/companion',headers=a,params={'child_id':a_child},json={'name':'测试学伴'}).json()['ok']
        assert client.get('/api/pocketclaw/mp/companion',headers=b).json()['config']['name']!='测试学伴'
        assert client.get('/api/pocketclaw/mp/companion',headers=b,params={'child_id':a_child}).status_code==403
