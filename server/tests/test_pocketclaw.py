"""PocketClaw 墨水屏便利贴接入：协议 / 显示 / 语音 / 记事 / 单词 / 设备 / 桥接 端到端。"""
import asyncio
import json
import pathlib
import time

from agent.llm import FakeLLM
from agent.runtime import Runtime
from skill.registry import SkillRegistry
from guardrail.whitelist import render_whitelist
from parent.controls import ParentControls

from pocketclaw import protocol, display
from pocketclaw.voice import OfflineSTT, OfflineTTS
from pocketclaw.notes import NotesStore
from pocketclaw.device_registry import DeviceRegistry
from pocketclaw.bridge import PocketClawSession, quiet_reason


def run(c):
    return asyncio.get_event_loop().run_until_complete(c)


def _registry():
    reg = SkillRegistry()
    reg.load_dir(str(pathlib.Path(__file__).parent.parent / "skill" / "skills"))
    return reg


class Sink:
    """收集会话出站帧（传输替身）。"""
    def __init__(self):
        self.json = []      # list[dict]
        self.bin = []       # list[bytes]

    async def send_json(self, s):
        self.json.append(json.loads(s))

    async def send_bin(self, b):
        self.bin.append(b)

    def kinds(self):
        return [m.get("kind") for m in self.json]

    def first(self, kind):
        return next((m for m in self.json if m.get("kind") == kind), None)


def _session(runtime, sink, *, controls=None, notes=None, registry=None):
    return PocketClawSession(
        runtime, send_json=sink.send_json, send_bin=sink.send_bin,
        stt=OfflineSTT(), tts=OfflineTTS(),
        registry=registry or DeviceRegistry(path=":memory:"),
        notes=notes or NotesStore(path=":memory:"),
        controls=controls)


# ── 协议 ────────────────────────────────────────────────
def test_protocol_parse_and_mac_norm():
    assert protocol.parse_control("not json") is None
    assert protocol.parse_control('{"no":"kind"}') is None
    assert protocol.parse_control('{"kind":"hello"}')["kind"] == "hello"
    h = protocol.Hello.parse({"mac": "441bf6c4d8b0", "device_id": "d1", "battery": "77"})
    assert h.mac == "44:1B:F6:C4:D8:B0" and h.battery == 77


def test_protocol_builders_and_audio():
    assert json.loads(protocol.stt_result("你好"))["text"] == "你好"
    assert json.loads(protocol.hello_ack("s", "kid"))["display"]["w"] == 400
    # 60ms @16k mono PCM16 = 1920 bytes/帧
    pcm = b"\x00\x00" * 16000            # 1s
    assert abs(protocol.pcm_duration_s(pcm) - 1.0) < 1e-6
    frames = list(protocol.frame_pcm(pcm, frame_ms=60))
    assert all(len(f) % 2 == 0 for f in frames)
    assert sum(len(f) for f in frames) == len(pcm)


# ── 显示（黑白 400×300 折行）────────────────────────────
def test_display_wrap_cjk_width_aware():
    lines = display.wrap_cjk("一二三四五六七八九十", cols=8)   # 8 半角列 = 4 汉字/行
    assert lines[0] == "一二三四"
    assert display.wrap_cjk("abcdefghij", cols=4) == ["abcd", "efgh", "ij"]


def test_display_render_turn_truncates_and_keeps_heard():
    turn = {"text": "步骤一\n" * 30, "banners": [{"kind": "invoke", "text": "已拦下代写"}],
            "chips": ["继续", "换一题", "看例子", "多余"]}
    page = display.render_turn(turn, heard="这题怎么做", battery=66)
    assert page["heard"] == ["你说：这题怎么做"]     # render_turn 给 heard 加「你说：」前缀（display.py:103）
    assert page["banner"] == "已拦下代写"
    # 现在整段下发、不裁剪（设备端按屏高翻页，display.py:78 起）：正文保留全部行、不置 truncated
    assert page["body"].count("步骤一") == 30 and page["truncated"] is False
    assert len(page["chips"]) == 3 and page["footer"]["battery"] == 66


# ── 语音离线兜底 ────────────────────────────────────────
def test_offline_stt_hint_bypass_and_empty():
    # transcribe 已是 async（voice.py:41，主链路 bridge.py:217 用 await）→ 测试也 await
    assert run(OfflineSTT().transcribe(b"", hint="今天学了什么")) == "今天学了什么"
    assert run(OfflineSTT().transcribe(b"")) == ""
    assert "未配置" in run(OfflineSTT().transcribe(b"\x00" * 8000))   # 0.25s 无 hint


def test_offline_tts_produces_pcm16():
    pcm = run(OfflineTTS().synthesize("你好世界"))   # synthesize 已 async（voice.py:58，bridge.py:477 await）
    assert len(pcm) > 0 and len(pcm) % 2 == 0


# ── 记想法 + 单词卡 ─────────────────────────────────────
def test_notes_add_and_list(tmp_path):
    st = NotesStore(path=str(tmp_path / "n.json"))
    st.add_note("kid", "我想养一只小龙虾", now=1.0)
    st.add_note("kid", "明天问老师光合作用", now=2.0)
    rows = st.list_notes("kid")
    assert rows[0].text == "明天问老师光合作用"        # 新→旧
    assert st.aggregate("kid")["notes"] == 2


def test_words_leitner_schedule(tmp_path):
    st = NotesStore(path=str(tmp_path / "w.json"))
    c = st.add_word("kid", "apple", meaning="苹果", now=0.0)
    assert st.next_word("kid", now=0.0).word == "apple"
    st.review_word("kid", c.id, remembered=True, now=0.0)      # 升到 1 盒 → 8h 后
    assert st.next_word("kid", now=0.0) is None                # 未到期
    assert st.next_word("kid", now=9 * 3600).word == "apple"   # 到期回归
    updated = st.review_word("kid", c.id, remembered=False, now=9 * 3600)
    assert updated.box == 0                                    # 忘了 → 回 0 盒


# ── 设备绑定 / NFC / 推送 ───────────────────────────────
def test_registry_bind_and_by_mac(tmp_path):
    reg = DeviceRegistry(path=str(tmp_path / "d.json"))
    reg.upsert("d1", mac="44:1B:F6:C4:D8:B0", child_id="kidA")
    assert reg.by_mac("44:1b:f6:c4:d8:b0").child_id == "kidA"
    reg.bind_child("d1", "kidB")
    assert reg.get("d1").child_id == "kidB"


def test_registry_nfc_switch_child(tmp_path):
    reg = DeviceRegistry(path=str(tmp_path / "d.json"))
    reg.upsert("d1", child_id="kidA")
    act = reg.on_nfc("d1", uid="04AABB", ndef="child:kidB")
    assert act["kind"] == "child" and act["child_id"] == "kidB"
    assert reg.get("d1").child_id == "kidB"
    # 无 NDEF → 用登记过的卡映射
    reg.register_card("04CCDD", "mode:word")
    assert reg.on_nfc("d1", uid="04CCDD")["value"] == "word"


def test_registry_push_and_drain(tmp_path):
    reg = DeviceRegistry(path=str(tmp_path / "d.json"))
    reg.push_content("d1", {"body": ["妈妈爱你"]})
    assert reg.pending_count("d1") == 1
    assert len(reg.drain_content("d1")) == 1 and reg.pending_count("d1") == 0


# ── 桥接：AI 对话端到端（hello→ptt→stt→脑→上屏→tts）──
def test_bridge_voice_turn_end_to_end(tmp_path):
    llm = FakeLLM(responses=["先把题读给我听，它已知什么？"])
    rt = Runtime(_registry(), llm, default_skill_id="math-tutor-01")
    sink = Sink()
    sess = _session(rt, sink, registry=DeviceRegistry(path=str(tmp_path / "d.json")),
                    notes=NotesStore(path=str(tmp_path / "n.json")))
    run(sess.on_control({"kind": "hello", "device_id": "d1", "mac": "44:1B:F6:C4:D8:B0"}))
    assert sink.first("hello_ack")["child_id"] == "kid"
    # 一次按住说话（debug hint 携带识别文本），中间灌两帧 PCM
    run(sess.on_control({"kind": "ptt_start", "text": "这道解方程不会"}))
    run(sess.on_binary(b"\x00\x00" * 1600))
    run(sess.on_control({"kind": "ptt_stop"}))
    # ptt_stop 把「STT→脑→TTS」这一轮丢进后台 asyncio 任务（bridge.py:153，为了在播报中
    # 仍能读到下一次打断的 ptt_start），on_control 不 await 它 → 测试须把这个后台轮 drain 完
    if sess._turn_task is not None:
        run(sess._turn_task)
    ks = sink.kinds()
    assert "stt" in ks and "display" in ks
    assert ks.count("tts_start") == 1 and ks.count("tts_end") == 1
    assert len(sink.bin) >= 1                             # 有 TTS 音频帧
    assert sink.first("stt")["text"] == "这道解方程不会"
    assert sink.first("display")["page"]["body"]         # 正文非空


def test_bridge_capability_gate_is_epaper(tmp_path):
    """便利贴走 epaper_4in2 画像：allowed_types 不含彩色可视化（chartjs/mermaid）。"""
    seen = {}

    class RecRuntime:
        async def turn(self, text, **kw):
            seen.update(kw)
            return {"kind": "chat_turn", "text": "好的", "banners": [], "blocks": [], "chips": []}

    sink = Sink()
    sess = _session(RecRuntime(), sink)
    run(sess.on_control({"kind": "hello", "device_id": "d1"}))
    run(sess.on_control({"kind": "text", "text": "讲讲光合作用"}))
    assert seen["client_profile"] == "epaper_4in2"
    assert "chartjs" not in seen["allowed_types"] and "mermaid" not in seen["allowed_types"]


def test_bridge_note_capture(tmp_path):
    notes = NotesStore(path=str(tmp_path / "n.json"))
    sink = Sink()
    sess = _session(_stub_rt(), sink, notes=notes)
    run(sess.on_control({"kind": "hello", "device_id": "d1"}))
    run(sess.on_control({"kind": "text", "text": "记一下 我想做一个太空站"}))
    assert notes.note_count("kid") == 1
    assert notes.list_notes("kid")[0].text == "我想做一个太空站"
    assert sink.first("display")["page"]["mode"] == "note"


def test_bridge_word_flow(tmp_path):
    notes = NotesStore(path=str(tmp_path / "n.json"))
    sink = Sink()
    sess = _session(_stub_rt(), sink, notes=notes)
    notes._bucket("kid")["_seeded"] = True   # 隔离：跳过 hello 的预设播种（bridge.py:185），单测只看本用例加的词
    run(sess.on_control({"kind": "hello", "device_id": "d1"}))
    run(sess.on_control({"kind": "text", "text": "加单词 apple 苹果"}))
    assert notes.word_count("kid") == 1
    run(sess.on_control({"kind": "text", "text": "背单词"}))
    word_pages = [m for m in sink.json if m.get("kind") == "display" and m["page"].get("mode") == "word"]
    assert any("apple" in "".join(p["page"]["body"]) for p in word_pages)


def test_bridge_nfc_switches_child(tmp_path):
    reg = DeviceRegistry(path=str(tmp_path / "d.json"))
    sink = Sink()
    sess = _session(_stub_rt(), sink, registry=reg)
    run(sess.on_control({"kind": "hello", "device_id": "d1", "child_id": "kidA"}))
    run(sess.on_control({"kind": "nfc", "uid": "04AABB", "ndef": "child:kidB"}))
    assert sess.child_id == "kidB"


def test_bridge_night_block(tmp_path):
    # quiet_reason 纯函数：夜间锁只在家长「开了时间控制」时生效（bridge.py:98，与 app.py 服务端
    # 权威门一致——家长没开时间控制就一律不拦；这是有意的口径对齐，不是安全闸失效）
    assert quiet_reason({"time_control": True, "night_block": True}, 23)
    assert not quiet_reason({"time_control": True, "night_block": True}, 10)
    assert not quiet_reason({"night_block": True}, 23)         # 没开时间控制 → 不拦
    # 会话级：家长开了时间控制，23:00 本地 → 给休息屏，不产出 tts
    controls = ParentControls(path=str(tmp_path / "c.json"))
    controls.update("kid", {"time_control": True})
    sink = Sink()
    sess = _session(_stub_rt(), sink, controls=controls)
    run(sess.on_control({"kind": "hello", "device_id": "d1"}))
    night = time.mktime((2026, 1, 1, 23, 0, 0, 0, 0, -1))
    run(sess.on_control({"kind": "text", "text": "陪我聊会儿"}, now=night))
    assert "tts_start" not in sink.kinds()
    assert "休息" in (sink.first("display")["page"]["title"] or "")


def test_whitelist_epaper_gate():
    wl = render_whitelist("g3_g6", "epaper_4in2")
    assert "chartjs" not in wl and "mermaid" not in wl
    assert wl <= {"card", "svg"}


def _stub_rt(llm=None):
    class RT:
        def __init__(self, llm):
            self.llm = llm
        async def turn(self, text, **kw):
            return {"kind": "chat_turn", "text": "好的，我在。", "banners": [], "blocks": [], "chips": []}
    return RT(llm)


# ── #1 语音单词卡：查词 → 出卡 + 存进复习盒 ──
def test_bridge_word_lookup_makes_and_saves_card(tmp_path):
    # LLM 返回严格 JSON → 解析成卡
    llm = FakeLLM(responses=['{"word":"apple","reading":"ˈæpəl","meaning":"苹果","example":"I eat an apple."}'])
    notes = NotesStore(path=str(tmp_path / "n.json"))
    sink = Sink()
    sess = _session(_stub_rt(llm), sink, notes=notes)
    notes._bucket("kid")["_seeded"] = True   # 隔离预设播种（bridge.py:185）→ word_count 只反映本次查词
    run(sess.on_control({"kind": "hello", "device_id": "d1"}))
    run(sess.on_control({"kind": "text", "text": "apple 什么意思"}))
    page = [m for m in sink.json if m.get("kind") == "display"][-1]["page"]
    assert page["mode"] == "word" and "apple" in "".join(page["body"])
    assert notes.find_word("kid", "apple").meaning == "苹果"       # 已入复习盒
    assert notes.word_count("kid") == 1


def test_word_lookup_offline_degrades(tmp_path):
    # LLM 给不出 JSON（离线兜底）→ 仍出占位卡、仍可保存
    llm = FakeLLM(responses=["我在呢。"])
    notes = NotesStore(path=str(tmp_path / "n.json"))
    sink = Sink()
    sess = _session(_stub_rt(llm), sink, notes=notes)
    run(sess.on_control({"kind": "hello", "device_id": "d1"}))
    run(sess.on_control({"kind": "text", "text": "查单词 photosynthesis"}))
    assert notes.find_word("kid", "photosynthesis") is not None


def test_add_word_dedup(tmp_path):
    st = NotesStore(path=str(tmp_path / "n.json"))
    st.add_word("kid", "apple", now=0.0)
    st.add_word("kid", "Apple", meaning="苹果", now=0.0)   # 大小写同词 → 合并
    assert st.word_count("kid") == 1
    assert st.find_word("kid", "apple").meaning == "苹果"


# ── #11 作业清单 ──
def test_tasks_store_add_complete(tmp_path):
    st = NotesStore(path=str(tmp_path / "n.json"))
    st.add_task("kid", "背10个单词", now=1.0)
    st.add_task("kid", "口算20题", now=2.0)
    assert st.task_stats("kid") == {"total": 2, "done": 0, "todo": 2}
    done = st.complete_task("kid", text="口算", now=3.0)
    assert done.text == "口算20题" and done.done is True
    assert st.task_stats("kid")["todo"] == 1


def test_bridge_checklist_flow(tmp_path):
    notes = NotesStore(path=str(tmp_path / "n.json"))
    sink = Sink()
    sess = _session(_stub_rt(), sink, notes=notes)
    notes._bucket("kid")["_seeded"] = True   # 隔离预设作业播种（bridge.py:185）→ progress 只数本用例加的两项
    run(sess.on_control({"kind": "hello", "device_id": "d1"}))
    run(sess.on_control({"kind": "text", "text": "加任务 背10个单词"}))
    run(sess.on_control({"kind": "text", "text": "加任务 口算20题"}))
    run(sess.on_control({"kind": "text", "text": "作业清单"}))
    page = [m for m in sink.json if m.get("kind") == "display"][-1]["page"]
    assert page["mode"] == "task" and page["progress"]["total"] == 2
    assert any("·" in ln for ln in page["body"])
    run(sess.on_control({"kind": "text", "text": "完成 背10个单词"}))
    assert notes.task_stats("kid")["done"] == 1


def test_bridge_all_tasks_done_celebrates(tmp_path):
    notes = NotesStore(path=str(tmp_path / "n.json"))
    notes.add_task("kid", "唯一任务", now=1.0)
    notes._bucket("kid")["_seeded"] = True   # 隔离预设作业播种（bridge.py:185）→「全部完成」才成立（只剩这一项）
    sink = Sink()
    sess = _session(_stub_rt(), sink, notes=notes)
    run(sess.on_control({"kind": "hello", "device_id": "d1"}))
    run(sess.on_control({"kind": "text", "text": "完成 唯一任务"}))
    page = [m for m in sink.json if m.get("kind") == "display"][-1]["page"]
    assert page["footer"].get("all_done") is True
    assert "tts_start" in sink.kinds()          # 报喜有语音


def test_button_todo_shows_checklist(tmp_path):
    notes = NotesStore(path=str(tmp_path / "n.json"))
    notes.add_task("kid", "写日记", now=1.0)
    sink = Sink()
    sess = _session(_stub_rt(), sink, notes=notes)
    run(sess.on_control({"kind": "hello", "device_id": "d1"}))
    run(sess.on_control({"kind": "button", "name": "todo"}))
    assert [m for m in sink.json if m.get("kind") == "display"][-1]["page"]["mode"] == "task"


# ── #18/#19 NFC 学科卡 / 单词收集卡 ──
def test_nfc_subject_card_sets_context(tmp_path):
    sink = Sink()
    sess = _session(_stub_rt(), sink)
    run(sess.on_control({"kind": "hello", "device_id": "d1"}))
    run(sess.on_control({"kind": "nfc", "uid": "u1", "ndef": "subject:数学"}))
    assert sess.subject == "数学"


def test_nfc_word_card_looks_up(tmp_path):
    llm = FakeLLM(responses=['{"word":"tiger","reading":"ˈtaɪɡər","meaning":"老虎","example":"A tiger runs."}'])
    notes = NotesStore(path=str(tmp_path / "n.json"))
    sink = Sink()
    sess = _session(_stub_rt(llm), sink, notes=notes)
    run(sess.on_control({"kind": "hello", "device_id": "d1"}))
    run(sess.on_control({"kind": "nfc", "uid": "u2", "ndef": "word:tiger"}))
    assert notes.find_word("kid", "tiger").meaning == "老虎"


def test_card_target_parses_all_kinds():
    from pocketclaw.device_registry import _card_target
    assert _card_target("subject:语文") == {"kind": "subject", "value": "语文"}
    assert _card_target("word:apple") == {"kind": "word", "value": "apple"}
    assert _card_target("mode:task") == {"kind": "mode", "value": "task"}


# ── 记忆持久化：重启不丢（换一个新实例仍读得到）──
def test_memory_persists_across_restart(tmp_path):
    from memory.service import MemoryService
    p = str(tmp_path / "mem.json")
    m1 = MemoryService(path=p)
    m1.apply_delta("kid", {"summary": "今天学了光合作用", "profile": {"grade": 4}})
    m2 = MemoryService(path=p)                       # 模拟重启：新实例读同一文件
    assert "光合作用" in m2.summary_text("kid")
    assert m2.get_profile("kid").get("grade") == 4
