"""生成便利贴「花火番茄钟」大号数字的 1bpp 位图字模头文件（固件 main/focus_digits.h）。

字体：冬青黑体 Hiragino Sans GB（或指定 TTF/TTC）；
字号按 400×300 墨水屏右半区排版取 ~100px 高，每个字形裁到实际宽度，行优先 MSB-first，1=黑。
用法：python -m pocketclaw.tools.gen_focus_digits [font.ttc/ttf] [out.h] [ttc_index]
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

GLYPHS = "0123456789:"
HEIGHT = 100          # 字模统一高度（像素）
FONT_PX = 118         # 字号：数字高度约 100px


def render_glyph(font: ImageFont.FreeTypeFont, ch: str):
    # 灰度画布：黑底白字，getbbox 直接得墨迹外框；>120 记为墨（1=黑，边缘更饱满平整）
    img = Image.new("L", (FONT_PX * 2, FONT_PX * 2), 0)
    d = ImageDraw.Draw(img)
    d.text((FONT_PX // 2, FONT_PX // 4), ch, font=font, fill=255)
    bbox = img.getbbox()
    if not bbox:
        return 8, [bytes(1)] * HEIGHT
    g = img.crop(bbox)
    if ch == ":":
        w = max(1, int(round(g.width * HEIGHT / 100)))
        h = max(1, int(round(g.height * w / max(1, g.width))))
        g = g.resize((w, h), Image.LANCZOS)
        canvas = Image.new("L", (w, HEIGHT), 0)
        canvas.paste(g, (0, (HEIGHT - h) // 2))
        g = canvas
    else:
        w = max(1, int(round(g.width * HEIGHT / max(1, g.height))))
        g = g.resize((w, HEIGHT), Image.LANCZOS)
    stride = (w + 7) // 8
    px = g.load()
    rows = []
    for y in range(HEIGHT):
        row = bytearray(stride)
        for x in range(w):
            if px[x, y] > 120:
                row[x >> 3] |= 1 << (7 - (x & 7))
        rows.append(bytes(row))
    return w, rows


def main(ttf: str = "/System/Library/Fonts/Hiragino Sans GB.ttc",
         out: str = "focus_digits.h",
         index: int = 0) -> None:
    font = ImageFont.truetype(ttf, FONT_PX, index=index)
    parts = ["// 自动生成：python -m pocketclaw.tools.gen_focus_digits（冬青黑体 Hiragino Sans GB）",
             "// 花火番茄钟大号数字字模：1bpp，行优先，MSB-first，1=黑；高度统一 FOCUS_DIGIT_H",
             "#pragma once", "#include <stdint.h>", "", f"#define FOCUS_DIGIT_H {HEIGHT}", ""]
    widths, offsets, blob = [], [], bytearray()
    for ch in GLYPHS:
        w, rows = render_glyph(font, ch)
        widths.append(w)
        offsets.append(len(blob))
        for r in rows:
            blob += r
    parts.append("static const uint8_t FOCUS_DIGIT_W[%d] = {%s};" % (len(GLYPHS), ", ".join(map(str, widths))))
    parts.append("static const uint32_t FOCUS_DIGIT_OFF[%d] = {%s};" % (len(GLYPHS), ", ".join(map(str, offsets))))
    parts.append("// 字形顺序：\"%s\"（下标 10 = 冒号）" % GLYPHS)
    parts.append("static const uint8_t FOCUS_DIGIT_BITS[%d] = {" % len(blob))
    for i in range(0, len(blob), 24):
        parts.append("  " + ", ".join("0x%02X" % b for b in blob[i:i + 24]) + ",")
    parts.append("};")
    Path(out).write_text("\n".join(parts) + "\n", encoding="utf-8")
    print(f"wrote {out}: {len(GLYPHS)} glyphs, {len(blob)} bytes, widths={widths}")


if __name__ == "__main__":
    ttf_arg = sys.argv[1] if len(sys.argv) > 1 else "/System/Library/Fonts/Hiragino Sans GB.ttc"
    out_arg = sys.argv[2] if len(sys.argv) > 2 else "focus_digits.h"
    idx_arg = int(sys.argv[3]) if len(sys.argv) > 3 else 0
    main(ttf_arg, out_arg, idx_arg)
