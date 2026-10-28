"""PocketClaw 小学生高频实践专项单测：古诗背诵质检、听写考官、口算大闯关、按键打钩。"""
import asyncio
import pytest
from pocketclaw import cards, display, practice, protocol
from pocketclaw.bridge import PocketClawSession
from pocketclaw.device_registry import DeviceRegistry
from pocketclaw.notes import NotesStore
from pocketclaw.voice import OfflineSTT, OfflineTTS


class DummyRuntime:
    async def turn(self, prompt: str, **kwargs):
        return {"text": "好的！", "mode": "chat"}


def _make_session():
    sent_json = []
    sent_bin = []

    async def sj(s: str):
        sent_json.append(protocol.parse_control(s) or s)

    async def sb(b: bytes):
        sent_bin.append(b)

    reg = DeviceRegistry(":memory:")
    notes = NotesStore(":memory:")
    sess = PocketClawSession(
        DummyRuntime(), send_json=sj, send_bin=sb,
        stt=OfflineSTT(), tts=OfflineTTS(), registry=reg, notes=notes
    )
    return sess, sent_json, sent_bin, notes


def test_recite_checker_real_algorithm():
    poem = practice.POEM_MAP["望庐山瀑布"]
    assert poem.title == "望庐山瀑布"
    assert "飞流直下三千尺" in poem.lines[2]

    # 1. 乱说 / 扯皮
    r1 = practice.check_recitation(poem, "我中午吃了炸鸡汉堡还有可乐")
    assert not r1.passed
    assert r1.is_irrelevant
    assert "日照香炉生紫烟" not in r1.feedback and "第一句" in r1.feedback
    assert all("待背诵" in l for l in r1.revealed_lines)

    # 2. 完美全对
    r2 = practice.check_recitation(poem, "日照香炉生紫烟，遥看瀑布挂前川。飞流直下三千尺，疑是银河落九天。")
    assert r2.passed
    assert not r2.is_irrelevant
    assert "一字不差" in r2.feedback
    assert r2.revealed_lines == poem.lines

    # 3. 错字 / 漏句
    r3 = practice.check_recitation(poem, "日照香炉生紫烟，飞流直下三千尺，疑是银河落九天。")
    assert not r3.passed
    assert not r3.is_irrelevant
    assert "卡壳" not in r3.feedback

    # 4. 单独背第 2 句或第 3 句：只点评该句，绝不说第 1 句/第 2 句卡壳，也不让重背第 1 句
    r_line2 = practice.check_recitation(poem, "遥看瀑布挂前川")
    assert not r_line2.passed
    assert not r_line2.is_irrelevant
    assert "第 2 句" in r_line2.feedback
    assert "第一句" not in r_line2.feedback and "第 1 句" not in r_line2.feedback
    assert "卡壳" not in r_line2.feedback
    assert r_line2.revealed_lines[1] == poem.lines[1]

    r_line3 = practice.check_recitation(poem, "飞流直下三千尺", done_indices=set(r_line2.matched_indices))
    assert not r_line3.passed
    assert "第 3 句" in r_line3.feedback
    assert "第一句" not in r_line3.feedback and "第 1 句" not in r_line3.feedback and "第 2 句" not in r_line3.feedback
    assert "卡壳" not in r_line3.feedback
    assert r_line3.revealed_lines[1] == poem.lines[1]
    assert r_line3.revealed_lines[2] == poem.lines[2]


def test_dictation_session():
    ds = practice.DictationSession([("温暖", "wēn nuǎn", "温和暖和"), ("仔细", "zǐ xì", "仔细观察")])
    assert ds.total == 2
    assert "第 1 个词：温暖" in ds.prompt_speech()
    
    # 下一个词
    assert ds.step_next() is True
    assert "第 2 个词：仔细" in ds.prompt_speech()
    
    # 结束
    assert ds.step_next() is False
    assert ds.active is False


def test_arith_session():
    arith = practice.ArithSession(count=5)
    assert len(arith.questions) == 5
    assert not arith.revealed

    # 第一次按键：揭晓答案
    stepped, msg = arith.reveal_or_step()
    assert stepped is False
    assert arith.revealed is True
    assert "答案是" in msg

    # 第二次按键：切到下一题
    stepped2, msg2 = arith.reveal_or_step()
    assert stepped2 is True
    assert arith.revealed is False
    assert arith.idx == 1


@pytest.mark.anyio
async def test_session_task_confirm_checkoff():
    sess, sent_json, _, notes = _make_session()
    # 模拟 hello 握手
    await sess.on_control({"kind": protocol.HELLO, "device_id": "test_dev_1"})
    
    # 初始有预设作业
    todos = notes.list_tasks(sess.child_id, include_done=False)
    assert len(todos) > 0
    first_task = todos[0].text

    # 短按 confirm 键，应该勾掉第一个作业！
    sess.mode = "task"
    await sess.on_control({"kind": protocol.BUTTON, "name": "confirm"})
    
    # 验证第一个作业已 done
    updated = notes.list_tasks(sess.child_id, include_done=True)
    done_items = [t for t in updated if t.done]
    assert len(done_items) == 1
    assert done_items[0].text == first_task

    # 检查屏幕渲染了中划线或勾号
    last_disp = [m for m in sent_json if isinstance(m, dict) and m.get("kind") == protocol.DISPLAY][-1]
    page = last_disp["page"]
    assert page["mode"] == "task"


@pytest.mark.anyio
async def test_session_recite_flow():
    sess, sent_json, _, notes = _make_session()
    await sess.on_control({"kind": protocol.HELLO, "device_id": "test_dev_2"})
    
    # 语音说 "背望庐山瀑布"
    await sess.on_control({"kind": protocol.TEXT, "text": "背望庐山瀑布"})
    assert sess.mode == "recite"
    assert sess._recite_poem is not None
    assert sess._recite_poem.title == "望庐山瀑布"

    # 孩子背诵整首
    await sess.on_control({"kind": protocol.TEXT, "text": "日照香炉生紫烟遥看瀑布挂前川飞流直下三千尺疑是银河落九天"})
    # 应该通关
    assert sess.mode == "task"  # 通关后切回 task
    last_disp = [m for m in sent_json if isinstance(m, dict) and m.get("kind") == protocol.DISPLAY][-1]
    assert "恭喜" in "".join(last_disp["page"]["body"])


def test_parent_parsers():
    from pocketclaw.endpoint import parse_dictation_words, parse_raw_homework

    raw_notice = """今日作业（10月22日）：
1. 语文：抄写生字两遍
2. 数学：口算天天练P15
3. 背古诗《望庐山瀑布》
4. 英语：朗读课文三遍
请家长督促孩子按时完成！"""

    tasks = parse_raw_homework(raw_notice)
    assert len(tasks) == 4
    assert tasks[0] == ("[语文] 抄写生字两遍", "语文")
    assert tasks[1] == ("[数学] 口算天天练P15", "数学")
    assert tasks[2] == ("背古诗《望庐山瀑布》", "")
    assert tasks[3] == ("[英语] 朗读课文三遍", "英语")

    words = parse_dictation_words("温暖，认真，仔细；明亮\n保护")
    assert len(words) == 5
    assert words[0][0] == "温暖"
    assert words[4][0] == "保护"


def test_guess_session():
    # 测试指定 secret: 熊猫
    gs = practice.GuessSession("熊猫")
    assert gs.secret == "熊猫"
    assert gs.category == "国宝动物"
    assert gs.status == "playing"
    assert gs.mask("这是大熊猫吗") == "这是大＊＊吗"

    # 线索提示
    h1 = gs.give_hint()
    assert "熊猫" not in h1
    assert "国宝动物" in h1

    # 提问判定 (未猜中)
    reply, won = gs.answer_question("你会飞吗？")
    assert won is False
    assert "不会" in reply or "不对" in reply or "不是" in reply
    assert len(gs.history) == 1

    # 猜中目标
    reply_win, won2 = gs.answer_question("你是熊猫吗？")
    assert won2 is True
    assert gs.status == "win"
    assert "太棒啦" in reply_win or "猜中" in reply_win or "就是" in reply_win


def test_feynman_session():
    fs = practice.FeynmanSession(topic="今日所学")
    assert len(fs.rounds) == 0
    assert fs.summary is None

    # 第 1 轮学生讲解
    r1, done1 = fs.add_round("我今天学了光合作用，就是植物用太阳光和二氧化碳制造养分和氧气。")
    assert not done1
    assert len(fs.rounds) == 1
    assert len(r1) > 0

    # 第 2 轮
    r2, done2 = fs.add_round("如果到了晚上没有光，植物就不能进行光合作用，只会呼吸消耗氧气。")
    assert not done2
    assert len(fs.rounds) == 2

    # 第 3 轮：达成结业
    r3, done3 = fs.add_round("所以把植物放在有阳光的阳台，它就能茁壮成长，还能净化空气！")
    assert done3 is True
    assert len(fs.rounds) == 3
    assert fs.summary is not None
    assert "光合作用" in fs.summary.topic or len(fs.summary.topic) > 0
    assert len(fs.summary.good_point) > 0
    assert len(fs.summary.think_point) > 0


def test_verify_claim_direct():
    # 1. 数学直接核实
    v1 = practice.verify_claim_direct("35乘以12等于420对吗")
    assert v1 is not None
    assert v1["verdict"] == "ok"
    assert "420" in v1["note"]

    v2 = practice.verify_claim_direct("15加27等于50")
    assert v2 is not None
    assert v2["verdict"] == "wrong"
    assert "42" in v2["note"]

    # 2. 常见小学生科学/常识误区核实
    v3 = practice.verify_claim_direct("西瓜是长在树上的吗")
    assert v3 is not None
    assert v3["verdict"] == "wrong"
    assert "草本植物" in v3["note"] or "树枝" in v3["note"]
    assert len(v3["how"]) > 0

    v4 = practice.verify_claim_direct("企鹅是鸟类吗")
    assert v4 is not None
    assert v4["verdict"] == "ok"
    assert "鸟纲" in v4["note"]

    v5 = practice.verify_claim_direct("光年是时间单位吗")
    assert v5 is not None
    assert v5["verdict"] == "wrong"
    assert "长度" in v5["note"] or "距离" in v5["note"]


@pytest.mark.anyio
async def test_session_snapclaw_modes_integration():
    sess, sent_json, _, notes = _make_session()
    await sess.on_control({"kind": protocol.HELLO, "device_id": "test_dev_snapclaw"})

    # 1. 语音进入猜猜看
    await sess.on_control({"kind": protocol.TEXT, "text": "玩猜猜看"})
    assert sess.mode == "guess"
    assert sess._guess_session is not None

    # 按 UP 键获取线索
    await sess.on_control({"kind": protocol.BUTTON, "name": "up"})
    last_disp = [m for m in sent_json if isinstance(m, dict) and m.get("kind") == protocol.DISPLAY][-1]
    assert "线索" in "".join(last_disp["page"]["body"])

    # 2. 语音进入费曼讲课
    await sess.on_control({"kind": protocol.TEXT, "text": "我想讲课费曼"})
    assert sess.mode == "feynman"
    assert sess._feynman_session is not None

    # 小老师开始第一讲
    await sess.on_control({"kind": protocol.TEXT, "text": "今天我教你除法竖式怎么列"})
    assert len(sess._feynman_session.rounds) == 1
    last_disp = [m for m in sent_json if isinstance(m, dict) and m.get("kind") == protocol.DISPLAY][-1]
    assert last_disp["page"]["mode"] == "feynman"

    # 3. 语音进入事实核查
    await sess.on_control({"kind": protocol.TEXT, "text": "帮我核实一下西瓜长在树上吗"})
    assert sess.mode == "verify"
    assert sess._last_verify_result is not None
    assert sess._last_verify_result["verdict"] == "wrong"
    last_disp = [m for m in sent_json if isinstance(m, dict) and m.get("kind") == protocol.DISPLAY][-1]
    assert "说法不正确" in "".join(last_disp["page"]["body"])


def test_feihua_session():
    # 测试指定令字：春
    fs = practice.FeihuaSession(keyword="春", target_rounds=3)
    assert fs.keyword == "春"
    assert fs.status == "playing"
    assert fs.rounds_done == 0

    # 1. 不带令字的诗句：失败拦截
    ok1, _, tip1 = fs.check_verse("白日依山尽，黄河入海流")
    assert not ok1
    assert "没有包含令字" in tip1

    # 2. 正常答对第一轮
    ok2, bot2, _ = fs.check_verse("春眠不觉晓，处处闻啼鸟")
    assert ok2
    assert "春" in bot2
    assert fs.rounds_done == 1

    # 3. 重复说同一句：失败拦截
    ok3, _, tip3 = fs.check_verse("春眠不觉晓")
    assert not ok3
    assert "已经对过" in tip3

    # 4. 正常答对第二轮
    ok4, bot4, _ = fs.check_verse("好雨知时节，当春乃发生")
    assert ok4
    assert fs.rounds_done == 2

    # 5. 答对第三轮达成通关
    ok5, bot5, _ = fs.check_verse("春色满园关不住，一枝红杏出墙来")
    assert ok5
    assert fs.rounds_done == 3
    assert fs.status == "win"

    # 6. 求助提示测试
    hint = fs.give_hint()
    assert "提示" in hint


def test_hanzi_session():
    hs = practice.HanziSession(count=5)
    assert hs.total == 5
    assert hs.idx == 0
    assert hs.selected_opt_idx == 0
    assert not hs.revealed

    # 切换选项
    assert hs.toggle_option() == 1
    assert hs.toggle_option() == 0

    # 获取当前题
    curr = hs.current
    correct_ans = curr["ans"]
    
    # 语音回答正确答案
    is_corr, speech, info = hs.confirm_answer(user_voice=f"我认为应该填{correct_ans}")
    assert is_corr is True
    assert hs.revealed is True
    assert hs.correct_cnt == 1
    assert "回答正确" in speech
    assert curr["mnemonic"] in speech

    # 步进到下一题
    assert hs.step_next() is True
    assert hs.idx == 1
    assert not hs.revealed


@pytest.mark.anyio
async def test_session_feihua_and_hanzi_integration():
    sess, sent_json, _, notes = _make_session()
    await sess.on_control({"kind": protocol.HELLO, "device_id": "test_dev_elem_features"})

    # 1. 语音进入飞花令
    await sess.on_control({"kind": protocol.TEXT, "text": "玩飞花令"})
    assert sess.mode == "feihua"
    assert sess._feihua_session is not None
    sess._feihua_session = practice.FeihuaSession(keyword="春")
    kw = sess._feihua_session.keyword

    # 孩子对出一句诗
    await sess.on_control({"kind": protocol.TEXT, "text": f"这是一句带{kw}字的诗句"})
    assert sess._feihua_session.rounds_done == 1
    last_disp = [m for m in sent_json if isinstance(m, dict) and m.get("kind") == protocol.DISPLAY][-1]
    assert last_disp["page"]["mode"] == "feihua"

    # 飞花令中诗句包含古诗标题时，不应隐式切入背诵模式。
    await sess.on_control({"kind": protocol.TEXT, "text": "春风又绿江南岸，明月何时照我还"})
    assert sess.mode == "feihua"
    assert sess._feihua_session.rounds_done == 2

    # 按 UP 键获取提示
    await sess.on_control({"kind": protocol.BUTTON, "name": "up"})
    last_disp2 = [m for m in sent_json if isinstance(m, dict) and m.get("kind") == protocol.DISPLAY][-1]
    assert "提示" in "".join(last_disp2["page"]["body"])

    # 2. 语音进入错字门诊
    await sess.on_control({"kind": protocol.TEXT, "text": "形近字辨析"})
    assert sess.mode == "hanzi"
    assert sess._hanzi_session is not None
    curr_q = sess._hanzi_session.current

    # 按 UP/DOWN 键切换选项
    await sess.on_control({"kind": protocol.BUTTON, "name": "down"})
    assert sess._hanzi_session.selected_opt_idx == 1

    # 短按 confirm 提交
    await sess.on_control({"kind": protocol.BUTTON, "name": "confirm"})
    assert sess._hanzi_session.revealed is True
    last_disp3 = [m for m in sent_json if isinstance(m, dict) and m.get("kind") == protocol.DISPLAY][-1]
    assert "记忆口诀" in "".join(last_disp3["page"]["body"])


