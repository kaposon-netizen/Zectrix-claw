import errno
from unittest.mock import Mock
from contextlib import nullcontext
import serve


def test_vpn_addresses_are_not_advertised_as_lan():
    assert serve._is_lan_ipv4('192.168.1.10')
    assert serve._is_lan_ipv4('10.0.0.5')
    assert not serve._is_lan_ipv4('198.18.0.1')
    assert not serve._is_lan_ipv4('127.0.0.1')


def test_busy_port_produces_clear_error(monkeypatch, capsys):
    monkeypatch.setenv('PORT', '8000')
    monkeypatch.setattr(serve, 'port_available', lambda host, port: False)
    monkeypatch.setattr(serve.uvicorn, 'run', Mock())
    assert serve.main() == 1
    assert '8000' in capsys.readouterr().err
    serve.uvicorn.run.assert_not_called()


def test_browser_opens_only_after_health_check(monkeypatch):
    response = Mock(status=200)
    urlopen = Mock(side_effect=[OSError('not ready'), nullcontext(response)])
    browser = Mock(return_value=True)
    monkeypatch.setattr(serve.urllib.request, 'urlopen', urlopen)
    monkeypatch.setattr(serve.webbrowser, 'open', browser)
    monkeypatch.setattr(serve.time, 'sleep', lambda _: None)
    assert serve.open_parent_when_ready(8123)
    assert urlopen.call_count == 2
    browser.assert_called_once_with('http://127.0.0.1:8123/pocketclaw/parent')


def test_browser_can_be_disabled(monkeypatch):
    monkeypatch.setenv('OPEN_BROWSER', '0')
    monkeypatch.setattr(serve, 'port_available', lambda *_: True)
    monkeypatch.setattr(serve, 'get_local_ip', lambda: '127.0.0.1')
    monkeypatch.setattr(serve.threading, 'Thread', Mock())
    monkeypatch.setattr(serve.uvicorn, 'run', Mock())
    serve.main()
    serve.threading.Thread.assert_not_called()
