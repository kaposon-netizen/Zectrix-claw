import asyncio
import json
import pytest
from unittest.mock import AsyncMock
from pocketclaw import protocol
from pocketclaw.bridge import PocketClawSession
from pocketclaw.device_registry import DeviceRegistry
from pocketclaw.notes import NotesStore
from pocketclaw.voice import OfflineSTT, OfflineTTS
from runtime import StandaloneRuntime


class LLM:
    def __init__(self): self.prompts = []
    async def complete(self, system, prompt, **kwargs):
        self.prompts.append(prompt)
        return '好的，我们一起想一想。'


def make_session(tmp_path, stt=None, tts=None):
    frames=[]
    async def send_json(frame): frames.append(json.loads(frame))
    async def send_bin(data): pass
    llm=LLM()
    session=PocketClawSession(StandaloneRuntime(llm), send_json=send_json, send_bin=send_bin,
        stt=stt or OfflineSTT(), tts=tts or OfflineTTS(), registry=DeviceRegistry(path=str(tmp_path/'devices.json')),
        notes=NotesStore(path=str(tmp_path/'notes.json')))
    session.helloed=True
    return session, frames, llm


async def voice_turn(session, text):
    await session.on_control({'kind':protocol.PTT_START, 'hint_text':text})
    await session.on_binary(bytes(3200))
    await session.on_control({'kind':protocol.PTT_STOP})
    task=session._turn_task
    if task: await asyncio.wait_for(task, 2)


@pytest.mark.asyncio
async def test_voice_menu_then_multiple_chat_turns_do_not_cancel_themselves(tmp_path):
    session, frames, llm = make_session(tmp_path)
    session.mode='chat'
    try:
        await voice_turn(session, '打开菜单')
        assert session.mode == 'menu'
        assert any(f.get('kind') == 'display' and f['page']['mode'] == 'menu' for f in frames)
        await voice_turn(session, '打开学伴')
        assert session.mode == 'chat'
        await voice_turn(session, '你好呀')
        await voice_turn(session, '天空为什么是蓝色')
        assert len(llm.prompts) == 2
        assert '你好呀' in llm.prompts[-1] and '天空为什么是蓝色' in llm.prompts[-1]
        assert not any(f.get('kind') == 'error' and f.get('code') == 'turn_failed' for f in frames)
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_stream_start_error_falls_back_to_batch_recognition(tmp_path):
    class FailingStreamSTT:
        supports_streaming = True
        def __init__(self): self.transcribe = AsyncMock(return_value='你好呀')
        def start_stream(self, callback): raise RuntimeError('network unavailable')
    stt=FailingStreamSTT()
    session, frames, llm = make_session(tmp_path, stt=stt)
    session.mode='chat'
    try:
        await voice_turn(session, '')
        stt.transcribe.assert_awaited_once()
        assert len(llm.prompts)==1 and '你好呀' in llm.prompts[0]
        assert session._stt_stream is None
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_mode_switch_inside_turn_stays_cancellable_until_complete(tmp_path):
    session, frames, _ = make_session(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    async def long_menu(*args, **kwargs):
        entered.set()
        await release.wait()
    session._show_menu = long_menu
    async def utterance(*args, **kwargs):
        await session._abort_speech(reason='internal_mode_change')
        await long_menu()
    session._on_utterance = utterance
    try:
        await session.on_control({'kind':protocol.PTT_START, 'hint_text':'菜单'})
        await session.on_control({'kind':protocol.PTT_STOP})
        await asyncio.wait_for(entered.wait(),1)
        old=session._turn_task
        assert old is not None and not old.done()
        await session.on_control({'kind':protocol.PTT_START})
        assert old.cancelled() and session._recording
    finally:
        release.set()
        await session.close()

@pytest.mark.asyncio
async def test_new_voice_interrupts_generation_then_next_turn_completes(tmp_path):
    started, cancelled = asyncio.Event(), asyncio.Event()
    class InterruptibleLLM:
        def __init__(self): self.prompts=[]
        async def complete(self, system, prompt, **kwargs):
            self.prompts.append(prompt)
            if len(self.prompts)==1:
                started.set()
                try: await asyncio.Event().wait()
                finally: cancelled.set()
            return '明白了。'
    session, frames, _ = make_session(tmp_path)
    llm=InterruptibleLLM(); session.runtime=StandaloneRuntime(llm); session.mode='chat'
    try:
        await session.on_control({'kind':protocol.PTT_START,'hint_text':'你好呀'})
        await session.on_binary(bytes(3200))
        await session.on_control({'kind':protocol.PTT_STOP})
        await asyncio.wait_for(started.wait(), 1)
        old=session._turn_task
        await session.on_control({'kind':protocol.PTT_START, 'hint_text':'天空为什么是蓝色'})
        assert cancelled.is_set() and old.cancelled()
        await session.on_binary(bytes(3200))
        await session.on_control({'kind':protocol.PTT_STOP})
        if session._turn_task: await asyncio.wait_for(session._turn_task,2)
        assert len(llm.prompts)==2 and '天空为什么是蓝色' in llm.prompts[-1]
        assert any(frame['kind']=='tts_stop' for frame in frames)
        assert not any(frame.get('kind')=='error' and frame.get('code')=='turn_failed' for frame in frames)
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_stream_late_failure_uses_full_audio_for_batch_fallback(tmp_path):
    class Stream:
        def __init__(self): self.cancelled=False; self.fed=[]
        def feed(self, data): self.fed.append(data)
        async def finish(self): raise RuntimeError('provider disconnected')
        async def cancel(self): self.cancelled=True
    class STT:
        supports_streaming=True
        def __init__(self): self.stream=Stream();self.transcribe=AsyncMock(return_value='你好呀')
        def start_stream(self,callback): return self.stream
    stt=STT();session,_,llm=make_session(tmp_path, stt=stt);session.mode='chat'
    try:
        await voice_turn(session,'')
        stt.transcribe.assert_awaited_once()
        assert stt.transcribe.await_args.args[0] == bytes(3200)
        assert stt.stream.fed == [bytes(3200)] and stt.stream.cancelled
        assert len(llm.prompts)==1 and '你好呀' in llm.prompts[0]
    finally:
        await session.close()

@pytest.mark.asyncio
async def test_native_tts_reports_provider_failure_without_exposing_credentials():
    from pocketclaw.voice import NativeVolcTTS
    class Response:
        status_code = 503
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
    class Client:
        def stream(self, *args, **kwargs): return Response()
    tts=NativeVolcTTS('app', 'test-private-token', 'voice')
    tts._get_client=lambda: Client()
    with pytest.raises(RuntimeError, match='语音合成暂时不可用') as error:
        await tts.synthesize('你好')
    assert 'test-private-token' not in str(error.value)

@pytest.mark.asyncio
async def test_speech_failure_reports_and_ends_started_stream(tmp_path):
    class BrokenTTS:
        sample_rate=16000
        async def synthesize_stream(self, text):
            yield bytes(1920)
            raise RuntimeError('provider failed')
    session, frames, _ = make_session(tmp_path, tts=BrokenTTS())
    try:
        await session._speak('测试', wait=True)
        kinds=[f['kind'] for f in frames]
        assert kinds == ['tts_start', 'error', 'tts_end']
        assert frames[1]['code']=='tts_unavailable'
    finally:
        await session.close()

@pytest.mark.asyncio
async def test_chat_idle_timeout_completes_task_screen_transition(tmp_path):
    session, frames, _ = make_session(tmp_path)
    session.mode='chat'; session._clock=lambda:1000.0;session._chat_idle_deadline=999.0
    try:
        task=asyncio.create_task(session._chat_idle_watchdog());session._chat_idle_task=task
        await asyncio.wait_for(task, 2)
        assert session.mode=='task'
        assert any(frame.get('kind')=='display' and frame['page']['mode']=='task' for frame in frames)
        assert session._chat_idle_task is None
    finally:
        await session.close()
