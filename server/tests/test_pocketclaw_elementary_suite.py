# -*- coding: utf-8 -*-
"""Comprehensive test suite for PocketClaw Elementary School Learning Management Features."""
import time
import pytest
from starlette.testclient import TestClient
from pocketclaw.notes import NotesStore
from pocketclaw import display
from pocketclaw.bridge import PocketClawSession
from pocketclaw.device_registry import DeviceRegistry
from app import app


from pocketclaw.voice import OfflineSTT, OfflineTTS


class MockBridgeSend:
    def __init__(self):
        self.sent_pages = []
        self.spoken = []

    async def send_display(self, page):
        if isinstance(page, str):
            import json
            try:
                page = json.loads(page)
            except Exception:
                pass
        if isinstance(page, dict) and "page" in page:
            page = page["page"]
        self.sent_pages.append(page)

    async def send_audio(self, pcm):
        pass


def _create_test_session():
    send = MockBridgeSend()
    reg = DeviceRegistry(":memory:", strict=False)
    reg.upsert("test_dev")
    notes = NotesStore(":memory:")
    sess = PocketClawSession(
        runtime=None,
        send_json=send.send_display,
        send_bin=None,
        stt=OfflineSTT(),
        tts=OfflineTTS(),
        registry=reg,
        notes=notes,
    )
    sess.child_id = "kid"
    sess.helloed = True
    sess.send_display = send.send_display
    async def mock_speak(text, **kw):
        send.spoken.append(text)
    sess._speak = mock_speak
    return sess, send, notes


def test_notes_habit_management():
    """测试好习惯打卡数据模型与连续打卡计数。"""
    notes = NotesStore(":memory:")
    habits = notes.list_habits("kid")
    assert len(habits) >= 5
    assert habits[0].done is False

    # 1. 打卡第一个习惯
    h1 = notes.toggle_habit("kid", habits[0].id)
    assert h1 is not None and h1.done is True
    assert h1.streak == 2

    # 2. 统计
    stats = notes.habit_stats("kid")
    assert stats["done"] == 1
    assert stats["stars"] == 2

    # 3. 添加自定义习惯
    new_h = notes.add_habit("kid", "每天练琴30分钟", icon="🎹")
    assert new_h.name == "每天练琴30分钟"
    assert new_h.icon == "🎹"
    assert len(notes.list_habits("kid")) == len(habits) + 1

    # 4. 删除习惯
    ok = notes.delete_habit("kid", new_h.id)
    assert ok is True


def test_notes_timetable_and_today_schedule():
    """测试桌面课程表排课与当日备忘。"""
    notes = NotesStore(":memory:")
    tt = notes.get_timetable("kid")
    assert "1" in tt and "5" in tt
    assert len(tt["1"]["courses"]) == 6

    # 检查周一备忘
    sched = notes.get_today_schedule("kid", weekday=0)
    assert sched["day_name"] == "周一"
    assert "语文" in sched["courses"]
    assert "穿礼服戴红领巾升旗" in sched["note"]

    # 自定义修改周三课程
    custom_wed = {"day_name": "周三", "courses": ["数学", "科学", "体育", "语文", "美术", "阅读"], "note": "带跳绳"}
    tt["3"] = custom_wed
    notes.set_timetable("kid", tt)

    sched3 = notes.get_today_schedule("kid", weekday=2)
    assert sched3["courses"][0] == "数学"
    assert sched3["note"] == "带跳绳"


def test_notes_mistakes_elimination():
    """测试错题沉淀、去重计数与消灭机制。"""
    notes = NotesStore(":memory:")
    
    # 1. 记录错题
    m1 = notes.add_mistake("kid", "arith", "15 - 7 = ?", "8", "6")
    assert m1.count == 1
    assert m1.expected == "8"

    # 2. 重复错同一道题，自动累加复错频次
    m2 = notes.add_mistake("kid", "arith", "15 - 7 = ?", "8", "7")
    assert m2.id == m1.id
    assert m2.count == 2
    assert m2.wrong_answer == "7"

    # 3. 记录听写错字
    notes.add_mistake("kid", "dictation", "按时", "按时", "按石")
    assert len(notes.list_mistakes("kid")) == 2
    assert len(notes.list_mistakes("kid", category="arith")) == 1

    # 4. 消灭一道错题
    ok = notes.delete_mistake("kid", m1.id)
    assert ok is True
    assert len(notes.list_mistakes("kid", category="arith")) == 0


def test_notes_daily_idioms():
    """测试每日成语与国学典故。"""
    notes = NotesStore(":memory:")
    idiom = notes.get_daily_idiom("kid")
    assert "idiom" in idiom
    assert "meaning" in idiom

    # 设置指定成语
    notes.set_daily_idiom("kid", {
        "idiom": "悬梁刺股",
        "pinyin": "xuán liáng cì gǔ",
        "meaning": "形容刻苦学习。",
        "source": "《战国策》",
        "example": "古人悬梁刺股的故事激励着我们。"
    })
    cur = notes.get_daily_idiom("kid")
    assert cur["idiom"] == "悬梁刺股"


def test_display_rendering_of_elementary_modules():
    """测试墨水屏整屏渲染：习惯打卡高亮、课表格式、错题本与成语卡片。"""
    notes = NotesStore(":memory:")
    habits = notes.list_habits("kid")

    # 1. 习惯打卡渲染（选中项包含 INVERT_MARK 黑底白字哨兵）
    page_habits = display.render_habits(habits, sel_idx=0, partial=True)
    assert page_habits["mode"] == "habit"
    assert page_habits["partial"] is True
    body_str = "\n".join(page_habits["body"])
    assert display.INVERT_MARK in body_str
    assert "晨读" in body_str

    # 2. 桌面课表渲染
    page_tt = display.render_timetable("周一", ["语文", "数学", "英语", "体育", "科学", "美术"], "带跳绳")
    assert page_tt["mode"] == "timetable"
    assert "第1节  语文" in page_tt["body"]
    assert "第6节  美术" in page_tt["body"]
    assert "备忘: 带跳绳" in "\n".join(page_tt["body"])

    # 3. 错题本渲染
    m = notes.add_mistake("kid", "arith", "13 - 6 = ?", "7", "8")
    page_m = display.render_mistakes([m], sel_idx=0)
    assert page_m["mode"] == "mistakes"
    assert "13 - 6 = ?" in "\n".join(page_m["body"])

    # 4. 每日成语渲染
    page_id = display.render_daily_idiom(notes.get_daily_idiom("kid"))
    assert page_id["mode"] == "idiom"
    assert "【成语】" in "\n".join(page_id["body"])


@pytest.mark.anyio
async def test_bridge_interactive_habits_and_timetable():
    """测试 Note 4 按键在好习惯与课表模式下的交互。"""
    sess, send, notes = _create_test_session()

    # 1. 启动好习惯打卡模式
    await sess._switch_to_mode("habit", now=100.0)
    assert sess.mode == "habit"
    assert len(send.sent_pages) >= 1
    assert send.sent_pages[-1]["mode"] == "habit"

    # 2. 按向下键切换光标到第 2 项（局部刷新，静默无延时）
    await sess._on_button("down", now=101.0)
    assert sess._habit_idx == 1
    assert send.sent_pages[-1]["partial"] is True

    # 3. 按确认键打卡
    await sess._on_button("confirm", now=102.0)
    assert any("打卡完成" in s for s in send.spoken)
    h_list = notes.list_habits("kid")
    assert h_list[1].done is True

    # 4. 切换到桌面课表模式
    await sess._switch_to_mode("timetable", now=103.0)
    assert sess.mode == "timetable"
    assert send.sent_pages[-1]["mode"] == "timetable"

    # 5. 按确认键朗读当日课表
    await sess._on_button("confirm", now=104.0)
    assert any("课程" in s for s in send.spoken)


@pytest.mark.anyio
async def test_device_timetable_is_daily_and_navigation_wraps():
    sess, send, notes = _create_test_session()
    await sess._show_timetable(100, weekday=4)
    page = send.sent_pages[-1]
    assert "周五" in page["title"] and len(page["body"]) <= 8
    assert "第1节" in page["body"][0]
    await sess._on_button("down", now=101)
    assert "周一" in send.sent_pages[-1]["title"]
    assert send.sent_pages[-1]["partial"] is True
    await sess._on_button("up", now=102)
    assert "周五" in send.sent_pages[-1]["title"]
    card = sess._screensaver_page({"type": "timetable"})
    assert card["mode"] == "screensaver"
    assert "桌面课表" in card["title"] and "一周课表" not in card["title"]


def test_daily_timetable_keeps_long_content_within_screen():
    page = display.render_timetable("周三", ["道德与法治" * 8] * 6, "带好书包" * 15)
    assert len(page["body"]) == 7
    assert all(display._display_width(line) <= display.CHAT_COLS for line in page["body"])
    assert all(line.endswith("…") for line in page["body"])


def test_mp_rest_apis_full_coverage():
    """测试微信小程序 /api/pocketclaw/mp/... 全量接口。"""
    client = TestClient(app)

    # 1. 综合态势大盘
    r = client.get("/api/pocketclaw/mp/overview")
    assert r.status_code == 200
    ov = r.json()
    assert ov["ok"] is True
    assert "habits" in ov
    assert "timetable_today" in ov
    assert "mistakes" in ov
    assert "daily_idiom" in ov

    # 2. 习惯 CRUD
    r_add_h = client.post("/api/pocketclaw/mp/habits", json={"name": "按时写作业", "icon": "⭐"})
    assert r_add_h.status_code == 200
    h_id = r_add_h.json()["habit"]["id"]
    assert r_add_h.json()["habit"]["done"] is False

    r_tog = client.post(f"/api/pocketclaw/mp/habits/{h_id}/toggle")
    assert r_tog.status_code == 200
    assert r_tog.json()["habit"]["done"] is True

    r_del_h = client.delete(f"/api/pocketclaw/mp/habits/{h_id}")
    assert r_del_h.status_code == 200 and r_del_h.json()["ok"] is True

    # 3. 课程表排课
    r_tt = client.get("/api/pocketclaw/mp/timetable")
    assert r_tt.status_code == 200
    tt_data = r_tt.json()["timetable"]
    tt_data["1"]["note"] = "带水彩笔和跳绳"
    r_set_tt = client.post("/api/pocketclaw/mp/timetable", json={"timetable": tt_data})
    assert r_set_tt.status_code == 200

    # 4. 错题消灭本
    r_add_m = client.post("/api/pocketclaw/mp/mistakes", json={
        "category": "arith",
        "question": "16 - 8 = ?",
        "expected": "8",
        "wrong_answer": "9"
    })
    assert r_add_m.status_code == 200
    mid = r_add_m.json()["mistake"]["id"]

    r_m_list = client.get("/api/pocketclaw/mp/mistakes")
    assert r_m_list.status_code == 200
    assert any(m["id"] == mid for m in r_m_list.json()["mistakes"])

    r_del_m = client.delete(f"/api/pocketclaw/mp/mistakes/{mid}")
    assert r_del_m.status_code == 200 and r_del_m.json()["ok"] is True

    # 5. 每日成语
    r_idiom = client.get("/api/pocketclaw/mp/idiom")
    assert r_idiom.status_code == 200
    assert len(r_idiom.json()["all"]) >= 5

    # 6. 星星存折与心愿兑换
    r_stars = client.get("/api/pocketclaw/mp/stars")
    assert r_stars.status_code == 200
    bank = r_stars.json()["bank"]
    assert "balance" in bank
    cur_bal = bank["balance"]

    # 家长奖励 10 颗星
    r_add_s = client.post("/api/pocketclaw/mp/stars/add", json={"count": 10, "reason": "自主阅读好习惯"})
    assert r_add_s.status_code == 200
    assert r_add_s.json()["balance"] == cur_bal + 10

    # 添加新心愿并兑换
    r_wish = client.post("/api/pocketclaw/mp/stars/wishes", json={"title": "去一次天文馆", "stars_needed": 10, "icon": "🔭"})
    assert r_wish.status_code == 200
    wid = r_wish.json()["wish"]["id"]

    r_redeem = client.post(f"/api/pocketclaw/mp/stars/wishes/{wid}/redeem")
    assert r_redeem.status_code == 200
    assert r_redeem.json()["ok"] is True


def test_practice_parse_spoken_number():
    """测试口语语音数字解析器（支持阿拉伯数字与中文字符表达）。"""
    from pocketclaw import practice
    assert practice.parse_spoken_number("等于8") == 8
    assert practice.parse_spoken_number("是 24") == 24
    assert practice.parse_spoken_number("十五") == 15
    assert practice.parse_spoken_number("二十四") == 24
    assert practice.parse_spoken_number("一百零五") == 105
    assert practice.parse_spoken_number("负三") == -3
    assert practice.parse_spoken_number("算不出来") is None


def test_star_bank_data_layer():
    """测试星星存折与心愿兑换数据层逻辑。"""
    notes = NotesStore(":memory:")
    bank = notes.get_star_bank("kid")
    assert bank["balance"] == 10
    assert len(bank["wishes"]) >= 4

    # 赚取星星
    b2 = notes.add_stars("kid", 5, reason="好习惯打卡")
    assert b2 == 15

    # 兑换心愿（需 20 颗，余额不足）
    res = notes.redeem_wish("kid", "w_3")  # w_3 需 20 颗
    assert res["ok"] is False
    assert "星星不足" in res["error"]

    # 赚足星星并兑换
    notes.add_stars("kid", 10, reason="连续通关")
    res_ok = notes.redeem_wish("kid", "w_3")
    assert res_ok["ok"] is True
    assert res_ok["balance"] == 5


@pytest.mark.anyio
async def test_arith_mistake_auto_accumulation():
    """测试口算模式下答错或按上键时，错题自动沉淀进错题本。"""
    sess, send, notes = _create_test_session()

    # 进入口算模式
    await sess._switch_to_mode("arith", now=100.0)
    assert sess.mode == "arith"
    assert sess._arith_session is not None

    cur_q = sess._arith_session.cur_question
    wrong_ans = str(int(cur_q.ans) + 5)

    # 语音回答错误答案
    await sess.on_control({"kind": "text", "text": f"等于 {wrong_ans}"}, now=101.0)
    assert any("算错啦" in s for s in send.spoken)

    # 检查错题本是否已自动录入该题
    m_list = notes.list_mistakes("kid", category="arith")
    assert len(m_list) == 1
    assert m_list[0].question == cur_q.expr
    assert m_list[0].expected == cur_q.ans
    assert m_list[0].wrong_answer == str(wrong_ans)
