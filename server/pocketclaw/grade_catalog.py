"""小学伴学预设内容目录。

年级只是按阅读难度作的建议分组，不代表任何版本教材的课标或必背清单。
调用者可以选择一个或多个年级；None 表示全部，空列表表示不选。
"""
from __future__ import annotations

from copy import deepcopy
from typing import Iterable

from pocketclaw.notes import PRESET_IDIOMS, PRESET_WORDS
from pocketclaw.practice import CORE_POEMS

WORDS_BY_GRADE = {
    1: '天空 大地 日月 山水 花草 树木 小鸟 小鱼 白云 春天 夏天 秋天 冬天 下雨 彩虹 明亮 快乐 朋友 妈妈 爸爸 老师 学校 同学 书包 铅笔 早上 回家 手足 左右 开心'.split(),
    2: '春雨 阳光 树木 朋友 快乐 仔细 帮助 寻找 温暖 河流 田野 风景 故乡 花园 柳树 蝴蝶 认真 勇敢 关心 礼物 旅行 观察 发现 劳动 安静 清晨 休息 热闹 故事 世界'.split(),
    3: '温暖 认真 遵守 奇迹 灿烂 严寒 准备 丰富 想象 欣赏 旅行 经验 观察 发现 保护 环境 知识 勤奋 感受 美丽 精彩 勇气 耐心 诚实 礼貌 讨论 努力 习惯 探索 创造'.split(),
    4: '观察 探索 繁茂 慎重 柔和 逐渐 拂晓 研究 资料 科学 想象 奥秘 敏锐 积极 责任 信任 坚定 选择 理解 感激 鼓励 创造 精神 掌握 合作 困难 实践 尊重 锻炼 景象'.split(),
    5: '吩咐 茅亭 榨油 播种 浇水 爱慕 体面 开辟 宁静 期待 习惯 坚持 成就 实践 珍惜 贡献 责任 细腻 探索 创新 谦虚 态度 积累 运用 规律 生命 体会 奉献 沟通 感悟'.split(),
    6: '探索 规律 资源 环境 想象 成长 追求 坚毅 辽阔 郑重 意义 敬佩 慷慨 雄伟 考察 观点 证据 实践 理想 策略 担当 交流 探究 独立 寄托 顽强 传承 尊严 珍贵 未来'.split(),
}

POEM_TITLES_BY_GRADE = {
    1: ('咏鹅', '春晓', '静夜思', '小池', '池上', '风', '古朗月行'),
    2: ('登鹳雀楼', '悯农', '村居', '咏柳', '江雪', '所见', '草'),
    3: ('绝句', '望庐山瀑布', '山行', '赠汪伦', '望天门山', '清明', '饮湖上初晴后雨'),
    4: ('题西林壁', '鹿柴', '九月九日忆山东兄弟', '惠崇春江晚景', '早发白帝城', '晓出净慈寺送林子方', '游子吟'),
    5: ('示儿', '题临安邸', '泊船瓜洲', '枫桥夜泊', '黄鹤楼送孟浩然之广陵', '江南春', '书湖阴先生壁'),
    6: ('凉州词', '出塞', '芙蓉楼送辛渐', '石灰吟', '竹石', '塞下曲', '渔歌子'),
}

IDIOM_TITLES_BY_GRADE = {
    1: ('专心致志', '守株待兔', '井底之蛙', '狐假虎威', '熟能生巧', '亡羊补牢'),
    2: ('刻舟求剑', '掩耳盗铃', '拔苗助长', '愚公移山', '温故知新', '水滴石穿'),
    3: ('闻鸡起舞', '持之以恒', '滴水穿石', '精卫填海', '一诺千金', '迎难而上'),
    4: ('程门立雪', '囊萤映雪', '悬梁刺股', '开卷有益', '举一反三', '饮水思源'),
    5: ('同舟共济', '循序渐进', '见贤思齐', '学无止境', '妙笔生花', '敏而好学'),
    6: ('业精于勤', '海纳百川', '博学笃志', '实事求是', '千里之行', '志存高远'),
}

# Additional common idioms extend the existing explanations without changing the
# older practice library or its selections. These are recommended difficulty only.
EXTRA_IDIOMS_BY_GRADE = {
    1: [('画蛇添足', '做了多余的事，反而把事情弄坏。'), ('一心一意', '心思专一，没有别的念头。')],
    2: [('自相矛盾', '说话或行动前后互相抵触。'), ('叶公好龙', '表面上喜欢，实际上并不真正喜欢。')],
    3: [('百发百中', '形容射击准确，也比喻做事有把握。'), ('画龙点睛', '在关键地方加上一笔，使整体更生动。')],
    4: [('胸有成竹', '做事之前已经有完整的计划或把握。'), ('纸上谈兵', '只从理论上空谈，不能解决实际问题。')],
    5: [('不耻下问', '不以向不如自己的人请教为羞耻。'), ('锲而不舍', '坚持不懈，直到事情完成。')],
    6: [('集思广益', '集中众人的意见，吸收有益的建议。'), ('精益求精', '已经很好了，还要求更好。')],
}

# Public-domain poems added to the directory. The caller should use the full
# structured text, rather than assuming that every title is in CORE_POEMS.
EXTRA_POEMS_BY_GRADE = {
    1: [('小儿垂钓', '胡令能', '唐', ['蓬头稚子学垂纶，', '侧坐莓苔草映身。', '路人借问遥招手，', '怕得鱼惊不应人。'])],
    2: [('江畔独步寻花', '杜甫', '唐', ['黄四娘家花满蹊，', '千朵万朵压枝低。', '留连戏蝶时时舞，', '自在娇莺恰恰啼。'])],
    3: [('竹里馆', '王维', '唐', ['独坐幽篁里，', '弹琴复长啸。', '深林人不知，', '明月来相照。'])],
    4: [('乡村四月', '翁卷', '宋', ['绿遍山原白满川，', '子规声里雨如烟。', '乡村四月闲人少，', '才了蚕桑又插田。'])],
    5: [('四时田园杂兴·其三十一', '范成大', '宋', ['昼出耘田夜绩麻，', '村庄儿女各当家。', '童孙未解供耕织，', '也傍桑阴学种瓜。'])],
    6: [('墨梅', '王冕', '元', ['我家洗砚池头树，', '朵朵花开淡墨痕。', '不要人夸好颜色，', '只留清气满乾坤。'])],
}

CATEGORIES = ('words', 'idioms', 'poems', 'english')
GRADE_LABELS = {i: f'{"一二三四五六"[i-1]}年级' for i in range(1, 7)}


def _grades(grades: Iterable[int] | None) -> set[int]:
    if grades is None:
        return set(range(1, 7))
    if isinstance(grades, (str, bytes)):
        raise ValueError('年级必须是一至六年级数字列表')
    selected = set()
    for grade in grades:
        if type(grade) is not int or grade not in GRADE_LABELS:
            raise ValueError('年级必须是一至六年级数字列表')
        selected.add(grade)
    return selected


def get_catalog(category: str, grades: Iterable[int] | None = None) -> list[dict]:
    """Return detached records for the selected suggested grades.

    ``None`` returns all grades, ``[]`` returns no items. Existing collections
    are read only; the caller decides whether and how to apply a selection.
    """
    if category not in CATEGORIES:
        raise ValueError('未知预设内容类型')
    selected = _grades(grades)
    rows = []
    poems = {p.title: p for p in CORE_POEMS}
    idioms = {item['idiom']: item for item in PRESET_IDIOMS}
    for grade in sorted(selected):
        if category == 'words':
            for word in WORDS_BY_GRADE[grade]:
                rows.append({'id': f'word:{grade}:{word}', 'grade': grade, 'title': word, 'word': word})
        elif category == 'idioms':
            for title in IDIOM_TITLES_BY_GRADE[grade]:
                item = deepcopy(idioms[title])
                rows.append({'id': f'idiom:{title}', 'grade': grade, 'title': title, **item})
            for title, meaning in EXTRA_IDIOMS_BY_GRADE[grade]:
                rows.append({'id': f'idiom:{title}', 'grade': grade, 'title': title, 'idiom': title, 'pinyin': '', 'meaning': meaning, 'source': '', 'example': ''})
        elif category == 'poems':
            for title in POEM_TITLES_BY_GRADE[grade]:
                p = poems[title]
                rows.append({'id': f'poem:{p.title}:{p.author}', 'grade': grade, 'title': p.title, 'author': p.author, 'dynasty': p.dynasty, 'lines': list(p.lines)})
            for title, author, dynasty, lines in EXTRA_POEMS_BY_GRADE[grade]:
                rows.append({'id': f'poem:{title}:{author}', 'grade': grade, 'title': title, 'author': author, 'dynasty': dynasty, 'lines': list(lines)})
        else:
            # Existing vocabulary is ordered by introductory, intermediate and
            # advanced difficulty; each band is divided between two grades.
            starts = (0, 19, 39, 65, 91, 98, len(PRESET_WORDS))
            for word, meaning, example, example_cn in PRESET_WORDS[starts[grade-1]:starts[grade]]:
                rows.append({'id': f'english:{word.lower()}', 'grade': grade,
                             'title': word, 'word': word, 'meaning': meaning,
                             'example': example, 'example_cn': example_cn})
    return rows
