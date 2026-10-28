"""单词查询（#1 语音单词卡）：把一个词交给 LLM，产出 {word, reading, meaning, example}。

这是**词典查询**，不是替孩子写作业 —— 属于产品核心功能（单词卡），走一个专用的严格 JSON
提示，与苏格拉底式辅导（runtime.turn）分开。离线/解析失败时优雅降级为占位卡（仍可入卡复习）。

用词典口径：中文释义 + 一个适龄短例句；英文词给音标，中文词给拼音。适龄由 grade_band 提示。
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

_SYS = (
    "你是儿童单词卡词典。只输出一个 JSON 对象，字段："
    "word(原词)、reading(英文音标或中文拼音)、meaning(一句话中文释义，适龄、简单)、"
    "example(一个适龄英文例句，不超过 8 个单词；若原词是中文，则给一个不超过 12 字的中文例句)、"
    "example_cn(example 的中文翻译；若 example 本就是中文则留空)。"
    "不要输出别的话，不要代写作业，不要解题。"
)


def _extract_word(text: str) -> str:
    """从「apple 什么意思 / 查单词 photosynthesis / “光合作用”怎么读」里抽出要查的词。"""
    t = text.strip()
    for kw in ("是什么意思", "什么意思", "怎么读", "怎么写", "的意思", "的读音", "查单词", "查词", "查一下", "查"):
        t = t.replace(kw, " ")
    t = t.strip(" 。，,.?？!！\"'“”‘’")
    # 取最长的一段连续「词」（中英数）
    cands = re.findall(r"[A-Za-z][A-Za-z\-']*|[一-鿿]+", t)
    return max(cands, key=len) if cands else t


def _parse(reply: str, word: str) -> Optional[Dict[str, str]]:
    m = re.search(r"\{.*\}", reply, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(d, dict) or not d.get("meaning"):
        return None
    return {"word": str(d.get("word") or word), "reading": str(d.get("reading") or ""),
            "meaning": str(d.get("meaning") or ""), "example": str(d.get("example") or ""),
            "example_cn": str(d.get("example_cn") or "")}


async def lookup_word(llm, text_or_word: str, *, grade_band: str = "g3_g6",
                      is_raw_word: bool = False) -> Dict[str, Any]:
    """返回 {word, reading, meaning, example, resolved}；resolved=False 表示离线未查到（占位）。"""
    word = text_or_word.strip() if is_raw_word else _extract_word(text_or_word)
    if not word:
        return {"word": "", "reading": "", "meaning": "", "example": "", "resolved": False}
    user = f"学段 {grade_band}。查这个词：{word}"
    try:
        reply = await llm.complete(_SYS, user, max_tokens=200, trace={"phase": "chat"})
    except Exception:
        reply = ""
    parsed = _parse(reply, word)
    if parsed:
        parsed["resolved"] = True
        return parsed
    # 离线/失败兜底：仍给一张可保存的卡，释义留待联网补全
    return {"word": word, "reading": "", "meaning": "（联网或配置模型后自动补全释义）",
            "example": "", "resolved": False}
