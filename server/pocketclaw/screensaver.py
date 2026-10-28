"""PocketClaw 桌面伴学屏保模式（Screensaver / Standby Mode）
支持多种屏保类型切换与家长端管理：
  1. 名人语录 (quote): 中外名人格言、治学勉励、启迪智慧
  2. 经典古诗词 (poem): 课标传世经典、意境鉴赏、书法排版
  3. 世界名画 (art): 艺术启蒙、中外传世巨作、时代流派与鉴赏导读
  4. 重要日期倒计时 (countdown): 期末考/升级考/生日/寒暑假天数倒计时
  5. 桌面课程表 (timetable): 当日/明日课程安排与收拾书包备忘
  6. 混合轮播模式 (cycle): 各类屏保卡片自动/手动循环切换
"""
from __future__ import annotations

import datetime
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class CountdownEvent:
    id: str
    title: str
    target_date: str  # "YYYY-MM-DD"
    slogan: str = "每一天的积累，都在为梦想添砖加瓦！"

    @property
    def days_left(self) -> int:
        try:
            target = datetime.datetime.strptime(self.target_date, "%Y-%m-%d").date()
            today = datetime.date.today()
            return (target - today).days
        except Exception:
            return 0


# ── 1. 名人语录库 ──
QUOTES_DB: List[Dict[str, str]] = [
    {
        "quote": "书山有路勤为径，学海无涯苦作舟。",
        "author": "韩愈",
        "source": "《古今贤文》",
        "motto": "勉励珍惜少年光阴，笃学不倦。"
    },
    {
        "quote": "千里之行，始于足下。不积跬步，无以至千里。",
        "author": "荀子",
        "source": "《荀子·劝学》",
        "motto": "伟大的成绩源于每天点滴积累。"
    },
    {
        "quote": "天行健，君子以自强不息；地势坤，君子以厚德载物。",
        "author": "孔子",
        "source": "《周易》",
        "motto": "刚毅坚韧，胸怀博大宽厚。"
    },
    {
        "quote": "哪里有天才，我只是把别人喝咖啡的时间都用在工作上。",
        "author": "鲁迅",
        "source": "《鲁迅全集》",
        "motto": "专注与惜时是成功的真正秘诀。"
    },
    {
        "quote": "想象力比知识更重要，因为知识有限，而想象力囊括整个宇宙。",
        "author": "阿尔伯特·爱因斯坦",
        "source": "《爱因斯坦谈科学》",
        "motto": "敢于探索与提问，拥抱好奇心。"
    },
    {
        "quote": "读书破万卷，下笔如有神。",
        "author": "杜甫",
        "source": "《奉赠韦左丞丈二十二韵》",
        "motto": "广泛阅读是优美写作的坚实基石。"
    },
    {
        "quote": "好奇心是学者的第一美德。",
        "author": "居里夫人",
        "source": "《居里夫人传》",
        "motto": "保持对未知世界永远的热爱与探求。"
    },
    {
        "quote": "发奋识遍天下字，立志读尽人间书。",
        "author": "苏轼",
        "source": "苏轼书房联",
        "motto": "少年立鸿鹄之志，博览群书。"
    },
]

# ── 2. 经典古诗词库 ──
POEMS_DB: List[Dict[str, Any]] = [
    {
        "title": "登鹳雀楼",
        "dynasty": "唐",
        "poet": "王之涣",
        "lines": ["白日依山尽，黄河入海流。", "欲穷千里目，更上一层楼。"],
        "appreciation": "气势磅礴，寓意只有站得更高，才能望得更远。"
    },
    {
        "title": "望岳",
        "dynasty": "唐",
        "poet": "杜甫",
        "lines": ["岱宗夫如何？齐鲁青未了。", "造化钟神秀，阴阳割昏晓。", "会当凌绝顶，一览众山小。"],
        "appreciation": "泰山巍峨雄伟，尽显勇攀高峰、俯视万物的胸怀。"
    },
    {
        "title": "竹石",
        "dynasty": "清",
        "poet": "郑燮",
        "lines": ["咬定青山不放松，立根原在破岩中。", "千磨万击还坚劲，任尔东西南北风。"],
        "appreciation": "咏竹言志，展现坚韧不拔、刚正不阿的浩然之气。"
    },
    {
        "title": "长歌行",
        "dynasty": "汉",
        "poet": "汉乐府",
        "lines": ["青青园中葵，朝露待日晞。", "百川东到海，何时复西归？", "少壮不努力，老大徒伤悲！"],
        "appreciation": "劝诫少年珍惜韶华寸阴，莫等白头空悲切。"
    },
    {
        "title": "定风波",
        "dynasty": "宋",
        "poet": "苏轼",
        "lines": ["莫听穿林打叶声，何妨吟啸且徐行。", "竹杖芒鞋轻胜马，谁怕？一蓑烟雨任平生。"],
        "appreciation": "风雨之中漫步徐行，展现从容超脱的乐观豁达。"
    },
]

# ── 3. 世界名画艺术赏析库 ──
ART_DB: List[Dict[str, Any]] = [
    {
        "id": "starry_night",
        "title": "星月夜 (星空)",
        "artist": "文森特·梵高 (荷兰)",
        "year": "1889年",
        "style": "后印象主义",
        "appreciation": "画中狂放旋转的星云、璀璨炙热的弯月与苍翠挺拔的丝柏，用纯粹炽热的笔触表达了对浩瀚宇宙与生命的热烈向往。"
    },
    {
        "id": "ma_yuan_spring",
        "title": "山径春行图 (局部)",
        "artist": "马远 (南宋)",
        "year": "约1190年",
        "style": "宋代院体水墨",
        "appreciation": "南宋四大家马远传世名作，以“马一角”精妙构图与大片空灵留白，描绘春日高士捻须漫步山径、垂柳飞鸟相伴的诗意瞬间。"
    },
    {
        "id": "mona_lisa",
        "title": "蒙娜丽莎 (局部)",
        "artist": "达·芬奇 (意大利)",
        "year": "1503年",
        "style": "文艺复兴全盛期",
        "appreciation": "聚焦达·芬奇首创“晕涂法”描绘的眼眸与神秘微笑局部，光影过渡如烟似雾，展现了古典人文主义的永恒魅力。"
    },
    {
        "id": "creation_adam",
        "title": "创造亚当 (指尖局部)",
        "artist": "米开朗基罗 (意大利)",
        "year": "1512年",
        "style": "文艺复兴壁画巅峰",
        "appreciation": "西斯廷礼拜堂天顶画最震撼的局部特写，两只伸向彼此的手指即将触碰的刹那，凝聚着生命觉醒与智慧传递的永恒张力。"
    },
    {
        "id": "fuchun_mountain",
        "title": "富春山居图 (局部)",
        "artist": "黄公望 (元代)",
        "year": "1350年",
        "style": "水墨山水巅峰",
        "appreciation": "中国古代水墨山水画神作，以披麻皴与干湿浓淡兼备的笔墨描绘富春江秋景，峰峦旷朗，意境深邃超逸。"
    },
    {
        "id": "great_wave",
        "title": "神奈川冲浪里",
        "artist": "葛饰北斋 (日本)",
        "year": "1831年",
        "style": "浮世绘木版画",
        "appreciation": "惊涛巨浪如鹰爪般凌空卷起，浪尖下扁舟勇敢破浪前行，远方富士山静穆独立，动静对比充满强烈的视觉张力。"
    },
    {
        "id": "red_fuji",
        "title": "凯风快晴 (赤富士)",
        "artist": "葛饰北斋 (日本)",
        "year": "1831年",
        "style": "浮世绘富岳三十六景",
        "appreciation": "初秋清晨南风吹拂，高天之上鱼鳞白云层叠铺展，富士山积雪山脊与山麓苍翠松林交相辉映，线条简练而气势恢宏。"
    },
    {
        "id": "praying_hands",
        "title": "祈祷之手 (局部)",
        "artist": "阿尔布雷希特·丢勒 (德国)",
        "year": "1508年",
        "style": "文艺复兴大师素描",
        "appreciation": "欧洲文艺复兴白描巨匠丢勒以极尽精微的墨线与高光，刻画出双手合十的虔诚与温情，每一根线条都饱含感恩与力量。"
    },
    {
        "id": "mountain",
        "title": "崇山峻岭 (16灰阶水墨)",
        "artist": "自然写生 (官方实拍同款)",
        "year": "现代",
        "style": "16灰阶墨水屏微胶囊驱动",
        "appreciation": "厂家实拍同款4bpp十六级灰阶，微胶囊多次受控电场偏转，呈现如宣纸水墨般细腻的远山层峦与近景苍松亭台。"
    },
    {
        "id": "pocketclaw_workshop",
        "title": "伴学小龙虾工坊",
        "artist": "小龙虾科技 · 原创插画",
        "year": "现代",
        "style": "蒸汽朋克工坊版画",
        "appreciation": "蒸汽朋克机械小龙虾伴读助手伏案伏笔，齿轮旋转与暖光灯泡交织出专注静谧的科学伴读时光。"
    },
    {
        "id": "astronaut_moon",
        "title": "月球伴读·星际漫步",
        "artist": "深空探索 · 原创版画",
        "year": "现代",
        "style": "太空探索版画",
        "appreciation": "宇航员静坐月球环形山边沉浸阅读，远方蔚蓝地球在浩瀚星海中静静守护，点亮求知与探索的梦想。"
    },
    {
        "id": "shanshui_pine",
        "title": "国风水墨·松岩孤亭",
        "artist": "青绿水墨 · 原创山水",
        "year": "现代",
        "style": "当代新写意水墨",
        "appreciation": "苍松盘根绝壁，飞瀑流泉汇入云海，孤亭临空独立，十六级黑白浓淡写意传统东方山水风骨。"
    },
    {
        "id": "study_kitten",
        "title": "书桌暖阳·伴读小猫",
        "artist": "书斋暖阳 · 原创素描",
        "year": "现代",
        "style": "炭笔素描治愈系",
        "appreciation": "午后温暖阳光倾泻在实木书桌上，软萌小猫在书本上安睡，眼镜与绿植相伴，温润治愈学习时光。"
    },
    {
        "id": "steampunk_airship",
        "title": "云海飞艇·天空探险",
        "artist": "复古机械 · 原创铜版",
        "year": "现代",
        "style": "复古机械蚀刻铜版",
        "appreciation": "十九世纪蒸汽飞艇迎风远航，掠过层峦云海与浮空岛屿，铜质齿轮与螺旋桨激荡科学求知航程。"
    },
]

# ── 名画二进制位图读取与 Floyd-Steinberg 墨水屏误差扩散抖动算法 ──
import base64
import pathlib

_IMG_DIR = pathlib.Path(__file__).parent / "card_images"


def get_art_bitmap(art_id: str) -> bytes:
    """获取名画 400×300 1bpp(15000字节) 或 4bpp 16灰阶(60000字节) 墨水屏位图二进制数据。"""
    for ext in ("_4bpp.bin", ".bin"):
        p = _IMG_DIR / f"{art_id}{ext}"
        if p.exists():
            return p.read_bytes()
    p = _IMG_DIR / f"{art_id}.bin"
    if p.exists():
        return p.read_bytes()
    return b""


def floyd_steinberg_dither(gray_grid: List[List[float]], width: int = 400, height: int = 300) -> bytes:
    """墨水屏 Floyd-Steinberg 误差扩散抖动算法 (Floyd-Steinberg Error Diffusion Dithering)。

    将 0..255 灰度二维网格量化为 1bpp 单色位图 (1=黑, 0=白，行优先 MSB-first，共 15000 字节)。
    误差扩散权重:
      右侧 (x+1, y)     : 7/16
      左下 (x-1, y+1)   : 3/16
      下方 (x,   y+1)   : 5/16
      右下 (x+1, y+1)   : 1/16
    """
    buf = [[float(v) for v in row] for row in gray_grid]
    out = bytearray(height * ((width + 7) // 8))

    for y in range(height):
        for x in range(width):
            old_val = buf[y][x]
            new_val = 0.0 if old_val < 128.0 else 255.0
            err = old_val - new_val

            if x + 1 < width:
                buf[y][x + 1] += err * 7.0 / 16.0
            if y + 1 < height:
                if x - 1 >= 0:
                    buf[y + 1][x - 1] += err * 3.0 / 16.0
                buf[y + 1][x] += err * 5.0 / 16.0
                if x + 1 < width:
                    buf[y + 1][x + 1] += err * 1.0 / 16.0

            if new_val == 0.0:  # 黑色
                byte_idx = y * ((width + 7) // 8) + (x // 8)
                bit_idx = 7 - (x % 8)
                out[byte_idx] |= (1 << bit_idx)

    return bytes(out)

# ── 4. 默认重要日期倒计时 ──
DEFAULT_COUNTDOWNS: List[CountdownEvent] = [
    CountdownEvent(id="cd_term_final", title="期末大考", target_date="2026-12-25", slogan="沉着冷静，每一次温习都在铸就自信！"),
    CountdownEvent(id="cd_vacation", title="寒假放假", target_date="2027-01-15", slogan="认真做完学期冲刺，迎接快乐假期！"),
    CountdownEvent(id="cd_children", title="六一儿童节", target_date="2027-06-01", slogan="愿童心永驻，勇敢拥抱未知世界！"),
]


SCREENSAVER_MODES = [
    ("quote", "名人语录"),
    ("poem", "经典古诗"),
    ("art", "世界名画"),
    ("countdown", "考试倒计时"),
    ("timetable", "桌面课表"),
    ("cycle", "混合轮播"),
]


class ScreensaverSession:
    """墨水屏屏保模式控制器。"""

    def __init__(self, mode: str = "cycle", countdown_events: Optional[List[CountdownEvent]] = None,
                 today_schedule: Optional[Dict[str, Any]] = None):
        self.mode = mode if mode in [m[0] for m in SCREENSAVER_MODES] else "cycle"
        self.countdown_events = countdown_events or list(DEFAULT_COUNTDOWNS)
        self.today_schedule = today_schedule or {}
        # 各分类当前查看的卡片索引
        self._indices: Dict[str, int] = {
            "quote": 0,
            "poem": 0,
            "art": 0,
            "countdown": 0,
            "timetable": 0,
        }
        # 轮播模式内部轮换游标
        self._cycle_types = ["quote", "poem", "art", "countdown", "timetable"]
        self._cycle_idx = 0
        # 画作随机顺序表（每次进入画作打乱一次）
        self._art_order: List[int] = list(range(len(ART_DB)))
        self.shuffle_art()

    def shuffle_art(self, target_art_id: Optional[str] = None) -> None:
        """打乱画作顺序，保证每次进入都有新鲜感。"""
        indices = list(range(len(ART_DB)))
        random.shuffle(indices)
        if target_art_id:
            for pos, idx in enumerate(indices):
                if ART_DB[idx].get("id") == target_art_id:
                    indices.pop(pos)
                    indices.insert(0, idx)
                    break
        self._art_order = indices
        self._indices["art"] = 0

    @property
    def effective_type(self) -> str:
        """若为 cycle 混合轮播，返回当前选中的子分类；否则返回当前分类。"""
        if self.mode == "cycle":
            return self._cycle_types[self._cycle_idx % len(self._cycle_types)]
        return self.mode

    def step_type(self, delta: int = 1) -> str:
        """按 ▲/▼ 键切换屏保分类。"""
        types = [m[0] for m in SCREENSAVER_MODES]
        curr_idx = types.index(self.mode) if self.mode in types else 0
        new_mode = types[(curr_idx + delta) % len(types)]
        if new_mode == "art" and self.mode != "art":
            self.shuffle_art()
        self.mode = new_mode
        return self.mode

    def step_card(self, delta: int = 1) -> Dict[str, Any]:
        """按 ● 确认键刷新/切到该分类下的下一张卡片。"""
        if self.mode == "cycle":
            self._cycle_idx = (self._cycle_idx + delta) % len(self._cycle_types)
            return self.get_card()

        t = self.mode
        if t == "quote":
            self._indices[t] = (self._indices[t] + delta) % len(QUOTES_DB)
        elif t == "poem":
            self._indices[t] = (self._indices[t] + delta) % len(POEMS_DB)
        elif t == "art":
            if not getattr(self, "_art_order", None) or len(self._art_order) != len(ART_DB):
                self.shuffle_art()
            self._indices[t] = (self._indices[t] + delta) % len(self._art_order)
        elif t == "countdown":
            cnt = max(1, len(self.countdown_events))
            self._indices[t] = (self._indices[t] + delta) % cnt
        elif t == "timetable":
            self._indices[t] = 0

        return self.get_card()

    def get_card(self) -> Dict[str, Any]:
        """获取当前应渲染的屏保卡片数据。"""
        act_type = self.effective_type
        if act_type == "quote":
            idx = self._indices["quote"] % len(QUOTES_DB)
            item = QUOTES_DB[idx]
            return {
                "type": "quote",
                "type_name": "名人语录",
                "idx": idx,
                "total": len(QUOTES_DB),
                "title": f"📜 名人语录 · 每日修身 ({idx + 1}/{len(QUOTES_DB)})",
                "quote": item["quote"],
                "author": item["author"],
                "source": item.get("source", ""),
                "motto": item.get("motto", ""),
                "is_cycle": self.mode == "cycle",
            }
        elif act_type == "poem":
            idx = self._indices["poem"] % len(POEMS_DB)
            item = POEMS_DB[idx]
            return {
                "type": "poem",
                "type_name": "经典古诗",
                "idx": idx,
                "total": len(POEMS_DB),
                "title": f"🏮 诗词雅鉴 · 每日古诗 ({idx + 1}/{len(POEMS_DB)})",
                "poem_title": item["title"],
                "dynasty": item["dynasty"],
                "poet": item["poet"],
                "lines": item["lines"],
                "appreciation": item["appreciation"],
                "is_cycle": self.mode == "cycle",
            }
        elif act_type == "art":
            if not getattr(self, "_art_order", None) or len(self._art_order) != len(ART_DB):
                self.shuffle_art()
            curr_pos = self._indices["art"] % len(self._art_order)
            idx = self._art_order[curr_pos]
            item = ART_DB[idx]
            art_id = item.get("id", "starry_night")
            raw_bytes = get_art_bitmap(art_id)
            raw_b64 = base64.b64encode(raw_bytes).decode("ascii") if raw_bytes else ""
            res = {
                "type": "art",
                "type_name": "世界名画",
                "idx": curr_pos,
                "total": len(ART_DB),
                "title": "🎨 艺术长廊 · 世界名画",  # 彻底去除 (13/14) 这类页码
                "art_id": art_id,
                "art_title": item["title"],
                "artist": item["artist"],
                "year": item["year"],
                "style": item["style"],
                "appreciation": item["appreciation"],
                "is_cycle": self.mode == "cycle",
            }
            if len(raw_bytes) == 60000:
                res["raw_4bpp_b64"] = raw_b64
            else:
                res["raw_1bpp_b64"] = raw_b64
            return res
        elif act_type == "countdown":
            events = self.countdown_events or list(DEFAULT_COUNTDOWNS)
            idx = self._indices["countdown"] % len(events)
            ev = events[idx]
            return {
                "type": "countdown",
                "type_name": "倒计时",
                "idx": idx,
                "total": len(events),
                "title": f"⏳ 目标倒计时 ({idx + 1}/{len(events)})",
                "event_title": ev.title,
                "target_date": ev.target_date,
                "days_left": ev.days_left,
                "slogan": ev.slogan,
                "is_cycle": self.mode == "cycle",
            }
        elif act_type == "timetable":
            # 课程表卡片
            return {
                "type": "timetable",
                "type_name": "桌面课表",
                "idx": 0,
                "total": 1,
                "title": "📅 桌面课表 · 今日与明日",
                "schedule": self.today_schedule,
                "is_cycle": self.mode == "cycle",
            }
        # 兜底
        return {
            "type": "quote",
            "type_name": "名人语录",
            "idx": 0,
            "total": 1,
            "title": "📜 伴学屏保",
            "quote": "好好学习，天天向上！",
            "author": "学伴寄语",
            "is_cycle": False,
        }

    def get_speech_text(self) -> str:
        """获取当前卡片的朗读文本。"""
        card = self.get_card()
        t = card["type"]
        if t == "quote":
            return f"今日名言：“{card['quote']}”。出自{card['author']}。{card.get('motto','')}"
        elif t == "poem":
            lines_str = "，".join(card["lines"])
            return f"诗词鉴赏：《{card['poem_title']}》，{card['dynasty']}代，{card['poet']}。“{lines_str}”。{card.get('appreciation','')}"
        elif t == "art":
            return f"世界名画鉴赏：《{card['art_title']}》，由{card['artist']}创作于{card['year']}年。流派为{card['style']}。{card.get('appreciation','')}"
        elif t == "countdown":
            days = card["days_left"]
            if days > 0:
                day_say = f"距离{card['event_title']}还有{days}天！"
            elif days == 0:
                day_say = f"今天就是{card['event_title']}啦！全力以赴！"
            else:
                day_say = f"{card['event_title']}已经圆满完成！"
            return f"{day_say}{card.get('slogan','')}"
        elif t == "timetable":
            return "这是今日的桌面课程表。晚上记得按照课表收拾书包哦！"
        return "伴学屏保就绪。按上下键可自由切换屏保分类哦！"
