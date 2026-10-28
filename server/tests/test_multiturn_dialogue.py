import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pocketclaw.bridge import PocketClawSession
from pocketclaw.notes import NotesStore
from runtime import StandaloneRuntime


class RecordingModel:
    def __init__(self):
        self.calls = []
    async def complete(self, system, prompt, **kwargs):
        self.calls.append((system, prompt))
        return '我们一起想想。'


class SilentTTS:
    sample_rate = 16000
    async def synthesize_stream(self, text):
        if False:
            yield b''


def make_session(tmp_path):
    notes = NotesStore(str(tmp_path / 'notes.json'))
    model = RecordingModel()
    session = PocketClawSession(StandaloneRuntime(model), send_json=AsyncMock(), send_bin=AsyncMock(),
        stt=AsyncMock(), tts=SilentTTS(), registry=SimpleNamespace(strict=False), notes=notes,
        reading_path=str(tmp_path / 'reading.json'))
    session.child_id = 'kid-a'
    session.mode = 'chat'
    return session, model, notes


@pytest.mark.asyncio
async def test_multiple_conversational_questions_do_not_hijack_modes(tmp_path):
    session, model, notes = make_session(tmp_path)
    notes.set_child_profile('kid-a', {'name': '小明', 'grade': 3, 'gender': '', 'interests': '恐龙'})
    questions = ['你好，我喜欢恐龙。', '恐龙为什么会灭绝？', '恐龙这个词是什么意思？',
                 '我的作业为什么这么难？', '口算怎么做才能更快？', '故事什么时候结束？',
                 '苏格拉底是谁？', '课表有什么作用？', '继续聊聊天空', '把恐龙的故事讲给我听']
    try:
        for n, question in enumerate(questions):
            await session._on_utterance(b'', '', 100 + n, transcript=question)
            assert session.mode == 'chat', question
        assert len(model.calls) == len(questions)
        assert '小明' in model.calls[-1][1] and '恐龙' in model.calls[-1][1]
        assert '故事什么时候结束' in model.calls[-1][1]
        assert len(session._history) == 10
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_chat_retains_explicit_commands_and_focus_control(tmp_path):
    session, model, _ = make_session(tmp_path)
    session._start_arith = AsyncMock()
    session._show_tasks = AsyncMock()
    session._focus_control = AsyncMock()
    try:
        await session._on_utterance(b'', '', 1, transcript='开始口算')
        session._start_arith.assert_awaited_once()
        await session._on_utterance(b'', '', 2, transcript='今天有什么作业')
        session._show_tasks.assert_awaited_once()
        session.mode = 'chat'
        session._focus = object()
        await session._on_utterance(b'', '', 3, transcript='还剩多少时间')
        session._focus_control.assert_awaited_once()
        assert not model.calls
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_child_switch_cancels_old_model_before_new_identity(tmp_path):
    session, _, _ = make_session(tmp_path)
    session.registry.on_nfc = lambda *_: {'kind': 'child', 'child_id': 'kid-b'}
    cancelled = asyncio.Event()
    async def old_turn():
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    session._turn_task = asyncio.create_task(old_turn())
    await asyncio.sleep(0)
    session._history = ['孩子：私人内容', '助手：私人回复']
    session._skill_lock = 'english-speaking-01'
    try:
        await session._on_nfc({'uid': 'child-b'}, 5)
        assert cancelled.is_set()
        assert session.child_id == 'kid-b' and session._history == [] and session._skill_lock is None
    finally:
        await session.close()
