"""PocketClaw 伴学桌面屏保专项测试套件：
涵盖名人语录、经典古诗词、世界名画、目标倒计时、桌面课表、混合轮播及按键/语音/家长端API联动。
"""
import pytest
from pocketclaw import display, protocol, screensaver
from pocketclaw.bridge import PocketClawSession
from pocketclaw.device_registry import DeviceRegistry
from pocketclaw.notes import NotesStore
from pocketclaw.voice import OfflineSTT, OfflineTTS


class DummyRuntime:
    async def turn(self, prompt: str, **kwargs):
        return {"text": "屏保模式已就绪！", "mode": "chat"}


def _make_session():
    sent_json = []
    sent_bin = []

    async def sj(s: str):
        sent_json.append(protocol.parse_control(s) or s)

    async def sb(b: bytes):
        sent_bin.append(b)

    reg = DeviceRegistry(":memory:")
    notes = NotesStore(":memory:")
    sess = PocketClawSession(
        DummyRuntime(), send_json=sj, send_bin=sb,
        stt=OfflineSTT(), tts=OfflineTTS(), registry=reg, notes=notes
    )
    return sess, sent_json, sent_bin, notes


def test_screensaver_session_logic():
    # 1. 默认轮播模式
    ss = screensaver.ScreensaverSession(mode="cycle")
    assert ss.mode == "cycle"
    c1 = ss.get_card()
    assert c1["is_cycle"] is True
    assert c1["type"] in ("quote", "poem", "art", "countdown", "timetable")

    # 2. 切换到具体分类：名人语录
    ss.mode = "quote"
    card_quote = ss.get_card()
    assert card_quote["type"] == "quote"
    assert "quote" in card_quote
    assert "author" in card_quote

    # 步进下一张名言
    card_q2 = ss.step_card(+1)
    assert card_q2["type"] == "quote"
    assert card_q2["idx"] == 1

    # 3. 切换到古诗词
    ss.mode = "poem"
    card_poem = ss.get_card()
    assert card_poem["type"] == "poem"
    assert "poem_title" in card_poem
    assert len(card_poem["lines"]) > 0

    # 4. 切换到世界名画
    ss.mode = "art"
    card_art = ss.get_card()
    assert card_art["type"] == "art"
    assert "artist" in card_art
    assert "style" in card_art
    assert len(card_art["appreciation"]) > 0

    # 5. 切换到目标倒计时
    ss.mode = "countdown"
    card_cd = ss.get_card()
    assert card_cd["type"] == "countdown"
    assert "event_title" in card_cd
    assert isinstance(card_cd["days_left"], int)

    # 6. 分类步进 (▲/▼)
    next_mode = ss.step_type(+1)
    assert next_mode == "timetable"


def test_countdown_event_math():
    ev = screensaver.CountdownEvent(id="t1", title="期末大考", target_date="2099-12-31")
    assert ev.days_left > 1000
    assert ev.title == "期末大考"


def test_render_screensaver_layouts():
    # 1. 名人语录渲染
    q_card = {
        "type": "quote",
        "type_name": "名人语录",
        "title": "📜 名人语录 (1/8)",
        "quote": "书山有路勤为径，学海无涯苦作舟。",
        "author": "韩愈",
        "source": "《古今贤文》",
        "motto": "勉励珍惜少年光阴",
        "is_cycle": False
    }
    rq = display.render_screensaver(q_card)
    assert rq["mode"] == "screensaver"
    assert any("书山有路勤为径" in line for line in rq["body"])
    assert any("韩愈" in line for line in rq["body"])

    # 2. 世界名画渲染
    art_card = {
        "type": "art",
        "type_name": "世界名画",
        "title": "🎨 世界名画 (1/6)",
        "art_title": "星月夜 (星空)",
        "artist": "文森特·梵高",
        "year": "1889年",
        "style": "后印象主义",
        "appreciation": "画中狂放旋转的星云...",
        "is_cycle": False
    }
    ra = display.render_screensaver(art_card)
    assert any("星月夜" in line for line in ra["body"])
    assert any("后印象主义" in line for line in ra["body"])

    # 3. 倒计时高亮反色黑底白字渲染
    cd_card = {
        "type": "countdown",
        "type_name": "倒计时",
        "title": "⏳ 目标倒计时 (1/3)",
        "event_title": "期末考",
        "target_date": "2026-12-25",
        "days_left": 48,
        "slogan": "认真备战！",
        "is_cycle": False
    }
    rcd = display.render_screensaver(cd_card)
    # 必须包含 INVERT_MARK 高亮哨兵
    assert any(display.INVERT_MARK in line for line in rcd["body"])
    assert any("48 天" in line for line in rcd["body"])


@pytest.mark.anyio
async def test_session_screensaver_voice_and_buttons():
    sess, sent_json, _, notes = _make_session()
    await sess.on_control({"kind": protocol.HELLO, "device_id": "test_dev_screensaver"})

    # 1. 语音开启名画屏保
    await sess.on_control({"kind": protocol.TEXT, "text": "打开名画屏保"})
    assert sess.mode == "screensaver"
    assert sess._screensaver_session is not None
    assert sess._screensaver_session.mode == "art"

    last_disp = [m for m in sent_json if isinstance(m, dict) and m.get("kind") == protocol.DISPLAY][-1]
    assert "世界名画" in last_disp["page"]["title"]

    # 2. 按 DOWN 键切换屏保分类（art -> countdown）
    await sess.on_control({"kind": protocol.BUTTON, "name": "down"})
    assert sess._screensaver_session.mode == "countdown"
    last_disp2 = [m for m in sent_json if isinstance(m, dict) and m.get("kind") == protocol.DISPLAY][-1]
    assert "倒计时" in last_disp2["page"]["title"]

    # 3. 按 confirm 键步进切到下一张倒计时
    await sess.on_control({"kind": protocol.BUTTON, "name": "confirm"})
    last_disp3 = [m for m in sent_json if isinstance(m, dict) and m.get("kind") == protocol.DISPLAY][-1]
    assert "倒计时" in last_disp3["page"]["title"]


def test_notes_store_screensaver_apis():
    notes = NotesStore(":memory:")
    child = "test_kid"

    # 初始默认配置
    cfg = notes.get_screensaver_config(child)
    assert cfg["default_mode"] == "cycle"
    assert cfg["auto_timeout_minutes"] == 5

    # 修改配置
    notes.set_screensaver_config(child, {"default_mode": "art", "auto_timeout_minutes": 10, "enabled": True})
    cfg2 = notes.get_screensaver_config(child)
    assert cfg2["default_mode"] == "art"
    assert cfg2["auto_timeout_minutes"] == 10

    # 倒计时事件管理
    events = notes.list_countdown_events(child)
    assert len(events) >= 3

    # 新增倒计时
    new_ev = notes.add_countdown_event(child, "数学竞赛", "2026-11-20", "勇夺一等奖！")
    assert new_ev["title"] == "数学竞赛"
    events_after = notes.list_countdown_events(child)
    assert any(e["title"] == "数学竞赛" for e in events_after)

    # 删除倒计时
    del_ok = notes.delete_countdown_event(child, new_ev["id"])
    assert del_ok is True
    assert not any(e["id"] == new_ev["id"] for e in notes.list_countdown_events(child))


def test_screensaver_art_shuffle_and_no_page_numbers():
    """测试画作进入随机乱序呈现，且标题与界面不再包含「13/14」这类页码。"""
    # 1. 标题不再包含 (13/14) 这类页码
    ss = screensaver.ScreensaverSession(mode="art")
    card = ss.get_card()
    assert card["type"] == "art"
    assert "13/14" not in card["title"]
    assert "/" not in card["title"]
    assert "(" not in card["title"]
    assert card["title"] == "🎨 艺术长廊 · 世界名画"

    # 2. 每次进入画作打乱顺序
    orders = []
    for _ in range(10):
        session = screensaver.ScreensaverSession(mode="art")
        orders.append(tuple(session._art_order))
    # 10 次进入至少产生多种不同排序组合（打乱生效）
    assert len(set(orders)) > 1

    # 3. 指定画作推屏时，该画作优先置于首位
    ss.shuffle_art(target_art_id="astronaut_moon")
    first_card = ss.get_card()
    assert first_card["art_id"] == "astronaut_moon"

    # 4. 连续按确认键步进，遍历整个乱序列表且不重复
    seen_ids = set()
    for _ in range(len(screensaver.ART_DB)):
        c = ss.get_card()
        seen_ids.add(c["art_id"])
        ss.step_card(+1)
    assert len(seen_ids) == len(screensaver.ART_DB)

