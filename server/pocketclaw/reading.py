"""小学生英语分级短文与文章阅读（English Graded Reading）。

功能特色：
  1. 贴合小学英语课程标准（人教 PEP / 外研版 / 新概念英语 1 难度）。
  2. 支持【按键选句】：上下键移动光标选择句子，光标高亮显示当前句与中文释义。
  3. 支持【选句朗读】：短按确认键调用 TTS 纯正英音朗读当前句。
  4. 支持【跟读评测】：长按按键录音跟读当前句，通过智能口语评测打分（0-100分与星级反馈）。
"""
from __future__ import annotations

import copy
import json
import os
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("pocketclaw")


@dataclass
class ReadingArticle:
    id: str
    title: str
    level: str
    sentences: List[Tuple[str, str]]    # (英文句子, 中文释义)


# 精选小学各年级核心阅读篇目（生动有趣、词汇地道、句式规范）
PRESET_ARTICLES: List[ReadingArticle] = [
    ReadingArticle(
        id="art_sunny_day",
        title="A Sunny Day",
        level="三年级起步",
        sentences=[
            ("Today is a sunny Sunday.", "今天是一个晴朗的星期天。"),
            ("Tom and his dog go to the park.", "汤姆和他的小狗去公园。"),
            ("They see many green trees and colorful flowers.", "他们看到许多绿树和五颜六色的花。"),
            ("The birds are singing happily in the trees.", "鸟儿在树上快乐地歌唱。"),
            ("Tom throws a red ball on the grass.", "汤姆把一个红色的球扔在草地上。"),
            ("His dog runs fast to catch it.", "他的小狗跑得很快去接球。"),
            ("They play together and have a great time.", "他们一起玩耍，度过了美好的时光。"),
        ]
    ),
    ReadingArticle(
        id="art_little_red_hen",
        title="The Little Red Hen",
        level="三-四年级",
        sentences=[
            ("Once upon a time, a little red hen lived on a farm.", "从前，一只小红母鸡住在农场里。"),
            ("She found some grains of wheat.", "她发现了一些小麦种子。"),
            ("Who will help me plant the wheat? asked the hen.", "谁来帮我播种小麦？母鸡问。"),
            ("Not I, said the lazy duck.", "我才不呢，懒惰的鸭子说。"),
            ("Then I will do it myself, said the hen.", "那我就自己做，母鸡说。"),
            ("She worked hard and made delicious bread.", "她辛勤劳作，做出了美味的面包。"),
            ("No one can eat bread without working.", "不劳作者不得食。"),
        ]
    ),
    ReadingArticle(
        id="art_zoo_adventure",
        title="A Trip to the Zoo",
        level="三-四年级",
        sentences=[
            ("Welcome to the city zoo!", "欢迎来到城市动物园！"),
            ("Look at the giant panda over there.", "看那边的这只大熊猫。"),
            ("It is eating fresh green bamboo.", "它正在吃新鲜的绿色竹子。"),
            ("The baby monkeys are jumping up and down.", "小猴子们正在跳上跳下。"),
            ("The tall giraffe is eating leaves from tall trees.", "高高的长颈鹿正在吃高树上的树叶。"),
            ("Animals are our good friends.", "动物是我们最好的朋友。"),
            ("We should love and protect them.", "我们应该爱护和保护它们。"),
        ]
    ),
    ReadingArticle(
        id="art_clever_fox",
        title="The Fox and the Grapes",
        level="四-五年级",
        sentences=[
            ("One hot summer day, a hungry fox walked in the woods.", "一个炎热的夏天，一只饥饿的狐狸在树林里走着。"),
            ("He saw bunches of ripe purple grapes hanging high.", "他看见高处挂着一串串成熟的紫葡萄。"),
            ("The grapes look so juicy and sweet, he thought.", "这些葡萄看起来汁多味甜，他心里想。"),
            ("He jumped high into the air, but he missed them.", "他高高跳起，但没够着。"),
            ("He tried again and again, but failed every time.", "他试了一次又一次，但每次都失败了。"),
            ("I am sure the grapes are sour, said the fox.", "我敢肯定这些葡萄是酸的，狐狸说。"),
            ("It is easy to dislike what you cannot get.", "吃不到葡萄说葡萄酸。"),
        ]
    ),
    ReadingArticle(
        id="art_my_friend_robot",
        title="My Friend PocketClaw",
        level="三-五年级",
        sentences=[
            ("PocketClaw is my smart learning buddy on my desk.", "PocketClaw 是我书桌上的智能学习学伴。"),
            ("It has a friendly screen and three useful buttons.", "它有一块友好的屏幕和三个实用的按键。"),
            ("Every evening, it helps me review my English words.", "每天傍晚，它帮助我复习英语生词。"),
            ("When I read sentences aloud, it listens carefully.", "当我大声朗读句子时，它认真倾听。"),
            ("Practice makes perfect every single day.", "每天坚持练习，就能做到熟能生巧。"),
        ]
    ),
    # ── 扩展经典少儿双语分级阅读（源自 Project Gutenberg 公版伊索寓言典故）──
    ReadingArticle(
        id="art_tortoise_hare",
        title="The Tortoise and the Hare",
        level="三-四年级",
        sentences=[
            ("A proud hare was laughing at a slow tortoise.", "一只傲慢的野兔在嘲笑慢吞吞的乌龟。"),
            ("The tortoise challenged the hare to a running race.", "乌龟向野兔发起了跑步比赛挑战。"),
            ("The hare ran very fast and got far ahead.", "野兔跑得飞快，遥遥领先。"),
            ("He thought he had time to take a nap under a tree.", "他觉得自己有充裕的时间在树下睡一觉。"),
            ("The tortoise kept walking slowly without stopping.", "乌龟一步一步慢慢走着，毫不懈怠停歇。"),
            ("When the hare woke up, the tortoise had crossed the finish line.", "当野兔醒来时，乌龟已经冲过了终点线。"),
            ("Slow and steady wins the race.", "稳扎稳打，终成胜者。"),
        ]
    ),
    ReadingArticle(
        id="art_lion_mouse",
        title="The Lion and the Mouse",
        level="四-五年级",
        sentences=[
            ("A tiny mouse accidentally woke up a sleeping lion.", "一只微小的老鼠不小心吵醒了熟睡中的雄狮。"),
            ("The lion caught the mouse with his huge sharp paw.", "狮子用巨大锋利的爪子一把抓住了老鼠。"),
            ("Please spare me, cried the mouse. I may help you one day.", "请饶了我吧，老鼠求情道，也许哪天我能帮到您。"),
            ("The lion laughed loudly and kindly let him go.", "狮子哈哈大笑，好心放走了他。"),
            ("Days later, the lion was trapped in a hunter's strong net.", "几天后，狮子落入了猎人坚固的绳网陷阱中。"),
            ("The brave mouse chewed the thick ropes and set the lion free.", "勇敢的小老鼠咬断了粗绳，成功解救了狮子。"),
            ("Even the smallest friend can be of great help.", "再微小的朋友也能提供巨大的帮助。"),
        ]
    ),
    ReadingArticle(
        id="art_boy_cried_wolf",
        title="The Boy Who Cried Wolf",
        level="三-四年级",
        sentences=[
            ("A shepherd boy felt bored watching sheep on the hill.", "一个放羊娃在山坡上看羊，觉得非常无聊。"),
            ("He shouted loudly, Wolf! Wolf! The wolf is coming!", "他大声喊叫：狼来了！狼来了！狼真的来了！"),
            ("The kind villagers ran up the hill to help him.", "善良的村民们纷纷跑上山来帮助他。"),
            ("The boy laughed because there was no wolf at all.", "男孩哈哈大笑，因为根本就没有狼。"),
            ("One day, a real hungry wolf came out of the forest.", "有一天，一只真正的饿狼从森林里窜了出来。"),
            ("The boy cried for help, but nobody believed him.", "男孩哭着大声呼救，但再也没有人相信他了。"),
            ("Nobody believes a liar, even when he tells the truth.", "说谎者即使说了真话，也不会再有人相信。"),
        ]
    ),
    ReadingArticle(
        id="art_crow_pitcher",
        title="The Crow and the Pitcher",
        level="三-四年级",
        sentences=[
            ("A thirsty crow flew everywhere looking for water to drink.", "一只口渴的乌鸦四处飞翔寻找水喝。"),
            ("He found a tall pitcher with a little water at the bottom.", "他找到一个高高的水罐，罐底有少许水。"),
            ("His beak was too short to reach the cool water.", "他的嘴巴太短，够不着清凉的水。"),
            ("The clever crow thought of a brilliant idea.", "聪明的乌鸦想出了一个绝妙的办法。"),
            ("He picked up small pebbles one by one and dropped them in.", "他捡起一颗颗小石子，一颗接一颗扔进水罐里。"),
            ("The water rose to the top, and he drank his fill happily.", "水面渐渐升到了罐口，乌鸦痛快高兴地喝足了水。"),
            ("Where there is a will, there is a way.", "有志者，事竟成。"),
        ]
    ),
    ReadingArticle(
        id="art_ant_grasshopper",
        title="The Ant and the Grasshopper",
        level="四-五年级",
        sentences=[
            ("All summer long, a tiny ant worked hard storing grain.", "整个夏天，一只勤劳的小蚂蚁都在辛勤储存粮食。"),
            ("A lazy grasshopper sang all day and laughed at the ant.", "一只懒惰的蚱蜢整天唱歌，还嘲笑辛劳的蚂蚁。"),
            ("Winter came with heavy snow and freezing cold winds.", "寒冬降临，伴随着皑皑大雪与刺骨的寒风。"),
            ("The ant stayed warm and enjoyed plenty of sweet food.", "蚂蚁呆在温暖的窝里，享受着丰盛的食物。"),
            ("The freezing grasshopper had nothing to eat in the snow.", "挨冻的蚱蜢在大雪中找不到任何吃的。"),
            ("Work hard today so you can enjoy tomorrow.", "今日勤耕耘，明日得丰足。"),
        ]
    ),
]


# 常见语音称呼与别名映射（支持模糊指令与中英文别名）
ARTICLE_ALIASES: Dict[str, List[str]] = {
    "art_sunny_day": [
        "sunny day", "sunny", "a sunny day",
        "晴天", "晴朗", "星期天", "周日", "公园", "汤姆", "红球",
    ],
    "art_little_red_hen": [
        "little red hen", "red hen", "hen", "the little red hen",
        "小红母鸡", "红母鸡", "母鸡", "种麦子", "小麦", "懒鸭子", "做面包", "不劳不获",
    ],
    "art_zoo_adventure": [
        "trip to the zoo", "zoo", "a trip to the zoo",
        "动物园", "大熊猫", "熊猫", "猴子", "长颈鹿", "吃竹子",
    ],
    "art_clever_fox": [
        "fox and the grapes", "fox", "grapes", "the fox and the grapes",
        "狐狸和葡萄", "狐狸与葡萄", "酸葡萄", "狐狸", "葡萄",
    ],
    "art_my_friend_robot": [
        "my friend pocketclaw", "pocketclaw", "friend pocketclaw", "robot",
        "智能学伴", "学伴", "机器人", "我的朋友", "三键", "复习生词",
    ],
    "art_tortoise_hare": [
        "the tortoise and the hare", "tortoise and hare", "tortoise", "hare", "turtle",
        "龟兔赛跑", "乌龟赛跑", "兔子和乌龟", "赛跑", "乌龟", "兔子",
    ],
    "art_lion_mouse": [
        "the lion and the mouse", "lion and mouse", "lion", "mouse",
        "狮子和老鼠", "狮子与老鼠", "老鼠和狮子", "老鼠", "狮子", "猎人罗网",
    ],
    "art_boy_cried_wolf": [
        "the boy who cried wolf", "boy who cried wolf", "cried wolf", "wolf",
        "狼来了", "放羊的孩子", "放羊娃", "说谎的孩子", "牧童",
    ],
    "art_crow_pitcher": [
        "the crow and the pitcher", "crow and pitcher", "crow", "pitcher",
        "乌鸦喝水", "聪明的乌鸦", "乌鸦", "水罐", "扔石子",
    ],
    "art_ant_grasshopper": [
        "the ant and the grasshopper", "ant and grasshopper", "ant", "grasshopper",
        "蚂蚁和蚱蜢", "蚂蚁与蝉", "蚂蚁和蝈蝈", "勤劳的蚂蚁", "蚂蚁", "蚱蜢",
    ],
}

_CN_NUMS: Dict[str, int] = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


class ReadingSession:
    """文章阅读互动会话：管理当前文章、当前选句光标、朗读与评测状态。"""

    def __init__(self, article: ReadingArticle):
        self.article = article
        self.sel_idx: int = 0
        self.last_score: Optional[int] = None
        self.last_feedback: str = ""
        self.active: bool = True

    @property
    def total_sentences(self) -> int:
        return len(self.article.sentences)

    @property
    def cur_sentence(self) -> Tuple[str, str]:
        if 0 <= self.sel_idx < len(self.article.sentences):
            return self.article.sentences[self.sel_idx]
        return ("", "")

    def step_next(self) -> Tuple[str, str]:
        """按键【下】：选中下一句（末句循环回首句）。"""
        if self.total_sentences > 0:
            self.sel_idx = (self.sel_idx + 1) % self.total_sentences
        self.last_score = None
        self.last_feedback = ""
        return self.cur_sentence

    def step_prev(self) -> Tuple[str, str]:
        """按键【上】：选中上一句（首句循环至末句）。"""
        if self.total_sentences > 0:
            self.sel_idx = (self.sel_idx - 1) % self.total_sentences
        self.last_score = None
        self.last_feedback = ""
        return self.cur_sentence

    def record_score(self, score: int, feedback: str = "") -> None:
        self.last_score = score
        self.last_feedback = feedback


class ReadingStore:
    """英语文章库管理：内置篇目 + 支持本地持久化拓展。"""

    def __init__(self, path: Optional[str] = None):
        self.path = Path(path) if path else Path(os.environ.get("POCKETCLAW_DATA_DIR", str(Path(__file__).resolve().parent.parent / "data"))) / "english_readings.json"
        self.articles: List[ReadingArticle] = copy.deepcopy(PRESET_ARTICLES)
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                custom = []
                for item in data:
                    s_list = [(s.get("en", ""), s.get("cn", "")) for s in item.get("sentences", [])]
                    custom.append(ReadingArticle(
                        id=str(item.get("id", "")),
                        title=str(item.get("title", "")),
                        level=str(item.get("level", "小学英语")),
                        sentences=s_list,
                    ))
                if custom:
                    # 合并自定义文章与系统预设文章（保证预设篇目扩展后不被旧 json 屏蔽）
                    loaded_ids = {a.id for a in custom}
                    merged = list(custom)
                    for preset in PRESET_ARTICLES:
                        if preset.id not in loaded_ids:
                            merged.append(copy.deepcopy(preset))
                    self.articles = merged
        except Exception as e:
            log.warning("加载 english_readings.json 失败: %s", e)

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            data = [
                {
                    "id": a.id,
                    "title": a.title,
                    "level": a.level,
                    "sentences": [{"en": s[0], "cn": s[1]} for s in a.sentences]
                }
                for a in self.articles
            ]
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.warning("保存 english_readings.json 失败: %s", e)

    def add_article(self, article: ReadingArticle) -> None:
        for i, a in enumerate(self.articles):
            if a.id == article.id:
                self.articles[i] = article
                break
        else:
            self.articles.append(article)
        self._save()

    def update_article(self, article_id: str, *, title: Optional[str] = None,
                       level: Optional[str] = None,
                       sentences: Optional[List[Tuple[str, str]]] = None) -> Optional[ReadingArticle]:
        for a in self.articles:
            if a.id == article_id:
                if title is not None:
                    a.title = title.strip()
                if level is not None:
                    a.level = level.strip()
                if sentences is not None:
                    a.sentences = sentences
                self._save()
                return a
        return None

    def delete_article(self, article_id: str) -> bool:
        for i, a in enumerate(self.articles):
            if a.id == article_id:
                self.articles.pop(i)
                self._save()
                return True
        return False

    def get_article(self, article_id: str) -> Optional[ReadingArticle]:
        for art in self.articles:
            if art.id == article_id:
                return art
        return self.articles[0] if self.articles else None

    def get_article_by_idx(self, idx: int) -> Optional[ReadingArticle]:
        if not self.articles:
            return None
        return self.articles[idx % len(self.articles)]

    def find_article(self, query: str) -> Optional[Tuple[int, ReadingArticle]]:
        """按语音文本/关键词模糊匹配英语文章。
        支持：
          - 篇目序号：如“第一篇”、“第2篇”、“篇一”、“第二课”、“第3篇短文”、“1”
          - 篇名模糊/关键词：如“晴天”、“小红母鸡”、“动物园”、“狐狸和葡萄”、“PocketClaw”
          - 英文标题模糊匹配：如“sunny day”、“little red hen”、“zoo”、“fox”
        返回: (index, ReadingArticle) 或 None
        """
        raw = (query or "").strip()
        if not raw:
            return None

        # 1. 匹配篇目序号：第N篇 / 第N课 / 篇N / 单独数字
        m = re.search(r"第\s*([0-9]+|[一二两三四五六七八九十]+)\s*[篇课章个]?", raw)
        if not m:
            m = re.search(r"篇\s*([0-9]+|[一二两三四五六七八九十]+)", raw)
        if m:
            num_str = m.group(1)
            val = int(num_str) if num_str.isdigit() else _CN_NUMS.get(num_str, 0)
            if 1 <= val <= len(self.articles):
                return (val - 1, self.articles[val - 1])
        elif raw in ("1", "2", "3", "4", "5", "6", "7", "8", "9"):
            val = int(raw)
            if 1 <= val <= len(self.articles):
                return (val - 1, self.articles[val - 1])

        # 2. 清洗语音前缀与标点
        t = raw.lower()
        for ch in "《》“”\"'‘’，。！？,!?、":
            t = t.replace(ch, " ")
        prefixes = [
            "我想读", "我要读", "读一下", "读一读", "读篇", "念一下", "念一念",
            "学一下", "换到", "切换到", "听一下", "我想看", "看一看", "看一下",
            "英语阅读", "英文阅读", "英语课文", "英文短文", "分级阅读", "课文",
            "短文", "文章", "请读", "选", "念", "读", "看", "学", "听", "背"
        ]
        t = t.strip()
        for pfx in sorted(prefixes, key=len, reverse=True):
            if t.startswith(pfx):
                t = t[len(pfx):].strip()

        if not t:
            return None

        # 3. 别名与关键词匹配
        for idx, art in enumerate(self.articles):
            aliases = ARTICLE_ALIASES.get(art.id, [])
            for alias in aliases:
                if alias in t or (len(t) >= 2 and t in alias):
                    return (idx, art)

        # 4. 英文标题与篇名包含匹配
        for idx, art in enumerate(self.articles):
            title_lower = art.title.lower()
            if t in title_lower or title_lower in t:
                return (idx, art)

        return None
