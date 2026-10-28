import pytest
import time
from pocketclaw.notes import NotesStore
from pocketclaw.practice import DictationSession
from pocketclaw import display

def test_dictation_session_flow():
    words = [("温暖", "wēn nuǎn", "温和暖和的温暖"), ("认真", "rèn zhēn", "认认真真的认真")]
    session = DictationSession(words, title="第一单元生字")
    assert session.title == "第一单元生字"
    assert session.total == 2
    assert session.cur_word[0] == "温暖"
    
    # Standard prompt
    prompt = session.prompt_speech()
    assert "温暖" in prompt
    assert "温和暖和的温暖" in prompt
    
    # Repeat prompt
    repeat = session.prompt_speech(repeat=True)
    assert "请注意听：温暖" in repeat

    # Step to next
    has_next = session.step_next()
    assert has_next is True
    assert session.cur_word[0] == "认真"

    # Step past end
    has_next = session.step_next()
    assert has_next is False
    assert session.active is False


def test_dictation_notes_management_and_parent_review():
    notes = NotesStore(":memory:")
    child_id = "test_kid"

    # 1. Parent saves dictation set
    words = [("温暖", "wēn nuǎn", "温和暖和的温暖"), ("遵守", "zūn shǒu", "遵守纪律")]
    dset = notes.save_dictation_set(child_id, title="第一单元生字", words=words)
    assert dset["title"] == "第一单元生字"
    assert len(notes.list_dictation_sets(child_id)) == 1
    assert notes.get_dictation_title(child_id) == "第一单元生字"
    assert len(notes.get_dictation_words(child_id)) == 2

    # 2. Child completes dictation on Note 4 -> creates record
    rec = notes.add_dictation_record(child_id, title="第一单元生字", words=words, now=time.time())
    assert rec["status"] == "pending"
    assert rec["count"] == 2
    
    records = notes.list_dictation_records(child_id)
    assert len(records) == 1
    assert records[0]["id"] == rec["id"]

    # 3. Parent reviews dictation: marks "遵守" as mistake, rewards 2 stars
    reviewed = notes.review_dictation_record(child_id, rec["id"], mistakes=["遵守"], stars=2, comment="字迹工整，继续加油！")
    assert reviewed is not None
    assert reviewed["status"] == "reviewed"
    assert reviewed["mistakes"] == ["遵守"]
    assert reviewed["stars_rewarded"] == 2

    # 4. Verify mistake was automatically recorded into Mistake book
    mistakes = notes.list_mistakes(child_id, category="dictation")
    assert len(mistakes) == 1
    assert mistakes[0].expected == "遵守"
    assert "生字听写错字" in mistakes[0].question

    # 5. Verify stars balance increased
    bank = notes.get_star_bank(child_id)
    assert bank["total_earned"] >= 2
    assert bank["balance"] >= 2


def test_dictation_irregular_handwriting_in_mistake_book():
    """测试书写不规范生字与错字一同归集沉淀至错题消灭本。"""
    notes = NotesStore(":memory:")
    child_id = "test_kid"
    words = [("温暖", "wēn nuǎn", "温暖"), ("认真", "rèn zhēn", "认真"), ("奇迹", "qí jì", "奇迹")]
    rec = notes.add_dictation_record(child_id, title="第二单元生字", words=words, now=time.time())

    # 家长核对：认真为错字，奇迹为书写不规范（间架结构松散）
    reviewed = notes.review_dictation_record(
        child_id, rec["id"],
        mistakes=["认真"],
        irregular_words=[{"word": "奇迹", "reason": "间架结构左右松散，偏旁比例失衡", "suggestion": "注意左窄右宽紧凑"}],
        stars=2,
        comment="总体不错，注意笔顺与规范书写！"
    )
    assert reviewed is not None
    assert reviewed["status"] == "reviewed"
    assert "认真" in reviewed["mistakes"]
    assert len(reviewed["irregular_words"]) == 1
    assert reviewed["irregular_words"][0]["word"] == "奇迹"

    # 验证错题消灭本中：同时收录了错字与书写不规范生字
    all_mistakes = notes.list_mistakes(child_id, category="dictation")
    assert len(all_mistakes) == 2
    
    wrong_item = next(m for m in all_mistakes if m.expected == "认真")
    assert "生字听写错字" in wrong_item.question

    irreg_item = next(m for m in all_mistakes if m.expected == "奇迹")
    assert "生字听写书写不规范" in irreg_item.question
    assert "书写不规范" in irreg_item.wrong_answer

    # 验证墨水屏渲染错题本能正常排版展示书写不规范生字
    rendered = display.render_mistakes(all_mistakes, sel_idx=1)
    rendered_body = " ".join(rendered.get("body", []))
    assert "奇迹" in rendered_body
    assert "不规范" in rendered_body


def test_dictation_photo_auto_check_api(monkeypatch):
    """测试家长在小程序/网页端拍照自动检查核对听写接口。"""
    from starlette.testclient import TestClient
    from app import app
    from unittest.mock import AsyncMock
    import json
    model_result = {"items": [
        {"word": "晨曦", "status": "correct"},
        {"word": "芬芳", "status": "wrong", "reason": "错字"},
        {"word": "探索", "status": "irregular", "reason": "结构松散"},
        {"word": "坚韧", "status": "correct"},
    ]}
    monkeypatch.setattr(app.state.runtime.llm, "complete", AsyncMock(return_value=json.dumps(model_result, ensure_ascii=False)))
    with TestClient(app) as client:
        # 1. 先设置听写词单
        r_set = client.post("/api/pocketclaw/mp/dictation", json={
            "title": "生字智能拍照批改测试",
            "raw_text": "晨曦, 芬芳, 探索, 坚韧",
            "push": False
        })
        assert r_set.status_code == 200

        # 2. 模拟孩子在 Note 4 端完成听写，生成待批改记录
        notes = app.state.pocketclaw["notes"]
        words = [("晨曦", "chén xī", "晨曦"), ("芬芳", "fēn fāng", "芬芳"), ("探索", "tàn suǒ", "探索"), ("坚韧", "jiān rèn", "坚韧")]
        rec = notes.add_dictation_record("kid", title="生字智能拍照批改测试", words=words)
        assert rec["status"] == "pending"

        # 3. 家长调用拍照自动检查核对接口 (POST /api/pocketclaw/mp/dictation/auto_check)
        r_check = client.post("/api/pocketclaw/mp/dictation/auto_check", json={
            "record_id": rec["id"],
            "image": "data:image/jpeg;base64,aW1hZ2U=",
            "stars": 2
        })
        assert r_check.status_code == 200
        res = r_check.json()
        assert res["ok"] is True
        assert res["record"]["status"] == "reviewed"
        assert res["record"]["auto_checked"] is True
        assert "score" in res["check_result"]
        assert "items" in res["check_result"]

        # 4. 关键验证：错误的字和书写不规范的字均必须列入错题本
        mistakes_in_db = notes.list_mistakes("kid", category="dictation")
        dictation_expected_words = [m.expected for m in mistakes_in_db]
        
        # 只要在自动检查结果中有标注 wrong 或 irregular 的词，都必须在错题本中找到
        for m in res["mistakes"]:
            assert m in dictation_expected_words
        for irreg in res["irregular_words"]:
            w = irreg["word"] if isinstance(irreg, dict) else irreg
            assert w in dictation_expected_words
            # 且错题本中包含书写不规范标签
            irreg_db = next(item for item in mistakes_in_db if item.expected == w)
            assert "书写不规范" in irreg_db.question or "书写不规范" in irreg_db.wrong_answer

    # 5. 再次拉取听写记录，验证状态与字段持久化完整
    r_get = TestClient(app).get("/api/pocketclaw/mp/dictation")
    assert r_get.status_code == 200
    d_data = r_get.json()
    matched_rec = next(r for r in d_data["records"] if r["id"] == rec["id"])
    assert matched_rec["status"] == "reviewed"
    assert matched_rec["auto_checked"] is True


def test_dictation_review_rejects_invalid_and_repeated_submissions(monkeypatch):
    from starlette.testclient import TestClient
    from app import app
    from unittest.mock import AsyncMock
    notes = app.state.pocketclaw["notes"]
    child = "review_safety_test"
    rec = notes.add_dictation_record(child, "安全测试", [("温暖", "", ""), ("认真", "", "")])
    initial_balance = notes.get_star_bank(child)["balance"]
    url = "/api/pocketclaw/mp/dictation/auto_check"
    payload = {"record_id": rec["id"], "child_id": child, "image": "aW1hZ2U=", "stars": 2}
    model = AsyncMock(return_value='{"items": []}')
    monkeypatch.setattr(app.state.runtime.llm, "complete", model)
    with TestClient(app) as client:
        assert client.post(url, json={**payload, "image": "!"}).status_code == 400
        assert client.post(url, json=payload).status_code == 503
        assert rec["status"] == "pending"
        assert notes.list_mistakes(child, category="dictation") == []
        assert notes.get_star_bank(child)["balance"] == initial_balance
        review = {"child_id": child, "record_id": rec["id"], "mistakes": ["温暖"], "stars": 2}
        assert client.post("/api/pocketclaw/mp/dictation/review", json={**review, "mistakes": ["不在词单"]}).status_code == 400
        assert client.post("/api/pocketclaw/mp/dictation/review", json=review).status_code == 200
        assert client.post("/api/pocketclaw/mp/dictation/review", json=review).status_code == 409
        assert client.post(url, json=payload).status_code == 409
        assert notes.get_star_bank(child)["balance"] == initial_balance + 2
        assert len(notes.list_mistakes(child, category="dictation")) == 1


def test_dictation_set_api():
    from starlette.testclient import TestClient
    from app import app
    child = "dictation_sets_test"
    with TestClient(app) as client:
        response = client.post("/api/pocketclaw/mp/dictation", json={"child_id": child, "title": "第一单元", "raw_text": "温暖,认真", "save_as_set": True, "push": False})
        assert response.status_code == 200
        data = client.get("/api/pocketclaw/mp/dictation", params={"child_id": child}).json()
        assert data["title"] == "第一单元"
        assert len(data["sets"]) == 1
        set_id = data["sets"][0]["id"]
        assert client.post("/api/pocketclaw/mp/dictation/select_set", json={"child_id": child, "set_id": set_id}).json()["ok"]
        assert client.post("/api/pocketclaw/mp/dictation/delete_set", json={"child_id": child, "set_id": set_id}).json()["ok"]


def test_overview_is_scoped_to_child():
    from types import SimpleNamespace
    from starlette.testclient import TestClient
    from app import app
    state = app.state.pocketclaw
    child = 'overview_scope_child'
    state['notes'].add_dictation_record(child, '待批改', [('温暖', '', '')])
    state['live']['other-test-device'] = SimpleNamespace(child_id='other_child', mode='chat', battery=88, _focus=None)
    try:
        with TestClient(app) as client:
            data = client.get('/api/pocketclaw/mp/overview', params={'child_id': child}).json()
        assert data['pending_dictation_count'] == 1
        assert data['online'] is False
        assert data['current_mode'] == 'task'
        assert data['battery'] is None
    finally:
        del state['live']['other-test-device']
