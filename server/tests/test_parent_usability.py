import base64
import io
import re
import pytest
from html.parser import HTMLParser
from unittest.mock import AsyncMock
from PIL import Image
from fastapi.testclient import TestClient
from app import create_app
from pocketclaw.endpoint import _HTML_PARENT_PORTAL
from pocketclaw.notes import NotesStore


def test_parent_static_handlers_and_selectors_exist():
    class Collector(HTMLParser):
        def __init__(self): super().__init__(); self.ids = set()
        def handle_starttag(self, tag, attrs):
            a = dict(attrs)
            if 'id' in a: self.ids.add(a['id'])
    parser = Collector(); parser.feed(_HTML_PARENT_PORTAL)
    script = _HTML_PARENT_PORTAL.split('<script>', 1)[1]
    defined = set(re.findall(r'\b(?:async\s+)?function\s+(\w+)\s*\(', script))
    handlers = set()
    for handler in re.findall(r'on(?:click|change|input)="([^"]*)"', _HTML_PARENT_PORTAL):
        handlers.update(re.findall(r'\b([A-Za-z_$][\w$]*)\s*\(', handler))
    assert not (handlers - defined - {'if', 'parseInt', 'confirm', 'alert', 'replace', 'stringify'})
    ids = set(re.findall(r"getElementById\(['\"]([^'\"]+)", script))
    ids |= set(re.findall(r"querySelector(?:All)?\(['\"]#([\w-]+)", script))
    assert not (ids - parser.ids - {'tab-', 'tabBadge-', 'tabToggle-', 'week_'})


def test_news_and_week_schedule_end_to_end(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        news = client.get('/api/pocketclaw/mp/news').json()
        assert news['items'] and news['sources'] and news['region'] == '全国'
        assert client.post('/api/pocketclaw/mp/news/config', json={'region':'上海', 'whitelist':['科创']}).json()['ok']
        assert client.get('/api/pocketclaw/mp/news').json()['region'] == '上海'
        source = client.post('/api/pocketclaw/mp/news/source', json={'name':'测试信源'}).json()['source']
        assert any(x['id'] == source['id'] for x in client.get('/api/pocketclaw/mp/news').json()['sources'])
        assert client.post('/api/pocketclaw/mp/news/source/delete', json={'source_id':source['id']}).json()['ok']
        created = client.post('/api/pocketclaw/mp/news', json={'title':'校内活动','region':'上海'}).json()['item']
        assert any(x['id'] == created['id'] for x in client.get('/api/pocketclaw/mp/news').json()['items'])
        assert client.delete('/api/pocketclaw/mp/news/' + created['id']).json()['ok']
        assert client.post('/api/pocketclaw/mp/news/restore', json={'item_id':created['id']}).json()['ok']
        original = client.get('/api/pocketclaw/mp/timetable').json()['timetable']
        original['3']['courses'][2] = '测试课程'
        assert client.post('/api/pocketclaw/mp/timetable', json={'timetable':original}).json()['ok']
        assert client.get('/api/pocketclaw/mp/timetable').json()['timetable'] == original


def test_timetable_defaults_are_independent():
    store = NotesStore(':memory:')
    a = store.get_timetable('a'); b = store.get_timetable('b')
    original = b['1']['courses'][0]
    a['1']['courses'][0] = 'changed'
    assert b['1']['courses'][0] == original


def test_photo_text_validates_and_returns_editable_preview(tmp_path):
    app = create_app(data_dir=tmp_path)
    app.state.runtime.llm.ocr_mode = 'online'
    app.state.runtime.llm._call_api = AsyncMock(return_value='语文作业\n完成练习')
    image = io.BytesIO(); Image.new('RGB', (3, 3), 'white').save(image, 'PNG')
    with TestClient(app) as client:
        assert client.post('/api/pocketclaw/mp/ocr_text', json={'image':'not-base64'}).status_code == 400
        result = client.post('/api/pocketclaw/mp/ocr_text', json={'image':base64.b64encode(image.getvalue()).decode()})
        assert result.status_code == 200 and result.json()['text'] == '语文作业\n完成练习'
        assert result.headers['cache-control'] == 'no-store'
        app.state.runtime.llm._call_api = AsyncMock(side_effect=RuntimeError())
        assert client.post('/api/pocketclaw/mp/ocr_text', json={'image':base64.b64encode(image.getvalue()).decode()}).status_code == 503


def test_grade_presets_can_be_filtered_and_imported_without_overwriting(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        def catalog(category, grade):
            return client.get('/api/pocketclaw/mp/presets', params={'category':category,'grades':grade}).json()['items']
        assert not catalog('words','none')
        words = catalog('words','2'); assert len(words) >= 20 and all(w['grade'] == 2 for w in words)
        assert len(catalog('words','all')) >= 100
        assert client.get('/api/pocketclaw/mp/presets', params={'category':'words','grades':'7'}).status_code == 400
        imported = client.post('/api/pocketclaw/mp/presets/import',json={'category':'words','ids':[words[0]['id'],words[1]['id']]})
        assert imported.status_code == 200 and imported.json()['imported'] == 2
        assert len(client.get('/api/pocketclaw/mp/dictation').json()['words']) == 2
        idiom = catalog('idioms','1')[0]
        client.post('/api/pocketclaw/mp/idiom/custom', json={'idiom':idiom['idiom'],'meaning':'自定义内容'})
        result = client.post('/api/pocketclaw/mp/presets/import', json={'category':'idioms','ids':[idiom['id']]})
        assert result.json()['skipped'] == 1
        assert client.get('/api/pocketclaw/mp/idiom/custom').json()['idioms'][0]['meaning'] == '自定义内容'
        poem = catalog('poems','1')[0]
        assert client.post('/api/pocketclaw/mp/presets/import', json={'category':'poems','ids':[poem['id']]}).json()['imported'] == 1
        assert any(p['title']==poem['title'] for p in client.get('/api/pocketclaw/mp/poems/custom').json()['poems'])
        assert client.post('/api/pocketclaw/mp/presets/import', json={'category':'poems','ids':[]}).json()['imported'] == 0
        assert client.post('/api/pocketclaw/mp/presets/import', json={'category':'poems','ids':['wrong']}).status_code == 400


async def test_device_recites_imported_custom_poem(tmp_path):
    from unittest.mock import AsyncMock
    from pocketclaw.bridge import PocketClawSession
    notes = NotesStore(str(tmp_path / 'notes.json'))
    notes.add_custom_poem('kid','自编示例','作者','现代',['春风轻轻吹，','小鸟展翅飞。'])
    send = AsyncMock()
    class SilentTTS:
        sample_rate = 16000
        async def synthesize_stream(self, text):
            if False: yield b''
    session = PocketClawSession(None, send_json=send, send_bin=AsyncMock(), stt=AsyncMock(), tts=SilentTTS(), registry=AsyncMock(strict=False), notes=notes)
    session.child_id='kid'
    try:
        await session._start_recite('自编示例', 1)
        assert session._recite_poem.title == '自编示例'
    finally:
        await session.close()


def test_grade_import_respects_family_ownership(tmp_path):
    from pocketclaw.multitenant import MultiTenantApp
    server = MultiTenantApp(tmp_path, create_app, allow_insecure=True)
    _, a = server.store.create_family('preset-a', 'preset password a', 'A')
    _, b = server.store.create_family('preset-b', 'preset password b', 'B')
    with TestClient(server) as client:
        def login(name, password):
            return {'Authorization':'Bearer ' + client.post('/api/session/login',json={'name':name,'password':password}).json()['token']}
        ah, bh = login('preset-a','preset password a'), login('preset-b','preset password b')
        items = client.get('/api/pocketclaw/mp/presets', headers=ah, params={'category':'words','grades':'1'}).json()['items']
        payload = {'category':'words','ids':[items[0]['id']],'child_id':a}
        assert client.post('/api/pocketclaw/mp/presets/import',headers=bh,json=payload).status_code == 403
        assert client.post('/api/pocketclaw/mp/presets/import',headers=ah,json=payload).json()['imported'] == 1
        assert client.get('/api/pocketclaw/mp/dictation',headers=ah).json()['words'][0][0] == items[0]['word']
        assert client.get('/api/pocketclaw/mp/dictation',headers=bh).json()['words'] != client.get('/api/pocketclaw/mp/dictation',headers=ah).json()['words']


def test_english_grade_presets_import_and_deduplicate(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        for grade in range(1, 7):
            rows = client.get('/api/pocketclaw/mp/presets', params={'category': 'english', 'grades': str(grade)}).json()['items']
            assert len(rows) >= 7 and all(x['grade'] == grade and x['meaning'] for x in rows)
        item = rows[0]
        request = {'category': 'english', 'ids': [item['id']]}
        assert client.post('/api/pocketclaw/mp/presets/import', json=request).json()['imported'] == 1
        assert client.post('/api/pocketclaw/mp/presets/import', json=request).json()['skipped'] == 1


def test_structured_photo_preview_and_vocab_save(tmp_path):
    app = create_app(data_dir=tmp_path)
    app.state.runtime.llm.ocr_mode = 'online'
    app.state.runtime.llm._call_api = AsyncMock(return_value='{"title":"", "body":"", "extra":"", "entries":[{"word":"apple", "meaning":"苹果"}, {"word":"pear", "meaning":"梨"}]}')
    image = io.BytesIO(); Image.new('RGB', (3, 3), 'white').save(image, 'PNG')
    with TestClient(app) as client:
        before = client.get('/api/pocketclaw/mp/vocab').json()['words']
        preview = client.post('/api/pocketclaw/mp/ocr_text', json={'target':'英语生词','image':base64.b64encode(image.getvalue()).decode()})
        assert preview.status_code == 200
        entries = preview.json()['fields']['entries']
        assert len(entries) == 2 and entries[1]['meaning'] == '梨'
        assert client.get('/api/pocketclaw/mp/vocab').json()['words'] == before
        saved = client.post('/api/pocketclaw/mp/vocab', json=entries[1]).json()
        assert saved['word']['word'] == 'pear'
        app.state.runtime.llm._call_api = AsyncMock(return_value='{"days":[{"day":1,"courses":["语文","数学"]}]}')
        schedule = client.post('/api/pocketclaw/mp/ocr_text', json={'target':'课表','image':base64.b64encode(image.getvalue()).decode()})
        assert schedule.json()['fields']['days'] == [{'day': 1, 'courses': ['语文', '数学']}]
        app.state.runtime.llm._call_api = AsyncMock(return_value='not json')
        assert client.post('/api/pocketclaw/mp/ocr_text', json={'target':'英语生词','image':base64.b64encode(image.getvalue()).decode()}).status_code == 503


@pytest.mark.parametrize('target', ['口算题','事实核查','飞花令','心愿','亲子留言','倒计时'])
def test_extended_structured_photo_preview_is_non_destructive(tmp_path, target):
    app=create_app(data_dir=tmp_path)
    app.state.runtime.llm.ocr_mode='online'
    app.state.runtime.llm._call_api=AsyncMock(return_value='{"title":"测试","body":"内容","extra":"真"}')
    image=io.BytesIO(); Image.new('RGB',(3,3),'white').save(image,'PNG')
    with TestClient(app) as client:
        result=client.post('/api/pocketclaw/mp/ocr_text',json={'target':target,'image':base64.b64encode(image.getvalue()).decode()})
        assert result.status_code==200 and result.json()['fields']['body']=='内容'
        if target=='事实核查':
            assert result.json()['fields']['extra']==''
