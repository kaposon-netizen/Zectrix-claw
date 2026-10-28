"""PocketClawSession：Note 4 伴学掌机单设备会话状态机编排。

职责（处理设备端上下行帧、18 大伴学状态机调度与墨水屏图像渲染）：
  · AI 伴聊对决：长按说话 → STT/LLM → 400×300 墨水屏整屏排版 + 流式 TTS。
  · 18 大伴学功能路由：作业清单、生字听写、古诗背诵、口算闯关、英语短文阅读、花火番茄钟等。
  · 记录想法：NotesStore 存储（保护儿童隐私，家长端仅展示聚合打卡）。
  · 单词卡：Leitner 间隔记忆盒排期，物理三键判读记忆状态。
  · NFC 贴卡：GT23SC6699 刷卡换孩子/功能卡。

墨水屏画像契约（4.2" 电子墨水屏）：黑白/16灰阶显示，无蓝光伤害。
固定画像为 epaper_4in2，安全白名单渲染。

传输注入：构造时注入两个 async 回调 send_json(str) / send_bin(bytes)，Session 纯逻辑与传输解耦。
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import datetime
import json
import logging
import random
import re
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, Optional

try:
    from guardrail.whitelist import render_whitelist
except ImportError:
    def render_whitelist(grade_band: str, client_profile: str) -> set[str]:
        return {"card", "svg"}

log = logging.getLogger("pocketclaw")
from pocketclaw import cards, display, practice, protocol, wordlookup
from pocketclaw import focus as _focus
from pocketclaw.device_registry import DeviceRegistry, is_unbound
from pocketclaw.notes import NotesStore
from pocketclaw.voice import STTProvider, TTSProvider

SendJson = Callable[[str], Awaitable[None]]
SendBin = Callable[[bytes], Awaitable[None]]

PROFILE = "epaper_4in2"            # Note 4 墨水屏画像契约

# 学伴音频缓存：word(lower) → 发音 PCM；跨会话复用，避免重复合成
_WORD_AUDIO_CACHE: dict = {}

# 默认学伴人设事实源（支持家长端网页与小程序实时自定义）
try:
    from agent.companion import SHRIMP_NAME as LOBSTER_NAME, SHRIMP_PERSONA as LOBSTER_PERSONA  # noqa: E402
except ImportError:
    LOBSTER_NAME = "虾崽"
    LOBSTER_PERSONA = (
        "你的第一只伴学小龙虾，天生好奇、话里带笑。最爱把话头交回给小朋友，"
        "遇到有趣的知识会兴奋地举例子，讲完只留一个小问题等他接。"
        "遇到自己不擅长的领域，会大方地把专家朋友请出来。"
    )
# 墨水屏专属叮嘱：只说要对小朋友讲的话，禁止脚注/自我说明（曾把「[1] 说明：…」教学理由发到屏上）
_PC_GUIDANCE = (
    "你在一块黑白墨水屏便利贴上和小朋友说话，屏幕很小、只能靠语音听。只输出要对小朋友讲的话本身，"
    "口语、亲切。**每次回复最多两句、总共不超过 40 个字**——说完就停，把话头留给孩子；"
    "英语练习时英文只说一句短句，再给一句中文意思。绝不要加脚注编号（如 [1]、[2]），绝不要出现「说明：」去解释"
    "你为什么这么问、你遵循了什么规则或年级要求，也不要复述你的教学设计。"
    # 表达教练（agent/expression.py 同源）：便利贴上孩子更容易一个字一个字地答，学伴得把话头交回去
    "说完把话头交给孩子：结尾留一个短问题（不超过 12 个字）；孩子只说「嗯/不知道/随便」时，"
    "别当成结束，给他两个选项或一个句子开头让他接着说，别一连问好几个。"
)

# 口语练习「模式锁」：主脑每轮按触发词重选技能——孩子答一句中文「随便闲聊」（回应它自己问的
# 「选情景还是闲聊」）就没有英语触发词，直接掉回常规聊天。进入口语陪练官后锁住该技能，
# 直到孩子明确退出，或闲置超过 _EN_LOCK_TTL。查词/作业/书本等指令仍在 _chat 之前路由，练习中照常可用。
_EN_SKILL = "english-speaking-01"
_EN_EXIT = ("不练了", "不想练", "别练了", "退出", "不说英语", "说中文", "换个话题", "结束练习", "停下", "不玩了")
_EN_LOCK_TTL = 10 * 60      # 秒：闲置这么久后自动解锁，免得几小时后一句「今天天气真好」还被按英语练习答

# 兜底清洗：即便模型仍漏出脚注/自我说明，也不上屏/不念出来
_FOOTNOTE_TAIL_RE = re.compile(r"\n?\s*\[?\s*\d+\s*\]?\s*说明[:：].*\Z", re.S)
_FOOTNOTE_MARK_RE = re.compile(r"\s*\[\s*\d+\s*\]")


def _clean_reply(text: str) -> str:
    """去掉模型漏出的「[1] 说明：…」尾巴与正文里的 [n] 脚注标记。"""
    if not text:
        return text
    text = _FOOTNOTE_TAIL_RE.sub("", text)   # 先砍「[1] 说明：…」及其后全部
    text = _FOOTNOTE_MARK_RE.sub("", text)    # 再去正文内的 [1][2] 标记
    return text.strip()


# 口语陪练回合：模型输出「英文 %%ZH%% 中文意思」。同平板规矩（英文气泡默认只显示/只念英文，中文藏在「中」按钮）：
# 便利贴**只念英文**——此前英文+中文全念，一轮 24s 音频、孩子插不上话；标记本身也曾原样印上墨水屏。
_ZH_MARK = "%%ZH%%"


def _spoken_part(text: str) -> str:
    i = (text or "").find(_ZH_MARK)
    return text if i < 0 else text[:i]


def _display_part(text: str) -> str:
    """上屏：英文在上、中文意思在下，去掉标记本身。"""
    return (text or "").replace(_ZH_MARK, "\n").strip()

# 意图触发词（设备端不判意图，全在服务端）
_NOTE_TRIGGERS = ("记一下", "记录", "帮我记", "记个", "备忘")
_SHOW_NOTES = ("看看想法", "我的想法", "看想法", "想法列表")
_WORD_REVIEW = ("背单词", "单词卡", "复习单词", "记单词", "每日一词", "今天的单词", "今天单词")
# 随机卡片轮播（古诗/现代诗/名言）
_CAROUSEL = ("卡片轮播", "轮播卡片", "轮播", "随机卡片", "卡片模式", "看卡片", "来首诗", "读首诗",
             "念首诗", "来点诗词", "来句诗", "来句名言", "名人名言", "读句名言", "随机诗词")
# 我的书本（奇阅书房最近在看那章，墨水屏分页朗读；三端进度互通）
_BOOK_TRIGGERS = ("我的书本", "我的阅读", "继续读", "接着读")   # 进入/续读当前章
_BOOK_NEXT = ("下一章",)                                       # 翻到下一章
_BOOK_PREV = ("上一章", "前一章")
_ADD_WORD_PREFIX = ("加单词", "学单词", "新单词", "收藏单词")
_LOOKUP_TRIGGERS = ("什么意思", "怎么读", "怎么写", "什么意", "查单词", "查词", "查一下", "的意思", "的读音")
_TASK_ADD_PREFIX = ("加任务", "添加任务", "新任务", "记个作业", "加作业", "加待办")
_TASK_DONE_PREFIX = ("完成", "做完了", "做完", "打钩", "勾掉")
# 「XX 写完了 / 做了」后缀 → 打勾（先作业、再想法）
_DONE_SUFFIX = ("写完了", "写好了", "做完了", "做好了", "完成了", "搞定了", "做了", "写完", "做完")
# 「删掉 XX」/「XX 删掉」→ 从想法列表删除
_DELETE_PREFIX = ("删掉", "删了", "删除", "去掉")
_DELETE_SUFFIX = ("删掉", "删了", "删除", "去掉", "不要了")
_TASK_SHOW = ("作业清单", "任务清单", "待办", "我的任务", "我的作业", "今天的作业", "今天作业",
              "今天要做", "今天要写", "看作业", "看看作业", "看下作业", "看一下作业", "查作业",
              "有什么作业", "有哪些作业", "作业有哪些", "还有什么作业", "作业还有", "今天有什么作业",
              "作业写完没", "作业做完没", "作业还剩")
# 小学生核心高频三大件：听写、口算、古诗背诵
_DICTATION_TRIGGERS = ("听写", "听写生字", "报听写", "生字听写", "开始听写", "考听写")
_ARITH_TRIGGERS = ("做口算", "口算", "口算题", "口算速练", "做算术", "心算", "口算大闯关")
_RECITE_PREFIX = ("背诗", "背诵", "抽查古诗", "考我背诗", "背古诗", "背一下", "背")
_EXIT_TRIGGERS = ("不背了", "不练了", "不听写了", "退出", "返回", "结束", "回主页", "不玩了",
                  "退出学伴", "退出对话", "退出聊天", "关闭学伴", "关闭对话", "做作业", "写作业",
                  "不聊了", "先不聊了", "再见", "拜拜", "不讲了", "讲完了", "下课", "下课了",
                  "不猜了", "不做了", "不读了")
_MENU_TRIGGERS = ("功能菜单", "打开菜单", "功能列表", "主菜单", "选功能", "菜单", "目录")
_CHAT_COMPANION_TRIGGERS = ("ai学伴", "学伴", "ai伴学", "聊聊天", "陪我聊天", "聊天模式", "开启学伴", "打开学伴", "智能学伴", "苏格拉底", "聊会儿天", "想和你聊天")
_VOCAB_TRIGGERS = ("生词本", "英语生词", "生词卡", "生词", "复习生词", "单词本", "背单词", "学单词", "查生词")
_READING_TRIGGERS = ("英语阅读", "读英语", "英文阅读", "读课文", "分级阅读", "英文短文", "读短文", "课文朗读")
_ARTICLE_NEXT_TRIGGERS = ("下一篇", "下一篇文章", "换一篇文章", "换篇", "换一篇", "下一课", "读下一篇", "看下一篇", "换一课", "换个文章", "下一篇课文", "切下一篇", "下一篇文章阅读")
_ARTICLE_PREV_TRIGGERS = ("上一篇", "上一篇文章", "换上一篇", "上一课", "读上一篇", "看上一篇", "前一篇", "上一篇课文", "切上一篇")
_POEM_NEXT_TRIGGERS = ("下一首", "下一首诗", "换一首", "换一首诗", "换首诗", "下一首背", "切下一首")
_POEM_PREV_TRIGGERS = ("上一首", "上一首诗", "换上一首", "前一首", "切上一首")
_READING_NEXT_TRIGGERS = ("下一句", "下一段", "往下读")
_READING_PREV_TRIGGERS = ("上一句", "上一段", "往上读")
_READING_REPEAT_TRIGGERS = ("读这句", "念这句", "读一遍", "朗读句子", "朗读这句")
_HABIT_TRIGGERS = ("好习惯", "习惯打卡", "打卡", "每日习惯", "做打卡")
_TIMETABLE_TRIGGERS = ("课程表", "看课表", "今日课程", "今天有什么课", "明天有什么课", "课表", "书包备忘")
_IDIOM_TRIGGERS = ("每日成语", "成语典故", "学成语", "听成语", "读成语", "成语")
_MISTAKES_TRIGGERS = ("消灭错题", "错题消灭", "错题挑战", "消除错题", "错题门诊", "消灭错题本")
_GUESS_TRIGGERS = ("猜猜看", "猜动物", "二十问", "玩猜猜", "猜谜语")
_FEYNMAN_TRIGGERS = ("费曼讲课", "讲给我听", "小老师", "当老师", "我来讲课", "我要讲课", "我要当老师", "开始讲课", "费曼")
_VERIFY_TRIGGERS = ("核实一下", "核实", "真的吗", "是不是真的", "帮我核实", "真假核实")
_FEIHUA_TRIGGERS = ("飞花令", "诗词飞花令", "玩飞花令", "对诗", "诗词对战", "诗词接龙")
_HANZI_TRIGGERS = ("形近字", "错字门诊", "易错字", "辨字", "字形辨析", "纠错字", "汉字门诊", "易错字门诊", "辨字门诊")
_NEWS_TRIGGERS = ("小学新鲜事", "新鲜事", "校园新闻", "看新闻", "比赛展会", "学习活动", "小学生新闻",
                  "看新鲜事", "有什么新闻", "最新活动", "有什么比赛", "有什么展会", "新鲜事儿")
_SCREENSAVER_TRIGGERS = ("打开屏保", "显示屏保", "屏保模式", "屏保", "看屏保", "进入屏保", "桌面屏保",
                         "名画屏保", "诗词屏保", "语录屏保", "倒计时屏保", "课表屏保")
# 内容策略（growth/daily_spark.py）：今日探索题 → 把当天为这个孩子现编的问题当孩子的话交给主脑，三张轮着来
_SPARK_TRIGGERS = ("聊聊这个", "今天的问题", "今日探索", "今天的探索", "探索问题",
                   "有什么好玩的问题", "好玩的问题", "有意思的问题")
# 错题本到期复习：推屏显示题面 + 让学伴苏格拉底式引导（不给答案）
_MISTAKE_TRIGGERS = ("错题",)
# 早安推屏「今日探索」按 child 每天只推一次（进程内存；重连/深睡唤醒的每次 hello 都不再重推）。
# 只有**真推出去**才记账：当天没生成出卡时不记，下次 hello 还有机会。
_MORNING_PUSHED: Dict[str, str] = {}        # child_id → "YYYY-MM-DD"
_SPARK_CURSOR: Dict[str, int] = {}          # child_id → 下一张探索题下标（跨会话轮着来）
_MISTAKE_CURSOR: Dict[str, int] = {}        # child_id → 下一道到期错题下标
_SPARK_DISABLED_LOGGED = False              # daily_spark=None 只在日志里说一次，别每条连接刷一行


def _chat_question(text: str) -> bool:
    """In chat mode, questions about a feature are not themselves a mode switch."""
    cleaned = text.strip().strip('。！？?!，, ')
    if cleaned in _TASK_SHOW or cleaned in _NEWS_TRIGGERS or cleaned in _SPARK_TRIGGERS:
        return False
    if cleaned.startswith(('打开', '开始', '启动', '进入', '切换到', '切到', '退出', '返回',
                           '完成', '删除', '删掉', '加任务', '加作业', '记一下', '帮我记')):
        return False
    if cleaned.endswith('讲给我听') and cleaned != '讲给我听' and not cleaned.startswith(('我来', '我要', '我想当')):
        return True
    return any(word in text for word in ('为什么', '怎么', '什么', '哪里', '哪儿', '多少',
                                          '谁', '是否', '能不能', '可不可以', '吗', '？', '?'))


def quiet_reason(controls: Dict[str, Any], hour: int) -> str:
    """睡前时段是否禁用；返回原因文案（空=不禁）。

    先看 `time_control`——家长没开时间控制就一律不拦（与 app.py 的服务端权威门一致；
    否则会出现「家长把时间控制关了，pocketclaw 却还按写死的 22-07 拦」的不一致）。
    时段用家长配置的 night_start/night_end（时级近似），不再写死 22-07。
    """
    if not controls.get("time_control"):
        return ""
    if not controls.get("night_block", True):
        return ""
    start_h = int(str(controls.get("night_start", "21:30")).split(":")[0])
    end_h = int(str(controls.get("night_end", "07:00")).split(":")[0])
    in_window = (start_h <= hour or hour < end_h) if start_h > end_h else (start_h <= hour < end_h)
    return "现在是睡觉时间，明天再聊哦。晚安！" if in_window else ""


# 严格模式下未绑定设备的上屏提示（P0-3）。绑定入口是家长小程序/平板（REST bind，需令牌）。
_UNBOUND_TIP = "这台便利贴还没绑定小朋友。请家长在小程序或平板上绑定后再来聊天～"


class PocketClawSession:
    def __init__(self, runtime, *, send_json: SendJson, send_bin: SendBin,
                 stt: STTProvider, tts: TTSProvider,
                 registry: DeviceRegistry, notes: NotesStore,
                 controls=None, books=None, grade_band: str = "g3_g6", on_focus_done=None, on_focus_today=None,
                 daily_spark=None, qbank=None, reading_path=None):
        self.runtime = runtime
        self.send_json = send_json
        self.send_bin = send_bin
        self.stt = stt
        self.tts = tts
        self._skill_lock: Optional[str] = None     # 口语练习模式锁（见 _EN_SKILL）
        self._skill_lock_ts: float = 0.0
        self.registry = registry
        self.notes = notes
        self.controls = controls
        self.books = books           # 伴读书房 BookStore（「我的书本」用）；None=未启用
        self.grade_band = grade_band
        # 内容策略：今日探索题（growth.daily_spark.DailySpark）与错题本（qbank.QuestionBank）。
        # None = 功能不启用：早安不推屏、「聊聊这个」/「错题」落回 AI 对话兜底（主脑自己有 spark_today 工具）。
        self.daily_spark = daily_spark
        self.qbank = qbank
        global _SPARK_DISABLED_LOGGED
        if daily_spark is None and not _SPARK_DISABLED_LOGGED:
            _SPARK_DISABLED_LOGGED = True
            log.info("pocketclaw 今日探索未启用（daily_spark=None）：早安不推屏、「聊聊这个」走普通对话")
        self._spark_task: Optional["asyncio.Task"] = None   # 早安推屏后台任务（hello_ack 不等它）

        self.session_id = uuid.uuid4().hex[:12]
        self.device_id = ""
        # P0-3：严格模式（生产 /pocketclaw，registry.strict=True）下，hello 之前**没有任何孩子**，
        # hello 之后也只认服务端的绑定（不认设备自报）。非严格保留旧缺省 "kid"（既有单测/离线工具）。
        self._strict = bool(getattr(registry, "strict", False))
        self.child_id = "" if self._strict else "kid"
        self.helloed = False             # 是否已完成 hello（严格模式下 hello 前的业务帧一律拒绝）
        self.reject_close = False        # 严格模式拒掉了一帧 → endpoint 据此关连接
        self.subject = ""            # 当前学科上下文（NFC 学科卡设置，#18）
        self.battery: Optional[int] = None
        self._ptt = bytearray()      # 当前 PTT 累积的 PCM
        self._ptt_hint = ""          # debug 旁路：本段 PTT 附带的文本（模拟器用）
        self._recording = False
        self._stt_stream = None
        self._partial_last = 0.0
        self._screen_lock = asyncio.Lock()
        self._screen_pending = None
        self._turn_task: Optional["asyncio.Task"] = None   # 当前一轮（STT→脑→TTS）后台任务，供打断取消
        self._speech_task: Optional["asyncio.Task"] = None # 独立朗读（句子/单词/课表/提示等）后台流式任务，供毫秒级打断
        self._history: list = []     # 最近对话（孩子/助手 交替，供上下文记忆），保留最近 10 行
        self._focus: Optional[_focus.FocusTimer] = None    # 花火番茄钟/倒计时（进行中）
        self._focus_task: Optional["asyncio.Task"] = None  # 每分钟推屏的后台任务
        self.on_focus_done = on_focus_done                  # (child_id, minutes, kind) → 成长卡证据挂点，可 None
        self.on_focus_today = on_focus_today                # (child_id) → 今日已专注分钟，可 None
        self._features: tuple = ()                          # 设备 hello 声明的能力（focus_local 等）
        self._clock = time.time                             # 计时循环用的时钟（测试可注入假时钟）
        self.mode: str = "task"                             # 当前交互模式: task|recite|dictation|arith|vocab|reading|focus|menu
        self._menu_idx: int = 0
        self._menu_prev_mode: str = "task"
        self._task_idx: int = -1                            # 作业清单选中光标（-1=未选中，0..N-1=当前选中项）
        self._idiom_idx: int = 0                            # 每日成语浏览游标
        self._dictation_session: Optional[practice.DictationSession] = None
        self._arith_session: Optional[practice.ArithSession] = None
        self._recite_poem: Optional[practice.Poem] = None
        self._recite_done_indices: set[int] = set()
        # 英语生词本状态
        self._vocab_idx: int = 0
        self._vocab_last_score: Optional[int] = None
        self._vocab_last_rating: str = ""
        self._vocab_last_feedback: str = ""
        # 英语文章阅读状态
        from pocketclaw.reading import ReadingStore, ReadingSession
        self.reading_store = ReadingStore(path=reading_path)
        self._reading_article_idx: int = 0
        self._reading_session: Optional[ReadingSession] = None
        # SnapClaw 移植：猜猜看、费曼小老师、事实核查官状态
        self._guess_session: Optional[practice.GuessSession] = None
        self._feynman_session: Optional[practice.FeynmanSession] = None
        self._last_verify_result: Optional[Dict[str, Any]] = None
        # 小学生拓展：诗词飞花令、错字门诊状态
        self._feihua_session: Optional[practice.FeihuaSession] = None
        self._hanzi_session: Optional[practice.HanziSession] = None
        self._screensaver_session: Optional[Any] = None
        # 小学新鲜事状态
        self._news_items: List[Dict[str, Any]] = []
        self._news_idx: int = 0
        self._news_view: str = "list"
        self._news_scroll_task: Optional["asyncio.Task"] = None
        self._news_scroll_offset: int = 0
        # AI学伴 5分钟自动退出守卫
        self._chat_idle_task: Optional["asyncio.Task"] = None
        self._chat_idle_deadline: float = 0.0

    def __del__(self) -> None:
        try:
            if hasattr(self, "_chat_idle_task") and self._chat_idle_task is not None and not self._chat_idle_task.done():
                self._chat_idle_task.cancel()
            if hasattr(self, "_news_scroll_task") and self._news_scroll_task is not None and not self._news_scroll_task.done():
                self._news_scroll_task.cancel()
            if hasattr(self, "_speech_task") and self._speech_task is not None and not self._speech_task.done():
                self._speech_task.cancel()
            if hasattr(self, "_turn_task") and self._turn_task is not None and not self._turn_task.done():
                self._turn_task.cancel()
        except Exception:
            pass

    CORE_MODES = [
        ("task", "今日作业"),
        ("recite", "课内古诗"),
        ("dictation", "生字听写"),
        ("arith", "口算闯关"),
        ("vocab", "英语生词"),
        ("reading", "英语阅读"),
        ("focus", "花火番茄"),
        ("habit", "习惯打卡"),
        ("timetable", "桌面课表"),
        ("idiom", "每日成语"),
        ("mistakes", "错题消灭"),
        ("feynman", "费曼讲课"),
        ("guess", "猜猜看"),
        ("verify", "核实一下"),
        ("feihua", "诗词飞花令"),
        ("hanzi", "错字门诊"),
        ("news", "小学新鲜事"),
        ("chat", "AI学伴"),
        ("screensaver", "桌面屏保"),
    ]

    # ── 入口：一条控制帧 ──
    async def on_control(self, msg: Dict[str, Any], *, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        kind = msg.get("kind")
        if self._strict and kind != protocol.HELLO and not self.helloed:
            # 与 /ws 同口径（P0-3）：没 hello 就没有身份，业务帧不许落到任何孩子身上
            log.warning("pocketclaw 未 hello 的 %s 帧被拒 session=%s", str(kind)[:24], self.session_id)
            self.reject_close = True
            await self.send_json(protocol.error("hello_required", "请先发送 hello 完成握手"))
            return
        if kind == protocol.HELLO:
            await self._on_hello(msg, now)
        elif kind == "screen_snapshot_chunk":
            self._accept_screen_chunk(msg)
        elif kind == "screen_snapshot_error":
            pending = self._screen_pending
            if pending and msg.get("request_id") == pending["id"] and not pending["future"].done():
                pending["future"].set_exception(RuntimeError("设备忙或尚无屏幕画面"))
        elif kind == protocol.PTT_START:
            # 打断（barge-in）：正在出话/播报时按住说话，取消当前一轮，让麦上行接管
            await self._abort_speech(reason="ptt_start", send_stop=True)
            self._ptt = bytearray()
            self._ptt_hint = str(msg.get("text") or msg.get("hint_text") or "")
            self._recording = True
            starter = getattr(self.stt, "start_stream", None)
            if getattr(self.stt, "supports_streaming", False) is True and callable(starter):
                self._partial_last = 0.0
                try:
                    self._stt_stream = starter(self._on_stt_partial)
                except Exception:
                    # 启动失败仍保留整段录音，松键后可使用普通识别。
                    self._stt_stream = None
                    log.warning("流式识别启动失败，使用整段识别")
            if self.mode == "chat":
                self._reset_chat_idle_timer(now)
        elif kind == "ptt_cancel":
            self._recording = False
            self._ptt.clear()
            self._ptt_hint = ""
            if self._stt_stream:
                await self._stt_stream.cancel()
                self._stt_stream = None
        elif kind == protocol.PTT_STOP:
            if not self._recording:
                return
            self._recording = False
            pcm, hint = bytes(self._ptt), self._ptt_hint
            self._ptt = bytearray()
            self._ptt_hint = ""
            # 后台跑一轮：接收循环保持空闲，才能在播报中读到下一次打断的 ptt_start
            stream, self._stt_stream = self._stt_stream, None
            self._turn_task = asyncio.create_task(self._run_turn(pcm, hint, now, stream))
        elif kind == protocol.TEXT:
            await self._abort_speech(reason="text_input", send_stop=True)
            await self._on_utterance(b"", str(msg.get("text") or ""), now)
        elif kind == protocol.NOTE:
            await self._abort_speech(reason="note_input", send_stop=True)
            await self._capture_note(str(msg.get("text") or ""), source="text", now=now)
        elif kind == protocol.NFC:
            await self._on_nfc(msg, now)
        elif kind == protocol.FOCUS_DONE:         # 设备本地跑完一轮花火 → 记证据 + 播报
            await self._on_focus_done_from_device(_as_int(msg.get("total_s")) or 0, now)
        elif kind == protocol.FOCUS_STATE:        # 设备端按键暂停/继续 → 同步服务端影子计时器
            t = self._focus
            if t is not None:
                if msg.get("paused"):
                    t.pause(now)
                else:
                    t.resume(now)
        elif kind == "word_more":                 # 单词卡翻到末尾 → 要下一批（新随机 30）
            await self._abort_speech(reason="word_more", send_stop=True)
            await self._serve_word(now)
        elif kind == "card_more":                 # 轮播到末尾 → 要下一副（重新打乱）
            await self._abort_speech(reason="card_more", send_stop=True)
            await self._serve_carousel()
        elif kind == protocol.BUTTON:
            # 无论处于任何页面，用户一旦操作按键（切模式、翻页、选菜单、确认等），必须立即中止语音朗读，极速响应操作！
            await self._abort_speech(reason=f"button_{msg.get('name')}", send_stop=True)
            await self._on_button(str(msg.get("name") or ""), now)
        elif kind == protocol.HEARTBEAT:
            self.battery = _as_int(msg.get("battery"))
            self.registry.upsert(self.device_id, battery=self.battery, now=now)
        # 未知 kind：忽略不崩

    # ── 入口：一帧二进制音频 ──
    async def on_binary(self, data: bytes) -> None:
        if self._recording:
            # 超限时丢弃整段，避免内存无限增长或提交半句话。
            if len(self._ptt) + len(data) > 45 * protocol.AUDIO_SAMPLE_RATE * 2:
                self._recording = False
                self._ptt.clear()
                self._ptt_hint = ""
                if self._stt_stream:
                    await self._stt_stream.cancel()
                    self._stt_stream = None
                await self.send_json(protocol.error("audio_too_long", "录音太长，请松开后重试"))
                return
            self._ptt += data
            if self._stt_stream:
                try:
                    self._stt_stream.feed(data)
                except (asyncio.QueueFull, RuntimeError):
                    await self._stt_stream.cancel()
                    self._stt_stream = None

    async def _on_stt_partial(self, text: str) -> None:
        now = time.monotonic()
        if self._recording and self.mode == "chat" and now - self._partial_last >= 1.2:
            self._partial_last = now
            await self.send_json(json.dumps({"kind": "stt_partial", "text": display.screen_text(text[:120])}, ensure_ascii=False))

    # ── 握手 ──
    async def _on_hello(self, msg: Dict[str, Any], now: float) -> None:
        hello = protocol.Hello.parse(msg)
        self.device_id = hello.device_id or hello.mac or ("dev_" + self.session_id)
        # 严格模式：hello 自报的 child_id 只用于比对留痕（registry 里会打 warning），**不参与绑定**。
        dev = self.registry.upsert(self.device_id, mac=hello.mac, fw=hello.fw,
                                   child_id=hello.child_id or None,
                                   battery=hello.battery, now=now)
        self.child_id = dev.child_id
        self.helloed = True
        self.battery = hello.battery
        self._features = tuple(hello.features)     # focus_local：花火番茄钟在设备本地跑秒与绘屏
        if self._unbound():
            # 新设备还没被家长/平板绑定：进本设备专属的隔离身份，不播种、不推早安屏，先提示绑定。
            await self.send_json(protocol.hello_ack(
                self.session_id, self.child_id, sync_period_s=dev.sync_period_s))
            await self._display(display.render_lines("先绑定一下", _UNBOUND_TIP, mode="chat"))
            log.info("pocketclaw 未绑定设备上线 dev=%s → 隔离身份 %s", self.device_id, self.child_id)
            return
        self.notes.ensure_seeded(self.child_id, now)   # 首次使用初始化示例作业
        await self.send_json(protocol.hello_ack(
            self.session_id, self.child_id, sync_period_s=dev.sync_period_s))
        # 早安推屏「今日探索」：放在 hello_ack **之后**才起任务——daily_spark.today() 可能现生成要几秒，
        # 握手回执不能等它；先发完 ack 再起，也保证两帧在 WS 上的先后顺序。
        if self.daily_spark is not None and _MORNING_PUSHED.get(self.child_id) != _day_key(now):
            self._spark_task = asyncio.create_task(self._morning_spark(now))

    # ── 一轮的后台包装：吞掉打断产生的 CancelledError，收尾清引用 ──
    async def _run_turn(self, pcm: bytes, hint: str, now: float, stream=None) -> None:
        try:
            transcript = None
            if stream:
                try:
                    transcript = await stream.finish() or None
                except Exception:
                    log.warning("流式识别失败，使用整段识别")
                finally:
                    await stream.cancel()
            await self._on_utterance(pcm, hint, now, transcript=transcript)
        except asyncio.CancelledError:
            # 被打断：正常路径，不当错误
            raise
        except Exception as e:  # noqa: BLE001 — 一轮内部异常不该拖垮连接
            try:
                await self.send_json(protocol.error("turn_failed", str(e)[:120]))
            except Exception:
                pass
        finally:
            if self._turn_task is asyncio.current_task():
                self._turn_task = None

    # ── 毫秒级打断：取消正在跑的对话轮或语音朗读，并向设备端发送 tts_stop ──
    async def _abort_speech(self, *, reason: str = "user_action", send_stop: bool = True) -> None:
        """立即终止当前的一切朗读与生成，通知设备端静音并清空抖动缓冲（毫秒级打断响应）。"""
        if self._stt_stream:
            await self._stt_stream.cancel()
            self._stt_stream = None
        self._recording = False
        self._ptt.clear()
        self._ptt_hint = ""
        # 1. 取消正在运行的对话轮后台任务（STT -> LLM -> TTS 流式链路）
        t_turn = self._turn_task
        # 语音意图本身可能触发模式切换；此时不要取消或等待当前回合自己。
        if t_turn is not None and t_turn is not asyncio.current_task():
            self._turn_task = None
        if t_turn is not None and t_turn is not asyncio.current_task() and not t_turn.done():
            t_turn.cancel()
            try:
                await t_turn
            except (asyncio.CancelledError, Exception):
                pass

        # 2. 取消正在运行的独立朗读后台任务（非对话式的页面朗读，如句子、单词、课表等）
        t_spk = self._speech_task
        self._speech_task = None
        if t_spk is not None and not t_spk.done():
            t_spk.cancel()
            try:
                await t_spk
            except (asyncio.CancelledError, Exception):
                pass

        # 3. 立即下发 tts_stop 指令给设备端
        if send_stop:
            try:
                await self.send_json(protocol.tts_stop())
            except Exception:
                pass

    # ── 打断：取消正在跑的一轮（含正在发的 TTS），并等它真正结束 ──
    async def _cancel_turn(self) -> None:
        await self._abort_speech(reason="cancel_turn", send_stop=True)

    # ── 一次「话语」= 语音或文本（AI对话/记想法/单词卡 的总入口）──
    async def _on_utterance(self, pcm: bytes, hint: str, now: float, *, transcript: Optional[str] = None) -> None:
        text = transcript if transcript is not None else (await self.stt.transcribe(pcm, hint=hint) if (pcm or hint) else "")
        text = (text or "").strip()
        secs = len(pcm) / 32000.0 if pcm else 0.0
        if not text:
            # 只有录音超过 0.6 秒且没识别出文字时才推提示，短促杂音保持静默
            if pcm and secs >= 0.6:
                await self._speak("我在听呢，再说一遍好不好？")
            return
        await self.send_json(protocol.stt_result(text))    # 识别文本先上屏

        # 睡前时段：直接给休息屏，不跑一轮
        reason = self._quiet_now(now)
        if reason:
            await self._display(display.render_lines("休息一下", reason, mode="chat",
                                                     footer={"battery": self.battery}))
            return

        # 意图路由（设备端不判意图，全在服务端；顺序即优先级）
        # 花火番茄钟/倒计时：计时进行中时「暂停/继续/取消/还剩多久」先于一切；「番茄钟 25 分钟」开新一轮
        if self._focus is not None and (ctl := _focus.parse_focus_control(text)):
            await self._focus_control(ctl, now)
        elif (req := _focus.parse_focus_request(text)) is not None:
            await self._start_focus(req, now)
        elif self.mode == 'chat' and _chat_question(text):
            await self._chat(text, heard=text, now=now)
        elif self.mode == "habit" and (any(k in text for k in ("打卡", "完成", "做完", "取消", "撤销", "下一", "上一")) or any(len(h.name) >= 2 and h.name[:2] in text for h in self.notes.list_habits(self.child_id))):
            await self._handle_habit_voice(text, now)
        elif _startswith_any(text, _ADD_WORD_PREFIX):
            await self._add_word_from_text(text, now)             # 「加单词 X」
        elif _startswith_any(text, _TASK_ADD_PREFIX):
            await self._add_task(_strip_trigger(text, _TASK_ADD_PREFIX), now)   # 「加任务/记个作业 X」
        elif _startswith_any(text, _NOTE_TRIGGERS):
            # 「记一下/记个/备忘 X」= 显式记想法，最高优先——避免 X 里的关键词（作业/单词…）被别的意图抢走
            await self._capture_note(_strip_trigger(text, _NOTE_TRIGGERS), source="voice", now=now)
        elif _startswith_any(text, _DELETE_PREFIX) or _endswith_any(text, _DELETE_SUFFIX):
            # 「删掉 XX」/「把 XX 这个想法去掉」→ 从想法列表删除。放在查词之前，命令压过贪婪关键词
            x = _strip_command_wrapper(_strip_suffix(_strip_trigger(text, _DELETE_PREFIX), _DELETE_SUFFIX))
            await self._delete_note_cmd(x)
        elif _startswith_any(text, _TASK_DONE_PREFIX) or _endswith_any(text, _DONE_SUFFIX):
            # 「完成 XX」/「XX 写完了/做了」→ 先勾作业、再勾想法
            x = _strip_command_wrapper(_strip_suffix(_strip_trigger(text, _TASK_DONE_PREFIX), _DONE_SUFFIX))
            await self._mark_done(x, now)
        elif _contains_any(text, _EXIT_TRIGGERS):
            # 退出当前专项模式回到作业清单（静默返回，不报功能名称）
            self._cancel_chat_idle_timer()
            self.mode = "task"
            await self._show_tasks()
        elif _contains_any(text, _MENU_TRIGGERS):
            self._menu_prev_mode = self.mode
            self.mode = "menu"
            self._menu_idx = 0
            await self._show_menu()
        elif _contains_any(text, _CHAT_COMPANION_TRIGGERS):
            # Once in chat, mentioning the companion or "聊聊" inside a longer
            # sentence is content for the model, not a new mode transition.
            utterance = text.strip().strip('。！？?!，, ')
            if self.mode == 'chat' and utterance not in _CHAT_COMPANION_TRIGGERS and not utterance.startswith(('打开学伴', '开启学伴', '进入学伴', '切换到学伴')):
                await self._chat(text, heard=text, now=now)
            else:
                await self._start_chat_companion(now, speak=False)    # AI学伴模式
        # ── 核心模式切换指令（最高优先，绝不被背诗/评测等模式内部流程吞掉）──
        elif _contains_any(text, _HABIT_TRIGGERS):
            await self._start_habits(now)                         # 好习惯打卡
        elif _contains_any(text, _TIMETABLE_TRIGGERS):
            await self._show_timetable(now)                       # 桌面课程表
        elif _contains_any(text, _IDIOM_TRIGGERS):
            await self._show_daily_idiom(now)                     # 每日国学成语
        elif _contains_any(text, _MISTAKES_TRIGGERS):
            await self._start_mistakes(now)                       # 错题消灭本
        elif _contains_any(text, _GUESS_TRIGGERS):
            await self._start_guess(now)                          # 猜猜看（语音二十问）
        elif _contains_any(text, _FEYNMAN_TRIGGERS):
            await self._start_feynman(now)                        # 费曼讲课（讲给我听）
        elif _contains_any(text, _VERIFY_TRIGGERS):
            q_clean = text
            for trig in _VERIFY_TRIGGERS:
                q_clean = q_clean.replace(trig, "")
            q_clean = q_clean.strip(" ，。？！,?!")
            await self._start_verify(now, query=q_clean)          # 事实核查官（核实一下）
        elif _contains_any(text, _FEIHUA_TRIGGERS):
            await self._start_feihua(now)                         # 诗词飞花令
        elif _contains_any(text, _HANZI_TRIGGERS):
            await self._start_hanzi(now)                          # 错字门诊 / 形近字辨析
        elif _contains_any(text, _SCREENSAVER_TRIGGERS):
            req_mode = None
            if "名画" in text:
                req_mode = "art"
            elif "诗" in text:
                req_mode = "poem"
            elif "倒计时" in text or "考试" in text:
                req_mode = "countdown"
            elif "语录" in text or "名言" in text:
                req_mode = "quote"
            elif "课表" in text:
                req_mode = "timetable"
            await self._start_screensaver(now, mode=req_mode)     # 伴学屏保模式
        elif _contains_any(text, _ARITH_TRIGGERS):
            await self._start_arith(now)                          # 口算大闯关
        elif _contains_any(text, _DICTATION_TRIGGERS):
            await self._start_dictation(now, speak=True)          # 生字词听写官
        elif _contains_any(text, _NEWS_TRIGGERS):
            await self._start_news(now, speak=True)               # 小学新鲜事
        elif _contains_any(text, _VOCAB_TRIGGERS):
            await self._start_vocab(now)                          # 英语生词本
        elif _contains_any(text, _READING_TRIGGERS) or (
            any(w in text for w in ("读", "课文", "文章", "短文", "篇", "reading", "article", "hen", "zoo", "fox", "sunny"))
            and getattr(self, "reading_store", None) and self.reading_store.find_article(text) is not None
        ):
            found = self.reading_store.find_article(text) if getattr(self, "reading_store", None) else None
            art_idx = found[0] if found else None
            await self._start_reading(now, article_idx=art_idx, speak=False)  # 英语分级阅读
        elif _contains_any(text, _ARTICLE_NEXT_TRIGGERS):
            await self._reading_switch_article(+1, now)
        elif _contains_any(text, _ARTICLE_PREV_TRIGGERS):
            await self._reading_switch_article(-1, now)
        elif _startswith_any(text, _RECITE_PREFIX) or (self.mode != "feihua" and not _contains_any(text, _ARITH_TRIGGERS) and practice.find_poem(text) is not None):
            await self._start_recite(text, now)                   # 古诗背诵质检官
        elif _contains_any(text, _WORD_REVIEW):
            self.mode = "word"
            await self._serve_word(now)                           # 背单词 / 单词卡
        elif _contains_any(text, _TASK_SHOW):
            self.mode = "task"
            await self._show_tasks()                              # #11 作业清单（今天作业/我的作业/看作业…）
        # ── 模式专属内部语音交互（在非模式切换指令时生效）──
        elif self.mode == "arith" and getattr(self, "_arith_session", None) and self._arith_session.active:
            # 处于口算大闯关模式：语音答题、看答案、记错题
            await self._handle_arith_speech(text, now)
        elif self.mode == "dictation" and getattr(self, "_dictation_session", None):
            # 处于生字词听写模式：重读、下一题、记错字
            await self._handle_dictation_speech(text, now)
        elif self.mode == "reading":
            # 处于英语文章阅读模式：支持切文章/选句/朗读指令，其余直接作为选定句子的跟读评测
            if _contains_any(text, _ARTICLE_NEXT_TRIGGERS):
                await self._reading_switch_article(+1, now)
            elif _contains_any(text, _ARTICLE_PREV_TRIGGERS):
                await self._reading_switch_article(-1, now)
            elif getattr(self, "reading_store", None) and (found := self.reading_store.find_article(text)) is not None:
                idx, art = found
                await self._start_reading(now, article_idx=idx, speak=False)
            elif _contains_any(text, _READING_NEXT_TRIGGERS):
                await self._reading_step(+1, now)
            elif _contains_any(text, _READING_PREV_TRIGGERS):
                await self._reading_step(-1, now)
            elif _contains_any(text, _READING_REPEAT_TRIGGERS):
                await self._reading_read_selected(now)
            elif any(w in text for w in ("意思", "什么意思", "为什么", "怎么读", "翻译", "是谁", "帮我", "怎么翻译")):
                self.mode = "chat"
                await self._chat(text, heard=text, now=now)
            else:
                await self._reading_evaluate(text, now)
        elif self.mode == "vocab":
            # 处于英语生词本模式，语音指令支持切词/读词，其余为生词发音评测
            if any(w in text for w in ("下一个", "下一词", "下一个词", "下一个单词", "换一个词", "切下一个")):
                await self._vocab_step(+1, now)
            elif any(w in text for w in ("上一个", "上一词", "上一个词", "上一个单词", "切上一个")):
                await self._vocab_step(-1, now)
            elif any(w in text for w in ("读一遍", "发音", "朗读", "念一遍")):
                await self._vocab_speak_current(now)
            elif any(w in text for w in ("意思", "什么意思", "造句", "怎么用", "为什么", "怎么读")):
                self.mode = "chat"
                await self._chat(text, heard=text, now=now)
            else:
                await self._vocab_evaluate(text, now)
        elif self.mode == "guess":
            await self._handle_guess_speech(text, now)
        elif self.mode == "feynman":
            await self._handle_feynman_speech(text, now)
        elif self.mode == "verify":
            await self._handle_verify_speech(text, now)
        elif self.mode == "feihua":
            await self._handle_feihua_speech(text, now)
        elif self.mode == "hanzi":
            await self._handle_hanzi_speech(text, now)
        elif self.mode == "screensaver":
            await self._handle_screensaver_speech(text, now)
        elif self.mode == "idiom":
            if any(w in text for w in ("下一", "换一", "再来一")):
                await self._idiom_step(+1, now)
            elif any(w in text for w in ("上一", "前一个")):
                await self._idiom_step(-1, now)
            elif any(w in text for w in ("读一遍", "朗读", "念一遍", "再读", "再念", "典故")):
                await self._idiom_read_current(now)
            else:
                self.mode = "chat"
                await self._chat(text, heard=text, now=now)
        elif self.mode == "recite" and self._recite_poem is not None:
            # 古诗背诵模式中切诗/选诗/质检
            if _contains_any(text, _POEM_NEXT_TRIGGERS):
                await self._recite_step(+1, now)
            elif _contains_any(text, _POEM_PREV_TRIGGERS):
                await self._recite_step(-1, now)
            elif (p := practice.find_poem(text)) is not None:
                self._recite_poem = p
                self._recite_done_indices = set()
                self.notes.set_poem_target(self.child_id, p.title)
                init_lines = [f"第 {i+1} 句：【 · · · · · 】" for i in range(len(p.lines))]
                page = display.render_recite(p.title, p.author, p.dynasty, init_lines, footer={"battery": self.battery})
                await self._display(page)
            else:
                await self._handle_recitation(text, now)
        elif _contains_any(text, _LOOKUP_TRIGGERS):
            await self._lookup_word(text, now)                    # #1 语音单词卡
        elif _contains_any(text, _CAROUSEL):
            await self._serve_carousel()                          # 随机卡片轮播（古诗/现代诗/名言）
        elif _contains_any(text, _BOOK_NEXT):
            await self._serve_book(now, delta=1)                  # 我的书本：下一章
        elif _contains_any(text, _BOOK_PREV):
            await self._serve_book(now, delta=-1)                 # 我的书本：上一章
        elif _contains_any(text, _BOOK_TRIGGERS):
            await self._serve_book(now)                           # 我的书本：进入阅读（当前章）
        elif _contains_any(text, _SHOW_NOTES):
            await self._list_notes()
        elif self.daily_spark is not None and _contains_any(text, _SPARK_TRIGGERS):
            await self._serve_spark(text, now)                    # 今日探索题：当天的问题轮着交给主脑
        elif self.qbank is not None and _contains_any(text, _MISTAKE_TRIGGERS):
            await self._serve_mistake(text, now)                  # 错题复习：推屏题面 + 苏格拉底式引导
        elif _contains_any(text, _NOTE_TRIGGERS):
            await self._capture_note(_strip_trigger(text, _NOTE_TRIGGERS), source="voice", now=now)  # 兜底
        else:
            self.mode = "chat"
            await self._chat(text, heard=text, now=now)

    # ── AI 对话：复用主脑 runtime.turn（带上下文记忆 + 平板一致的身份/叮嘱框架）──
    async def _chat(self, text: str, *, heard: str, now: float,
                    heard_prefix: str = "你说：", guidance_extra: str = "") -> None:
        """heard_prefix / guidance_extra：服务端替孩子代问（今日探索题、错题复习）时用——
        顶部那行不再叫「你说：」，叮嘱里追加本轮的教学要求（如「苏格拉底式、不给答案」）。"""
        if self._unbound():
            # P0-3：未绑定设备不跑模型回合——runtime.turn 会读写记忆（L3/L4）与逐字，隔离身份也不该攒这些。
            await self._display(display.render_lines("先绑定一下", _UNBOUND_TIP, mode="chat"))
            await self._speak("这台便利贴还没有绑定小朋友，请爸爸妈妈先在手机或平板上绑定一下哦。")
            return
        allowed = render_whitelist(self.grade_band, PROFILE) - {"card"}
        prompt = (f"（当前学科：{self.subject}）{text}") if self.subject else text

        pc = self.controls.get(self.child_id) if self.controls else {}
        guidance = (str(pc.get("guidance") or "").strip() + "\n" + _PC_GUIDANCE).strip()
        if guidance_extra:
            guidance = (guidance + "\n" + guidance_extra.strip()).strip()
        # ── 句子级流式朗读：第一句成型就开始念，不等整段生成完 ──────────────
        # 实测（便利贴）：整段版首声 6.6s（其中 LLM 生成 4.5s 全在干等）。
        # 按句放行后，第一句 ~1.5s 就能出声，孩子不再觉得「它死了」。
        # 与打断的关系：本协程被 _cancel_turn 取消时，_speak 内部会补发 tts_end，
        # 设备不会卡在 Speaking。
        _sent_buf = {"acc": "", "spoken": 0}
        _speak_q: "asyncio.Queue" = asyncio.Queue()
        _SENT_END = "。！？!?…\n"

        async def _on_delta(delta: str):
            _sent_buf["acc"] += delta or ""
            acc = _sent_buf["acc"]
            # 口语回合只念 %%ZH%% 之前的英文：中文意思上屏不念（否则一轮音频翻倍）
            zh = acc.find(_ZH_MARK)
            limit = zh if zh >= 0 else len(acc)
            if _sent_buf["spoken"] >= limit:
                return
            # 从上次已排入的位置起，找到最后一个句末标点，把成型的整句排进朗读队列
            tail = acc[_sent_buf["spoken"]:limit]
            cut = max((tail.rfind(ch) for ch in _SENT_END), default=-1)
            if cut >= 0:
                seg = tail[:cut + 1].strip()
                _sent_buf["spoken"] += cut + 1
                if seg:
                    await _speak_q.put(seg)

        _spk = {"started": False}

        async def _speaker():
            """逐句取出来念。**整轮只发一次 tts_start/tts_end**，中间连续送帧——
            且延迟到第一块音频实际到达时才发 tts_start，杜绝云端 TTS 首包慢导致设备 8000ms 空缓冲超时。"""
            sr = getattr(self.tts, "sample_rate", protocol.AUDIO_SAMPLE_RATE)
            cancelled = False

            async def _ensure_started():
                if not _spk["started"]:
                    await self.send_json(protocol.tts_start(sr))
                    _spk["started"] = True

            try:
                while True:
                    seg = await _speak_q.get()
                    if seg is None:
                        break
                    seg = _clean_reply(seg)
                    if not seg:
                        continue
                    await self._speak_frames(seg, on_first_chunk=_ensure_started)
            except asyncio.CancelledError:
                cancelled = True
                raise
            except Exception:
                try:
                    await self.send_json(protocol.error('tts_unavailable', '语音播报暂时不可用，请稍后再试'))
                except Exception:
                    pass
            finally:
                if _spk["started"] and not cancelled:
                    await self.send_json(protocol.tts_end())   # 正常收尾才发 tts_end；打断时不发 end，由 _abort_speech 发 stop

        # 口语练习模式锁：明确退出 / 闲置超时 → 解锁；否则锁着就强制沿用口语陪练官
        if self._skill_lock and (_contains_any(text, _EN_EXIT) or (now - self._skill_lock_ts) > _EN_LOCK_TTL):
            self._skill_lock = None
        lock = self._skill_lock
        speaker_task = asyncio.create_task(_speaker())
        self._speech_task = speaker_task

        # 动态读取学伴人设配置（支持家长端网页与小程序实时定制）
        comp_cfg = self.notes.get_companion_config(self.child_id) if hasattr(self.notes, "get_companion_config") else {}
        comp_name = comp_cfg.get("name") or LOBSTER_NAME
        comp_persona = comp_cfg.get("persona") or LOBSTER_PERSONA
        comp_style = comp_cfg.get("style") or (str(pc.get("style") or "") or "温柔耐心")
        comp_guidance = guidance if (guidance and guidance != _PC_GUIDANCE) else (comp_cfg.get("guidance") or guidance)

        try:
            turn = await self.runtime.turn(
            prompt, child_id=self.child_id, grade_band=self.grade_band,
            skill_id=lock,                                # ← 锁着时强制口语陪练官，中文回答也不掉出去
            stream_cb=_on_delta,
            client_profile=PROFILE, allowed_types=(allowed or {"svg"}), now=now,
            history="\n".join(self._history),            # ← 上下文记忆（此前一直为空，故答非所问）
            assistant_name=comp_name, persona=comp_persona,
            style=comp_style,
            guidance=comp_guidance,                       # ← 禁脚注/自我说明（曾把教学理由发上屏）
            child_profile=self.notes.get_child_profile(self.child_id) if hasattr(self.notes, 'get_child_profile') else None,
            scaffold=pc.get("scaffold"),
            emotion_enabled=bool(pc.get("emotion_coach", True)),
                quick_answer=bool(pc.get("quick_answer")))
        except BaseException:
            speaker_task.cancel()
            await asyncio.gather(speaker_task, return_exceptions=True)
            if self._speech_task is speaker_task:
                self._speech_task = None
            raise
        answer = _clean_reply(turn.get("text") or "")     # 兜底再清一遍脚注/说明
        # 这一轮进入了口语陪练官 → 上锁并刷新闲置计时；此后孩子用中文答「随便闲聊」也不会掉回常规聊天
        if turn.get("_skill") == _EN_SKILL:
            self._skill_lock, self._skill_lock_ts = _EN_SKILL, now
        # 上屏文本开头加「{comp_name}说：」；语音仍念原文，不念前缀
        display_turn = dict(turn)
        display_turn["text"] = (f"{comp_name}说：" + _display_part(answer)) if answer else answer   # 英文在上、中文在下，不印标记
        page = display.render_turn(display_turn, cols=display.CHAT_COLS,
                                   heard=heard, battery=self.battery, mode="chat",
                                   heard_prefix=heard_prefix)
        await self._display(page)
        # 收尾：把最后没凑成整句的尾巴补上；若这一路流式一句都没排到（模型没走流式），
        # 则整段兜底念一次，保证任何情况下都不会哑。
        streamed_any = _sent_buf["spoken"] > 0
        if streamed_any:
            tail = _spoken_part(_sent_buf["acc"])[_sent_buf["spoken"]:].strip()   # 尾巴同样不越过 %%ZH%%
            if tail:
                await _speak_q.put(tail)
        await _speak_q.put(None)
        if streamed_any or _spk["started"]:
            self._speech_task = speaker_task
        else:
            await self._speak(_spoken_part(answer), wait=False)      # 模型没走流式 → 整段兜底（只念英文部分），保证不哑

        # 记入滚动历史（与平板 recent_chat 同格式），供下一轮上下文
        self._history.append("孩子：" + text[:120])
        self._history.append("助手：" + answer[:120])
        del self._history[:-10]
        if self.mode == "chat":
            self._reset_chat_idle_timer(now)

    # ── 内容策略：今日探索题 + 错题本到期复习（growth/daily_spark.py · qbank/service.py）──
    async def _today_cards(self, now: float) -> list:
        """当天的探索卡：先零成本读缓存，没有再现生成（可能几秒）。任何失败回空，绝不抛到握手/对话路径。"""
        ds = self.daily_spark
        if ds is None:
            return []
        try:
            cards = list(ds.cached_today(self.child_id) or [])
            if not cards:
                cards = list(await ds.today(self.child_id, grade_band=self.grade_band) or [])
        except Exception as e:  # noqa: BLE001
            log.warning("pocketclaw 取今日探索题失败 child=%s: %s", self.child_id, e)
            return []
        return [c for c in cards if isinstance(c, dict) and str(c.get("title") or "").strip()]

    def _due_mistakes(self, now: float) -> list:
        if self.qbank is None:
            return []
        try:
            return list(self.qbank.due_for_review(self.child_id, now) or [])
        except Exception as e:  # noqa: BLE001
            log.warning("pocketclaw 读错题本失败 child=%s: %s", self.child_id, e)
            return []

    async def _morning_spark(self, now: float) -> None:
        """早安推屏：这个孩子今天第一次连上来 → 推一页「今日探索」（一张 daily 卡 + 到期错题数 + 说法提示）。

        跑在 hello 之后的后台任务里。没有 daily 卡（新孩子 / 生成失败）就什么都不推、也不记账；
        睡前时段不推（家长开了时间控制时，孩子半夜摸便利贴不该被问题勾住）。
        """
        try:
            if self._quiet_now(now):
                return
            cards = await self._today_cards(now)
            if not cards:
                log.info("pocketclaw 今日探索无卡，早安不推屏 child=%s", self.child_id)
                return
            idx = _SPARK_CURSOR.get(self.child_id, 0) % len(cards)     # 早上先亮的是「轮到的那张」
            due_n = len(self._due_mistakes(now))
            lt = time.localtime(now)
            page = display.render_spark(cards[idx], due_n=due_n, date_label=f"{lt.tm_mon}月{lt.tm_mday}日",
                                        footer={"battery": self.battery})
            await self._display(page)
            _MORNING_PUSHED[self.child_id] = _day_key(now)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — 早安屏是锦上添花，出错不能影响连接
            log.warning("pocketclaw 早安推屏失败 child=%s: %s", self.child_id, e)
        finally:
            self._spark_task = None

    async def _serve_spark(self, text: str, now: float) -> None:
        """「聊聊这个 / 今天的问题」→ 把轮到的那张探索题当孩子的话交给主脑，三张轮着来。"""
        cards = await self._today_cards(now)
        if not cards:
            # 今天没编出题（新孩子/生成失败）：落回普通对话——主脑自己带 spark_today 工具，能现场编
            await self._chat(text, heard=text, now=now)
            return
        idx = _SPARK_CURSOR.get(self.child_id, 0) % len(cards)
        _SPARK_CURSOR[self.child_id] = idx + 1
        question = str(cards[idx]["title"]).strip()
        await self._chat(
            question, heard=question, now=now, heard_prefix="今日探索：",
            guidance_extra="这是今天为他准备的探索问题，他刚选中它。把它当孩子自己提出来的问题一起聊，"
                           "先问问他怎么想，别一上来就讲完。")

    async def _serve_mistake(self, text: str, now: float) -> None:
        """「错题 / 复习错题 / 考考我错题」→ 取一道到期错题：先推屏题面，再让学伴苏格拉底式引导（不给答案）。"""
        due = self._due_mistakes(now)
        if not due:
            await self._display(display.render_lines("错题本", "今天没有到期的错题，真棒！\n有新题错了记得让平板收进错题本。",
                                                     mode="chat", footer={"battery": self.battery}))
            await self._speak("今天没有到期的错题，真棒！")
            return
        idx = _MISTAKE_CURSOR.get(self.child_id, 0) % len(due)
        _MISTAKE_CURSOR[self.child_id] = idx + 1
        m = due[idx]
        question = str(getattr(m, "question", "") or "").strip()
        subject = str(getattr(m, "subject", "") or "").strip()
        title = "复习错题" + (f" · {subject}" if subject else "") + f"（{idx + 1}/{len(due)}）"
        # 先把题面推上屏（render_lines 按 8 行截断，屏幕放得下），孩子读题的这几秒主脑在想怎么问
        await self._display(display.render_lines(title, question + "\n\n（先说说你打算怎么做）", mode="chat",
                                                 footer={"battery": self.battery, "mistake_id": getattr(m, "id", "")}))
        wrong = str(getattr(m, "wrong_step", "") or "").strip()
        cause = str(getattr(m, "cause", "") or "").strip()
        extra = ("这是孩子错题本里到期复习的一道题，他上次做错了。用苏格拉底式引导：一次只问一个小问题，"
                 "让他自己往下想，**绝不直接给答案、不给完整解法**。")
        if wrong:
            extra += f"他上次错在：{wrong[:80]}。从这一步附近切入提问。"
        if cause:
            extra += f"归因是「{cause}」。"
        await self._chat(f"我想再做一遍错题本里这道题：{question[:200]}", heard=question, now=now,
                         heard_prefix="错题：", guidance_extra=extra)

    # ── 记录想法（孩子私有）──
    async def _capture_note(self, text: str, *, source: str, now: float) -> None:
        text = _strip_note_filler((text or "").strip())   # 去口头 filler（「一个事，」）
        if not text:
            await self._display(display.render_lines("记想法", "想记点什么？说完我帮你记下来。",
                                                     mode="note"))
            return
        text = _annotate_dates(text, now)                  # 相对时间补绝对日期（明天→明天（8/26））
        self.notes.add_note(self.child_id, text, source=source, now=now)
        n = self.notes.note_count(self.child_id)
        body = "已记下：\n" + text + f"\n\n（共 {n} 条想法，随时说「看看想法」）"
        await self._display(display.render_lines("记好啦 ✓", body, mode="note",
                                                 footer={"battery": self.battery}))

    async def _list_notes(self) -> None:
        rows = self.notes.list_notes(self.child_id, limit=6)
        if not rows:
            await self._display(display.render_lines("我的想法", "还没有想法。说「记一下…」试试。", mode="note"))
            return
        # 完成的想法：不可见哨兵 \x01 前缀（设备据此划删除线，避免 ✓ 字符渲染乱码）；
        # 全部统一「· 」圆点、条目之间空一行，排版更清爽。body_rows 放大 → 不裁剪，设备端翻页看全。
        body = "\n\n".join((("\x01· " if r.done else "· ") + r.text) for r in rows)
        await self._display(display.render_lines("我的想法", body, mode="note", body_rows=60,
                                                 footer={"battery": self.battery}))

    # ── 语音单词卡（#1）：按语音查一个词，出卡 + 存进复习盒 ──
    async def _lookup_word(self, text: str, now: float) -> None:
        res = await wordlookup.lookup_word(getattr(self.runtime, "llm", None), text,
                                           grade_band=self.grade_band)
        if not res.get("word"):
            await self._display(display.render_lines("查单词", "想查哪个词？说「apple 什么意思」。", mode="word"))
            return
        # 存进复习盒（去重），之后可「背单词」复习
        card = self.notes.add_word(self.child_id, res["word"], reading=res.get("reading", ""),
                                   meaning=res.get("meaning", ""), example=res.get("example", ""),
                                   example_cn=res.get("example_cn", ""), now=now)
        page = display.render_wordcard(card, footer={"battery": self.battery, "word_id": card.id,
                                                     "saved": True, "resolved": res.get("resolved", True)})
        await self._display(page)
        await self._speak(res["word"] + ("。" + res["meaning"] if res.get("resolved") else ""))

    # ── 单词卡：端上缓存（文本 + 发音全推给设备，上/下键本地翻页 + 本地发音）──
    async def _serve_word(self, now: float) -> None:
        deck = self.notes.list_words(self.child_id)
        if not deck:
            await self._display(display.render_lines("单词卡", "还没有单词。说「apple 什么意思」或「加单词 apple」先收几个。", mode="word"))
            return
        random.shuffle(deck)                          # 每次背单词随机打乱顺序
        deck = deck[:30]                              # 端上一批最多 30 个（翻完向服务端要下一批）
        # ① 先推文本包（小、即时）→ 设备缓存 + 本地翻页立刻可用。固件本地把这四个字段画上屏（RenderWordLocal），
        #    所以同样先按字库清洗（display.screen_text）；发音在下面按原词合成，不受影响。
        _st = display.screen_text
        pack = {"kind": "word_pack",
                "words": [{"w": _st(c.word), "m": _st(c.meaning), "e": _st(c.example), "ec": _st(c.example_cn)}
                          for c in deck]}
        await self.send_json(json.dumps(pack, ensure_ascii=False))
        # ② 逐词合成发音并推送（设备缓存）；服务端缓存避免重复合成，随后进度式到达
        for i, c in enumerate(deck):
            pcm = await self._word_audio(c.word)
            if not pcm:
                continue
            await self.send_json(json.dumps({"kind": "word_audio", "idx": i}))
            for chunk in protocol.frame_pcm(pcm):
                await self.send_bin(chunk)
            await self.send_json(json.dumps({"kind": "word_audio_end", "idx": i}))

    # ── 随机卡片轮播（端上缓存：整副卡推给设备，本地 1 分钟自动切 + 上/下键切）──
    async def _serve_carousel(self) -> None:
        deck = cards.build_deck()
        random.shuffle(deck)                          # 每次随机打乱顺序
        # ① 文字包（名画卡 lines 空，只带 type/title/footer）——固件本地画上屏，先按字库清洗
        _st = display.screen_text
        pack_cards = [{"type": c["type"], "title": _st(c["title"]),
                       "footer": _st(c["footer"]), "lines": [_st(x) for x in c["lines"]]} for c in deck]
        await self.send_json(json.dumps({"kind": "card_pack", "cards": pack_cards}, ensure_ascii=False))
        # ② 名画位图按其在牌堆里的下标逐张流式推送 → 设备缓存到对应 idx
        for i, c in enumerate(deck):
            if c.get("type") == "painting":
                bmp = cards.painting_bitmap(c.get("image", ""))
                if not bmp:
                    continue
                await self.send_json(json.dumps({"kind": "card_image", "idx": i, "w": 400, "h": 300}))
                for off in range(0, len(bmp), 4096):
                    await self.send_bin(bmp[off:off + 4096])
                await self.send_json(json.dumps({"kind": "card_image_end", "idx": i}))

    # ── 我的书本：奇阅书房最近在看那章，墨水屏分页显示 + 朗读；上/下键走固件长文本翻页 ──
    async def _serve_book(self, now: float, *, delta: int = 0) -> None:
        if not self.books:
            await self._display(display.render_lines("我的书本", "书本功能未启用。", mode="chat"))
            return
        data = self.books.latest_chapter_pages(self.child_id)
        if not data or not data.get("pages"):
            await self._display(display.render_lines(
                "我的书本",
                "还没有在看的书～让爸爸妈妈在小程序里传一本，或在平板「奇阅书房」选一本，这里就能读啦。",
                mode="chat"))
            return
        if delta:                                     # 上/下一章：翻章后重取当前章
            if self.books.turn_chapter(self.child_id, data["book_id"], delta) is not None:
                data = self.books.latest_chapter_pages(self.child_id) or data
        title = f"{data.get('title','书')} · 第{int(data.get('chapter',0)) + 1}/{data.get('chapter_count',1)}章"
        ct = str(data.get("chapter_title") or "").strip()
        if ct:
            title += " " + ct
        text = "".join(data.get("pages") or [])
        hint = "上/下键翻页" + ("；说「下一章」继续" if data.get("has_next") else "；这本读完啦")
        body = text + "\n\n（" + hint + "）"
        # body_rows 放大 → 服务端不裁剪，整章推给设备，由固件长文本翻页（page_lines_）本地上/下翻
        await self._display(display.render_lines(title, body, mode="chat", body_rows=400))
        await self._speak(text)                       # 语音进入即朗读本章
        try:                                          # 进度落库 → 平板/其它端下次打开落在同一章
            self.books.set_position(self.child_id, data["book_id"], int(data.get("chapter", 0)))
        except Exception:  # noqa: BLE001
            pass

    async def _word_audio(self, word: str) -> bytes:
        """合成一个词的发音 PCM，服务端按词缓存，避免每次背单词都重合成。"""
        key = (word or "").strip().lower()
        if not key:
            return b""
        if key in _WORD_AUDIO_CACHE:
            return _WORD_AUDIO_CACHE[key]
        try:
            pcm = await self.tts.synthesize(word)
        except Exception:
            pcm = b""
        if pcm:
            _WORD_AUDIO_CACHE[key] = pcm
        return pcm

    async def _add_word_from_text(self, text: str, now: float) -> None:
        rest = _strip_trigger(text, _ADD_WORD_PREFIX).strip()
        if not rest:
            await self._display(display.render_lines("加单词", "说「加单词 apple」就能把它收进单词卡。", mode="word"))
            return
        parts = rest.split(None, 1)
        word = parts[0]
        meaning = parts[1] if len(parts) > 1 else ""
        self.notes.add_word(self.child_id, word, meaning=meaning, now=now)
        await self._display(display.render_lines("收好啦 ✓", f"{word} 已加入单词卡\n说「背单词」开始复习。",
                                                 mode="word", footer={"battery": self.battery}))

    # ── 作业清单（#11）：支持上下键光标选行 + 确认键一键打勾 ──
    async def _show_tasks(self, *, partial: bool = False) -> None:
        self._cancel_chat_idle_timer()
        self.mode = "task"
        rows = self.notes.list_tasks(self.child_id)
        if not rows:
            self._task_idx = -1
        elif self._task_idx >= len(rows):
            self._task_idx = len(rows) - 1
        items = [(t.text, t.done) for t in rows]
        await self._display(display.render_checklist("今日作业清单", items,
                                                     sel_idx=self._task_idx,
                                                     footer={"battery": self.battery},
                                                     partial=partial))

    async def _task_step(self, delta: int, now: float) -> None:
        """作业清单上下键移动选中光标（局刷不闪屏）。"""
        self.mode = "task"
        rows = self.notes.list_tasks(self.child_id)
        if not rows:
            self._task_idx = -1
            await self._show_tasks(partial=True)
            return
        if self._task_idx < 0:
            self._task_idx = 0 if delta > 0 else len(rows) - 1
        else:
            self._task_idx = (self._task_idx + delta) % len(rows)
        await self._show_tasks(partial=True)

    async def _complete_next_task(self, now: float) -> None:
        """短按确认键：若已用上下键选中某项则完成该项，否则勾掉第一个未完成作业，伴随庆祝语音。"""
        self.mode = "task"
        rows = self.notes.list_tasks(self.child_id)
        todos = self.notes.list_tasks(self.child_id, include_done=False)

        target = None
        if 0 <= self._task_idx < len(rows):
            chosen = rows[self._task_idx]
            if not chosen.done:
                target = chosen
            else:
                # 选中的是已完成项 → 取消光标选中并刷新
                self._task_idx = -1
                await self._show_tasks(partial=True)
                await self._speak(f"这项「{chosen.text}」已经完成啦！")
                return
        elif todos:
            target = todos[0]

        if target is not None:
            self.notes.complete_task(self.child_id, task_id=target.id, now=now)
            rem = max(0, len(todos) - 1)
            if rem == 0:
                self._task_idx = -1
                updated_rows = self.notes.list_tasks(self.child_id)
                await self._display(display.render_checklist("今日作业清单", [(t.text, t.done) for t in updated_rows],
                                                             sel_idx=-1,
                                                             footer={"battery": self.battery, "all_done": True},
                                                             partial=True))
                await self._speak("太棒啦！今天的所有作业全都搞定啦！给你点赞！")
            else:
                if self._task_idx >= 0:
                    self._task_idx = min(self._task_idx, rem - 1)
                await self._show_tasks(partial=True)
                await self._speak(f"搞定了一项：{target.text}！还剩 {rem} 项，继续加油！")
        else:
            await self._show_tasks(partial=True)
            await self._speak("今天没有待完成的作业啦，快去休息一下吧！")

    # ── 小学生专项：古诗背诵质检官 ──
    async def _start_recite(self, text: str, now: float, *, speak: bool = False) -> None:
        target = text or self.notes.get_poem_target(self.child_id)
        custom = self.notes.list_custom_poems(self.child_id) if hasattr(self.notes, 'list_custom_poems') else []
        poem = practice.find_poem(target, custom) or random.choice(practice.CORE_POEMS)
        self._recite_poem = poem
        self._recite_done_indices = set()
        self.mode = "recite"
        init_lines = [f"第 {i+1} 句：【 · · · · · 】" for i in range(len(poem.lines))]
        page = display.render_recite(poem.title, poem.author, poem.dynasty, init_lines,
                                     footer={"battery": self.battery})
        await self._display(page)
        if speak or text:
            await self._speak(f"准备好背诵《{poem.title}》了吗？{poem.author}〔{poem.dynasty}〕。按住中间键开始背诵吧！")

    async def _recite_step(self, delta: int, now: float) -> None:
        custom = self.notes.list_custom_poems(self.child_id) if hasattr(self.notes, 'list_custom_poems') else []
        extra = [practice.Poem(title=p['title'], author=p.get('author', ''), dynasty=p.get('dynasty', ''), lines=p.get('lines', [])) for p in custom if p.get('title') and p.get('lines')]
        titles = {p.title for p in extra}
        pool = extra + [p for p in practice.CORE_POEMS if p.title not in titles]
        if not self._recite_poem:
            target = self.notes.get_poem_target(self.child_id)
            self._recite_poem = practice.find_poem(target, custom) or pool[0]
        cur_idx = 0
        for i, p in enumerate(pool):
            if p.title == self._recite_poem.title:
                cur_idx = i
                break
        next_idx = (cur_idx + delta) % len(pool)
        poem = pool[next_idx]
        self._recite_poem = poem
        self._recite_done_indices = set()
        self.notes.set_poem_target(self.child_id, poem.title)
        init_lines = [f"第 {i+1} 句：【 · · · · · 】" for i in range(len(poem.lines))]
        page = display.render_recite(poem.title, poem.author, poem.dynasty, init_lines,
                                     footer={"battery": self.battery}, partial=True)
        await self._display(page)

    async def _handle_recitation(self, text: str, now: float) -> None:
        poem = self._recite_poem
        if not poem:
            await self._start_recite("", now)
            return
        res = practice.check_recitation(poem, text, done_indices=self._recite_done_indices)
        self._recite_done_indices.update(res.matched_indices)
        if res.passed:
            self._recite_done_indices = set()
            page = display.render_recite(poem.title, poem.author, poem.dynasty, res.revealed_lines,
                                         passed=True, footer={"battery": self.battery})
            await self._display(page)
            await self._speak(res.feedback)
            # 自动完成作业清单中的背诗任务
            for t in self.notes.list_tasks(self.child_id, include_done=False):
                if "背" in t.text or "诗" in t.text:
                    self.notes.complete_task(self.child_id, task_id=t.id, now=now)
                    break
            self.mode = "task"
        else:
            page = display.render_recite(poem.title, poem.author, poem.dynasty, res.revealed_lines,
                                         prompt=res.feedback, footer={"battery": self.battery})
            await self._display(page)
            await self._speak(res.feedback)

    # ── 小学生专项：生字词听写考官 ──
    async def _start_dictation(self, now: float, *, speak: bool = True) -> None:
        custom_words = self.notes.get_dictation_words(self.child_id)
        words = custom_words if custom_words else practice.DEFAULT_DICTATION_WORDS
        title = self.notes.get_dictation_title(self.child_id) if hasattr(self.notes, "get_dictation_title") else "生字词听写"
        self._dictation_session = practice.DictationSession(words, title=title)
        self.mode = "dictation"
        cw = self._dictation_session.cur_word
        w_len = len(cw[0]) if cw else 2
        page = display.render_dictation(title, 1, self._dictation_session.total,
                                        word_len=w_len,
                                        footer={"battery": self.battery})
        await self._display(page)
        if speak:
            speech = self._dictation_session.prompt_speech()
            await self._speak(speech)

    async def _dictation_step(self, now: float) -> None:
        if not self._dictation_session:
            await self._start_dictation(now)
            return
        has_next = self._dictation_session.step_next()
        if has_next:
            cw = self._dictation_session.cur_word
            w_len = len(cw[0]) if cw else 2
            page = display.render_dictation(self._dictation_session.title,
                                            self._dictation_session.idx + 1,
                                            self._dictation_session.total,
                                            word_len=w_len,
                                            footer={"battery": self.battery},
                                            partial=True)
            await self._display(page)
            await self._speak(self._dictation_session.prompt_speech())
        else:
            title = self._dictation_session.title
            words = list(self._dictation_session.words)
            page = display.render_dictation_summary(f"{title} · 对照清单", words,
                                                    footer={"battery": self.battery})
            await self._display(page)
            await self._speak("生字全部听写完啦！已生成对照清单，请把练习本和屏幕交给家长检查批改吧！")
            
            # 记录一条待家长检查的听写记录
            if hasattr(self.notes, "add_dictation_record"):
                self.notes.add_dictation_record(self.child_id, title=title, words=words, now=now)

            for t in self.notes.list_tasks(self.child_id, include_done=False):
                if "听写" in t.text:
                    self.notes.complete_task(self.child_id, task_id=t.id, now=now)
                    break

    async def _dictation_repeat(self, now: float) -> None:
        if not self._dictation_session or not self._dictation_session.active:
            return
        await self._speak(self._dictation_session.prompt_speech(repeat=True))

    # ── 小学生专项：口算大闯关 ──
    async def _start_arith(self, now: float, *, speak: bool = False) -> None:
        cfg = self.notes.get_arith_config(self.child_id) if hasattr(self.notes, "get_arith_config") else {}
        cfg = cfg or {}
        cnt = max(5, min(50, int(cfg.get("count", 10) or 10)))
        grade = int(cfg.get("grade", 2) or 2)
        op_type = str(cfg.get("op_type", "all") or "all")
        self._arith_session = practice.ArithSession(count=cnt, grade=grade, op_type=op_type)
        self.mode = "arith"
        q = self._arith_session.cur_question
        expr = q.expr if q else "10 + 10"
        page = display.render_arith(expr, answer="", revealed=False, cur_idx=1, total=cnt,
                                    footer={"battery": self.battery})
        await self._display(page)
        if speak:
            await self._speak(f"口算大闯关开始！第 1 题：{expr} 等于多少？在草稿纸速算，按中间键核对答案！")

    async def _arith_step(self, now: float) -> None:
        if not self._arith_session or not self._arith_session.active:
            await self._start_arith(now)
            return
        was_revealed = self._arith_session.revealed
        stepped, msg = self._arith_session.reveal_or_step()
        if not was_revealed:
            # 刚揭晓答案
            q = self._arith_session.cur_question
            expr = q.expr if q else ""
            ans = q.ans if q else ""
            page = display.render_arith(expr, answer=ans, revealed=True,
                                        cur_idx=self._arith_session.idx + 1, total=self._arith_session.count,
                                        footer={"battery": self.battery}, partial=True)
            await self._display(page)
            await self._speak(msg)
        else:
            # 步进到下一题或通关
            if not self._arith_session.active:
                for t in self.notes.list_tasks(self.child_id, include_done=False):
                    if "口算" in t.text or "数学" in t.text:
                        self.notes.complete_task(self.child_id, task_id=t.id, now=now)
                        break
                self.mode = "task"
                await self._show_tasks()
                await self._speak(msg)
            else:
                q = self._arith_session.cur_question
                expr = q.expr if q else ""
                page = display.render_arith(expr, answer="", revealed=False,
                                            cur_idx=self._arith_session.idx + 1, total=self._arith_session.count,
                                            footer={"battery": self.battery}, partial=True)
                await self._display(page)
                await self._speak(f"第 {self._arith_session.idx + 1} 题：{expr} 等于多少？")

    async def _handle_arith_speech(self, text: str, now: float) -> None:
        if not self._arith_session or not self._arith_session.active:
            return
        clean = text.strip()
        if any(w in clean for w in ("看答案", "不知道", "揭晓", "公布答案")):
            if not self._arith_session.revealed:
                await self._arith_step(now)
            return

        if any(w in clean for w in ("下一题", "下一道", "下一个")):
            if not self._arith_session.revealed:
                await self._arith_step(now)
            await self._arith_step(now)
            return

        if any(w in clean for w in ("算错", "做错", "没算对", "记错题")):
            q = self._arith_session.cur_question
            if q:
                self.notes.add_mistake(self.child_id, category="arith", question=q.expr,
                                      expected=q.ans, wrong_answer="算错", now=now)
            await self._speak("已为你记入错题消灭本！稍后专项消灭！继续看下一题！")
            self._arith_session.revealed = True
            await self._arith_step(now)
            return

        val = practice.parse_spoken_number(clean)
        if val is not None:
            q = self._arith_session.cur_question
            if q:
                try:
                    exp_val = int(q.ans)
                except ValueError:
                    exp_val = None
                if exp_val is not None and val == exp_val:
                    self._arith_session.revealed = True
                    self.notes.add_stars(self.child_id, 1, reason=f"口算答对: {q.expr}")
                    page = display.render_arith(q.expr, answer=q.ans, revealed=True,
                                                cur_idx=self._arith_session.idx + 1, total=self._arith_session.count,
                                                footer={"battery": self.battery})
                    await self._display(page)
                    await self._speak(f"太棒啦！回答正确！{q.expr} 等于 {q.ans}！奖励 1 颗星星！按确认键下一题！")
                else:
                    self._arith_session.revealed = True
                    self.notes.add_mistake(self.child_id, category="arith", question=q.expr,
                                          expected=q.ans, wrong_answer=str(val), now=now)
                    page = display.render_arith(q.expr, answer=q.ans, revealed=True,
                                                cur_idx=self._arith_session.idx + 1, total=self._arith_session.count,
                                                footer={"battery": self.battery})
                    await self._display(page)
                    await self._speak(f"哎呀算错啦，你说的是 {val}，正确答案是 {q.ans}。已自动收录进错题消灭本！按确认键下一题！")
            return

        q = self._arith_session.cur_question
        expr = q.expr if q else ""
        await self._speak(f"当前这道题是：{expr} 等于多少？请直接说出答案或按中间键核对！")

    async def _handle_dictation_speech(self, text: str, now: float) -> None:
        if not self._dictation_session:
            return
        clean = text.strip()
        if any(w in clean for w in ("没听清", "重读", "再读一遍", "再念一遍", "再播一遍", "念一遍", "读一遍", "重复一遍", "再说一遍")):
            await self._dictation_repeat(now)
            return
        if any(w in clean for w in ("写好", "写完", "下一个", "下一题", "下一首", "过了", "跳过", "下一个词", "下一个字", "下一个生字")):
            await self._dictation_step(now)
            return
        if any(w in clean for w in ("写错", "不会写", "记错字", "错词")):
            cw = self._dictation_session.cur_word
            if cw:
                word = cw[0]
                pinyin = cw[1] if len(cw) > 1 else ""
                self.notes.add_mistake(self.child_id, category="dictation", question=word,
                                      expected=pinyin, wrong_answer="写错/未掌握", now=now)
                await self._speak(f"已把生字【{word}】收录进错题消灭本，稍后对照订正！")
                return
        await self._speak("生字听写中。写好后请按确认键或说“写好了”进入下一个词，没听清可以说“再读一遍”。")

    # ── 家长留言推屏便签 ──
    async def _show_parent_message(self, text: str, ts_str: str = "") -> None:
        self.mode = "parent_note"
        page = display.render_parent_message(text, ts_str, footer={"battery": self.battery})
        await self._display(page)
        await self._speak("你有一条来自爸爸妈妈的温馨留言。")

    # ── 功能导航菜单与模式切换 ──
    async def _show_menu(self, *, partial: bool = False) -> None:
        self._stop_news_scroll()
        await self._abort_speech(reason="show_menu", send_stop=True)
        self.mode = "menu"
        items = self.notes.get_sorted_menu_items(self.child_id)
        page = display.render_menu(self._menu_idx, items=items, footer={"battery": self.battery}, partial=partial)
        await self._display(page)

    async def _switch_to_mode(self, mode: str, now: float) -> None:
        if mode not in ("task", "menu") and not self.notes.is_feature_enabled(self.child_id, mode):
            log.info("模式 %s 已被家长停用，拒绝切入", mode)
            await self._speak("该功能已被家长暂时停用，请先专心完成其他学习任务哦！")
            return
        await self._abort_speech(reason=f"switch_to_{mode}", send_stop=True)
        if self.mode == "news" and mode != "news":
            self._stop_news_scroll()
        if self.mode == "chat" and mode != "chat":
            self._cancel_chat_idle_timer()
        if mode != "focus" and self._focus is not None:
            await self._stop_focus_task()
            self._focus = None
            if self._focus_local:
                await self.send_json(protocol.focus_stop())
        self.mode = mode
        if mode == "task":
            await self._show_tasks()
        elif mode == "chat":
            await self._start_chat_companion(now, speak=False)
        elif mode == "habit":
            await self._start_habits(now, speak=False)
        elif mode == "timetable":
            await self._show_timetable(now, speak=False)
        elif mode == "idiom":
            await self._show_daily_idiom(now, speak=False)
        elif mode == "recite":
            await self._start_recite("", now, speak=False)
        elif mode == "dictation":
            await self._start_dictation(now, speak=True)
        elif mode == "arith":
            await self._start_arith(now, speak=False)
        elif mode == "mistakes":
            await self._start_mistakes(now, speak=False)
        elif mode == "vocab":
            await self._start_vocab(now, speak=False)
        elif mode == "reading":
            await self._start_reading(now, speak=False)
        elif mode == "feynman":
            await self._start_feynman(now, speak=False)
        elif mode == "guess":
            await self._start_guess(now, speak=False)
        elif mode == "verify":
            await self._start_verify(now, speak=False)
        elif mode == "feihua":
            await self._start_feihua(now, speak=False)
        elif mode == "hanzi":
            await self._start_hanzi(now, speak=False)
        elif mode == "news":
            await self._start_news(now, speak=False)
        elif mode == "screensaver":
            await self._start_screensaver(now, speak=False)
        elif mode == "focus":
            await self._start_focus({"kind": "pomodoro", "minutes": 25, "style": "spark"}, now)
        elif mode == "menu":
            self._menu_idx = 0
            await self._show_menu(partial=False)

    # ── AI学伴（智能对话伴学·答疑解惑谈心）──
    async def _start_chat_companion(self, now: float, *, speak: bool = False) -> None:
        self.mode = "chat"
        self._reset_chat_idle_timer(now)
        page = display.render_chat_companion_welcome(battery=self.battery)
        await self._display(page)
        if speak:
            await self._speak("我是你的专属AI学伴小龙虾。遇到难题或想聊天，随时按住中间键告诉我吧！")

    # ── 移植自 SnapClaw：猜猜看（语音二十问）──
    async def _start_guess(self, now: float, *, speak: bool = False) -> None:
        self.mode = "guess"
        self._guess_session = practice.GuessSession()
        page = display.render_guess(0, 20, [], status="playing", footer={"battery": self.battery})
        await self._display(page)
        if speak:
            await self._speak("猜猜看！我想好了一种神秘事物，你可以按住中间键问我是非问题，我只回答是或者不是哦！")

    async def _handle_guess_speech(self, text: str, now: float) -> None:
        if not self._guess_session or self._guess_session.status in ("win", "over"):
            await self._start_guess(now)
            return

        sess = self._guess_session
        model_answer = None
        # 若未直接猜中，且配置了 LLM，则优先调用大模型精准科学判定孩子的问题
        if not sess.check_guess(text) and getattr(self.runtime, "llm", None):
            try:
                sys_prompt = (
                    "你正在陪小学生玩「猜猜看（20个问题猜事物）」互动游戏。\n"
                    f"现在的秘密谜底是：【{sess.secret}】（分类：{sess.category}）。\n"
                    "孩子正在向你提问以推理出这个谜底。\n"
                    "请根据谜底的真实科学与生活常识，简短生动（25字以内）回答孩子的问题：\n"
                    "1. 绝对不要直接说出谜底的名字！\n"
                    "2. 明确回答「是的」或「不是」，并附带一句儿童友好的生动事实特征解释。\n"
                    "3. 如果问题是直接猜测谜底（但不是本谜底），明确告知「不是哦」。"
                )
                llm_out = await asyncio.wait_for(
                    self.runtime.llm.complete(sys_prompt, f"孩子提问：{text}", max_tokens=100),
                    timeout=2.5
                )
                if llm_out and len(llm_out.strip()) >= 2:
                    model_answer = llm_out.strip()
            except Exception as e:
                log.debug("LLM guess answer fallback: %s", e)

        reply, is_win = sess.answer_question(text, model_answer=model_answer)
        page = display.render_guess(
            asked_cnt=len(sess.history),
            max_cnt=sess.max_questions,
            history=sess.history,
            status=sess.status,
            secret=sess.secret,
            hint=sess.hint,
            footer={"battery": self.battery},
            partial=True
        )
        await self._display(page)
        if is_win:
            self.notes.add_stars(self.child_id, 2, reason=f"猜猜看通关({sess.secret})", now=now)
        await self._speak(reply)

    # ── 移植自 SnapClaw：费曼小老师（讲给我听）──
    async def _start_feynman(self, now: float, *, speak: bool = False) -> None:
        self.mode = "feynman"
        self._feynman_session = practice.FeynmanSession()
        page = display.render_feynman(1, 3, [], footer={"battery": self.battery})
        await self._display(page)
        if speak:
            await self._speak("小萌新认真听讲中！小老师，今天在学校学了什么新知识？按住中间键开始给我讲课吧！")

    async def _handle_feynman_speech(self, text: str, now: float) -> None:
        if not self._feynman_session or self._feynman_session.summary:
            await self._start_feynman(now)
            return

        sess = self._feynman_session
        round_idx = len(sess.rounds) + 1
        reply, is_done = sess.add_round(text)
        page = display.render_feynman(
            round_idx=round_idx,
            max_rounds=sess.max_rounds,
            current_dialogue=sess.rounds,
            summary=sess.summary,
            footer={"battery": self.battery},
            partial=True
        )
        await self._display(page)
        if is_done and sess.summary:
            self.notes.add_stars(self.child_id, 2, reason="费曼小老师讲课结业", now=now)
            self.notes.add_note(self.child_id, f"【费曼小老师讲课作品】课题：《{sess.summary.topic}》\n讲得好：{sess.summary.good_point}\n再思考：{sess.summary.think_point}", tag="费曼讲课", now=now)
        await self._speak(reply)

    # ── 移植自 SnapClaw：事实核查官（核实一下）──
    async def _start_verify(self, now: float, query: str = "", *, speak: bool = False) -> None:
        self.mode = "verify"
        if query:
            await self._handle_verify_speech(query, now)
            return
        self._last_verify_result = None
        page = display.render_verify("", "", "", "", footer={"battery": self.battery})
        await self._display(page)
        if speak:
            await self._speak("事实核查官已就绪！按住中间键说一句你想核实的话或算式吧！")

    async def _handle_verify_speech(self, text: str, now: float) -> None:
        self.mode = "verify"
        res = practice.verify_claim_direct(text)
        # 若内置规则库未命中（unsure），且配置了 LLM，则由大模型开展少儿科学事实核查
        if res.get("verdict") == "unsure" and getattr(self.runtime, "llm", None):
            try:
                sys_prompt = (
                    "你是一位面向小学生的事实核查官与少儿科学导师。请对孩子的陈述进行严谨通俗的科学核实。\n"
                    "请严格按以下 JSON 格式输出，不要输出任何 markdown 标签或多余文字：\n"
                    "{\n"
                    '  "verdict": "ok" 或 "wrong" 或 "unsure",\n'
                    '  "verdict_text": "核实：说法正确！" 或 "核实：说法有误！" 或 "核实：说法存疑/不准确",\n'
                    '  "note": "40字以内给小学生讲清楚科学事实和原因",\n'
                    '  "how": "25字以内教孩子一种自己验证或查阅的方法"\n'
                    "}"
                )
                llm_out = await asyncio.wait_for(
                    self.runtime.llm.complete(sys_prompt, f"孩子提出的陈述：{text}", max_tokens=150),
                    timeout=3.0
                )
                if llm_out:
                    clean_json = re.sub(r"^```(?:json)?\s*", "", llm_out.strip())
                    clean_json = re.sub(r"\s*```$", "", clean_json)
                    parsed = json.loads(clean_json)
                    if isinstance(parsed, dict) and "verdict" in parsed:
                        res["verdict"] = parsed.get("verdict", "unsure")
                        res["verdict_icon"] = "✅" if res["verdict"] == "ok" else ("❌" if res["verdict"] == "wrong" else "❓")
                        res["verdict_text"] = parsed.get("verdict_text", "核实结果")
                        res["note"] = parsed.get("note", res.get("note", ""))
                        res["how"] = parsed.get("how", res.get("how", ""))
            except Exception as e:
                log.debug("LLM verify fallback: %s", e)

        self._last_verify_result = {"query": text, **res}
        page = display.render_verify(
            query=text,
            verdict=res.get("verdict", "unsure"),
            note=res.get("note", ""),
            how=res.get("how", ""),
            footer={"battery": self.battery},
            partial=True
        )
        await self._display(page)
        v_say = res.get("verdict_text", "")
        note_say = res.get("note", "")
        how_say = res.get("how", "")
        await self._speak(f"{v_say}。{note_say}。教你核实：{how_say}")

    # ── 小学生拓展功能 1：诗词飞花令（人机诗词对战）──
    async def _start_feihua(self, now: float, keyword: Optional[str] = None, *, speak: bool = False) -> None:
        self.mode = "feihua"
        self._feihua_session = practice.FeihuaSession(keyword=keyword)
        kw = self._feihua_session.keyword
        page = display.render_feihua(kw, 0, self._feihua_session.target_rounds, [],
                                     status="playing", footer={"battery": self.battery})
        await self._display(page)
        if speak:
            await self._speak(f"诗词飞花令开始啦！本局令字是「{kw}」！请按住中间键，说出一句带有「{kw}」字的古诗吧！")

    async def _handle_feihua_speech(self, text: str, now: float) -> None:
        if not self._feihua_session or self._feihua_session.status in ("win", "giveup"):
            await self._start_feihua(now)
            return

        sess = self._feihua_session
        valid, bot_line, feedback = sess.check_verse(text)

        page = display.render_feihua(
            keyword=sess.keyword,
            round_idx=sess.rounds_done,
            target_rounds=sess.target_rounds,
            history=sess.history,
            status=sess.status,
            footer={"battery": self.battery},
            partial=True
        )
        await self._display(page)

        if valid:
            if sess.status == "win":
                self.notes.add_stars(self.child_id, 2, reason=f"诗词飞花令获胜(令:{sess.keyword})")
                await self._speak(f"{feedback}恭喜小才子通关飞花令，奖励2颗自律星星！")
            else:
                await self._speak(f"{feedback}“{bot_line}”！轮到你啦，请再说一句带「{sess.keyword}」的诗！")
        else:
            await self._speak(feedback)

    async def _feihua_hint(self, now: float) -> None:
        if not self._feihua_session:
            return
        hint_text = self._feihua_session.give_hint()
        page = display.render_feihua(
            keyword=self._feihua_session.keyword,
            round_idx=self._feihua_session.rounds_done,
            target_rounds=self._feihua_session.target_rounds,
            history=self._feihua_session.history,
            status=self._feihua_session.status,
            hint=hint_text,
            footer={"battery": self.battery},
            partial=True
        )
        await self._display(page)
        await self._speak(hint_text)

    async def _feihua_giveup(self, now: float) -> None:
        if not self._feihua_session:
            return
        self._feihua_session.status = "giveup"
        page = display.render_feihua(
            keyword=self._feihua_session.keyword,
            round_idx=self._feihua_session.rounds_done,
            target_rounds=self._feihua_session.target_rounds,
            history=self._feihua_session.history,
            status="giveup",
            footer={"battery": self.battery},
            partial=True
        )
        await self._display(page)
        await self._speak(f"胜败乃兵家常事！本局令字是「{self._feihua_session.keyword}」，按向下键可以换个令字重新挑战哦！")

    async def _feihua_next_keyword(self, now: float) -> None:
        kw_list = list(practice.FEIHUA_DB.keys())
        cur_kw = self._feihua_session.keyword if self._feihua_session else ""
        try:
            idx = kw_list.index(cur_kw)
            next_kw = kw_list[(idx + 1) % len(kw_list)]
        except ValueError:
            next_kw = kw_list[0] if kw_list else "春"
        await self._start_feihua(now, keyword=next_kw, speak=True)

    # ── 小学生拓展功能 2：错字门诊 / 形近字辨析 ──
    async def _start_hanzi(self, now: float, *, speak: bool = False) -> None:
        self.mode = "hanzi"
        self._hanzi_session = practice.HanziSession()
        curr = self._hanzi_session.current
        page = display.render_hanzi(
            question_idx=self._hanzi_session.idx,
            total_questions=self._hanzi_session.total,
            stem=curr["stem"],
            opts=curr["opts"],
            selected_opt_idx=self._hanzi_session.selected_opt_idx,
            revealed=False,
            footer={"battery": self.battery}
        )
        await self._display(page)
        if speak:
            await self._speak(f"错字门诊开诊啦！第1题：{curr['stem']}。请按上下键选择，或者按住中间键直接说出你的答案！")

    async def _handle_hanzi_speech(self, text: str, now: float) -> None:
        if not self._hanzi_session:
            await self._start_hanzi(now)
            return
        if self._hanzi_session.revealed:
            await self._hanzi_next(now)
            return

        is_corr, speech, curr = self._hanzi_session.confirm_answer(user_voice=text)
        await self._render_hanzi_revealed(is_corr, speech, curr, now)

    async def _hanzi_toggle(self, now: float) -> None:
        if not self._hanzi_session:
            return
        if self._hanzi_session.revealed:
            await self._hanzi_next(now)
            return

        self._hanzi_session.toggle_option()
        curr = self._hanzi_session.current
        page = display.render_hanzi(
            question_idx=self._hanzi_session.idx,
            total_questions=self._hanzi_session.total,
            stem=curr["stem"],
            opts=curr["opts"],
            selected_opt_idx=self._hanzi_session.selected_opt_idx,
            revealed=False,
            footer={"battery": self.battery},
            partial=True
        )
        await self._display(page)

    async def _hanzi_confirm(self, now: float) -> None:
        if not self._hanzi_session:
            return
        if self._hanzi_session.revealed:
            await self._hanzi_next(now)
            return

        is_corr, speech, curr = self._hanzi_session.confirm_answer()
        await self._render_hanzi_revealed(is_corr, speech, curr, now)

    async def _render_hanzi_revealed(self, is_corr: bool, speech: str, curr: Dict[str, Any], now: float) -> None:
        sess = self._hanzi_session
        if not sess:
            return
        if is_corr:
            self.notes.add_stars(self.child_id, 1, reason=f"错字门诊答对: {curr['stem']}")
        else:
            # 自动收录进孩子错题本
            self.notes.add_mistake(
                self.child_id, category="hanzi",
                question=f"选字填空：{curr['stem']}",
                expected=curr["ans"],
                wrong_answer=curr["opts"][1 - curr["opts"].index(curr["ans"])] if curr["ans"] in curr["opts"] else "混淆",
                now=now
            )

        page = display.render_hanzi(
            question_idx=sess.idx,
            total_questions=sess.total,
            stem=curr["stem"],
            opts=curr["opts"],
            selected_opt_idx=sess.selected_opt_idx,
            revealed=True,
            is_correct=is_corr,
            mnemonic=curr.get("mnemonic", ""),
            detail=curr.get("detail", ""),
            footer={"battery": self.battery},
            partial=True
        )
        await self._display(page)
        await self._speak(speech)

    async def _hanzi_next(self, now: float) -> None:
        if not self._hanzi_session:
            return
        if self._hanzi_session.step_next():
            curr = self._hanzi_session.current
            page = display.render_hanzi(
                question_idx=self._hanzi_session.idx,
                total_questions=self._hanzi_session.total,
                stem=curr["stem"],
                opts=curr["opts"],
                selected_opt_idx=self._hanzi_session.selected_opt_idx,
                revealed=False,
                footer={"battery": self.battery},
                partial=True
            )
            await self._display(page)
            await self._speak(f"第{self._hanzi_session.idx + 1}题：{curr['stem']}。")
        else:
            cnt = self._hanzi_session.correct_cnt
            tot = self._hanzi_session.total
            await self._speak(f"错字门诊闯关完成！共{tot}题，答对{cnt}题！错字已自动记录进错题本，记得复习哦！")
            await self._show_tasks()

    # ── 伴学屏保模式（Screensaver / Standby Mode）──
    async def _start_screensaver(self, now: float, mode: Optional[str] = None, *,
                                 art_id: Optional[str] = None, idx: Optional[int] = None,
                                 speak: bool = False) -> None:
        from pocketclaw.screensaver import ART_DB, CountdownEvent, ScreensaverSession
        if self._focus is not None:
            await self._stop_focus_task()
            self._focus = None
            if self._focus_local:
                await self.send_json(protocol.focus_stop())
        self.mode = "screensaver"

        cfg = self.notes.get_screensaver_config(self.child_id)
        if art_id:
            chosen_mode = "art"
        else:
            chosen_mode = mode or cfg.get("default_mode", "cycle")

        raw_events = self.notes.list_countdown_events(self.child_id)
        cd_events = [
            CountdownEvent(id=e.get("id", ""), title=e.get("title", ""),
                           target_date=e.get("target_date", ""), slogan=e.get("slogan", ""))
            for e in raw_events
        ]
        today_sched = self.notes.get_today_schedule(self.child_id)

        self._screensaver_session = ScreensaverSession(
            mode=chosen_mode,
            countdown_events=cd_events,
            today_schedule=today_sched
        )
        if chosen_mode == "art" or art_id:
            self._screensaver_session.shuffle_art(target_art_id=art_id)
        elif idx is not None and chosen_mode in self._screensaver_session._indices:
            self._screensaver_session._indices[chosen_mode] = int(idx)

        card = self._screensaver_session.get_card()
        page = self._screensaver_page(card)
        await self._display(page)
        if speak:
            speech = self._screensaver_session.get_speech_text()
            await self._speak(speech)

    def _screensaver_page(self, card: Dict[str, Any], *, partial: bool = False) -> Dict[str, Any]:
        if card.get('type') == 'timetable':
            sched = self.notes.get_today_schedule(self.child_id)
            page = display.render_timetable(sched.get('day_name', ''), sched.get('courses', []),
                                            sched.get('note', ''),
                                            footer={'battery': self.battery, 'hint': '上下切分类 · 中键换内容'}, partial=partial)
            page['mode'] = 'screensaver'
            return page
        return display.render_screensaver(card, footer={'battery': self.battery}, partial=partial)

    async def _screensaver_step_type(self, delta: int, now: float) -> None:
        if not self._screensaver_session:
            await self._start_screensaver(now, speak=False)
            return
        self._screensaver_session.step_type(delta)
        card = self._screensaver_session.get_card()
        page = self._screensaver_page(card, partial=True)
        await self._display(page)

    async def _screensaver_step_card(self, delta: int, now: float) -> None:
        if not self._screensaver_session:
            await self._start_screensaver(now, speak=False)
            return
        card = self._screensaver_session.step_card(delta)
        page = self._screensaver_page(card, partial=True)
        await self._display(page)

    async def _handle_screensaver_speech(self, text: str, now: float) -> None:
        if not self._screensaver_session:
            await self._start_screensaver(now, speak=False)
            return
        if any(w in text for w in ("下一张", "换一张", "下一个", "下一幅", "换一个", "下一幅画", "下一页")):
            await self._screensaver_step_card(+1, now)
            return
        elif any(w in text for w in ("上一张", "换上一张", "上一个", "上一幅", "上一幅画", "上一页")):
            await self._screensaver_step_card(-1, now)
            return
        elif any(w in text for w in ("下一类", "换类型", "换分类", "切换分类")):
            await self._screensaver_step_type(+1, now)
            return
        elif any(w in text for w in ("上一类", "上一个分类")):
            await self._screensaver_step_type(-1, now)
            return
        elif "名画" in text:
            if self._screensaver_session.mode != "art":
                self._screensaver_session.shuffle_art()
            self._screensaver_session.mode = "art"
        elif "诗" in text:
            self._screensaver_session.mode = "poem"
        elif "倒计时" in text or "考试" in text:
            self._screensaver_session.mode = "countdown"
        elif "语录" in text or "名言" in text:
            self._screensaver_session.mode = "quote"
        elif "课表" in text:
            self._screensaver_session.mode = "timetable"
        elif "轮播" in text:
            self._screensaver_session.mode = "cycle"
        else:
            # 用户在屏保下按住中间键提问说话：直接唤醒进入 AI学伴！
            self.mode = "chat"
            await self._chat(text, heard=text, now=now)
            return

        card = self._screensaver_session.get_card()
        page = self._screensaver_page(card, partial=True)
        await self._display(page)
        if self._screensaver_session.mode != "art":
            await self._speak(self._screensaver_session.get_speech_text())

    # ── 小学生核心功能 1：好习惯打卡 ──
    async def _start_habits(self, now: float, *, speak: bool = False) -> None:
        self.mode = "habit"
        self._habit_idx = 0
        habits = self.notes.list_habits(self.child_id)
        page = display.render_habits(habits, self._habit_idx, footer={"battery": self.battery})
        await self._display(page)
        if speak:
            await self._speak("好习惯打卡。按上下键选择习惯，按确认键打卡！")

    async def _habit_step(self, delta: int, now: float) -> None:
        habits = self.notes.list_habits(self.child_id)
        if not habits:
            return
        self._habit_idx = (getattr(self, "_habit_idx", 0) + delta) % len(habits)
        page = display.render_habits(habits, self._habit_idx, footer={"battery": self.battery}, partial=True)
        await self._display(page)

    async def _habit_toggle_current(self, now: float) -> None:
        habits = self.notes.list_habits(self.child_id)
        if not habits:
            return
        idx = getattr(self, "_habit_idx", 0)
        cur = habits[idx % len(habits)]
        updated = self.notes.toggle_habit(self.child_id, cur.id, now=now)
        updated_habits = self.notes.list_habits(self.child_id)
        page = display.render_habits(updated_habits, self._habit_idx, footer={"battery": self.battery}, partial=True)
        await self._display(page)
        if updated and updated.done:
            self.notes.add_stars(self.child_id, 1, reason=f"习惯打卡: {updated.name}")
            if all(h.done for h in updated_habits) and len(updated_habits) >= 3:
                self.notes.add_stars(self.child_id, 5, reason="好习惯大满贯成就")
                await self._speak(f"太棒啦！{updated.name} 打卡成功！全部习惯打卡完成达成大满贯！")
            else:
                await self._speak(f"打卡完成：{updated.name}")
        else:
            await self._speak(f"已取消：{cur.name}")

    async def _handle_habit_voice(self, text: str, now: float) -> None:
        """习惯打卡语音支持：支持「打卡/完成」或「取消/撤销」或直接说出习惯名"""
        habits = self.notes.list_habits(self.child_id)
        if not habits:
            return
        if any(k in text for k in ("下一个", "往下", "后一个")):
            await self._habit_step(+1, now)
            return
        elif any(k in text for k in ("上一个", "往上", "前一个")):
            await self._habit_step(-1, now)
            return

        is_cancel = any(k in text for k in ("取消", "撤销", "不打了", "没做"))
        matched_idx = None
        for idx, h in enumerate(habits):
            if any(part in text for part in h.name.split()) or (len(h.name) >= 2 and h.name[:2] in text):
                matched_idx = idx
                break

        if matched_idx is not None:
            self._habit_idx = matched_idx
        idx = getattr(self, "_habit_idx", 0)
        cur = habits[idx % len(habits)]
        if is_cancel:
            if cur.done:
                await self._habit_toggle_current(now)
            else:
                await self._speak(f"{cur.name} 尚未打卡。")
        else:
            if not cur.done:
                await self._habit_toggle_current(now)
            else:
                await self._speak(f"{cur.name} 已经打过卡啦！再按一次说话键可取消。")

    # ── 小学生核心功能 2：桌面课程表与课前备忘 ──
    async def _show_timetable(self, now: float, weekday: Optional[int] = None, *, speak: bool = False) -> None:
        self.mode = "timetable"
        is_evening = (time.localtime().tm_hour >= 19)
        if weekday is None:
            w = time.localtime().tm_wday
            if is_evening:
                self._tt_day = (w + 1) % 7
                if self._tt_day >= 5:
                    self._tt_day = 0
            else:
                self._tt_day = w if w < 5 else 0
        else:
            self._tt_day = weekday % 5
        sched = self.notes.get_today_schedule(self.child_id, weekday=self._tt_day)
        day_name = sched.get("day_name", "周一")
        courses = sched.get("courses", [])
        note = sched.get("note", "")
        page = display.render_timetable(day_name, courses, note, footer={"battery": self.battery})
        await self._display(page)
        if speak:
            c_str = "、".join(courses[:4])
            prefix = "明天是" if is_evening else "今天是"
            tip = f"特别备忘：{note}。" if note else ""
            await self._speak(f"{prefix}{day_name}。上午课程有：{c_str}。{tip}")

    async def _timetable_step(self, delta: int, now: float) -> None:
        self._tt_day = (getattr(self, "_tt_day", 0) + delta) % 5
        sched = self.notes.get_today_schedule(self.child_id, weekday=self._tt_day)
        day_name = sched.get("day_name", "周一")
        courses = sched.get("courses", [])
        note = sched.get("note", "")
        page = display.render_timetable(day_name, courses, note, footer={"battery": self.battery}, partial=True)
        await self._display(page)

    async def _timetable_read_current(self, now: float) -> None:
        sched = self.notes.get_today_schedule(self.child_id, weekday=getattr(self, "_tt_day", 0))
        day_name = sched.get("day_name", "周一")
        courses = sched.get("courses", [])
        note = sched.get("note", "")
        am = "、".join(courses[:4])
        pm = "、".join(courses[4:]) if len(courses) > 4 else "无"
        tip = f"特别备忘：{note}。" if note else ""
        await self._speak(f"{day_name}课程：上午有{am}；下午有{pm}。{tip}")

    # ── 小学生核心功能 3：错题消灭本 ──
    async def _start_mistakes(self, now: float, *, speak: bool = False) -> None:
        self.mode = "mistakes"
        self._mistake_idx = 0
        mistakes = self.notes.list_mistakes(self.child_id)
        page = display.render_mistakes(mistakes, self._mistake_idx, footer={"battery": self.battery})
        await self._display(page)
        if speak:
            if mistakes:
                await self._speak(f"错题消灭本，共有{len(mistakes)}道待攻克错题。按确认键消灭此题！")
            else:
                await self._speak("太棒啦！当前没有历史错题！继续保持哦！")

    async def _mistake_step(self, delta: int, now: float) -> None:
        mistakes = self.notes.list_mistakes(self.child_id)
        if not mistakes:
            return
        self._mistake_idx = (getattr(self, "_mistake_idx", 0) + delta) % len(mistakes)
        page = display.render_mistakes(mistakes, self._mistake_idx, footer={"battery": self.battery}, partial=True)
        await self._display(page)

    async def _mistake_eliminate(self, now: float) -> None:
        mistakes = self.notes.list_mistakes(self.child_id)
        if not mistakes:
            return
        idx = getattr(self, "_mistake_idx", 0)
        cur = mistakes[idx % len(mistakes)]
        self.notes.delete_mistake(self.child_id, cur.id)
        self.notes.add_stars(self.child_id, 2, reason="攻克消灭错题")
        rem = self.notes.list_mistakes(self.child_id)
        self._mistake_idx = min(idx, max(0, len(rem) - 1))
        page = display.render_mistakes(rem, self._mistake_idx, footer={"battery": self.battery}, partial=True)
        await self._display(page)
        await self._speak("太棒啦！这道错题已彻底消灭！获得 2 颗星星奖励！")

    # ── 小学生核心功能 4：每日国学成语 ──
    async def _show_daily_idiom(self, now: float, *, speak: bool = False, partial: bool = False) -> None:
        self.mode = "idiom"
        idioms = self.notes.list_idioms()
        idiom_data = self.notes.get_daily_idiom(self.child_id)
        cur_idx = 0
        for i, it in enumerate(idioms):
            if it.get("idiom") == idiom_data.get("idiom"):
                cur_idx = i
                break
        self._idiom_idx = cur_idx
        page = display.render_daily_idiom(
            idiom_data,
            footer={"battery": self.battery},
            cur_idx=cur_idx + 1 if idioms else 0,
            total=len(idioms),
            partial=partial,
        )
        await self._display(page)
        if speak:
            idiom = idiom_data.get("idiom", "")
            meaning = idiom_data.get("meaning", "")
            await self._speak(f"今日成语：{idiom}。释义：{meaning}")

    async def _idiom_step(self, delta: int, now: float) -> None:
        self.mode = "idiom"
        idioms = self.notes.list_idioms()
        if not idioms:
            return
        cur_data = self.notes.get_daily_idiom(self.child_id)
        cur_idx = getattr(self, "_idiom_idx", 0)
        for i, it in enumerate(idioms):
            if it.get("idiom") == cur_data.get("idiom"):
                cur_idx = i
                break
        next_idx = (cur_idx + delta) % len(idioms)
        self._idiom_idx = next_idx
        next_data = idioms[next_idx]
        self.notes.set_daily_idiom(self.child_id, next_data)
        page = display.render_daily_idiom(
            next_data,
            footer={"battery": self.battery},
            cur_idx=next_idx + 1,
            total=len(idioms),
            partial=True,
        )
        await self._display(page)

    async def _idiom_read_current(self, now: float) -> None:
        idiom_data = self.notes.get_daily_idiom(self.child_id)
        idiom = idiom_data.get("idiom", "")
        meaning = idiom_data.get("meaning", "")
        source = idiom_data.get("source", "")
        example = idiom_data.get("example", "")
        text = f"{idiom}。出自{source}。意思是：{meaning}。造句示范：{example}"
        await self._speak(text)

    # ── 英语专项 1：英语生词本（语音朗读 + 跟读评测）──
    async def _start_vocab(self, now: float, *, speak: bool = False) -> None:
        self.mode = "vocab"
        self.notes.ensure_seeded(self.child_id, now=now)
        words = self.notes.list_words(self.child_id)
        if not words:
            await self._display(display.render_lines('英语生词', '还没有生词\n请家长录入或按年级选择', mode='vocab', footer={'battery': self.battery}))
            if speak:
                await self._speak('还没有生词，请家长录入或按年级选择。')
            return
        if self._vocab_idx >= len(words):
            self._vocab_idx = 0
        w = words[self._vocab_idx]
        self._vocab_last_score = None
        self._vocab_last_rating = ""
        self._vocab_last_feedback = ""
        page = display.render_vocab_card(
            word=w.word,
            meaning=w.meaning,
            example=w.example,
            example_cn=w.example_cn,
            cur_idx=self._vocab_idx + 1,
            total=len(words),
            footer={"battery": self.battery}
        )
        await self._display(page)
        if speak:
            await self._speak(f"{w.word}. {w.meaning}.")

    async def _vocab_step(self, delta: int, now: float) -> None:
        words = self.notes.list_words(self.child_id)
        if not words:
            await self._start_vocab(now)
            return
        self._vocab_idx = (self._vocab_idx + delta) % len(words)
        self._vocab_last_score = None
        self._vocab_last_rating = ""
        self._vocab_last_feedback = ""
        w = words[self._vocab_idx]
        page = display.render_vocab_card(
            word=w.word,
            meaning=w.meaning,
            example=w.example,
            example_cn=w.example_cn,
            cur_idx=self._vocab_idx + 1,
            total=len(words),
            footer={"battery": self.battery},
            partial=True,
        )
        await self._display(page)

    async def _vocab_speak_current(self, now: float) -> None:
        words = self.notes.list_words(self.child_id)
        if not words:
            return
        w = words[self._vocab_idx % len(words)]
        text = f"{w.word}。{w.meaning}。"
        if w.example:
            text += f"例句：{w.example}。{w.example_cn}"
        await self._speak(text)

    async def _vocab_evaluate(self, heard_text: str, now: float) -> None:
        words = self.notes.list_words(self.child_id)
        if not words:
            return
        w = words[self._vocab_idx % len(words)]
        expected = w.word
        score, rating, speech = practice.evaluate_english_speech(expected, heard_text, band=self.grade_band)
        self._vocab_last_score = score
        self._vocab_last_rating = rating
        self._vocab_last_feedback = speech
        page = display.render_vocab_card(
            word=w.word,
            meaning=w.meaning,
            example=w.example,
            example_cn=w.example_cn,
            cur_idx=self._vocab_idx + 1,
            total=len(words),
            score=score,
            rating=rating,
            feedback=speech,
            footer={"battery": self.battery}
        )
        await self._display(page)
        await self._speak(f"发音得分{score}分！{speech}")
        if score >= 80:
            self.notes.review_word(self.child_id, w.id, remembered=True, now=now)
            self.notes.add_stars(self.child_id, 1, reason=f"生词发音优秀: {w.word}")
        elif score < 60:
            self.notes.add_mistake(self.child_id, category="vocab", question=f"生词发音: {w.word}",
                                  expected=f"{w.word} ({w.meaning})", wrong_answer=f"{heard_text} ({score}分)", now=now)

    # ── 英语专项 2：英语文章阅读（按键选句 + 选句朗读 + 跟读评测）──
    async def _start_reading(self, now: float, article_idx: Optional[int] = None, *, speak: bool = False) -> None:
        from pocketclaw import reading
        self.mode = "reading"
        if article_idx is not None:
            self._reading_article_idx = article_idx
        else:
            target_id = self.notes.get_reading_article(self.child_id)
            for idx, a in enumerate(self.reading_store.articles):
                if a.id == target_id:
                    self._reading_article_idx = idx
                    break
        art = self.reading_store.get_article_by_idx(self._reading_article_idx)
        if not art:
            await self._show_tasks()
            return
        self.notes.set_reading_article(self.child_id, art.id)
        self._reading_session = reading.ReadingSession(art)
        self._reading_show_translation = False
        page = display.render_reading_article(
            title=art.title,
            sentences=art.sentences,
            sel_idx=self._reading_session.sel_idx,
            show_translation=False,
            footer={"battery": self.battery}
        )
        await self._display(page)
        if speak:
            await self._speak(f"英语阅读《{art.title}》。")

    async def _reading_step(self, delta: int, now: float) -> None:
        await self._abort_speech(reason="reading_step", send_stop=True)
        if not self._reading_session:
            await self._start_reading(now)
            return
        if delta > 0:
            self._reading_session.step_next()
        else:
            self._reading_session.step_prev()
        self._reading_show_translation = False
        art = self._reading_session.article
        page = display.render_reading_article(
            title=art.title,
            sentences=art.sentences,
            sel_idx=self._reading_session.sel_idx,
            show_translation=False,
            score=self._reading_session.last_score,
            rating="",
            feedback=self._reading_session.last_feedback,
            footer={"battery": self.battery},
            partial=True,
        )
        await self._display(page)
        # 选句上下键切换：纯净黑底白字高亮反色，静默无声，不播报语音，不展示中文

    async def _reading_read_selected(self, now: float) -> None:
        if not self._reading_session:
            await self._start_reading(now)
            return
        art = self._reading_session.article
        self._reading_show_translation = True
        page = display.render_reading_article(
            title=art.title,
            sentences=art.sentences,
            sel_idx=self._reading_session.sel_idx,
            show_translation=True,
            score=self._reading_session.last_score,
            rating="",
            feedback=self._reading_session.last_feedback,
            footer={"battery": self.battery},
            partial=True,
        )
        await self._display(page)
        en_s, _ = self._reading_session.cur_sentence
        if en_s:
            await self._speak(en_s)

    async def _reading_evaluate(self, heard_text: str, now: float) -> None:
        if not self._reading_session:
            return
        en_s, cn_s = self._reading_session.cur_sentence
        score, rating, speech = practice.evaluate_english_speech(en_s, heard_text, band=self.grade_band)
        self._reading_session.record_score(score, speech)
        art = self._reading_session.article
        self._reading_show_translation = True
        page = display.render_reading_article(
            title=art.title,
            sentences=art.sentences,
            sel_idx=self._reading_session.sel_idx,
            show_translation=True,
            score=score,
            rating=rating,
            feedback=speech,
            footer={"battery": self.battery},
            partial=True,
        )
        await self._display(page)
        await self._speak(f"本句评测得分{score}分！{speech}")
        if score >= 80:
            self.notes.add_stars(self.child_id, 1, reason="英文短文选句跟读优秀")
        elif score < 60:
            self.notes.add_mistake(self.child_id, category="reading", question=f"朗读选句: {en_s}",
                                  expected=en_s, wrong_answer=f"{heard_text} ({score}分)", now=now)

    async def _reading_switch_article(self, delta: int, now: float) -> None:
        await self._abort_speech(reason="reading_switch_article", send_stop=True)
        if not getattr(self, "reading_store", None) or not self.reading_store.articles:
            return
        self._reading_article_idx = (self._reading_article_idx + delta) % len(self.reading_store.articles)
        await self._start_reading(now, article_idx=self._reading_article_idx, speak=False)

    async def _add_task(self, text: str, now: float) -> None:
        text = (text or "").strip()
        if not text:
            await self._display(display.render_lines("加任务", "说「加任务 背10个单词」就能记进清单。", mode="task"))
            return
        self.notes.add_task(self.child_id, text, subject=self.subject, now=now)
        await self._show_tasks()

    async def _complete_task(self, text: str, now: float, *, quiet_if_missing: bool = False) -> bool:
        done = self.notes.complete_task(self.child_id, text=text.strip(), now=now)
        if not done:
            if not quiet_if_missing:
                await self._display(display.render_lines("没找到", f"清单里没有「{text}」这项。说「作业清单」看看。", mode="task"))
            return False
        st = self.notes.task_stats(self.child_id)
        # 全部完成 → 报喜播报庆祝语音
        if st["todo"] == 0 and st["total"] > 0:
            await self._display(display.render_lines("全部完成 🎉", "今天的作业都做完啦，太棒了！", mode="task",
                                                     footer={"battery": self.battery, "all_done": True}))
            await self._speak("今天的作业都做完啦，太棒了！")
        else:
            await self._show_tasks()
        return True

    async def _mark_done(self, text: str, now: float) -> None:
        """「XX 写完了/做了」→ 先勾作业清单，没有再勾想法列表。"""
        x = (text or "").strip()
        if x and await self._complete_task(x, now, quiet_if_missing=True):
            return
        if x and self.notes.complete_note(self.child_id, x, now=now):
            await self._list_notes()
            await self._speak("好，标记完成啦")
            return
        await self._display(display.render_lines("没找到", f"作业清单和想法里都没找到「{x}」。\n说「作业清单」或「我的想法」看看。", mode="chat"))

    async def _delete_note_cmd(self, text: str) -> None:
        """「删掉 XX」→ 从想法列表删除。"""
        x = (text or "").strip()
        removed = self.notes.delete_note(self.child_id, x) if x else None
        if removed:
            await self._list_notes()
            await self._speak("好，删掉啦")
        else:
            await self._display(display.render_lines("没找到", f"想法里没找到「{x}」。说「我的想法」看看有哪些。", mode="note"))

    # ── 物理键 / NFC ──
    # ── 花火番茄钟 / 倒计时（pocketclaw/focus.py）──
    @property
    def _focus_local(self) -> bool:
        """设备自己会跑秒与绘屏（固件 focus_page.cc）→ 服务端只发开始/停止，不每分钟推屏。

        没有这个能力的设备（旧固件 / 模拟器）走服务端推屏的老路，两条路的语音口径完全一致。
        """
        return "focus_local" in self._features

    def _today_focus_min(self) -> int:
        if self.on_focus_today is None:
            return 0
        try:
            return int(self.on_focus_today(self.child_id) or 0)
        except Exception as e:  # noqa: BLE001
            log.warning("pocketclaw 取今日专注分钟失败: %s", e)
            return 0

    async def _start_focus(self, req: Dict[str, Any], now: float) -> None:
        if req.get("minutes") is None:                      # 「倒计时」没说多久 → 追问，不瞎猜
            await self._display(_focus.render_ask_minutes_page(battery=self.battery))
            await self._speak("倒计时多久？比如说，倒计时十分钟。")
            return
        await self._stop_focus_task()
        self.mode = "focus"
        self._focus = _focus.FocusTimer(req["kind"], int(req["minutes"]), now=now,
                                        style=str(req.get("style") or _focus.STYLE_SPARK))
        if self._focus_local:
            # 设备本地跑：引线形状由固件每轮随机生成，秒与画面都不经网络
            await self.send_json(protocol.focus_start(self._focus.total_s,
                                                      today_min=self._today_focus_min(),
                                                      mode=self._focus.kind,
                                                      style=self._focus.style))
        else:
            await self._display(_focus.render_focus_page(self._focus, now=now, battery=self.battery))
        await self._speak(_focus.spoken_start(self._focus))
        if not self._focus_local:
            self._focus_task = asyncio.create_task(self._focus_loop())

    async def _focus_control(self, ctl: str, now: float) -> None:
        t = self._focus
        if t is None:
            return
        if ctl == "pause":
            t.pause(now)
            if self._focus_local:
                await self.send_json(protocol.focus_pause(True))
            else:
                await self._display(_focus.render_focus_page(t, now=now, battery=self.battery))
            await self._speak("先暂停，花火等你回来。")
        elif ctl == "resume":
            t.resume(now)
            if self._focus_local:
                await self.send_json(protocol.focus_pause(False))
            else:
                await self._display(_focus.render_focus_page(t, now=now, battery=self.battery))
                self._kick_focus_loop()
            await self._speak("继续烧！")
        elif ctl == "remain":
            await self._speak(_focus.spoken_remaining(t, now))
        elif ctl == "cancel":
            await self._stop_focus_task()
            self._focus = None
            self.mode = "task"
            if self._focus_local:
                await self.send_json(protocol.focus_stop())
            await self._display(display.render_lines("花火熄灭", "这轮先不计了。想再来就说「番茄钟 25 分钟」。",
                                                     mode="chat", footer={"battery": self.battery}))
            await self._speak("好，这轮先不计了。")

    async def _on_focus_done_from_device(self, total_s: int, now: float) -> None:
        """设备本地烧完一轮 → 服务端只负责记证据 + 说一句（绽放屏设备已自绘）。"""
        self.mode = "task"
        t = self._focus
        if t is None:                       # 服务端影子丢了（重连过）：按设备报的时长补一个
            minutes = max(1, int(round((total_s or 0) / 60.0))) if total_s else 0
            if minutes <= 0:
                return
            t = _focus.FocusTimer("pomodoro", minutes, now=now)
        await self._stop_focus_task()
        self._focus = None
        if self.on_focus_done is not None and t.kind == "pomodoro":
            try:
                self.on_focus_done(self.child_id, t.minutes, t.kind)
            except Exception as e:  # noqa: BLE001
                log.warning("pocketclaw focus_done 记证据失败: %s", e)
        await self._speak(_focus.spoken_done(t))

    def _kick_focus_loop(self) -> None:
        if self._focus_task is None or self._focus_task.done():
            self._focus_task = asyncio.create_task(self._focus_loop())

    async def _stop_focus_task(self) -> None:
        t = self._focus_task
        self._focus_task = None
        if t is not None and not t.done():
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass

    async def _focus_loop(self) -> None:
        """每分钟（最后一分钟每 15 秒）推一屏；暂停时退出（「继续」再拉起）；到点推「绽放」屏 + 播报 + 记证据。"""
        try:
            while self._focus is not None:
                t = self._focus
                if t.paused:
                    return
                delay = t.next_tick_delay(self._clock())
                if delay > 0:
                    await asyncio.sleep(delay)
                    if self._focus is not t or t.paused:
                        return
                now = self._clock()
                if t.remaining(now) > 0:
                    await self._display(_focus.render_focus_page(t, now=now, battery=self.battery))
                    continue
                await self._finish_focus(t)
                return
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — 计时循环异常不该拖垮连接
            log.warning("pocketclaw focus loop 异常: %s", e)

    async def _finish_focus(self, t: "_focus.FocusTimer") -> None:
        t.done = True
        self._focus = None
        # 先记证据、再推屏播报：设备恰好在这一刻断连时，推屏/播报会抛异常，证据不能跟着丢
        # （实测：客户端收到「完成」屏就关连接 → 播报 send 抛 RuntimeError → 证据没记上）
        if self.on_focus_done is not None and t.kind == "pomodoro":
            try:
                self.on_focus_done(self.child_id, t.minutes, t.kind)   # 与平板同一事件 focus_done → 番茄专注法卡
            except Exception as e:  # noqa: BLE001
                log.warning("pocketclaw focus_done 记证据失败: %s", e)
        await self._display(_focus.render_done_page(t, battery=self.battery))
        await self._speak(_focus.spoken_done(t))

    # ── 小学新鲜事 (News, Competitions, Exhibitions & Activities) ──

    async def _start_news(self, now: float, *, speak: bool = True) -> None:
        """进入小学新鲜事功能页面。"""
        self.mode = "news"
        self._news_view = "list"
        if hasattr(self.notes, "check_and_update_daily_news"):
            try:
                self.notes.check_and_update_daily_news(self.child_id, now=now)
            except Exception as e:  # noqa: BLE001
                log.warning("check_and_update_daily_news failed: %s", e)
        self._news_items = self.notes.list_news(self.child_id, apply_filter=True, now=now) if hasattr(self.notes, "list_news") else []
        self._news_idx = 0
        user_reg = self.notes.get_news_region(self.child_id) if hasattr(self.notes, "get_news_region") else "全国"

        page = display.render_news_list(
            self._news_items,
            self._news_idx,
            region=user_reg,
            footer={"battery": self.battery},
            partial=False,
        )
        await self._display(page)
        self._start_news_scroll()

        if speak:
            n = len(self._news_items)
            reg_text = f"为您筛选出与【{user_reg}】相关的" if user_reg and user_reg != "全国" else ""
            if n > 0:
                await self._speak(f"小学新鲜事已就绪！{reg_text}过去一个月最新{n}条赛事、展会与校园资讯。按上下键浏览，按中间播放键看详细报道！")
            else:
                await self._speak(f"小学新鲜事已就绪。当前{user_reg}暂无匹配的活动报道，可以在家长端添加信源或调整关注地区哦。")

    async def _news_step(self, delta: int, now: float) -> None:
        """上下按键切换报道。"""
        if self._news_view == "list":
            n = len(self._news_items)
            if n <= 0:
                return
            self._news_idx = (self._news_idx + delta) % n
            user_reg = self.notes.get_news_region(self.child_id)
            page = display.render_news_list(
                self._news_items,
                self._news_idx,
                region=user_reg,
                footer={"battery": self.battery},
                partial=True,
            )
            await self._display(page)
            self._start_news_scroll()
        elif self._news_view == "detail":
            self._stop_news_scroll()
            n = len(self._news_items)
            if n <= 0:
                return
            self._news_idx = (self._news_idx + delta) % n
            item = self._news_items[self._news_idx]
            page = display.render_news_detail(
                item,
                footer={"battery": self.battery},
                partial=False,
            )
            await self._display(page)
            await self._speak(f"切换至：{item['title']}")

    async def _news_play_or_detail(self, now: float) -> None:
        """播放/确认键：查看详细报道并语音播报。"""
        n = len(self._news_items)
        if n <= 0:
            await self._speak("当前暂无报道内容，请在家长端添加信源。")
            return
        
        self._news_idx = max(0, min(self._news_idx, n - 1))
        item = self._news_items[self._news_idx]

        points = item.get("points") or item.get("highlights")
        if points:
            pts_text = " ".join(p.lstrip("· ") for p in points)
            speech_body = pts_text
        else:
            speech_body = item.get('content') or item.get('summary') or ""

        if self._news_view == "list":
            self._stop_news_scroll()
            self._news_view = "detail"
            page = display.render_news_detail(
                item,
                footer={"battery": self.battery},
                partial=False,
            )
            await self._display(page)
            speech = f"详细报道：{item['title']}。{speech_body}"
            await self._speak(speech)
        else:
            speech = f"正在为你播报：{item['title']}。{speech_body}"
            await self._speak(speech)

    async def _news_back_to_list(self, now: float) -> None:
        """长按下方按键返回「小学新鲜事」主页（列表页面）。"""
        self._news_view = "list"
        user_reg = self.notes.get_news_region(self.child_id)
        page = display.render_news_list(
            self._news_items,
            self._news_idx,
            region=user_reg,
            footer={"battery": self.battery},
            partial=False,
        )
        await self._display(page)
        self._start_news_scroll()
        await self._speak("已返回小学新鲜事主页。按上下键选择，按中间键看详情。")

    def _start_news_scroll(self) -> None:
        """若当前选中的报道标题超长，开启平缓走马灯横向滚动。"""
        self._stop_news_scroll()
        if self.mode != "news" or self._news_view != "list":
            return
        if not self._news_items or self._news_idx >= len(self._news_items):
            return
        sel_item = self._news_items[self._news_idx]
        title = sel_item.get("title", "")
        clean_title = display.screen_text(title).strip()
        # 仅当标题超长（>30 列，即超过 15 个汉字）才需要启动走马灯滚动
        if display._display_width(clean_title) <= 30:
            return
        self._news_scroll_task = asyncio.create_task(self._news_scroll_loop())

    def _stop_news_scroll(self) -> None:
        """停止走马灯横向滚动任务。"""
        if self._news_scroll_task and not self._news_scroll_task.done():
            self._news_scroll_task.cancel()
        self._news_scroll_task = None

    async def _news_scroll_loop(self) -> None:
        """平缓横向走马灯：步进移动标题，展示完整长标题。"""
        try:
            # 初始在开头停顿 2.0 秒，让孩子先看清前 15 个字
            await asyncio.sleep(2.0)
            sel_idx = self._news_idx
            sel_item = self._news_items[sel_idx]
            clean_title = display.screen_text(sel_item.get("title", "")).strip()

            offset = 2
            while self.mode == "news" and self._news_view == "list" and self._news_idx == sel_idx:
                user_reg = self.notes.get_news_region(self.child_id)
                page = display.render_news_list(
                    self._news_items,
                    self._news_idx,
                    region=user_reg,
                    footer={"battery": self.battery},
                    partial=True,
                    scroll_offset=offset,
                )
                await self._display(page)

                sub = clean_title[offset:]
                # 如果当前 offset 已经能够完整展示标题尾部（不需要省略号）
                if display._display_width(sub) <= 30:
                    # 在尾部停留 2.0 秒，让孩子看清结尾
                    await asyncio.sleep(2.0)
                    offset = 0
                    if self.mode == "news" and self._news_view == "list" and self._news_idx == sel_idx:
                        page = display.render_news_list(
                            self._news_items,
                            self._news_idx,
                            region=user_reg,
                            footer={"battery": self.battery},
                            partial=True,
                            scroll_offset=0,
                        )
                        await self._display(page)
                        await asyncio.sleep(2.0)
                        offset = 2
                else:
                    await asyncio.sleep(1.5)
                    offset += 2
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.warning("news_scroll_loop error: %s", e)

    async def close(self) -> None:
        """断连收尾：取消对话轮、计时循环与早安推屏任务。"""
        self._stop_news_scroll()
        if self._screen_pending and not self._screen_pending["future"].done():
            self._screen_pending["future"].set_exception(RuntimeError("设备已断开"))
        idle_task = self._chat_idle_task
        self._cancel_chat_idle_timer()
        if idle_task is not None and idle_task is not asyncio.current_task():
            try:
                await idle_task
            except (asyncio.CancelledError, Exception):
                pass
        await self._cancel_turn()
        await self._stop_focus_task()
        t = self._spark_task
        self._spark_task = None
        if t is not None and not t.done():
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass

    # ── AI学伴 5 分钟闲置超时守卫 ──
    CHAT_IDLE_TIMEOUT_SECONDS = 300.0   # 对话停止5分钟自动退出对话窗口回到作业清单

    def _reset_chat_idle_timer(self, now: float = 0.0) -> None:
        if self.mode != "chat":
            self._cancel_chat_idle_timer()
            return
        self._chat_idle_deadline = self._clock() + self.CHAT_IDLE_TIMEOUT_SECONDS
        if self._chat_idle_task is None or self._chat_idle_task.done():
            self._chat_idle_task = asyncio.create_task(self._chat_idle_watchdog())

    def _cancel_chat_idle_timer(self) -> None:
        t = self._chat_idle_task
        self._chat_idle_task = None
        self._chat_idle_deadline = 0.0
        if t is not None and t is not asyncio.current_task() and not t.done():
            t.cancel()

    async def _chat_idle_watchdog(self) -> None:
        try:
            while self.mode == "chat":
                now = self._clock()
                remaining = self._chat_idle_deadline - now
                if remaining <= 0:
                    log.info("pocketclaw AI学伴闲置满 5 分钟，自动退出对话窗口返回作业清单 session=%s", self.session_id)
                    await self._abort_speech(reason="chat_idle_timeout", send_stop=True)
                    await self._switch_to_mode("task", now)
                    break
                await asyncio.sleep(max(0.01, min(remaining, 1.0)))
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.warning("pocketclaw chat_idle_watchdog 异常: %s", e)
        finally:
            if self._chat_idle_task is asyncio.current_task():
                self._chat_idle_task = None

    async def _serve_font_preview(self, idx: int) -> None:
        pids = ["p1", "p2", "p3"]
        pid = pids[idx % len(pids)]
        from pathlib import Path
        img_path = Path(__file__).resolve().parent / "card_images" / f"font_comparison_{pid}.png"
        try:
            if not img_path.exists():
                return
            from PIL import Image
            import base64
            img = Image.open(img_path).convert("1")
            pil_bytes = img.tobytes()
            fw_bytes = bytes((~b) & 0xFF for b in pil_bytes)
            b64_str = base64.b64encode(fw_bytes).decode("ascii")
            page = {
                "schema_version": "pc-0.1",
                "mode": "font_preview",
                "title": f"字体选型对比 {pid}",
                "raw_1bpp_b64": b64_str,
                "partial": False,
            }
            await self._display(page)
        except Exception as e:
            log.warning("serve_font_preview failed: %s", e)

    async def _on_button(self, name: str, now: float) -> None:
        log.info("pocketclaw button event: %s (mode: %s)", name, self.mode)
        if self.mode == "font_preview":
            if name in ("down", "mode_next"):
                self._font_preview_idx = getattr(self, "_font_preview_idx", 0) + 1
                await self._serve_font_preview(self._font_preview_idx)
                return
            elif name in ("up", "mode_prev"):
                self._font_preview_idx = getattr(self, "_font_preview_idx", 0) - 1
                await self._serve_font_preview(self._font_preview_idx)
                return

        if name in ("todo", "task"):
            await self._switch_to_mode("task", now)
            return

        # ── 长按下方按键(电源键/下键) / 上键：统一返回/功能菜单分发（严禁进入AI学伴）──
        if name in ("power_long", "down_long"):
            if self.mode == "news" and self._news_view == "detail":
                # 小学新鲜事详细报道页中长按下方按键：返回新鲜事主页（列表页）
                await self._news_back_to_list(now)
                return
            elif self.mode == "chat":
                # AI学伴模式下长按电源键退出回到作业清单待机页
                await self._switch_to_mode("task", now)
                return
            elif self.mode == "menu":
                # 菜单模式下长按电源键退出返回前一个模式
                prev_mode = getattr(self, "_menu_prev_mode", "task") or "task"
                await self._switch_to_mode(prev_mode, now)
                return
            else:
                # 所有其他页面（作业清单、新鲜事列表、古诗、听写、口算、打卡、课表、屏保等）：
                # 长按下方按键统一打开功能导航菜单，严禁进入 AI学伴！进入学伴只能按住中间语音键！
                name = "menu"

        elif name == "up_long":
            if self.mode == "news" and self._news_view == "detail":
                await self._news_back_to_list(now)
                return
            elif self.mode == "menu":
                # 已经在功能菜单中再次长按上键：退出菜单，返回之前的模式
                prev_mode = getattr(self, "_menu_prev_mode", "task") or "task"
                await self._switch_to_mode(prev_mode, now)
                return
            # 无论处于任何页面，长按上键统一进入功能导航菜单
            name = "menu"

        if name == "menu":
            if self.mode == "menu":
                # 已经在功能菜单中：保持菜单显示，严禁因按键事件重复触发而意外关闭退出！
                return
            else:
                self._menu_prev_mode = self.mode
                self.mode = "menu"
                self._menu_idx = 0
                await self._show_menu(partial=False)
            return

        # 固件在两次上下短按之外还会上报双击事件；菜单中这些短按已经
        # 移动了光标，不能再把双击解释成切换功能。
        if self.mode == "menu" and name in ("mode_next", "mode_prev"):
            return

        if name == "mode_next":
            cur_idx = 0
            for i, (m, _) in enumerate(self.CORE_MODES):
                if m == self.mode:
                    cur_idx = i
                    break
            next_mode = self.CORE_MODES[(cur_idx + 1) % len(self.CORE_MODES)][0]
            await self._switch_to_mode(next_mode, now)
            return

        if name == "mode_prev":
            cur_idx = 0
            for i, (m, _) in enumerate(self.CORE_MODES):
                if m == self.mode:
                    cur_idx = i
                    break
            prev_mode = self.CORE_MODES[(cur_idx - 1) % len(self.CORE_MODES)][0]
            await self._switch_to_mode(prev_mode, now)
            return

        # 亲子留言便签态下按任意键返回作业清单
        if self.mode == "parent_note":
            await self._show_tasks()
            return

        # 处于菜单界面时的光标移动与选中（局部刷新，静默无延时）
        if self.mode == "menu":
            items = self.notes.get_sorted_menu_items(self.child_id)
            n_items = len(items) if items else len(self.CORE_MODES)
            if name == "up":
                self._menu_idx = (self._menu_idx - 1) % n_items
                await self._show_menu(partial=True)
                # 功能菜单上下选择不播报，瞬时切换响应
            elif name == "down":
                self._menu_idx = (self._menu_idx + 1) % n_items
                await self._show_menu(partial=True)
                # 功能菜单上下选择不播报，瞬时切换响应
            elif name == "confirm":
                if 0 <= self._menu_idx < len(items):
                    target_mode = items[self._menu_idx][2]
                else:
                    target_mode = self.CORE_MODES[self._menu_idx % len(self.CORE_MODES)][0]
                self.notes.record_menu_click(self.child_id, target_mode)
                await self._switch_to_mode(target_mode, now)
            return

        # 模式分发（精准按键响应）
        if self.mode == "habit":
            if name == "down":
                await self._habit_step(+1, now)
            elif name == "up":
                await self._habit_step(-1, now)
            elif name in ("confirm", "ok", "talk", "ptt"):
                await self._habit_toggle_current(now)
            return
        elif self.mode == "timetable":
            if name == "down":
                await self._timetable_step(+1, now)
            elif name == "up":
                await self._timetable_step(-1, now)
            elif name == "confirm":
                await self._timetable_read_current(now)
            return
        elif self.mode == "idiom":
            if name == "confirm":
                await self._idiom_read_current(now)
            elif name == "down":
                await self._idiom_step(+1, now)
            elif name == "up":
                await self._idiom_step(-1, now)
            return
        elif self.mode == "mistakes":
            if name == "down":
                await self._mistake_step(+1, now)
            elif name == "up":
                await self._mistake_step(-1, now)
            elif name == "confirm":
                await self._mistake_eliminate(now)
            return
        elif self.mode == "vocab":
            if name == "confirm":
                await self._vocab_speak_current(now)
            elif name == "down":
                await self._vocab_step(+1, now)
            elif name == "up":
                await self._vocab_step(-1, now)
            return
        elif self.mode == "reading":
            if name == "down":
                await self._reading_step(+1, now)
            elif name == "up":
                await self._reading_step(-1, now)
            elif name == "confirm":
                await self._reading_read_selected(now)
            return
        elif self.mode == "guess":
            if name == "confirm":
                if self._guess_session and self._guess_session.status == "playing":
                    await self._speak(self._guess_session.give_hint())
                else:
                    await self._start_guess(now)
            elif name == "up":
                if self._guess_session and self._guess_session.status == "playing":
                    hint_msg = self._guess_session.give_hint()
                    page = display.render_guess(
                        asked_cnt=len(self._guess_session.history),
                        max_cnt=self._guess_session.max_questions,
                        history=self._guess_session.history,
                        status="playing",
                        secret=self._guess_session.secret,
                        hint=hint_msg,
                        footer={"battery": self.battery},
                        partial=True
                    )
                    await self._display(page)
                    await self._speak(hint_msg)
            elif name == "down":
                if self._guess_session:
                    self._guess_session.status = "reveal"
                    page = display.render_guess(
                        asked_cnt=len(self._guess_session.history),
                        max_cnt=self._guess_session.max_questions,
                        history=self._guess_session.history,
                        status="reveal",
                        secret=self._guess_session.secret,
                        hint=self._guess_session.hint,
                        footer={"battery": self.battery},
                        partial=True
                    )
                    await self._display(page)
                    await self._speak(f"谜底揭晓啦！其实是【{self._guess_session.secret}】！按确认键再玩一次吧！")
            return
        elif self.mode == "feynman":
            if name == "confirm":
                if self._feynman_session and self._feynman_session.rounds:
                    await self._speak(self._feynman_session.rounds[-1].student_reply)
                else:
                    await self._speak("小萌新认真听讲中！请按住中间键给我讲讲新知识吧！")
            elif name in ("up", "down"):
                await self._start_feynman(now)
            return
        elif self.mode == "verify":
            if name == "confirm":
                if self._last_verify_result:
                    v = self._last_verify_result.get("verdict_text", "")
                    n = self._last_verify_result.get("note", "")
                    await self._speak(f"{v}。{n}")
                else:
                    await self._speak("请按住中间键说出你想核实的一句话或算式！")
            elif name in ("up", "down"):
                await self._start_verify(now)
            return
        elif self.mode == "feihua":
            if name == "up":
                await self._feihua_hint(now)
            elif name == "down":
                await self._feihua_next_keyword(now)
            elif name == "confirm":
                kw = self._feihua_session.keyword if self._feihua_session else "令"
                await self._speak(f"请按住中间键，说出一句带有「{kw}」字的古诗哦！")
            return
        elif self.mode == "hanzi":
            if name in ("up", "down"):
                await self._hanzi_toggle(now)
            elif name == "confirm":
                await self._hanzi_confirm(now)
            return
        elif self.mode == "news":
            if name == "up":
                await self._news_step(-1, now)
            elif name == "down":
                await self._news_step(+1, now)
            elif name in ("confirm", "ok", "talk", "ptt"):
                await self._news_play_or_detail(now)
            return
        elif self.mode == "screensaver":
            if name == "up":
                await self._screensaver_step_type(-1, now)
            elif name == "down":
                await self._screensaver_step_type(+1, now)
            elif name == "confirm":
                await self._screensaver_step_card(+1, now)
            return
        elif self.mode == "dictation":
            if name in ("confirm", "down"):
                await self._dictation_step(now)
            elif name == "up":
                await self._dictation_repeat(now)
            return
        elif self.mode == "arith":
            if name in ("confirm", "down"):
                if self._arith_session and self._arith_session.revealed:
                    q = self._arith_session.cur_question
                    if q:
                        self.notes.add_stars(self.child_id, 1, reason=f"口算答对: {q.expr}")
                await self._arith_step(now)
            elif name == "up":
                q = self._arith_session.cur_question if self._arith_session else None
                if q:
                    if self._arith_session and self._arith_session.revealed:
                        self.notes.add_mistake(self.child_id, category="arith", question=q.expr,
                                              expected=q.ans, wrong_answer="算错", now=now)
                        await self._speak("已收录进错题消灭本，继续加油！")
                        await self._arith_step(now)
                    else:
                        await self._speak(f"这道题是：{q.expr} 等于多少？")
            return
        elif self.mode == "recite":
            if name == "confirm":
                if self._recite_poem:
                    await self._speak(f"请按住确认键背诵《{self._recite_poem.title}》整首诗哦！")
            elif name in ("down", "up"):
                await self._recite_step(+1 if name == "down" else -1, now)
            return
        elif self.mode == "focus":
            if name == "confirm":
                if self._focus:
                    if self._focus.paused:
                        await self._focus_control("resume", now)
                    else:
                        await self._focus_control("pause", now)
            elif name in ("up", "down"):
                if self._focus:
                    await self._focus_control("remain", now)
            return
        elif self.mode == "word":
            if name in ("confirm", "down"):
                await self._serve_word(now)
            elif name == "up":
                await self._serve_word(now)
            return
        elif self.mode == "chat":
            if name == "confirm":
                await self._speak("按住中间键和我说话，长按下方按键可以退出学伴回到作业哦。")
            return
        elif self.mode == "task":
            if name == "confirm":
                await self._complete_next_task(now)
            elif name == "down":
                await self._task_step(+1, now)
            elif name == "up":
                await self._task_step(-1, now)
            return

        # 默认兜底：按中间键打勾作业
        if name in ("confirm", "down"):
            self.mode = "task"
            await self._complete_next_task(now)
        elif name == "up":
            await self._show_tasks()

    async def _on_nfc(self, msg: Dict[str, Any], now: float) -> None:
        action = self.registry.on_nfc(self.device_id, str(msg.get("uid") or ""), str(msg.get("ndef") or ""))
        kind, value = action.get("kind"), action.get("value", "")
        if kind == "child" and action.get("refused"):
            # 严格模式：卡上写的孩子没经家长/平板绑定到这台设备 → 不切（P0-3「不信自报」）
            await self._display(display.render_lines("换不了人", "这张卡的小朋友还没绑定到这台便利贴，请家长先在小程序或平板上绑定。", mode="chat"))
        elif kind == "child" and action.get("child_id"):
            if self.child_id != action["child_id"]:
                # Do not let a previous child's pending model/audio reach the new child.
                await self._abort_speech(reason="child_switch", send_stop=True)
                self._history.clear()
                self._skill_lock = None
            self.child_id = action["child_id"]
            self.subject = ""
            await self._display(display.render_lines("你好 👋", f"已切换到 {self.child_id} 的便利贴", mode="chat"))
        elif kind == "subject" and value:                    # #18 学科卡
            self.subject = value
            await self._display(display.render_lines(f"{value}模式", f"已切到「{value}」。有题就按住说给我听。", mode="chat"))
        elif kind == "word" and value:                       # #19 单词收集卡
            await self._lookup_word(value + " 什么意思", now)
        elif kind == "mode" and value == "word":
            await self._serve_word(now)
        elif kind == "mode" and value == "task":
            await self._show_tasks()
        elif kind == "mode" and value == "book":                 # #book 我的书本卡
            await self._serve_book(now)
        elif kind == "mode" and value == "note":
            await self._display(display.render_lines("记想法", "想记点什么？按住说给我听。", mode="note"))
        elif kind == "skill" and value:
            await self._chat("使用技能 " + value, heard="", now=now)

    async def request_screen_snapshot(self) -> bytes:
        """Request the device's displayed framebuffer, never a server-side approximation."""
        if "screen_capture" not in self._features:
            raise RuntimeError("设备固件不支持屏幕采集")
        async with self._screen_lock:
            request_id = uuid.uuid4().hex
            future = asyncio.get_running_loop().create_future()
            self._screen_pending = {"id": request_id, "future": future, "parts": {}, "count": None, "bits": None}
            try:
                await self.send_json(json.dumps({"kind": "screen_snapshot_request", "request_id": request_id}))
                return await asyncio.wait_for(future, timeout=12)
            finally:
                self._screen_pending = None

    def _accept_screen_chunk(self, msg: Dict[str, Any]) -> None:
        pending = self._screen_pending
        if not pending or msg.get("request_id") != pending["id"] or pending["future"].done():
            return
        try:
            count, index, bits = msg.get("count"), msg.get("index"), msg.get("bits")
            data = msg.get("data")
            if type(count) is not int or type(index) is not int or type(bits) is not int or bits not in (1, 4) or not 1 <= count <= 4 or not 0 <= index < count or not isinstance(data, str) or len(data) > 20000:
                raise ValueError("invalid chunk")
            if pending["count"] not in (None, count) or pending["bits"] not in (None, bits) or index in pending["parts"]:
                raise ValueError("inconsistent chunk")
            pending["count"], pending["bits"] = count, bits
            pending["parts"][index] = data
            if len(pending["parts"]) == count:
                encoded = ''.join(pending["parts"][i] for i in range(count))
                raw = base64.b64decode(encoded, validate=True)
                from pocketclaw.screen_snapshot import framebuffer_png
                pending["future"].set_result(framebuffer_png(raw, bits))
        except (ValueError, binascii.Error):
            pending["future"].set_exception(RuntimeError("设备返回无效屏幕数据"))

    # ── 出站 ──
    async def _display(self, page: Dict[str, Any]) -> None:
        if page.get("mode"):
            self.mode = page["mode"]
        await self.send_json(protocol.display_page(page))

    async def _do_speak(self, text: str) -> None:
        """整段/流式朗读工作协程（自带 tts_start/tts_end）。运行在 _speech_task 后台协程中。"""
        if not text:
            return
        sr = getattr(self.tts, "sample_rate", protocol.AUDIO_SAMPLE_RATE)
        started = False
        cancelled = False

        async def _ensure_started() -> None:
            nonlocal started
            if not started:
                await self.send_json(protocol.tts_start(sr))
                started = True

        try:
            ok = await self._speak_frames(text, on_first_chunk=_ensure_started)
            if not ok:
                log.warning("pocketclaw TTS 无音频（provider=%s）→ 设备无声；查凭证/网络",
                            type(self.tts).__name__)
                await self.send_json(protocol.error("tts_unavailable", "语音播报暂时不可用"))
        except asyncio.CancelledError:
            cancelled = True
            raise
        except Exception:
            log.warning("pocketclaw 语音播报失败（provider=%s）", type(self.tts).__name__)
            try:
                await self.send_json(protocol.error("tts_unavailable", "语音播报暂时不可用"))
            except Exception:
                pass
        finally:
            if started and not cancelled:
                try:
                    await self.send_json(protocol.tts_end())
                except Exception:
                    pass
            try:
                cur_task = asyncio.current_task()
            except RuntimeError:
                cur_task = None
            if self._speech_task is cur_task:
                self._speech_task = None

    async def _speak(self, text: str, *, wait: bool = False) -> None:
        """整段朗读（自带 tts_start/tts_end）。支持异步非阻塞流式播放与毫秒级打断。"""
        if not text:
            return
        # 取消此前未完成的朗读任务，并下发 tts_stop 排空设备端可能留存的半截音频与打断状态
        if self._speech_task is not None and not self._speech_task.done():
            self._speech_task.cancel()
            try:
                await self._speech_task
            except (asyncio.CancelledError, Exception):
                pass
            self._speech_task = None
            try:
                await self.send_json(protocol.tts_stop())
            except Exception:
                pass

        self._speech_task = asyncio.create_task(self._do_speak(text))
        if wait:
            await self._speech_task
        else:
            # 让 _do_speak 立即有机会启动并发出 tts_start
            await asyncio.sleep(0)

    async def _speak_frames(self, text: str, *, on_first_chunk: Optional[Callable[[], Awaitable[None]]] = None) -> bool:
        """只发音频帧，**不发 tts_start/tts_end**（若传入 on_first_chunk 则在首包音频就绪瞬间回调）。
        返回是否发出过音频。

        逐句流式朗读要这个：一轮只在最外层首帧到达时发一次 start、收尾发一次 end，中间连续送帧，
        设备既不会因云端合成慢而触发 8000ms 空缓冲超时，也不会在句与句之间反复进出 Speaking 态。
        """
        if not text:
            return False
        sr = getattr(self.tts, "sample_rate", protocol.AUDIO_SAMPLE_RATE)
        # 按略快于播放的节奏下发（0.7x 实时）：既维持设备端 32KB 缓冲有充分安全余量（抗网络抖动与局刷），又不过度冲击网络
        pace_s = (protocol.AUDIO_FRAME_MS / 1000.0) * 0.70
        streamer = getattr(self.tts, "synthesize_stream", None)
        sent = 0
        if streamer is not None:
            # **流式**：合成一块发一块。首块音频真正到达时再触发 on_first_chunk (tts_start)，
            # 彻底消除设备端等待首包音频 >8000ms 触发空缓冲超时自救的问题。
            leftover = b""
            started = False
            try:
                async for piece in streamer(text):
                    if not piece:
                        continue
                    if not started:
                        started = True
                        if on_first_chunk is not None:
                            await on_first_chunk()
                    buf = leftover + piece
                    frame = protocol.AUDIO_FRAME_BYTES if hasattr(protocol, "AUDIO_FRAME_BYTES") \
                        else int(sr * 2 * protocol.AUDIO_FRAME_MS / 1000)
                    n = (len(buf) // frame) * frame
                    for off in range(0, n, frame):
                        await self.send_bin(buf[off:off + frame])
                        sent += frame
                        # 首包突发预充：前 9600 字节（~300ms）不休眠极速推入设备，瞬间越过设备 6400 字节起播门，彻底杜绝等音频超时卡死
                        if sent > 9600:
                            await asyncio.sleep(pace_s)
                    leftover = buf[n:]
            except asyncio.CancelledError:
                raise                      # 收尾(tts_end)由调用方负责，见 _speak / _chat
            if leftover and started:
                await self.send_bin(leftover)
                sent += len(leftover)
            return started
        # 回落：不支持流式的 provider（OfflineTTS 等）走整段
        pcm = await self.tts.synthesize(text)
        if not pcm:
            return False
        if on_first_chunk is not None:
            await on_first_chunk()
        for chunk in protocol.frame_pcm(pcm):
            await self.send_bin(chunk)
            sent += len(chunk)
            if sent > 9600:
                await asyncio.sleep(pace_s)
        return True

    # ── 家长管控 ──
    def _unbound(self) -> bool:
        """严格模式下，本会话的设备还没被已认证方绑定到任何孩子（身份是 unbound-<hash> 或空）。"""
        return self._strict and is_unbound(self.child_id)

    def _quiet_now(self, now: float) -> str:
        if not self.controls:
            return ""
        hour = time.localtime(now).tm_hour
        return quiet_reason(self.controls.get(), hour)


def _as_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _day_key(now: float) -> str:
    """本地日期键（早安推屏按天记账用），与 DailySpark 的缓存日期同口径。"""
    return time.strftime("%Y-%m-%d", time.localtime(now))


def _contains_any(text: str, needles) -> bool:
    return any(n in text for n in needles)


def _startswith_any(text: str, prefixes) -> bool:
    t = text.lstrip()
    return any(t.startswith(p) for p in prefixes)


def _endswith_any(text: str, suffixes) -> bool:
    t = text.rstrip().rstrip("。.!！~ ")
    return any(t.endswith(s) for s in suffixes)


# 想法开头的口头 filler（「记一下 一个事，星期六…」→ 去掉「一个事，」）
_NOTE_FILLERS = ("一个事情", "一个事儿", "一个事", "个事情", "个事儿", "个事",
                 "有个事", "有件事", "一件事情", "一件事", "件事")


# 命令包裹词：「把 X 这个想法 去掉」→ 剥掉「把」「这个想法」拿到 X
_WRAP_LEAD = ("帮我把", "帮我", "麻烦把", "把", "将")
_WRAP_TAIL = ("这个想法", "这条想法", "那个想法", "那条想法", "这个念头", "这条念头",
              "这个事情", "这个事", "这条事", "这个", "这条", "那个", "那条", "这", "那")


def _strip_command_wrapper(t: str) -> str:
    t = (t or "").strip()
    for w in _WRAP_LEAD:
        if t.startswith(w):
            t = t[len(w):]
            break
    for w in _WRAP_TAIL:
        if t.endswith(w):
            t = t[: -len(w)]
            break
    return t.strip("：: ，,。、的 ")


def _strip_note_filler(text: str) -> str:
    t = (text or "").strip()
    changed = True
    while changed:
        changed = False
        for f in _NOTE_FILLERS:
            if t.startswith(f):
                t = t[len(f):].lstrip("：: ，,。、 ")
                changed = True
                break
    return t


_WD_MAP = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6, "末": 5}
_REL_DAY = {"大后天": 3, "后天": 2, "明天": 1, "今天": 0, "昨天": -1}
_WD_RE = re.compile(r"(下个|下|本|这个|这)?(星期|周|礼拜)([一二三四五六日天末])")


def _annotate_dates(text: str, now: float) -> str:
    """把相对时间（明天/星期六/下周三…）后面补一个绝对日期「（M/D）」。"""
    if not text:
        return text
    base = datetime.date.fromtimestamp(now)
    for word, delta in _REL_DAY.items():
        if word in text and (word + "（") not in text:
            d = base + datetime.timedelta(days=delta)
            text = text.replace(word, f"{word}（{d.month}/{d.day}）", 1)

    def _repl(m):
        prefix, marker, wd = m.group(1) or "", m.group(2), m.group(3)
        target = _WD_MAP.get(wd)
        if target is None:
            return m.group(0)
        days = (target - base.weekday()) % 7
        if prefix in ("下", "下个"):
            days += 7
        d = base + datetime.timedelta(days=days)
        return f"{m.group(0)}（{d.month}/{d.day}）"

    return _WD_RE.sub(_repl, text)


def _strip_suffix(text: str, suffixes) -> str:
    t = text.rstrip().rstrip("。.!！~ ")
    for s in suffixes:
        if t.endswith(s):
            return t[: -len(s)].rstrip("的了 ，,").strip()
    return t


def _strip_trigger(text: str, triggers) -> str:
    t = text.strip()
    for tr in triggers:
        if t.startswith(tr):
            return t[len(tr):].lstrip("：: ,，").strip()
        if tr in t:
            return t.replace(tr, "", 1).strip()
    return t
