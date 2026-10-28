"""测试 PocketClaw 屏保模式静默进入、世界名画 Floyd-Steinberg 墨水屏抖动位图与菜单智能频次/家长端定制排序。"""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from pocketclaw import display, notes, screensaver
from pocketclaw.bridge import PocketClawSession
from pocketclaw.notes import NotesStore


def test_fast_menu_navigation_does_not_trigger_mode_shortcuts(tmp_path):
    async def run():
        store = NotesStore(path=str(tmp_path / "notes.json"))
        session = PocketClawSession(
            runtime=AsyncMock(), send_json=AsyncMock(), send_bin=AsyncMock(),
            stt=AsyncMock(), tts=AsyncMock(), registry=AsyncMock(), notes=store,
        )
        session.child_id = "menu_test"
        session.helloed = True
        items = store.get_sorted_menu_items(session.child_id)

        async def button(name, now):
            await session.on_control({"kind": "button", "name": name}, now=now)

        await button("menu", 1.0)
        # 两次短按与固件生成的双击事件可以按不同顺序到达。
        for i, name in enumerate(("down", "mode_next", "down", "down", "mode_next")):
            await button(name, 2.0 + i * 0.01)
        assert session.mode == "menu"
        assert session._menu_idx == 3

        for i, name in enumerate(("up", "up", "mode_prev")):
            await button(name, 3.0 + i * 0.01)
        assert session.mode == "menu"
        assert session._menu_idx == 1

        await button("confirm", 4.0)
        assert session.mode == items[1][2]

    asyncio.run(run())


def test_floyd_steinberg_dither_algorithm():
    # 构造一个 300 行 × 400 列的简单渐变灰度图
    gray_grid = [[float((x + y) % 256) for x in range(400)] for y in range(300)]
    bmp = screensaver.floyd_steinberg_dither(gray_grid, width=400, height=300)

    # 400 * 300 / 8 = 15000 字节
    assert len(bmp) == 15000
    assert isinstance(bmp, bytes)
    # 验证不是全黑也不是全白
    ones = sum(bin(b).count('1') for b in bmp)
    assert 1000 < ones < 15000 * 8 - 1000


def test_screensaver_art_card_has_bitmap_and_clean_display():
    sess = screensaver.ScreensaverSession(mode="art")
    card = sess.get_card()

    assert card["type"] == "art"
    assert "raw_4bpp_b64" in card or "raw_1bpp_b64" in card
    bmp_b64 = card.get("raw_4bpp_b64") or card.get("raw_1bpp_b64")
    assert len(bmp_b64) > 0  # 含有 base64 编码的名画数据
    assert "art_title" in card

    # 验证渲染输出
    page = display.render_screensaver(card)
    assert page.get("raw_4bpp_b64") == card.get("raw_4bpp_b64") or page.get("raw_1bpp_b64") == card.get("raw_1bpp_b64")
    assert page["mode"] == "screensaver"
    # 验证名画重点展示画作位图，标题包含画名，无冗余的长篇介绍
    body_text = "\n".join(page["body"])
    assert f"《{card['art_title']}》" in body_text


def test_silent_screensaver_entry(tmp_path):
    async def _run():
        db_path = tmp_path / "notes.json"
        ndb = NotesStore(path=str(db_path))

        send_mock = AsyncMock()
        runtime_mock = AsyncMock()
        bridge = PocketClawSession(
            runtime=runtime_mock,
            send_json=send_mock,
            send_bin=AsyncMock(),
            stt=AsyncMock(),
            tts=AsyncMock(),
            registry=AsyncMock(),
            notes=ndb
        )
        bridge.child_id = "test_child"
        bridge.battery = 85

        # 模拟从功能菜单切换到屏保模式
        with patch.object(bridge, "_speak", new_callable=AsyncMock) as mock_speak:
            await bridge._switch_to_mode("screensaver", 1000.0)
            # 必须静默进入，严禁播报语音
            assert mock_speak.call_count == 0
            assert bridge.mode == "screensaver"

        # 模拟在屏保模式下按上下键切换分类或确认键翻一张
        with patch.object(bridge, "_speak", new_callable=AsyncMock) as mock_speak:
            await bridge._screensaver_step_card(1, 1005.0)
            await bridge._screensaver_step_type(1, 1010.0)
            # 翻看屏保同样保持静默
            assert mock_speak.call_count == 0

    asyncio.run(_run())


def test_menu_frequency_sorting_and_custom_override(tmp_path):
    db_path = tmp_path / "notes.json"
    ndb = NotesStore(path=str(db_path))
    child = "child_menu_test"

    # 初始默认顺序
    init_items = ndb.get_sorted_menu_items(child)
    assert init_items[0][2] == "task"

    # 模拟点击：狂点 "screensaver" 10 次，点 "arith" 5 次
    for _ in range(10):
        ndb.record_menu_click(child, "screensaver")
    for _ in range(5):
        ndb.record_menu_click(child, "arith")

    # 频次算法：选择越多越靠前
    sorted_items = ndb.get_sorted_menu_items(child)
    modes_order = [m for _, _, m in sorted_items]
    assert modes_order[0] == "screensaver"  # 10 次排第 1
    assert modes_order[1] == "arith"        # 5 次排第 2

    # 家长手动指定顺序
    custom = ["reading", "dictation", "screensaver"]
    ndb.set_custom_menu_order(child, custom)
    custom_items = ndb.get_sorted_menu_items(child)
    custom_modes = [m for _, _, m in custom_items]
    assert custom_modes[0] == "reading"
    assert custom_modes[1] == "dictation"
    assert custom_modes[2] == "screensaver"

    # 家长重置回智能频次排序（保留点击统计）
    ndb.reset_menu_order(child, reset_clicks=False)
    reset_freq_items = ndb.get_sorted_menu_items(child)
    reset_freq_modes = [m for _, _, m in reset_freq_items]
    assert reset_freq_modes[0] == "screensaver"

    # 家长彻底重置（清除统计与顺序）
    ndb.reset_menu_order(child, reset_clicks=True)
    full_reset_items = ndb.get_sorted_menu_items(child)
    full_reset_modes = [m for _, _, m in full_reset_items]
    assert full_reset_modes[0] == "task"


def test_menu_api_endpoints(tmp_path):
    from fastapi.testclient import TestClient
    from app import app
    from pocketclaw import notes as pnotes

    db_path = tmp_path / "notes.json"
    pnotes.db = NotesStore(path=str(db_path))

    client = TestClient(app)

    # 1. GET /api/pocketclaw/mp/menu
    res = client.get("/api/pocketclaw/mp/menu")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert len(data["items"]) >= 15
    assert data["is_custom"] is False

    # 2. POST /api/pocketclaw/mp/menu/order
    res = client.post("/api/pocketclaw/mp/menu/order", json={"order": ["screensaver", "task", "habit"]})
    assert res.status_code == 200
    res = client.get("/api/pocketclaw/mp/menu")
    data = res.json()
    assert data["is_custom"] is True
    assert data["items"][0]["mode"] == "screensaver"

    # 3. POST /api/pocketclaw/mp/menu/reset
    res = client.post("/api/pocketclaw/mp/menu/reset", json={"reset_clicks": False})
    assert res.status_code == 200
    res = client.get("/api/pocketclaw/mp/menu")
    data = res.json()
    assert data["is_custom"] is False


def test_instant_speech_interruption_on_button_press(tmp_path):
    """验证按键操作（下一页、切换功能、选菜单）立即中止当前正在进行的语音朗读并秒级响应下一步。"""
    async def _run():
        db_path = tmp_path / "notes.json"
        ndb = NotesStore(path=str(db_path))

        sent_json_msgs = []
        async def mock_send_json(msg_str):
            import json
            sent_json_msgs.append(json.loads(msg_str))

        sent_bin_data = []
        async def mock_send_bin(b):
            sent_bin_data.append(b)

        # 构造一个长时间分块流式 TTS
        class SlowStreamingTTS:
            name = "slow_stream"
            sample_rate = 16000
            async def synthesize_stream(self, text: str):
                for i in range(100):
                    yield b"\x00\x00" * 960   # 模拟 100 个 60ms 块
                    await asyncio.sleep(0.02)

        session = PocketClawSession(
            runtime=AsyncMock(),
            send_json=mock_send_json,
            send_bin=mock_send_bin,
            stt=AsyncMock(),
            tts=SlowStreamingTTS(),
            registry=AsyncMock(strict=False),
            notes=ndb
        )
        session.child_id = "test_kid"
        session.helloed = True
        session.battery = 90

        # 1. 启动一段长文本朗读
        await session._speak("这是一个正在长时间朗读的长句子，用来测试按键随时毫秒级打断。")
        assert session._speech_task is not None
        assert not session._speech_task.done()

        # 检查是否已发送 tts_start
        assert any(m.get("kind") == "tts_start" for m in sent_json_msgs)

        # 2. 模拟用户突然按下向下键（下一页/下一句）
        await session.on_control({"kind": "button", "name": "down"}, now=10.0)

        # 验证之前的朗读任务已被彻底取消打断
        assert session._speech_task is None or session._speech_task.done()

        # 验证已向设备下发 tts_stop 帧（通知设备关闭功放并清空播放缓冲区）
        assert any(m.get("kind") == "tts_stop" for m in sent_json_msgs)

        # 3. 验证进入英语阅读模式并选句朗读时，按下一页立即打断并切换高亮
        await session._switch_to_mode("reading", now=20.0)
        assert session.mode == "reading"

        # 按确认键朗读当前句
        sent_json_msgs.clear()
        await session.on_control({"kind": "button", "name": "confirm"}, now=21.0)
        assert session._speech_task is not None
        assert not session._speech_task.done()

        # 正在朗读中按向下键（选下一句）
        await session.on_control({"kind": "button", "name": "down"}, now=22.0)
        # 朗读立即停止，tts_stop 立即下发
        assert session._speech_task is None or session._speech_task.done()
        assert any(m.get("kind") == "tts_stop" for m in sent_json_msgs)
        # 英语阅读选句已切换到第 2 句 (sel_idx == 1)
        assert session._reading_session.sel_idx == 1

        # 4. 验证在任何播报中按菜单键立即静音并打开功能菜单
        await session._speak("正在播报其他内容...")
        sent_json_msgs.clear()
        await session.on_control({"kind": "button", "name": "menu"}, now=30.0)
        assert session.mode == "menu"
        assert any(m.get("kind") == "tts_stop" for m in sent_json_msgs)
        # 且进入功能菜单保持彻底静默，不发 tts_start
        assert not any(m.get("kind") == "tts_start" for m in sent_json_msgs)

        # 在菜单中上下选择时立即下发 stop 并切项
        sent_json_msgs.clear()
        await session.on_control({"kind": "button", "name": "down"}, now=31.0)
        assert any(m.get("kind") == "tts_stop" for m in sent_json_msgs)
        assert session._menu_idx == 1

    asyncio.run(_run())


def test_menu_and_function_entry_completely_silent(tmp_path):
    """验证进入功能导航菜单与进入各个功能页面时，完全静默，绝不播报功能名字与用法。"""
    async def _run():
        db_path = tmp_path / "notes.json"
        ndb = NotesStore(path=str(db_path))

        sent_json_msgs = []
        async def mock_send_json(msg_str):
            import json
            sent_json_msgs.append(json.loads(msg_str))

        session = PocketClawSession(
            runtime=AsyncMock(),
            send_json=mock_send_json,
            send_bin=AsyncMock(),
            stt=AsyncMock(),
            tts=AsyncMock(),
            registry=AsyncMock(strict=False),
            notes=ndb
        )
        session.child_id = "test_kid"
        session.helloed = True
        session.battery = 90

        # 1. 按菜单键进入功能菜单：彻底静音
        with patch.object(session, "_speak", new_callable=AsyncMock) as mock_spk:
            await session.on_control({"kind": "button", "name": "menu"}, now=1.0)
            assert session.mode == "menu"
            assert mock_spk.call_count == 0

        # 2. 依次测试进入所有核心功能页面：除听写遵照用户需求首词播报外，其余全部静默（不念功能名称与用法）
        modes_to_test = [
            "task", "habit", "timetable", "idiom", "recite", "dictation",
            "arith", "mistakes", "vocab", "reading", "feynman", "guess",
            "verify", "feihua", "hanzi", "screensaver"
        ]
        for m in modes_to_test:
            with patch.object(session, "_speak", new_callable=AsyncMock) as mock_spk:
                await session._switch_to_mode(m, now=10.0)
                assert session.mode == m
                if m == "dictation":
                    # 用户明确要求：切换到听写功能页面，第一个词要播报
                    assert mock_spk.call_count == 1
                    assert "第 1 个词" in mock_spk.call_args[0][0]
                else:
                    assert mock_spk.call_count == 0, f"进入功能 {m} 不应有语音播报"

    asyncio.run(_run())
