from fastapi.testclient import TestClient


def test_parent_task_is_sent_to_connected_device():
    from app import create_app
    app = create_app()
    with TestClient(app) as client:
        task = client.post('/api/pocketclaw/mp/tasks', json={'child_id': 'kid', 'text': '端到端测试作业'})
        assert task.status_code == 200
        with client.websocket_connect('/pocketclaw') as ws:
            ws.send_json({'kind': 'hello', 'device_id': 'standalone-e2e'})
            assert ws.receive_json()['kind'] == 'hello_ack'
            result = client.post('/api/pocketclaw/mp/mode', json={'child_id': 'kid', 'mode': 'task'})
            assert result.json()['pushed'] == 1
            frames = [ws.receive_json() for _ in range(2)]
            display = next(frame for frame in frames if frame['kind'] == 'display')
            assert '端到端测试作业' in ''.join(display['page']['body'])


def test_ocr_preview_does_not_replace_active_dictation(monkeypatch):
    from unittest.mock import AsyncMock
    from app import create_app
    app = create_app()
    app.state.runtime.llm.complete = AsyncMock(return_value='温暖, wen nuan, 温暖的温')
    with TestClient(app) as client:
        client.post('/api/pocketclaw/mp/dictation', json={'child_id': 'ocr-preview', 'raw_text': '认真', 'push': False})
        result = client.post('/api/pocketclaw/mp/ocr_words', json={'child_id': 'ocr-preview', 'image': 'aW1hZ2U=', 'push': False})
        assert result.status_code == 200 and result.json()['words'][0][0] == '温暖'
        stored = client.get('/api/pocketclaw/mp/dictation', params={'child_id': 'ocr-preview'}).json()
        assert stored['words'][0][0] == '认真'


def test_day_timetable_from_parent_to_device(tmp_path):
    from app import create_app
    from pocketclaw.display import _display_width
    with TestClient(create_app(data_dir=tmp_path)) as client:
        timetable = client.get('/api/pocketclaw/mp/timetable', params={'child_id':'kid'}).json()['timetable']
        for day in range(1, 6):
            timetable[str(day)]['courses'] = [f'第{day}天语文', '数学', '英语', '道德与法治', '体育', '音乐']
        assert client.post('/api/pocketclaw/mp/timetable', json={'child_id':'kid', 'timetable':timetable}).json()['ok']
        with client.websocket_connect('/pocketclaw') as ws:
            ws.send_json({'kind':'hello', 'device_id':'week-e2e'})
            assert ws.receive_json()['kind'] == 'hello_ack'
            assert client.post('/api/pocketclaw/mp/mode', json={'child_id':'kid', 'mode':'timetable'}).json()['pushed'] == 1
            frames = [ws.receive_json() for _ in range(2)]
            page = next(x['page'] for x in frames if x['kind'] == 'display')
            assert len(page['body']) >= 6
            assert '道德与法治' in page['body'][3] and '音乐' in page['body'][5]
            assert all(_display_width(row) <= 32 for row in page['body'])
            old_day = int(page['body'][0].split('第')[2][0])
            ws.send_json({'kind': 'button', 'name': 'down'})
            while True:
                frame = ws.receive_json()
                if frame['kind'] == 'display':
                    break
            next_page = frame['page']
            assert next_page['mode'] == 'timetable' and next_page['partial'] is True
            assert f'第{old_day % 5 + 1}天语文' in next_page['body'][0]
