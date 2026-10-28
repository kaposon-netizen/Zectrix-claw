"""Decode a device framebuffer for a future authenticated screen capture endpoint.

1bpp: device framebuffer uses MSB-first pixels with 1=white. 4bpp: two
pixels per byte, high nibble first, 0=black and 15=white.
"""
from __future__ import annotations
import io
from PIL import Image

WIDTH, HEIGHT = 400, 300


def framebuffer_png(data: bytes, bits_per_pixel: int) -> bytes:
    if bits_per_pixel == 1:
        if len(data) != WIDTH * HEIGHT // 8:
            raise ValueError('invalid 1bpp framebuffer size')
        image = Image.frombytes('1', (WIDTH, HEIGHT), data).convert('L')
    elif bits_per_pixel == 4:
        if len(data) != WIDTH * HEIGHT // 2:
            raise ValueError('invalid 4bpp framebuffer size')
        pixels = bytearray(WIDTH * HEIGHT)
        for i, byte in enumerate(data):
            pixels[i * 2] = (byte >> 4) * 17
            pixels[i * 2 + 1] = (byte & 15) * 17
        image = Image.frombytes('L', (WIDTH, HEIGHT), bytes(pixels))
    else:
        raise ValueError('unsupported framebuffer format')
    out = io.BytesIO()
    image.save(out, format='PNG')
    return out.getvalue()
