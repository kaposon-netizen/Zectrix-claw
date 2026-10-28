"""记录想法（notes）+ 单词卡（word cards）本地存储。

隐私红线：**孩子记录的想法逐字内容属于孩子，保护儿童隐私，家长端不可见**。
所以 notes 只按 child_id 存本地，绝不进 parent gateway 的逐字面；家长最多看到聚合条数。

单词卡：轻量 Leitner 盒子排期（对齐 qbank 思路，但内容是词表，独立存储不耦合）。
持久化到一个 JSON 文件（默认 pocketclaw_notes.json），进程重启不丢。
"""
from __future__ import annotations

import copy
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# Leitner 盒子 → 下次复习间隔（秒）；盒子越高间隔越长
_LEITNER_INTERVAL_S = {0: 0, 1: 8 * 3600, 2: 24 * 3600, 3: 3 * 86400, 4: 7 * 86400, 5: 15 * 86400}
_MAX_BOX = 5


@dataclass
class Note:
    id: str
    text: str
    ts: float
    source: str = "voice"          # voice|button|text
    tag: str = ""                  # 可选：想法分类（灵感/待办/问题…）
    done: bool = False             # 说「XX 做了」→ 打勾 + 删除线
    done_ts: float = 0.0


@dataclass
class WordCard:
    id: str
    word: str
    reading: str = ""              # 拼音/音标（IPA 墨水屏字体渲染不出，不上屏，仅存档）
    meaning: str = ""              # 一句话中文释义
    example: str = ""              # 英文例句
    example_cn: str = ""           # 例句中文翻译
    box: int = 0                   # Leitner 盒号
    due: float = 0.0               # 下次复习时间戳
    added: float = 0.0


# 首次使用播种的预设单词（覆盖小学 1~6 年级核心词汇 · 源自 PEP 人教版课标词库 qwerty-learner）
PRESET_WORDS = [
    # ── 低段启蒙词汇（1~2年级）──
    ("apple", "苹果", "I eat an apple.", "我吃一个苹果。"),
    ("banana", "香蕉", "The banana is yellow.", "香蕉是黄色的。"),
    ("cat", "猫", "The cat is cute.", "这只猫很可爱。"),
    ("dog", "狗", "I have a dog.", "我有一只狗。"),
    ("book", "书", "I read a book.", "我读一本书。"),
    ("water", "水", "I drink water.", "我喝水。"),
    ("happy", "高兴的", "I am happy today.", "我今天很高兴。"),
    ("run", "跑", "I can run fast.", "我能跑得很快。"),
    ("school", "学校", "I go to school.", "我去上学。"),
    ("friend", "朋友", "She is my friend.", "她是我的朋友。"),
    ("red", "红色的", "The apple is red.", "这个苹果是红色的。"),
    ("sun", "太阳", "The sun is bright.", "太阳很明亮。"),
    ("milk", "牛奶", "I like milk.", "我喜欢牛奶。"),
    ("jump", "跳", "I can jump high.", "我能跳得很高。"),
    ("tree", "树", "The tree is tall.", "这棵树很高。"),
    ("bird", "鸟", "The bird can fly.", "鸟儿会飞。"),
    ("fish", "鱼", "Fish live in water.", "鱼生活在水里。"),
    ("egg", "蛋", "I eat an egg.", "我吃一个鸡蛋。"),
    ("rain", "雨", "It is raining.", "下雨了。"),
    ("star", "星星", "The star is bright.", "星星很亮。"),
    ("moon", "月亮", "The moon is round.", "月亮是圆的。"),
    ("hand", "手", "Wash your hands.", "洗洗你的手。"),
    ("foot", "脚", "My foot hurts.", "我的脚疼。"),
    ("blue", "蓝色的", "The sky is blue.", "天空是蓝色的。"),
    ("green", "绿色的", "The grass is green.", "草是绿色的。"),
    ("big", "大的", "The elephant is big.", "大象很大。"),
    ("small", "小的", "The ant is small.", "蚂蚁很小。"),
    ("hot", "热的", "The soup is hot.", "汤很烫。"),
    ("cold", "冷的", "The ice is cold.", "冰是冷的。"),
    ("eat", "吃", "I eat lunch.", "我吃午饭。"),
    ("sleep", "睡觉", "I sleep at night.", "我晚上睡觉。"),
    ("play", "玩", "We play together.", "我们一起玩。"),
    ("sing", "唱歌", "I like to sing.", "我喜欢唱歌。"),
    ("home", "家", "I go home.", "我回家。"),
    ("mother", "妈妈", "I love my mother.", "我爱妈妈。"),
    ("father", "爸爸", "My father is tall.", "我爸爸很高。"),
    ("teacher", "老师", "She is my teacher.", "她是我的老师。"),
    ("pencil", "铅笔", "I have a pencil.", "我有一支铅笔。"),
    ("flower", "花", "The flower is pretty.", "这朵花很漂亮。"),
    # ── 中段课标核心词汇（3~4年级：学习、身体、动物、日常场所）──
    ("ruler", "直尺", "Show me your ruler.", "把你的直尺拿给我看。"),
    ("eraser", "橡皮擦", "I have a white eraser.", "我有一块白色的橡皮擦。"),
    ("bag", "书包", "My schoolbag is heavy.", "我的书包很重。"),
    ("pen", "钢笔", "Write with a pen.", "用钢笔写字。"),
    ("desk", "书桌", "Clean your desk please.", "请把你的书桌清理干净。"),
    ("chair", "椅子", "Sit on the chair.", "请坐在椅子上。"),
    ("black", "黑色的", "The cat is black.", "那只猫是黑色的。"),
    ("white", "白色的", "Snow is white.", "雪是白色的。"),
    ("yellow", "黄色的", "The duck is yellow.", "这只鸭子是黄色的。"),
    ("orange", "橙色的", "An orange is orange.", "橙子是橙色的。"),
    ("head", "头", "Touch your head.", "摸摸你的头。"),
    ("face", "脸", "Wash your face.", "洗洗你的脸。"),
    ("eye", "眼睛", "Close your eyes.", "闭上你的眼睛。"),
    ("ear", "耳朵", "Listen with your ears.", "用耳朵倾听。"),
    ("nose", "鼻子", "I have a small nose.", "我有一个小鼻子。"),
    ("mouth", "嘴巴", "Open your mouth.", "张开你的嘴巴。"),
    ("arm", "手臂", "Raise your arm.", "举起你的手臂。"),
    ("leg", "腿", "He has long legs.", "他的腿很长。"),
    ("sister", "姐妹", "My sister is cute.", "我的妹妹很可爱。"),
    ("brother", "兄弟", "He is my big brother.", "他是我的哥哥。"),
    ("family", "家庭", "I love my family.", "我爱我的家。"),
    ("duck", "鸭子", "The duck swims fast.", "鸭子游得很快。"),
    ("pig", "猪", "The pig is fat.", "这头猪胖乎乎的。"),
    ("bear", "熊", "The bear sleeps in winter.", "熊在冬天冬眠。"),
    ("tiger", "老虎", "The tiger is strong.", "老虎很强壮。"),
    ("elephant", "大象", "The elephant has a long trunk.", "大象有长长的鼻子。"),
    ("monkey", "猴子", "The monkey eats bananas.", "猴子吃香蕉。"),
    ("panda", "大熊猫", "The panda likes bamboo.", "大熊猫喜欢吃竹子。"),
    ("zoo", "动物园", "Let us go to the zoo.", "我们一起去动物园吧。"),
    ("park", "公园", "We run in the park.", "我们在公园里奔跑。"),
    ("classroom", "教室", "Our classroom is bright.", "我们的教室很明亮。"),
    ("library", "图书馆", "Be quiet in the library.", "在图书馆里要保持安静。"),
    ("playground", "操场", "We play on the playground.", "我们在操场上玩耍。"),
    ("sunny", "晴朗的", "It is a sunny day.", "今天是一个大晴天。"),
    ("rainy", "下雨的", "It is rainy outside.", "外面正在下雨。"),
    ("windy", "有风的", "The windy weather is cool.", "起风的天气真凉爽。"),
    ("cloudy", "多云的", "It is cloudy today.", "今天多云。"),
    ("snow", "雪", "I like playing in snow.", "我喜欢在雪里玩耍。"),
    ("warm", "温暖的", "Spring is warm.", "春天非常温暖。"),
    ("cool", "凉爽的", "Autumn is cool.", "秋天很凉爽。"),
    ("bread", "面包", "I eat bread for breakfast.", "我早餐吃面包。"),
    ("rice", "米饭", "We eat rice every day.", "我们每天都吃米饭。"),
    ("juice", "果汁", "Orange juice is sweet.", "橙汁很甜。"),
    ("cake", "蛋糕", "Happy birthday cake.", "生日快乐蛋糕。"),
    ("noodles", "面条", "I like hot noodles.", "我喜欢吃热气腾腾的面条。"),
    ("vegetable", "蔬菜", "Eat more vegetables.", "要多吃新鲜蔬菜。"),
    ("jacket", "夹克外套", "Put on your jacket.", "穿上你的夹克外套。"),
    ("shoes", "鞋子", "Tie your shoes.", "系好你的鞋带。"),
    ("hat", "帽子", "Wear a sun hat.", "戴上一顶遮阳帽。"),
    ("window", "窗户", "Open the window please.", "请把窗户打开。"),
    ("door", "门", "Close the door softly.", "轻轻地把门关上。"),
    ("clock", "时钟", "Look at the clock.", "看一看那个时钟。"),
    # ── 高段课标进阶词汇（5~6年级：星期、季节、品质、动作探索）──
    ("Monday", "星期一", "We have music on Monday.", "我们周一有音乐课。"),
    ("Friday", "星期五", "Friday is my favorite day.", "星期五是我最喜欢的一天。"),
    ("weekend", "周末", "Have a good weekend.", "祝你周末愉快。"),
    ("season", "季节", "There are four seasons.", "一年有四个季节。"),
    ("spring", "春天", "Flowers bloom in spring.", "花朵在春天绽放。"),
    ("summer", "夏天", "Summer is sunny and hot.", "夏天阳光灿烂很炎热。"),
    ("autumn", "秋天", "Leaves turn yellow in autumn.", "树叶在秋天变黄。"),
    ("winter", "冬天", "It snows in winter.", "冬天会下雪。"),
    ("clever", "聪明的", "He is a clever boy.", "他是一个聪明的男孩。"),
    ("polite", "有礼貌的", "Always be polite to others.", "对别人要常怀礼貌。"),
    ("helpful", "乐于助人的", "She is helpful at home.", "她在家里很乐于助人。"),
    ("strong", "强壮的", "Exercise makes us strong.", "体育锻炼使我们强壮。"),
    ("brave", "勇敢的", "Be brave and try again.", "勇敢一点，再试一次。"),
    ("study", "学习/钻研", "We study hard at school.", "我们在学校刻苦学习。"),
    ("travel", "旅行", "I like to travel by train.", "我喜欢乘火车旅行。"),
]

# 首次使用播种的预设作业清单
PRESET_TASKS = [
    "语文：抄写生字两遍",
    "数学：口算 20 题",
    "英语：朗读课文 3 遍",
    "背古诗一首",
    "跳绳 100 个",
]


@dataclass
class Task:
    id: str
    text: str
    ts: float
    done: bool = False
    done_ts: float = 0.0
    subject: str = ""              # 可选：科目（数学/语文…）


@dataclass
class Habit:
    id: str
    name: str
    icon: str = "⭐"
    done: bool = False
    done_ts: float = 0.0
    streak: int = 0
    target_days: int = 21


PRESET_HABITS = [
    ("晨读语文/英语 15分钟", "📖"),
    ("课后跳绳/阳光运动 500个", "🏃"),
    ("独立自主整理书包与学具", "🎒"),
    ("远眺放松/眼保健操", "👀"),
    ("21:00准时洗漱就寝", "🛏️"),
]

DEFAULT_WISHES = [
    {"id": "w_1", "title": "看一部周末动画电影", "stars_needed": 30, "icon": "🎬", "redeemed": False},
    {"id": "w_2", "title": "去一次游乐园/动物园", "stars_needed": 50, "icon": "🎡", "redeemed": False},
    {"id": "w_3", "title": "挑选一本心愿课外书", "stars_needed": 20, "icon": "📚", "redeemed": False},
    {"id": "w_4", "title": "吃一次心愿美味大餐", "stars_needed": 25, "icon": "🍕", "redeemed": False},
]

DEFAULT_TIMETABLE = {
    "1": {"day_name": "周一", "courses": ["语文", "数学", "英语", "体育", "道德与法治", "美术"], "note": "穿礼服戴红领巾升旗"},
    "2": {"day_name": "周二", "courses": ["数学", "语文", "科学", "音乐", "劳动", "体育"], "note": "带科学学具与口风琴"},
    "3": {"day_name": "周三", "courses": ["语文", "英语", "数学", "美术", "信息科技", "体育"], "note": "带水彩笔、跳绳"},
    "4": {"day_name": "周四", "courses": ["数学", "语文", "体育", "英语", "科学", "综合实践"], "note": "带劳作剪刀与彩纸"},
    "5": {"day_name": "周五", "courses": ["语文", "数学", "班队活动", "音乐", "阅读", "体育"], "note": "周末作业登记、借阅书归还"},
}

PRESET_IDIOMS = [
    # ── 勤学笃行篇 ──
    {"idiom": "闻鸡起舞", "pinyin": "wén jī qǐ wǔ", "meaning": "听到鸡叫就起来舞剑。比喻有志气的人发奋图强，刻苦学习。", "source": "《晋书·祖逖传》", "example": "作为新时代的小学生，我们要有闻鸡起舞的勤勉精神。"},
    {"idiom": "持之以恒", "pinyin": "chí zhī yǐ héng", "meaning": "长久地坚持下去。形容有恒心、有毅力。", "source": "清·曾国藩《家训》", "example": "学习任何知识都要持之以恒，不能三天打鱼两天晒网。"},
    {"idiom": "敏而好学", "pinyin": "mǐn ér hào xué", "meaning": "天资聪敏又爱好学习。", "source": "《论语·公冶长》", "example": "敏而好学，不耻下问，是古人传给我们的重要学习态度。"},
    {"idiom": "业精于勤", "pinyin": "yè jīng yú qín", "meaning": "学业由于勤奋而精深，由于懈怠而荒废。", "source": "唐·韩愈《进学解》", "example": "业精于勤荒于嬉，行成于思毁于随。"},
    {"idiom": "专心致志", "pinyin": "zhuān xīn zhì zhì", "meaning": "把心思全放在上面。形容一心一意，聚精会神。", "source": "《孟子·告子上》", "example": "上课时专心致志听讲，做作业才会事半功倍。"},
    {"idiom": "水滴石穿", "pinyin": "shuǐ dī shí chuān", "meaning": "水滴不断地下落，天长日久能把石头滴穿。比喻坚持不懈，微小力量也能成就大业。", "source": "《汉书·枚乘传》", "example": "只要我们有水滴石穿的坚韧毅力，任何学习难题都能攻克。"},
    {"idiom": "熟能生巧", "pinyin": "shú néng shēng qiǎo", "meaning": "熟练了就能掌握精巧的窍门。", "source": "宋·欧阳修《归田录》", "example": "每天坚持练习口算与朗读，熟能生巧，速度自然会越来越快。"},
    {"idiom": "温故知新", "pinyin": "wēn gù zhī xīn", "meaning": "温习旧的知识，进而能够得到新的理解与体会。", "source": "《论语·为政》", "example": "每天写作业前先翻看课本温故知新，掌握得更加扎实。"},
    {"idiom": "程门立雪", "pinyin": "chéng mén lì xuě", "meaning": "学生在老师门外雪中肃立等候。比喻尊师重道、虚心求学。", "source": "《宋史·杨时传》", "example": "程门立雪的典故教育我们无论何时都要尊敬老师、虚心求教。"},
    {"idiom": "囊萤映雪", "pinyin": "náng yíng yìng xuě", "meaning": "捉萤火虫照亮读书，借雪地反光夜读。形容家境贫寒仍勤奋苦读。", "source": "《晋书·车胤传》", "example": "古人囊萤映雪刻苦攻读，我们拥有这么好的条件更应珍惜时光。"},
    {"idiom": "悬梁刺股", "pinyin": "xuán liáng cì gǔ", "meaning": "把头发挂在梁上，用锥子刺大腿来提神读书。形容发愤苦读的精神。", "source": "《战国策·秦策一》", "example": "孙敬悬梁、苏秦刺股，展现了古人战胜怠惰的超常毅力。"},
    # ── 寓言哲理篇 ──
    {"idiom": "亡羊补牢", "pinyin": "wáng yáng bǔ láo", "meaning": "羊丢失了再去修补羊圈，还不算晚。比喻出了差错及时补救。", "source": "《战国策·楚策四》", "example": "虽然这次考试有错题，但亡羊补牢为时不晚，订正复习好最关键。"},
    {"idiom": "守株待兔", "pinyin": "shǒu zhū dài tù", "meaning": "守着树桩等待兔子撞上来。比喻不主动努力而心存侥幸。", "source": "《韩非子·五蠹》", "example": "想要取得优异成绩必须脚踏实地，绝不能抱着守株待兔的侥幸心理。"},
    {"idiom": "拔苗助长", "pinyin": "bá miáo zhù zhǎng", "meaning": "把禾苗拔高来帮助它生长。比喻违反事物发展客观规律，急于求成反而坏事。", "source": "《孟子·公孙丑上》", "example": "学习知识要循序渐进，拔苗助长往往适得其反。"},
    {"idiom": "刻舟求剑", "pinyin": "kè zhōu qiú jiàn", "meaning": "在掉落宝剑的船舷上刻记号去寻找。比喻拘泥成法、不知客观情况已变。", "source": "《吕氏春秋·察今》", "example": "时代在发展，我们思考问题不能刻舟求剑，要学会变通。"},
    {"idiom": "掩耳盗铃", "pinyin": "yǎn ěr dào líng", "meaning": "捂住自己的耳朵去偷铃铛。比喻自欺欺人。", "source": "《吕氏春秋·自知》", "example": "学习上不懂装懂就像掩耳盗铃，最后骗的其实是自己。"},
    {"idiom": "井底之蛙", "pinyin": "jǐng dǐ zhī wā", "meaning": "井底的青蛙只能看到井口大的天空。比喻眼界狭隘、见识短浅的人。", "source": "《庄子·秋水》", "example": "我们要多读书、多开阔眼界，不做目光狭隘的井底之蛙。"},
    {"idiom": "狐假虎威", "pinyin": "hú jiǎ hǔ wēi", "meaning": "狐狸借着老虎的威势吓跑百兽。比喻依仗别人的威势来欺负人。", "source": "《战国策·楚策一》", "example": "仗着别人的威风耀武扬威，就像狐假虎威一样可笑。"},
    {"idiom": "愚公移山", "pinyin": "yú gōng yí shān", "meaning": "愚公不畏艰难移平大山。比喻做事有坚韧不拔的毅力与必胜决心。", "source": "《列子·汤问》", "example": "发扬愚公移山的精神，迎难而上，终能战胜一切挑战。"},
    # ── 优秀品德与立志篇 ──
    {"idiom": "饮水思源", "pinyin": "yǐn shuǐ sī yuán", "meaning": "喝水的时候想到水源。比喻不忘本，时刻懂得感恩。", "source": "庾信《徵调曲》", "example": "我们在享受幸福生活的同时，要懂得饮水思源，感激父母与师长的付出。"},
    {"idiom": "精卫填海", "pinyin": "jīng wèi tián hǎi", "meaning": "精卫鸟衔石填海。比喻意志坚决，不向艰难险阻低头。", "source": "《山海经·北山经》", "example": "我国航天科学家凭着精卫填海的韧劲，攻克了一个又一个技术难关。"},
    {"idiom": "见贤思齐", "pinyin": "jiàn xián sī qí", "meaning": "见到德才兼备的人就向他看齐。", "source": "《论语·里仁》", "example": "看到同学身上有良好的学习习惯，我们应当见贤思齐，向他看齐。"},
    {"idiom": "同舟共济", "pinyin": "tóng zhōu gòng jì", "meaning": "同坐一条船的人在风浪中互相救助。比喻团结互助，共同克服困难。", "source": "《孙子·九地》", "example": "班级集体活动中大家同舟共济，齐心协力赢得了接力比赛第一名。"},
    {"idiom": "海纳百川", "pinyin": "hǎi nà bǎi chuān", "meaning": "大海容纳成千上万条江河。形容胸怀宽广博大，有包容心。", "source": "晋·袁宏《三国名臣序赞》", "example": "做人要有海纳百川的宽阔胸襟，包容理解身边不同意见的同学。"},
    {"idiom": "循序渐进", "pinyin": "xún xù jiàn jìn", "meaning": "按一定的顺序和步骤逐渐深入提高。", "source": "《论语·宪问》朱熹注", "example": "基础知识必须循序渐进地学扎实，不能好高骛远。"},
    {"idiom": "博学笃志", "pinyin": "bó xué dǔ zhì", "meaning": "广泛学习而且坚守崇高的志向。", "source": "《论语·子张》", "example": "博学笃志，切问而近思，是古人勉励青少年求学的至理名言。"},
    {"idiom": "开卷有益", "pinyin": "kāi juàn yǒu yì", "meaning": "打开书本阅读就一定会得到收获启发。", "source": "宋·王辟之《渑水燕谈录》", "example": "无论名著还是科普，开卷有益，多读书总能增长智慧。"},
    {"idiom": "学无止境", "pinyin": "xué wú zhǐ jìng", "meaning": "学问和知识是没有止境的。", "source": "清·刘开《问说》", "example": "世界如此浩瀚，学无止境，我们要时刻保持谦虚求知的童心。"},
    {"idiom": "一诺千金", "pinyin": "yī nuò qiān jīn", "meaning": "一句许诺价值千两黄金。形容说话极其讲信用。", "source": "《史记·季布栾布列传》", "example": "做人最重要的是言而有信、一诺千金，答应别人的事情就要做到。"},
    {"idiom": "实事求是", "pinyin": "shí shì qiú shì", "meaning": "从客观实际出发，探求事物的真实规律。", "source": "《汉书·河间献王传》", "example": "在科学小实验中，我们要本着实事求是的态度认真记录每一次数据。"},
    {"idiom": "千里之行", "pinyin": "qiān lǐ zhī xíng", "meaning": "千里远行是从迈出第一步开始的。比喻事情成功在于脚踏实地积累。", "source": "《老子·第六十四章》", "example": "千里之行始于足下，想要实现心中的理想，先从做好今天的作业开始。"},
    {"idiom": "举一反三", "pinyin": "jǔ yī fǎn sān", "meaning": "从一件事情类推而知道其他许多事情。比喻善于推理论证。", "source": "《论语·述而》", "example": "解数学题时不仅要会做一道题，更要学会总结规律、举一反三。"},
    {"idiom": "妙笔生花", "pinyin": "miào bǐ shēng huā", "meaning": "比喻文笔极其精妙，写出非常出彩生动的文章。", "source": "五代·王仁裕《开元天宝遗事》", "example": "他平时博览群书积淀丰厚，写起作文来妙笔生花，字字珠玑。"},
    {"idiom": "志存高远", "pinyin": "zhì cún gāo yuǎn", "meaning": "追求远大的理想和抱负。", "source": "诸葛亮《诫外甥书》", "example": "少年强则国强，青少年应当志存高远，树立远大的人生梦想。"},
    {"idiom": "迎难而上", "pinyin": "yíng nán ér shàng", "meaning": "遇到困难绝不退缩，勇敢地去克服战胜它。", "source": "传统格言", "example": "面对复杂的奥数题，我们要迎难而上，不轻言放弃。"},
    {"idiom": "滴水穿石", "pinyin": "dī shuǐ chuān shí", "meaning": "即使力量微小，只要目标专一持之以恒，终能达成奇迹。", "source": "宋·罗大经《鹤林玉露》", "example": "每天坚持阅读半小时，滴水穿石，一年下来就会收获惊人的阅读量。"},
]

@dataclass
class MistakeItem:
    id: str
    category: str  # "arith" | "dictation" | "reading"
    question: str
    expected: str
    wrong_answer: str = ""
    ts: float = 0.0
    count: int = 1


class NotesStore:
    def __init__(self, path: Optional[str] = None):
        # path=None → 默认落 pocketclaw_notes.json；path=":memory:" → 纯内存不落盘（测试用）
        self.path = path or str(Path(__file__).parent.parent / "pocketclaw_notes.json")
        self._persist = path != ":memory:"
        self._data: Dict[str, Dict[str, List[dict]]] = {}   # child_id → {notes:[], words:[]}
        self._seq = 0
        self._load()

    # ── 持久化 ──
    def _load(self) -> None:
        if not self._persist:
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self._data = {}

    def _save(self) -> None:
        if not self._persist:
            return
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def _bucket(self, child_id: str) -> Dict[str, List[dict]]:
        b = self._data.setdefault(child_id, {"notes": [], "words": [], "tasks": []})
        for k in ("notes", "words", "tasks"):     # 兼容旧数据：补齐缺失的键
            b.setdefault(k, [])
        return b

    def _next_id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}{int(time.time())}_{self._seq}"

    # ── 记录想法 ──
    def add_note(self, child_id: str, text: str, *, source: str = "voice",
                 tag: str = "", now: Optional[float] = None) -> Note:
        note = Note(id=self._next_id("n"), text=text.strip(),
                    ts=now if now is not None else time.time(), source=source, tag=tag)
        self._bucket(child_id)["notes"].append(asdict(note))
        self._save()
        return note

    def list_notes(self, child_id: str, limit: int = 20) -> List[Note]:
        rows = self._bucket(child_id)["notes"][-limit:]
        return [Note(**r) for r in reversed(rows)]   # 新→旧

    def note_count(self, child_id: str) -> int:
        return len(self._bucket(child_id)["notes"])

    def complete_note(self, child_id: str, text: str, now: Optional[float] = None) -> Optional[Note]:
        """说「XX 做了」→ 把匹配的想法标记完成（打勾 + 删除线）。"""
        needle = (text or "").strip().lower()
        if not needle:
            return None
        t = now if now is not None else time.time()
        for r in self._bucket(child_id)["notes"]:
            if not r.get("done") and needle in r["text"].lower():
                r["done"] = True
                r["done_ts"] = t
                self._save()
                return Note(**r)
        return None

    def delete_note(self, child_id: str, text: str) -> Optional[Note]:
        """说「删掉 XX」→ 从想法列表移除匹配的一条。"""
        needle = (text or "").strip().lower()
        if not needle:
            return None
        notes = self._bucket(child_id)["notes"]
        for i, r in enumerate(notes):
            if needle in r["text"].lower():
                removed = notes.pop(i)
                self._save()
                return Note(**removed)
        return None

    # ── 单词卡 ──
    def find_word(self, child_id: str, word: str) -> Optional[WordCard]:
        w = word.strip().lower()
        for r in self._bucket(child_id)["words"]:
            if r["word"].strip().lower() == w:
                return WordCard(**r)
        return None

    def add_word(self, child_id: str, word: str, *, reading: str = "", meaning: str = "",
                 example: str = "", example_cn: str = "", now: Optional[float] = None) -> WordCard:
        t = now if now is not None else time.time()
        # 去重：同词已在卡里 → 补齐空缺字段并返回既有卡，不重复入库
        existing = self.find_word(child_id, word)
        if existing:
            for r in self._bucket(child_id)["words"]:
                if r["id"] == existing.id:
                    if reading and not r.get("reading"):
                        r["reading"] = reading
                    if meaning and not r.get("meaning"):
                        r["meaning"] = meaning
                    if example and not r.get("example"):
                        r["example"] = example
                    if example_cn and not r.get("example_cn"):
                        r["example_cn"] = example_cn
                    self._save()
                    return WordCard(**r)
        card = WordCard(id=self._next_id("w"), word=word.strip(), reading=reading,
                        meaning=meaning, example=example, example_cn=example_cn, box=0, due=t, added=t)
        self._bucket(child_id)["words"].append(asdict(card))
        self._save()
        return card

    def ensure_seeded(self, child_id: str, now: Optional[float] = None) -> bool:
        """首次使用时初始化示例作业；单词由家长按年级选择导入。"""
        b = self._bucket(child_id)
        t = now if now is not None else time.time()
        added_any = False
        if not b.get("_seeded"):
            for tk in PRESET_TASKS:
                self.add_task(child_id, tk, now=t)
            b["_seeded"] = True
            added_any = True

        if added_any:
            self._save()
        return added_any

    def due_words(self, child_id: str, now: Optional[float] = None, limit: int = 20) -> List[WordCard]:
        t = now if now is not None else time.time()
        rows = [WordCard(**r) for r in self._bucket(child_id)["words"]]
        due = [c for c in rows if c.due <= t]
        due.sort(key=lambda c: (c.box, c.due))     # 先复习盒子低、最早到期的
        return due[:limit]

    def next_word(self, child_id: str, now: Optional[float] = None) -> Optional[WordCard]:
        due = self.due_words(child_id, now=now, limit=1)
        return due[0] if due else None

    def list_words(self, child_id: str, limit: int = 200) -> List[WordCard]:
        """全部单词卡（按加入顺序），供连续练习逐张翻。"""
        rows = [WordCard(**r) for r in self._bucket(child_id)["words"]]
        rows.sort(key=lambda c: c.added)
        return rows[:limit]

    def get_word(self, child_id: str, word_id: str) -> Optional[WordCard]:
        for r in self._bucket(child_id)["words"]:
            if r["id"] == word_id:
                return WordCard(**r)
        return None

    def review_word(self, child_id: str, word_id: str, *, remembered: bool,
                    now: Optional[float] = None) -> Optional[WordCard]:
        """复习一张卡：记得→升盒、忘了→回 0 盒（Leitner）。返回更新后的卡。"""
        t = now if now is not None else time.time()
        words = self._bucket(child_id)["words"]
        for r in words:
            if r["id"] == word_id:
                if remembered:
                    r["box"] = min(_MAX_BOX, int(r.get("box", 0)) + 1)
                else:
                    r["box"] = 0
                r["due"] = t + _LEITNER_INTERVAL_S.get(r["box"], 0)
                self._save()
                return WordCard(**r)
        return None

    def word_count(self, child_id: str) -> int:
        return len(self._bucket(child_id)["words"])

    # ── 作业清单 / 待办（#11）──
    def add_task(self, child_id: str, text: str, *, subject: str = "",
                 now: Optional[float] = None) -> Task:
        task = Task(id=self._next_id("t"), text=text.strip(),
                    ts=now if now is not None else time.time(), subject=subject)
        self._bucket(child_id)["tasks"].append(asdict(task))
        self._save()
        return task

    def list_tasks(self, child_id: str, *, include_done: bool = True,
                   limit: int = 20) -> List[Task]:
        rows = [Task(**r) for r in self._bucket(child_id)["tasks"]]
        if not include_done:
            rows = [t for t in rows if not t.done]
        rows.sort(key=lambda t: (t.done, t.ts))     # 未完成在前，按加入顺序
        return rows[:limit]

    def complete_task(self, child_id: str, *, task_id: str = "", text: str = "",
                      now: Optional[float] = None) -> Optional[Task]:
        """按 id 或按文本模糊匹配勾掉一项。返回被勾掉的项。"""
        t = now if now is not None else time.time()
        needle = (text or "").strip().lower()
        for r in self._bucket(child_id)["tasks"]:
            hit = (task_id and r["id"] == task_id) or \
                  (needle and not r["done"] and needle in r["text"].lower())
            if hit:
                r["done"] = True
                r["done_ts"] = t
                self._save()
                return Task(**r)
        return None

    def clear_done_tasks(self, child_id: str) -> int:
        b = self._bucket(child_id)
        before = len(b["tasks"])
        b["tasks"] = [r for r in b["tasks"] if not r.get("done")]
        self._save()
        return before - len(b["tasks"])

    def task_stats(self, child_id: str) -> Dict[str, int]:
        rows = self._bucket(child_id)["tasks"]
        done = sum(1 for r in rows if r.get("done"))
        return {"total": len(rows), "done": done, "todo": len(rows) - done}

    def delete_task(self, child_id: str, task_id: str) -> bool:
        """删除指定 id 的作业项。优先在当前 child_id 下删除，未命中则跨桶查找。"""
        b = self._bucket(child_id)
        before = len(b["tasks"])
        b["tasks"] = [r for r in b["tasks"] if r.get("id") != task_id]
        if len(b["tasks"]) != before:
            self._save()
            return True
        for cid, b_other in self._data.items():
            if cid == child_id:
                continue
            before_other = len(b_other.get("tasks", []))
            b_other["tasks"] = [r for r in b_other.get("tasks", []) if r.get("id") != task_id]
            if len(b_other.get("tasks", [])) != before_other:
                self._save()
                return True
        return False

    def toggle_task(self, child_id: str, task_id: str, now: Optional[float] = None) -> Optional[Task]:
        """切换作业项的完成状态。优先在当前 child_id 下切换，未命中则跨桶查找。"""
        t = now if now is not None else time.time()
        for r in self._bucket(child_id)["tasks"]:
            if r.get("id") == task_id:
                r["done"] = not r.get("done", False)
                r["done_ts"] = t if r["done"] else None
                self._save()
                return Task(**r)
        for cid, b in self._data.items():
            if cid == child_id:
                continue
            for r in b.get("tasks", []):
                if r.get("id") == task_id:
                    r["done"] = not r.get("done", False)
                    r["done_ts"] = t if r["done"] else None
                    self._save()
                    return Task(**r)
        return None

    def update_task(self, child_id: str, task_id: str, *, text: Optional[str] = None,
                    subject: Optional[str] = None, done: Optional[bool] = None) -> Optional[Task]:
        """家长修改作业内容/科目/完成状态。"""
        b = self._bucket(child_id)
        for r in b.get("tasks", []):
            if r.get("id") == task_id:
                if text is not None:
                    r["text"] = text.strip()
                if subject is not None:
                    r["subject"] = subject.strip()
                if done is not None:
                    r["done"] = bool(done)
                    if r["done"] and not r.get("done_ts"):
                        r["done_ts"] = time.time()
                self._save()
                return Task(**r)
        return None

    def delete_word(self, child_id: str, word_id_or_word: str) -> bool:
        """从生词本删除指定单词。"""
        b = self._bucket(child_id)
        before = len(b["words"])
        needle = word_id_or_word.strip().lower()
        b["words"] = [r for r in b["words"] if r.get("id") != needle and r.get("word", "").lower() != needle]
        if len(b["words"]) != before:
            self._save()
            return True
        return False

    def update_word(self, child_id: str, word_id: str, *, word: Optional[str] = None,
                    meaning: Optional[str] = None, example: Optional[str] = None,
                    example_cn: Optional[str] = None) -> Optional[WordCard]:
        """家长修改生词卡内容/释义/例句。"""
        for r in self._bucket(child_id).get("words", []):
            if r.get("id") == word_id or (word and r.get("word", "").lower() == word.lower()):
                if word is not None:
                    r["word"] = word.strip()
                if meaning is not None:
                    r["meaning"] = meaning.strip()
                if example is not None:
                    r["example"] = example.strip()
                if example_cn is not None:
                    r["example_cn"] = example_cn.strip()
                self._save()
                return WordCard(**r)
        return None

    def set_dictation_words(self, child_id: str, words: List[Tuple[str, str, str]], title: str = "生字词听写") -> None:
        """设置该孩子的听写生字词单 [(词语, 拼音, 组词提示), ...] 及标题"""
        b = self._bucket(child_id)
        b["dictation_words"] = [list(w) for w in words]
        b["dictation_title"] = title.strip() or "生字词听写"
        self._save()

    def get_dictation_words(self, child_id: str) -> List[Tuple[str, str, str]]:
        """获取听写生字词单，未设置则返回空列表"""
        b = self._bucket(child_id)
        raw = b.get("dictation_words") or []
        return [tuple(w) for w in raw]

    def get_dictation_title(self, child_id: str) -> str:
        """获取当前听写词单标题"""
        b = self._bucket(child_id)
        return b.get("dictation_title") or "生字词听写"

    def list_dictation_sets(self, child_id: str) -> List[Dict[str, Any]]:
        """获取家长已保存的所有听写词单列表"""
        b = self._bucket(child_id)
        return b.get("dictation_sets") or []

    def save_dictation_set(self, child_id: str, title: str, words: List[Tuple[str, str, str]], set_id: Optional[str] = None) -> Dict[str, Any]:
        """保存或更新一份听写词单，并设为当前词单"""
        b = self._bucket(child_id)
        if "dictation_sets" not in b:
            b["dictation_sets"] = []
        sid = set_id or self._next_id("dset")
        item = {
            "id": sid,
            "title": title.strip() or "生字词听写",
            "words": [list(w) for w in words],
            "count": len(words),
            "updated_at": time.time()
        }
        idx = next((i for i, x in enumerate(b["dictation_sets"]) if x["id"] == sid), None)
        if idx is not None:
            b["dictation_sets"][idx] = item
        else:
            b["dictation_sets"].insert(0, item)
        b["dictation_words"] = [list(w) for w in words]
        b["dictation_title"] = item["title"]
        b["active_dictation_set_id"] = sid
        self._save()
        return item

    def select_dictation_set(self, child_id: str, set_id: str) -> bool:
        """家长选择激活某一词单，同步设为当前听写"""
        b = self._bucket(child_id)
        sets = b.get("dictation_sets") or []
        target = next((x for x in sets if x["id"] == set_id), None)
        if target:
            b["dictation_words"] = [list(w) for w in target.get("words", [])]
            b["dictation_title"] = target.get("title", "生字词听写")
            b["active_dictation_set_id"] = set_id
            self._save()
            return True
        return False

    def delete_dictation_set(self, child_id: str, set_id: str) -> bool:
        """删除指定的听写词单"""
        b = self._bucket(child_id)
        sets = b.get("dictation_sets") or []
        before = len(sets)
        b["dictation_sets"] = [x for x in sets if x["id"] != set_id]
        if len(b["dictation_sets"]) != before:
            self._save()
            return True
        return False

    def update_dictation_set(self, child_id: str, set_id: str, *, title: Optional[str] = None,
                             words: Optional[List[Tuple[str, str, str]]] = None) -> Optional[Dict[str, Any]]:
        """修改指定听写词单的名称或词条列表。"""
        b = self._bucket(child_id)
        sets = b.get("dictation_sets") or []
        for s in sets:
            if s.get("id") == set_id:
                if title is not None:
                    s["title"] = title.strip()
                if words is not None:
                    s["words"] = [list(w) for w in words]
                    s["count"] = len(words)
                s["updated_at"] = time.time()
                if b.get("active_dictation_set_id") == set_id:
                    if title is not None:
                        b["dictation_title"] = s["title"]
                    if words is not None:
                        b["dictation_words"] = s["words"]
                self._save()
                return s
        return None

    def add_dictation_record(self, child_id: str, title: str, words: List[Tuple[str, str, str]], now: Optional[float] = None) -> Dict[str, Any]:
        """孩子在 Note 4 端完成听写时记录一条听写记录，状态为 pending (待家长检查)"""
        b = self._bucket(child_id)
        if "dictation_records" not in b:
            b["dictation_records"] = []
        rec = {
            "id": self._next_id("drec"),
            "title": title or "生字词听写",
            "words": [list(w) for w in words],
            "count": len(words),
            "completed_at": now if now is not None else time.time(),
            "status": "pending",
            "mistakes": [],
            "stars_rewarded": 0,
            "comment": ""
        }
        b["dictation_records"].insert(0, rec)
        if len(b["dictation_records"]) > 50:
            b["dictation_records"] = b["dictation_records"][:50]
        self._save()
        return rec

    def list_dictation_records(self, child_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """获取听写完成记录"""
        b = self._bucket(child_id)
        return (b.get("dictation_records") or [])[:limit]

    def review_dictation_record(self, child_id: str, record_id: str,
                              mistakes: List[str], stars: int = 1, comment: str = "",
                              irregular_words: Optional[List[Any]] = None,
                              auto_checked: bool = False,
                              check_result: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """家长检查批改听写：记录错字与书写不规范生字收录错题本、奖励星星"""
        b = self._bucket(child_id)
        records = b.get("dictation_records") or []
        rec = next((r for r in records if r["id"] == record_id), None)
        if not rec:
            return None
        rec["status"] = "reviewed"
        rec["mistakes"] = list(mistakes)
        rec["stars_rewarded"] = stars
        rec["comment"] = comment
        rec["reviewed_at"] = time.time()
        rec["auto_checked"] = auto_checked
        if check_result:
            rec["check_result"] = check_result

        # 规范化 irregular_words（支持字符串或详细字典对象）
        norm_irregular: List[Dict[str, Any]] = []
        if irregular_words:
            for item in irregular_words:
                if isinstance(item, str):
                    w = item.strip()
                    if w:
                        norm_irregular.append({
                            "word": w,
                            "reason": "笔画或间架结构书写不规范",
                            "suggestion": "注意规范笔顺与间架结构比例",
                            "recognized_text": ""
                        })
                elif isinstance(item, dict):
                    w = str(item.get("word", "")).strip()
                    if w:
                        norm_irregular.append({
                            "word": w,
                            "reason": str(item.get("reason", "笔画或间架结构书写不规范")),
                            "suggestion": str(item.get("suggestion", "注意规范笔顺与间架结构比例")),
                            "recognized_text": str(item.get("recognized_text", ""))
                        })
        rec["irregular_words"] = norm_irregular

        # 1. 错误的字列入错题本 (Mistake Book)
        for m in mistakes:
            if m.strip():
                reason = "字形写错/错别字"
                if check_result and isinstance(check_result.get("items"), list):
                    for it in check_result["items"]:
                        if isinstance(it, dict) and it.get("word") == m.strip() and it.get("reason"):
                            reason = it["reason"]
                            break
                self.add_mistake(child_id, category="dictation", question=f"生字听写错字：{m}",
                                expected=m, wrong_answer=reason, now=rec["reviewed_at"])

        # 2. 书写不规范的字列入错题本 (Mistake Book)
        for irreg in norm_irregular:
            w = irreg["word"]
            r = irreg.get("reason", "笔画或间架结构书写不规范")
            sug = irreg.get("suggestion", "")
            wrong_desc = f"书写不规范：{r}" + (f" ({sug})" if sug else "")
            self.add_mistake(child_id, category="dictation", question=f"生字听写书写不规范：{w}",
                            expected=w, wrong_answer=wrong_desc, now=rec["reviewed_at"])

        if stars > 0:
            tag = "拍照智能批改" if auto_checked else "家长批改"
            self.add_stars(child_id, stars, reason=f"完成生字听写{tag}({rec['title']})")
        self._save()
        return rec

    def set_poem_target(self, child_id: str, poem_title: str) -> None:
        """家长设置当前要背诵的古诗篇目。"""
        b = self._bucket(child_id)
        b["poem_target"] = poem_title.strip()
        self._save()

    def get_poem_target(self, child_id: str) -> str:
        """获取当前设置的古诗篇目，默认静夜思。"""
        b = self._bucket(child_id)
        return b.get("poem_target") or "静夜思"

    def set_arith_config(self, child_id: str, config: Dict[str, Any]) -> None:
        """家长设置口算题型与题量配置。"""
        b = self._bucket(child_id)
        b["arith_config"] = dict(config)
        self._save()

    def get_arith_config(self, child_id: str) -> Dict[str, Any]:
        """获取口算配置，默认二年级10道。"""
        b = self._bucket(child_id)
        return b.get("arith_config") or {"grade": 2, "count": 10, "op_type": "all"}

    def set_reading_article(self, child_id: str, article_id: str) -> None:
        """家长设置当前要阅读的英语短文。"""
        b = self._bucket(child_id)
        b["reading_article"] = article_id.strip()
        self._save()

    def get_reading_article(self, child_id: str) -> str:
        """获取当前要阅读的英语短文 ID。"""
        b = self._bucket(child_id)
        return b.get("reading_article") or "art_sunny_day"

    def add_parent_message(self, child_id: str, text: str, now: Optional[float] = None) -> Dict[str, Any]:
        """家长发送远程便签留言（推送到 Note 4 墨水屏）。"""
        b = self._bucket(child_id)
        if "messages" not in b:
            b["messages"] = []
        msg = {
            "id": self._next_id("msg"),
            "text": text.strip(),
            "ts": now if now is not None else time.time(),
            "read": False
        }
        b["messages"].append(msg)
        self._save()
        return msg

    def get_latest_parent_message(self, child_id: str) -> Optional[Dict[str, Any]]:
        b = self._bucket(child_id)
        msgs = b.get("messages") or []
        return msgs[-1] if msgs else None

    # ── 家长可见的聚合（无逐字）──
    def aggregate(self, child_id: str) -> Dict[str, Any]:
        b = self._bucket(child_id)
        due = len(self.due_words(child_id))
        ts = self.task_stats(child_id)
        return {"notes": len(b["notes"]), "words": len(b["words"]), "words_due": due,
                "tasks": ts["total"], "tasks_todo": ts["todo"]}

    # ── 小学生核心功能 1：好习惯打卡（Habit Routine）──
    def list_habits(self, child_id: str) -> List[Habit]:
        """获取习惯打卡列表，首次使用播种小学生常用习惯。"""
        b = self._bucket(child_id)
        if "habits" not in b or not b["habits"]:
            b["habits"] = []
            for name, icon in PRESET_HABITS:
                h = Habit(id=self._next_id("h"), name=name, icon=icon, done=False, streak=1)
                b["habits"].append(asdict(h))
            self._save()
        return [Habit(**r) for r in b["habits"]]

    def add_habit(self, child_id: str, name: str, icon: str = "⭐") -> Habit:
        """家长端自定义添加好习惯。"""
        h = Habit(id=self._next_id("h"), name=name.strip(), icon=icon.strip() or "⭐")
        b = self._bucket(child_id)
        if "habits" not in b:
            b["habits"] = []
        b["habits"].append(asdict(h))
        self._save()
        return h

    def toggle_habit(self, child_id: str, habit_id: str, now: Optional[float] = None) -> Optional[Habit]:
        """切换打卡状态，打卡成功累加 streak 连续天数。"""
        t = now if now is not None else time.time()
        for h in self.list_habits(child_id):
            if h.id == habit_id:
                h.done = not h.done
                h.done_ts = t if h.done else 0.0
                if h.done:
                    h.streak += 1
                b = self._bucket(child_id)
                for r in b.get("habits", []):
                    if r["id"] == habit_id:
                        r["done"] = h.done
                        r["done_ts"] = h.done_ts
                        r["streak"] = h.streak
                        break
                self._save()
                return h
        return None

    def delete_habit(self, child_id: str, habit_id: str) -> bool:
        """删除习惯项。"""
        b = self._bucket(child_id)
        habits = b.get("habits", [])
        before = len(habits)
        b["habits"] = [r for r in habits if r.get("id") != habit_id]
        if len(b["habits"]) != before:
            self._save()
            return True
        return False

    def update_habit(self, child_id: str, habit_id: str, *, name: Optional[str] = None,
                     icon: Optional[str] = None, target_days: Optional[int] = None) -> Optional[Habit]:
        """家长修改习惯名称、图标或目标天数。"""
        b = self._bucket(child_id)
        for r in b.get("habits", []):
            if r.get("id") == habit_id:
                if name is not None:
                    r["name"] = name.strip()
                if icon is not None:
                    r["icon"] = icon.strip()
                if target_days is not None:
                    r["target_days"] = int(target_days)
                self._save()
                return Habit(**r)
        return None

    def habit_stats(self, child_id: str) -> Dict[str, Any]:
        habits = self.list_habits(child_id)
        done = sum(1 for h in habits if h.done)
        total_streak = max((h.streak for h in habits), default=0)
        bank = self.get_star_bank(child_id)
        return {
            "total": len(habits),
            "done": done,
            "max_streak": total_streak,
            "stars": done * 2,
            "star_balance": bank["balance"],
            "stars_total": bank["total_earned"],
        }

    # ── 自律星星存折与心愿兑换商场（Star Bank & Wishes）──
    def get_star_bank(self, child_id: str) -> Dict[str, Any]:
        b = self._bucket(child_id)
        if "stars_total" not in b:
            b["stars_total"] = 10  # 初始自律启动金
            b["stars_spent"] = 0
            b["wishes"] = [dict(w) for w in DEFAULT_WISHES]
            b["star_log"] = [{"ts": time.time(), "delta": 10, "reason": "萌新自律启动金"}]
            self._save()
        total = b.get("stars_total", 10)
        spent = b.get("stars_spent", 0)
        return {
            "balance": max(0, total - spent),
            "total_earned": total,
            "spent": spent,
            "wishes": b.get("wishes", []),
            "history": b.get("star_log", [])[-20:],
        }

    def add_stars(self, child_id: str, count: int, reason: str = "", now: Optional[float] = None) -> int:
        if count <= 0:
            return self.get_star_bank(child_id)["balance"]
        t = now if now is not None else time.time()
        b = self._bucket(child_id)
        self.get_star_bank(child_id)
        b["stars_total"] = b.get("stars_total", 0) + count
        if "star_log" not in b:
            b["star_log"] = []
        b["star_log"].append({"ts": t, "delta": count, "reason": reason or "自律激励"})
        self._save()
        return max(0, b["stars_total"] - b.get("stars_spent", 0))

    def redeem_wish(self, child_id: str, wish_id: str) -> Dict[str, Any]:
        b = self._bucket(child_id)
        bank = self.get_star_bank(child_id)
        balance = bank["balance"]
        wishes = b.get("wishes", [])
        for w in wishes:
            if w.get("id") == wish_id:
                needed = w.get("stars_needed", 0)
                if balance < needed:
                    return {"ok": False, "error": f"星星不足，还差 {needed - balance} 颗星星哦！", "balance": balance}
                w["redeemed"] = True
                w["redeemed_ts"] = time.time()
                b["stars_spent"] = b.get("stars_spent", 0) + needed
                if "star_log" not in b:
                    b["star_log"] = []
                b["star_log"].append({"ts": time.time(), "delta": -needed, "reason": f"心愿兑换: {w.get('title')}"})
                self._save()
                return {"ok": True, "wish": w, "balance": max(0, b["stars_total"] - b["stars_spent"])}
        return {"ok": False, "error": "未找到该心愿", "balance": balance}

    def add_wish(self, child_id: str, title: str, stars_needed: int, icon: str = "🎁") -> Dict[str, Any]:
        b = self._bucket(child_id)
        self.get_star_bank(child_id)
        w = {
            "id": self._next_id("wish"),
            "title": title.strip(),
            "stars_needed": max(1, stars_needed),
            "icon": icon.strip() or "🎁",
            "redeemed": False,
        }
        b["wishes"].append(w)
        self._save()
        return w

    def delete_wish(self, child_id: str, wish_id: str) -> bool:
        b = self._bucket(child_id)
        wishes = b.get("wishes", [])
        before = len(wishes)
        b["wishes"] = [w for w in wishes if w.get("id") != wish_id]
        if len(b["wishes"]) != before:
            self._save()
            return True
        return False

    def update_wish(self, child_id: str, wish_id: str, *, title: Optional[str] = None,
                    stars_needed: Optional[int] = None, icon: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """家长修改心愿内容、所需星数或图标。"""
        b = self._bucket(child_id)
        for w in b.get("wishes", []):
            if w.get("id") == wish_id:
                if title is not None:
                    w["title"] = title.strip()
                if stars_needed is not None:
                    w["stars_needed"] = max(1, int(stars_needed))
                if icon is not None:
                    w["icon"] = icon.strip()
                self._save()
                return w
        return None

    # ── 小学生核心功能 2：每周课程表与桌面课前书包备忘（Timetable）──
    def get_timetable(self, child_id: str) -> Dict[str, Any]:
        b = self._bucket(child_id)
        if "timetable" not in b or not b["timetable"]:
            b["timetable"] = copy.deepcopy(DEFAULT_TIMETABLE)
            self._save()
        return b["timetable"]

    def set_timetable(self, child_id: str, timetable: Dict[str, Any]) -> None:
        b = self._bucket(child_id)
        b["timetable"] = timetable
        self._save()

    def get_today_schedule(self, child_id: str, weekday: Optional[int] = None) -> Dict[str, Any]:
        """获取当天课程安排，weekday: 0=周一 ... 4=周五，默认获取本地当前星期。"""
        tt = self.get_timetable(child_id)
        if weekday is None:
            w = time.localtime().tm_wday
        else:
            w = weekday
        day_key = str(min(max(w + 1, 1), 5)) if w < 5 else "1"
        return tt.get(day_key) or DEFAULT_TIMETABLE["1"]

    # ── 小学生核心功能 3：错题消灭本（Mistake Elimination）──
    def list_mistakes(self, child_id: str, category: Optional[str] = None) -> List[MistakeItem]:
        b = self._bucket(child_id)
        items = b.get("mistakes", [])
        res = [MistakeItem(**r) for r in items]
        if category:
            res = [m for m in res if m.category == category]
        return res

    def add_mistake(self, child_id: str, category: str, question: str, expected: str,
                    wrong_answer: str = "", now: Optional[float] = None) -> MistakeItem:
        t = now if now is not None else time.time()
        b = self._bucket(child_id)
        if "mistakes" not in b:
            b["mistakes"] = []
        for r in b["mistakes"]:
            if r.get("category") == category and r.get("question", "").strip() == question.strip():
                r["count"] = r.get("count", 1) + 1
                r["wrong_answer"] = wrong_answer or r.get("wrong_answer", "")
                r["ts"] = t
                self._save()
                return MistakeItem(**r)
        m = MistakeItem(id=self._next_id("m"), category=category, question=question.strip(),
                        expected=expected.strip(), wrong_answer=wrong_answer.strip(), ts=t, count=1)
        b["mistakes"].append(asdict(m))
        self._save()
        return m

    def delete_mistake(self, child_id: str, mistake_id: str) -> bool:
        b = self._bucket(child_id)
        items = b.get("mistakes", [])
        before = len(items)
        b["mistakes"] = [r for r in items if r.get("id") != mistake_id]
        if len(b["mistakes"]) != before:
            self._save()
            return True
        return False

    def update_mistake(self, child_id: str, mistake_id: str, *, question: Optional[str] = None,
                       expected: Optional[str] = None, wrong_answer: Optional[str] = None,
                       category: Optional[str] = None) -> Optional[MistakeItem]:
        """家长修改错题题目、期望答案或历史错答。"""
        b = self._bucket(child_id)
        for r in b.get("mistakes", []):
            if r.get("id") == mistake_id:
                if question is not None:
                    r["question"] = question.strip()
                if expected is not None:
                    r["expected"] = expected.strip()
                if wrong_answer is not None:
                    r["wrong_answer"] = wrong_answer.strip()
                if category is not None:
                    r["category"] = category.strip()
                self._save()
                return MistakeItem(**r)
        return None

    def clear_mistakes(self, child_id: str, category: Optional[str] = None) -> int:
        b = self._bucket(child_id)
        items = b.get("mistakes", [])
        if category:
            before = len(items)
            b["mistakes"] = [r for r in items if r.get("category") != category]
            cnt = before - len(b["mistakes"])
        else:
            cnt = len(items)
            b["mistakes"] = []
        self._save()
        return cnt

    # ── 小学生核心功能 4：每日国学成语与励志金句（Daily Idiom & Wisdom）──
    def list_idioms(self) -> List[Dict[str, str]]:
        return PRESET_IDIOMS

    def get_daily_idiom(self, child_id: str) -> Dict[str, str]:
        b = self._bucket(child_id)
        if "cur_idiom" in b and b["cur_idiom"]:
            return b["cur_idiom"]
        idx = int(time.time() // 86400) % len(PRESET_IDIOMS)
        return PRESET_IDIOMS[idx]

    def set_daily_idiom(self, child_id: str, idiom_data: Dict[str, str]) -> None:
        b = self._bucket(child_id)
        b["cur_idiom"] = idiom_data
        self._save()

    # ── 伴学屏保模式（Screensaver / Standby Mode）──
    def get_screensaver_config(self, child_id: str) -> Dict[str, Any]:
        b = self._bucket(child_id)
        cfg = b.get("screensaver_config")
        if not cfg:
            cfg = {
                "default_mode": "cycle",  # quote | poem | art | countdown | timetable | cycle
                "auto_timeout_minutes": 5,
                "enabled": True,
            }
        return cfg

    def set_screensaver_config(self, child_id: str, config: Dict[str, Any]) -> None:
        b = self._bucket(child_id)
        b["screensaver_config"] = config
        self._save()

    def list_countdown_events(self, child_id: str) -> List[Dict[str, Any]]:
        b = self._bucket(child_id)
        events = b.get("countdown_events")
        if events is None:
            # 初始默认事件
            events = [
                {"id": "cd_final", "title": "期末大考", "target_date": "2026-12-25", "slogan": "认真复习，笃行致远！"},
                {"id": "cd_vacation", "title": "寒假放假", "target_date": "2027-01-15", "slogan": "快乐假期在向你招手！"},
                {"id": "cd_children", "title": "六一儿童节", "target_date": "2027-06-01", "slogan": "童心永驻，勇敢探索！"},
            ]
            b["countdown_events"] = events
        return events

    def add_countdown_event(self, child_id: str, title: str, target_date: str,
                            slogan: str = "每一天的努力都在发光！") -> Dict[str, Any]:
        b = self._bucket(child_id)
        if "countdown_events" not in b or b["countdown_events"] is None:
            b["countdown_events"] = self.list_countdown_events(child_id)
        ev_id = self._next_id("cd")
        ev = {
            "id": ev_id,
            "title": title.strip(),
            "target_date": target_date.strip(),
            "slogan": slogan.strip() or "加油！每一天的积累都在创造奇迹！"
        }
        b["countdown_events"].append(ev)
        self._save()
        return ev

    def delete_countdown_event(self, child_id: str, event_id: str) -> bool:
        b = self._bucket(child_id)
        events = b.get("countdown_events", [])
        init_len = len(events)
        b["countdown_events"] = [e for e in events if e.get("id") != event_id]
        if len(b["countdown_events"]) < init_len:
            self._save()
            return True
        return False

    # ── 功能导航菜单排序与频次统计（Menu Order & Frequency Auto-Sort）──
    def record_menu_click(self, child_id: str, mode: str) -> int:
        """记录功能菜单点击频次，用于选择越多越靠前的智能排序算法。"""
        if not mode:
            return 0
        b = self._bucket(child_id)
        clicks = b.setdefault("menu_clicks", {})
        clicks[mode] = clicks.get(mode, 0) + 1
        self._save()
        return clicks[mode]

    def get_menu_clicks(self, child_id: str) -> Dict[str, int]:
        """获取各功能的历史点击使用次数字典。"""
        b = self._bucket(child_id)
        return dict(b.get("menu_clicks", {}))

    def get_custom_menu_order(self, child_id: str) -> Optional[List[str]]:
        """获取家长手动调整的菜单功能顺序列表（mode 字符串列表）。"""
        b = self._bucket(child_id)
        return b.get("custom_menu_order")

    def set_custom_menu_order(self, child_id: str, order: List[str]) -> None:
        """家长端手动调整功能菜单顺序。"""
        b = self._bucket(child_id)
        b["custom_menu_order"] = [m for m in order if isinstance(m, str) and m.strip()]
        self._save()

    def reset_menu_order(self, child_id: str, reset_clicks: bool = False) -> None:
        """家长端重置菜单顺序：清空手动排序规则；若 reset_clicks=True，同时清空历史使用频次。"""
        b = self._bucket(child_id)
        b.pop("custom_menu_order", None)
        if reset_clicks:
            b.pop("menu_clicks", None)
        self._save()

    # ── 功能开关管理 (Feature Toggles) ──
    def get_disabled_features(self, child_id: str) -> List[str]:
        """获取当前孩子被家长停用的功能列表。"""
        b = self._bucket(child_id)
        return list(b.get("disabled_features", []))

    def set_feature_enabled(self, child_id: str, feature: str, enabled: bool) -> bool:
        """家长开关某一功能：enabled=True 开启，enabled=False 停用。"""
        b = self._bucket(child_id)
        disabled = set(b.get("disabled_features", []))
        if enabled:
            disabled.discard(feature.strip())
        else:
            disabled.add(feature.strip())
        b["disabled_features"] = list(disabled)
        self._save()
        return True

    def is_feature_enabled(self, child_id: str, feature: str) -> bool:
        """判定某一功能当前是否对孩子可用。"""
        b = self._bucket(child_id)
        disabled = set(b.get("disabled_features", []))
        return feature.strip() not in disabled

    def get_features_status(self, child_id: str) -> List[Dict[str, Any]]:
        """获取全部 19 项功能的开启状态、分类与元数据。"""
        from pocketclaw.display import MENU_ITEMS
        disabled = set(self.get_disabled_features(child_id))
        category_map = {
            "task": "基础管理", "recite": "学科进阶", "dictation": "学科进阶", "arith": "学科进阶",
            "vocab": "学科进阶", "reading": "学科进阶", "focus": "自律专注", "habit": "自律专注",
            "timetable": "生活日常", "idiom": "国学素养", "mistakes": "错题攻坚", "feynman": "思维表达",
            "guess": "趣味科普", "verify": "科学探究", "feihua": "国学素养", "hanzi": "学科进阶",
            "news": "视野长廊", "chat": "智能学伴", "screensaver": "生活日常",
        }
        res = []
        for name, desc, key in MENU_ITEMS:
            res.append({
                "key": key,
                "name": name,
                "desc": desc,
                "category": category_map.get(key, "综合功能"),
                "enabled": key not in disabled,
            })
        return res

    def get_sorted_menu_items(self, child_id: str) -> List[Tuple[str, str, str]]:
        """获取当前孩子的动态排序菜单列表。
        规则：
          1. 若家长手动配置了 custom_menu_order，严格优先按家长的自定义顺序呈现；
          2. 否则启用智能算法：按使用频次（点击次数）降序排列，选择越多的功能越靠前；
          3. 严格遵循家长端功能开关设置，过滤已停用的功能。
        """
        from pocketclaw.display import MENU_ITEMS
        item_map = {m: (name, desc, m) for name, desc, m in MENU_ITEMS}
        custom_order = self.get_custom_menu_order(child_id)

        if custom_order:
            ordered = []
            seen = set()
            for m in custom_order:
                if m in item_map and m not in seen:
                    ordered.append(item_map[m])
                    seen.add(m)
            # 补全可能新增的菜单项
            for item in MENU_ITEMS:
                if item[2] not in seen:
                    ordered.append(item)
            candidates = ordered
        else:
            # 智能频次算法：选择越多越靠前
            clicks = self.get_menu_clicks(child_id)
            candidates = sorted(
                MENU_ITEMS,
                key=lambda it: (-clicks.get(it[2], 0), MENU_ITEMS.index(it))
            )

        # 严格过滤掉家长停用的功能
        disabled = set(self.get_disabled_features(child_id))
        filtered = [item for item in candidates if item[2] not in disabled]
        # 保护性兜底：若家长停用了所有功能，至少保留作业项
        return filtered if filtered else [item_map.get("task", MENU_ITEMS[0])]

    # ── 孩子资料（与其他学习数据一样按孩子分桶）──
    def get_child_profile(self, child_id: str) -> Dict[str, Any]:
        defaults = {"name": "", "grade": 0, "gender": "", "interests": ""}
        stored = self._bucket(child_id).get("child_profile")
        return {**defaults, **stored} if isinstance(stored, dict) else defaults

    def set_child_profile(self, child_id: str, profile: Dict[str, Any]) -> Dict[str, Any]:
        self._bucket(child_id)["child_profile"] = dict(profile)
        self._save()
        return self.get_child_profile(child_id)

    # ── 学伴人设与风格定制 ──
    def get_companion_config(self, child_id: str) -> Dict[str, Any]:
        """获取学伴人设配置（名称、性格、风格、提示词）。"""
        bucket = self._bucket(child_id)
        cfg = bucket.get("companion_config")
        default_cfg = {
            "name": "虾崽",
            "persona": (
                "你的第一只伴学小龙虾，天生好奇、话里带笑。最爱把话头交回给小朋友，"
                "遇到有趣的知识会兴奋地举例子，讲完只留一个小问题等他接。"
                "遇到自己不擅长的领域，会大方地把专家朋友请出来。"
            ),
            "style": "温柔耐心",
            "guidance": (
                "你在一块黑白墨水屏便利贴上和小朋友说话，屏幕很小、只能靠语音听。只输出要对小朋友讲的话本身，"
                "口语、亲切。**每次回复最多两句、总共不超过 40 个字**——说完就停，把话头留给孩子；"
                "英语练习时英文只说一句短句，再给一句中文意思。绝不要加脚注编号，绝不要出现「说明：」，"
                "也不要复述你的教学设计。结尾留一个短问题（不超过 12 个字）。"
            ),
            "max_words": 40,
        }
        if not cfg or not isinstance(cfg, dict):
            return default_cfg
        res = dict(default_cfg)
        res.update(cfg)
        return res

    def set_companion_config(self, child_id: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """更新学伴人设配置并持久化。"""
        bucket = self._bucket(child_id)
        current = self.get_companion_config(child_id)
        for k in ("name", "persona", "style", "guidance", "max_words"):
            if k in config and config[k] is not None:
                current[k] = config[k]
        bucket["companion_config"] = current
        self._save()
        return current

    def reset_companion_config(self, child_id: str) -> Dict[str, Any]:
        """恢复学伴出厂人设设置。"""
        bucket = self._bucket(child_id)
        bucket.pop("companion_config", None)
        self._save()
        return self.get_companion_config(child_id)

    # ── LLM 与 OCR 系统配置 ──
    def get_ai_config(self) -> Dict[str, Any]:
        """获取大模型与 OCR 运行配置（本地版本 / 在线版本）。"""
        cfg = self._data.get("__ai_config__")
        default_cfg = {
            "llm": {
                "mode": os.getenv("LLM_MODE", "online" if os.getenv("OPENAI_API_KEY") else "local"),
                "provider": os.getenv("LLM_PROVIDER", "deepseek"),
                "api_key": os.getenv("OPENAI_API_KEY", ""),
                "base_url": os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1"),
                "model": os.getenv("OPENAI_MODEL", "deepseek-chat"),
            },
            "ocr": {
                "mode": os.getenv("OCR_MODE", "online" if os.getenv("OPENAI_API_KEY") else "local"),
                "vision_model": os.getenv("OPENAI_VISION_MODEL", "gpt-4o-mini"),
                "api_key": os.getenv("OCR_API_KEY", "") or os.getenv("OPENAI_API_KEY", ""),
                "base_url": os.getenv("OCR_BASE_URL", "") or os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1"),
            }
        }
        if not cfg or not isinstance(cfg, dict):
            return default_cfg
        res = {
            "llm": dict(default_cfg["llm"]),
            "ocr": dict(default_cfg["ocr"]),
        }
        if "llm" in cfg and isinstance(cfg["llm"], dict):
            res["llm"].update(cfg["llm"])
        if "ocr" in cfg and isinstance(cfg["ocr"], dict):
            res["ocr"].update(cfg["ocr"])
        return res

    def set_ai_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """保存大模型与 OCR 配置并持久化。"""
        current = self.get_ai_config()
        if "llm" in config and isinstance(config["llm"], dict):
            for k in ("mode", "provider", "api_key", "base_url", "model"):
                if k in config["llm"] and config["llm"][k] is not None:
                    val = str(config["llm"][k]).strip()
                    if not val.startswith("sk-***"):
                        current["llm"][k] = val
        if "ocr" in config and isinstance(config["ocr"], dict):
            for k in ("mode", "vision_model", "api_key", "base_url"):
                if k in config["ocr"] and config["ocr"][k] is not None:
                    val = str(config["ocr"][k]).strip()
                    if not val.startswith("sk-***"):
                        current["ocr"][k] = val
        self._data["__ai_config__"] = current
        self._save()
        return current

    # ── 倒计时事件修改 ──
    def update_countdown_event(self, child_id: str, event_id: str, *,
                               title: Optional[str] = None, target_date: Optional[str] = None,
                               slogan: Optional[str] = None) -> Optional[Dict[str, Any]]:
        b = self._bucket(child_id)
        events = b.get("countdown_events", [])
        for e in events:
            if e.get("id") == event_id:
                if title is not None:
                    e["title"] = title.strip()
                if target_date is not None:
                    e["target_date"] = target_date.strip()
                if slogan is not None:
                    e["slogan"] = slogan.strip()
                self._save()
                return e
        return None

    # ── 课内古诗：家长自定义古诗库 CRUD ──
    def list_custom_poems(self, child_id: str) -> List[Dict[str, Any]]:
        b = self._bucket(child_id)
        return list(b.get("custom_poems", []))

    def add_custom_poem(self, child_id: str, title: str, author: str, dynasty: str, lines: List[str]) -> Dict[str, Any]:
        b = self._bucket(child_id)
        poems = b.setdefault("custom_poems", [])
        t_clean = title.strip()
        for p in poems:
            if p.get("title") == t_clean:
                p["author"] = author.strip()
                p["dynasty"] = dynasty.strip()
                p["lines"] = [line.strip() for line in lines if line.strip()]
                self._save()
                return p
        item = {
            "id": self._next_id("poem"),
            "title": t_clean,
            "author": author.strip(),
            "dynasty": dynasty.strip(),
            "lines": [line.strip() for line in lines if line.strip()],
            "created_at": time.time(),
        }
        poems.append(item)
        self._save()
        return item

    def update_custom_poem(self, child_id: str, poem_id_or_title: str, *,
                           title: Optional[str] = None, author: Optional[str] = None,
                           dynasty: Optional[str] = None, lines: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        b = self._bucket(child_id)
        for p in b.get("custom_poems", []):
            if p.get("id") == poem_id_or_title or p.get("title") == poem_id_or_title:
                if title is not None:
                    p["title"] = title.strip()
                if author is not None:
                    p["author"] = author.strip()
                if dynasty is not None:
                    p["dynasty"] = dynasty.strip()
                if lines is not None:
                    p["lines"] = [line.strip() for line in lines if line.strip()]
                self._save()
                return p
        return None

    def delete_custom_poem(self, child_id: str, poem_id_or_title: str) -> bool:
        b = self._bucket(child_id)
        poems = b.get("custom_poems", [])
        for i, p in enumerate(poems):
            if p.get("id") == poem_id_or_title or p.get("title") == poem_id_or_title:
                poems.pop(i)
                self._save()
                return True
        return False

    # ── 每日成语：家长自定义成语库 CRUD ──
    def list_custom_idioms(self, child_id: str) -> List[Dict[str, Any]]:
        b = self._bucket(child_id)
        return list(b.get("custom_idioms", []))

    def add_custom_idiom(self, child_id: str, idiom: str, pinyin: str, meaning: str,
                         source: str = "", example: str = "") -> Dict[str, Any]:
        b = self._bucket(child_id)
        idioms = b.setdefault("custom_idioms", [])
        i_clean = idiom.strip()
        for item in idioms:
            if item.get("idiom") == i_clean:
                item["pinyin"] = pinyin.strip()
                item["meaning"] = meaning.strip()
                item["source"] = source.strip()
                item["example"] = example.strip()
                self._save()
                return item
        item = {
            "id": self._next_id("idiom"),
            "idiom": i_clean,
            "pinyin": pinyin.strip(),
            "meaning": meaning.strip(),
            "source": source.strip(),
            "example": example.strip(),
            "created_at": time.time(),
        }
        idioms.append(item)
        self._save()
        return item

    def update_custom_idiom(self, child_id: str, idiom_id_or_name: str, *,
                            idiom: Optional[str] = None, pinyin: Optional[str] = None,
                            meaning: Optional[str] = None, source: Optional[str] = None,
                            example: Optional[str] = None) -> Optional[Dict[str, Any]]:
        b = self._bucket(child_id)
        for item in b.get("custom_idioms", []):
            if item.get("id") == idiom_id_or_name or item.get("idiom") == idiom_id_or_name:
                if idiom is not None:
                    item["idiom"] = idiom.strip()
                if pinyin is not None:
                    item["pinyin"] = pinyin.strip()
                if meaning is not None:
                    item["meaning"] = meaning.strip()
                if source is not None:
                    item["source"] = source.strip()
                if example is not None:
                    item["example"] = example.strip()
                self._save()
                return item
        return None

    def delete_custom_idiom(self, child_id: str, idiom_id_or_name: str) -> bool:
        b = self._bucket(child_id)
        idioms = b.get("custom_idioms", [])
        for i, item in enumerate(idioms):
            if item.get("id") == idiom_id_or_name or item.get("idiom") == idiom_id_or_name:
                idioms.pop(i)
                self._save()
                return True
        return False

    # ── 口算闯关：家长自定义靶向口算题库 CRUD ──
    def list_custom_arith(self, child_id: str) -> List[Dict[str, Any]]:
        b = self._bucket(child_id)
        return list(b.get("custom_arith", []))

    def add_custom_arith(self, child_id: str, expr: str, ans: str, hint: str = "") -> Dict[str, Any]:
        b = self._bucket(child_id)
        qs = b.setdefault("custom_arith", [])
        item = {
            "id": self._next_id("arith"),
            "expr": expr.strip(),
            "ans": ans.strip(),
            "hint": hint.strip(),
            "created_at": time.time(),
        }
        qs.append(item)
        self._save()
        return item

    def update_custom_arith(self, child_id: str, item_id: str, *,
                            expr: Optional[str] = None, ans: Optional[str] = None,
                            hint: Optional[str] = None) -> Optional[Dict[str, Any]]:
        b = self._bucket(child_id)
        for q in b.get("custom_arith", []):
            if q.get("id") == item_id:
                if expr is not None:
                    q["expr"] = expr.strip()
                if ans is not None:
                    q["ans"] = ans.strip()
                if hint is not None:
                    q["hint"] = hint.strip()
                self._save()
                return q
        return None

    def delete_custom_arith(self, child_id: str, item_id: str) -> bool:
        b = self._bucket(child_id)
        qs = b.get("custom_arith", [])
        for i, q in enumerate(qs):
            if q.get("id") == item_id:
                qs.pop(i)
                self._save()
                return True
        return False

    # ── 猜猜看：家长自定义谜题库 CRUD ──
    def list_custom_guesses(self, child_id: str) -> List[Dict[str, Any]]:
        b = self._bucket(child_id)
        return list(b.get("custom_guesses", []))

    def add_custom_guess(self, child_id: str, name: str, category: str, hint: str) -> Dict[str, Any]:
        b = self._bucket(child_id)
        items = b.setdefault("custom_guesses", [])
        item = {
            "id": self._next_id("guess"),
            "name": name.strip(),
            "category": category.strip(),
            "hint": hint.strip(),
            "created_at": time.time(),
        }
        items.append(item)
        self._save()
        return item

    def update_custom_guess(self, child_id: str, item_id: str, *,
                            name: Optional[str] = None, category: Optional[str] = None,
                            hint: Optional[str] = None) -> Optional[Dict[str, Any]]:
        b = self._bucket(child_id)
        for item in b.get("custom_guesses", []):
            if item.get("id") == item_id:
                if name is not None:
                    item["name"] = name.strip()
                if category is not None:
                    item["category"] = category.strip()
                if hint is not None:
                    item["hint"] = hint.strip()
                self._save()
                return item
        return None

    def delete_custom_guess(self, child_id: str, item_id: str) -> bool:
        b = self._bucket(child_id)
        items = b.get("custom_guesses", [])
        for i, item in enumerate(items):
            if item.get("id") == item_id:
                items.pop(i)
                self._save()
                return True
        return False

    # ── 核实一下：家长自定义科学常识辨伪库 CRUD ──
    def list_custom_facts(self, child_id: str) -> List[Dict[str, Any]]:
        b = self._bucket(child_id)
        return list(b.get("custom_facts", []))

    def add_custom_fact(self, child_id: str, kw1: str, kw2: str, is_true: bool, note: str, how: str = "") -> Dict[str, Any]:
        b = self._bucket(child_id)
        facts = b.setdefault("custom_facts", [])
        item = {
            "id": self._next_id("fact"),
            "kw1": kw1.strip(),
            "kw2": kw2.strip(),
            "is_true": bool(is_true),
            "note": note.strip(),
            "how": how.strip(),
            "created_at": time.time(),
        }
        facts.append(item)
        self._save()
        return item

    def update_custom_fact(self, child_id: str, item_id: str, *,
                           kw1: Optional[str] = None, kw2: Optional[str] = None,
                           is_true: Optional[bool] = None, note: Optional[str] = None,
                           how: Optional[str] = None) -> Optional[Dict[str, Any]]:
        b = self._bucket(child_id)
        for item in b.get("custom_facts", []):
            if item.get("id") == item_id:
                if kw1 is not None:
                    item["kw1"] = kw1.strip()
                if kw2 is not None:
                    item["kw2"] = kw2.strip()
                if is_true is not None:
                    item["is_true"] = bool(is_true)
                if note is not None:
                    item["note"] = note.strip()
                if how is not None:
                    item["how"] = how.strip()
                self._save()
                return item
        return None

    def delete_custom_fact(self, child_id: str, item_id: str) -> bool:
        b = self._bucket(child_id)
        facts = b.get("custom_facts", [])
        for i, item in enumerate(facts):
            if item.get("id") == item_id:
                facts.pop(i)
                self._save()
                return True
        return False

    # ── 费曼小老师：家长指定讲解主题库 CRUD ──
    def list_feynman_topics(self, child_id: str) -> List[Dict[str, Any]]:
        b = self._bucket(child_id)
        return list(b.get("feynman_topics", []))

    def add_feynman_topic(self, child_id: str, topic: str, hint: str = "") -> Dict[str, Any]:
        b = self._bucket(child_id)
        topics = b.setdefault("feynman_topics", [])
        item = {
            "id": self._next_id("feyn"),
            "topic": topic.strip(),
            "hint": hint.strip(),
            "created_at": time.time(),
        }
        topics.append(item)
        self._save()
        return item

    def update_feynman_topic(self, child_id: str, topic_id: str, *,
                             topic: Optional[str] = None, hint: Optional[str] = None) -> Optional[Dict[str, Any]]:
        b = self._bucket(child_id)
        for item in b.get("feynman_topics", []):
            if item.get("id") == topic_id:
                if topic is not None:
                    item["topic"] = topic.strip()
                if hint is not None:
                    item["hint"] = hint.strip()
                self._save()
                return item
        return None

    def delete_feynman_topic(self, child_id: str, topic_id: str) -> bool:
        b = self._bucket(child_id)
        topics = b.get("feynman_topics", [])
        for i, item in enumerate(topics):
            if item.get("id") == topic_id:
                topics.pop(i)
                self._save()
                return True
        return False

    # ── 诗词飞花令：家长自定义令字对仗句 CRUD ──
    def list_custom_feihua(self, child_id: str) -> Dict[str, List[str]]:
        b = self._bucket(child_id)
        return dict(b.get("custom_feihua", {}))

    def add_custom_feihua_verse(self, child_id: str, keyword: str, verse: str) -> Dict[str, Any]:
        b = self._bucket(child_id)
        db = b.setdefault("custom_feihua", {})
        kw = keyword.strip()
        v_list = db.setdefault(kw, [])
        v_clean = verse.strip()
        if v_clean and v_clean not in v_list:
            v_list.append(v_clean)
        self._save()
        return {"keyword": kw, "verses": v_list}

    def delete_custom_feihua_verse(self, child_id: str, keyword: str, verse: str) -> bool:
        b = self._bucket(child_id)
        db = b.get("custom_feihua", {})
        kw = keyword.strip()
        v_list = db.get(kw, [])
        v_clean = verse.strip()
        if v_clean in v_list:
            v_list.remove(v_clean)
            if not v_list:
                db.pop(kw, None)
            self._save()
            return True
        return False


# ── 小学新鲜事 (News, Competitions, Exhibitions & Activities) ──

_DISTRICT_CITY_MAP = {
    # 北京
    "海淀": "北京", "朝阳": "北京", "西城": "北京", "东城": "北京", "丰台": "北京",
    "石景山": "北京", "通州": "北京", "昌平": "北京", "大兴": "北京", "顺义": "北京",
    # 上海
    "浦东": "上海", "黄浦": "上海", "徐汇": "上海", "长宁": "上海", "静安": "上海",
    "普陀": "上海", "虹口": "上海", "杨浦": "上海", "闵行": "上海", "宝山": "上海",
    # 广东
    "广州": "广东", "深圳": "广东", "天河": "广东", "越秀": "广东", "海珠": "广东",
    "白云": "广东", "番禺": "广东", "南山": "广东", "福田": "广东", "宝安": "广东",
    # 江苏
    "南京": "江苏", "苏州": "江苏", "无锡": "江苏", "常州": "江苏", "鼓楼": "江苏",
    # 浙江
    "杭州": "浙江", "宁波": "浙江", "温州": "浙江", "西湖": "浙江", "余杭": "浙江",
    # 四川
    "成都": "四川", "锦江": "四川", "青羊": "四川", "武侯": "四川", "高新": "四川",
}


def normalize_region_tokens(r: str) -> List[str]:
    tokens = []
    r_clean = r.replace("特别行政区", " ").replace("自治区", " ").replace("省", " ").replace("市", " ").replace("区", " ").replace("县", " ")
    for part in r_clean.split():
        part = part.strip()
        if len(part) >= 2:
            tokens.append(part)
    return tokens


def is_news_region_relevant(item: Dict[str, Any], user_region: str) -> bool:
    """判断报道是否与设定的地区相关。
    - 全国级/通用/无地区限制的消息对所有地区均具有相关性；
    - 地方专属消息（如北京专属古诗赛）与对应地区及上辖/下辖区县相关，与其它城市无关。
    """
    if not user_region or user_region.strip() in ("", "全国", "不限", "全部"):
        return True
    
    u_reg = user_region.strip()
    item_reg = (item.get("region") or "全国").strip()
    if item_reg in ("全国", "全国/线上", "通用", "教育部", ""):
        return True

    u_tokens = normalize_region_tokens(u_reg)
    i_tokens = normalize_region_tokens(item_reg)

    # 扩展映射（区县扩展到地级市/省份）
    for t in list(u_tokens):
        if t in _DISTRICT_CITY_MAP:
            u_tokens.append(_DISTRICT_CITY_MAP[t])
    for t in list(i_tokens):
        if t in _DISTRICT_CITY_MAP:
            i_tokens.append(_DISTRICT_CITY_MAP[t])

    # 包含或词根交叉比对
    for ut in u_tokens:
        if ut in item_reg or ut in item.get("title", "") or ut in item.get("location", ""):
            return True
        for it in i_tokens:
            if it in u_reg or ut in it or it in ut:
                return True
    return False


DEFAULT_NEWS_SOURCES = [
    {"id": "src_cast", "name": "中国科协青少年科技中心", "category": "赛事/科普", "region": "全国", "url": "https://www.cast.org.cn"},
    {"id": "src_cstm", "name": "中国数字科技馆", "category": "展会/研学", "region": "全国", "url": "https://www.cstm.org.cn"},
    {"id": "src_moe", "name": "教育部少儿阳光体美平台", "category": "政策/资讯", "region": "全国", "url": "http://www.moe.gov.cn"},
    {"id": "src_castic", "name": "全国青少年科技创新大赛组委会", "category": "少儿科创", "region": "全国", "url": "https://castic.org.cn"},
    {"id": "src_bj_edu", "name": "北京市教育学会研学博览中心", "category": "展讯/赛事", "region": "北京市", "url": "http://www.bje.gov.cn"},
    {"id": "src_sh_scitech", "name": "上海市青少年科学社", "category": "地方展会", "region": "上海市", "url": "http://www.shs.org.cn"},
]

DEFAULT_NEWS_ITEMS = [
    {
        "id": "news_comp_ai_2026",
        "title": "2026全国少儿人工智能与创意编程挑战赛",
        "category": "赛事",
        "region": "全国",
        "source": "中国科协青少年科技中心",
        "summary": "面向小学3-6年级，设图形化编程、趣味算法与智能硬件造物三大赛道，免报名费。",
        "points": [
            "· 赛道设项：Scratch图形化编程、Python算法闯关、开源硬件造物三大赛道。",
            "· 参赛对象：全国小学3-6年级，免收报名费，全程线上提交作品初选。",
            "· 奖励荣誉：优秀作品受邀参加暑期全国科技嘉年华现场展示交流！",
        ],
        "content": "本次大赛由中国科协青少年科技中心指导，分为Scratch图形化编程设计、Python少儿算法趣味闯关以及智能硬件开源造物三大赛道。作品提交截止5月底，全程线上初选，优秀作品将受邀参加暑期全国科技嘉年华现场展示。旨在引导小学生通过动手编程解决日常生活中的小问题，锻炼逻辑与创造力。",
        "url": "https://castic.org.cn/ai2026",
        "days_ago": 1,
        "ts": 1792980000.0,
    },
    {
        "id": "news_exhib_cstm_moon",
        "title": "中国科技馆“深空探月与未来火星基地”沉浸研学特展",
        "category": "展会",
        "region": "北京市",
        "source": "中国科技馆",
        "summary": "1:1还原月球车探测场景与火星基地，小学生可体验低重力漫步与陨石切片显微镜观察。",
        "points": [
            "· 沉浸体验：1:1还原月球车与火星基地，体验低重力漫步与显微镜观察陨石。",
            "· 互动工坊：专设少年宇航员互动区，亲手操作机械臂抓取模拟月壤标本。",
            "· 观展贴士：科技馆一层东大厅展至暑假，持小学生证半价，周末有专业导览。",
        ],
        "content": "特展位于中国科技馆一层东大厅，持续展出至暑假。展区专设“少年宇航员”互动探索工坊，小学生可亲手操作机械臂抓取月壤模拟标本，通过VR模拟长征火箭发射指令台。持小学生证享半价研学票，周末特设专业少儿科普辅导员深度导览讲解。",
        "url": "https://www.cstm.org.cn/moon_exhib",
        "days_ago": 2,
        "ts": 1792981000.0,
    },
    {
        "id": "news_comp_castic_40",
        "title": "第40届全国青少年科技创新大赛申报全面启动",
        "category": "赛事",
        "region": "全国",
        "source": "全国青科创大赛组委会",
        "summary": "历史悠久的少儿科创盛会，设小学组“小发明、小论文、小设想”三小项目，鼓励生活探究。",
        "points": [
            "· 赛项重点：小学组设“小发明、小论文、小设想”，重点鼓励生活痛点探究。",
            "· 规则要求：严禁成人代做，强调孩子的真实探究过程与实验日记记录。",
            "· 申报指南：各省市基层小学选拔已全面启动，入选获国家级科创荣誉证书。",
        ],
        "content": "本届大赛重点鼓励小学生从身边的生活痛点出发，开展观察实验、绿色环保发明与生活便利小制作。严禁成人代做，强调孩子的真实探究过程与实验日记记录。各省市基层小学选拔现已全面展开，入选项目将获得国家级科创荣誉证书。",
        "url": "https://castic.org.cn/40th",
        "days_ago": 3,
        "ts": 1792982000.0,
    },
    {
        "id": "news_event_bj_poetry",
        "title": "北京市中小学“少年英才”古诗文素养风采展示",
        "category": "活动",
        "region": "北京市",
        "source": "北京市教育学会",
        "summary": "涵盖部编教材必背古诗与传统国学常识，设诗词接龙、飞花令擂台与古诗情境诵读朗诵赛。",
        "points": [
            "· 展示内容：部编教材必背古诗与传统国学常识，设接龙、飞花令与朗诵赛。",
            "· 报名对象：北京各区小学4-6年级学生均可在学校统一报名参赛。",
            "· 决赛盛会：决赛将举办紫禁城诗词雅会，优胜者将荣获“诗香少年”称号。",
        ],
        "content": "活动由北京市教育学会主办，全市各区小学4-6年级学生均可在学校报名。初选通过趣味古诗互动问答进行，决赛将组织“紫禁城诗词雅会”，鼓励孩子们在诵读经典中感悟传统中华文化意境，优秀者将获得“诗香少年”称号。",
        "url": "http://www.bje.gov.cn/poetry",
        "days_ago": 4,
        "ts": 1792983000.0,
    },
    {
        "id": "news_exhib_sh_expo",
        "title": "上海市青少年科技博览会少儿科创发明展",
        "category": "展会",
        "region": "上海市",
        "source": "上海市科协·少儿活动中心",
        "summary": "汇集全上海小学生脑洞大开的发明：自动避障车、防近视智能台灯、智能分类垃圾桶实物展出。",
        "points": [
            "· 展出亮点：汇集全上海小学生发明：自动避障车、防近视台灯、分类垃圾桶。",
            "· 动手体验：现场设创客工作坊，可跟随辅导老师组装感应小台灯并交流心得。",
            "· 入场指南：世博展览馆举办，小学生持免预约学生证直接入场参观。",
        ],
        "content": "本次博览会在上海世博展览馆举办，特设少儿创客互动体验区，小学生凭免预约学生证入场。现场设有开源硬件动手工作坊，孩子们可以跟着辅导老师现场组装感应小台灯，并与同龄的小发明家交流制作心得。",
        "url": "http://www.shs.org.cn/expo",
        "days_ago": 5,
        "ts": 1792984000.0,
    },
    {
        "id": "news_comp_gba_robot",
        "title": "大湾区中小学生智能机器人与无人机挑战赛",
        "category": "赛事",
        "region": "广东省",
        "source": "广东省青少年科技教育协会",
        "summary": "设轮式机器人迷宫避障、无人机穿越障碍环与团队协作对抗赛，锻炼工程实践能力。",
        "points": [
            "· 竞赛项目：轮式机器人迷宫避障、无人机穿越障碍环与团队协作对抗赛。",
            "· 参赛组别：广州大学城体育中心举行，设初级(1-3年级)与高级(4-6年级)组。",
            "· 奖励机制：强调开源软硬件现场即时调试，优胜者入选中小学生科创夏令营。",
        ],
        "content": "比赛在广州大学城体育中心举行，设小学初级组（1-3年级）与小学高级组（4-6年级）。参赛队伍需使用开源硬件与传感器自主搭建并编写行进控制程序，强调即时调试能力。优胜选手将优先获得大湾区科创夏令营交流名额。",
        "url": "http://www.gdscitech.org.cn/robot",
        "days_ago": 6,
        "ts": 1792985000.0,
    },
    {
        "id": "news_event_sh_dino",
        "title": "上海自然博物馆“恐龙重聚与远古生物大探秘”亲子研学",
        "category": "展会",
        "region": "上海市",
        "source": "上海自然博物馆",
        "summary": "馆长揭秘“会动的霸王龙与马门溪龙”化石修复，现场体验恐龙骨骼石膏拓印与古生物挖掘。",
        "points": [
            "· 研学体验：馆长揭秘马门溪龙骨架组装，戴专用手套与刮刀亲身体验模拟挖掘。",
            "· 亲子课堂：每周六上午特设小学生专属科普课，体验恐龙骨骼石膏拓印。",
            "· 启蒙价值：沉浸式观察远古生物标本，培养对地球演化与生命科学的浓厚兴趣。",
        ],
        "content": "每周六上午特设小学亲子专属科普课，古生物化石修复专家现场讲解马门溪龙骨架的组装历程，并向小学生分发化石修复手套与专用刮刀，亲身体验模拟地层挖掘化石标本，培养对地球演化与生命科学的浓厚兴趣。",
        "url": "http://www.snhm.org.cn/dino",
        "days_ago": 7,
        "ts": 1792986000.0,
    },
    {
        "id": "news_comp_js_science",
        "title": "江苏省少儿“小科学家”科学探究成果展",
        "category": "活动",
        "region": "江苏省",
        "source": "江苏省少工委",
        "summary": "展示全省小学生在水质检测、植物生长对比、天象观测等自然科学研究中的优秀探究日记。",
        "points": [
            "· 成果展览：南京妇女儿童活动中心展出300余份小学生独立完成的探究报告。",
            "· 探究主题：涵盖水质检测、植物生长对比、天象观测等自然科学研究日记。",
            "· 流动科普：现场可参与酸碱中和魔术水、静电发生器头发飞扬等趣味实验。",
        ],
        "content": "展览在南京市妇女儿童活动中心举行，展出300余份小学生独立完成的科学探究报告。配套开展“流动科学实验室”科普互动，小学生可现场参与酸碱中和魔术水实验、静电发生器头发飞扬体验等趣味物理实验。",
        "url": "http://www.jssgw.org.cn/science",
        "days_ago": 8,
        "ts": 1792987000.0,
    },
    {
        "id": "news_report_eyes_15m",
        "title": "教育部发布“健康护眼与课间15分钟”全国活力校园新指引",
        "category": "报道",
        "region": "全国",
        "source": "教育部新闻办",
        "summary": "倡导中小学校保障课间走出教室，鼓励跳绳、踢毽子、远眺，每天户外活动不少于2小时。",
        "points": [
            "· 核心要求：保障中小学生课间走出教室，严禁以任何理由拖堂或限制自由活动。",
            "· 阳光运动：推广课间护眼操与室外趣味球类，鼓励每天户外活动不少于2小时。",
            "· 家校协同：引导孩子放下电子屏幕，在大自然与阳光中健康茁壮成长。",
        ],
        "content": "新指引明确要求全国中小学全面落实大课间与课间走出教室活动，严禁以任何理由拖堂或限制学生课间自由活动。推广“阳光课间护眼操”和室外趣味球类运动，鼓励家校协同，引导孩子放下电子屏幕，在大自然与阳光中健康成长。",
        "url": "http://www.moe.gov.cn/eyes_15m",
        "days_ago": 9,
        "ts": 1792988000.0,
    },
    {
        "id": "news_event_tiangong_lab",
        "title": "中国空间站“天宫课堂”少儿地面同步微重力实验启动征集",
        "category": "活动",
        "region": "全国",
        "source": "中国载人航天工程网",
        "summary": "邀请全国小学生设计微重力对比实验方案，优秀创意将由航天员在空间站天和舱内演示！",
        "points": [
            "· 征集主题：面向小学生征集“微重力对比”实验（如水滴表面张力、陀螺、种子）。",
            "· 申报方式：只需提交一页实验设想简报或视频说明，全线上便捷申报。",
            "· 航天大奖：优秀方案由航天员在天和舱演示，入围者获天地连线实时互动答疑！",
        ],
        "content": "活动面向全国在校小学生征集“太空与地面奇妙物理差异”实验设想，例如水滴表面张力、太空陀螺进动、种子太空发芽等。小学生只需提交一页实验设想简报或视频说明，入围同学还将获得与英雄航天员天地连线互动答疑的难得机会！",
        "url": "http://www.cmse.gov.cn/tiangong",
        "days_ago": 10,
        "ts": 1792989000.0,
    },
]


NEWS_TIME_WINDOW_DAYS = 30
NEWS_TIME_WINDOW_SECONDS = NEWS_TIME_WINDOW_DAYS * 86400  # 30 天严格滑动窗口（秒）


class NotesStoreNewsMixin:
    """小学新鲜事存取与筛选方法混入。"""

    def get_news_config(self, child_id: str) -> Dict[str, Any]:
        """获取新鲜事配置（地区、白名单关键词、黑名单关键词）。"""
        bucket = self._bucket(child_id)
        cfg = bucket.get("news_config") or {}
        return {
            "region": cfg.get("region", "全国"),
            "whitelist": list(cfg.get("whitelist", [])),
            "blacklist": list(cfg.get("blacklist", [])),
        }

    def get_news_region(self, child_id: str) -> str:
        """获取当前设定的地区。"""
        cfg = self.get_news_config(child_id)
        return cfg.get("region", "全国")

    def set_news_config(self, child_id: str,
                        region: Optional[str] = None,
                        whitelist: Optional[List[str]] = None,
                        blacklist: Optional[List[str]] = None) -> Dict[str, Any]:
        """设置新鲜事配置并持久化。"""
        bucket = self._bucket(child_id)
        current = self.get_news_config(child_id)
        if region is not None:
            current["region"] = region.strip() or "全国"
        if whitelist is not None:
            current["whitelist"] = [w.strip() for w in whitelist if w and w.strip()]
        if blacklist is not None:
            current["blacklist"] = [b.strip() for b in blacklist if b and b.strip()]
        bucket["news_config"] = current
        self._save()
        return current

    def list_news_sources(self, child_id: str) -> List[Dict[str, Any]]:
        """获取当前配置的所有信源（预设 + 家长添加）。"""
        bucket = self._bucket(child_id)
        custom_sources = bucket.get("news_sources", [])
        return list(DEFAULT_NEWS_SOURCES) + list(custom_sources)

    def add_news_source(self, child_id: str, name: str, url: str = "",
                        category: str = "综合", region: str = "全国") -> Dict[str, Any]:
        """家长端增加信源。"""
        bucket = self._bucket(child_id)
        sources = bucket.setdefault("news_sources", [])
        src = {
            "id": f"src_custom_{int(time.time())}_{len(sources)+1}",
            "name": name.strip(),
            "url": url.strip(),
            "category": category.strip() or "综合",
            "region": region.strip() or "全国",
            "added_at": time.time(),
        }
        sources.append(src)
        self._save()
        return src

    def delete_news_source(self, child_id: str, source_id: str) -> bool:
        """家长端删除自定义信源。"""
        bucket = self._bucket(child_id)
        sources = bucket.get("news_sources", [])
        before_len = len(sources)
        sources = [s for s in sources if s.get("id") != source_id]
        bucket["news_sources"] = sources
        self._save()
        return len(sources) < before_len

    def delete_news_item(self, child_id: str, item_id: str) -> bool:
        """家长端删除/屏蔽某个报道。"""
        bucket = self._bucket(child_id)
        deleted = bucket.setdefault("news_deleted_ids", [])
        if item_id not in deleted:
            deleted.append(item_id)
            self._save()
        return True

    def restore_news_item(self, child_id: str, item_id: str) -> bool:
        """家长端取消屏蔽某个报道。"""
        bucket = self._bucket(child_id)
        deleted = bucket.get("news_deleted_ids", [])
        if item_id in deleted:
            deleted.remove(item_id)
            self._save()
            return True
        return False

    def add_news_item(self, child_id: str, title: str, summary: str,
                      content: str = "", category: str = "活动",
                      region: str = "全国", source: str = "家长录入",
                      url: str = "", date_str: str = "",
                      ts: Optional[float] = None) -> Dict[str, Any]:
        """家长端手动录入新报道/比赛/展会。"""
        now = time.time() if ts is None else ts
        if not date_str:
            date_str = time.strftime("%m-%d", time.localtime(now))
        bucket = self._bucket(child_id)
        custom_items = bucket.setdefault("news_custom_items", [])
        item = {
            "id": f"news_custom_{int(now)}_{len(custom_items)+1}",
            "title": title.strip(),
            "summary": summary.strip(),
            "content": content.strip() or summary.strip(),
            "category": category.strip() or "活动",
            "region": region.strip() or "全国",
            "source": source.strip() or "家长录入",
            "url": url.strip(),
            "date": date_str,
            "is_custom": True,
            "ts": now,
        }
        custom_items.insert(0, item)
        self._save()
        return item

    def update_news_item(self, child_id: str, item_id: str, *,
                         title: Optional[str] = None, summary: Optional[str] = None,
                         content: Optional[str] = None, category: Optional[str] = None,
                         region: Optional[str] = None, date_str: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """修改家长录入的或指定的小学新鲜事资讯。"""
        bucket = self._bucket(child_id)
        custom_items = bucket.get("news_custom_items", [])
        for item in custom_items:
            if item.get("id") == item_id:
                if title is not None:
                    item["title"] = title.strip()
                if summary is not None:
                    item["summary"] = summary.strip()
                if content is not None:
                    item["content"] = content.strip()
                if category is not None:
                    item["category"] = category.strip()
                if region is not None:
                    item["region"] = region.strip()
                if date_str is not None:
                    item["date"] = date_str.strip()
                self._save()
                return item
        return None

    def check_and_update_daily_news(self, child_id: str, now: Optional[float] = None) -> Dict[str, Any]:
        """每日自动滚动维护小学新鲜事：
        1. 检查今日是否已完成日常维护；
        2. 扫描并自动下架/归档超过 30 天（一个月）的过期资讯；
        3. 滚动更新 30 天滑动窗口状态。
        """
        if now is None:
            now = time.time()
        today_key = time.strftime("%Y-%m-%d", time.localtime(now))
        cutoff_ts = now - NEWS_TIME_WINDOW_SECONDS

        bucket = self._bucket(child_id)
        last_update = bucket.get("news_last_daily_update")
        custom_items = bucket.get("news_custom_items", [])

        # 检查过期自定义资讯
        active_custom = []
        expired_custom = []
        for it in custom_items:
            if it.get("ts", now) < cutoff_ts:
                expired_custom.append(it)
            else:
                active_custom.append(it)

        # 自动归档过期自定义项
        if expired_custom:
            archived = bucket.setdefault("news_archived_items", [])
            archived.extend(expired_custom)
            bucket["news_custom_items"] = active_custom

        bucket["news_last_daily_update"] = today_key
        self._save()

        active_total = len(self.list_news(child_id, apply_filter=True, now=now))
        return {
            "today": today_key,
            "last_update": last_update,
            "updated": last_update != today_key,
            "purged_count": len(expired_custom),
            "active_news_count": active_total,
            "time_window_days": NEWS_TIME_WINDOW_DAYS,
        }

    def list_news(self, child_id: str, apply_filter: bool = True,
                  now: Optional[float] = None) -> List[Dict[str, Any]]:
        """获取报道列表（结合过去一个月/30天时效过滤、地区相关性过滤、黑名单过滤、白名单加权与家长删除）。"""
        if now is None:
            now = time.time()
        cutoff_ts = now - NEWS_TIME_WINDOW_SECONDS

        bucket = self._bucket(child_id)
        cfg = self.get_news_config(child_id)
        user_region = cfg.get("region", "全国")
        whitelist = cfg.get("whitelist", [])
        blacklist = cfg.get("blacklist", [])
        deleted_ids = set(bucket.get("news_deleted_ids", []))
        custom_items = bucket.get("news_custom_items", [])

        # 预设库中根据 days_ago 动态对齐时间戳至最近 1~10 天（严格保证在 30 天滑动窗口内）
        aligned_defaults = []
        for it in DEFAULT_NEWS_ITEMS:
            c = dict(it)
            c['is_example'] = True
            days_ago = c.get("days_ago", 1)
            c["ts"] = now - days_ago * 86400
            c["date"] = time.strftime("%m-%d", time.localtime(c["ts"]))
            aligned_defaults.append(c)

        # 格式化 custom_items 的 date
        aligned_customs = []
        for it in custom_items:
            c = dict(it)
            item_ts = c.get("ts", now)
            if "date" not in c:
                c["date"] = time.strftime("%m-%d", time.localtime(item_ts))
            aligned_customs.append(c)

        all_items: List[Dict[str, Any]] = aligned_customs + aligned_defaults

        if not apply_filter:
            # 完整原始清单（标注删除状态、过期状态与过滤状态，供家长端后台管理）
            result = []
            for it in all_items:
                item_copy = dict(it)
                item_ts = it.get("ts", now)
                item_copy["expired"] = item_ts < cutoff_ts
                item_copy["age_days"] = max(0, int((now - item_ts) / 86400))
                item_copy["deleted"] = it["id"] in deleted_ids
                item_copy["region_relevant"] = is_news_region_relevant(it, user_region)
                text = f"{it.get('title','')} {it.get('summary','')} {it.get('content','')}"
                item_copy["is_blacklisted"] = any(b in text for b in blacklist if b)
                item_copy["is_whitelisted"] = any(w in text for w in whitelist if w)
                result.append(item_copy)
            return result

        filtered: List[Dict[str, Any]] = []
        for it in all_items:
            # 1. 严格 30 天（一个月）时效过滤：超过 30 天的历史资讯自动过期过滤下架
            item_ts = it.get("ts", now)
            if item_ts < cutoff_ts:
                continue

            # 2. 过滤已删除项
            if it["id"] in deleted_ids:
                continue

            # 3. 黑名单过滤（标题、简介、正文含有黑名单关键词的一律阻断）
            full_text = f"{it.get('title','')} {it.get('summary','')} {it.get('content','')}"
            if any(b in full_text for b in blacklist if b):
                continue

            # 4. 地区相关性判断（仅在有地区相关性时才展示）
            if not is_news_region_relevant(it, user_region):
                continue

            item_copy = dict(it)
            # 5. 白名单加权标记
            item_copy["whitelisted"] = any(w in full_text for w in whitelist if w)
            filtered.append(item_copy)

        # 排序：白名单优先置顶 > 家长自定义条目 > 按时间戳倒序
        filtered.sort(key=lambda x: (1 if x.get("whitelisted") else 0, 1 if x.get("is_custom") else 0, x.get("ts", 0)), reverse=True)
        return filtered

    def get_news_item(self, child_id: str, item_id: str) -> Optional[Dict[str, Any]]:
        """获取单篇报道详情。"""
        for it in self.list_news(child_id, apply_filter=False):
            if it.get("id") == item_id:
                return it
        return None


# 让 NotesStore 继承或合并小学新鲜事能力
for _attr_name in dir(NotesStoreNewsMixin):
    if not _attr_name.startswith("__"):
        setattr(NotesStore, _attr_name, getattr(NotesStoreNewsMixin, _attr_name))


