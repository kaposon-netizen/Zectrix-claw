"""便利贴上的「花火」番茄钟 / 倒计时（语音起停，墨水屏整屏渲染）。

两种外观，都对齐平板端番茄钟（client/src/pomotimer.ts）：
  · **火花**（默认）：一条引线，燃点沿线走，烧到头「花火绽放」。
  · **蜡烛**：一根蜡烛越烧越短，火苗在顶端摇曳，烧完只剩烛台和一缕烟。
    只有**明确说「蜡烛模式」**才进（用户 2026-09-12 定），不说就是火花。
墨水屏 400×300 黑白、只能摆 24px 思源黑体文字行（固件 RenderPage 只认 title/heard/body 三段文本），
所以引线用字画：`火` 是燃点，`一` 是没烧到的引线，`丶` 是烧过的灰烬，`花` 是引线尽头的花火。
这三个字都在固件的 slim 字体子集里（U+706B/U+4E00/U+4E36/U+82B1 已核）；■ ● ━ 之类符号**不在**，别用。

不逐秒刷：墨水屏刷一次几百毫秒且有残影，倒计时**每分钟推一屏**（最后一分钟每 15 秒），到点再推「绽放」屏。
设备端不需要改固件——全部是服务端按时间推 display 页。

语音口径（全部在服务端判，设备不判意图）：
  开：「番茄钟 25 分钟」「开个番茄钟」（默认 25）「倒计时 10 分钟」「专注 20 分钟」「计时 5 分钟」「倒数半小时」
  控：「暂停」「继续」「取消/停止/结束番茄钟」「还剩多久」
中文数字（十五 / 二十五 / 两 / 半小时 / 一个半小时 / 一刻钟）都认。
"""
from __future__ import annotations

import re
import time
from typing import Any, Callable, Dict, List, Optional

# ── 触发词 ──
START_WORDS = ("番茄钟", "番茄时钟", "番茄", "倒计时", "倒数", "计时", "专注")
DEFAULT_POMODORO_MIN = 25          # 「开个番茄钟」没说时长 → 25
MIN_MINUTES, MAX_MINUTES = 1, 60   # 与 agent/toolbox.verify_start_focus 同口径：小学生一次 ≤60 分钟
CANDLE_WORDS = ("蜡烛", "蜡烛模式", "烛光")     # 说到「蜡烛」才换蜡烛外观，否则默认火花
SPARK_WORDS = ("火花", "火花模式", "花火")
STYLE_SPARK, STYLE_CANDLE = "spark", "candle"
PAUSE_WORDS = ("暂停",)
RESUME_WORDS = ("继续", "接着来", "恢复")
CANCEL_WORDS = ("取消", "停止", "停掉", "结束", "不计时", "不要计时", "关掉", "关闭", "退出")
REMAIN_WORDS = ("还剩", "剩多久", "剩多少", "还有多久", "还有多少时间", "到了吗", "几分钟了")
# 问句不当命令：「番茄钟是什么」「为什么要专注」
QUESTION_MARKS = ("是什么", "什么是", "为什么", "怎么", "如何", "有什么用", "意思")

FUSE_SEGMENTS = 14                 # 引线段数：一行 16 个汉字 = 火 + 14 段 + 花

_CN_DIGIT = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9}


def cn_to_int(s: str) -> Optional[int]:
    """中文数字 → 整数（只管 0–99，够分钟用）：十五=15 二十=20 二十五=25 两=2 十=10。"""
    s = s.strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if s == "十":
        return 10
    total, cur = 0, 0
    for ch in s:
        if ch in _CN_DIGIT:
            cur = _CN_DIGIT[ch]
        elif ch == "十":
            total += (cur if cur else 1) * 10
            cur = 0
        else:
            return None
    return total + cur


_NUM = r"(\d+|[零〇一二两三四五六七八九十]+)"
# 「一个半小时」「半小时」「1小时」「一小时二十分钟」「25分钟」「十五分」「一刻钟」「90秒」
_HOUR_RE = re.compile(_NUM + r"?\s*(个)?\s*(半)?\s*(小时|个钟头|钟头)")
_HALF_HOUR_RE = re.compile(r"半\s*(个)?\s*(小时|钟头)")
_QUARTER_RE = re.compile(r"一刻钟|一刻")
_MIN_RE = re.compile(_NUM + r"\s*(分钟|分)")
_SEC_RE = re.compile(_NUM + r"\s*秒")


def parse_minutes(text: str) -> Optional[int]:
    """从一句话里抠出时长（分钟，四舍五入到整分，最少 1）。抠不到 → None。"""
    t = text.replace("，", " ").replace(",", " ")
    minutes = 0.0
    found = False
    m = _HOUR_RE.search(t)
    if m and (m.group(1) or m.group(3)):
        n = cn_to_int(m.group(1)) if m.group(1) else 0
        if n is None:
            n = 0
        if m.group(1) and not m.group(3) and n == 0:
            return None
        minutes += n * 60 + (30 if m.group(3) else 0)
        found = True
    elif _HALF_HOUR_RE.search(t):
        minutes += 30
        found = True
    if _QUARTER_RE.search(t):
        minutes += 15
        found = True
    m = _MIN_RE.search(t)
    if m:
        n = cn_to_int(m.group(1))
        if n is not None:
            minutes += n
            found = True
    m = _SEC_RE.search(t)
    if m:
        n = cn_to_int(m.group(1))
        if n is not None:
            minutes += n / 60.0
            found = True
    if not found:
        return None
    return max(1, int(round(minutes)))


def parse_focus_request(text: str) -> Optional[Dict[str, Any]]:
    """开计时的话 → {"kind": "pomodoro"|"countdown", "minutes": int}；不是开计时的话 → None。

    「番茄钟」不带时长默认 25；「倒计时」不带时长返回 minutes=None（调用方追问）。
    带问句词（是什么/为什么/怎么…）的一律不当命令——那是在问，不是在开。
    """
    t = text.strip()
    if not t or not any(w in t for w in START_WORDS):
        return None
    if any(q in t for q in QUESTION_MARKS):
        return None
    # 「暂停/取消/还剩」这类控制词优先由 parse_focus_control 处理；这里只认「开」
    if any(w in t for w in PAUSE_WORDS + CANCEL_WORDS + REMAIN_WORDS):
        return None
    kind = "pomodoro" if ("番茄" in t or "专注" in t) else "countdown"
    # 外观：默认火花；说到「蜡烛」才换。说「火花模式」也认（明确要默认的那个）。
    style = STYLE_CANDLE if any(w in t for w in CANDLE_WORDS) else STYLE_SPARK
    mins = parse_minutes(t)
    if mins is None:
        mins = DEFAULT_POMODORO_MIN if kind == "pomodoro" else None
    if mins is not None:
        mins = max(MIN_MINUTES, min(MAX_MINUTES, mins))
    return {"kind": kind, "minutes": mins, "style": style}


def parse_focus_control(text: str) -> Optional[str]:
    """计时进行中的控制词 → pause|resume|cancel|remain；否则 None。"""
    t = text.strip()
    if not t:
        return None
    if any(w in t for w in CANCEL_WORDS):
        return "cancel"
    if any(w in t for w in PAUSE_WORDS):
        return "pause"
    if any(w in t for w in REMAIN_WORDS):
        return "remain"
    if any(w in t for w in RESUME_WORDS) and len(t) <= 8:
        return "resume"
    return None


# ── 计时器状态（纯数据，时间由调用方注入，便于测试）──
class FocusTimer:
    def __init__(self, kind: str, minutes: int, *, now: float, style: str = STYLE_SPARK):
        self.kind = kind                      # pomodoro | countdown
        self.style = style if style in (STYLE_SPARK, STYLE_CANDLE) else STYLE_SPARK
        self.total_s = int(minutes) * 60
        self.started_at = now
        self.paused_at: Optional[float] = None
        self._elapsed_before_pause = 0.0      # 暂停前累计走过的秒数
        self.done = False

    @property
    def minutes(self) -> int:
        return self.total_s // 60

    def elapsed(self, now: float) -> float:
        if self.paused_at is not None:
            return self._elapsed_before_pause
        return self._elapsed_before_pause + (now - self.started_at)

    def remaining(self, now: float) -> int:
        return max(0, int(round(self.total_s - self.elapsed(now))))

    @property
    def paused(self) -> bool:
        return self.paused_at is not None

    def pause(self, now: float) -> None:
        if self.paused_at is None:
            self._elapsed_before_pause = self.elapsed(now)
            self.paused_at = now

    def resume(self, now: float) -> None:
        if self.paused_at is not None:
            self.paused_at = None
            self.started_at = now

    def progress(self, now: float) -> float:
        if self.total_s <= 0:
            return 1.0
        return min(1.0, self.elapsed(now) / self.total_s)

    def next_tick_delay(self, now: float) -> float:
        """下一次推屏的等待秒数：每分钟一次；最后一分钟每 15 秒。到 0 立刻。"""
        rem = self.remaining(now)
        if rem <= 0:
            return 0.0
        if rem <= 60:
            step = rem % 15 or 15
            return float(step)
        step = rem % 60 or 60
        return float(step)


# ── 渲染：字画引线 ──
def fuse_line(progress: float, segments: int = FUSE_SEGMENTS) -> str:
    """0~1 进度 → 「丶丶丶火一一一一一花」。燃点按长度推进（和平板端一样按弧长而不是按段数跳）。"""
    p = max(0.0, min(1.0, progress))
    burnt = int(p * segments + 1e-9)
    if p >= 1.0:
        return "丶" * segments + "火花"       # 烧到头：燃点贴着花
    return "丶" * burnt + "火" + "一" * (segments - burnt) + "花"


def candle_line(progress: float, segments: int = FUSE_SEGMENTS) -> str:
    """0~1 进度 → 「火一一一一一」：`火` 是火苗，`一` 是还没烧掉的蜡烛，越烧越短。
    与引线的区别：引线是燃点在固定长度上移动，蜡烛是**整体变短**。"""
    p = max(0.0, min(1.0, progress))
    left = int(round((1.0 - p) * segments))
    if left <= 0:
        return "丶丶丶"                      # 烧完：只剩一缕烟
    return "火" + "一" * left


def _mmss(seconds: int) -> str:
    seconds = max(0, int(seconds))
    return "%d:%02d" % (seconds // 60, seconds % 60)


def title_of(kind: str, style: str = STYLE_SPARK) -> str:
    head = "烛光" if style == STYLE_CANDLE else "花火"
    return head + ("番茄钟" if kind == "pomodoro" else "倒计时")


def render_focus_page(timer: FocusTimer, *, now: float, battery: Optional[int] = None) -> Dict[str, Any]:
    """计时中的整屏：标题行 + 剩余时间 + 引线 + 操作提示。8 行以内，不翻页。"""
    rem = timer.remaining(now)
    prog = timer.progress(now)
    total_min = timer.minutes
    candle = timer.style == STYLE_CANDLE
    if timer.paused:
        status = "已暂停 · 说「继续」接着烧"
    elif rem > 60:
        status = "还剩 %d 分钟，专心做手上的事" % ((rem + 59) // 60)
    elif candle:
        status = "最后 %d 秒，蜡烛快烧完了！" % rem
    else:
        status = "最后 %d 秒，花火马上绽放！" % rem
    body: List[str] = [
        "",
        "　　　　　" + _mmss(rem) + " / " + _mmss(timer.total_s),
        "",
        candle_line(prog) if candle else fuse_line(prog),
        "",
        status,
        "",
        "说「暂停」「继续」「取消」都行",
    ]
    return {
        "schema_version": "pc-0.1",
        "mode": "focus",
        "style": timer.style,
        "title": title_of(timer.kind, timer.style) + " · %d 分钟" % total_min,
        "body": body,
        "progress": {"done": int(prog * 100), "total": 100},
        "truncated": False,
        "footer": {"battery": battery},
    }


def render_done_page(timer: FocusTimer, *, battery: Optional[int] = None) -> Dict[str, Any]:
    """到点：花火绽放 / 蜡烛燃尽。"""
    candle = timer.style == STYLE_CANDLE
    art = ["　　　　　丶　丶　丶　　　　", "　　　　蜡　烛　燃　尽　啦"] if candle \
        else ["　　　丶丶丶丶丶丶火花　　　", "　　　　　花　火　绽　放　！"]
    body = [
        "",
        art[0],
        art[1],
        "",
        "你专注了 %d 分钟，真棒。" % timer.minutes if timer.kind == "pomodoro" else "%d 分钟到啦。" % timer.minutes,
        "站起来动一动，喝口水。",
        "",
        "再来一轮就说「番茄钟 25 分钟」",
    ]
    return {
        "schema_version": "pc-0.1",
        "mode": "focus",
        "style": timer.style,
        "title": title_of(timer.kind, timer.style) + " · 完成",
        "body": body,
        "progress": {"done": 100, "total": 100},
        "truncated": False,
        "footer": {"battery": battery},
    }


def render_ask_minutes_page(*, battery: Optional[int] = None) -> Dict[str, Any]:
    return {
        "schema_version": "pc-0.1",
        "mode": "focus",
        "title": "花火倒计时",
        "body": ["", "倒计时多久？", "", "说「倒计时 10 分钟」", "或「番茄钟 25 分钟」", "", "一次最长 60 分钟"],
        "truncated": False,
        "footer": {"battery": battery},
    }


def spoken_start(timer: FocusTimer) -> str:
    head = "蜡烛点上啦！" if timer.style == STYLE_CANDLE else "花火点燃！"
    if timer.kind == "pomodoro":
        return head + "%d 分钟专注开始，我在这儿陪你。" % timer.minutes
    return head + "倒计时 %d 分钟开始。" % timer.minutes


def spoken_done(timer: FocusTimer) -> str:
    head = "蜡烛燃尽啦！" if timer.style == STYLE_CANDLE else "花火绽放！"
    if timer.kind == "pomodoro":
        # 结尾留一个小问题：让孩子自己说说刚才做了什么（表达教练口径，见 agent/expression.py）
        return head + "你专注了 %d 分钟，真棒。站起来动一动。刚才做了什么呀？" % timer.minutes
    return head + "%d 分钟倒计时到啦。" % timer.minutes


def spoken_remaining(timer: FocusTimer, now: float) -> str:
    rem = timer.remaining(now)
    if timer.paused:
        return "现在是暂停的，还剩 %s。说继续就接着烧。" % _human(rem)
    return "还剩 %s，加油。" % _human(rem)


def _human(seconds: int) -> str:
    m, s = divmod(max(0, int(seconds)), 60)
    if m and s:
        return "%d 分 %d 秒" % (m, s)
    if m:
        return "%d 分钟" % m
    return "%d 秒" % s
