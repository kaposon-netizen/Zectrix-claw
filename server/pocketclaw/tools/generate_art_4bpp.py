#!/usr/bin/env python3
"""Generate high-clarity 400x300 4bpp (16-grayscale, 60000 bytes) masterpieces for Zectrix Note 4 EPD.

Specifically curated and locally cropped for 4.2" 400x300 16-grayscale E-Ink:
- Replaces low-contrast/muddy color-dependent pieces (such as 千里江山图 & 日出·印象 & 向日葵)
  with high-clarity ink, woodblock, engraving, and iconic close-up details (局部).
- Cleans paper/sky backgrounds to pure level-15 white so E-Ink reflectance is maximized.
"""
import pathlib
import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

WIDTH = 400
HEIGHT = 300
BAR_H = 26
ART_H = HEIGHT - BAR_H  # 274

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
OUT_DIR = BASE_DIR / "card_images"
SRC_DIR = OUT_DIR / "sources"
PREVIEW_DIR = OUT_DIR / "previews"
MOUNTAIN_SRC = OUT_DIR / "mountain_src.png"

FONT_PATH = "/System/Library/Fonts/Hiragino Sans GB.ttc"

ART_ITEMS = [
    {
        "id": "starry_night",
        "src": "starry_night.jpg",
        "title": "星月夜",
        "artist": "文森特·梵高 (1889)",
        "box": (0.0, 0.02, 1.0, 0.94),
        "mode": "starry",
    },
    {
        "id": "ma_yuan_spring",
        "src": "ma_yuan_spring.jpg",
        "title": "山径春行图(局部)",
        "artist": "南宋 · 马远",
        "box": (0.04, 0.10, 0.92, 0.92),
        "mode": "silk_clean",
        "legacy_alias": "thousand_li",
    },
    {
        "id": "mona_lisa",
        "src": "mona_lisa.jpg",
        "title": "蒙娜丽莎(局部)",
        "artist": "达·芬奇 (1503)",
        "box": (0.16, 0.04, 0.84, 0.44),
        "mode": "mona_v2",
    },
    {
        "id": "creation_adam",
        "src": "creation_detail.jpg",
        "title": "创造亚当(局部)",
        "artist": "米开朗基罗 (1512)",
        "box": (0.0, 0.0, 1.0, 1.0),
        "mode": "clean_fresco",
        "legacy_alias": "sunrise_impression",
    },
    {
        "id": "fuchun_mountain",
        "src": "fuchun_mountain.jpg",
        "title": "富春山居图(局部)",
        "artist": "元代 · 黄公望",
        "box": (0.24, 0.0, 0.96, 1.0),
        "mode": "ink_scroll",
    },
    {
        "id": "great_wave",
        "src": "great_wave.jpg",
        "title": "神奈川冲浪里",
        "artist": "葛饰北斋 (1831)",
        "box": (0.0, 0.04, 0.94, 0.96),
        "mode": "wave_clean",
    },
    {
        "id": "red_fuji",
        "src": "red_fuji.jpg",
        "title": "凯风快晴·赤富士",
        "artist": "葛饰北斋 (1831)",
        "box": (0.02, 0.02, 0.98, 0.98),
        "mode": "fuji",
        "legacy_alias": "sunflowers",
    },
    {
        "id": "praying_hands",
        "src": "praying_hands.jpg",
        "title": "祈祷之手(局部)",
        "artist": "丢勒 (1508)",
        "box": (0.12, 0.16, 0.88, 0.78),
        "mode": "duerer_hands",
        "legacy_alias": "pearl_earring",
    },
    {
        "id": "mountain",
        "src": "mountain",
        "title": "崇山峻岭",
        "artist": "水墨写生 · 16灰阶",
        "box": (0.0, 0.0, 1.0, 0.913),
        "mode": "mountain_native",
    },
]


def local_tone_map(arr: np.ndarray, strength: float = 0.45) -> np.ndarray:
    pil_im = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    blur_small = np.asarray(pil_im.filter(ImageFilter.GaussianBlur(radius=5)), dtype=np.float32)
    blur_large = np.asarray(pil_im.filter(ImageFilter.GaussianBlur(radius=24)), dtype=np.float32)
    detail = arr - blur_small
    mid = arr - blur_large
    out = arr + strength * 1.2 * detail + strength * 0.6 * mid
    p2, p98 = np.percentile(out, (2.0, 98.0))
    if p98 > p2:
        out = (out - p2) * 255.0 / (p98 - p2)
    return np.clip(out, 0, 255)


def render_art_gray(cropped_rgb: Image.Image, mode: str) -> Image.Image:
    arr = np.asarray(cropped_rgb.convert("RGB"), dtype=np.float32)
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]

    if mode == "starry":
        gray = 0.32 * r + 0.52 * g + 0.16 * b
        gray = local_tone_map(gray, strength=0.45)
        p4, p92 = np.percentile(gray, (4, 92))
        gray = (gray - p4) * 255.0 / max(1.0, (p92 - p4))
        gray = 255.0 * np.power(np.clip(gray / 255.0, 0.0, 1.0), 0.90)
        pil_g = Image.fromarray(np.clip(gray, 0, 255).astype(np.uint8))
        pil_g = ImageEnhance.Contrast(pil_g).enhance(1.38)
        return ImageEnhance.Sharpness(pil_g).enhance(1.7)

    elif mode == "silk_clean":
        gray = 0.50 * r + 0.40 * g + 0.10 * b
        pil_im = Image.fromarray(np.clip(gray, 0, 255).astype(np.uint8)).filter(ImageFilter.MedianFilter(size=3))
        gray = np.asarray(pil_im, dtype=np.float32)
        p3, p72 = np.percentile(gray, (3, 72))
        gray = (gray - p3) * 255.0 / max(1.0, (p72 - p3))
        pil_g = Image.fromarray(np.clip(gray, 0, 255).astype(np.uint8))
        pil_g = ImageEnhance.Contrast(pil_g).enhance(1.35)
        return ImageEnhance.Sharpness(pil_g).enhance(1.7)

    elif mode == "mona_v2":
        base = 0.45 * r + 0.45 * g + 0.10 * b
        smooth = np.asarray(
            Image.fromarray(np.clip(base, 0, 255).astype(np.uint8)).filter(ImageFilter.MedianFilter(size=3)),
            dtype=np.float32,
        )
        p5, p95 = np.percentile(smooth, (5, 95))
        out = (smooth - p5) * 255.0 / max(1.0, (p95 - p5))
        out = 255.0 * np.power(np.clip(out / 255.0, 0.0, 1.0), 0.68)
        pil_g = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))
        pil_g = ImageEnhance.Contrast(pil_g).enhance(1.28)
        return ImageEnhance.Sharpness(pil_g).enhance(1.55)

    elif mode == "clean_fresco":
        gray = 0.35 * r + 0.50 * g + 0.15 * b
        pil_im = Image.fromarray(np.clip(gray, 0, 255).astype(np.uint8)).filter(ImageFilter.MedianFilter(size=3))
        gray = np.asarray(pil_im, dtype=np.float32)
        p4, p76 = np.percentile(gray, (4, 76))
        gray = (gray - p4) * 255.0 / max(1.0, (p76 - p4))
        pil_g = Image.fromarray(np.clip(gray, 0, 255).astype(np.uint8))
        pil_g = ImageEnhance.Contrast(pil_g).enhance(1.32)
        return ImageEnhance.Sharpness(pil_g).enhance(1.65)

    elif mode == "ink_scroll":
        gray = 0.35 * r + 0.50 * g + 0.15 * b
        p5, p78 = np.percentile(gray, (5, 78))
        gray = (gray - p5) * 255.0 / max(1.0, (p78 - p5))
        pil_g = Image.fromarray(np.clip(gray, 0, 255).astype(np.uint8))
        pil_g = ImageEnhance.Contrast(pil_g).enhance(1.35)
        return ImageEnhance.Sharpness(pil_g).enhance(1.8)

    elif mode == "wave_clean":
        gray = 0.30 * r + 0.50 * g + 0.20 * b
        p4, p82 = np.percentile(gray, (4, 82))
        gray = (gray - p4) * 255.0 / max(1.0, (p82 - p4))
        pil_g = Image.fromarray(np.clip(gray, 0, 255).astype(np.uint8))
        pil_g = ImageEnhance.Contrast(pil_g).enhance(1.32)
        return ImageEnhance.Sharpness(pil_g).enhance(1.8)

    elif mode == "fuji":
        gray = 0.15 * r + 0.35 * g + 0.50 * b
        p5, p92 = np.percentile(gray, (5, 92))
        gray = (gray - p5) * 255.0 / max(1.0, (p92 - p5))
        pil_g = Image.fromarray(np.clip(gray, 0, 255).astype(np.uint8))
        pil_g = ImageEnhance.Contrast(pil_g).enhance(1.32)
        return ImageEnhance.Sharpness(pil_g).enhance(1.75)

    elif mode == "duerer_hands":
        gray = 0.30 * r + 0.55 * g + 0.15 * b
        p6, p90 = np.percentile(gray, (6, 90))
        gray = (gray - p6) * 255.0 / max(1.0, (p90 - p6))
        pil_g = Image.fromarray(np.clip(gray, 0, 255).astype(np.uint8))
        pil_g = ImageEnhance.Contrast(pil_g).enhance(1.28)
        return ImageEnhance.Sharpness(pil_g).enhance(1.6)

    elif mode == "mountain_native":
        pil_g = cropped_rgb.convert("L")
        pil_g = ImageEnhance.Contrast(pil_g).enhance(1.12)
        return ImageEnhance.Sharpness(pil_g).enhance(1.2)

    return ImageOps.autocontrast(cropped_rgb.convert("L"), cutoff=1)


def process_painting(idx: int, total: int, spec: dict) -> tuple[Image.Image, bytes]:
    if spec["src"] == "mountain":
        src_rgb = Image.open(MOUNTAIN_SRC).convert("RGB")
    else:
        src_rgb = Image.open(SRC_DIR / spec["src"]).convert("RGB")

    w, h = src_rgb.size
    bx0, by0, bx1, by1 = spec.get("box", (0.0, 0.0, 1.0, 1.0))
    sub_rgb = src_rgb.crop((int(w * bx0), int(h * by0), int(w * bx1), int(h * by1)))
    cropped_rgb = ImageOps.fit(
        sub_rgb,
        (WIDTH, ART_H),
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.5),
    )

    gray = render_art_gray(cropped_rgb, spec.get("mode", "standard"))

    # Compose full 400x300 canvas with bottom 26px museum gallery bar
    canvas = Image.new("L", (WIDTH, HEIGHT), 255)
    canvas.paste(gray, (0, 0))

    draw = ImageDraw.Draw(canvas)
    draw.rectangle([0, ART_H, WIDTH - 1, ART_H + 1], fill=0)
    draw.rectangle([0, ART_H + 2, WIDTH - 1, HEIGHT - 1], fill=255)

    try:
        font_title = ImageFont.truetype(FONT_PATH, 15)
        font_meta = ImageFont.truetype(FONT_PATH, 14)
    except Exception:
        font_title = ImageFont.load_default()
        font_meta = ImageFont.load_default()

    left_text = f"《{spec['title']}》 {spec['artist']}"
    right_text = f"[{idx + 1}/{total}] ●换画"

    draw.text((6, ART_H + 5), left_text, fill=0, font=font_title)
    rb = draw.textbbox((0, 0), right_text, font=font_meta)
    rw = rb[2] - rb[0]
    draw.text((WIDTH - rw - 6, ART_H + 6), right_text, fill=0, font=font_meta)

    raw_bytes = bytearray(canvas.tobytes())
    levels = []
    for i, val in enumerate(raw_bytes):
        y = i // WIDTH
        if y >= ART_H:
            levels.append(0 if val < 150 else 15)
        else:
            levels.append((val * 15 + 127) // 255)

    preview = Image.frombytes("L", (WIDTH, HEIGHT), bytes(lv * 17 for lv in levels))
    packed = bytearray()
    for i in range(0, len(levels), 2):
        packed.append((levels[i] << 4) | levels[i + 1])

    return preview, bytes(packed)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)

    grid = Image.new("L", (WIDTH * 3 + 40, HEIGHT * 3 + 40), 220)
    total = len(ART_ITEMS)

    for idx, spec in enumerate(ART_ITEMS):
        preview, packed = process_painting(idx, total, spec)
        art_id = spec["id"]
        bin_path = OUT_DIR / f"{art_id}.bin"
        bin_path.write_bytes(packed)
        print(f"[{idx+1}/{total}] {art_id:20s} -> {bin_path} ({len(packed)} bytes)")

        alias = spec.get("legacy_alias")
        if alias:
            alias_path = OUT_DIR / f"{alias}.bin"
            alias_path.write_bytes(packed)
            print(f"       legacy_alias {alias:14s} -> {alias_path}")

        gx = 10 + (idx % 3) * (WIDTH + 10)
        gy = 10 + (idx // 3) * (HEIGHT + 10)
        grid.paste(preview, (gx, gy))

    grid_path = PREVIEW_DIR / "new_art_4bpp_grid_v2.png"
    grid.save(grid_path)
    print(f"Saved preview grid to {grid_path}")


if __name__ == "__main__":
    main()
