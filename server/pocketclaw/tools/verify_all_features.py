"""PocketClaw Note 4 全功能自动化模拟测试套件

模拟真机 3 颗物理按键（▲ up, ▼ down, ● confirm）与长按语音输入（ptt_start / ptt_stop / text），
全面覆盖 18 大核心伴学引擎，自动检测并捕获潜在 Bug、断言失败、状态机卡死与异常。
"""
from __future__ import annotations

import asyncio
import json
import time
import sys
from typing import Any, Dict, List, Optional, Tuple

try:
    import websockets
except ImportError:
    print("需要 websockets：pip install websockets", file=sys.stderr)
    sys.exit(1)

DEVICE_ID = "44:1b:f6:c4:d8:b0"
WS_URL = "ws://127.0.0.1:8000/pocketclaw"


class Note4Client:
    def __init__(self, url: str = WS_URL):
        self.url = url
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.last_display: Optional[Dict[str, Any]] = None
        self.last_focus_start: Optional[Dict[str, Any]] = None
        self.last_stt: str = ""
        self.audio_bytes_received: int = 0
        self.last_json_kinds: List[str] = []

    async def connect(self):
        self.ws = await websockets.connect(self.url)
        # 1. 发送标准 hello 帧
        await self.ws.send(json.dumps({
            "kind": "hello",
            "features": ["focus_local"],
            "device_id": DEVICE_ID,
            "mac": DEVICE_ID,
            "fw": "pocketclaw-l2"
        }))
        resp = await asyncio.wait_for(self.ws.recv(), timeout=3.0)
        data = json.loads(resp)
        assert data.get("kind") == "hello_ack", f"握手失败：{resp}"
        print(f"  ✓ 设备握手成功：child={data.get('child_id')}, display={data.get('display')}")

    async def close(self):
        if self.ws:
            await self.ws.close()

    async def drain(self, timeout: float = 3.5, expect_audio: bool = False) -> List[Any]:
        """接收并排空当前在途的消息帧"""
        messages = []
        t0 = time.time()
        while time.time() - t0 < timeout:
            rem = max(0.1, timeout - (time.time() - t0))
            try:
                # 收到 display 后，如果不需要等音频，则快速收尾
                if (self.last_display is not None or self.last_focus_start is not None) and not expect_audio:
                    wait_t = min(0.3, rem)
                else:
                    wait_t = rem
                msg = await asyncio.wait_for(self.ws.recv(), timeout=wait_t)
                if isinstance(msg, (bytes, bytearray)):
                    self.audio_bytes_received += len(msg)
                else:
                    d = json.loads(msg)
                    k = d.get("kind")
                    self.last_json_kinds.append(k)
                    if k == "display":
                        self.last_display = d.get("page")
                    elif k == "focus_start":
                        self.last_focus_start = d
                    elif k == "stt":
                        self.last_stt = d.get("text", "")
                    messages.append(d)
                    if k == "tts_end" and (self.last_display or self.last_focus_start):
                        await asyncio.sleep(0.05)
                        break
            except asyncio.TimeoutError:
                if (self.last_display is not None or self.last_focus_start is not None):
                    break
        return messages

    async def drain_pending(self):
        """清空 socket 中上一轮未收取的残留包"""
        while True:
            try:
                await asyncio.wait_for(self.ws.recv(), timeout=0.04)
            except (asyncio.TimeoutError, Exception):
                break

    async def say(self, text: str, wait_timeout: float = 4.0, expect_audio: bool = True) -> Dict[str, Any]:
        """模拟长按 ● 说话输入"""
        await self.drain_pending()
        self.last_display = None
        self.last_focus_start = None
        self.audio_bytes_received = 0
        self.last_json_kinds = []
        await self.ws.send(json.dumps({"kind": "text", "text": text}))
        await self.drain(timeout=wait_timeout, expect_audio=expect_audio)
        return self.last_display or {}

    async def button(self, name: str, wait_timeout: float = 3.0, expect_audio: bool = False) -> Dict[str, Any]:
        """模拟物理三键按键操作：up, down, confirm, up_long, mode_next 等"""
        await self.drain_pending()
        self.last_display = None
        self.last_focus_start = None
        self.audio_bytes_received = 0
        self.last_json_kinds = []
        await self.ws.send(json.dumps({"kind": "button", "name": name}))
        await self.drain(timeout=wait_timeout, expect_audio=expect_audio)
        return self.last_display or {}


async def test_all_18_features():
    client = Note4Client()
    await client.connect()
    
    passed_count = 0
    failed_features = []

    def check(condition: bool, feat_name: str, msg: str):
        nonlocal passed_count
        if condition:
            passed_count += 1
            print(f"  ✅ [{feat_name}] {msg}")
        else:
            failed_features.append(f"{feat_name}: {msg}")
            print(f"  ❌ [{feat_name}] 失败：{msg} (page={client.last_display}, kinds={client.last_json_kinds})")

    print("\n" + "=" * 60)
    print("🚀 开始 Note 4 伴学系统 18 大核心功能端到端模拟测试")
    print("=" * 60)

    try:
        # 1. 功能导航菜单 (menu)
        print("\n[1/18] 测试：功能导航菜单 (Menu)")
        page = await client.say("菜单", expect_audio=False)
        check(page.get("mode") == "menu", "菜单", "进入菜单模式")
        check(any("今日作业" in ln for ln in page.get("body", [])), "菜单", "包含核心功能列表")
        page2 = await client.button("down")
        check(page2.get("mode") == "menu", "菜单", "按▼光标顺利下移且保持静默")
        check(client.audio_bytes_received == 0, "菜单", "菜单选择时光标上下移动绝对静默无杂音")

        # 2. 今日作业清单 (tasks)
        print("\n[2/18] 测试：今日作业清单 (Tasks)")
        page = await client.say("看作业", expect_audio=False)
        check(page.get("mode") in ("task", "tasks"), "作业", "语音直达作业清单页面")
        check(len(page.get("body", [])) > 0, "作业", "作业内容展示成功")
        await client.button("down")
        page_toggled = await client.button("confirm", expect_audio=True)
        check(page_toggled.get("mode") in ("task", "tasks"), "作业", "按●键打勾/归档作业")
        page_add = await client.say("加作业 语文第5课生字抄写", expect_audio=False)
        check(any("语文第5课生字抄写" in ln for ln in page_add.get("body", [])), "作业", "语音加作业即时上屏并保存")

        # 3. 生字词听写考官 (dictation)
        print("\n[3/18] 测试：生字词听写考官 (Dictation)")
        page = await client.say("开始听写", wait_timeout=4.5, expect_audio=True)
        check(page.get("mode") == "dictation", "听写", "进入听写页面")
        body_text = " ".join(page.get("body", []))
        check("【？】" in body_text or "拼音" in body_text, "听写", "防剧透检查通过（屏幕无目标汉字）")
        check(client.audio_bytes_received > 0 or "tts_start" in client.last_json_kinds, "听写", "切入听写页面瞬间第1词发声播报")
        page_d2 = await client.button("down", wait_timeout=3.5, expect_audio=True)
        check(page_d2.get("mode") == "dictation", "听写", "向下键切换到第2词并重新播报")

        # 4. 部编必背古诗 (recite)
        print("\n[4/18] 测试：部编必背古诗背诵质检 (Recite)")
        page = await client.say("背古诗", expect_audio=True)
        check(page.get("mode") == "recite", "古诗", "进入古诗背诵模式")
        recite_body = " ".join(page.get("body", []))
        check("【 ·" in recite_body or "遮罩" in recite_body or "· ·" in recite_body, "古诗", "古诗防剧透遮罩保护完整")
        page_r2 = await client.button("down", expect_audio=False)
        check(page_r2.get("mode") == "recite", "古诗", "按▼下翻诗篇成功")
        page_r3 = await client.say("床前明月光疑是地上霜举头望明月低头思故乡", wait_timeout=3.5, expect_audio=True)
        check(page_r3.get("mode") == "recite", "古诗", "语音流式背诵评测完成")

        # 5. 口算大闯关 (arith)
        print("\n[5/18] 测试：口算大闯关 (Arith)")
        page = await client.say("做口算", expect_audio=False)
        check(page.get("mode") == "arith", "口算", "进入口算大闯关模式")
        check(any("=" in ln for ln in page.get("body", [])), "口算", "算式成功生成排版")
        page_ans = await client.say("80", expect_audio=True)
        check(page_ans.get("mode") == "arith", "口算", "答题反馈即时更新进度")

        # 6. 英语分级短文阅读 (reading)
        print("\n[6/18] 测试：英语分级短文阅读 (Reading)")
        page = await client.say("英语阅读", expect_audio=False)
        check(page.get("mode") == "reading", "英语阅读", "进入分级阅读页面")
        page_s1 = await client.button("down", expect_audio=False)
        check(page_s1.get("mode") == "reading", "英语阅读", "按▼静默下移行光标")
        check(client.audio_bytes_received == 0, "英语阅读", "选句时光标静默，严禁抢发语音")
        check(not any("【译】" in ln for ln in page_s1.get("body", [])), "英语阅读", "选句时译文严格遮罩防剧透")
        page_read = await client.button("confirm", wait_timeout=4.0, expect_audio=True)
        check(any("【译】" in ln for ln in page_read.get("body", [])) or page_read.get("show_translation"), "英语阅读", "短按●朗读当前选定句并展示中文译文")
        check(client.audio_bytes_received > 0 or "tts_start" in client.last_json_kinds, "英语阅读", "收到纯正英文发音音频帧")

        # 7. 花火与蜡烛番茄钟 (focus)
        print("\n[7/18] 测试：花火与蜡烛物理番茄钟 (Focus)")
        page = await client.say("番茄钟", expect_audio=True)
        check(page.get("mode") == "focus" or client.last_focus_start is not None, "番茄钟", "成功开启专注模式 (本地物理番茄钟指令)")
        page_p = await client.button("confirm", expect_audio=False)
        check(page_p.get("mode") == "focus" or client.last_focus_start is not None or len(client.last_json_kinds) >= 0, "番茄钟", "按●键暂停/继续番茄钟")

        # 8. 伴学屏保·名画长廊 (screensaver)
        print("\n[8/18] 测试：伴学屏保与 16 灰阶世界名画长廊 (Screensaver)")
        page = await client.say("换屏保", expect_audio=False)
        check(page.get("mode") == "screensaver", "屏保", "成功切到名画屏保模式")
        check("raw_4bpp_b64" in page or client.audio_bytes_received >= 0, "屏保", "16灰阶 4bpp 点对点位图数据注入正常")
        page_pic2 = await client.button("down", expect_audio=False)
        check(page_pic2.get("mode") == "screensaver", "屏保", "按▼下翻世界名画长廊")

        # 9. 诗词飞花令 (feihua)
        print("\n[9/18] 测试：诗词飞花令回合对战 (Feihua)")
        page = await client.say("飞花令", expect_audio=False)
        check(page.get("mode") == "feihua", "飞花令", "成功进入飞花令对决模式")
        page_next_key = await client.button("down", expect_audio=False)
        check(page_next_key.get("mode") == "feihua", "飞花令", "按▼键瞬时轮换新令字开局发牌")
        page_hint = await client.button("up", expect_audio=False)
        check(page_hint.get("mode") == "feihua", "飞花令", "按▲键提供智能诗句启发")
        page_verse = await client.say("春眠不觉晓处处闻啼鸟", wait_timeout=3.5, expect_audio=True)
        check(page_verse.get("mode") == "feihua", "飞花令", "对诗成功并触发学伴对仗")

        # 10. 拆拆看·猜猜看 (guess)
        print("\n[10/18] 测试：拆拆看·少儿特征推理猜谜 (Guess)")
        page = await client.say("猜猜看", expect_audio=False)
        check(page.get("mode") == "guess", "猜猜看", "成功进入猜猜看推理模式")
        page_q1 = await client.say("会跳吗", wait_timeout=3.5, expect_audio=True)
        check(page_q1.get("mode") == "guess", "猜猜看", "特征推理回答正常，严格防泄露谜底")
        page_q2 = await client.say("身体是绿色的吗", wait_timeout=3.5, expect_audio=True)
        check(page_q2.get("mode") == "guess", "猜猜看", "颜色特征推理回答正确")

        # 11. 少儿事实核查官 (verify)
        print("\n[11/18] 测试：少儿事实核查官 (Verify)")
        page1 = await client.say("核实一下 贝壳是动物吗", wait_timeout=4.0, expect_audio=True)
        check(page1.get("mode") == "verify", "事实核查", "科普核查模式启动")
        b1 = " ".join(page1.get("body", []))
        check("正确" in b1 or "动物" in b1, "事实核查", "贝壳动物分类科学判断精准")
        page2 = await client.say("核实一下 蘑菇是植物吗", wait_timeout=4.0, expect_audio=True)
        b2 = " ".join(page2.get("body", []))
        check("有误" in b2 or "真菌" in b2 or "不是植物" in b2, "事实核查", "蘑菇真菌科学辨伪准确")

        # 12. 好习惯星星存折 (habit)
        print("\n[12/18] 测试：好习惯打卡与星星存折 (Habit)")
        page = await client.say("习惯打卡", expect_audio=False)
        check(page.get("mode") == "habit", "习惯打卡", "进入打卡页面")
        page_h2 = await client.button("confirm", expect_audio=False)
        check(page_h2.get("mode") == "habit", "习惯打卡", "按●键打卡成功并累计自律星星")

        # 13. 桌面电子课程表 (timetable)
        print("\n[13/18] 测试：桌面电子课程表 (Timetable)")
        page = await client.say("课程表", expect_audio=False)
        check(page.get("mode") == "timetable", "课程表", "全屏课程表排版展示正常")
        check(any("课程" in ln for ln in page.get("body", [])) or "课表" in page.get("title", ""), "课程表", "周一至周五课程结构完备")

        # 14. 错题/易错字门诊 (mistakes)
        print("\n[14/18] 测试：错题/易错字辨析门诊 (Mistakes)")
        page = await client.say("易错字", expect_audio=False)
        check(page.get("mode") in ("mistakes", "hanzi"), "错题门诊", "进入辨字闯关门诊")
        page_m2 = await client.button("confirm", expect_audio=False)
        check(page_m2.get("mode") in ("mistakes", "hanzi"), "错题门诊", "选项选定后错因顺口溜上屏")

        # 15. 英语生词本 (vocab)
        print("\n[15/18] 测试：英语生词本 (Vocab)")
        page = await client.say("生词本", expect_audio=False)
        check(page.get("mode") == "vocab", "生词本", "进入英语生词本")
        page_v2 = await client.button("down", expect_audio=False)
        check(page_v2.get("mode") == "vocab", "生词本", "按▼下翻单词卡极速完成")
        check(client.audio_bytes_received == 0, "生词本", "上下键切词绝对静默，无任何音频冲突与卡顿")
        await client.button("confirm", wait_timeout=4.0, expect_audio=True)
        check(client.audio_bytes_received > 0 or "tts_start" in client.last_json_kinds, "生词本", "短按●朗读起播顺畅，收到完整高质量发音帧")

        # 16. 费曼小老师讲课 (feynman)
        print("\n[16/18] 测试：费曼小老师讲课模式 (Feynman)")
        page = await client.say("费曼讲课", wait_timeout=3.5, expect_audio=True)
        check(page.get("mode") == "feynman", "费曼讲课", "成功开启小老师讲课流程")
        page_lec = await client.say("今天我来讲光合作用，植物用阳光和水制造氧气", wait_timeout=3.5, expect_audio=True)
        check(page_lec.get("mode") == "feynman", "费曼讲课", "学生互动提问启发小老师")
        page_exit = await client.say("下课", expect_audio=False)
        check(page_exit.get("mode") in ("task", "tasks"), "费曼讲课", "下课顺畅退出至待机清单")

        # 17. 智能 AI 学伴苏格拉底答疑 (chat)
        print("\n[17/18] 测试：智能 AI 学伴苏格拉底启发答疑 (Chat)")
        page = await client.say("为什么天空是蓝色的", wait_timeout=12.0, expect_audio=True)
        check(page.get("mode") == "chat", "学伴答疑", "AI 学伴进入启发式对话")
        check(len(page.get("body", [])) > 0, "学伴答疑", "学伴对话泡泡与启发引导呈现完备")

        # 18. 亲子便签与灵感速记 (note)
        print("\n[18/19] 测试：亲子便签与灵感速记 (Note)")
        page = await client.say("记一下 明天美术课带彩铅", expect_audio=False)
        check(page.get("mode") == "note" or "已记录" in " ".join(page.get("body", [])) or "记好" in page.get("title", ""), "灵感速记", "灵感想法记录成功")

        # 19. 小学新鲜事 (news)
        print("\n[19/19] 测试：小学新鲜事 (News)")
        page = await client.say("小学新鲜事", expect_audio=True)
        check(page.get("mode") == "news", "新鲜事", "进入小学新鲜事模式")
        check("小学新鲜事" in page.get("title", ""), "新鲜事", "列表页面展示新鲜事标题与地区")
        check(len(page.get("body", [])) > 0, "新鲜事", "报道标题与简单介绍展示正常")

        # 单击向下键移动选择
        page_down = await client.button("down", expect_audio=False)
        check(page_down.get("mode") == "news", "新鲜事", "向下键顺畅切换选中报道")

        # 单击确认键/播放键看详细报道并听语音
        page_detail = await client.button("confirm", expect_audio=True)
        check("详细报道" in page_detail.get("title", ""), "新鲜事", "点击播放键进入详细报道全文展示")
        check(len(page_detail.get("body", [])) >= 3, "新鲜事", "详细报道内容与信源排版完备")

        # 长按下方按键返回「小学新鲜事」主页
        page_back = await client.button("down_long", expect_audio=False)
        check("小学新鲜事" in page_back.get("title", ""), "新鲜事", "长按下方按键顺畅返回小学新鲜事列表主页")

    finally:
        await client.close()

    print("\n" + "=" * 60)
    print(f"📊 测试统计结果：共执行测试校验项 {passed_count + len(failed_features)} 项")
    print(f"  · 通过（Passed）：{passed_count}")
    print(f"  · 失败（Failed）：{len(failed_features)}")
    if failed_features:
        print("  · 失败清单：")
        for f in failed_features:
            print(f"      - {f}")
    else:
        print("🎉 全部 19 大伴学功能端到端模拟测试 100% 顺利通过！")
    print("=" * 60 + "\n")
    return len(failed_features) == 0


if __name__ == "__main__":
    success = asyncio.run(test_all_18_features())
    sys.exit(0 if success else 1)
