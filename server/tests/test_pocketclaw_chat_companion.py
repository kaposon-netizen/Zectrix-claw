"""测试 PocketClaw AI学伴功能：长按电源键进入/退出、5分钟闲置看门狗自动退出、以及菜单/模式集成。"""
import asyncio
import time
import pytest
from unittest.mock import AsyncMock, patch

from pocketclaw import display, notes
from pocketclaw.bridge import PocketClawSession
from pocketclaw.notes import NotesStore


def test_ai_companion_in_menu_and_modes():
    # 验证 MENU_ITEMS 中含有 AI学伴
    modes_in_menu = [m[2] for m in display.MENU_ITEMS]
    labels_in_menu = [m[0] for m in display.MENU_ITEMS]
    assert "chat" in modes_in_menu
    assert "AI学伴" in labels_in_menu

    # 验证 CORE_MODES 中注册了 ("chat", "AI学伴")
    core_mode_keys = [m[0] for m in PocketClawSession.CORE_MODES]
    assert "chat" in core_mode_keys


def test_render_chat_companion_welcome():
    page = display.render_chat_companion_welcome(battery=90)
    assert page["mode"] == "chat"
    assert "AI学伴" in page["title"]
    assert len(page["body"]) == 8
    assert page["truncated"] is False
    assert page["footer"]["battery"] == 90
    body_text = "\n".join(page["body"])
    assert "专属AI学伴小龙虾" in body_text
    assert "5分钟" in body_text


def test_power_button_enter_and_exit_chat(tmp_path):
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
        bridge.battery = 80
        bridge.mode = "task"

        # 长按下键进入菜单，再次长按返回原页面。学伴从菜单进入。
        await bridge._on_button("power_long", 1000.0)
        assert bridge.mode == "menu"
        await bridge._on_button("power_long", 1010.0)
        assert bridge.mode == "task"
        await bridge._switch_to_mode("chat", 1020.0)
        assert bridge._chat_idle_task is not None
        await bridge._on_button("down_long", 1030.0)
        assert bridge.mode == "task"
        assert bridge._chat_idle_task is None

        await bridge.close()

    asyncio.run(_run())


def test_reading_mode_down_long_preserved(tmp_path):
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
        bridge.mode = "reading"

        await bridge._on_button("power_long", 1000.0)
        assert bridge.mode == "menu"
        await bridge._on_button("power_long", 1001.0)
        assert bridge.mode == "reading"

        await bridge.close()

    asyncio.run(_run())


def test_chat_idle_watchdog_auto_exit(tmp_path):
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

        # 注入可控时钟
        fake_time = [1000.0]
        bridge._clock = lambda: fake_time[0]

        # 进入 AI学伴模式
        await bridge._switch_to_mode("chat", fake_time[0])
        assert bridge.mode == "chat"
        assert bridge._chat_idle_task is not None

        # 模拟经过 100 秒（未到 300 秒）：仍在 chat 模式
        fake_time[0] += 100.0
        await asyncio.sleep(0.05)
        assert bridge.mode == "chat"

        # 模拟经过 301 秒（超过 300 秒）：看门狗触发，自动返回 task 模式
        fake_time[0] += 205.0
        # 等待 watchdog loop 醒来并切回 task
        for _ in range(20):
            await asyncio.sleep(0.05)
            if bridge.mode == "task":
                break

        assert bridge.mode == "task"
        assert bridge._chat_idle_task is None

        await bridge.close()

    asyncio.run(_run())


def test_chat_voice_exit(tmp_path):
    async def _run():
        db_path = tmp_path / "notes.json"
        ndb = NotesStore(path=str(db_path))

        send_mock = AsyncMock()
        runtime_mock = AsyncMock()
        stt_mock = AsyncMock()
        stt_mock.transcribe = AsyncMock(return_value="做作业")
        bridge = PocketClawSession(
            runtime=runtime_mock,
            send_json=send_mock,
            send_bin=AsyncMock(),
            stt=stt_mock,
            tts=AsyncMock(),
            registry=AsyncMock(),
            notes=ndb
        )
        bridge.child_id = "test_child"
        await bridge._switch_to_mode("chat", 1000.0)
        assert bridge.mode == "chat"

        # 语音说“做作业”或“退出”
        await bridge._on_utterance(b"123", "做作业", 1010.0)
        assert bridge.mode == "task"
        assert bridge._chat_idle_task is None

        await bridge.close()

    asyncio.run(_run())
