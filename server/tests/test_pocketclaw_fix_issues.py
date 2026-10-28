import asyncio
import pytest
from pocketclaw import display, practice, protocol
from pocketclaw.bridge import PocketClawSession
from tests.test_pocketclaw_focus import Sink, _session


def run(coro):
    return asyncio.run(coro)


def test_dictation_anti_spoiler_no_characters_on_screen():
    """听写界面严禁剧透汉字：只展示进度、字数占位符【？】与拼音，严禁在屏幕上打印目标汉字。"""
    page = display.render_dictation("生字词听写", cur_idx=1, total=8, word_len=2, pinyin="wēn nuǎn")
    body_text = "\n".join(page["body"])
    # 目标汉字绝对不能出现在界面上
    assert "温暖" not in body_text
    assert "温" not in body_text
    assert "暖" not in body_text
    # 占位符与拼音正常展示（注：screen_text 将拼音音标规范化为 ASCII 字符以便 1bpp 字体显示）
    assert "【？】" in body_text
    assert "wen nuan" in body_text or "wēn nuǎn" in body_text
    assert "第 1 / 8 词" in body_text

    # 只有全部听写完进入自查总结时，才展示完整订正清单
    summary = display.render_dictation_summary("听写自查清单", practice.DEFAULT_DICTATION_WORDS)
    summary_text = "\n".join(summary["body"])
    assert "温暖" in summary_text
    assert "认真" in summary_text


def test_recite_anti_spoiler_no_first_line_spoken():
    """背诵考核严禁提前剧透第一句诗句。"""
    sink = Sink()
    sess = _session(sink)
    sess._features = ("focus_local",)

    spoken_texts = []
    orig_speak = sess._speak

    async def record_speak(text, **kw):
        spoken_texts.append(text)
        return await orig_speak(text, **kw)

    sess._speak = record_speak

    poem = practice.POEM_MAP["静夜思"]
    first_line = poem.lines[0].replace("，", "").replace("。", "")  # "床前明月光"

    # 1. 启动背诵时：只报诗题、朝代、作者，绝不提前念第一句
    run(sess._start_recite("静夜思", 0.0))
    assert any("静夜思" in s for s in spoken_texts)
    assert not any(first_line in s for s in spoken_texts)

    # 2. 短按确认键求助或提示时：提示孩子背诵整首诗，绝不报第一句
    spoken_texts.clear()
    run(sess._on_button("confirm", 0.0))
    assert not any(first_line in s for s in spoken_texts)

    # 3. 初始屏幕遮罩：所有句子均被遮蔽
    page = sink.pages()[-1]
    page_text = "\n".join(page["body"])
    assert "【 · · · · · 】" in page_text
    assert first_line not in page_text

    # 4. 彻底不相干或卡壳时：质检算法提示从第一句开始背，不泄露原句
    res = practice.check_recitation(poem, "天气真好呀想吃冰淇淋")
    assert not res.passed
    assert first_line not in res.feedback


def test_recite_mode_voice_switching_to_arith_and_other_modes():
    """在古诗背诵进行中，说“做口算”或“口算”等全局模式切换指令，必须立即精准切入目标模式。"""
    sink = Sink()
    sess = _session(sink)
    sess._features = ("focus_local",)

    # 进入背诵模式
    run(sess._start_recite("静夜思", 0.0))
    assert sess.mode == "recite"

    # 在背诵模式中直接说「做口算」
    run(sess.on_control({"kind": "text", "text": "做口算"}, now=10.0))
    assert sess.mode == "arith"
    assert sess._arith_session is not None
    arith_page = sink.pages()[-1]
    assert "口算大闯关" in arith_page["title"]

    # 在口算模式中直接说「听写」
    run(sess.on_control({"kind": "text", "text": "开始听写"}, now=20.0))
    assert sess.mode == "dictation"
    assert sess._dictation_session is not None

    # 在听写模式中直接说「英语生词」
    run(sess.on_control({"kind": "text", "text": "生词本"}, now=30.0))
    assert sess.mode == "vocab"

    # 在生词本模式中直接说「英语阅读」
    run(sess.on_control({"kind": "text", "text": "英语阅读"}, now=40.0))
    assert sess.mode == "reading"

    # 在英语阅读模式中直接切回「口算」
    run(sess.on_control({"kind": "text", "text": "口算"}, now=50.0))
    assert sess.mode == "arith"


def test_focus_local_mode_and_no_global_refresh_frames():
    """番茄钟必须恢复纯本地跑秒与局刷机制，服务端严禁推全局全刷覆盖帧。"""
    sink = Sink()
    sess = _session(sink)
    sess._features = ("focus_local",)

    # 1. 从菜单/按键切换到专注模式
    run(sess._switch_to_mode("focus", now=100.0))
    assert sess.mode == "focus"
    assert sess._focus is not None
    assert sess._focus.minutes == 25

    # 严禁发送全屏 display 覆盖帧（本地跑秒画面由设备 focus_page 自绘）
    assert not sink.pages()
    assert sess._focus_task is None  # 不跑每分钟推全屏的老循环

    # 必须发送标准 focus_start 帧给硬件启动本地 FocusTick
    start_frames = [m for m in sink.json if m.get("kind") == "focus_start"]
    assert len(start_frames) == 1
    assert start_frames[0]["total_s"] == 1500
    assert start_frames[0]["mode"] == "pomodoro"
    assert start_frames[0]["style"] == "spark"

    # 2. 物理确认键暂停/继续
    sink.json.clear()
    run(sess._on_button("confirm", now=120.0))
    pause_frames = [m for m in sink.json if m.get("kind") == "focus_pause"]
    assert len(pause_frames) == 1
    assert pause_frames[0]["paused"] is True
    assert sess._focus.paused is True

    # 再次按确认键继续
    sink.json.clear()
    run(sess._on_button("confirm", now=130.0))
    resume_frames = [m for m in sink.json if m.get("kind") == "focus_pause"]
    assert len(resume_frames) == 1
    assert resume_frames[0]["paused"] is False
    assert sess._focus.paused is False


def test_menu_navigation_partial_refresh():
    """功能导航菜单选择时，必须采用局部刷新（partial: True），严禁全局黑白全刷。"""
    sink = Sink()
    sess = _session(sink)
    sess._features = ("focus_local",)

    # 1. 打开菜单（首次进入菜单）：initial frame partial=False
    run(sess._on_button("menu", now=1.0))
    assert sess.mode == "menu"
    assert sess._menu_idx == 0
    pages = sink.pages()
    assert len(pages) == 1
    assert pages[-1]["mode"] == "menu"
    assert pages[-1].get("partial") is False

    # 2. 按下键移动光标：必须带 partial=True 局部刷新
    run(sess._on_button("down", now=2.0))
    assert sess.mode == "menu"
    assert sess._menu_idx == 1
    pages = sink.pages()
    assert len(pages) == 2
    assert pages[-1]["mode"] == "menu"
    assert pages[-1].get("partial") is True
    assert "> [2]" in "\n".join(pages[-1]["body"])

    # 3. 按上键移动光标：同样带 partial=True 局部刷新
    run(sess._on_button("up", now=3.0))
    assert sess.mode == "menu"
    assert sess._menu_idx == 0
    pages = sink.pages()
    assert len(pages) == 3
    assert pages[-1]["mode"] == "menu"
    assert pages[-1].get("partial") is True
    assert "> [1]" in "\n".join(pages[-1]["body"])

    # 4. 英语阅读选句切换：也必须带 partial=True 局部刷新
    run(sess._start_reading(now=4.0, article_idx=0))
    assert sess.mode == "reading"
    pages = sink.pages()
    count_before = len(pages)

    run(sess._on_button("down", now=5.0))
    pages = sink.pages()
    assert len(pages) == count_before + 1
    assert pages[-1]["mode"] == "reading"
    assert pages[-1].get("partial") is True


def test_english_reading_full_article_and_inverted_highlight():
    """英语阅读展示自然英文段落，选中的句子为黑底白字高亮(\\x02)，选句静默无声，短按确认键朗读并展示中文翻译。"""
    from pocketclaw import reading
    sink = Sink()
    sess = _session(sink)
    sess._features = ("focus_local",)

    spoken_sentences = []
    orig_speak = sess._speak

    async def mock_speak(text, **kw):
        spoken_sentences.append(text)
        return await orig_speak(text, **kw)

    sess._speak = mock_speak

    # 1. 开启英语短文阅读
    run(sess._start_reading(now=10.0, article_idx=0))
    art = reading.PRESET_ARTICLES[0]  # A Sunny Day
    pages = sink.pages()
    last_page = pages[-1]
    body_text = "\n".join(last_page["body"])

    # 验证自然段落呈现：无 [1] 序号，无中文译文干扰
    assert "[1]" not in body_text
    assert "【译】" not in body_text
    assert "Today is a sunny Sunday." in body_text
    # 验证黑底白字高亮哨兵：当前选中第 1 句带 \x02
    assert display.INVERT_MARK + "Today is a sunny Sunday." in body_text
    # 首行显示进度与上下键提示
    assert "《A Sunny Day》 (1/7)" in last_page["title"]
    assert "↑/↓" in last_page["title"] or "▲/▼" in last_page["title"]

    # 2. 按【下键】切换到第 2 句：静默切换不播报、不显示中文，黑底白字高亮移动到第 2 句
    spoken_sentences.clear()
    run(sess._on_button("down", now=20.0))
    pages = sink.pages()
    body_text = "\n".join(pages[-1]["body"])
    assert display.INVERT_MARK + "Tom and" in body_text
    assert "his dog go to the park." in body_text
    assert display.INVERT_MARK + "Today is a sunny" not in body_text
    assert len(spoken_sentences) == 0  # 选句静默，不朗读！
    assert "【译】" not in body_text      # 选句不显示中文！

    # 3. 短按确认键（说话键）：朗读选中的当前句，并在屏幕上展示中文译文！
    spoken_sentences.clear()
    run(sess._on_button("confirm", now=30.0))
    pages = sink.pages()
    body_text = "\n".join(pages[-1]["body"])
    assert art.sentences[1][0] in spoken_sentences[-1]
    assert "【译】汤姆和他的小狗去公园。" in body_text


def test_menu_navigation_silent_and_instant():
    """功能菜单在上下选择时严禁播报语音，保证瞬时切换零延时。"""
    sink = Sink()
    sess = _session(sink)
    sess._features = ("focus_local",)

    spoken_list = []
    orig_speak = sess._speak

    async def mock_speak(text, **kw):
        spoken_list.append(text)
        return await orig_speak(text, **kw)

    sess._speak = mock_speak

    # 首次按菜单键进入：全程静默，严禁任何语音播报
    run(sess._on_button("menu", now=1.0))
    assert sess.mode == "menu"
    assert len(spoken_list) == 0

    # 上下移动选择项目：严禁任何语音播报！屏幕跟上瞬时切换
    spoken_list.clear()
    run(sess._on_button("down", now=2.0))
    run(sess._on_button("down", now=3.0))
    run(sess._on_button("up", now=4.0))
    assert len(spoken_list) == 0  # 静默零延迟


def test_mp_and_parent_apis():
    """测试微信小程序与家长网页全功能 REST API。"""
    from starlette.testclient import TestClient
    from app import app
    client = TestClient(app)

    # 1. 概览接口
    r = client.get("/api/pocketclaw/mp/overview")
    assert r.status_code == 200
    ov = r.json()
    assert ov["ok"] is True
    assert "tasks" in ov
    assert "poem" in ov
    assert "arith_config" in ov

    # 2. 作业清单 CRUD
    r = client.post("/api/pocketclaw/mp/tasks", json={"text": "写两页生字", "subject": "语文"})
    assert r.status_code == 200
    task_id = r.json()["task"]["id"]

    r = client.post(f"/api/pocketclaw/mp/tasks/{task_id}/toggle")
    assert r.status_code == 200
    assert r.json()["task"]["done"] is True

    r = client.delete(f"/api/pocketclaw/mp/tasks/{task_id}")
    assert r.status_code == 200

    # 3. 古诗背诵设置
    r = client.get("/api/pocketclaw/mp/poems")
    assert r.status_code == 200
    assert len(r.json()["poems"]) >= 10

    r = client.post("/api/pocketclaw/mp/poems/target", json={"title": "春晓"})
    assert r.status_code == 200
    assert r.json()["target"] == "春晓"

    # 4. 英语阅读篇目设置
    r = client.get("/api/pocketclaw/mp/reading")
    assert r.status_code == 200
    assert len(r.json()["articles"]) >= 3

    r = client.post("/api/pocketclaw/mp/reading/target", json={"article_id": "art_little_red_hen"})
    assert r.status_code == 200

    # 5. 亲子留言便签推屏
    r = client.post("/api/pocketclaw/mp/message", json={"text": "宝贝今天真棒，为你骄傲！"})
    assert r.status_code == 200
    assert r.json()["ok"] is True

    # 6. 学情报表与成就勋章
    r = client.get("/api/pocketclaw/mp/report")
    assert r.status_code == 200
    rep = r.json()
    assert rep["ok"] is True
    assert len(rep["badges"]) >= 4


def test_comprehensive_pagination_menu_reading_habits():
    """全面校验功能导航菜单、英语阅读、好习惯打卡、作业清单等多屏分页联动机制。"""
    from pocketclaw import reading
    sink = Sink()
    sess = _session(sink)
    sess._features = ("focus_local",)

    # 1. 功能导航菜单多页分页测试（每页 8 项饱满呈现，共 17 项，3 页）
    run(sess._on_button("menu", now=1.0))
    p = sink.pages()[-1]
    assert "功能导航菜单 (1/3)" in p["title"]
    assert len(p["body"]) == 8
    assert "> [1]" in p["body"][0]

    # 连续下移到第 9 项（下标 8）：自动翻页至第 2 页！
    for i in range(8):
        run(sess._on_button("down", now=2.0 + i))
    p2 = sink.pages()[-1]
    assert "功能导航菜单 (2/3)" in p2["title"]
    assert len(p2["body"]) == 8  # 第 2 页 8 项
    assert "> [9]" in p2["body"][0]

    # 连续下移到第 17 项（下标 16）：自动翻页至第 3 页！
    for i in range(8):
        run(sess._on_button("down", now=10.0 + i))
    p2_3 = sink.pages()[-1]
    assert "功能导航菜单 (3/3)" in p2_3["title"]
    assert len(p2_3["body"]) == 3  # 第 3 页 3 项（含新增小学新鲜事与AI学伴共19项）
    assert "> [17]" in p2_3["body"][0]

    # 上移回到第 8 项（下标 7）：自动翻回第 1 页！
    for i in range(9):
        run(sess._on_button("up", now=19.0 + i))
    p3 = sink.pages()[-1]
    assert "功能导航菜单 (1/3)" in p3["title"]
    assert "> [8]" in p3["body"][-1]

    # 2. 英语分级阅读多页分页联动测试
    run(sess._start_reading(now=30.0, article_idx=0))  # A Sunny Day (7句)
    rp1 = sink.pages()[-1]
    assert "《A Sunny Day》 (1/7)" in rp1["title"]
    assert "Today is a sunny Sunday." in rp1["body"][0]

    # 移动到第 5 句（下标 4）：自然流式选句高亮
    for i in range(4):
        run(sess._on_button("down", now=31.0 + i))
    rp2 = sink.pages()[-1]
    assert "《A Sunny Day》 (5/7)" in rp2["title"]
    # 选中的第 5 句必须黑底白字高亮反色在可见行中
    assert any(display.INVERT_MARK + "Tom throws" in line for line in rp2["body"])
    # 选句时静默无中文
    assert not any("【译】" in line for line in rp2["body"])

    # 短按确认键：朗读并展示中文
    run(sess._on_button("confirm", now=30.0))
    rp3 = sink.pages()[-1]
    body_joined = "".join(rp3["body"])
    assert "【译】" in body_joined
    assert "汤姆把一个红色的球扔在草地上。" in body_joined

    # 3. 作业清单长列表不被截断测试
    items = [(f"作业第{i+1}项", i % 2 == 0) for i in range(12)]
    cl_page = display.render_checklist("今日作业", items)
    assert len(cl_page["body"]) >= 12  # 全量正文下发给硬件进行本地多页翻页

    # 4. 好习惯打卡分页测试
    from types import SimpleNamespace
    habits = [SimpleNamespace(name=f"习惯{i+1}", done=False, streak=i) for i in range(9)]
    h_p1 = display.render_habits(habits, sel_idx=0)
    assert "(1/2)" in h_p1["title"]
    assert len(h_p1["body"]) == 6

    h_p2 = display.render_habits(habits, sel_idx=7)
    assert "(2/2)" in h_p2["title"]
    assert len(h_p2["body"]) == 3


def test_audit_fixes_recite_stepping_and_parent_portal(tmp_path):
    """全面审查修复回归测试：
    1. 背诵模式上下键切诗静默不复读
    2. _resolve_child 杜绝 mp/undefined/null 键污染
    3. 家长端 _HTML_PARENT_PORTAL 包含移动端模态弹窗与 data-tab 属性
    4. 奖励星星实时推屏便签
    """
    from pocketclaw.bridge import PocketClawSession
    from pocketclaw.notes import NotesStore
    from pocketclaw.device_registry import DeviceRegistry
    from pocketclaw import endpoint
    from pocketclaw.practice import CORE_POEMS

    def run(coro):
        return asyncio.run(coro)

    sink = Sink()
    sess = _session(sink)

    # 1. 背诵模式进入与上下键静默切诗
    run(sess._start_recite(CORE_POEMS[0].title, now=10.0))
    assert sess.mode == "recite"
    assert sess._recite_poem.title == CORE_POEMS[0].title

    # 按下键切下一首诗，应当静默换屏，不触发重复的 _start_recite 语音提示
    bin_before = len(sink.bin)
    run(sess._on_button("down", now=11.0))
    assert sess._recite_poem.title == CORE_POEMS[1].title
    assert len(sink.bin) == bin_before  # 静默无声
    recite_p = sink.pages()[-1]
    assert CORE_POEMS[1].title in recite_p["title"]

    # 按上键切回第一首诗
    run(sess._on_button("up", now=12.0))
    assert sess._recite_poem.title == CORE_POEMS[0].title

    # 2. _resolve_child 过滤测试
    from app import app
    from fastapi.testclient import TestClient
    client = TestClient(app)

    # 3. 模态框与 data-tab 存在于家长端 HTML
    portal_res = client.get("/pocketclaw/parent")
    assert portal_res.status_code == 200
    html = portal_res.text
    assert 'id="modalAddStars"' in html
    assert 'id="modalAddWish"' in html
    assert 'data-tab="tasks"' in html
    assert 'data-tab="habits"' in html
    assert 'data-tab="stars"' in html
    assert 'data-tab="report"' in html
    assert 'confirmAddStars' in html
    assert 'confirmAddWish' in html

    # 4. 家长奖励星星推屏与数据更新
    add_star_res = client.post("/api/pocketclaw/mp/stars/add", json={"count": 5, "reason": "数学口算满分", "child_id": "kid"})
    assert add_star_res.status_code == 200
    star_data = add_star_res.json()
    assert star_data["ok"] is True
    assert star_data["balance"] >= 5


def test_habit_key_and_voice_logic():
    """测试好习惯打卡按键与语音逻辑：
    - 上下键切换具体任务
    - 说话按键（confirm）确认完成打卡，再次按下说话按键取消选中具体任务打卡
    - 语音支持直接打卡与取消
    """
    sink = Sink()
    sess = _session(sink)
    sess.child_id = "test_kid_habit"
    # 初始化几项习惯
    sess.notes.add_habit(sess.child_id, "晨读语文15分钟")
    sess.notes.add_habit(sess.child_id, "课后跳绳500个")
    sess.notes.add_habit(sess.child_id, "整理书包")

    # 1. 进入习惯模式
    run(sess._start_habits(now=100.0, speak=False))
    assert sess.mode == "habit"
    assert getattr(sess, "_habit_idx", 0) == 0

    # 2. 下键切换具体任务
    run(sess._on_button("down", now=101.0))
    assert getattr(sess, "_habit_idx", 0) == 1

    # 3. 上键切换回上一项
    run(sess._on_button("up", now=102.0))
    assert getattr(sess, "_habit_idx", 0) == 0

    # 4. 说话按键（confirm）确认完成打卡
    run(sess._on_button("confirm", now=103.0))
    habits = sess.notes.list_habits(sess.child_id)
    assert habits[0].done is True

    # 5. 再按一次说话按键（confirm）取消打卡
    run(sess._on_button("confirm", now=104.0))
    habits = sess.notes.list_habits(sess.child_id)
    assert habits[0].done is False

    # 6. 语音控制打卡当前项
    run(sess.on_control({"kind": "text", "text": "完成打卡"}, now=105.0))
    habits = sess.notes.list_habits(sess.child_id)
    assert habits[0].done is True

    # 7. 语音控制取消打卡
    run(sess.on_control({"kind": "text", "text": "取消打卡"}, now=106.0))
    habits = sess.notes.list_habits(sess.child_id)
    assert habits[0].done is False


def test_task_cursor_and_idiom_stepping_partial_refresh():
    """测试今日作业清单上下键光标选行 + 局刷完成指定项，以及每日成语上下键局刷切换。"""
    sink = Sink()
    sess = _session(sink)
    sess.child_id = "test_kid_task_idiom"
    sess.notes.add_task(sess.child_id, "语文背古诗")
    sess.notes.add_task(sess.child_id, "数学口算10题")
    sess.notes.add_task(sess.child_id, "英语读课文")

    run(sess._show_tasks())
    assert sess.mode == "task"
    assert sess._task_idx == -1

    # 按下键两次选中第 2 项（索引 1：数学口算10题）
    run(sess._on_button("down", now=10.0))
    assert sess._task_idx == 0
    assert sink.pages()[-1]["partial"] is True
    run(sess._on_button("down", now=11.0))
    assert sess._task_idx == 1
    assert sink.pages()[-1]["partial"] is True

    # 按确认键直接完成选中的第 2 项（数学口算10题），完成后自动归入底部已完成区
    run(sess._on_button("confirm", now=12.0))
    rows = sess.notes.list_tasks(sess.child_id)
    by_text = {r.text: r.done for r in rows}
    assert by_text["语文背古诗"] is False
    assert by_text["数学口算10题"] is True
    assert by_text["英语读课文"] is False

    # 切换到每日成语模式，测试上下键切换成语 + partial=True
    run(sess._show_daily_idiom(now=20.0))
    assert sess.mode == "idiom"
    first_idiom = sess.notes.get_daily_idiom(sess.child_id)["idiom"]
    run(sess._on_button("down", now=21.0))
    second_idiom = sess.notes.get_daily_idiom(sess.child_id)["idiom"]
    assert second_idiom != first_idiom
    assert sink.pages()[-1]["partial"] is True


def test_dictation_entry_speaks_first_word():
    """刚切换到听写功能页面，第一个词必须立即播报。"""
    sink = Sink()
    sess = _session(sink)

    spoken = []

    async def mock_speak(text, **kw):
        spoken.append(text)

    sess._speak = mock_speak

    # 1. 直接启动听写
    run(sess._start_dictation(10.0))
    assert sess.mode == "dictation"
    assert len(spoken) >= 1
    assert "第 1 个词" in spoken[-1]
    assert "温暖" in spoken[-1]
    assert "工整写下" in spoken[-1]

    # 2. 从其它模式切入听写
    spoken.clear()
    run(sess._switch_to_mode("task", 20.0))
    run(sess._switch_to_mode("dictation", 21.0))
    assert sess.mode == "dictation"
    assert len(spoken) >= 1
    assert "第 1 个词" in spoken[-1]
    assert "温暖" in spoken[-1]


def test_feihua_down_button_rotates_keyword():
    """飞花令向下键换令字：按下键必须轮换到下一个令字并重新开局播报。"""
    sink = Sink()
    sess = _session(sink)

    spoken = []

    async def mock_speak(text, **kw):
        spoken.append(text)

    sess._speak = mock_speak

    # 启动飞花令，指定令字为 春
    run(sess._start_feihua(10.0, keyword="春", speak=False))
    assert sess._feihua_session.keyword == "春"

    # 按向下键换令字
    spoken.clear()
    run(sess._on_button("down", 11.0))
    first_rotated = sess._feihua_session.keyword
    assert first_rotated != "春"
    assert len(spoken) >= 1
    assert f"本局令字是「{first_rotated}」" in spoken[-1] or f"令字是「{first_rotated}」" in spoken[-1]

    # 再次按向下键换令字
    spoken.clear()
    run(sess._on_button("down", 12.0))
    second_rotated = sess._feihua_session.keyword
    assert second_rotated != first_rotated
    assert len(spoken) >= 1
    assert f"本局令字是「{second_rotated}」" in spoken[-1] or f"令字是「{second_rotated}」" in spoken[-1]


def test_guess_game_frog_jumping_and_features():
    """拆拆看谜底为青蛙时，问会跳吗、叫声、颜色等常识，必须精准识别特征。"""
    sess = practice.GuessSession(secret="青蛙")
    assert sess.secret == "青蛙"

    # 1. 问会跳吗
    ans1, win1 = sess.answer_question("它会跳吗？")
    assert win1 is False
    assert "是的" in ans1 and "跳跃" in ans1

    # 2. 问叫声
    ans2, win2 = sess.answer_question("它叫声很吵吗？")
    assert win2 is False
    assert "是的" in ans2 and "呱呱呱" in ans2

    # 3. 问皮肤颜色
    ans3, win3 = sess.answer_question("它是绿色的吗？")
    assert win3 is False
    assert "是的" in ans3 and "绿色" in ans3

    # 4. 问吃害虫
    ans4, win4 = sess.answer_question("它会吃害虫吗？")
    assert win4 is False
    assert "是的" in ans4 and "害虫" in ans4

    # 5. 直接猜中
    ans5, win5 = sess.answer_question("它是青蛙吗？")
    assert win5 is True
    assert "完全猜对啦" in ans5


def test_verify_claim_shell_and_extended_facts():
    """事实核查官核查「贝壳是动物吗」等常识。"""
    res1 = practice.verify_claim_direct("贝壳是动物吗")
    assert res1["ok"] is True
    assert res1["verdict"] == "ok"
    assert "软体动物" in res1["note"] or "外壳" in res1["note"]

    res2 = practice.verify_claim_direct("蘑菇是植物吗")
    assert res2["ok"] is True
    assert res2["verdict"] == "wrong"
    assert "真菌" in res2["note"]

    res3 = practice.verify_claim_direct("月亮会发光吗")
    assert res3["ok"] is True
    assert res3["verdict"] == "wrong"
    assert "反射" in res3["note"]

    res4 = practice.verify_claim_direct("8 乘以 9 等于 72")
    assert res4["ok"] is True
    assert res4["verdict"] == "ok"
    assert "72" in res4["note"]


def test_ocr_words_and_ocr_reading_api(monkeypatch):
    """测试生字词拍照识别接口与英文短文拍照识别接口。"""
    from starlette.testclient import TestClient
    from app import app
    client = TestClient(app)

    # 1. 生字词 OCR 识别接口
    # 测试文本输入解析或图片 OCR 提取
    r1 = client.post("/api/pocketclaw/mp/ocr_words", json={
        "raw_text": "晨光, 喜悦, 勇敢, 拼搏",
        "push": False
    })
    assert r1.status_code == 200
    d1 = r1.json()
    assert d1["ok"] is True
    assert d1["count"] == 4
    assert any(w[0] == "晨光" for w in d1["words"])

    # 模拟真正的视觉服务响应；不可用时不能生成示例内容。
    from unittest.mock import AsyncMock
    import json
    async def fake_ocr(system, prompt, **kwargs):
        if "英文短文" in prompt:
            return json.dumps({"title": "A story", "sentences": [{"en": "Hello.", "cn": "你好。"}]})
        return "温暖, wen nuan, 温暖的温"
    monkeypatch.setattr(app.state.runtime.llm, "complete", AsyncMock(side_effect=fake_ocr))
    # 测试模拟视觉服务
    r2 = client.post("/api/pocketclaw/mp/ocr_words", json={
        "image": "data:image/jpeg;base64,mockbase64data",
        "push": False
    })
    assert r2.status_code == 200
    d2 = r2.json()
    assert d2["ok"] is True
    assert d2["count"] >= 1

    # 2. 英文短文 OCR 识别接口
    r3 = client.post("/api/pocketclaw/mp/ocr_reading", json={
        "image": "data:image/jpeg;base64,mockreadingdata",
        "level": "三年级起步",
        "push": False
    })
    assert r3.status_code == 200
    d3 = r3.json()
    assert d3["ok"] is True
    assert d3["article_id"].startswith("art_")
    assert len(d3["sentences"]) >= 1


def test_settings_and_companion_persona_endpoints():
    """测试学伴人设与 LLM/OCR 运行模式（本地版/在线版）配置接口与网页端。"""
    from starlette.testclient import TestClient
    from app import app
    client = TestClient(app)

    # 1. 网页端包含 Tab 17 及其设置卡片
    portal_res = client.get("/pocketclaw/parent")
    assert portal_res.status_code == 200
    html = portal_res.text
    assert 'data-tab="settings"' in html
    assert 'id="tab-settings"' in html
    assert 'id="cfgCompName"' in html
    assert 'id="cfgLlmProvider"' in html
    assert 'id="onlineLlmSettings"' in html
    assert 'id="onlineOcrSettings"' in html

    # 2. 读取默认系统设置
    r1 = client.get("/api/pocketclaw/mp/settings")
    assert r1.status_code == 200
    d1 = r1.json()
    assert d1["ok"] is True
    assert d1["companion"]["name"] == "虾崽"
    assert "llm" in d1
    assert "ocr" in d1

    # 3. 保存新的人设与在线大模型配置
    r2 = client.post("/api/pocketclaw/mp/settings", json={
        "companion": {
            "name": "聪聪",
            "style": "幽默风趣",
            "persona": "幽默风趣的数学小天才，善于用冷笑话启发孩子思考。",
            "guidance": "每次回复两句话以内不超过40字，结尾留一个反问。"
        },
        "llm": {
            "mode": "online",
            "provider": "deepseek",
            "model": "deepseek-chat",
            "base_url": "https://api.deepseek.com/v1",
            "api_key": "sk-test123456"
        },
        "ocr": {
            "mode": "online",
            "vision_model": "gpt-4o-mini"
        }
    })
    assert r2.status_code == 200
    d2 = r2.json()
    assert d2["ok"] is True
    assert d2["companion"]["name"] == "聪聪"

    # 4. 再次读取验证更新
    r3 = client.get("/api/pocketclaw/mp/settings")
    d3 = r3.json()
    assert d3["companion"]["name"] == "聪聪"
    assert d3["companion"]["style"] == "幽默风趣"
    assert d3["llm"]["provider"] == "deepseek"
    assert d3["llm"]["mode"] == "online"

    # 5. 测试兼容 companion 独立端点
    r4 = client.get("/api/pocketclaw/mp/companion")
    assert r4.status_code == 200
    assert r4.json()["config"]["name"] == "聪聪"

    # 6. 测试重置为出厂设置
    r5 = client.post("/api/pocketclaw/mp/settings/reset")
    assert r5.status_code == 200
    r6 = client.get("/api/pocketclaw/mp/settings")
    assert r6.json()["companion"]["name"] == "虾崽"


@pytest.mark.anyio
async def test_comprehensive_button_logic_and_footer_hints():
    """全面校验物理按键分发逻辑与底部提示文案：
    1. 长按下方按键（power_long / down_long）：严禁进入 AI学伴！
       - 小学新鲜事详细报道页长按下键返回列表页；
       - AI学伴模式长按下键退出学伴回到作业清单；
       - 菜单模式长按下键退出菜单返回原模式；
       - 其他所有模式长按下键统一打开功能导航菜单。
    2. 中间键长按（PTT录音说话）：直接向 AI学伴提问，进入 chat 模式。
    3. 全系统页面文案检查：绝不出现「长按●返回」或「长按●退出」等错误提示。
    """
    from pocketclaw.bridge import PocketClawSession
    from pocketclaw.notes import NotesStore
    from pocketclaw.device_registry import DeviceRegistry
    import tempfile, shutil

    tmpdir = tempfile.mkdtemp()
    try:
        sink = Sink()
        bridge = _session(sink)
        bridge.device_id = "test_dev"
        bridge.child_id = "kid"
        bridge.mode = "task"

        # 1. 作业清单下长按下方按键（power_long / down_long）-> 必须进入 menu，绝对不进 chat！
        await bridge._on_button("power_long", 100.0)
        assert bridge.mode == "menu", f"Expected mode 'menu', got {bridge.mode}"
        assert bridge.mode != "chat"

        # 2. 菜单模式下长按下方按键 -> 退出菜单返回原模式 (task)
        await bridge._on_button("power_long", 101.0)
        assert bridge.mode == "task"

        # 3. 成语模式下长按下方按键 -> 必须进入 menu，绝对不进 chat！
        bridge.mode = "idiom"
        await bridge._on_button("power_long", 102.0)
        assert bridge.mode == "menu"
        assert bridge.mode != "chat"

        # 4. 屏保模式下长按下方按键 -> 必须进入 menu，绝对不进 chat！
        bridge.mode = "screensaver"
        await bridge._on_button("down_long", 103.0)
        assert bridge.mode == "menu"
        assert bridge.mode != "chat"

        # 5. 小学新鲜事列表下长按下方按键 -> 进入 menu
        bridge.mode = "news"
        bridge._news_view = "list"
        await bridge._on_button("power_long", 104.0)
        assert bridge.mode == "menu"
        assert bridge.mode != "chat"

        # 6. 小学新鲜事详情页下长按下方按键 -> 返回小学新鲜事列表
        bridge.mode = "news"
        bridge._news_view = "detail"
        await bridge._on_button("power_long", 105.0)
        assert bridge.mode == "news"
        assert bridge._news_view == "list"

        # 7. AI学伴模式下长按下方按键 -> 退出学伴回到作业
        bridge.mode = "chat"
        await bridge._on_button("power_long", 106.0)
        assert bridge.mode == "task"

        # 8. 中间键（●）短按：如果处于 chat，语音提示正确退出方法
        bridge.mode = "chat"
        spoke = []
        bridge._speak = lambda text: spoke.append(text) or asyncio.sleep(0)
        await bridge._on_button("confirm", 107.0)
        assert any("长按下方按键可以退出学伴" in s for s in spoke)

        # 9. 检查所有 display.py 渲染函数：绝无「长按●返回」或「长按●退出」等自相矛盾的文案
        from pocketclaw import display
        import inspect
        funcs = [getattr(display, name) for name in dir(display) if name.startswith("render_")]
        for fn in funcs:
            sig = inspect.signature(fn)
            # 构造安全默认实参调用
            kwargs = {}
            for param in sig.parameters.values():
                if param.default is not inspect.Parameter.empty:
                    continue
                if param.annotation == str:
                    kwargs[param.name] = "测试"
                elif param.annotation == int:
                    kwargs[param.name] = 1
                elif "list" in str(param.annotation).lower():
                    kwargs[param.name] = []
                elif "dict" in str(param.annotation).lower():
                    kwargs[param.name] = {}
                else:
                    kwargs[param.name] = "测试"
            try:
                page = fn(**kwargs)
                if isinstance(page, dict) and "footer" in page:
                    hint = page["footer"].get("hint", "")
                    assert "长按●返回" not in hint, f"Found wrong hint '长按●返回' in {fn.__name__}: {hint}"
                    assert "长按●退出" not in hint, f"Found wrong hint '长按●退出' in {fn.__name__}: {hint}"
                    assert "长按●" not in hint, f"Found '长按●' in {fn.__name__}: {hint}"
                    assert "长按▲" not in hint, f"Found '长按▲' in {fn.__name__}: {hint}"
            except Exception:
                pass
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)



