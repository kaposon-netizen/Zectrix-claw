import asyncio
import pytest
import json
from pocketclaw.bridge import PocketClawSession
from pocketclaw.notes import NotesStore
from pocketclaw.reading import ReadingStore, ReadingSession
from pocketclaw.voice import OfflineSTT, OfflineTTS
from pocketclaw import display


class FakeBridgeSend:
    def __init__(self):
        self.sent_jsons = []
        self.sent_bins = []

    async def send_json(self, data):
        if isinstance(data, str):
            data = json.loads(data)
        self.sent_jsons.append(data)

    async def send_bin(self, data):
        self.sent_bins.append(data)


def last_page(send):
    for j in reversed(send.sent_jsons):
        if j.get("kind") == "display":
            return j["page"]
    raise ValueError(f"No display page found in sent_jsons: {[j.get('kind') for j in send.sent_jsons]}")


@pytest.fixture
def fake_bridge():
    send = FakeBridgeSend()
    store = NotesStore(path=":memory:")
    store.add_word('test_kid', 'apple', meaning='苹果', example='I eat an apple.', example_cn='我吃一个苹果。')
    store.add_word('test_kid', 'banana', meaning='香蕉', example='The banana is yellow.', example_cn='香蕉是黄色的。')
    b = PocketClawSession(
        runtime=None,
        send_json=send.send_json,
        send_bin=send.send_bin,
        stt=OfflineSTT(),
        tts=OfflineTTS(),
        registry=None,
        notes=store,
    )
    b.child_id = "test_kid"
    b.helloed = True
    return b, send, store


@pytest.mark.anyio
async def test_vocab_mode_workflow(fake_bridge):
    bridge, send, notes = fake_bridge

    # 1. 开启英语生词本
    await bridge._start_vocab(now=1000.0)
    assert bridge.mode == "vocab"
    page = last_page(send)
    assert page["mode"] == "vocab"
    assert "apple" in " ".join(page["body"])

    # 2. 按键下翻一个生词
    await bridge._on_button("down", now=1001.0)
    assert bridge._vocab_idx == 1
    page2 = last_page(send)
    assert "banana" in " ".join(page2["body"])

    # 3. 按键上翻一个生词
    await bridge._on_button("up", now=1002.0)
    assert bridge._vocab_idx == 0
    page3 = last_page(send)
    assert "apple" in " ".join(page3["body"])

    # 4. 生词跟读评测
    await bridge._vocab_evaluate("apple", now=1003.0)
    assert bridge._vocab_last_score == 100
    page4 = last_page(send)
    assert "跟读评测：100分" in " ".join(page4["body"])


@pytest.mark.anyio
async def test_reading_mode_workflow_and_sentence_selection(fake_bridge):
    bridge, send, notes = fake_bridge

    # 1. 进入英语分级阅读
    await bridge._start_reading(now=1000.0)
    assert bridge.mode == "reading"
    assert bridge._reading_session is not None
    assert bridge._reading_session.sel_idx == 0

    page1 = last_page(send)
    assert page1["mode"] == "reading"
    # 段落自然呈现，无 [1] 序号，当前选中句黑底白字高亮反色，静默无中文
    assert "Today is a sunny Sunday." in " ".join(page1["body"])
    assert display.INVERT_MARK + "Today is a sunny Sunday." in " ".join(page1["body"])
    assert "[1]" not in " ".join(page1["body"])
    assert "【译】" not in " ".join(page1["body"])
    assert "(1/" in page1["title"]
    assert "↑/↓" in page1["title"] or "▲/▼" in page1["title"]

    # 2. 按键【下】：选句下移到第 2 句（静默无声、无中文、仅高亮反色）
    await bridge._on_button("down", now=1001.0)
    assert bridge._reading_session.sel_idx == 1
    page2 = last_page(send)
    joined_text = " ".join(page2["body"]).replace(display.INVERT_MARK, "").replace(display.INVERT_END, "")
    assert "Tom and his dog" in joined_text
    assert display.INVERT_MARK + "Tom and" in " ".join(page2["body"])
    assert "【译】" not in " ".join(page2["body"])

    # 3. 按确认键【选句朗读】：朗读当前选中的第 2 句并显示中文翻译
    await bridge._on_button("confirm", now=1002.0)
    cur_en, cur_cn = bridge._reading_session.cur_sentence
    assert cur_en == "Tom and his dog go to the park."
    page3 = last_page(send)
    assert "【译】汤姆和他的小狗去公园。" in " ".join(page3["body"])

    # 4. 按键【上】：选句上移回第 1 句，译文自动清除保持纯净段落
    await bridge._on_button("up", now=1003.0)
    assert bridge._reading_session.sel_idx == 0
    page4 = last_page(send)
    assert "【译】" not in " ".join(page4["body"])

    # 5. 选句跟读评测
    await bridge._reading_evaluate("Today is a sunny Sunday.", now=1004.0)
    assert bridge._reading_session.last_score == 100
    page5 = last_page(send)
    assert "选句评测：100分" in " ".join(page5["body"])
    assert "【译】今天是一个晴朗的星期天。" in " ".join(page5["body"])


@pytest.mark.anyio
async def test_mode_menu_and_cycling(fake_bridge):
    bridge, send, notes = fake_bridge

    # 1. 打开功能菜单
    await bridge._on_button("menu", now=1000.0)
    assert bridge.mode == "menu"
    page = last_page(send)
    assert page["mode"] == "menu"

    # 2. 光标移动并选择
    await bridge._on_button("down", now=1001.0)
    assert bridge._menu_idx == 1  # 课内古诗

    await bridge._on_button("down", now=1002.0)
    assert bridge._menu_idx == 2  # 生字听写

    await bridge._on_button("down", now=1003.0)
    assert bridge._menu_idx == 3  # 口算闯关

    await bridge._on_button("down", now=1004.0)
    assert bridge._menu_idx == 4  # 英语生词

    # 按确认键进入英语生词
    await bridge._on_button("confirm", now=1005.0)
    assert bridge.mode == "vocab"

    # 3. 双击切换下一模式 (mode_next)
    await bridge._on_button("mode_next", now=1006.0)
    assert bridge.mode == "reading"

    # 4. 双击切换下一模式 (mode_next)
    await bridge._on_button("mode_next", now=1007.0)
    assert bridge.mode == "focus"


@pytest.mark.anyio
async def test_reading_article_switching_by_long_press_buttons(fake_bridge):
    bridge, send, notes = fake_bridge

    # 1. 进入英语阅读模式（默认第一篇 A Sunny Day）
    await bridge._start_reading(now=1000.0)
    assert bridge.mode == "reading"
    assert bridge._reading_article_idx == 0
    p1 = last_page(send)
    assert "A Sunny Day" in p1["title"]
    assert "Today is a sunny Sunday." in " ".join(p1["body"])

    # 长按下键统一进入菜单，再次长按返回阅读。
    await bridge._on_button("down_long", now=1001.0)
    assert bridge.mode == "menu"
    await bridge._on_button("down_long", now=1002.0)
    assert bridge.mode == "reading"
    assert bridge._reading_article_idx == 0
    await bridge._on_button("up_long", now=1003.0)
    assert bridge.mode == "menu"


@pytest.mark.anyio
async def test_reading_article_voice_switching_and_selection(fake_bridge):
    bridge, send, notes = fake_bridge

    # 1. 语音开启阅读模式
    await bridge._on_utterance(b"", "英语阅读", now=1000.0)
    assert bridge.mode == "reading"
    assert bridge._reading_article_idx == 0

    # 2. 语音说“下一篇” / “下一篇文章”切换下一篇
    await bridge._on_utterance(b"", "下一篇", now=1001.0)
    assert bridge._reading_article_idx == 1
    assert "The Little Red Hen" in last_page(send)["title"]

    await bridge._on_utterance(b"", "换一篇文章", now=1002.0)
    assert bridge._reading_article_idx == 2
    assert "A Trip to the Zoo" in last_page(send)["title"]

    # 3. 语音说“上一篇”切换回上一篇
    await bridge._on_utterance(b"", "上一篇", now=1003.0)
    assert bridge._reading_article_idx == 1
    assert "The Little Red Hen" in last_page(send)["title"]

    # 4. 语音指定篇名选择：“我想读狐狸和葡萄”
    await bridge._on_utterance(b"", "我想读狐狸和葡萄", now=1004.0)
    assert bridge._reading_article_idx == 3
    assert "The Fox" in last_page(send)["title"]

    # 5. 语音指定序号选择：“读第1篇”
    await bridge._on_utterance(b"", "读第1篇", now=1005.0)
    assert bridge._reading_article_idx == 0
    assert "A Sunny Day" in last_page(send)["title"]

    # 6. 语音指定篇名别名：“读动物园”
    await bridge._on_utterance(b"", "读动物园", now=1006.0)
    assert bridge._reading_article_idx == 2
    assert "A Trip to the Zoo" in last_page(send)["title"]

    # 7. 语音指定篇名别名：“小红母鸡”
    await bridge._on_utterance(b"", "读小红母鸡", now=1007.0)
    assert bridge._reading_article_idx == 1
    assert "The Little Red Hen" in last_page(send)["title"]


@pytest.mark.anyio
async def test_long_press_in_other_modes_opens_menu(fake_bridge):
    bridge, send, notes = fake_bridge

    # 在任务列表模式下，长按下键打开功能菜单
    await bridge._show_tasks()
    assert bridge.mode == "task"

    await bridge._on_button("down_long", now=1001.0)
    assert bridge.mode == "menu"

    # 在课程表模式下，长按上键打开功能菜单
    await bridge._switch_to_mode("timetable", now=1002.0)
    assert bridge.mode == "timetable"

    await bridge._on_button("up_long", now=1003.0)
    assert bridge.mode == "menu"


@pytest.mark.anyio
async def test_unified_voice_controls_in_recite_screensaver_and_vocab(fake_bridge):
    bridge, send, notes = fake_bridge

    # 1. 古诗背诵模式：语音切诗
    await bridge._switch_to_mode("recite", now=1000.0)
    assert bridge.mode == "recite"
    cur_poem = bridge._recite_poem.title

    await bridge._on_utterance(b"", "下一首", now=1001.0)
    assert bridge.mode == "recite"
    assert bridge._recite_poem.title != cur_poem

    # 2. 屏保模式：语音切卡片
    await bridge._start_screensaver(now=1002.0, mode="quote")
    assert bridge.mode == "screensaver"
    card1 = bridge._screensaver_session.get_card()

    await bridge._on_utterance(b"", "下一张", now=1003.0)
    card2 = bridge._screensaver_session.get_card()
    assert bridge.mode == "screensaver"

    # 3. 英语生词本：语音切词
    await bridge._start_vocab(now=1004.0)
    assert bridge.mode == "vocab"
    assert bridge._vocab_idx == 0

    await bridge._on_utterance(b"", "下一个", now=1005.0)
    assert bridge._vocab_idx == 1

    await bridge._on_utterance(b"", "上一个", now=1006.0)
    assert bridge._vocab_idx == 0


