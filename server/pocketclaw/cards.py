"""随机卡片轮播内容库（古诗 / 现代诗 / 名言）。

版权红线：只收**公有领域**内容——唐诗宋词、作者已故 70 年以上的现代诗（如徐志摩，1931 卒）、
以及广为流传的短名言（附出处）。英文名言配中文译。世界名画（图片）走单独的抖动图片链路，不在此文件。
"""
from __future__ import annotations

import pathlib
from typing import Dict, List

_IMG_DIR = pathlib.Path(__file__).parent / "card_images"

# 世界名画（公有领域，已制作成 400×300 4bpp 16灰阶墨水屏位图，存 card_images/*.bin）
_PAINTINGS = [
    ("starry_night", "星月夜", "梵高"),
    ("ma_yuan_spring", "山径春行图(局部)", "马远"),
    ("great_wave", "神奈川冲浪里", "葛饰北斋"),
    ("fuchun_mountain", "富春山居图(局部)", "黄公望"),
    ("creation_adam", "创造亚当(局部)", "米开朗基罗"),
    ("red_fuji", "凯风快晴·赤富士", "葛饰北斋"),
    ("praying_hands", "祈祷之手(局部)", "丢勒"),
    ("mona_lisa", "蒙娜丽莎(局部)", "达·芬奇"),
    ("pocketclaw_workshop", "伴学小龙虾工坊", "原创插画"),
    ("astronaut_moon", "月球伴读·星际漫步", "原创版画"),
    ("shanshui_pine", "国风水墨·松岩孤亭", "原创山水"),
    ("study_kitten", "书桌暖阳·伴读小猫", "原创素描"),
    ("steampunk_airship", "云海飞艇·天空探险", "原创铜版"),
]


def painting_bitmap(name: str) -> bytes:
    p = _IMG_DIR / f"{name}.bin"
    return p.read_bytes() if p.exists() else b""

# 古诗（唐/宋，公有领域）
_POEMS_CLASSIC = [
    ("静夜思", "李白", ["床前明月光，", "疑是地上霜。", "举头望明月，", "低头思故乡。"]),
    ("春晓", "孟浩然", ["春眠不觉晓，", "处处闻啼鸟。", "夜来风雨声，", "花落知多少。"]),
    ("登鹳雀楼", "王之涣", ["白日依山尽，", "黄河入海流。", "欲穷千里目，", "更上一层楼。"]),
    ("悯农", "李绅", ["锄禾日当午，", "汗滴禾下土。", "谁知盘中餐，", "粒粒皆辛苦。"]),
    ("江雪", "柳宗元", ["千山鸟飞绝，", "万径人踪灭。", "孤舟蓑笠翁，", "独钓寒江雪。"]),
    ("咏鹅", "骆宾王", ["鹅，鹅，鹅，", "曲项向天歌。", "白毛浮绿水，", "红掌拨清波。"]),
    ("相思", "王维", ["红豆生南国，", "春来发几枝。", "愿君多采撷，", "此物最相思。"]),
    ("绝句", "杜甫", ["两个黄鹂鸣翠柳，", "一行白鹭上青天。", "窗含西岭千秋雪，", "门泊东吴万里船。"]),
    ("望庐山瀑布", "李白", ["日照香炉生紫烟，", "遥看瀑布挂前川。", "飞流直下三千尺，", "疑是银河落九天。"]),
    ("早发白帝城", "李白", ["朝辞白帝彩云间，", "千里江陵一日还。", "两岸猿声啼不住，", "轻舟已过万重山。"]),
]

# 现代诗（作者已故 70 年以上，公有领域——徐志摩 1931 卒）
_POEMS_MODERN = [
    ("偶然", "徐志摩", ["我是天空里的一片云，", "偶尔投影在你的波心——", "你不必讶异，", "更无须欢喜——", "在转瞬间消灭了踪影。"]),
    ("再别康桥（节选）", "徐志摩", ["轻轻的我走了，", "正如我轻轻的来；", "我轻轻的招手，", "作别西天的云彩。"]),
]

# 名言：中文名言只给中文；英文名言给「英文 + 中文译」。均短、附出处，公有领域/广为流传
_QUOTES = [
    {"lines": ["学而不思则罔，", "思而不学则殆。"], "en": "", "cn": "", "author": "孔子"},
    {"lines": ["千里之行，", "始于足下。"], "en": "", "cn": "", "author": "老子"},
    {"lines": ["业精于勤，", "荒于嬉。"], "en": "", "cn": "", "author": "韩愈"},
    {"lines": [], "en": "Imagination is more important than knowledge.", "cn": "想象力比知识更重要。", "author": "爱因斯坦"},
    {"lines": [], "en": "The only true wisdom is in knowing you know nothing.", "cn": "真正的智慧，是知道自己一无所知。", "author": "苏格拉底"},
    {"lines": [], "en": "Keep your face to the sunshine.", "cn": "面朝阳光，就看不到阴影。", "author": "海伦·凯勒"},
    {"lines": [], "en": "Stay hungry, stay foolish.", "cn": "求知若饥，虚心若愚。", "author": "乔布斯"},
]


def build_deck() -> List[Dict]:
    """返回一副卡片（文字卡）。每张 = {type, title, lines, footer}。设备端缓存后本地轮播。"""
    deck: List[Dict] = []
    for title, author, lines in _POEMS_CLASSIC:
        deck.append({"type": "poem", "title": title, "lines": lines, "footer": "—— " + author})
    for title, author, lines in _POEMS_MODERN:
        deck.append({"type": "poem", "title": title, "lines": lines, "footer": "—— " + author})
    for q in _QUOTES:
        if q["en"]:
            lines = [q["en"], q["cn"]]
        else:
            lines = list(q["lines"])
        deck.append({"type": "quote", "title": "名言", "lines": lines, "footer": "—— " + q["author"]})
    for name, title, author in _PAINTINGS:          # 世界名画（位图另行流式推送）
        if (_IMG_DIR / f"{name}.bin").exists():
            deck.append({"type": "painting", "title": title, "lines": [],
                         "footer": "—— " + author, "image": name})
    return deck
