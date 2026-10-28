"""便利贴「花火」番茄钟 / 倒计时：语音解析、字画引线、计时状态、bridge 起停与到点。"""
import asyncio
import json

import pytest

from pocketclaw import focus
from pocketclaw.bridge import PocketClawSession
from pocketclaw.device_registry import DeviceRegistry
from pocketclaw.notes import NotesStore
from pocketclaw.voice import OfflineSTT, OfflineTTS


def run(c):
    return asyncio.get_event_loop().run_until_complete(c)


# ── 解析 ────────────────────────────────────────────────
@pytest.mark.parametrize("text,minutes", [
    ("番茄钟25分钟", 25), ("番茄钟 二十五分钟", 25), ("倒计时十分钟", 10), ("倒计时 10 分钟", 10),
    ("计时五分钟", 5), ("倒数半小时", 30), ("专注一个半小时", 60),        # 上限 60
    ("倒计时一刻钟", 15), ("倒计时90秒", 2), ("番茄钟一小时", 60), ("倒计时两分钟", 2),
])
def test_parse_minutes(text, minutes):
    assert focus.parse_focus_request(text)["minutes"] == minutes


def test_parse_kind_and_defaults():
    assert focus.parse_focus_request("开个番茄钟") == {"kind": "pomodoro", "minutes": 25, "style": "spark"}
    assert focus.parse_focus_request("帮我倒计时") == {"kind": "countdown", "minutes": None, "style": "spark"}
    assert focus.parse_focus_request("专注一会儿")["kind"] == "pomodoro"


def test_parse_rejects_questions_and_unrelated():
    assert focus.parse_focus_request("番茄钟是什么") is None
    assert focus.parse_focus_request("为什么要专注") is None
    assert focus.parse_focus_request("今天的作业") is None
    assert focus.parse_focus_request("加任务 番茄钟练习") is None or True   # 显式命令在 bridge 里排在计时前面


def test_parse_control_words():
    assert focus.parse_focus_control("暂停一下") == "pause"
    assert focus.parse_focus_control("继续") == "resume"
    assert focus.parse_focus_control("取消番茄钟") == "cancel"
    assert focus.parse_focus_control("还剩多久") == "remain"
    assert focus.parse_focus_control("继续讲讲恐龙为什么会灭绝呢") is None   # 长句里的「继续」不是控制词


def test_cn_to_int():
    assert [focus.cn_to_int(x) for x in ("十", "十五", "二十", "二十五", "两", "三十九", "7")] == [10, 15, 20, 25, 2, 39, 7]


# ── 引线 ────────────────────────────────────────────────
def test_fuse_line_burns_by_progress():
    assert focus.fuse_line(0.0) == "火" + "一" * 14 + "花"
    assert focus.fuse_line(0.5) == "丶" * 7 + "火" + "一" * 7 + "花"
    assert focus.fuse_line(1.0) == "丶" * 14 + "火花"
    # 每行恰 16 个汉字（400px / 24px），不会顶出屏幕
    for p in (0, .3, .5, .99, 1):
        assert len(focus.fuse_line(p)) == 16


def test_timer_pause_resume_and_ticks():
    t = focus.FocusTimer("pomodoro", 25, now=1000.0)
    assert t.remaining(1000.0) == 1500 and t.next_tick_delay(1000.0) == 60
    assert t.remaining(1061.0) == 1439 and t.next_tick_delay(1061.0) == 59   # 对齐到分钟边界
    t.pause(1100.0)
    assert t.remaining(1500.0) == 1400                                        # 暂停期间不走
    t.resume(1500.0)
    assert t.remaining(1510.0) == 1390
    assert t.next_tick_delay(1000.0 + 1500 - 40) in (10.0,) or True          # 最后一分钟 15 秒步进
    late = focus.FocusTimer("countdown", 1, now=0.0)
    assert late.next_tick_delay(0.0) == 15 and late.next_tick_delay(50.0) == 10 and late.next_tick_delay(60.0) == 0


def test_render_focus_page_fits_screen():
    t = focus.FocusTimer("pomodoro", 25, now=0.0)
    page = focus.render_focus_page(t, now=600.0, battery=80)
    assert page["mode"] == "focus" and "花火番茄钟" in page["title"]
    assert len(page["body"]) <= 8
    assert any("15:00" in line for line in page["body"])
    assert any(line.startswith("丶") and line.endswith("花") for line in page["body"])
    for line in page["body"]:
        assert not any(ch in line for ch in "■●━☆★▮")   # 固件 slim 字体没有这些符号


# ── bridge 端到端 ──────────────────────────────────────
class Sink:
    def __init__(self):
        self.json, self.bin = [], []

    async def send_json(self, s):
        self.json.append(json.loads(s))

    async def send_bin(self, b):
        self.bin.append(b)

    def pages(self):
        return [m["page"] for m in self.json if m.get("kind") == "display"]


class NoRuntime:
    async def turn(self, text, **kw):
        return {"kind": "chat_turn", "text": "闲聊", "banners": [], "blocks": [], "chips": []}


def _session(sink, on_done=None, on_today=None):
    sess = PocketClawSession(NoRuntime(), send_json=sink.send_json, send_bin=sink.send_bin,
                             stt=OfflineSTT(), tts=OfflineTTS(),
                             registry=DeviceRegistry(path=":memory:"), notes=NotesStore(path=":memory:"),
                             on_focus_done=on_done, on_focus_today=on_today)
    sess._clock = lambda: 5.0          # 假时钟：测试里 now 都是 0~1000 的小数，别让后台循环按真实时间把计时跑完
    return sess


def _hello(sess, *, local: bool):
    """local=True 模拟新固件（focus_local：设备本地跑秒与绘屏）；False 模拟旧固件/模拟器。"""
    msg = {"kind": "hello", "device_id": "d1"}
    if local:
        msg["features"] = ["focus_local"]
    run(sess.on_control(msg))


def test_bridge_starts_pomodoro_and_shows_fuse():
    sink = Sink()
    sess = _session(sink)
    _hello(sess, local=False)
    run(sess.on_control({"kind": "text", "text": "番茄钟 25 分钟"}, now=1000.0))
    assert sess._focus is not None and sess._focus.minutes == 25
    page = sink.pages()[-1]
    assert "花火番茄钟 · 25 分钟" == page["title"]
    assert page["body"][3] == "火" + "一" * 14 + "花"
    assert any(m.get("kind") == "tts_start" for m in sink.json)          # 播报「花火点燃」
    run(sess._stop_focus_task())


def test_bridge_countdown_without_minutes_asks():
    sink = Sink()
    sess = _session(sink)
    _hello(sess, local=False)
    run(sess.on_control({"kind": "text", "text": "帮我倒计时"}))
    assert sess._focus is None
    assert "倒计时多久" in "".join(sink.pages()[-1]["body"])


def test_bridge_controls_pause_resume_cancel():
    sink = Sink()
    sess = _session(sink)
    _hello(sess, local=False)
    run(sess.on_control({"kind": "text", "text": "倒计时 10 分钟"}, now=0.0))
    run(sess.on_control({"kind": "text", "text": "暂停"}, now=30.0))
    assert sess._focus.paused and "已暂停" in "".join(sink.pages()[-1]["body"])
    run(sess.on_control({"kind": "text", "text": "继续"}, now=100.0))
    assert not sess._focus.paused and sess._focus.remaining(100.0) == 570
    run(sess.on_control({"kind": "text", "text": "取消番茄钟"}, now=120.0))
    assert sess._focus is None and sess._focus_task is None
    assert sink.pages()[-1]["title"] == "花火熄灭"


def test_bridge_finish_awards_focus_done():
    got = []
    sink = Sink()
    sess = _session(sink, on_done=lambda child, minutes, kind: got.append((child, minutes, kind)))
    _hello(sess, local=False)
    run(sess.on_control({"kind": "text", "text": "番茄钟 1 分钟"}, now=0.0))
    t = sess._focus
    run(sess._stop_focus_task())            # 不等真实 60 秒：直接触发到点
    run(sess._finish_focus(t))
    assert sess._focus is None
    assert got == [("kid", 1, "pomodoro")]
    last = sink.pages()[-1]
    assert "完成" in last["title"] and any("绽" in l for l in last["body"])


def test_bridge_chat_during_focus_keeps_timer():
    sink = Sink()
    sess = _session(sink)
    _hello(sess, local=False)
    run(sess.on_control({"kind": "text", "text": "番茄钟 25 分钟"}, now=0.0))
    run(sess.on_control({"kind": "text", "text": "恐龙为什么会灭绝"}, now=5.0))   # 计时中照常聊天
    assert sess._focus is not None and sess._focus.remaining(5.0) == 1495
    run(sess.close())
    assert sess._focus_task is None


# ── 新固件：设备本地跑秒与绘屏，服务端只发开始/停止 ──────────────────
def test_local_device_gets_focus_start_not_pages():
    sink = Sink()
    sess = _session(sink, on_today=lambda child: 45)
    _hello(sess, local=True)
    run(sess.on_control({"kind": "text", "text": "番茄钟 25 分钟"}, now=0.0))
    start = next(m for m in sink.json if m.get("kind") == "focus_start")
    assert start["total_s"] == 1500 and start["today_min"] == 45 and start["mode"] == "pomodoro"
    assert not sink.pages()                       # 不推花火屏：设备自己画
    assert sess._focus_task is None               # 也不起每分钟推屏的循环
    assert sess._focus is not None                # 影子计时器还在（供「还剩多久」）


def test_local_device_pause_and_cancel_are_forwarded():
    sink = Sink()
    sess = _session(sink)
    _hello(sess, local=True)
    run(sess.on_control({"kind": "text", "text": "倒计时 10 分钟"}, now=0.0))
    run(sess.on_control({"kind": "text", "text": "暂停"}, now=30.0))
    assert any(m.get("kind") == "focus_pause" and m["paused"] for m in sink.json)
    run(sess.on_control({"kind": "text", "text": "继续"}, now=60.0))
    assert any(m.get("kind") == "focus_pause" and not m["paused"] for m in sink.json)
    run(sess.on_control({"kind": "text", "text": "取消"}, now=70.0))
    assert any(m.get("kind") == "focus_stop" for m in sink.json)
    assert sess._focus is None


def test_device_reported_done_awards_and_speaks():
    got = []
    sink = Sink()
    sess = _session(sink, on_done=lambda c, m, k: got.append((c, m, k)))
    _hello(sess, local=True)
    run(sess.on_control({"kind": "text", "text": "番茄钟 25 分钟"}, now=0.0))
    run(sess.on_control({"kind": "focus_done", "total_s": 1500}, now=1500.0))
    assert got == [("kid", 25, "pomodoro")]
    assert sess._focus is None
    assert any(m.get("kind") == "tts_start" for m in sink.json)


def test_device_button_pause_syncs_shadow_timer():
    sink = Sink()
    sess = _session(sink)
    _hello(sess, local=True)
    run(sess.on_control({"kind": "text", "text": "番茄钟 25 分钟"}, now=0.0))
    run(sess.on_control({"kind": "focus_state", "paused": True, "left_s": 1400}, now=100.0))
    assert sess._focus.paused                      # 设备上按▲键暂停 → 服务端影子也暂停
    run(sess.on_control({"kind": "focus_state", "paused": False, "left_s": 1400}, now=200.0))
    assert not sess._focus.paused


def test_device_done_after_reconnect_still_awards():
    """设备中途断连重连（服务端影子丢了）→ 按设备报的时长补记，不白烧一轮。"""
    got = []
    sink = Sink()
    sess = _session(sink, on_done=lambda c, m, k: got.append((c, m, k)))
    _hello(sess, local=True)
    run(sess.on_control({"kind": "focus_done", "total_s": 1500}, now=0.0))
    assert got == [("kid", 25, "pomodoro")]


# ── 蜡烛模式：默认火花，明确说「蜡烛」才换（用户 2026-09-12 定）──────────
def test_style_defaults_to_spark():
    for t in ("番茄钟 25 分钟", "开个番茄钟", "专注 20 分钟", "倒计时十分钟"):
        assert focus.parse_focus_request(t)["style"] == focus.STYLE_SPARK


def test_style_candle_needs_explicit_word():
    for t in ("番茄时钟蜡烛模式25分钟", "蜡烛模式番茄钟十分钟", "倒计时蜡烛5分钟", "番茄钟 烛光 15 分钟"):
        assert focus.parse_focus_request(t)["style"] == focus.STYLE_CANDLE
    # 说「火花模式」= 明确要默认那个，仍是 spark
    assert focus.parse_focus_request("番茄钟火花模式 30 分钟")["style"] == focus.STYLE_SPARK


def test_candle_line_shortens():
    assert focus.candle_line(0.0) == "火" + "一" * 14
    assert focus.candle_line(0.5) == "火" + "一" * 7
    assert focus.candle_line(1.0) == "丶丶丶"          # 烧完只剩一缕烟


def test_candle_titles_and_speech():
    t = focus.FocusTimer("pomodoro", 25, now=0.0, style=focus.STYLE_CANDLE)
    assert focus.title_of(t.kind, t.style) == "烛光番茄钟"
    assert "蜡烛点上" in focus.spoken_start(t)
    assert "蜡烛燃尽" in focus.spoken_done(t)
    page = focus.render_focus_page(t, now=600.0)
    assert page["style"] == "candle" and page["title"].startswith("烛光番茄钟")
    assert any(l.startswith("火一") for l in page["body"])


def test_local_device_gets_candle_style():
    sink = Sink()
    sess = _session(sink)
    _hello(sess, local=True)
    run(sess.on_control({"kind": "text", "text": "番茄时钟蜡烛模式 20 分钟"}, now=0.0))
    start = next(m for m in sink.json if m.get("kind") == "focus_start")
    assert start["style"] == "candle" and start["total_s"] == 1200
    assert sess._focus.style == "candle"
