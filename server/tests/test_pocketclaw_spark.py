"""便利贴 × 内容策略：早安「今日探索」推屏 + 语音「聊聊这个」/「错题」（pocketclaw/bridge.py）。

用假 daily_spark（cached_today / today 可控）与假 qbank；不碰模型、不碰固件。
"""
import asyncio
import json
import time

import pytest

from pocketclaw import bridge, display
from pocketclaw.bridge import PocketClawSession
from pocketclaw.device_registry import DeviceRegistry
from pocketclaw.notes import NotesStore
from pocketclaw.voice import OfflineSTT, OfflineTTS

NOW = time.mktime((2026, 9, 15, 8, 30, 0, 0, 0, -1))          # 早上 8:30（本地）
CARDS = [
    {"id": "daily-0", "title": "一只霸王龙能吃掉几分之几头三角龙？", "emoji": "🦖", "daily": True},
    {"id": "daily-1", "title": "秋天的叶子为什么先变黄？", "emoji": "🍂", "daily": True},
    {"id": "daily-2", "title": "library 少了哪个字母？", "emoji": "📚", "daily": True},
]


def run(c):
    return asyncio.get_event_loop().run_until_complete(c)


class Sink:
    def __init__(self):
        self.json, self.bin = [], []

    async def send_json(self, s):
        self.json.append(json.loads(s))

    async def send_bin(self, b):
        self.bin.append(b)

    def pages(self):
        return [m["page"] for m in self.json if m.get("kind") == "display"]

    def kinds(self):
        return [m.get("kind") for m in self.json]


class RecRuntime:
    """记下每次 turn 的入参（文本 + 关键字参数），回一句固定话。"""
    def __init__(self):
        self.calls = []

    async def turn(self, text, **kw):
        self.calls.append((text, kw))
        return {"kind": "chat_turn", "text": "你先猜猜看？", "banners": [], "blocks": [], "chips": []}


class FakeSpark:
    """cached / generated 分别控制「零成本缓存」与「现生成」两条路；today_calls 数现生成被调了几次。"""
    def __init__(self, cached=None, generated=None):
        self.cached = list(cached or [])
        self.generated = list(generated or [])
        self.today_calls = 0

    def cached_today(self, child_id, now=None):
        return list(self.cached)

    async def today(self, child_id, *, grade_band="", now=None, force=False):
        self.today_calls += 1
        return list(self.generated)


class Mk:
    def __init__(self, id, question, subject="数学", wrong_step="", cause="", due=0.0):
        self.id, self.question, self.subject = id, question, subject
        self.wrong_step, self.cause, self.due = wrong_step, cause, due


class FakeQbank:
    def __init__(self, due=None):
        self.due = list(due or [])

    def due_for_review(self, child_id, now):
        # 与 qbank.service.QuestionBank.due_for_review 同契约：只给到期的，按 due 升序
        return sorted([m for m in self.due if m.due <= now], key=lambda m: m.due)


def _session(sink, *, runtime=None, daily_spark=None, qbank=None):
    sess = PocketClawSession(runtime or RecRuntime(), send_json=sink.send_json, send_bin=sink.send_bin,
                             stt=OfflineSTT(), tts=OfflineTTS(),
                             registry=DeviceRegistry(path=":memory:"), notes=NotesStore(path=":memory:"),
                             daily_spark=daily_spark, qbank=qbank)
    return sess


def _hello(sess, child="kid"):
    run(sess.on_control({"kind": "hello", "device_id": "d1", "child_id": child}, now=NOW))
    if sess._spark_task is not None:          # 早安推屏跑在后台：测试里把它 drain 完再断言
        run(sess._spark_task)


@pytest.fixture(autouse=True)
def _reset_module_state():
    bridge._MORNING_PUSHED.clear()
    bridge._SPARK_CURSOR.clear()
    bridge._MISTAKE_CURSOR.clear()
    yield
    bridge._MORNING_PUSHED.clear()
    bridge._SPARK_CURSOR.clear()
    bridge._MISTAKE_CURSOR.clear()


# ── 版式 ───────────────────────────────────────────────────────
def test_render_spark_fits_one_screen_and_uses_only_slim_glyphs():
    page = display.render_spark(CARDS[0], due_n=3, date_label="9月15日")
    assert page["mode"] == "spark" and page["title"] == "今日探索 · 9月15日"
    assert "".join(page["body"]).startswith("一只霸王龙")
    assert any("错题本里有 3 道题" in ln for ln in page["body"])
    assert any("聊聊这个" in ln and "错题" in ln for ln in page["body"])
    # 固件每屏 8 行（标题 + 正文）：早安屏必须一屏放下，不出「第 1/2 页」脚注
    assert 1 + len(page["body"]) <= 8
    # 思源黑体 slim 子集没有这些字形（含 emoji / 「」『』✓）：题面以外的行流里一个都不能出现
    for ln in [page["title"]] + page["body"]:
        assert not any(ch in ln for ch in "■●━☆★▮✓「」『』"), ln
        assert all(ord(ch) <= 0xFFFF for ch in ln), ln
    assert page["emoji"] == "🦖" and page["due"] == 3          # emoji 只作数据带下去，不进行流


def test_render_spark_hides_due_line_when_zero():
    page = display.render_spark(CARDS[1], due_n=0)
    assert page["title"] == "今日探索"
    assert not any("错题" in ln for ln in page["body"])
    assert page["body"][-1] == "按住说“聊聊这个”"


# ── 早安推屏 ─────────────────────────────────────────────────
def test_hello_pushes_spark_once_per_day():
    sink = Sink()
    spark = FakeSpark(cached=CARDS)
    qb = FakeQbank([Mk("m1", "3x+2=11，求x", due=NOW - 1), Mk("m2", "9×7=?", due=NOW + 999)])
    sess = _session(sink, daily_spark=spark, qbank=qb)
    _hello(sess)
    assert sink.kinds()[0] == "hello_ack"                       # 握手回执永远先于早安屏
    pages = sink.pages()
    assert len(pages) == 1 and pages[0]["mode"] == "spark"
    assert "一只霸王龙" in "".join(pages[0]["body"])
    assert any("错题本里有 1 道题" in ln for ln in pages[0]["body"])   # 只数到期的那道
    assert spark.today_calls == 0                                # 有缓存就不现生成
    # 同一天再 hello（深睡唤醒 / 重连）→ 不重复推
    sink2 = Sink()
    sess2 = _session(sink2, daily_spark=spark, qbank=qb)
    _hello(sess2)
    assert sink2.kinds() == ["hello_ack"] and not sink2.pages()
    # 另一个孩子今天第一次连 → 各推各的
    sink3 = Sink()
    sess3 = _session(sink3, daily_spark=spark, qbank=qb)
    _hello(sess3, child="kidB")
    assert len(sink3.pages()) == 1


def test_hello_generates_when_cache_empty_but_does_not_block_ack():
    sink = Sink()
    spark = FakeSpark(cached=[], generated=CARDS[1:])
    sess = _session(sink, daily_spark=spark, qbank=None)
    _hello(sess)
    assert spark.today_calls == 1
    page = sink.pages()[0]
    assert "秋天的叶子" in "".join(page["body"])
    assert not any("错题" in ln for ln in page["body"])         # qbank=None → 不显示到期错题行
    assert sink.kinds()[0] == "hello_ack"


def test_hello_without_cards_pushes_nothing_and_keeps_next_chance():
    sink = Sink()
    spark = FakeSpark(cached=[], generated=[])
    sess = _session(sink, daily_spark=spark)
    _hello(sess)
    assert sink.kinds() == ["hello_ack"]
    assert "kid" not in bridge._MORNING_PUSHED                   # 没推出去不记账，下次 hello 还会试


def test_hello_spark_failure_is_swallowed():
    class Boom(FakeSpark):
        def cached_today(self, child_id, now=None):
            raise RuntimeError("disk")

    sink = Sink()
    sess = _session(sink, daily_spark=Boom())
    _hello(sess)
    assert sink.kinds() == ["hello_ack"]


# ── 语音：聊聊这个 ───────────────────────────────────────────
def test_spark_utterance_hands_question_to_runtime_and_rotates():
    sink = Sink()
    rt = RecRuntime()
    sess = _session(sink, runtime=rt, daily_spark=FakeSpark(cached=CARDS))
    _hello(sess)
    for said, expect in (("聊聊这个", CARDS[0]["title"]), ("今天的问题", CARDS[1]["title"]),
                         ("有什么好玩的问题", CARDS[2]["title"]), ("今日探索", CARDS[0]["title"])):
        run(sess.on_control({"kind": "text", "text": said}, now=NOW + 60))
        assert rt.calls[-1][0] == expect                          # 题面原样当孩子的话
    assert "探索问题" in rt.calls[-1][1]["guidance"]
    page = sink.pages()[-1]
    assert page["mode"] == "chat" and page["heard"][0].startswith("今日探索：")
    assert "tts_start" in sink.kinds()


def test_spark_utterance_without_cards_falls_back_to_chat():
    sink = Sink()
    rt = RecRuntime()
    sess = _session(sink, runtime=rt, daily_spark=FakeSpark())
    _hello(sess)
    run(sess.on_control({"kind": "text", "text": "聊聊这个"}, now=NOW))
    assert rt.calls[-1][0] == "聊聊这个"                          # 主脑自带 spark_today 工具，交给它


# ── 语音：错题 ───────────────────────────────────────────────
def test_mistake_utterance_shows_question_and_asks_socratic():
    sink = Sink()
    rt = RecRuntime()
    qb = FakeQbank([Mk("m1", "3x+2=11，求x", wrong_step="把 2 移到右边忘了变号", cause="粗心", due=NOW - 1),
                    Mk("m2", "小明有 12 个苹果，分给 3 个朋友，每人几个？", subject="数学", due=NOW - 2)])
    sess = _session(sink, runtime=rt, daily_spark=None, qbank=qb)
    _hello(sess)
    run(sess.on_control({"kind": "text", "text": "考考我错题"}, now=NOW))
    pages = sink.pages()
    # 先推题面屏（due 最早的那道排第一），再推学伴回复屏（顶部仍带题面）
    assert pages[0]["title"].startswith("复习错题 · 数学") and "12 个苹果" in "".join(pages[0]["body"])
    assert pages[-1]["heard"][0].startswith("错题：")
    text, kw = rt.calls[-1]
    assert "12 个苹果" in text
    assert "苏格拉底" in kw["guidance"] and "不直接给答案" in kw["guidance"]
    # 第二次 → 轮到下一道，且把上次错的那一步喂给学伴
    run(sess.on_control({"kind": "text", "text": "复习错题"}, now=NOW))
    assert "3x+2=11" in "".join(sink.pages()[-2]["body"])
    assert "忘了变号" in rt.calls[-1][1]["guidance"] and "粗心" in rt.calls[-1][1]["guidance"]


def test_mistake_utterance_with_nothing_due_says_so():
    sink = Sink()
    rt = RecRuntime()
    sess = _session(sink, runtime=rt, qbank=FakeQbank([Mk("m1", "9×7=?", due=NOW + 999)]))
    _hello(sess)
    run(sess.on_control({"kind": "text", "text": "错题"}, now=NOW))
    assert "没有到期的错题" in "".join(sink.pages()[-1]["body"])
    assert not rt.calls                                          # 没题就不跑一轮


# ── 未启用：全部静默 ─────────────────────────────────────────
def test_disabled_when_stores_are_none():
    sink = Sink()
    rt = RecRuntime()
    sess = _session(sink, runtime=rt, daily_spark=None, qbank=None)
    _hello(sess)
    assert sess._spark_task is None and sink.kinds() == ["hello_ack"]
    run(sess.on_control({"kind": "text", "text": "聊聊这个"}, now=NOW))
    run(sess.on_control({"kind": "text", "text": "错题"}, now=NOW))
    assert [c[0] for c in rt.calls] == ["聊聊这个", "错题"]       # 原话走普通对话，不加前缀、不改叮嘱
    assert all("苏格拉底" not in c[1]["guidance"] for c in rt.calls)
    assert all(p["heard"][0].startswith("你说：") for p in sink.pages())
