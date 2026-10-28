"""把服务端一轮 chat_turn 渲染成「墨水屏整屏」结构（DisplayPage）。

墨水屏 400×300 · 黑白 · 刷新慢 → 三条硬约束：
  1. **不逐字流式**：一次给整屏，设备整刷一次。
  2. **黑白**：不下发彩色可视化；svg 图解按「示意」下发，其余能力门已在 bridge 关掉。
  3. **字少**：中文按显示宽度折行（CJK 记 2 列，ASCII 记 1 列），超出的正文尾部省略并给「继续」提示。

DisplayPage 是设备无关的纯数据（dict），设备端 LVGL 拿到后落地成思源黑体页面。

上屏字形（2026-09-28）：所有上屏文本先过 `screen_text`（整页过 `screen_page`）——按固件字库的**真实字符表**清洗，
表外的字替换成子集里的等价写法或去掉。只清上屏，**朗读文本别过它**（TTS 念的是原文）。
"""
from __future__ import annotations

import logging
import time
import unicodedata
from typing import Any, Dict, List, Optional

log = logging.getLogger("pocketclaw")

# ── 上屏字形：按固件字库子集清洗 ─────────────────────────────────────────────
# 字符表是核实过的**真表**，不是猜的。固件正文 24px 用 SourceHanSansSC_Medium_slim（非 ASCII）+ Montserrat 24（ASCII），
# 16px 用 SourceHanSansSC_Regular_slim + Montserrat 16（固件 boards/zectrix-s3-epaper-4.2/custom_lcd_display.cc
# DrawTexts / render_text_to_buffer）。两份 slim 字库的 cmap 已逐条解析（pocketclaw/firmware/zectrix/main/components/
# 78__xiaozhi-fonts/src/SourceHanSansSC_{Medium,Regular}_slim.c，各 6905 个字形、两份完全相同）＝
#   ASCII 可打印 95 个 + 下面 `_SCREEN_SYMBOLS` 的 47 个符号 + GB2312 一、二级汉字 6763 个（与 gb2312 编码表逐字相等）。
# 字库生成参数里点名要过的天气符号（☀☁🌧…）因为源字体里没有，**实际没进字库**；「」『』、✓☑☐、■●━▲▼⚠、
# 全角空格 U+3000、全角波浪 ～、短横 –、一切 emoji 也都不在。表外的字：固件自绘路径跳过、留半个字宽的空白，
# LVGL 控件上画成占位方块（豆腐块）。固件换字库时同步改这里——tests/test_pocketclaw_ownership_2026_09_28.py
# 会重新解析固件字库文件、核对这张表没漂。
_SCREEN_SYMBOLS = "¢£¥°±·×÷—‘’“”…‰€℃℉←↑→↓↔↕↖↗↘↙√∞≈≠≤≥、。《》【】！（），：；？"
SCREEN_GLYPH_COUNT = 6905          # 固件 slim 字库的字形数（cmap 解析结果），SCREEN_CHARSET 必须恰好这么大


def _gb2312_hanzi() -> str:
    """GB2312 一级（16–55 区）+ 二级（56–87 区）汉字，共 6763 个（55 区末尾 5 个空位跳过）。"""
    out = []
    for hi in range(0xB0, 0xF8):
        for lo in range(0xA1, 0xFF):
            try:
                out.append(bytes((hi, lo)).decode("gb2312"))
            except UnicodeDecodeError:
                continue
    return "".join(out)


SCREEN_CHARSET = frozenset(
    [chr(c) for c in range(0x20, 0x7F)] + list(_SCREEN_SYMBOLS) + list(_gb2312_hanzi()))
if len(SCREEN_CHARSET) != SCREEN_GLYPH_COUNT:   # 解释器的 gb2312 码表与固件字库对不上 → 大声说，不静默
    log.error("便利贴上屏字符表 %d 个 ≠ 固件字库 %d 个字形：上屏清洗可能误删 / 漏删，核对 display.SCREEN_CHARSET",
              len(SCREEN_CHARSET), SCREEN_GLYPH_COUNT)

# 表外字符 → 子集里的等价写法（逐条都是「同一个意思换一种子集里有的写法」，不做意译）
_SCREEN_EQUIV = {
    # 引号 / 括号：子集只有 “”‘’《》【】（）——直角引号按大陆排版的对应关系换成弯引号（外层双、内层单）
    "「": "“", "」": "”", "『": "‘", "』": "’", "﹁": "“", "﹂": "”", "﹃": "‘", "﹄": "’",
    "〈": "《", "〉": "》", "〔": "（", "〕": "）", "〖": "【", "〗": "】", "﹝": "（", "﹞": "）",
    # 对号 / 错号：子集里有 √（U+221A，中文「打勾」就写它）与 ×
    "✓": "√", "✔": "√", "☑": "√", "✅": "√",
    "✗": "×", "✘": "×", "☒": "×", "❌": "×", "❎": "×", "✖": "×",
    # 列表圆点 → ·；各种横线 → - / —；中线省略号 → …；箭头变体 → 子集里的箭头
    "•": "·", "‧": "·", "・": "·", "∙": "·", "●": "·", "◦": "·", "‣": "·", "⁃": "·",
    "–": "-", "‒": "-", "−": "-", "‐": "-", "‑": "-", "―": "—", "─": "—", "━": "—", "═": "—", "⋯": "…",
    "⇒": "→", "⟶": "→", "➡": "→", "➔": "→", "⇐": "←", "⬅": "←", "⬆": "↑", "⬇": "↓", "▲": "↑", "▼": "↓", "≦": "≤", "≧": "≥",
    "⁄": "/",                      # 分数斜线：½ 的兼容分解 1⁄2 → 1/2
    "\u3000": "  ",                # 全角空格 → 两个半角空格：折行算宽（2 列）不变，版式对齐不跑
    "\t": " ", " ": "\n", " ": "\n",
}
STRIKE_MARK = "\x01"               # 行首不可见哨兵：固件据此给这一行划删除线（完成的想法），必须原样留着
INVERT_MARK = "\x02"               # 不可见哨兵：固件据此开启黑底白字高亮反色（行首整行高亮或行内句子高亮）
INVERT_END = "\x03"                # 不可见哨兵：固件据此结束黑底白字高亮反色
FONT_16_MARK = "\x04"              # 行首不可见哨兵：固件据此切换为 16px 小字号排版（摘要/长文本）


def _screen_char(ch: str) -> str:
    """一个表外字符 → 子集里的写法；没有等价写法的：字母 / 汉字留同宽空白（前后文别粘成一个词），
    其余（emoji、符号、变体选择符 FE0F、零宽连接符 ZWJ 等格式 / 控制字符）去掉。"""
    rep = _SCREEN_EQUIV.get(ch)
    if rep is not None:
        return rep
    nk = unicodedata.normalize("NFKC", ch)             # 全角 ＡＢＣ１２３、～、①、㎝、™、不换行空格……
    if nk != ch:
        nk = "".join(_SCREEN_EQUIV.get(c, c) for c in nk)
        if all(c in SCREEN_CHARSET for c in nk):
            return nk
    base = "".join(c for c in unicodedata.normalize("NFD", ch) if not unicodedata.combining(c))
    if base and base != ch and all(c in SCREEN_CHARSET for c in base):
        return base                                    # é → e、ǚ → u（去掉附加符号，字母留下）
    if unicodedata.category(ch).startswith("L"):       # 生僻字 / 其它文字的字母：子集里没有，留空白占位
        return " " * _char_cols(ch)
    return ""


def screen_text(text: Any) -> str:
    """上屏前按固件字库清洗一段文字（**只清上屏，朗读文本别过它**）。

    字库里有的字原样留；有等价写法的换掉（「」→“”、✓→√、全角空格 → 两个半角空格…）；其余 emoji / 符号 /
    变体选择符 / 零宽连接符去掉，删完顺手收掉因此多出来的行首、行尾空格与双空格。行首的删除线哨兵 \x01 与高亮哨兵 \x02 / \x03 原样保留。
    幂等：清过的再清一遍不变。"""
    s = "" if text is None else str(text)
    if all(c in SCREEN_CHARSET or c == "\n" or c in (STRIKE_MARK, INVERT_MARK, INVERT_END, FONT_16_MARK) for c in s):
        return s                                       # 快路径：绝大多数回复一个字都不用动
    out: List[str] = []
    dropped = False                                    # 上一个可见字之后删过东西 → 收掉因此多出来的空格
    for ch in s:
        if ch in SCREEN_CHARSET or ch == "\n":
            rep = ch
        elif ch in (INVERT_MARK, INVERT_END, FONT_16_MARK):
            out.append(ch)
            continue
        elif ch == STRIKE_MARK:
            if not out or out[-1] == "\n":
                out.append(ch)
            continue
        else:
            rep = _screen_char(ch)
            if not rep:
                dropped = True
                continue
        for r in rep:
            if r == " " and dropped and (not out or out[-1] in (" ", "\n", STRIKE_MARK, INVERT_MARK, INVERT_END, FONT_16_MARK)):
                continue
            if r == "\n" and dropped:
                while out and out[-1] == " ":
                    out.pop()
            out.append(r)
            if r != " ":
                dropped = False
    if dropped:
        while out and out[-1] == " ":
            out.pop()
    return "".join(out)


_SCREEN_STR_FIELDS = ("title", "banner")
_SCREEN_LIST_FIELDS = ("heard", "body", "chips")


def screen_page(page: Any) -> Any:
    """整屏 DisplayPage 的上屏字段（title / banner / heard / body / chips）按字库清洗，返回浅拷贝（不改调用方的 dict）。
    其余字段（emoji / question / word / footer / progress…）是给设备逻辑或将来换字库用的数据，固件不上屏，原样保留。
    protocol.display_page 出站前一律再过一遍——display.py 之外拼出来的页（focus.py 的花火屏等）也跑不掉。"""
    if not isinstance(page, dict):
        return page
    out = dict(page)
    for k in _SCREEN_STR_FIELDS:
        if isinstance(out.get(k), str):
            out[k] = screen_text(out[k])
    for k in _SCREEN_LIST_FIELDS:
        v = out.get(k)
        if isinstance(v, list):
            out[k] = [screen_text(x) if isinstance(x, str) else x for x in v]
    return out

# 4.2" 400×300，思源黑体 ~24px：一行约 16 个汉字（=32 半角列），正文约 8 行。
DEFAULT_COLS = 32          # 每行半角列数（24px 版式基准）
DEFAULT_BODY_ROWS = 8      # 正文最多行数
TITLE_COLS = 28
# 便利贴聊天正文在设备端按 24px 思源黑体渲染（CJK 推进宽=24px）：
# 400px 宽 − 左右 margin ≈ 16 个汉字/行（=32 半角列）刚好铺满、用到右边缘，再多就顶出屏幕。
CHAT_COLS = 32


def _char_cols(ch: str) -> int:
    """一个字符占的显示列数：东亚宽/全角=2，控制哨兵=0，其余=1。"""
    if ch in ("\n", STRIKE_MARK, INVERT_MARK, INVERT_END, FONT_16_MARK):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def _display_width(s: str) -> int:
    return sum(_char_cols(c) for c in s)


import re as _re
# 英文单词/数字当一个整体折行（此前逐字符切，英语练习时满屏「ch oices」「anythi ng」）；中文仍逐字。
_WRAP_TOK_RE = _re.compile(r"[A-Za-z0-9'’\-]+|.", _re.S)


def wrap_cjk(text: str, cols: int = DEFAULT_COLS) -> List[str]:
    """按显示宽度折行；尊重原有换行，不在中间硬切多字节，也不把英文单词从中间切开。
    若段落以删除线 \\x01 或反色高亮 \\x02 开头，折出的每一行均继承该哨兵。"""
    lines: List[str] = []
    for para in str(text).replace("\r\n", "\n").split("\n"):
        if para == "":
            lines.append("")
            continue
        prefix = ""
        while para and (para[0] in (STRIKE_MARK, INVERT_MARK, FONT_16_MARK)):
            prefix += para[0]
            para = para[1:]

        cur, width = "", 0
        for tok in _WRAP_TOK_RE.findall(para):
            w = _display_width(tok)
            if w > cols:                      # 超长单词整行都放不下：只能按字符硬切
                for ch in tok:
                    cw = _char_cols(ch)
                    if width + cw > cols and cur:
                        lines.append(prefix + cur)
                        cur, width = "", 0
                    cur += ch
                    width += cw
                continue
            if width + w > cols and cur:
                lines.append(prefix + cur.rstrip())
                cur, width = "", 0
                if tok == " ":                # 换行后不要以空格开头
                    continue
            cur += tok
            width += w
        if cur:
            lines.append(prefix + cur)
    return lines


def _clip_body(lines: List[str], rows: int) -> (List[str], bool):
    if len(lines) <= rows:
        return lines, False
    kept = lines[:rows]
    # 末行留出省略号
    if kept:
        last = kept[-1]
        while _display_width(last) > DEFAULT_COLS - 1:
            last = last[:-1]
        kept[-1] = last + "…"
    return kept, True


def render_turn(turn: Dict[str, Any], *, cols: int = DEFAULT_COLS,
                body_rows: int = DEFAULT_BODY_ROWS, heard: str = "",
                battery: Optional[int] = None, mode: str = "chat",
                heard_prefix: str = "你说：") -> Dict[str, Any]:
    """chat_turn(dict) → DisplayPage(dict)。

    heard: 本轮识别到的话（显示在顶部「你说」区，可空）。
    heard_prefix: 顶部那行的前缀。默认「你说：」；服务端替孩子代问时（今日探索题 / 错题复习）
                  改成「今日探索：」「错题：」，让题面和学伴的回复留在同一屏上。
    mode:  chat|note|word，仅用于设备端选版式与图标。
    上屏的字先按字库清洗（screen_text）再折行——折行宽度按清洗后的字算；turn 本身不动（TTS 念的是原文）。
    """
    text = screen_text(turn.get("text") or "")
    # 不裁剪正文：整段下发，设备端按屏高翻页（上/下键）看全
    body_lines = wrap_cjk(text, cols)
    truncated = False

    # 顶部横幅（护栏提示：如「已拦下代写」）取第一条，简短
    banners = turn.get("banners") or []
    banner_text = ""
    for b in banners:
        if isinstance(b, dict) and b.get("text"):
            banner_text = screen_text(b["text"])
            break

    # 可视化：黑白墨水屏只透传 svg「示意」，附一行说明；其余类型不上屏
    hint: Optional[Dict[str, Any]] = None
    for blk in (turn.get("blocks") or []):
        rt = blk.get("render_type") if isinstance(blk, dict) else None
        if rt == "svg":
            hint = {"type": "svg", "note": "（图解见台灯屏 · 便利贴只给要点）"}
            break

    chips = [screen_text(c) for c in (turn.get("chips") or [])][:3]

    return {
        "schema_version": "pc-0.1",
        "mode": mode,
        # 「你说：」前缀并入折行（含前缀一起算宽，避免设备再加前缀而超出右边缘）；我说的话全部下发
        "heard": wrap_cjk(screen_text(heard_prefix + heard), cols) if heard else [],
        "banner": banner_text,
        "body": body_lines,
        "chips": chips,
        "hint": hint,
        "truncated": truncated,
        "footer": {"battery": battery},
    }


def render_checklist(title: str, items, *, cols: int = DEFAULT_COLS,
                     rows: int = 30, sel_idx: int = -1, partial: bool = False,
                     footer: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """作业清单整屏（#11）：每项「√ 已完成」/「· 未完成」+ 文本；items = [(text, done), ...]。
    支持上下键光标选中高亮（sel_idx >= 0 时用 > 与反色标记）以及局刷（partial）。"""
    lines: List[str] = []
    done_n = 0
    items = list(items)
    for idx, (text, done) in enumerate(items):
        is_sel = (idx == sel_idx)
        mark = "√ " if done else "· "
        if is_sel:
            prefix = f"{INVERT_MARK}> {mark}"
        else:
            prefix = f"  {mark}" if sel_idx >= 0 else mark
        if done:
            done_n += 1
        # 学科自动格式化
        formatted = text
        for sub in ("语文", "数学", "英语", "阅读", "科学", "习惯", "道法"):
            if text.startswith(sub + "：") or text.startswith(sub + ":") or text.startswith(sub + " "):
                formatted = f"[{sub}] " + text[len(sub)+1:].strip()
                break
        wrap_w = cols - 4 if sel_idx >= 0 else cols - 2
        for i, seg in enumerate(wrap_cjk(screen_text(formatted), wrap_w) or [""]):
            lines.append((prefix if i == 0 else "  ") + seg)
    body_lines, truncated = _clip_body(lines, rows)
    total = len(items)
    display_title = f"{title} (完成 {done_n}/{total})" if total > 0 else (title or "今日待办")
    display_title = screen_text(display_title)
    default_hint = "▲/▼选作业 · ●打勾 · 长按▼菜单 · 按住●问学伴"
    f = dict(footer) if footer else {}
    if "hint" not in f:
        f["hint"] = default_hint
    res: Dict[str, Any] = {
        "schema_version": "pc-0.1",
        "mode": "task",
        "title": wrap_cjk(display_title, TITLE_COLS)[:1][0] if display_title else "今日待办",
        "body": body_lines or ["今天没有任务，去玩会儿吧！"],
        "progress": {"done": done_n, "total": total},
        "truncated": truncated,
        "footer": f,
    }
    if partial:
        res["partial"] = True
    return res


def render_dictation(title: str, cur_idx: int, total: int, word_len: int = 2, pinyin: str = "",
                     hint: str = "", *, partial: bool = False,
                     footer: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """生字词听写整屏：大号进度提示、防剧透占位符、按键引导。屏幕播放内容但不显示汉字！"""
    chars_placeholder = "  ".join(["【？】"] * max(1, word_len))
    lines = [
        f"第 {cur_idx} / {total} 词",
        "",
        f"字数：{word_len} 个字    {chars_placeholder}",
    ]
    if pinyin:
        lines.append(f"拼音：{pinyin}")
    lines.append("")
    lines.append("🎧 正在报词：请听清发音，工整书写")
    lines.append("👉 写好按中间【确认键】进入下一词")
    lines.append("🔊 没听清可按【▲键】重听")
    default_hint = "●下一词 · ▲重听 · 长按▼返回菜单 · 按住●问学伴"
    f = dict(footer) if footer else {}
    if "hint" not in f:
        f["hint"] = default_hint
    res = render_lines(title or "生字词听写", "\n".join(lines), mode="dictation", footer=f)
    if partial:
        res["partial"] = True
    return res


def render_dictation_summary(title: str, words: List[Tuple[str, str, str]],
                             *, footer: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """听写全部完成整屏：全词清单对照自查与家长检查。"""
    lines = ["🎉 听写完成！请交给家长检查批改：", ""]
    # 两个一组排版
    for i in range(0, len(words), 2):
        row = f"{i+1}. {words[i][0]}"
        if i + 1 < len(words):
            row += f"   {i+2}. {words[i+1][0]}"
        lines.append(row)
    lines.append("")
    lines.append("📝 家长可在小程序或后台同步勾选错字入库！")
    lines.append("长按▼键返回功能菜单")
    default_hint = "听写已归档 · 长按▼返回菜单 · 按住●问学伴"
    f = dict(footer) if footer else {}
    if "hint" not in f:
        f["hint"] = default_hint
    return render_lines(title or "听写检查清单", "\n".join(lines), mode="dictation", footer=f)


def render_recite(poem_title: str, author: str, dynasty: str, lines: List[str],
                  *, prompt: str = "", passed: bool = False, partial: bool = False,
                  footer: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """古诗背诵整屏：诗题居中、朝代作者、分句精准状态。"""
    body = [f"《{poem_title}》〔{dynasty}〕{author}", ""]
    for l in lines:
        body.append("  " + l)
    body.append("")
    if passed:
        body.append("★ 恭喜！整首诗背诵通关！")
    elif prompt:
        body.append(f"提示：{prompt}")
    else:
        body.append("按住●可背单句或全诗   ▲/▼切换古诗")
    default_hint = "▲/▼切诗 · ●重听 · 长按▼返回菜单 · 按住●背诗"
    f = dict(footer) if footer else {}
    if "hint" not in f:
        f["hint"] = default_hint
    res = render_lines(f"古诗背诵 · {poem_title}", "\n".join(body), mode="recite", footer=f)
    if partial:
        res["partial"] = True
    return res


def render_arith(expr: str, answer: str = "", revealed: bool = False, cur_idx: int = 1, total: int = 10,
                 *, partial: bool = False, footer: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """口算大闯关整屏：题目、草稿自测、答案揭晓。"""
    lines = [
        f"第 {cur_idx} / {total} 题",
        "",
        f"     {expr} = {answer if revealed else '?'}",
        "",
    ]
    if revealed:
        lines.append(f"★ 揭晓：{expr} = {answer}")
        lines.append("●我对了·下一题  ▲算错了·记错题")
    else:
        lines.append("在草稿纸速算或按住说出答案！")
        lines.append("●揭晓答案  ▲重读题目")
    default_hint = "●看答案/下一题 · ▲记错题 · 长按▼返回菜单 · 按住●问学伴"
    f = dict(footer) if footer else {}
    if "hint" not in f:
        f["hint"] = default_hint
    res = render_lines("口算大闯关", "\n".join(lines), mode="arith", footer=f)
    if partial:
        res["partial"] = True
    return res


def render_wordcard(card, *, box: Optional[int] = None, pos: Optional[str] = None,
                    footer: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """单词卡整屏（#1/#19）：大字词 + 释义 + 英文例句(+中文)。
    不显示 IPA 音标——墨水屏思源黑体渲染不出 IPA 字形，发音由 TTS 念出。
    pos: 连续练习时的进度，如 "3/15"。"""
    lines = [card.word]
    lines.append("")
    lines.append(getattr(card, "meaning", "") or "（想想它的意思）")
    example = getattr(card, "example", "")
    if example:
        lines.append("")
        lines.append("例：" + example)
        example_cn = getattr(card, "example_cn", "")
        if example_cn:
            lines.append("　　" + example_cn)
    title = "单词卡"
    if box is not None:
        title += " · 第 %d 盒" % box
    if pos:
        title += "（%s）" % pos
    page = render_lines(title, "\n".join(lines), mode="word", footer=footer)
    page["word"] = card.word
    return page


def render_spark(card: Dict[str, Any], *, due_n: int = 0, date_label: str = "",
                 footer: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """「今日探索」早安屏（内容策略：每天为这一个孩子现编的探索题 + 错题本到期数）。

    固件把 title + body 摊平成 24px 行流、每屏 8 行（application.cc kLinesPerPage），
    这里保证**最多 8 行**、不触发翻页脚注：
        今日探索 · 9月15日            ← 标题
        （空）
        一只霸王龙能吃掉几分之几头      ← 题面（≤17 字，最多折 2 行）
        三角龙？
        （空）
        错题本里有 3 道题到期啦        ← due_n=0 时整行不出
        （空）
        按住说“聊聊这个”或“错题”      ← due_n=0 时只提「聊聊这个」
    字形红线：思源黑体 slim 子集没有 emoji / 「」『』✓ ■●━，引号只能用 “ ”（U+201C/D 在子集里）——
    上屏的题面与标题一律先过 screen_text（见上「上屏字形」）。
    card 的 emoji 不上行流，只作为 page["emoji"] 数据带下去（固件忽略未知字段），换字库后可用。
    """
    title = screen_text("今日探索" + (f" · {date_label}" if date_label else ""))
    question = str(card.get("title") or "").strip()
    lines: List[str] = [""]
    lines += wrap_cjk(screen_text(question).strip(), DEFAULT_COLS)[:2] or ["（今天的问题还在路上）"]
    lines.append("")
    if due_n > 0:
        lines.append(f"错题本里有 {int(due_n)} 道题到期啦")
        lines.append("")
        lines.append("按住说“聊聊这个”或“错题”")
    else:
        lines.append("按住说“聊聊这个”")
    return {
        "schema_version": "pc-0.1",
        "mode": "spark",
        "title": wrap_cjk(title, TITLE_COLS)[0],
        "body": lines,
        "emoji": str(card.get("emoji") or ""),
        "question": question,
        "due": int(due_n),
        "truncated": False,
        "footer": footer or {},
    }


MENU_ITEMS = [
    ("今日作业", "每日清单与打勾完成", "task"),
    ("课内古诗", "小学必背古诗逐句评测", "recite"),
    ("生字听写", "语文生字词自动报词听写", "dictation"),
    ("口算闯关", "数学速算心算计时闪卡", "arith"),
    ("英语生词", "生词卡片·语音朗读·跟读评测", "vocab"),
    ("英语阅读", "分级短文·按键选句·朗读评测", "reading"),
    ("花火番茄", "25分钟专注沉浸学习钟", "focus"),
    ("习惯打卡", "自律好习惯打卡成长星星", "habit"),
    ("桌面课表", "今日课程安排与书包备忘", "timetable"),
    ("每日成语", "国学成语典故与朗读积累", "idiom"),
    ("错题消灭", "口算生字历史错题强化练", "mistakes"),
    ("费曼讲课", "费曼学习法·小老师讲给我听", "feynman"),
    ("猜猜看", "语音二十问·逻辑归纳猜动物", "guess"),
    ("核实一下", "AI事实核查·辨真伪教方法", "verify"),
    ("诗词飞花令", "名诗飞花令·语音人机诗词对战", "feihua"),
    ("错字门诊", "形近字同音字口诀速记闯关", "hanzi"),
    ("小学新鲜事", "学习新闻·赛事展会·活动速递", "news"),
    ("AI学伴", "智能对话伴学·答疑解惑谈心", "chat"),
    ("桌面屏保", "名言诗词名画倒计时常显屏保", "screensaver"),
]
MENU_PAGE_SIZE = 8


def render_menu(selected_idx: int = 0, *, footer: Optional[Dict[str, Any]] = None,
                partial: bool = False,
                items: Optional[List[Tuple[str, str, str]]] = None) -> Dict[str, Any]:
    """PocketClaw 功能导航菜单整屏。
    支持物理按键 ▲/▼ 移动光标，● 确认进入。
    每页 8 项分页饱满展示（充分利用 Note 4 屏幕 8 行正文空间，不留底部空档），
    选中的项黑底白字高亮反色，移动光标采用 partial 局部刷新。
    首行显示当前页码与选功能提示。
    支持动态排序传入 items（按使用频次或家长自定义排序）。
    """
    menu_items = items if items is not None else MENU_ITEMS
    total_items = len(menu_items)
    selected_idx = max(0, min(selected_idx, total_items - 1)) if total_items > 0 else 0
    page_idx = selected_idx // MENU_PAGE_SIZE if total_items > 0 else 0
    total_pages = max(1, (total_items + MENU_PAGE_SIZE - 1) // MENU_PAGE_SIZE)

    start_idx = page_idx * MENU_PAGE_SIZE
    end_idx = min(start_idx + MENU_PAGE_SIZE, total_items)

    lines = []
    for i in range(start_idx, end_idx):
        name, desc, _ = menu_items[i]
        tag = f"[{i+1}] {name}  {desc}"
        if i == selected_idx:
            lines.append(f"{INVERT_MARK}> {tag}")
        else:
            lines.append(f"  {tag}")

    title = f"功能导航菜单 ({page_idx + 1}/{total_pages}) ▲/▼"
    default_hint = "▲/▼选功能 · ●确认 · 长按▼退出"
    f = dict(footer) if footer else {}
    if "hint" not in f:
        f["hint"] = default_hint
    return {
        "schema_version": "pc-0.1",
        "mode": "menu",
        "title": screen_text(title),
        "body": lines,
        "selected_idx": selected_idx,
        "partial": partial,
        "truncated": False,
        "footer": f,
    }


def render_vocab_card(word: str, meaning: str, example: str = "", example_cn: str = "",
                      *, cur_idx: int = 1, total: int = 1,
                      score: Optional[int] = None, rating: str = "",
                      feedback: str = "", partial: bool = False,
                      footer: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """英语生词本卡片整屏：支持生词展示、释义例句、发音朗读与语音评测打分。"""
    lines: List[str] = [
        f"【 {word} 】",
        f"释义：{meaning}",
    ]
    if example:
        lines.append(f"例句：{example}")
        if example_cn:
            lines.append(f"译文：{example_cn}")

    if score is not None:
        r_str = f" {rating}" if rating else ""
        lines.append(f"跟读评测：{score}分{r_str}")
        if feedback:
            lines.append(f"评语：{feedback}")
    else:
        lines.append("提示：短按●朗读，按住●跟读评测")

    title = f"英语生词本 ({cur_idx}/{total})"
    default_hint = "▲/▼切词 · ●发音 · 长按▼返回菜单 · 按住●评测"
    f = dict(footer) if footer else {}
    if "hint" not in f:
        f["hint"] = default_hint
    res = render_lines(title, "\n".join(lines), mode="vocab", footer=f)
    if partial:
        res["partial"] = True
    return res


def render_reading_article(title: str, sentences: List[Tuple[str, str]],
                           sel_idx: int = 0,
                           show_translation: bool = False,
                           score: Optional[int] = None, rating: str = "",
                           feedback: str = "",
                           *, footer: Optional[Dict[str, Any]] = None,
                           partial: bool = False) -> Dict[str, Any]:
    """英语文章阅读整屏：展示完整英文短文（段落自然流式排版，句与句顺畅相接），
    按上下键选择句子（行内精准黑底白字高亮反色，静默无声不播报、不显示中文），
    短按确认键显示中文译文并朗读当前句。
    充分利用 Note 4 墨水屏 8 行正文空间，不留底部空档。
    选中的句子始终在当前视野内，带上下文滑动呈现。
    """
    total = len(sentences)
    sel_idx = max(0, min(sel_idx, total - 1)) if total > 0 else 0

    # 1. 将所有句子切词并标记句子归属，构建自然流式英语段落
    tokens: List[Tuple[str, int]] = []
    for s_idx, (en_s, _) in enumerate(sentences):
        words = en_s.strip().split()
        for w in words:
            tokens.append((w, s_idx))

    # 2. 单词边界自然折行（最大列宽 DEFAULT_COLS = 32）
    lines_tokens: List[List[Tuple[str, int]]] = []
    cur_line: List[Tuple[str, int]] = []
    cur_len = 0
    for w, s_idx in tokens:
        w_len = len(w)
        space_needed = 1 if cur_line else 0
        if cur_len + space_needed + w_len <= DEFAULT_COLS:
            cur_line.append((w, s_idx))
            cur_len += space_needed + w_len
        else:
            if cur_line:
                lines_tokens.append(cur_line)
            cur_line = [(w, s_idx)]
            cur_len = w_len
    if cur_line:
        lines_tokens.append(cur_line)

    # 3. 为每行注入行内精准反色哨兵 \x02 和 \x03（仅高亮 sel_idx 对应文字）
    all_flow_lines: List[str] = []
    for line in lines_tokens:
        parts: List[str] = []
        in_sel = False
        for i, (w, s_idx) in enumerate(line):
            is_sel = (s_idx == sel_idx)
            if is_sel and not in_sel:
                if i > 0:
                    parts.append(" ")
                parts.append(INVERT_MARK)
                parts.append(w)
                in_sel = True
            elif not is_sel and in_sel:
                parts.append(INVERT_END)
                parts.append(" ")
                parts.append(w)
                in_sel = False
            else:
                if i > 0:
                    parts.append(" ")
                parts.append(w)
        if in_sel:
            parts.append(INVERT_END)
        all_flow_lines.append("".join(parts))

    # 4. 找到当前选中句子所在的起始行
    target_line = 0
    for line_idx, line in enumerate(lines_tokens):
        if any(s_idx == sel_idx for _, s_idx in line):
            target_line = line_idx
            break

    # 5. 准备译文和评测（仅当主动确认或已评测时才追加）
    PAGE_BODY_LINES = 8  # Note 4 墨水屏单屏饱满正文容纳行数
    cur_en, cur_cn = sentences[sel_idx] if 0 <= sel_idx < total else ("", "")
    extra_lines: List[str] = []
    if (show_translation or score is not None) and cur_cn:
        for cn_line in wrap_cjk(f"【译】{cur_cn}", DEFAULT_COLS):
            extra_lines.append(cn_line)

    if score is not None:
        r_str = f" {rating}" if rating else ""
        extra_lines.append(f"选句评测：{score}分{r_str} {feedback}".strip())

    en_budget = max(4, PAGE_BODY_LINES - len(extra_lines))
    total_flow_lines = len(all_flow_lines)

    # 6. 滑动窗口：确保 target_line 在视野内，且优先向上/下扩展完整填满 en_budget 行
    if total_flow_lines <= en_budget:
        start_line = 0
        end_line = total_flow_lines
    else:
        start_line = max(0, min(target_line, total_flow_lines - en_budget))
        end_line = min(start_line + en_budget, total_flow_lines)

    body_lines = list(all_flow_lines[start_line:end_line]) + extra_lines

    # 7. 首行标题格式：文章标题 + (选句序号/总数) + ▲/▼
    hint_suffix = f" ({sel_idx + 1}/{total}) ▲/▼"
    max_title_len = DEFAULT_COLS - _display_width(hint_suffix) - 4
    clean_title = title
    if _display_width(clean_title) > max_title_len:
        clean_title = clean_title[:max(4, max_title_len // 2)] + "…"
    page_title = f"《{clean_title}》{hint_suffix}"

    default_hint = "▲/▼选句 · ●朗读 · 长按▼返回菜单 · 按住●评测"
    f = dict(footer) if footer else {}
    if "hint" not in f:
        f["hint"] = default_hint
    return {
        "schema_version": "pc-0.1",
        "mode": "reading",
        "title": screen_text(page_title),
        "body": body_lines,
        "sel_idx": sel_idx,
        "total": total,
        "show_translation": show_translation,
        "partial": partial,
        "footer": f,
    }


HABIT_PAGE_SIZE = 6


def render_habits(habits: List[Any], sel_idx: int = 0, *, footer: Optional[Dict[str, Any]] = None,
                  partial: bool = False) -> Dict[str, Any]:
    """好习惯打卡整屏：展示今日习惯，选中项黑底白字高亮。支持按页翻看。"""
    done_cnt = sum(1 for h in habits if getattr(h, "done", False))
    total = len(habits)
    sel_idx = max(0, min(sel_idx, total - 1)) if total > 0 else 0
    page_idx = sel_idx // HABIT_PAGE_SIZE if total > 0 else 0
    total_pages = max(1, (total + HABIT_PAGE_SIZE - 1) // HABIT_PAGE_SIZE)

    start_idx = page_idx * HABIT_PAGE_SIZE
    end_idx = min(start_idx + HABIT_PAGE_SIZE, total)

    lines = []
    for i in range(start_idx, end_idx):
        h = habits[i]
        name = getattr(h, "name", str(h))
        done = getattr(h, "done", False)
        streak = getattr(h, "streak", 0)
        mark = "[✓]" if done else "[ ]"
        streak_str = f" (连打{streak}天)" if streak > 1 else ""
        text_line = f"{mark} {name}{streak_str}"
        if i == sel_idx:
            lines.append(f"{INVERT_MARK}> {text_line}")
        else:
            lines.append(f"  {text_line}")

    page_title = f"好习惯打卡 ({done_cnt}/{total}) ({page_idx+1}/{total_pages})"
    default_hint = "▲/▼选习惯 · ●打卡 · 长按▼返回菜单 · 按住●问学伴"
    f = dict(footer) if footer else {}
    if "hint" not in f:
        f["hint"] = default_hint
    return {
        "schema_version": "pc-0.1",
        "mode": "habit",
        "title": screen_text(page_title),
        "body": lines,
        "sel_idx": sel_idx,
        "partial": partial,
        "footer": f,
    }


def render_timetable(day_name: str, courses: List[str], note: str = "", *,
                     footer: Optional[Dict[str, Any]] = None, partial: bool = False) -> Dict[str, Any]:
    """桌面课程表整屏：精简排版完整显示当日课程与备忘，不超屏。"""
    def fit_line(value: str) -> str:
        value = screen_text(value).replace("\n", " ")
        if _display_width(value) <= CHAT_COLS:
            return value
        first = wrap_cjk(value, CHAT_COLS - 2)[0]
        return first.rstrip() + "…"

    lines = [fit_line(f"第{i + 1}节  {course or '无'}")
             for i, course in enumerate(courses[:6])]
    if not lines:
        lines.append("今天没有课程")
    if note:
        lines.append(fit_line(f"备忘: {note}"))

    page_title = f"📅 桌面课表 · {day_name} ▲/▼"
    default_hint = "▲/▼换星期 · ●朗读 · 长按▼返回菜单 · 按住●问学伴"
    f = dict(footer) if footer else {}
    if "hint" not in f:
        f["hint"] = default_hint
    return {
        "schema_version": "pc-0.1",
        "mode": "timetable",
        "title": screen_text(page_title),
        "body": lines,
        "partial": partial,
        "footer": f,
    }


def render_timetable_week(schedules: List[Dict[str, Any]], selected: int = 0, *,
                          footer: Optional[Dict[str, Any]] = None,
                          partial: bool = False) -> Dict[str, Any]:
    """Five days and six periods within the device's 32-column body width."""
    def cell(value: Any) -> str:
        value = screen_text(value or '--').replace('\n', ' ')
        if _display_width(value) > 4:
            shortened = ''
            for ch in value:
                if _display_width(shortened + ch) > 3:
                    break
                shortened += ch
            value = shortened + '~'
        return value + ' ' * (4 - _display_width(value))

    lines = []
    for day in range(5):
        courses = schedules[day].get('courses', []) if day < len(schedules) else []
        cells = [cell(courses[i] if i < len(courses) else '') for i in range(6)]
        lines.append(('*' if day == selected else ' ') + '一二三四五'[day] + ' '.join(cells))
    lines.append('~ 表示课程名缩写')
    f = dict(footer or {})
    f.setdefault('hint', '上下选星期 · 中键朗读 · 长按返回')
    return {'schema_version': 'pc-0.1', 'mode': 'timetable',
            'title': '一周课表  1  2  3  4  5  6', 'body': lines,
            'partial': partial, 'footer': f}


def render_mistakes(mistakes: List[Any], sel_idx: int = 0, *,
                    footer: Optional[Dict[str, Any]] = None, partial: bool = False) -> Dict[str, Any]:
    """错题强化消灭本整屏。"""
    total = len(mistakes)
    default_hint = "▲/▼翻看 · ●消灭此题 · 长按▼返回菜单 · 按住●问学伴"
    f = dict(footer) if footer else {}
    if "hint" not in f:
        f["hint"] = default_hint

    if not mistakes:
        lines = [
            "",
            "🎉 太棒啦！暂无历史错题！",
            "",
            "做口算和生字听写时的错题",
            "会自动沉淀在这里专项消灭～",
        ]
        return render_lines("📕 错题强化消灭本", "\n".join(lines), mode="mistakes",
                            footer=f)

    sel_idx = max(0, min(sel_idx, total - 1))
    cur = mistakes[sel_idx]
    cat_map = {"arith": "口算题", "dictation": "生字听写", "reading": "英语阅读", "vocab": "英语生词"}
    cat_name = cat_map.get(getattr(cur, "category", ""), "弱项题")

    lines = [
        f"【分类】{cat_name}",
        f"【题目】{getattr(cur, 'question', '')}",
        "",
        f"【正确答案】{getattr(cur, 'expected', '')}",
    ]
    wrong = getattr(cur, "wrong_answer", "")
    if wrong:
        lines.append(f"【历史错答】{wrong}")
    cnt = getattr(cur, "count", 1)
    if cnt > 1:
        lines.append(f"【出错频次】复错 {cnt} 次")

    page_title = f"📕 错题消灭 ({sel_idx + 1}/{total})"
    res = render_lines(page_title, "\n".join(lines), mode="mistakes", body_rows=15, footer=f)
    if partial:
        res["partial"] = True
    return res


def render_daily_idiom(idiom_data: Dict[str, str], *, cur_idx: Optional[int] = None,
                       total: Optional[int] = None, footer: Optional[Dict[str, Any]] = None,
                       partial: bool = False) -> Dict[str, Any]:
    """每日国学成语与励志金句整屏。"""
    idiom = idiom_data.get("idiom", "")
    py = idiom_data.get("pinyin", "")
    meaning = idiom_data.get("meaning", "")
    source = idiom_data.get("source", "")
    example = idiom_data.get("example", "")

    lines = [
        f"【成语】{idiom}  ({py})",
        "",
        f"【释义】{meaning}",
    ]
    if source:
        lines.append(f"【出处】{source}")
    if example:
        lines.append(f"【造句】{example}")

    idx_tag = f" ({cur_idx}/{total})" if (cur_idx is not None and total) else ""
    page_title = f"📜 每日成语{idx_tag} · {idiom}"
    default_hint = "▲/▼换成语 · ●朗读典故 · 长按▼返回菜单 · 按住●问学伴"
    f = dict(footer) if footer else {}
    if "hint" not in f:
        f["hint"] = default_hint
    res = render_lines(page_title, "\n".join(lines), mode="idiom", body_rows=15, footer=f)
    if partial:
        res["partial"] = True
    return res


def render_parent_message(text: str, ts_str: str = "", *,
                          footer: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """家长留言便签整屏：温馨留言推送到 Note 4 墨水屏。"""
    lines = [
        "亲爱的宝贝：",
        "",
        text,
        "",
        f"—— 爱你的爸爸妈妈 {ts_str}".strip(),
    ]
    return render_lines("💌 家长温馨留言", "\n".join(lines), mode="parent_note",
                        footer=footer or {"hint": "按任意键返回学习"})


def render_guess(asked_cnt: int, max_cnt: int = 20, history: Optional[List[Any]] = None,
                 status: str = "playing", secret: str = "", hint: str = "",
                 *, footer: Optional[Dict[str, Any]] = None, partial: bool = False) -> Dict[str, Any]:
    """猜猜看（语音二十问，移植自 SnapClaw）墨水屏整屏。"""
    lines: List[str] = []
    if status == "win":
        lines.append("🎉 太厉害啦！完全猜中！")
        lines.append("")
        lines.append(f"★ 谜底正是：【 {secret} 】")
        lines.append(f"你总共只用了 {asked_cnt} 次提问！")
        lines.append("获得自律小红星 +2 颗！⭐")
        lines.append("")
        lines.append("按确认键或下键开启新一轮挑战！")
        page_title = "❓ 猜猜看 · 挑战成功！"
    elif status in ("over", "reveal"):
        lines.append("💡 谜底揭晓啦：")
        lines.append("")
        lines.append(f"★ 它的秘密是：【 {secret} 】")
        if hint:
            lines.append(f"特征：{hint}")
        lines.append("")
        lines.append("没关系，胜败乃兵家常事，")
        lines.append("按确认键或下键再来一局吧！")
        page_title = "❓ 猜猜看 · 答案揭晓"
    else:
        page_title = f"❓ 猜猜看 (已问 {asked_cnt}/{max_cnt} 题) ▲/▼"
        if not history:
            lines.append("【我心里想好了一种神秘事物】")
            lines.append("按住中间键问我是非问题（如：它会飞吗？）")
            lines.append("我只会回答是、不是或不一定哦！")
            lines.append("")
            lines.append("▲键获取线索提示   ▼键揭晓答案")
        else:
            for item in history[-2:]:
                q = getattr(item, "question", "")
                a = getattr(item, "answer", "")
                lines.append(f"问：{q}")
                for seg in wrap_cjk(f"答：{a}", DEFAULT_COLS - 2):
                    lines.append(f"  {seg}")
                lines.append("")

    default_hint = "按住●提问 · ▲线索 · ▼揭晓 · 长按▼返回菜单"
    f = dict(footer) if footer else {}
    if "hint" not in f:
        f["hint"] = default_hint
    res = render_lines(page_title, "\n".join(lines), mode="guess", footer=f)
    if partial:
        res["partial"] = True
    return res


def render_feynman(round_idx: int, max_rounds: int = 3, current_dialogue: Optional[List[Any]] = None,
                   summary: Optional[Any] = None, *, footer: Optional[Dict[str, Any]] = None,
                   partial: bool = False) -> Dict[str, Any]:
    """费曼小老师（讲给我听，移植自 SnapClaw）墨水屏整屏。"""
    lines: List[str] = []
    if summary:
        page_title = "🎓 费曼小老师 · 结课点赞！"
        lines.append("🏆 讲课结业！小老师太棒啦！")
        lines.append(f"【课题】{getattr(summary, 'topic', '今日所学')}")
        lines.append("")
        lines.append("★ 讲得最清楚的一点：")
        lines.append(f"  {getattr(summary, 'good_point', '生动形象，举例清晰！')}")
        lines.append("★ 还可以再思考的一点：")
        lines.append(f"  {getattr(summary, 'think_point', '联系实际，深入探究。')}")
        lines.append("")
        lines.append("小老师表现优异，奖励 2 颗自律星星！⭐")
    else:
        page_title = f"🎓 费曼小老师 · 讲给我听 ({round_idx}/{max_rounds})"
        if not current_dialogue:
            lines.append("【小萌新认真听讲中…】")
            lines.append("小老师，今天在学校学了什么新知识？")
            lines.append("或者有一道难题想给我讲一讲？")
            lines.append("")
            lines.append("按住中间键开始给我讲课吧！")
            lines.append("能用自己的话讲明白，才是真正学透哦！")
        else:
            last = current_dialogue[-1]
            t_speech = getattr(last, "teacher_speech", "")
            s_reply = getattr(last, "student_reply", "")
            lines.append(f"小老师讲：“{t_speech[:28]}…”" if len(t_speech) > 28 else f"小老师讲：“{t_speech}”")
            lines.append("")
            lines.append("小萌新听后提问：")
            for seg in wrap_cjk(s_reply, DEFAULT_COLS - 2):
                lines.append(f"  {seg}")

    default_hint = "按住●讲课 · ●重听 · 长按▼返回菜单"
    f = dict(footer) if footer else {}
    if "hint" not in f:
        f["hint"] = default_hint
    res = render_lines(page_title, "\n".join(lines), mode="feynman", footer=f)
    if partial:
        res["partial"] = True
    return res


def render_verify(query: str, verdict: str, note: str, how: str,
                  *, footer: Optional[Dict[str, Any]] = None, partial: bool = False) -> Dict[str, Any]:
    """事实核查官（核实一下，移植自 SnapClaw）墨水屏整屏。"""
    page_title = "🔎 核实一下 · 事实核查官"
    lines: List[str] = []
    if not query:
        lines.append("【培养科学审辩与事实核查思维】")
        lines.append("")
        lines.append("听来的传闻？脑筋急转弯？算式？")
        lines.append("按住中间键，说一句想核实的话，例如：")
        lines.append("  “西瓜是长在树上的吗？”")
        lines.append("  “企鹅到底是不是鸟类？”")
        lines.append("  “35乘以12等于420对吗？”")
    else:
        icon_map = {"ok": "✅ 说法正确！", "wrong": "❌ 说法不正确！", "unsure": "🔎 存疑/待求证"}
        v_title = icon_map.get(verdict, "🔎 事实核实")
        lines.append(f"【你说】“{query}”")
        lines.append(f"【结论】{v_title}")
        lines.append("")
        lines.append("【核实依据】")
        for seg in wrap_cjk(note, DEFAULT_COLS):
            lines.append(seg)
        if how:
            lines.append("")
            lines.append(f"【教你核实】💡 {how}")

    default_hint = "按住●说话核实 · ●重读 · 长按▼返回菜单"
    f = dict(footer) if footer else {}
    if "hint" not in f:
        f["hint"] = default_hint
    res = render_lines(page_title, "\n".join(lines), mode="verify", footer=f)
    if partial:
        res["partial"] = True
    return res


def render_feihua(keyword: str, round_idx: int, target_rounds: int = 3,
                  history: Optional[List[Dict[str, str]]] = None,
                  status: str = "playing", hint: str = "",
                  *, footer: Optional[Dict[str, Any]] = None, partial: bool = False) -> Dict[str, Any]:
    """诗词飞花令（小学生古诗词对战）墨水屏整屏。"""
    history = history or []
    lines: List[str] = []

    if status == "win":
        page_title = f"🌸 飞花令 · 大获全胜！(令：{keyword})"
        lines.append("🏆 恭喜通关！诗才横溢，满腹经纶！")
        lines.append(f"【本局令字】「 {keyword} 」")
        lines.append(f"【战绩】成功对诗 {len(history)} 轮！")
        lines.append("")
        for i, h in enumerate(history[-2:], 1):
            lines.append(f"第{i}轮 你：“{h.get('child','')}”")
            lines.append(f"      伴：“{h.get('bot','')}”")
        lines.append("")
        lines.append("表现优异，奖励 2 颗自律星星！⭐")
    elif status == "giveup":
        page_title = f"🌸 飞花令 · 胜败乃兵家常事(令：{keyword})"
        lines.append("【胜败乃兵家常事，换个令字再战！】")
        lines.append(f"本局令字是「{keyword}」。")
        if hint:
            lines.append(hint)
        lines.append("")
        lines.append("按 ▼ 换令字重开，按住中间键继续挑战！")
    else:
        page_title = f"🌸 飞花令 · 诗词对战 ({round_idx}/{target_rounds})"
        lines.append(f"【本局令字】「 {keyword} 」")
        if not history:
            lines.append("比赛规则：按住中间键，说出一句")
            lines.append(f"带有「{keyword}」字的五言或七言古诗。")
            lines.append("你说一句，学伴对一句，对满3轮即获胜！")
            if hint:
                lines.append("")
                lines.append(hint)
        else:
            last = history[-1]
            lines.append(f"你：“{last.get('child', '')}” ✅")
            lines.append(f"学伴：“{last.get('bot', '')}”")
            lines.append("")
            if hint:
                lines.append(hint)
            else:
                lines.append(f"轮到你啦！请再说一句带「{keyword}」的诗：")

    default_hint = "按住●对诗 · ▲提示 · ▼换字 · 长按▼返回菜单"
    f = dict(footer) if footer else {}
    if "hint" not in f:
        f["hint"] = default_hint
    res = render_lines(page_title, "\n".join(lines), mode="feihua", footer=f)
    if partial:
        res["partial"] = True
    return res


def render_hanzi(question_idx: int, total_questions: int, stem: str,
                 opts: List[str], selected_opt_idx: int, revealed: bool = False,
                 is_correct: Optional[bool] = None, mnemonic: str = "", detail: str = "",
                 *, footer: Optional[Dict[str, Any]] = None, partial: bool = False) -> Dict[str, Any]:
    """错字门诊 / 形近字辨析墨水屏整屏。"""
    page_title = f"🔍 错字门诊 · 辨字闯关 ({question_idx + 1}/{total_questions})"
    lines: List[str] = []

    lines.append("【题目】请选字填空：")
    lines.append(f"  {stem}")
    lines.append("")

    if not revealed:
        lines.append("【选项】（▲/▼ 切换光标，● 确认）：")
        for i, opt in enumerate(opts):
            arrow = "▲" if i == 0 else "▼"
            tag = f"[{arrow}] 选项：【 {opt} 】"
            if i == selected_opt_idx:
                lines.append(f"{INVERT_MARK}> {tag}")
            else:
                lines.append(f"  {tag}")
        lines.append("")
        lines.append("也可以按住中间键直接说出你的选择！")
    else:
        v_icon = "✅ 回答正确！" if is_correct else "❌ 回答错误！"
        lines.append(f"【判定】{v_icon}")
        lines.append(f"💡 记忆口诀：{mnemonic}")
        if detail:
            for seg in wrap_cjk(f"📖 辨析：{detail}", DEFAULT_COLS):
                lines.append(seg)
        lines.append("")
        lines.append("按 ● 键进入下一题闯关！")

    default_hint = "▲/▼换选 · ●确认/下一题 · 长按▼返回菜单 · 按住●语音"
    f = dict(footer) if footer else {}
    if "hint" not in f:
        f["hint"] = default_hint
    res = render_lines(page_title, "\n".join(lines), mode="hanzi", footer=f)
    if partial:
        res["partial"] = True
    return res


def render_screensaver(card: Dict[str, Any], *, footer: Optional[Dict[str, Any]] = None,
                       partial: bool = False) -> Dict[str, Any]:
    """PocketClaw 桌面伴学屏保模式（名人语录/古诗词/名画/倒计时/课表/轮播）。"""
    t = card.get("type", "quote")
    type_name = card.get("type_name", "屏保")
    is_cycle = card.get("is_cycle", False)
    mode_tag = " [轮播]" if is_cycle else f" [{type_name}]"

    lines: List[str] = []

    if t == "quote":
        page_title = card.get("title", f"📜 名人语录{mode_tag}")
        quote = card.get("quote", "")
        author = card.get("author", "")
        source = card.get("source", "")
        motto = card.get("motto", "")

        lines.append("【名言金句】")
        for seg in wrap_cjk(f"“{quote}”", DEFAULT_COLS):
            lines.append(f"  {seg}")
        lines.append("")
        src_str = f"《{source}》" if source and not source.startswith("《") else source
        lines.append(f"              —— {author} {src_str}".rstrip())
        if motto:
            lines.append("")
            for m_seg in wrap_cjk(f"💡 寄语：{motto}", DEFAULT_COLS):
                lines.append(m_seg)

    elif t == "poem":
        page_title = card.get("title", f"🏮 经典诗词{mode_tag}")
        p_title = card.get("poem_title", "")
        dynasty = card.get("dynasty", "")
        poet = card.get("poet", "")
        p_lines = card.get("lines", [])
        appreciation = card.get("appreciation", "")

        lines.append(f"《{p_title}》  [{dynasty}] {poet}")
        lines.append("────────────────────────")
        for pl in p_lines:
            lines.append(f"  {pl}")
        lines.append("────────────────────────")
        if appreciation:
            for seg in wrap_cjk(f"💡 鉴赏：{appreciation}", DEFAULT_COLS):
                lines.append(seg)

    elif t == "art":
        page_title = card.get("title", f"🎨 世界名画{mode_tag}")
        art_title = card.get("art_title", "")
        artist = card.get("artist", "")
        year = card.get("year", "")
        style = card.get("style", "")
        raw_b64 = card.get("raw_1bpp_b64", "")

        # 世界名画重点展示画作位图（Floyd-Steinberg 墨水屏全屏抖动），底端保留精简标牌
        lines.append(f"《{art_title}》")
        lines.append(f"【创作者】{artist} · {year}")
        if style:
            lines.append(f"【流派】{style}")

    elif t == "countdown":
        page_title = card.get("title", f"⏳ 目标倒计时{mode_tag}")
        ev_title = card.get("event_title", "期末大考")
        t_date = card.get("target_date", "")
        days_left = card.get("days_left", 0)
        slogan = card.get("slogan", "")

        lines.append(f"【目标事项】{ev_title}")
        lines.append(f"【目标日期】{t_date}")
        lines.append("")

        if days_left > 0:
            box = f"★ 距目标还有： {days_left} 天 ★"
        elif days_left == 0:
            box = "★ 今天就是目标日！全力冲刺！ ★"
        else:
            box = f"★ 目标已圆满达成 (过去{abs(days_left)}天) ★"
        lines.append(f"{INVERT_MARK}{box}")
        lines.append("")
        if slogan:
            for s_seg in wrap_cjk(f"🚀 冲刺宣言：{slogan}", DEFAULT_COLS):
                lines.append(s_seg)

    elif t == "timetable":
        page_title = card.get("title", f"📅 桌面课表{mode_tag}")
        sched = card.get("schedule", {})
        day_name = sched.get("day_name", "今日课程")
        morning = sched.get("morning", ["语文", "数学", "英语", "科学"])
        afternoon = sched.get("afternoon", ["美术", "体育", "自习"])
        notes = sched.get("notes", "带齐课本、作业本、铅笔盒与跳绳！")

        lines.append(f"【{day_name}】")
        lines.append("上午：" + " ".join(f"[{i+1}]{s}" for i, s in enumerate(morning[:4])))
        lines.append("下午：" + " ".join(f"[{i+1}]{s}" for i, s in enumerate(afternoon[:3])))
        lines.append("")
        lines.append("🎒 书包备忘：")
        for seg in wrap_cjk(notes, DEFAULT_COLS):
            lines.append(f"  {seg}")

    else:
        page_title = f"📜 伴学屏保{mode_tag}"
        lines.append("【伴学桌面屏保】")
        lines.append("劳逸结合，静心专注。")

    default_hint = "▲/▼切分类 · ●换一张 · 长按▼返回菜单 · 按住●问学伴"
    f = dict(footer) if footer else {}
    if "hint" not in f:
        f["hint"] = default_hint
    res = render_lines(page_title, "\n".join(lines), mode="screensaver", footer=f)
    if "raw_1bpp_b64" in card and card["raw_1bpp_b64"]:
        res["raw_1bpp_b64"] = card["raw_1bpp_b64"]
    if "raw_4bpp_b64" in card and card["raw_4bpp_b64"]:
        res["raw_4bpp_b64"] = card["raw_4bpp_b64"]
    if partial:
        res["partial"] = True
    return res


def render_chat_companion_welcome(*, footer: Optional[Dict[str, Any]] = None,
                                  battery: Optional[int] = None) -> Dict[str, Any]:
    """PocketClaw AI学伴欢迎页整屏（400×300墨水屏）。
    9 行饱满排版（首行标题 + 8 行正文），提示按住中间键说话、长按下方按键退出、5分钟无操作自动返回。
    """
    body = [
        "我是你的专属AI学伴小龙虾",
        "",
        "遇到作业难题、生字词理解，",
        "历史科学百科，或分享心情，",
        "按住中间键，随时与我对话！",
        "",
        "对话停止5分钟后自动退出",
        "长按下方按键退出",
    ]
    f = dict(footer) if footer else {}
    if "hint" not in f:
        f["hint"] = "按住中间键说话 · 长按下方按键退出"
    if battery is not None and "battery" not in f:
        f["battery"] = battery
    return render_lines("AI学伴 · 随时互动", "\n".join(body), mode="chat", footer=f)


def _slice_title(title: str, offset: int = 0, max_cols: int = 30) -> str:
    """按显示宽度截取标题窗口（充分利用屏幕右侧空间至 30 列，并支持长标题横向走马灯滚动）。"""
    t = title.strip()
    if _display_width(t) <= max_cols:
        return t

    sub = t[offset:] if offset < len(t) else t
    if _display_width(sub) <= max_cols:
        return sub

    res = ""
    cur_w = 0
    limit = max_cols - 2  # 预留 2 列给省略号
    for ch in sub:
        w = _char_cols(ch)
        if cur_w + w > limit:
            break
        res += ch
        cur_w += w
    return res + "…"


def render_news_list(items: List[Dict[str, Any]], sel_idx: int = 0,
                     region: str = "全国", *, footer: Optional[Dict[str, Any]] = None,
                     partial: bool = False, scroll_offset: int = 0) -> Dict[str, Any]:
    """小学新鲜事列表页面：展示标题和简单介绍。
    支持物理按键 ▲/▼ 移动选中报道，● 确认查看详细报道。
    选中的报道黑底反白高亮指示。
    - 标题采用 24px 大字号（醒目），不加「活动·江苏省」等冗余分类地区前缀。
    - 摘要采用 16px 小字号（FONT_16_MARK），容纳更多字数，与标题层次分明。
    - 一页展示 3 则消息（page_size = 3），排版疏密得当。
    """
    total = len(items)
    sel_idx = max(0, min(sel_idx, total - 1)) if total > 0 else 0
    page_size = 3
    page_idx = sel_idx // page_size if total > 0 else 0
    total_pages = max(1, (total + page_size - 1) // page_size)

    lines = []
    if total == 0:
        lines = [
            "当前设定的地区暂无新鲜事",
            "",
            "· 请在家长端切换关注地区（如：全国）",
            "· 或在家长端添加自定义关注信源",
            "",
            "长按下方按键可返回主菜单",
        ]
    else:
        start = page_idx * page_size
        end = min(start + page_size, total)

        for i in range(start, end):
            it = items[i]
            is_sel = (i == sel_idx)
            raw_title = it.get('title', '无标题')
            summary = it.get('summary', '')
            src = it.get('source', '')

            # 标题：24px 字体，纯净展示，不加 [分类·地区] 前缀
            # 扩展右侧展示空间至 30 列（15 汉字）；选中的超长标题支持动态走马灯滚动（scroll_offset）
            title_clean = screen_text(raw_title).strip()
            if is_sel and scroll_offset > 0:
                t_str = _slice_title(title_clean, scroll_offset, max_cols=30)
            else:
                t_str = _slice_title(title_clean, 0, max_cols=30)

            if is_sel:
                lines.append(f"{INVERT_MARK}> {t_str} {INVERT_END}")
            else:
                lines.append(f"  {t_str}")

            # 摘要：16px 小字号排版（行首带 FONT_16_MARK 哨兵），可容纳 1~2 行详细介绍
            sum_clean = screen_text(summary).strip()
            if sum_clean:
                # 16px 屏幕宽度约 44 列半角（22 汉字），缩进 2 空格
                sum_lines = wrap_cjk(sum_clean, 42)
                lines.append(f"{FONT_16_MARK}  {sum_lines[0]}")
                if len(sum_lines) >= 2:
                    l2 = sum_lines[1]
                    if len(sum_lines) > 2 and _display_width(l2) > 38:
                        l2 = l2[:18] + "…"
                    if src and _display_width(l2) + _display_width(f" ({src})") <= 42:
                        l2 = f"{l2} ({src})"
                    lines.append(f"{FONT_16_MARK}  {l2}")
                elif src:
                    if _display_width(sum_lines[0]) + _display_width(f" ({src})") <= 42:
                        lines[-1] = f"{FONT_16_MARK}  {sum_lines[0]} ({src})"
            else:
                lines.append(f"{FONT_16_MARK}  (点击 ● 键查看详细报道与语音播报)")

            # 资讯之间留 1 行透气空行（在固件中空行仅占 8px 高度）
            if i < end - 1:
                lines.append("")

    reg_label = region if region and region != "全国" else "全国"
    title_bar = f"📰 小学新鲜事 ({page_idx + 1}/{total_pages}页 · {reg_label})"
    default_hint = "▲/▼选报道 · ●看详情 · 长按▼返回菜单 · 按住●问学伴"
    f = dict(footer) if footer else {}
    if "hint" not in f:
        f["hint"] = default_hint
    return {
        "schema_version": "pc-0.1",
        "mode": "news",
        "title": screen_text(title_bar),
        "body": lines,
        "selected_idx": sel_idx,
        "partial": partial,
        "truncated": False,
        "footer": f,
    }


def render_news_detail(item: Dict[str, Any], *, footer: Optional[Dict[str, Any]] = None,
                       partial: bool = False) -> Dict[str, Any]:
    """小学新鲜事详细报道页面：
    全屏采用 16px 精致字号排版（字号适中不臃肿），结构化分条呈现核心看点，简洁明了。
    长按下方按键返回「小学新鲜事」主页，单击确认键语音播报。
    """
    cat = item.get("category", "活动")
    reg = item.get("region", "全国")
    src = item.get("source", "官方发布")
    title = item.get("title", "报道详情")
    summary = item.get("summary", "")
    content = item.get("content") or summary or ""
    points = item.get("points") or item.get("highlights")

    lines = []
    # 1. 标题（16px 字号，娟秀适中，不采用臃肿的 24px 大字），44 列单行或两行
    title_clean = screen_text(title).strip()
    t_lines = wrap_cjk(title_clean, 44)
    for tl in t_lines[:2]:
        lines.append(f"{FONT_16_MARK}{tl}")

    # 2. 属性信息栏（16px）
    date_str = item.get("date", "")
    if not date_str and item.get("ts"):
        date_str = time.strftime("%m-%d", time.localtime(item["ts"]))
    meta_parts = []
    if src:
        meta_parts.append(f"信源: {src}")
    if date_str:
        meta_parts.append(f"时间: {date_str}")
    if reg:
        meta_parts.append(f"地区: {reg}")
    meta_line = "  ·  ".join(meta_parts)
    for ml in wrap_cjk(meta_line, 44):
        lines.append(f"{FONT_16_MARK}{ml}")
    lines.append(f"{FONT_16_MARK}──────────────────────────────────")

    # 3. 结构化要点（16px）：分条展示，简洁清晰，杜绝大段无序文字
    if not points:
        raw_sentences = [s.strip() for s in _re.split(r"[。！；]", content) if s and len(s.strip()) >= 6]
        if summary and (not raw_sentences or summary.strip("。") != raw_sentences[0]):
            clean_sum = summary.strip("。")
            points = [f"· 核心看点：{clean_sum}。"]
            for s in raw_sentences[:2]:
                points.append(f"· 详细说明：{s}。")
        elif raw_sentences:
            points = [f"· 要点{i+1}：{s}。" for i, s in enumerate(raw_sentences[:3])]
        else:
            points = [f"· 报道简介：{content}"]

    for pt in points:
        pt_clean = screen_text(pt).strip()
        pt_lines = wrap_cjk(pt_clean, 44)
        if pt_lines:
            lines.append(f"{FONT_16_MARK}{pt_lines[0]}")
            for pl in pt_lines[1:]:
                lines.append(f"{FONT_16_MARK}  {pl}")

    title_bar = f"📰 详细报道 · {cat}"
    default_hint = "●语音播报 · 长按下方按键返回主页 · 按住●问学伴"
    f = dict(footer) if footer else {}
    if "hint" not in f:
        f["hint"] = default_hint
    return {
        "schema_version": "pc-0.1",
        "mode": "news",
        "title": screen_text(title_bar),
        "body": lines,
        "partial": partial,
        "truncated": False,
        "footer": f,
    }


def render_lines(title: str, body: str, *, cols: int = DEFAULT_COLS,
                 body_rows: int = DEFAULT_BODY_ROWS, mode: str = "chat",
                 footer: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """通用整屏：给标题 + 正文（记想法确认、单词卡、提醒都走它）。标题与正文先按字库清洗再折行。"""
    title_lines = wrap_cjk(screen_text(title), TITLE_COLS)
    body_lines, truncated = _clip_body(wrap_cjk(screen_text(body), cols), body_rows)
    return {
        "schema_version": "pc-0.1",
        "mode": mode,
        "title": title_lines[0] if title_lines else "",
        "body": body_lines,
        "truncated": truncated,
        "footer": footer or {},
    }
