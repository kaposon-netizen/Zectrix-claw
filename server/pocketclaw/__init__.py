"""PocketClaw · Zectrix Note 4 小学生智能学习伴侣服务端。

形态：ZECTRIX Note 4 · ESP32-S3 双核 · 4.2" 墨水屏 400×300 16灰阶硬件直驱 · 纯物理三键盲操 + ES8311 音频。
开机即“今天清单”专注哲学：今日作业、生字听写、必背古诗、口算闯关、英语短文阅读、花火番茄钟、名画屏保等 18 大伴学引擎。

架构设计：**智能全在服务端，设备做高响应低延迟的伴学客户端**。
提供 WebSocket 全双工流式链路 + 家长端 Web 管理后台 + 微信原生小程序端 + OCR 视觉识图接口。
"""
from __future__ import annotations

__all__ = [
    "protocol",
    "voice",
    "display",
    "notes",
    "device_registry",
    "bridge",
    "endpoint",
]
