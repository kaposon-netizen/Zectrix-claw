from types import SimpleNamespace
from unittest.mock import AsyncMock
from starlette.testclient import TestClient


def test_remote_commands_only_target_selected_child():
    from app import app
    live = app.state.pocketclaw['live']
    def session(child):
        return SimpleNamespace(child_id=child, mode='task', battery=50, _focus=None,
            _switch_to_mode=AsyncMock(), _start_focus=AsyncMock(), _focus_control=AsyncMock(),
            _start_screensaver=AsyncMock(), _show_tasks=AsyncMock())
    own, other = session('control_child_a'), session('control_child_b')
    live.update({'control_a': own, 'control_b': other})
    try:
        with TestClient(app) as client:
            assert client.post('/api/pocketclaw/mp/tasks', json={'child_id': own.child_id, 'text': '隔离测试'}).status_code == 200
            assert client.post('/api/pocketclaw/mp/mode', json={'child_id': own.child_id, 'mode': 'task'}).json()['pushed'] == 1
            assert client.post('/api/pocketclaw/mp/focus', json={'child_id': own.child_id, 'action': 'start'}).json()['pushed'] == 1
            assert client.post('/api/pocketclaw/mp/screensaver/push', json={'child_id': own.child_id, 'mode': 'art'}).json()['pushed'] == 1
            assert client.post('/api/pocketclaw/mp/focus', json={'child_id': own.child_id, 'action': 'invalid'}).status_code == 400
        own._show_tasks.assert_awaited_once()
        other._show_tasks.assert_not_awaited()
        own._switch_to_mode.assert_awaited_once()
        own._start_focus.assert_awaited_once()
        own._start_screensaver.assert_awaited_once()
        other._switch_to_mode.assert_not_awaited()
        other._start_focus.assert_not_awaited()
        other._start_screensaver.assert_not_awaited()
    finally:
        live.pop('control_a', None)
        live.pop('control_b', None)
