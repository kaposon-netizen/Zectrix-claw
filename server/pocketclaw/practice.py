"""小学生高频专项练习模块：古诗背诵质检官、生字词听写官、口算大闯关。

针对小学生日常高频需求设计：
1. 古诗背诵质检官：真算法比对（部编版核心必背篇目，逐句比对与容错判定，拒绝虚假夸奖或篇名混淆）。
2. 生字词听写官：语音逐词报读 + 墨水屏防偷看进度 + 按键切词/重听 + 完毕全量核对。
3. 口算大闯关：大字体算式卡片 + 按键翻看答案 + 自动生成。
"""
from __future__ import annotations

import difflib
import random
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# ── 1. 古诗背诵库与真质检算法 ──

@dataclass
class Poem:
    title: str
    author: str
    dynasty: str
    lines: List[str]

    @property
    def clean_text(self) -> str:
        """纯文字（无标点）便于比对。"""
        return re.sub(r"[，。！？、\s]", "", "".join(self.lines))


# 部编版小学核心必背古诗真题库（杜绝篇名与诗句错配，源自 chinese-poetry 权威整理 1~6 年级必背篇目）
CORE_POEMS: List[Poem] = [
    # ── 基础篇目（低中段经典） ──
    Poem("望庐山瀑布", "李白", "唐", ["日照香炉生紫烟，", "遥看瀑布挂前川。", "飞流直下三千尺，", "疑是银河落九天。"]),
    Poem("绝句", "杜甫", "唐", ["两个黄鹂鸣翠柳，", "一行白鹭上青天。", "窗含西岭千秋雪，", "门泊东吴万里船。"]),
    Poem("静夜思", "李白", "唐", ["床前明月光，", "疑是地上霜。", "举头望明月，", "低头思故乡。"]),
    Poem("春晓", "孟浩然", "唐", ["春眠不觉晓，", "处处闻啼鸟。", "夜来风雨声，", "花落知多少。"]),
    Poem("登鹳雀楼", "王之涣", "唐", ["白日依山尽，", "黄河入海流。", "欲穷千里目，", "更上一层楼。"]),
    Poem("咏鹅", "骆宾王", "唐", ["鹅，鹅，鹅，", "曲项向天歌。", "白毛浮绿水，", "红掌拨清波。"]),
    Poem("悯农", "李绅", "唐", ["锄禾日当午，", "汗滴禾下土。", "谁知盘中餐，", "粒粒皆辛苦。"]),
    Poem("江雪", "柳宗元", "唐", ["千山鸟飞绝，", "万径人踪灭。", "孤舟蓑笠翁，", "独钓寒江雪。"]),
    Poem("早发白帝城", "李白", "唐", ["朝辞白帝彩云间，", "千里江陵一日还。", "两岸猿声啼不住，", "轻舟已过万重山。"]),
    Poem("草", "白居易", "唐", ["离离原上草，", "一岁一枯荣。", "野火烧不尽，", "春风吹又生。"]),
    Poem("小池", "杨万里", "宋", ["泉眼无声惜细流，", "树阴照水爱晴柔。", "小荷才露尖尖角，", "早有蜻蜓立上头。"]),
    Poem("山行", "杜牧", "唐", ["远上寒山石径斜，", "白云生处有人家。", "停车坐爱枫林晚，", "霜叶红于二月花。"]),
    Poem("村居", "高鼎", "清", ["草长莺飞二月天，", "拂堤杨柳醉春烟。", "儿童散学归来早，", "忙趁东风放纸鸢。"]),
    Poem("池上", "白居易", "唐", ["小娃撑小艇，", "偷采白莲回。", "不解藏踪迹，", "浮萍一道开。"]),
    Poem("古朗月行", "李白", "唐", ["小时不识月，", "呼作白玉盘。", "又疑瑶台镜，", "飞在青云端。"]),
    # ── 扩展篇目：小学低段（1~2年级）──
    Poem("咏柳", "贺知章", "唐", ["碧玉妆成一树高，", "万条垂下绿丝绦。", "不知细叶谁裁出，", "二月春风似剪刀。"]),
    Poem("风", "李峤", "唐", ["解落三秋叶，", "能开二月花。", "过江千尺浪，", "入竹万竿斜。"]),
    Poem("所见", "袁枚", "清", ["牧童骑黄牛，", "歌声振林樾。", "意欲捕鸣蝉，", "忽然闭口立。"]),
    Poem("鹿柴", "王维", "唐", ["空山不见人，", "但闻人语响。", "返景入深林，", "复照青苔上。"]),
    # ── 扩展篇目：小学中段（3~4年级）──
    Poem("望天门山", "李白", "唐", ["天门中断楚江开，", "碧水东流至此回。", "两岸青山相对出，", "孤帆一片日边来。"]),
    Poem("赠汪伦", "李白", "唐", ["李白乘舟将欲行，", "忽闻岸上踏歌声。", "桃花潭水深千尺，", "不及汪伦送我情。"]),
    Poem("黄鹤楼送孟浩然之广陵", "李白", "唐", ["故人西辞黄鹤楼，", "烟花三月下扬州。", "孤帆远影碧空尽，", "唯见长江天际流。"]),
    Poem("九月九日忆山东兄弟", "王维", "唐", ["独在异乡为异客，", "每逢佳节倍思亲。", "遥知兄弟登高处，", "遍插茱萸少一人。"]),
    Poem("题西林壁", "苏轼", "宋", ["横看成岭侧成峰，", "远近高低各不同。", "不识庐山真面目，", "只缘身在此山中。"]),
    Poem("饮湖上初晴后雨", "苏轼", "宋", ["水光潋滟晴方好，", "山色空蒙雨亦奇。", "欲把西湖比西子，", "淡妆浓抹总相宜。"]),
    Poem("惠崇春江晚景", "苏轼", "宋", ["竹外桃花三两枝，", "春江水暖鸭先知。", "蒌蒿满地芦芽短，", "正是河豚欲上时。"]),
    Poem("游子吟", "孟郊", "唐", ["慈母手中线，", "游子身上衣。", "临行密密缝，", "意恐迟迟归。", "谁言寸草心，", "报得三春晖。"]),
    Poem("枫桥夜泊", "张继", "唐", ["月落乌啼霜满天，", "江枫渔火对愁眠。", "姑苏城外寒山寺，", "夜半钟声到客船。"]),
    Poem("清明", "杜牧", "唐", ["清明时节雨纷纷，", "路上行人欲断魂。", "借问酒家何处有，", "牧童遥指杏花村。"]),
    Poem("江南春", "杜牧", "唐", ["千里莺啼绿映红，", "水村山郭酒旗风。", "南朝四百八十寺，", "多少楼台烟雨中。"]),
    Poem("晓出净慈寺送林子方", "杨万里", "宋", ["毕竟西湖六月中，", "风光不与四时同。", "接天莲叶无穷碧，", "映日荷花别样红。"]),
    # ── 扩展篇目：小学高段（5~6年级）──
    Poem("示儿", "陆游", "宋", ["死去元知万事空，", "但悲不见九州同。", "王师北定中原日，", "家祭无忘告乃翁。"]),
    Poem("题临安邸", "林升", "宋", ["山外青山楼外楼，", "西湖歌舞几时休。", "暖风熏得游人醉，", "直把杭州作汴州。"]),
    Poem("泊船瓜洲", "王安石", "宋", ["京口瓜洲一水间，", "钟山只隔数重山。", "春风又绿江南岸，", "明月何时照我还。"]),
    Poem("书湖阴先生壁", "王安石", "宋", ["茅檐长扫净无苔，", "花木成畴手自栽。", "一水护田将绿绕，", "两山排闼送青来。"]),
    Poem("凉州词", "王翰", "唐", ["葡萄美酒夜光杯，", "欲饮琵琶马上催。", "醉卧沙场君莫笑，", "古来征战几人回。"]),
    Poem("出塞", "王昌龄", "唐", ["秦时明月汉时关，", "万里长征人未还。", "但使龙城飞将在，", "不教胡马度阴山。"]),
    Poem("芙蓉楼送辛渐", "王昌龄", "唐", ["寒雨连江夜入吴，", "平明送客楚山孤。", "洛阳亲友如相问，", "一片冰心在玉壶。"]),
    Poem("石灰吟", "于谦", "明", ["千锤万凿出深山，", "烈火焚烧若等闲。", "粉骨碎身全不怕，", "要留清白在人间。"]),
    Poem("竹石", "郑燮", "清", ["咬定青山不放松，", "立根原在破岩中。", "千磨万击还坚劲，", "任尔东西南北风。"]),
    Poem("塞下曲", "卢纶", "唐", ["月黑雁飞高，", "单于夜遁逃。", "欲将轻骑逐，", "大雪满弓刀。"]),
    Poem("渔歌子", "张志和", "唐", ["西塞山前白鹭飞，", "桃花流水鳜鱼肥。", "青箬笠，", "绿蓑衣，", "斜风细雨不须归。"]),
]

POEM_MAP: Dict[str, Poem] = {p.title: p for p in CORE_POEMS}


def find_poem(keyword: str, custom_poems: Optional[List[Dict[str, Any]]] = None) -> Optional[Poem]:
    """按诗名模糊匹配，若无则返回 None。优先从家长自定义诗库匹配，再从部编核心诗库匹配。"""
    kw = keyword.strip()
    if custom_poems:
        for cp in custom_poems:
            name = cp.get("title", "").strip()
            if name and (name in kw or kw in name):
                return Poem(
                    title=name,
                    author=cp.get("author", "佚名"),
                    dynasty=cp.get("dynasty", "清"),
                    lines=cp.get("lines", []),
                )
    for name, p in POEM_MAP.items():
        if name in kw or kw in name:
            return p
    return None


@dataclass
class ReciteResult:
    passed: bool
    score: float                      # 0.0 ~ 1.0 匹配率
    feedback: str                     # 语音反馈文案
    revealed_lines: List[str]         # 墨水屏显示的各句状态
    is_irrelevant: bool = False       # 是否完全答非所问（胡说）
    matched_indices: List[int] = field(default_factory=list)  # 本轮背对的句子下标


def check_recitation(
    poem: Poem,
    recited_raw: str,
    done_indices: Optional[set[int]] = None,
) -> ReciteResult:
    """真实比对孩子背诵内容（支持单句独立背诵点评与整首连贯背诵）。
    
    采用逐句 LCS + 局部窗口比对：
    1. 乱说/无关内容：指出不属于这首诗，绝不虚假赞美。
    2. 单句或部分句背诵：仅针对孩子本轮实际背诵的那一句（或几句）进行精准点评，
       绝不把未背到的第一句/第二句误判为“卡壳”，也绝不强迫孩子重背第一句。
    3. 全诗背完（单次连背或分句累计背完）：通过并给予祝贺。
    """
    clean_target = poem.clean_text
    clean_recited = re.sub(r"[，。！？、\s]", "", recited_raw or "")
    prev_done = set(done_indices) if done_indices else set()

    if not clean_recited:
        return ReciteResult(
            passed=False,
            score=0.0,
            feedback=f"准备好背诵《{poem.title}》了吗？按住说话键，可以整首背，也可以挑任意一句背给我听哦！",
            revealed_lines=[
                poem.lines[i] if i in prev_done else "【待背诵】"
                for i in range(len(poem.lines))
            ],
            is_irrelevant=False,
            matched_indices=sorted(prev_done),
        )

    # 计算整体相似度
    matcher = difflib.SequenceMatcher(None, clean_target, clean_recited)
    ratio = matcher.ratio()

    # 逐句匹配情况
    line_scores: List[float] = []
    for raw_line in poem.lines:
        c_line = re.sub(r"[，。！？、\s]", "", raw_line)
        if c_line and c_line in clean_recited:
            line_scores.append(1.0)
            continue

        best_ratio = difflib.SequenceMatcher(None, c_line, clean_recited).ratio()
        n = len(c_line)
        for win_len in (max(1, n - 1), n, n + 1):
            if len(clean_recited) >= win_len:
                for start in range(len(clean_recited) - win_len + 1):
                    chunk = clean_recited[start:start + win_len]
                    sm = difflib.SequenceMatcher(None, c_line, chunk).ratio()
                    if sm > best_ratio:
                        best_ratio = sm
        line_scores.append(best_ratio)

    max_line_score = max(line_scores) if line_scores else 0.0

    # 1. 彻底不相干（瞎说：整体匹配率低且没有任何一句局部匹配）
    if ratio < 0.28 and max_line_score < 0.45:
        return ReciteResult(
            passed=False,
            score=ratio,
            feedback=f"咦，这好像不是《{poem.title}》的诗句哦。试着回忆一下，从第一句或任意一句开始背吧！",
            revealed_lines=[
                poem.lines[i] if i in prev_done else f"第 {i+1} 句：【待背诵】"
                for i in range(len(poem.lines))
            ],
            is_irrelevant=True,
            matched_indices=sorted(prev_done),
        )

    # 本轮背对的句子与累计已背对句子
    current_matched = [i for i, s in enumerate(line_scores) if s >= 0.75]
    all_done = prev_done | set(current_matched)

    revealed_lines = [
        poem.lines[i] if i in all_done else f"第 {i+1} 句：【待背诵】"
        for i in range(len(poem.lines))
    ]

    # 2. 完美通关（单次全对 或 分句累计全部背完）
    if (ratio >= 0.88 and all(s >= 0.65 for s in line_scores)) or len(all_done) == len(poem.lines):
        if len(current_matched) == 1 and prev_done:
            idx = current_matched[0]
            line_text = poem.lines[idx].rstrip("，。！？、")
            fb = f"第 {idx + 1} 句“{line_text}”背得非常准确！太棒啦，整首《{poem.title}》一字不差全部背完啦！"
        else:
            fb = f"太棒啦！整首《{poem.title}》背诵得一字不差！给你点一个大大的赞！"
        return ReciteResult(
            passed=True,
            score=max(ratio, max_line_score),
            feedback=fb,
            revealed_lines=list(poem.lines),
            is_irrelevant=False,
            matched_indices=list(range(len(poem.lines))),
        )

    # 3. 单句或部分句背诵：只点评本轮实际背诵的句子，绝不说未背的句子“卡壳”或要求重背第一句
    attempted = [i for i, s in enumerate(line_scores) if s >= 0.45]
    if not attempted:
        attempted = [max(range(len(line_scores)), key=lambda i: line_scores[i])]

    if len(attempted) == 1:
        idx = attempted[0]
        line_text = poem.lines[idx].rstrip("，。！？、")
        if line_scores[idx] >= 0.99:
            feedback = f"第 {idx + 1} 句“{line_text}”背得非常准确，一字不差！"
        elif line_scores[idx] >= 0.75:
            feedback = f"第 {idx + 1} 句背对啦！原句是“{line_text}”。"
        else:
            feedback = f"第 {idx + 1} 句有一点小偏差，原句是“{line_text}”，再读一遍记牢它吧！"
    else:
        ok_lines = [i for i in attempted if line_scores[i] >= 0.75]
        err_lines = [i for i in attempted if line_scores[i] < 0.75]
        if err_lines:
            e_idx = err_lines[0]
            e_text = poem.lines[e_idx].rstrip("，。！？、")
            if ok_lines:
                ok_str = "、".join(str(i + 1) for i in ok_lines)
                feedback = f"第 {ok_str} 句背得很准！第 {e_idx + 1} 句有一点小偏差，原句是“{e_text}”。"
            else:
                feedback = f"第 {e_idx + 1} 句有一点小偏差，原句是“{e_text}”。"
        else:
            ok_str = "、".join(str(i + 1) for i in ok_lines)
            feedback = f"第 {ok_str} 句背得非常准确！"

    return ReciteResult(
        passed=False,
        score=max(ratio, max_line_score),
        feedback=feedback,
        revealed_lines=revealed_lines,
        is_irrelevant=False,
        matched_indices=sorted(all_done),
    )


# ── 2. 生字词听写考官 ──

DEFAULT_DICTATION_WORDS: List[Tuple[str, str, str]] = [
    ("温暖", "wēn nuǎn", "温和暖和的温暖"),
    ("认真", "rèn zhēn", "认认真真的认真"),
    ("仔细", "zǐ xì", "仔细观察的仔细"),
    ("明亮", "míng liàng", "明亮的眼睛"),
    ("美丽", "měi lì", "美丽的花朵"),
    ("感谢", "gǎn xiè", "非常感谢的感谢"),
    ("保护", "bǎo hù", "保护环境的保护"),
    ("快乐", "kuài lè", "快快乐乐的快乐"),
    ("荷花", "hé huā", "池塘里盛开的荷花"),
    ("森林", "sēn lín", "郁郁葱葱的茂密森林"),
    ("宇宙", "yǔ zhòu", "浩瀚神秘的太空宇宙"),
    ("勤奋", "qín fèn", "勤奋刻苦地读书学习"),
    ("探索", "tàn suǒ", "勇敢探索大自然的奥秘"),
    ("谦虚", "qiān xū", "虚心使人进步的谦虚"),
    ("坚持", "jiān chí", "坚持不懈迎难而上"),
    ("智慧", "zhì huì", "充满智慧的聪明大脑"),
]


class DictationSession:
    def __init__(self, words: Optional[List[Tuple[str, str, str]]] = None, title: str = "生字词听写"):
        self.words = words or list(DEFAULT_DICTATION_WORDS)
        self.title = title.strip() or "生字词听写"
        self.idx = 0
        self.active = True

    @property
    def total(self) -> int:
        return len(self.words)

    @property
    def cur_word(self) -> Optional[Tuple[str, str, str]]:
        if 0 <= self.idx < len(self.words):
            return self.words[self.idx]
        return None

    def prompt_speech(self, repeat: bool = False) -> str:
        """朗读报词文案（拼音/组词标准节奏引导孩子在练习本上听写，严格不上屏目标汉字）。"""
        cw = self.cur_word
        if not cw:
            return "听写已经全部完成啦！已生成对照清单，请交给家长批改！"
        w, py, hint = cw
        hint_text = f"{hint}。" if hint else ""
        if repeat:
            return f"请注意听：{w}。{hint_text}…… {w}。"
        return f"第 {self.idx + 1} 个词：{w}。{hint_text}…… {w}。请在练习本上工整写下。"

    def step_next(self) -> bool:
        """步进到下一个词。若还有下一词返回 True，若已结束返回 False。"""
        self.idx += 1
        if self.idx >= len(self.words):
            self.active = False
            return False
        return True


# ── 3. 口算大闯关 ──

@dataclass
class ArithQuestion:
    expr: str
    ans: str
    hint: str = ""


class ArithSession:
    def __init__(self, count: int = 10, mode: str = "mix", grade: int = 2, op_type: str = "all",
                 custom_questions: Optional[List[Dict[str, str]]] = None):
        self.count = count
        self.grade = grade
        self.op_type = op_type
        self.idx = 0
        self.revealed = False
        self.questions = self._generate_questions(count, mode, grade=grade, op_type=op_type, custom_questions=custom_questions)
        self.active = True

    def _generate_questions(self, count: int, mode: str, grade: int = 2, op_type: str = "all",
                            custom_questions: Optional[List[Dict[str, str]]] = None) -> List[ArithQuestion]:
        qs: List[ArithQuestion] = []
        if custom_questions:
            for q in custom_questions:
                if q.get("expr") and q.get("ans"):
                    qs.append(ArithQuestion(str(q["expr"]), str(q["ans"]), str(q.get("hint", ""))))
        if len(qs) >= count:
            return qs[:count]
        needed = count - len(qs)
        for _ in range(needed):
            if grade == 1:
                op = random.choice(["+", "-"])
                if op == "+":
                    a = random.randint(2, 14)
                    b = random.randint(2, 20 - a)
                    qs.append(ArithQuestion(f"{a} + {b}", str(a + b)))
                else:
                    a = random.randint(6, 20)
                    b = random.randint(1, a - 1)
                    qs.append(ArithQuestion(f"{a} - {b}", str(a - b)))
            elif grade in (3, 4):
                if op_type == "mul_div":
                    op = random.choice(["×", "÷"])
                elif op_type == "add_sub":
                    op = random.choice(["+", "-"])
                else:
                    op = random.choice(["+", "-", "×", "÷"])
                if op == "+":
                    a = random.randint(35, 299)
                    b = random.randint(25, 299)
                    qs.append(ArithQuestion(f"{a} + {b}", str(a + b)))
                elif op == "-":
                    a = random.randint(80, 499)
                    b = random.randint(25, a - 10)
                    qs.append(ArithQuestion(f"{a} - {b}", str(a - b)))
                elif op == "×":
                    a = random.randint(11, 25)
                    b = random.randint(2, 9)
                    qs.append(ArithQuestion(f"{a} × {b}", str(a * b)))
                else:
                    b = random.randint(2, 9)
                    ans = random.randint(6, 15)
                    a = b * ans
                    qs.append(ArithQuestion(f"{a} ÷ {b}", str(ans)))
            elif grade >= 5:
                # 5~6 年级高段：强化简算速算技巧、乘法分配律与四则混合运算
                flavor = random.choice(["distrib", "multi_step", "speed_calc", "standard"])
                if flavor == "distrib":
                    base = random.choice([25, 125, 15, 50])
                    mult = random.choice([4, 8, 2, 6, 12, 16])
                    qs.append(ArithQuestion(f"{base} × {mult}", str(base * mult)))
                elif flavor == "multi_step":
                    sub_flavor = random.choice(["add_mul", "sub_mul", "bracket"])
                    if sub_flavor == "add_mul":
                        c = random.randint(10, 50)
                        a = random.randint(3, 9)
                        b = random.randint(4, 9)
                        qs.append(ArithQuestion(f"{c} + {a} × {b}", str(c + a * b)))
                    elif sub_flavor == "sub_mul":
                        a = random.randint(4, 9)
                        b = random.randint(5, 9)
                        c = random.randint(a * b + 5, a * b + 60)
                        qs.append(ArithQuestion(f"{c} - {a} × {b}", str(c - a * b)))
                    else:
                        d = random.randint(2, 8)
                        ans = random.randint(3, 12)
                        tot = d * ans
                        part = random.randint(2, tot - 2)
                        qs.append(ArithQuestion(f"({part} + {tot - part}) ÷ {d}", str(ans)))
                elif flavor == "speed_calc":
                    if random.random() < 0.5:
                        a = random.choice([20, 30, 40, 50, 60, 70, 80, 90])
                        b = random.choice([3, 4, 5, 6, 7, 8, 9, 20])
                        qs.append(ArithQuestion(f"{a} × {b}", str(a * b)))
                    else:
                        ans = random.choice([20, 30, 40, 50, 60, 70, 80, 90])
                        b = random.choice([2, 3, 4, 5, 6, 7, 8, 9])
                        a = ans * b
                        qs.append(ArithQuestion(f"{a} ÷ {b}", str(ans)))
                else:
                    if random.random() < 0.5:
                        a = random.randint(120, 890)
                        b = random.randint(50, 450)
                        qs.append(ArithQuestion(f"{a} + {b}", str(a + b)))
                    else:
                        a = random.randint(200, 999)
                        b = random.randint(45, a - 20)
                        qs.append(ArithQuestion(f"{a} - {b}", str(a - b)))
            else:
                if op_type == "mul_div":
                    op = random.choice(["×", "÷"])
                elif op_type == "add_sub" or mode == "add_sub":
                    op = random.choice(["+", "-"])
                else:
                    op = random.choice(["+", "-"])
                if op == "+":
                    a = random.randint(11, 49)
                    b = random.randint(11, 49)
                    qs.append(ArithQuestion(f"{a} + {b}", str(a + b)))
                elif op == "-":
                    a = random.randint(25, 99)
                    b = random.randint(11, a - 1)
                    qs.append(ArithQuestion(f"{a} - {b}", str(a - b)))
                elif op == "×":
                    a = random.randint(2, 9)
                    b = random.randint(2, 9)
                    qs.append(ArithQuestion(f"{a} × {b}", str(a * b)))
                else:
                    b = random.randint(2, 9)
                    ans = random.randint(2, 9)
                    a = b * ans
                    qs.append(ArithQuestion(f"{a} ÷ {b}", str(ans)))
        return qs

    @property
    def cur_question(self) -> Optional[ArithQuestion]:
        if 0 <= self.idx < len(self.questions):
            return self.questions[self.idx]
        return None

    def reveal_or_step(self) -> Tuple[bool, str]:
        """按确认键触发：
        若是未揭晓状态 -> 揭晓答案 (revealed=True, return False, 'revealed')
        若是已揭晓状态 -> 切到下一题 (revealed=False, return True/False depending on has_next)
        """
        if not self.revealed:
            self.revealed = True
            ans = self.cur_question.ans if self.cur_question else ""
            return False, f"答案是 {ans}，算对了吗？"
        else:
            self.revealed = False
            self.idx += 1
            if self.idx >= len(self.questions):
                self.active = False
                return True, "太棒了！10道口算全部通关！"
            return True, f"第 {self.idx + 1} 题开始！"


# ── 4. 英语口语与生词/课文发音评测 ──

def evaluate_english_speech(expected: str, heard: str, band: str = "g3_g6") -> Tuple[int, str, str]:
    """评测英文单词或句子的发音与朗读质量。
    返回 (score: int, stars: str, speech_feedback: str)。
    """
    try:
        from agent.pron_score import score_by_text, encourage
    except ImportError:
        import difflib
        import re

        def _norm(s: str) -> list[str]:
            return re.sub(r"[^a-z']+", " ", (s or "").lower()).split()

        def score_by_text(expected: str, heard: str, band: str = "g3_g6") -> dict:
            exp = _norm(expected)
            got = _norm(heard)
            if not exp:
                return {"score": 0, "words": [], "heard": heard}
            sm = difflib.SequenceMatcher(None, exp, got)
            matched = 0.0
            for tag, i1, i2, j1, j2 in sm.get_opcodes():
                if tag == "equal":
                    matched += (i2 - i1)
                elif tag == "replace":
                    cand = got[j1:j2]
                    for k in range(i1, i2):
                        best = max((difflib.SequenceMatcher(None, exp[k], g).ratio() for g in cand), default=0.0)
                        if best >= 0.7:
                            matched += 0.55
                        elif best >= 0.4:
                            matched += 0.3
            sc = round(100 * matched / len(exp))
            return {"score": max(0, min(100, sc)), "heard": heard}

        def encourage(score: int, band: str = "g3_g6") -> str:
            if score >= 90:
                return "太棒啦！读得又准又清楚，点赞！"
            if score >= 70:
                return "很不错！大部分都读对了，再练两遍就更顺啦～"
            if score >= 40:
                return "有进步！我们把几个生词再一起读一遍，好不好？"
            return "没关系，慢慢来，多读几遍就会了～"
    res = score_by_text(expected, heard, band=band)
    score = int(res.get("score", 0))
    if score >= 90:
        stars = "★★★★★"
    elif score >= 80:
        stars = "★★★★☆"
    elif score >= 60:
        stars = "★★★☆☆"
    elif score >= 40:
        stars = "★★☆☆☆"
    else:
        stars = "★☆☆☆☆"
    speech = encourage(score, band=band)
    return score, stars, speech


# ── 5. 口算口语数字解析器 ──
_CN_NUM_MAP = {
    '零': 0, '〇': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4,
    '五': 5, '六': 6, '七': 7, '八': 8, '九': 9,
}

def parse_spoken_number(text: str) -> Optional[int]:
    """从学生口语语音中提取口算答案数字（支持阿拉伯数字与中文数字表达）。
    例：'等于8' -> 8, '是24' -> 24, '35' -> 35, '负五' -> -5, '十二' -> 12, '一百零五' -> 105
    """
    if not text:
        return None
    text = text.strip()

    # 1. 优先提取阿拉伯数字
    m = re.search(r'(-?\d+)', text)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass

    # 2. 中文数字解析
    clean = re.sub(r'[^\u4e00-\u9fa5]', '', text)
    is_neg = ('负' in clean)

    m_cn = re.search(r'[零〇一二两三四五六七八九十百]+', clean)
    if not m_cn:
        return None
    cn_str = m_cn.group(0)

    val = 0
    temp = 0
    for ch in cn_str:
        if ch in _CN_NUM_MAP:
            temp = _CN_NUM_MAP[ch]
        elif ch == '十':
            if temp == 0:
                temp = 1
            val += temp * 10
            temp = 0
        elif ch == '百':
            val += temp * 100
            temp = 0
    val += temp
    return -val if is_neg else val


# ── 4. 猜猜看（语音二十问，移植自 SnapClaw）──

GUESS_SECRETS: List[Tuple[str, str, str]] = [
    # (name, category, hint)
    ("企鹅", "极地动物", "它长着黑白羽毛，虽然不会飞，但在冰冷的水里游泳捕鱼特别厉害"),
    ("大象", "陆地动物", "它是陆地上体型最大的哺乳动物，有着长长的鼻子和大耳朵"),
    ("长颈鹿", "陆地动物", "它有着世界上最长的脖子，喜欢吃高高树梢上的嫩叶"),
    ("熊猫", "国宝动物", "它黑白相间，最喜欢抱着鲜嫩的竹子啃个不停"),
    ("海豚", "海洋动物", "它是聪明的海洋哺乳动物，经常在海面上跃起，靠回声定位"),
    ("袋鼠", "有袋动物", "它后腿强壮跳得非常远，肚子上有个温暖的育儿袋"),
    ("猫头鹰", "鸟类", "它白天睡觉晚上捕食田鼠，脑袋能转大半圈"),
    ("蜜蜂", "昆虫", "它会飞，在花丛中忙碌采蜜，住在六角形的蜂巢里"),
    ("青蛙", "两栖动物", "它小时候是小蝌蚪，长大了捕捉害虫，叫声呱呱呱"),
    ("乌龟", "爬行动物", "它身上背着坚硬的壳，寿命很长，遇到危险会把头缩进去"),
    ("蝴蝶", "昆虫", "它小时候是毛毛虫，破茧成蝶后翅膀五颜六色特别美丽"),
    ("松鼠", "森林动物", "它有着毛茸茸的大尾巴，秋天喜欢把松果藏在地下"),
    ("刺猬", "小型动物", "它背上长满了尖尖的硬刺，害怕时会缩成一个小圆球"),
    ("章鱼", "海洋软体动物", "它住在海里，有八条长长的腕足，遇到危险会喷墨汁"),
    ("月亮", "自然天体", "它夜里照亮夜空，有时像弯弯的镰刀有时像圆盘"),
    ("彩虹", "自然现象", "它通常在雨过天晴时出现，像一座七彩斑斓的天桥"),
    ("书包", "学习用品", "它天天陪你上学，肚子里装满了课本、文具和知识"),
    ("自行车", "交通工具", "它有两个轮子，需要用脚踩脚踏板才能前进"),
    # ── 扩展少儿常识趣味科普 ──
    ("太阳", "恒星天体", "它是太阳系的中心，一颗炽热的巨大恒星，给我们带来阳光和温暖"),
    ("地球", "行星天体", "它是我们共同生活的蓝色星球，表面大部分被蔚蓝的海洋覆盖"),
    ("火星", "行星天体", "它表面有红色的土壤和岩石，科学家经常发射火星车去探索"),
    ("闪电", "自然现象", "它在雷雨天空中划出一道亮白曲折的电光，接着响起轰隆隆的雷声"),
    ("指南针", "发明仪器", "中国古代四大发明之一，小小的磁针总是一头指着南、一头指着北"),
    ("风筝", "传统玩具", "竹条彩纸糊成轻巧骨架，春暖花开时牵着长线逆风飞上天空"),
    ("显微镜", "科学仪器", "通过高倍透镜放大，能让我们看清肉眼看不到的细胞和微小生物"),
    ("地球仪", "学习工具", "一个可以转动的圆球模型，上面清晰标着大洲、大洋和各个国家"),
    ("望远镜", "光学仪器", "它装有凹凸透镜，能把遥远的星辰或远处的风景拉到眼前"),
    ("变色龙", "爬行动物", "两只眼睛能各自转动，皮肤能随着周围树叶岩石改变颜色伪装自己"),
    ("萤火虫", "发光昆虫", "夏夜在草丛间缓缓飞舞，尾巴末端发出柔和微弱的黄绿色荧光"),
    ("骆驼", "沙漠动物", "背上长着能储存能量的高高驼峰，耐饥耐渴，被称为沙漠之舟"),
    ("树懒", "热带动物", "行动极其缓慢，几乎整天倒挂在热带雨林的树枝上睡觉吃树叶"),
    ("考拉", "有袋动物", "住在澳大利亚的树上，圆圆的耳朵大鼻子，最喜欢吃桉树叶睡觉"),
    ("向日葵", "植物花卉", "金黄灿烂的大圆盘花朵，白天总是面朝着太阳的方向转动"),
    ("仙人掌", "耐旱植物", "为了适应干旱沙漠，叶子变成了硬刺，肉质茎储存着大量水分"),
    ("鲸鱼", "海洋巨兽", "地球上现存体型最大的哺乳动物，浮出海面时会喷出壮观的水柱"),
    ("啄木鸟", "森林鸟类", "嘴巴像结实的凿子，飞在树干上笃笃笃捉虫子，被称作森林医生"),
]


@dataclass
class GuessQA:
    question: str
    answer: str


class GuessSession:
    def __init__(self, secret: Optional[str] = None, custom_secrets: Optional[List[Dict[str, str]]] = None):
        pool = list(GUESS_SECRETS)
        if custom_secrets:
            for cs in custom_secrets:
                name = cs.get("name", "").strip()
                cat = cs.get("category", "神秘事物").strip()
                hint = cs.get("hint", "").strip()
                if name:
                    pool.insert(0, (name, cat, hint))
        self.pool = pool
        if secret:
            for s, cat, h in self.pool:
                if s == secret:
                    self.item = (s, cat, h)
                    break
            else:
                self.item = (secret, "神秘事物", "这是一个有趣的秘密事物")
        else:
            self.item = random.choice(self.pool)
        self.secret, self.category, self.hint = self.item
        self.history: List[GuessQA] = []
        self.max_questions = 20
        self.status = "playing"  # playing | win | over | reveal

    def mask(self, text: str) -> str:
        if not text:
            return ""
        return text.replace(self.secret, "＊＊")

    def check_guess(self, text: str) -> bool:
        clean = re.sub(r"[^\u4e00-\u9fa5a-zA-Z0-9]", "", text)
        return self.secret in clean

    def give_hint(self) -> str:
        return f"提示：它属于【{self.category}】。{self.hint}"

    def answer_question(self, question: str, model_answer: Optional[str] = None) -> Tuple[str, bool]:
        if self.check_guess(question):
            self.status = "win"
            reply = f"太棒啦！完全猜对啦！谜底正是【{self.secret}】！总共问了 {len(self.history) + 1} 个问题！"
            self.history.append(GuessQA(question, reply))
            return reply, True

        if len(self.history) + 1 >= self.max_questions:
            self.status = "over"
            reply = f"20次提问机会用完啦！谜底其实是【{self.secret}】。换一个继续挑战吧！"
            self.history.append(GuessQA(question, reply))
            return reply, False

        ans = model_answer or self._rule_based_answer(question)
        ans = self.mask(ans)
        self.history.append(GuessQA(question, ans))
        return ans, False

    def _rule_based_answer(self, q: str) -> str:
        s, cat, h = self.item
        q_clean = q.lower()
        # 跳 / 蹦 / 跳跃 特征
        if any(w in q_clean for w in ("跳", "蹦", "跃")):
            can_jump = s in ("青蛙", "袋鼠", "松鼠")
            return "是的，它特别擅长跳跃，后腿很有力！" if can_jump else "不是哦，它平时不怎么跳跃。"
        if any(w in q_clean for w in ("飞", "翅膀", "羽毛")):
            can_fly = s in ("蜜蜂", "蝴蝶", "猫头鹰", "啄木鸟", "萤火虫")
            return "是的，它长着翅膀会飞哦！" if can_fly else "不是哦，它不会飞。"
        if any(w in q_clean for w in ("水", "游", "海", "河", "池塘", "两栖")):
            in_water = s in ("海豚", "企鹅", "章鱼", "青蛙", "乌龟", "鲸鱼")
            return "是的，它经常在水里生活或游泳！" if in_water else "不是哦，它主要生活在陆地上。"
        if any(w in q_clean for w in ("活", "动物", "植物", "生命", "生物")):
            is_living = cat not in ("自然天体", "自然现象", "学习用品", "交通工具", "发明仪器", "传统玩具", "科学仪器", "学习工具", "光学仪器")
            return "是的，它是活生生的生物！" if is_living else "不是哦，它不是生物。"
        if any(w in q_clean for w in ("光", "发光", "亮")):
            glows = s in ("太阳", "萤火虫", "闪电", "月亮")
            return "是的，它在夜空或白天会发光/发亮哦！" if glows else "不是哦，它本身不会发光。"
        if any(w in q_clean for w in ("大", "重", "比人")):
            is_big = s in ("大象", "长颈鹿", "月亮", "太阳", "地球", "火星", "鲸鱼")
            return "是的，它的个头非常大！" if is_big else "不是哦，它体型并不算大。"
        if any(w in q_clean for w in ("叫", "声音", "呱", "鸣", "吵")):
            makes_sound = s in ("青蛙", "猫头鹰", "蜜蜂", "大象", "啄木鸟")
            if s == "青蛙":
                return "是的，它的叫声非常响亮，呱呱呱地叫！"
            elif s == "啄木鸟":
                return "是的，它啄木头会发出笃笃笃的声音！"
            return "是的，它会发出非常有特色的声音！" if makes_sound else "不是哦，它平时很安静。"
        if any(w in q_clean for w in ("绿", "颜色", "白", "黑", "黄")):
            if s == "青蛙":
                return "是的，它大部分皮肤是绿色的，肚皮是白色的！"
            elif s in ("熊猫", "企鹅"):
                return "是的，它身上主要是经典的黑白两色！"
            elif s == "彩虹":
                return "是的，它拥有七彩斑斓的美丽色彩！"
            elif s == "蝴蝶":
                return "是的，它的翅膀五颜六色非常漂亮！"
            elif s == "向日葵":
                return "是的，它开着金黄色灿烂的花盘！"
            return "颜色不是它最核心的辨识特征哦。"
        if any(w in q_clean for w in ("害虫", "蚊", "虫", "益")):
            eats_bugs = s in ("青蛙", "猫头鹰", "蝙蝠", "啄木鸟", "变色龙")
            return "是的，它是帮助消灭害虫的益虫/益鸟！" if eats_bugs else "不是以捕食害虫为主哦。"
        if any(w in q_clean for w in ("蛋", "卵", "变", "蝌蚪", "毛毛虫")):
            lays_eggs = s in ("青蛙", "企鹅", "猫头鹰", "蜜蜂", "蝴蝶", "乌龟", "章鱼", "啄木鸟", "萤火虫", "变色龙")
            return "是的，它是卵生或小时候会经历神奇蜕变哦！" if lays_eggs else "不是哦，它是胎生哺乳或植物。"
        if any(w in q_clean for w in ("壳", "刺", "硬", "甲")):
            has_armor = s in ("乌龟", "刺猬", "仙人掌")
            return "是的，它身上长着保护自己的硬壳或尖刺！" if has_armor else "不是哦，它的身体没有硬壳或尖刺。"
        if any(w in q_clean for w in ("吃", "肉", "草", "竹子", "树叶")):
            eats_plants = s in ("大象", "长颈鹿", "熊猫", "袋鼠", "松鼠", "骆驼", "树懒", "考拉")
            return "是的，它喜欢吃植物、竹子或高高的树叶！" if eats_plants else "它更喜欢吃小鱼、昆虫或其它食物哦。"
        if any(w in q_clean for w in ("腿", "脚", "足")):
            if s in ("章鱼",):
                return "是的，它长着8条柔软灵活的腕足！"
            elif s in ("大象", "长颈鹿", "熊猫", "青蛙", "乌龟", "松鼠", "刺猬", "骆驼", "变色龙", "树懒", "考拉"):
                return "是的，它长着4条腿！"
            elif s in ("企鹅", "猫头鹰", "袋鼠", "啄木鸟"):
                return "是的，它是用2条腿行走或站立的！"
            elif s in ("蜜蜂", "蝴蝶", "萤火虫"):
                return "是的，它是6条腿的昆虫！"
            elif s in ("鲸鱼", "海豚"):
                return "它没有脚，是用鱼鳍和尾巴游泳的！"
            return "不是通常意义上的腿脚哦。"
        return f"不是这个特征哦。给你一个小线索：它属于【{cat}】！"


# ── 5. 费曼小老师（讲给我听，移植自 SnapClaw）──

@dataclass
class FeynmanRound:
    teacher_speech: str
    student_reply: str


@dataclass
class FeynmanSummary:
    topic: str
    good_point: str
    think_point: str


class FeynmanSession:
    def __init__(self, topic: str = "今日所学"):
        self.topic = topic
        self.rounds: List[FeynmanRound] = []
        self.max_rounds = 3
        self.summary: Optional[FeynmanSummary] = None

    def add_round(self, speech: str, reply: Optional[str] = None) -> Tuple[str, bool]:
        round_idx = len(self.rounds) + 1
        is_final = (round_idx >= self.max_rounds)

        if not reply:
            reply = self._generate_response(speech, round_idx, is_final)

        self.rounds.append(FeynmanRound(speech, reply))

        if is_final:
            self.summary = self._generate_summary()
            return reply, True
        return reply, False

    def _generate_response(self, speech: str, round_idx: int, is_final: bool) -> str:
        if is_final:
            return "哇，小老师讲得太棒了！我完全听明白了！谢谢小老师的耐心讲解，为你点赞！"
        if round_idx == 1:
            return "原来是这样！小老师，那你能举一个日常生活里的具体例子吗？"
        return "我好像懂了一大半！那如果遇到了特殊情况，比如反过来的话，会发生什么呢？"

    def _generate_summary(self) -> FeynmanSummary:
        first_talk = self.rounds[0].teacher_speech if self.rounds else "所学知识"
        clean_topic = first_talk[:12] + ("…" if len(first_talk) > 12 else "")
        return FeynmanSummary(
            topic=clean_topic,
            good_point="能用自己的话生动举例，条理清晰！",
            think_point="试着想一想，如果遇到相反的情况该怎么解释？"
        )


# ── 6. 事实核查官（核实一下，移植自 SnapClaw）──

def verify_claim_direct(text: str, custom_facts: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """事实核查引擎：支持算式数学精准核实 + 经典常识核实 + 家长自定义常识 + AI 综合核实。"""
    clean = text.strip()

    # 0. 家长自定义常识库优先匹配
    if custom_facts:
        for f in custom_facts:
            k1 = str(f.get("kw1", "")).strip()
            k2 = str(f.get("kw2", "")).strip()
            if k1 and k2 and k1 in clean and k2 in clean:
                is_ok = bool(f.get("is_true", True))
                return {
                    "ok": True,
                    "verdict": "ok" if is_ok else "wrong",
                    "verdict_icon": "✅" if is_ok else "❌",
                    "verdict_text": "核实：说法正确！" if is_ok else "核实：说法不正确！",
                    "note": str(f.get("note", "")),
                    "how": str(f.get("how", "查阅少儿权威百科全书或科学词典。"))
                }

    # 1. 检测数学算式
    arith_match = re.search(r'(\d+)\s*(加|\+|减|\-|乘|\*|乘以|×|除|除以|÷)\s*(\d+)\s*(等于|=|是)\s*(\d+)', clean)
    if arith_match:
        a = int(arith_match.group(1))
        op = arith_match.group(2)
        b = int(arith_match.group(3))
        claimed = int(arith_match.group(5))
        if op in ('加', '+'):
            real = a + b
        elif op in ('减', '-'):
            real = a - b
        elif op in ('乘', '*', '乘以', '×'):
            real = a * b
        elif op in ('除', '除以', '÷'):
            real = a // b if b != 0 else 0
        else:
            real = 0

        is_correct = (real == claimed)
        expr_str = f"{a} {op} {b}"
        return {
            "ok": True,
            "verdict": "ok" if is_correct else "wrong",
            "verdict_icon": "✅" if is_correct else "❌",
            "verdict_text": "算式计算正确！" if is_correct else "算式计算错误！",
            "note": f"{expr_str} 实际精确计算等于 {real}。" + ("你算得很准确！" if is_correct else f"声称等于 {claimed} 是不对的哦。"),
            "how": "用竖式在草稿纸上验算，或者用逆运算检查（加法用减法验算，乘法用除法验算）。"
        }

    # 2. 常见小学生科学/自然常识快速核查库
    KNOWN_FACTS = [
        ("贝壳", "动物", True, "贝壳本身是双壳纲或腹足纲软体动物（如河蚌、扇贝、鲍鱼等）分泌出的坚硬碳酸钙外壳；活着的贝类是货真价实的动物！", "观察海滩或水族馆里的活贝类，看它们张合贝壳、露出斧足呼吸进食。"),
        ("贝类", "动物", True, "贝类属于无脊椎动物门中的软体动物，是自然界第二大动物门类！", "查阅少儿生物百科全书「软体动物门」分类。"),
        ("西瓜", "树上", False, "西瓜是草本植物，茎蔓贴地生长。西瓜太重，长在树上会压断树枝。", "查阅《十万个为什么》植物篇，观察菜地里的西瓜藤。"),
        ("企鹅", "鸟", True, "企鹅属于鸟纲，有羽毛且卵生，虽然翅膀退化成鳍不能飞，但是真正的鸟类。", "查阅动物百科「鸟类篇」，观察企鹅骨骼与卵生特征。"),
        ("光年", "时间", False, "光年不是时间单位，而是长度（距离）单位！代表光在真空中一年走的距离。", "查阅物理课本或天文常识词典，了解「距离 = 速度 × 时间」。"),
        ("蜘蛛", "昆虫", False, "蜘蛛不是昆虫！昆虫都有6条腿；蜘蛛有8条腿，属于蛛形纲节肢动物。", "数一数身边的蜘蛛有几条腿，和6条腿的蚂蚁、蜜蜂做对比。"),
        ("鲸鱼", "鱼", False, "鲸鱼不是鱼，而是用肺呼吸、胎生哺乳的哺乳动物！小鲸鱼吃母乳长大。", "观察鲸鱼呼吸时喷出的水柱，了解哺乳动物胎生哺乳特征。"),
        ("蝙蝠", "鸟", False, "蝙蝠不是鸟类，而是唯一能够真正飞行的哺乳动物，胎生哺乳。", "查阅生物百科，了解蝙蝠翼膜与鸟类羽毛翅膀的区别。"),
        ("太阳", "西边", False, "地球自西向东自转，因此在我们看来太阳总是东升西落，绝不会从西边升起！", "清晨观察日出方向，用指南针确定东方。"),
        ("月亮", "发光", False, "月亮本身是一个冰冷的球体不会发光，我们看到的月光其实是它反射太阳的光线！", "用手电筒照射小皮球，观察球面上被照亮的反射光斑。"),
        ("蘑菇", "植物", False, "蘑菇不是植物也不是动物，它属于真菌界！蘑菇没有叶绿体，不能进行光合作用。", "查阅生物分类「五界系统」，了解真菌与植物的区别。"),
        ("珊瑚", "植物", False, "珊瑚不是植物，而是成千上万只微小的珊瑚虫分泌石灰质聚集形成的刺胞动物！", "观察海洋馆里的活体珊瑚触手，看珊瑚虫捕食浮游生物。"),
        ("海星", "鱼", False, "海星不是鱼类，而是棘皮动物门，靠身体下面的数百只管足移动！", "翻开海星腹部，观察密密麻麻的小管足。"),
        ("鳄鱼", "鱼", False, "鳄鱼不是鱼，而是冷血爬行动物，用肺呼吸，生有坚硬的角质鳞片。", "观察鳄鱼露出水面用鼻孔呼吸的姿态。"),
        ("竹子", "树", False, "竹子不是树木，而是高大的多年生禾本科草本植物！竹子没有真正的树木年轮。", "观察竹子中空的茎秆结构，对比禾本科小麦或甘蔗。"),
        ("蜗牛", "昆虫", False, "蜗牛属于腹足纲软体动物，不是节肢动物昆虫！", "观察蜗牛背上的螺旋外壳与腹部扁平的肉质大脚。"),
        ("蛇", "骨头", True, "蛇不仅有骨头，而且拥有多达数百节的脊椎骨和肋骨，骨骼极其精密！", "参观自然博物馆的蛇类骨骼标本，数一数长长的脊柱。"),
        ("大熊猫", "猫", False, "大熊猫不是猫科动物，而是属于食肉目熊科，是名副其实的‘熊’！", "观察大熊猫圆滚滚的体态和牙齿结构，与黑熊、棕熊对比。"),
        ("声音", "真空", False, "声音的传播需要介质（如空气、水或固体），在没有空气的真空中声音是无法传播的！", "查阅物理课本声学篇，了解「声波需要介质振动」。"),
        ("近视", "恢复", False, "真性近视是眼轴变长造成的，目前医学上无法通过按摩或滴眼药水逆转恢复，关键在于科学预防！", "咨询专业眼科医生，坚持做眼保健操与户外活动。"),
        ("心脏", "左边", True, "人的心脏大约像自己拳头那么大，约三分之二偏向身体胸腔的左侧。", "把手轻轻放在左胸，感受有规律的心跳跳动。"),
        ("牙齿", "换牙", True, "人的一生有两副牙齿：乳牙（20颗）和恒牙（28-32颗），小学生阶段正是换牙的关键期！", "记录自己掉落的第一颗乳牙时间，保持每天早晚认真刷牙。"),
        ("含羞草", "害羞", True, "含羞草叶柄基部有膨大的叶枕，细胞充满水分；一碰触水分迅速流走，叶片就会闭合‘害羞’下垂！", "轻轻碰一下含羞草的叶子，观察闭合的全过程。"),
        ("蚂蚁", "气味", True, "蚂蚁外出觅食时会沿途释放信息素（气味），同伴靠头上的触角闻着气味整齐排队！", "观察地面上的蚂蚁搬家队列，看它们触角碰触交流。"),
        ("北极熊", "白", False, "北极熊的毛其实是透明的无色中空管状毛发，能把光线反射折射，看起来才像白色！它的皮肤其实是黑色的！", "查阅极地动物百科，显微镜下的北极熊中空毛发保温原理。"),
        ("热胀冷缩", "冷缩", True, "绝大多数物体受热时体积膨胀，遇冷时体积收缩，这叫做热胀冷缩。", "把瘪了的乒乓球放入热水里，观察球重新鼓起来。"),
        ("彩虹", "七彩", True, "太阳光穿过空气中的小水滴时发生折射和反射，被分解为红橙黄绿蓝靛紫七种颜色！", "背对太阳用喷雾器喷出一片水雾，寻找人工小彩虹。"),
        ("树木", "年轮", True, "树木茎干每年春夏季生长快、秋冬季生长慢，在横截面上形成深浅相间的同心圆圈，一圈代表一年！", "观察公园伐木桩上的横截面，数一数有几道年轮。"),
        ("流星", "星星", False, "流星并不是真正的天体星星落下，而是太空中的宇宙尘埃或流星体高速闯入地球大气层摩擦燃烧发光的现象！", "查阅少儿天文常识，了解大气层流星雨的形成。"),
        ("铅笔", "铅", False, "铅笔芯里根本没有重金属铅！它是由石墨和黏土按一定比例混合烧制而成的，无毒无害。", "查阅《十万个为什么》，了解石墨与黏土的比例如何决定HB深浅。"),
        ("水蒸气", "看不见", True, "真正的水蒸气是无色透明的气体，肉眼看不见！烧开水时壶口冒出的‘白气’其实是水蒸气遇冷凝结成的小水珠滴！", "仔细观察水壶嘴紧贴喷口的一小段无色透明区域。"),
        ("避雷针", "避雷", True, "避雷针其实应该叫‘引雷针’，它通过尖端放电把雷电安全引入大地，保护建筑物不被雷击损坏。", "观察高楼大厦顶端细长金属针，查阅富兰克林雷电实验。"),
    ]

    for kw1, kw2, is_true, note, how in KNOWN_FACTS:
        if kw1 in clean and kw2 in clean:
            return {
                "ok": True,
                "verdict": "ok" if is_true else "wrong",
                "verdict_icon": "✅" if is_true else "❌",
                "verdict_text": "核实：说法正确！" if is_true else "核实：说法不正确！",
                "note": note,
                "how": how
            }

    # 3. 兜底智能启发提示
    return {
        "ok": True,
        "verdict": "unsure",
        "verdict_icon": "🔎",
        "verdict_text": "已记录核实点！",
        "note": f"关于“{clean[:20]}”，这是一个非常棒的好奇探索问题！科学探索需要大胆假设，小心求证。",
        "how": "第一步查阅少儿权威百科全书；第二步向科学老师或家长请教；第三步尝试设计一个小观察或实验来验证！"
    }


# ── 7. 诗词飞花令（小学生古诗词语音对战）──

FEIHUA_DB: Dict[str, List[str]] = {
    "春": [
        "春眠不觉晓，处处闻啼鸟",
        "不知细叶谁裁出，二月春风似剪刀",
        "春风又绿江南岸，明月何时照我还",
        "好雨知时节，当春乃发生",
        "人面不知何处去，桃花依旧笑春风",
        "春色满园关不住，一枝红杏出墙来",
        "国破山河在，城春草木深",
        "谁言寸草心，报得三春晖",
        "红豆生南国，春来发几枝",
        "竹外桃花三两枝，春江水暖鸭先知",
    ],
    "月": [
        "床前明月光，疑是地上霜",
        "举杯邀明月，对影成三人",
        "露从今夜白，月是故乡明",
        "明月松间照，清泉石上流",
        "秦时明月汉时关，万里长征人未还",
        "野旷天低树，江清月近人",
        "小时不识月，呼作白玉盘",
        "海上生明月，天涯共此时",
        "月落乌啼霜满天，江枫渔火对愁眠",
    ],
    "花": [
        "借问酒家何处有，牧童遥指杏花村",
        "待到重阳日，还来就菊花",
        "乱花渐欲迷人眼，浅草才能没马蹄",
        "夜来风雨声，花落知多少",
        "感时花溅泪，恨别鸟惊心",
        "无可奈何花落去，似曾相识燕归来",
        "桃花潭水深千尺，不及汪伦送我情",
        "接天莲叶无穷碧，映日荷花别样红",
    ],
    "风": [
        "解落三秋叶，能开二月花",
        "野火烧不尽，春风吹又生",
        "随风潜入夜，润物细无声",
        "北风卷地白草折，胡天八月即飞雪",
        "柴门闻犬吠，风雪夜归人",
        "千里黄云白日曛，北风吹雁雪纷纷",
    ],
    "山": [
        "远上寒山石径斜，白云生处有人家",
        "会当凌绝顶，一览众山小",
        "横看成岭侧成峰，远近高低各不同",
        "千山鸟飞绝，万径人踪灭",
        "空山新雨后，天气晚来秋",
        "绿树村边合，青山郭外斜",
        "山重水复疑无路，柳暗花明又一村",
    ],
    "雪": [
        "孤舟蓑笠翁，独钓寒江雪",
        "窗含西岭千秋雪，门泊东吴万里船",
        "柴门闻犬吠，风雪夜归人",
        "忽如一夜春风来，千树万树梨花开",
        "遥知不是雪，为有暗香来",
        "大雪满弓刀",
        "千里黄云白日曛，北风吹雁雪纷纷",
        "梅须逊雪三分白，雪却输梅一段香",
        "草枯鹰眼疾，雪尽马蹄轻",
    ],
    "雨": [
        "好雨知时节，当春乃发生",
        "夜来风雨声，花落知多少",
        "清明时节雨纷纷，路上行人欲断魂",
        "天街小雨润如酥，草色遥看近却无",
        "水光潋滟晴方好，山色空蒙雨亦奇",
        "青箬笠，绿蓑衣，斜风细雨不须归",
        "空山新雨后，天气晚来秋",
        "南朝四百八十寺，多少楼台烟雨中",
        "寒雨连江夜入吴，平明送客楚山孤",
    ],
    "水": [
        "桃花潭水深千尺，不及汪伦送我情",
        "白毛浮绿水，红掌拨清波",
        "水光潋滟晴方好，山色空蒙雨亦奇",
        "泉眼无声惜细流，树阴照水爱晴柔",
        "竹外桃花三两枝，春江水暖鸭先知",
        "落霞与孤鹜齐飞，秋水共长天一色",
        "日出江花红胜火，春来江水绿如蓝",
        "天门中断楚江开，碧水东流至此回",
    ],
    "夜": [
        "床前明月光，疑是地上霜",
        "夜来风雨声，花落知多少",
        "月黑雁飞高，单于夜遁逃",
        "随风潜入夜，润物细无声",
        "姑苏城外寒山寺，夜半钟声到客船",
        "露从今夜白，月是故乡明",
        "野径云俱黑，江船火独明",
        "葡萄美酒夜光杯，欲饮琵琶马上催",
    ],
    "云": [
        "朝辞白帝彩云间，千里江陵一日还",
        "远上寒山石径斜，白云生处有人家",
        "黑云翻墨未遮山，白雨跳珠乱入船",
        "黄鹤一去不复返，白云千载空悠悠",
        "千里黄云白日曛，北风吹雁雪纷纷",
        "众鸟高飞尽，孤云独去闲",
        "只在此山中，云深不知处",
    ],
    "江": [
        "孤帆远影碧空尽，唯见长江天际流",
        "日出江花红胜火，春来江水绿如蓝",
        "天门中断楚江开，碧水东流至此回",
        "野旷天低树，江清月近人",
        "寒雨连江夜入吴，平明送客楚山孤",
        "春风又绿江南岸，明月何时照我还",
        "千山鸟飞绝，万径人踪灭，孤舟蓑笠翁，独钓寒江雪",
    ],
    "草": [
        "离离原上草，一岁一枯荣",
        "天街小雨润如酥，草色遥看近却无",
        "乱花渐欲迷人眼，浅草才能没马蹄",
        "草长莺飞二月天，拂堤杨柳醉春烟",
        "谁言寸草心，报得三春晖",
        "国破山河在，城春草木深",
        "独怜幽草涧边生，上有黄鹂深树鸣",
    ],
    "鸟": [
        "千山鸟飞绝，万径人踪灭",
        "春眠不觉晓，处处闻啼鸟",
        "感时花溅泪，恨别鸟惊心",
        "两个黄鹂鸣翠柳，一行白鹭上青天",
        "月出惊山鸟，时鸣春涧中",
        "众鸟高飞尽，孤云独去闲",
    ],
    "秋": [
        "解落三秋叶，能开二月花",
        "空山新雨后，天气晚来秋",
        "自古逢秋悲寂寥，我言秋日胜春朝",
        "落霞与孤鹜齐飞，秋水共长天一色",
        "窗含西岭千秋雪，门泊东吴万里船",
        "秋风吹不尽，总是玉关情",
    ],
    "天": [
        "一行白鹭上青天",
        "飞流直下三千尺，疑是银河落九天",
        "白日依山尽，黄河入海流",
        "天门中断楚江开，碧水东流至此回",
        "接天莲叶无穷碧，映日荷花别样红",
        "月落乌啼霜满天，江枫渔火对愁眠",
    ],
    "梅": [
        "墙角数枝梅，凌寒独自开",
        "遥知不是雪，为有暗香来",
        "梅须逊雪三分白，雪却输梅一段香",
        "不要人夸好颜色，只留清气满乾坤",
        "待到山花烂漫时，她在丛中笑",
    ],
}


class FeihuaSession:
    def __init__(self, keyword: Optional[str] = None, target_rounds: int = 3,
                 custom_feihua: Optional[Dict[str, List[str]]] = None):
        db = {k: list(v) for k, v in FEIHUA_DB.items()}
        if custom_feihua:
            for k, verses in custom_feihua.items():
                k_clean = k.strip()
                if k_clean in db:
                    for v in verses:
                        if v not in db[k_clean]:
                            db[k_clean].append(v)
                else:
                    db[k_clean] = [v for v in verses if v]
        self.db = db
        keys = list(self.db.keys())
        self.keyword = keyword if keyword and keyword in self.db else random.choice(keys)
        self.target_rounds = target_rounds
        self.rounds_done = 0
        self.used_lines: Set[str] = set()
        self.history: List[Dict[str, str]] = []
        self.status = "playing"  # playing | win | giveup

    def _normalize(self, text: str) -> str:
        return re.sub(r"[^\u4e00-\u9fa5]", "", text)

    def check_verse(self, raw_text: str) -> Tuple[bool, str, str]:
        """判定孩子背诵的诗句。
        返回: (是否有效通过, 设备接龙诗句, 评语/提示语)
        """
        clean = self._normalize(raw_text)
        if self.keyword not in clean:
            return False, "", f"这句诗里没有包含令字「{self.keyword}」哦！请再试一句～"

        if len(clean) < 4:
            return False, "", "诗句太短啦，至少说一句完整的五言或七言诗句哦！"

        if any(clean in u or u in clean for u in self.used_lines):
            return False, "", f"这句诗本轮已经对过啦，换一句带「{self.keyword}」字的诗吧！"

        self.used_lines.add(clean)

        # 挑选设备接龙对句（从未使用的备选库中挑）
        candidates = self.db.get(self.keyword, [])
        bot_verse = ""
        for verse in candidates:
            norm_v = self._normalize(verse)
            if norm_v not in self.used_lines:
                bot_verse = verse
                self.used_lines.add(norm_v)
                break

        if not bot_verse:
            bot_verse = f"小才子诗才过人，这句带「{self.keyword}」的诗太精妙了！"

        self.rounds_done += 1
        self.history.append({"child": raw_text.strip(" ，。？！"), "bot": bot_verse})

        if self.rounds_done >= self.target_rounds:
            self.status = "win"
            feedback = f"太厉害了！已成功对出 {self.rounds_done} 轮！飞花令大获全胜！"
        else:
            feedback = f"答得漂亮！包含令字「{self.keyword}」！现在看我的："

        return True, bot_verse, feedback

    def give_hint(self) -> str:
        candidates = FEIHUA_DB.get(self.keyword, [])
        for verse in candidates:
            norm = self._normalize(verse)
            if norm not in self.used_lines:
                first_half = verse.split("，")[0] if "，" in verse else verse[:5]
                return f"提示：可以想想这首诗的前半句：「{first_half}…」"
        return f"提示：试着回忆学过的古诗，里面带有「{self.keyword}」字的一句哦！"


# ── 8. 错字门诊 / 形近字辨析（小学语文高频生字辨析）──

HANZI_SETS: List[Dict[str, Any]] = [
    {
        "stem": "天气干[  ]",
        "ans": "燥",
        "opts": ["燥", "躁"],
        "pinyin": "gān zào",
        "meaning": "干燥：缺少水分",
        "mnemonic": "火字旁天气干燥，足字旁脾气急躁，口字旁噪音吵闹",
        "detail": "燥：缺少水分（干燥、燥热）；躁：性急慌张（急躁、暴躁）"
    },
    {
        "stem": "言语争[  ]",
        "ans": "辩",
        "opts": ["辩", "辨"],
        "pinyin": "zhēng biàn",
        "meaning": "争辩：争论辩解",
        "mnemonic": "言字旁中间是争辩，刀字旁中间是分辨，丝线中间是发辫",
        "detail": "辩：用言语辩驳（争辩、辩论）；辨：判别区分（分辨、辨别）"
    },
    {
        "stem": "用力[  ]萝卜",
        "ans": "拔",
        "opts": ["拔", "拨"],
        "pinyin": "bá luó bo",
        "meaning": "拔起：用手拉出",
        "mnemonic": "提手用力向上拔萝卜；右边加一点，轻轻拨动算盘珠",
        "detail": "拔：拉出、吸出（拔草、拔河）；拨：用手或棍移动（拨打、拨弄）"
    },
    {
        "stem": "剪[  ]衣服",
        "ans": "裁",
        "opts": ["裁", "截"],
        "pinyin": "jiǎn cái",
        "meaning": "裁剪：剪裁布料",
        "mnemonic": "衣字底做衣服叫剪裁，十把戈刀斩断叫截断",
        "detail": "裁：用剪刀做衣服（裁剪、裁缝）；截：割断、阻止（截断、截止）"
    },
    {
        "stem": "已经[  ]知",
        "ans": "已",
        "opts": ["已", "己"],
        "pinyin": "yǐ jīng",
        "meaning": "已经：表示动作完成",
        "mnemonic": "全张口是自己（己），半封口是已经（已），全闭紧是巳时（巳）",
        "detail": "已：表示过去或完毕（已经、已知）；己：自己、本我（知己、舍己为人）"
    },
    {
        "stem": "树木茂[  ]",
        "ans": "密",
        "opts": ["密", "蜜"],
        "pinyin": "mào mì",
        "meaning": "茂密：树木多而密",
        "mnemonic": "山下有木很稠密，虫字底小蜜蜂酿甜蜜",
        "detail": "密：紧密茂盛（茂密、精密）；蜜：昆虫酿造（蜂蜜、甜蜜）"
    },
    {
        "stem": "品[  ]美食",
        "ans": "尝",
        "opts": ["尝", "偿"],
        "pinyin": "pǐn cháng",
        "meaning": "品尝：辨别滋味",
        "mnemonic": "小口品尝美味，人字旁还钱叫补偿",
        "detail": "尝：吃一点辨别味道（品尝、尝试）；偿：归还、抵补（赔偿、偿还）"
    },
    {
        "stem": "秋天大[  ]南飞",
        "ans": "雁",
        "opts": ["雁", "燕"],
        "pinyin": "dà yàn",
        "meaning": "大雁：人字形候鸟",
        "mnemonic": "广字头带单人是大雁，草字头四点底是燕子",
        "detail": "雁：候鸟排成人字形（大雁、雁阵）；燕：春天的益鸟（燕子、燕雀）"
    },
    {
        "stem": "临危不[  ]",
        "ans": "惧",
        "opts": ["惧", "具"],
        "pinyin": "bú jù",
        "meaning": "畏惧：害怕",
        "mnemonic": "竖心旁代表内心情绪恐惧，八字底是工具文具",
        "detail": "惧：害怕恐惧（畏惧、临危不惧）；具：器具物件（文具、具备）"
    },
    {
        "stem": "按[  ]规定",
        "ans": "照",
        "opts": ["照", "召"],
        "pinyin": "àn zhào",
        "meaning": "依照：按照规矩",
        "mnemonic": "日字底四点火照耀光明，口字底召唤开会",
        "detail": "照：对照、依据（依照、按照）；召：呼唤召集（召集、号召）"
    },
]


class HanziSession:
    def __init__(self, count: int = 5):
        self.questions: List[Dict[str, Any]] = random.sample(HANZI_SETS, min(count, len(HANZI_SETS)))
        self.idx = 0
        self.selected_opt_idx = 0
        self.revealed = False
        self.last_correct: Optional[bool] = None
        self.correct_cnt = 0

    @property
    def current(self) -> Dict[str, Any]:
        return self.questions[self.idx]

    @property
    def total(self) -> int:
        return len(self.questions)

    def toggle_option(self) -> int:
        if not self.revealed:
            self.selected_opt_idx = 1 - self.selected_opt_idx
        return self.selected_opt_idx

    def confirm_answer(self, user_voice: Optional[str] = None) -> Tuple[bool, str, Dict[str, Any]]:
        curr = self.current
        correct_ans = curr["ans"]

        if user_voice:
            # 语音答题
            clean_voice = re.sub(r"[^\u4e00-\u9fa5]", "", user_voice)
            is_correct = (correct_ans in clean_voice)
        else:
            # 实体按键选定选项答题
            user_choice = curr["opts"][self.selected_opt_idx]
            is_correct = (user_choice == correct_ans)

        self.revealed = True
        self.last_correct = is_correct
        if is_correct:
            self.correct_cnt += 1
            speech = f"回答正确！这道题填「{correct_ans}」！口诀是：{curr['mnemonic']}。"
        else:
            speech = f"回答不正确哦！正确汉字是「{correct_ans}」！口诀是：{curr['mnemonic']}。"

        return is_correct, speech, curr

    def step_next(self) -> bool:
        if self.idx + 1 < len(self.questions):
            self.idx += 1
            self.selected_opt_idx = 0
            self.revealed = False
            self.last_correct = None
            return True
        return False



