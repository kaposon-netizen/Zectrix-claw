"""PocketClaw Note 4 FastAPI 端点路由与家长端 Web 门户。

  register_pocketclaw(app, runtime, controls=..., notes_path=..., devices_path=...)

提供：
  · WS  /pocketclaw            —— T1 语音：设备 hello/ptt/nfc/button/note，服务端 STT→脑→TTS+上屏。
  · REST /api/pocketclaw/health
  · REST /api/pocketclaw/devices                     家长端看设备/绑定/电量/待推送
  · REST /api/pocketclaw/devices/{id}/bind           绑定当前使用孩子
  · REST /api/pocketclaw/devices/{id}/push           T0 内容推送（把一屏内容入队，设备同步取走）
  · REST /api/pocketclaw/devices/{id}/sync           设备同步：取走待推送内容（零固件 T0 路径）
  · REST /api/pocketclaw/cards/register              家长端登记一张 NFC 卡 → child:/skill:/mode:
  · REST /api/pocketclaw/{child}/notes|words         记想法/单词卡（家长端只读聚合 + 孩子端管理）

隐私：notes/words 逐字只在 child 维度返回给「设备/孩子端」；家长报表只拿 aggregate()。

鉴权（2026-09-28 审查「按设备寻址、不校验归属」）：家长 / 平板端按**设备或卡号**寻址的六条
（/devices 列表、/devices/{id}/bind|push|focus|push_tasks、/cards/register）由本文件的处理器**自己**校，
中间件整条放行（authz.policy._pocket_self_guarded）。判据在 `_authenticate` + `_Caller.owns`：
  · 管理后台口令（X-Admin-Token，app 挂进来的 admin_check）→ 管全部（不受 ACL 模式影响）；
  · 否则必须是一把有效的家长 / 设备令牌（authz.gate.check，含注销名单），缺 / 无效 → 401；
  · 家长令牌：设备当前绑定的孩子 ∈ 令牌的 children；设备令牌：设备当前绑定的孩子 == 令牌的 child_id
    （便利贴固件没有令牌，现有的设备令牌全是平板 / SnapClaw 的，它们和便利贴唯一的共同锚点是孩子）；
  · 未绑定 / 不存在的设备只允许 /bind；不属于 → 403（对非管理员同一文案，不给「设备存不存在」当探针）；
  · 401 / 403 一律经安全拦截，与中间件同一口径：强制模式拒；观察模式（ACL_SOFT=1）
    该拒的照样放行、留一行 ACLSOFT（这几条不在 legacy 强制档，逃生阀对它们有效）；
  · 设备表只读降级 → 503「设备登记暂时不可用」（先过身份闸再报：强制模式匿名照样 401）。
设备侧（WS /pocketclaw、/devices/{id}/sync、/{child}/notes|words|tasks|book…）不变。
"""
from __future__ import annotations

import asyncio
import base64
import binascii
from contextvars import ContextVar
import json
import logging
import re
import time
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from pocketclaw import display, practice, protocol, reading
from pocketclaw.bridge import PocketClawSession
from pocketclaw.device_registry import (LEGACY_DEFAULT_CHILD, Device, DeviceRegistry, RegistryReadOnly,
                                        _card_target, is_unbound)
from pocketclaw.notes import NotesStore
from pocketclaw.voice import make_stt, make_tts

log = logging.getLogger("pocketclaw")
family_allowed_children: ContextVar[Optional[frozenset[str]]] = ContextVar('family_allowed_children', default=None)

class ChildProfile(BaseModel):
    name: str = Field(default="", max_length=40)
    grade: int = Field(default=0, ge=0, le=6, strict=True)
    gender: Literal["", "男", "女", "其他"] = ""
    interests: str = Field(default="", max_length=200)

class PresetImport(BaseModel):
    category: Literal['words', 'idioms', 'poems', 'english']
    ids: List[str] = Field(default_factory=list, max_length=200)
    child_id: Optional[str] = None

# 家长看得到的几句拒绝文案（平实，不带内部术语）
_MSG_NOT_YOURS = "这台便利贴没有绑定到你家的孩子，不能操作"
_MSG_ADMIN_UNBOUND = "这台便利贴还没有绑定孩子，只能先绑定"
_MSG_REGISTRY_DOWN = "便利贴的设备登记暂时不可用，请稍后再试"

_HTML_PARENT_PORTAL = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
<title>PocketClaw · 小学生全能伴学控制台 (Note 4 墨水屏)</title>
<style>

  :root {
    --primary: #4f46e5;
    --primary-hover: #4338ca;
    --primary-light: #eef2ff;
    --primary-border: #c7d2fe;
    --accent: #2563eb;
    --bg: #f8fafc;
    --card: #ffffff;
    --card-subtle: #fbfcfe;
    --text: #0f172a;
    --text-secondary: #475569;
    --muted: #64748b;
    --muted-light: #94a3b8;
    --border: #e2e8f0;
    --border-subtle: #f1f5f9;
    --success: #10b981;
    --success-bg: #ecfdf5;
    --success-border: #a7f3d0;
    --warning: #f59e0b;
    --warning-bg: #fffbeb;
    --warning-border: #fde68a;
    --danger: #ef4444;
    --danger-bg: #fef2f2;
    --danger-border: #fecaca;
    --tag-chinese: #ea580c;
    --tag-math: #2563eb;
    --tag-english: #7c3aed;
    --tag-sci: #0d9488;
    --radius-sm: 8px;
    --radius-md: 12px;
    --radius-lg: 16px;
    --radius-xl: 20px;
    --shadow-sm: 0 1px 2px rgba(15, 23, 42, 0.04);
    --shadow-md: 0 4px 12px -2px rgba(15, 23, 42, 0.06), 0 1px 3px rgba(15, 23, 42, 0.03);
    --shadow-lg: 0 10px 24px -4px rgba(15, 23, 42, 0.08), 0 2px 6px rgba(15, 23, 42, 0.04);
    --transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  }

  * { box-sizing: border-box; margin: 0; padding: 0; -webkit-tap-highlight-color: transparent; }
  
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.5;
    -webkit-font-smoothing: antialiased;
  }

  /* App Layout Shell */
  .app-shell {
    display: flex;
    min-height: 100vh;
  }

  /* Desktop Left Sidebar (>= 960px) */
  .app-sidebar {
    width: 280px;
    background: #ffffff;
    border-right: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    position: sticky;
    top: 0;
    height: 100vh;
    z-index: 40;
    overflow-y: auto;
    flex-shrink: 0;
    box-shadow: 2px 0 8px rgba(15, 23, 42, 0.02);
  }

  .sidebar-header {
    padding: 18px 20px 14px 20px;
    border-bottom: 1px solid var(--border-subtle);
  }

  .brand-box {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .brand-logo {
    width: 36px;
    height: 36px;
    background: linear-gradient(135deg, #4f46e5 0%, #2563eb 100%);
    border-radius: 10px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 20px;
    box-shadow: 0 2px 8px rgba(79, 70, 229, 0.3);
  }
  .brand-meta h1 {
    font-size: 15px;
    font-weight: 700;
    color: var(--text);
    line-height: 1.2;
  }
  .brand-meta p {
    font-size: 11px;
    color: var(--muted);
    margin-top: 2px;
  }

  /* Note 4 Hardware Island Widget */
  .hardware-card {
    margin: 14px 16px;
    background: #fafaf8;
    border: 1.5px solid #334155;
    border-radius: var(--radius-md);
    padding: 12px;
    box-shadow: inset 0 1px 3px rgba(0,0,0,0.06), 0 2px 6px rgba(0,0,0,0.03);
  }
  .hw-topbar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 11px;
    font-weight: 700;
    color: #475569;
    margin-bottom: 8px;
    letter-spacing: 0.5px;
  }
  .hw-live-status {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 11px;
    margin-bottom: 8px;
  }
  .status-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: #94a3b8;
  }
  .status-dot.online {
    background: #10b981;
    box-shadow: 0 0 0 3px rgba(16, 185, 129, 0.2);
  }
  .hw-screen-preview {
    background: #ffffff;
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    padding: 6px 8px;
    font-size: 11px;
    font-weight: 600;
    color: #1e293b;
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 10px;
  }

  /* Quick Screen Switcher Grid */
  .hw-quick-grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 5px;
  }
  .hw-btn {
    padding: 6px 4px;
    border-radius: 6px;
    border: 1px solid #cbd5e1;
    background: #ffffff;
    font-size: 11px;
    font-weight: 600;
    color: #334155;
    cursor: pointer;
    text-align: center;
    transition: var(--transition);
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 3px;
  }
  .hw-btn:hover {
    background: #f1f5f9;
    border-color: #94a3b8;
    transform: translateY(-1px);
  }
  .hw-btn:active {
    transform: scale(0.97);
  }

  /* Grouped Sidebar Navigation */
  .nav-groups {
    padding: 8px 12px 30px 12px;
    display: flex;
    flex-direction: column;
    gap: 16px;
  }
  .nav-group-title {
    font-size: 11px;
    font-weight: 700;
    color: var(--muted-light);
    text-transform: uppercase;
    letter-spacing: 0.6px;
    padding: 4px 10px;
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .nav-group-items {
    display: flex;
    flex-direction: column;
    gap: 3px;
  }
  .tab-btn {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 9px 12px;
    border-radius: var(--radius-sm);
    border: 1px solid transparent;
    background: transparent;
    color: var(--text-secondary);
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    transition: var(--transition);
    text-align: left;
    width: 100%;
  }
  .tab-btn:hover {
    background: #f1f5f9;
    color: var(--text);
  }
  .tab-btn.active {
    background: var(--primary-light);
    color: var(--primary);
    font-weight: 700;
    border-color: var(--primary-border);
  }
  .tab-btn-title {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .tab-badge {
    font-size: 11px;
    font-weight: 600;
    padding: 2px 6px;
    border-radius: 12px;
    background: #f1f5f9;
    color: var(--muted);
  }
  .tab-btn.active .tab-badge {
    background: #e0e7ff;
    color: var(--primary);
  }

  /* Main Content Area */
  .app-main {
    flex: 1;
    display: flex;
    flex-direction: column;
    min-width: 0;
    background: var(--bg);
  }

  /* Top Navigation Bar */
  .top-navbar {
    background: rgba(255, 255, 255, 0.95);
    backdrop-filter: blur(12px);
    border-bottom: 1px solid var(--border);
    padding: 12px 24px;
    position: sticky;
    top: 0;
    z-index: 30;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
  }
  .top-nav-left {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .top-page-meta h2 {
    font-size: 16px;
    font-weight: 700;
    color: var(--text);
  }
  .top-page-meta p {
    font-size: 12px;
    color: var(--muted);
  }
  .top-nav-badges {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }

  /* Mobile Category Switcher & Sub-tabs (< 960px) */
  .mobile-nav-panel {
    display: none;
    background: #ffffff;
    border-bottom: 1px solid var(--border);
    padding: 8px 12px;
  }
  .mobile-categories {
    display: flex;
    gap: 6px;
    overflow-x: auto;
    scrollbar-width: none;
    padding-bottom: 6px;
  }
  .mobile-categories::-webkit-scrollbar { display: none; }
  .cat-pill {
    flex-shrink: 0;
    padding: 6px 14px;
    border-radius: 20px;
    font-size: 12px;
    font-weight: 600;
    color: var(--muted);
    background: #f1f5f9;
    border: 1px solid transparent;
    cursor: pointer;
  }
  .cat-pill.active {
    background: var(--primary);
    color: #ffffff;
    border-color: var(--primary);
  }
  .mobile-subtabs-row {
    display: flex;
    gap: 6px;
    overflow-x: auto;
    scrollbar-width: none;
    padding-top: 6px;
  }
  .mobile-subtabs-row::-webkit-scrollbar { display: none; }
  .mobile-subtab-btn {
    flex-shrink: 0;
    padding: 5px 12px;
    border-radius: 16px;
    font-size: 12px;
    font-weight: 600;
    color: var(--text-secondary);
    background: #ffffff;
    border: 1px solid var(--border);
    cursor: pointer;
    white-space: nowrap;
  }
  .mobile-subtab-btn.active {
    background: var(--primary-light);
    color: var(--primary);
    border-color: var(--primary-border);
  }

  /* Content Container */
  .content-body {
    padding: 20px 24px 80px 24px;
    max-width: 1280px;
    width: 100%;
    margin: 0 auto;
  }

  /* Tab View Visibility */
  .tab-content { display: none; animation: fadeIn 0.15s ease-out; }
  .tab-content.active { display: block; }
  @keyframes fadeIn {
    from { opacity: 0; transform: translateY(3px); }
    to { opacity: 1; transform: translateY(0); }
  }

  /* Modern Card System */
  .card {
    background: var(--card);
    border-radius: var(--radius-lg);
    border: 1px solid var(--border);
    padding: 20px;
    margin-bottom: 16px;
    box-shadow: var(--shadow-sm);
    transition: var(--transition);
  }
  .card:hover {
    box-shadow: var(--shadow-md);
  }
  .card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 16px;
    padding-bottom: 12px;
    border-bottom: 1px solid var(--border-subtle);
  }
  .card-title {
    font-size: 15px;
    font-weight: 700;
    color: var(--text);
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .card-subtitle {
    font-size: 12px;
    color: var(--muted);
    font-weight: normal;
  }

  /* 2-Column Responsive Tab Grid */
  .tab-grid-2col {
    display: grid;
    grid-template-columns: 1fr;
    gap: 16px;
  }
  @media (min-width: 960px) {
    .tab-grid-2col {
      grid-template-columns: 380px 1fr;
      align-items: start;
      gap: 20px;
    }
    .sticky-col {
      position: sticky;
      top: 80px;
    }
  }

  /* Modern Form Controls */
  .form-group {
    margin-bottom: 14px;
  }
  .form-label {
    display: block;
    font-size: 13px;
    font-weight: 600;
    color: var(--text);
    margin-bottom: 6px;
  }
  .form-label-desc {
    font-size: 11px;
    color: var(--muted);
    font-weight: normal;
    margin-left: 6px;
  }
  
  textarea, input[type="text"], input[type="password"], input[type="date"], select {
    width: 100%;
    padding: 10px 14px;
    border-radius: var(--radius-sm);
    border: 1.5px solid var(--border);
    font-size: 14px;
    color: var(--text);
    outline: none;
    font-family: inherit;
    background: #fdfdfd;
    transition: var(--transition);
  }
  textarea:focus, input[type="text"]:focus, input[type="password"]:focus, input[type="date"]:focus, select:focus {
    border-color: var(--primary);
    background: #ffffff;
    box-shadow: 0 0 0 3px rgba(79, 70, 229, 0.12);
  }
  textarea {
    resize: vertical;
    min-height: 90px;
    line-height: 1.5;
  }
  .form-row {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
  }

  /* Modern Buttons */
  .btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    padding: 9px 16px;
    border-radius: var(--radius-sm);
    font-size: 13px;
    font-weight: 600;
    border: 1px solid transparent;
    cursor: pointer;
    transition: var(--transition);
    user-select: none;
  }
  .btn:active { transform: scale(0.98); }
  .btn-primary {
    background: linear-gradient(135deg, #4f46e5 0%, #2563eb 100%);
    color: #ffffff;
    box-shadow: 0 2px 6px rgba(79, 70, 229, 0.25);
  }
  .btn-primary:hover {
    opacity: 0.95;
    box-shadow: 0 4px 10px rgba(79, 70, 229, 0.35);
  }
  .btn-secondary {
    background: #ffffff;
    color: var(--text-secondary);
    border-color: var(--border);
    box-shadow: var(--shadow-sm);
  }
  .btn-secondary:hover {
    background: #f8fafc;
    color: var(--text);
    border-color: #cbd5e1;
  }
  .btn-danger {
    background: var(--danger-bg);
    color: var(--danger);
    border-color: var(--danger-border);
  }
  .btn-danger:hover {
    background: #fee2e2;
  }
  .btn-sm { padding: 5px 10px; font-size: 12px; border-radius: 6px; }
  .btn-block { width: 100%; }
  .btn-row { display: flex; gap: 8px; margin-top: 10px; flex-wrap: wrap; }

  /* Badges */
  .badge {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 3px 10px;
    border-radius: 20px;
    font-size: 11px;
    font-weight: 600;
  }
  .badge-online { background: #dcfce7; color: #15803d; border: 1px solid #bbf7d0; }
  .badge-offline { background: #f1f5f9; color: #64748b; border: 1px solid #e2e8f0; }
  .badge-battery { background: #eff6ff; color: #1d4ed8; border: 1px solid #dbeafe; }
  .badge-success { background: #dcfce7; color: #15803d; }

  /* Subject Badges */
  .subject-pill {
    padding: 2px 8px;
    border-radius: 6px;
    font-size: 11px;
    font-weight: 700;
  }
  .sub-语文 { background: #ffedd5; color: var(--tag-chinese); }
  .sub-数学 { background: #dbeafe; color: var(--tag-math); }
  .sub-英语 { background: #ede9fe; color: var(--tag-english); }
  .sub-科学 { background: #ccfbf1; color: var(--tag-sci); }
  .sub-通用 { background: #f1f5f9; color: var(--muted); }

  /* Interactive Task Items */
  .task-item {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px 14px;
    background: #ffffff;
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    margin-bottom: 8px;
    transition: var(--transition);
  }
  .task-item:hover {
    border-color: #cbd5e1;
    box-shadow: 0 2px 6px rgba(0,0,0,0.02);
  }
  .task-label {
    display: flex;
    align-items: center;
    gap: 10px;
    cursor: pointer;
    flex: 1;
  }
  .task-text {
    font-size: 14px;
    font-weight: 500;
    color: var(--text);
  }
  .task-text.done {
    text-decoration: line-through;
    color: var(--muted-light);
  }

  /* Segmented Pill Radios */
  .pill-group {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    margin-bottom: 12px;
  }
  .pill-btn {
    padding: 6px 14px;
    border-radius: 20px;
    font-size: 12px;
    font-weight: 600;
    border: 1px solid var(--border);
    background: #ffffff;
    color: var(--text-secondary);
    cursor: pointer;
    transition: var(--transition);
  }
  .pill-btn:hover { background: #f8fafc; color: var(--text); }
  .pill-btn.active {
    background: var(--primary);
    color: #ffffff;
    border-color: var(--primary);
    box-shadow: 0 2px 6px rgba(79, 70, 229, 0.2);
  }

  /* Flashcard Vocab Item */
  .vocab-card {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px 16px;
    background: #ffffff;
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    margin-bottom: 8px;
    transition: var(--transition);
  }
  .vocab-card:hover {
    border-color: var(--primary-border);
    box-shadow: 0 2px 8px rgba(79, 70, 229, 0.06);
  }
  .vocab-word-title {
    font-size: 15px;
    font-weight: 700;
    color: var(--primary);
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .vocab-meaning {
    font-size: 13px;
    font-weight: 500;
    color: var(--text);
    margin-left: 4px;
  }
  .vocab-example {
    font-size: 12px;
    color: var(--muted);
    margin-top: 4px;
    font-style: italic;
  }

  /* Dictation Chip */
  .dict-word-chip {
    padding: 6px 12px;
    background: #ffffff;
    border: 1px solid var(--border);
    border-radius: 8px;
    font-size: 14px;
    font-weight: 600;
    color: var(--text);
    display: inline-flex;
    align-items: center;
    gap: 6px;
    box-shadow: 0 1px 2px rgba(0,0,0,0.02);
  }

  /* Grid Cards (Poems, Art, etc.) */
  .grid-card {
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    padding: 14px;
    cursor: pointer;
    transition: var(--transition);
    background: #ffffff;
  }
  .grid-card:hover {
    border-color: var(--primary-border);
    transform: translateY(-2px);
    box-shadow: var(--shadow-md);
  }
  .grid-card.active {
    border-color: var(--primary);
    background: var(--primary-light);
  }
  .grid-card-title { font-weight: 700; font-size: 14px; margin-bottom: 4px; color: var(--text); }
  .grid-card-desc { font-size: 12px; color: var(--muted); line-height: 1.4; }

  /* Modern Segmented Radio Cards */
  .radio-card-group {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 10px;
    margin-bottom: 14px;
  }
  .radio-card {
    border: 1.5px solid var(--border);
    border-radius: var(--radius-md);
    padding: 12px 14px;
    cursor: pointer;
    background: #ffffff;
    transition: var(--transition);
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .radio-card:hover {
    border-color: #cbd5e1;
    background: #fafafc;
  }
  .radio-card.active {
    border-color: var(--primary);
    background: var(--primary-light);
  }
  .radio-card input { margin: 0; }
  .radio-card-text { font-size: 13px; font-weight: 600; color: var(--text); }
  .radio-card-sub { font-size: 11px; color: var(--muted); }

  /* Modals */
  .modal-overlay {
    position: fixed;
    top: 0; left: 0; right: 0; bottom: 0;
    background: rgba(15, 23, 42, 0.6);
    backdrop-filter: blur(4px);
    z-index: 100;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 20px;
    animation: fadeIn 0.15s ease-out;
  }
  .modal-card {
    background: #ffffff;
    border-radius: var(--radius-lg);
    width: 100%;
    max-width: 440px;
    padding: 24px;
    box-shadow: var(--shadow-lg);
    border: 1px solid var(--border);
  }
  .modal-title {
    font-size: 17px;
    font-weight: 700;
    color: var(--text);
    margin-bottom: 16px;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  /* Toast Notification */
  .toast {
    position: fixed;
    bottom: 24px;
    right: 24px;
    background: #0f172a;
    color: #ffffff;
    padding: 12px 20px;
    border-radius: var(--radius-md);
    font-size: 13px;
    font-weight: 600;
    opacity: 0;
    transform: translateY(12px);
    transition: var(--transition);
    pointer-events: none;
    z-index: 9999;
    box-shadow: var(--shadow-lg);
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .toast.show {
    opacity: 1;
    transform: translateY(0);
  }

  /* Responsive Rules for Mobile (< 960px) */
  @media (max-width: 959px) {
    .app-sidebar { display: none; }
    .mobile-nav-panel { display: block; }
    .content-body { padding: 14px 12px 80px 12px; }
    .top-navbar { padding: 10px 14px; }
    .top-page-meta p { display: none; }
    .radio-card-group { grid-template-columns: 1fr; }
    .toast { left: 16px; right: 16px; bottom: 16px; text-align: center; justify-content: center; }
  }

  /* iOS Style Toggle Switch */
  .switch {
    position: relative;
    display: inline-block;
    width: 44px;
    height: 24px;
    flex-shrink: 0;
  }
  .switch input {
    opacity: 0;
    width: 0;
    height: 0;
  }
  .slider {
    position: absolute;
    cursor: pointer;
    top: 0; left: 0; right: 0; bottom: 0;
    background-color: #cbd5e1;
    transition: .25s ease;
    border-radius: 24px;
  }
  .slider:before {
    position: absolute;
    content: "";
    height: 18px;
    width: 18px;
    left: 3px;
    bottom: 3px;
    background-color: white;
    transition: .25s ease;
    border-radius: 50%;
    box-shadow: 0 1px 3px rgba(0,0,0,0.25);
  }
  input:checked + .slider {
    background-color: #10b981;
  }
  input:checked + .slider:before {
    transform: translateX(20px);
  }
  .feature-toggle-card {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 14px 16px;
    background: #ffffff;
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    transition: all 0.2s ease;
  }
  .feature-toggle-card:hover {
    border-color: #94a3b8;
    box-shadow: 0 2px 8px rgba(0,0,0,0.04);
  }
  .feature-toggle-card.disabled {
    opacity: 0.65;
    background: #f8fafc;
  }
  .feature-header-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    background: #f8fafc;
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 10px 16px;
    margin-bottom: 14px;
  }
  .custom-item-card {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 14px;
    background: #ffffff;
    border: 1px solid var(--border);
    border-radius: 8px;
    margin-bottom: 8px;
    transition: all 0.15s ease;
  }
  .custom-item-card:hover {
    border-color: #94a3b8;
    background: #fafafa;
  }


  /* 通用录入工具 */
  .ocr-float {position:fixed;right:20px;bottom:20px;z-index:1500;box-shadow:0 8px 24px #17255425;}
  .ocr-panel {position:fixed;right:20px;bottom:78px;z-index:1501;width:min(390px,calc(100vw - 40px));background:#fff;border:1px solid #dbe4f0;border-radius:16px;padding:18px;box-shadow:0 16px 50px #0f172a30;}
  .ocr-panel textarea {width:100%;min-height:140px;box-sizing:border-box;margin:10px 0;padding:10px;border:1px solid #cbd5e1;border-radius:8px;}
  .ocr-panel p {font-size:13px;line-height:1.5;color:#64748b;}
  /* 家长端统一阅读和操作层级 */
  .content-body {max-width:1440px;margin:0 auto;}
  .card {border-radius:16px;box-shadow:0 3px 14px rgba(15,23,42,.045);}
  .card-title {letter-spacing:0;}
  .tab-btn, .mobile-subtab-btn {min-height:42px;}
  .tab-btn:focus-visible, button:focus-visible, input:focus-visible, textarea:focus-visible, select:focus-visible {outline:3px solid #93c5fd;outline-offset:2px;}
  .pill-btn {min-height:40px;}
  @media (max-width:640px) {.ocr-float {right:12px;bottom:12px;} .ocr-panel {right:12px;bottom:68px;width:calc(100vw - 24px);}}
</style>
</head>
<body>

<div class="app-shell">
  <!-- Desktop Left Sidebar (>= 960px) -->
  <aside class="app-sidebar">
    <div class="sidebar-header">
      <div class="brand-box">
        <div class="brand-logo">🦞</div>
        <div class="brand-meta">
          <h1>PocketClaw 伴学</h1>
          <p>Note 4 墨水屏家长控制台</p>
        </div>
      </div>
    </div>

    <!-- Note 4 Hardware Island Widget -->
    <div class="hardware-card">
      <div class="hw-topbar">
        <span>ZECTRIX NOTE 4</span>
        <span id="badgeOnline" class="badge badge-offline">⚪ 待命中</span>
      </div>
      <div class="hw-live-status">
        <span class="status-dot online"></span>
        <span id="badgeBattery" style="font-weight:600; color:#334155;">🔋 已接电</span>
      </div>
      <div class="hw-screen-preview">
        <span style="color:#64748b; font-size:10px;">当前墨水屏</span>
        <span id="badgeMode" style="color:#1d4ed8; font-weight:700;">📺 待同步</span>
      </div>
      <div style="font-size:10px; font-weight:700; color:#64748b; margin-bottom:6px; letter-spacing:0.5px;">⚡ 一键切屏远程控制：</div>
      <div class="hw-quick-grid">
        <button type="button" class="hw-btn" onclick="switchDeviceMode('task')">📋 作业屏</button>
        <button type="button" class="hw-btn" onclick="switchTab('focus')" style="color:#c2410c; background:#fff7ed; border-color:#ffedd5;">🔥 番茄钟</button>
        <button type="button" class="hw-btn" onclick="pushScreensaverMode('art')" style="color:#6d28d9; background:#f5f3ff; border-color:#ede9fe;">🎨 16灰阶名画</button>
        <button type="button" class="hw-btn" onclick="switchDeviceMode('timetable')">📅 课表屏</button>
        <button type="button" class="hw-btn" onclick="switchDeviceMode('habit')">🌟 打卡屏</button>
        <button type="button" class="hw-btn" onclick="switchDeviceMode('idiom')">📜 成语屏</button>
        <button type="button" class="hw-btn" onclick="switchDeviceMode('menu')" style="grid-column: span 2;">🧭 功能菜单</button>
      </div>
    </div>

    
    <!-- Grouped Sidebar Navigation -->
    <nav class="nav-groups">
      <!-- Group 1: 今日伴学 -->
      <div>
        <div class="nav-group-title">🌟 今日伴学</div>
        <div class="nav-group-items">
          <button class="tab-btn active" data-tab="tasks" onclick="switchTab('tasks')">
            <span class="tab-btn-title">📋 今日作业</span>
            <span class="tab-badge" id="sideBadgeTasks">0/0</span>
          </button>
          <button class="tab-btn" data-tab="focus" onclick="switchTab('focus')">
            <span class="tab-btn-title">🔥 番茄专注</span>
            <span class="tab-badge" id="sideBadgeFocus" style="color:#ea580c;">25m</span>
          </button>
          <button class="tab-btn" data-tab="stars" onclick="switchTab('stars')">
            <span class="tab-btn-title">⭐ 星星存折</span>
            <span class="tab-badge" id="sideBadgeStars">★ 0</span>
          </button>
          <button class="tab-btn" data-tab="habits" onclick="switchTab('habits')">
            <span class="tab-btn-title">🌟 习惯打卡</span>
            <span class="tab-badge" id="sideBadgeHabits">0/0</span>
          </button>
          <button class="tab-btn" data-tab="message" onclick="switchTab('message')">
            <span class="tab-btn-title">💌 亲子留言</span>
          </button>
        </div>
      </div>

      <!-- Group 2: 学科进阶 -->
      <div>
        <div class="nav-group-title">📚 学科进阶</div>
        <div class="nav-group-items">
          <button class="tab-btn" data-tab="dictation" onclick="switchTab('dictation')">
            <span class="tab-btn-title">✍️ 生字听写</span>
          </button>
          <button class="tab-btn" data-tab="poem" onclick="switchTab('poem');loadCustomPoems();">
            <span class="tab-btn-title">📜 课内古诗</span>
          </button>
          <button class="tab-btn" data-tab="arith" onclick="switchTab('arith');loadCustomArith();">
            <span class="tab-btn-title">🔢 口算闯关</span>
          </button>
          <button class="tab-btn" data-tab="vocab" onclick="switchTab('vocab')">
            <span class="tab-btn-title">🔤 英语生词</span>
            <span class="tab-badge" id="sideBadgeVocab">39词</span>
          </button>
          <button class="tab-btn" data-tab="reading" onclick="switchTab('reading')">
            <span class="tab-btn-title">📖 英语短文</span>
          </button>
          <button class="tab-btn" data-tab="mistakes" onclick="switchTab('mistakes')">
            <span class="tab-btn-title">📕 错题专练</span>
          </button>
          <button class="tab-btn" data-tab="idiom" onclick="switchTab('idiom');loadCustomIdioms();">
            <span class="tab-btn-title">📜 每日成语</span>
          </button>
          <button class="tab-btn" data-tab="thinking" onclick="switchTab('thinking');loadThinkingModules();">
            <span class="tab-btn-title">💡 拓展素养题库</span>
          </button>
          <button class="tab-btn" data-tab="timetable" onclick="switchTab('timetable')">
            <span class="tab-btn-title">📅 桌面课表</span>
          </button>
        </div>
      </div>

      <!-- Group 3: 视野长廊 -->
      <div>
        <div class="nav-group-title">🎨 视野长廊</div>
        <div class="nav-group-items">
          <button class="tab-btn" data-tab="news" onclick="switchTab('news');loadNews();">
            <span class="tab-btn-title">📰 小学新鲜事</span>
          </button>
          <button class="tab-btn" data-tab="screensaver" onclick="switchTab('screensaver');loadScreensaver();">
            <span class="tab-btn-title">🎨 名画与屏保</span>
          </button>
        </div>
      </div>

      <!-- Group 4: 智能管理 -->
      <div>
        <div class="nav-group-title">⚙️ 智能管理</div>
        <div class="nav-group-items">
          <button class="tab-btn" data-tab="features" onclick="switchTab('features');loadFeatures();">
            <span class="tab-btn-title">🛡️ 功能权限与开关</span>
            <span class="tab-badge" id="sideBadgeFeatures" style="background:#dcfce7; color:#15803d; font-weight:700;">19/19</span>
          </button>
          <button class="tab-btn" data-tab="settings" onclick="switchTab('settings');loadSettings();">
            <span class="tab-btn-title">🤖 学伴人设与AI</span>
          </button>
          <button class="tab-btn" data-tab="menu" onclick="switchTab('menu');loadMenuOrder();">
            <span class="tab-btn-title">📱 墨水屏菜单定制</span>
          </button>
          <button class="tab-btn" data-tab="report" onclick="switchTab('report')">
            <span class="tab-btn-title">📊 学情报表</span>
          </button>
        </div>
      </div>
    </nav>

  </aside>

  <!-- Main Content Area -->
  <main class="app-main">
    <!-- Top Nav Header -->
    <header class="top-navbar">
      <div class="top-nav-left">
        <div class="top-page-meta">
          <h2><span id="topPageCat" style="color:var(--muted); font-size:13px; font-weight:500;">今日伴学 / </span><span id="topPageTitle">📋 今日作业</span></h2>
          <p id="topPageSub">微信班级群作业智能解析、一键排版并秒级推送到 Note 4 墨水屏</p>
        </div>
      </div>
      <div class="top-nav-badges">
        <button class="btn btn-secondary btn-sm" onclick="loadOverview();showToast('已同步最新状态');">🔄 刷新</button>
        <button class="btn btn-primary btn-sm" onclick="switchDeviceMode('task')">📱 推送作业屏</button>
      </div>
    </header>

    
    <!-- Mobile Category Switcher & Sub-tabs (< 960px) -->
    <div class="mobile-nav-panel">
      <!-- Top 4 Primary Categories -->
      <div class="mobile-categories">
        <button type="button" class="cat-pill active" data-cat="daily" onclick="onMobileSelectCategory('daily')">🌟 今日伴学</button>
        <button type="button" class="cat-pill" data-cat="study" onclick="onMobileSelectCategory('study')">📚 学科进阶</button>
        <button type="button" class="cat-pill" data-cat="vision" onclick="onMobileSelectCategory('vision')">🎨 视野长廊</button>
        <button type="button" class="cat-pill" data-cat="system" onclick="onMobileSelectCategory('system')">⚙️ 智能管理</button>
      </div>
      
      <!-- Subtabs for Category 1: Daily -->
      <div class="mobile-subtabs-row mobile-subtabs-group" data-cat="daily" style="display:flex;">
        <button class="mobile-subtab-btn tab-btn active" data-tab="tasks" onclick="switchTab('tasks')">📋 作业</button>
        <button class="mobile-subtab-btn tab-btn" data-tab="focus" onclick="switchTab('focus')">🔥 番茄钟</button>
        <button class="mobile-subtab-btn tab-btn" data-tab="stars" onclick="switchTab('stars')">⭐ 星星</button>
        <button class="mobile-subtab-btn tab-btn" data-tab="habits" onclick="switchTab('habits')">🌟 打卡</button>
        <button class="mobile-subtab-btn tab-btn" data-tab="message" onclick="switchTab('message')">💌 留言</button>
      </div>

      <!-- Subtabs for Category 2: Study -->
      <div class="mobile-subtabs-row mobile-subtabs-group" data-cat="study" style="display:none;">
        <button class="mobile-subtab-btn tab-btn" data-tab="dictation" onclick="switchTab('dictation')">✍️ 听写</button>
        <button class="mobile-subtab-btn tab-btn" data-tab="poem" onclick="switchTab('poem');loadCustomPoems();">📜 古诗</button>
        <button class="mobile-subtab-btn tab-btn" data-tab="arith" onclick="switchTab('arith');loadCustomArith();">🔢 口算</button>
        <button class="mobile-subtab-btn tab-btn" data-tab="vocab" onclick="switchTab('vocab')">🔤 单词</button>
        <button class="mobile-subtab-btn tab-btn" data-tab="reading" onclick="switchTab('reading')">📖 阅读</button>
        <button class="mobile-subtab-btn tab-btn" data-tab="mistakes" onclick="switchTab('mistakes')">📕 错题</button>
        <button class="mobile-subtab-btn tab-btn" data-tab="idiom" onclick="switchTab('idiom');loadCustomIdioms();">📜 成语</button>
        <button class="mobile-subtab-btn tab-btn" data-tab="thinking" onclick="switchTab('thinking');loadThinkingModules();">💡 拓展</button>
        <button class="mobile-subtab-btn tab-btn" data-tab="timetable" onclick="switchTab('timetable')">📅 课表</button>
      </div>

      <!-- Subtabs for Category 3: Vision -->
      <div class="mobile-subtabs-row mobile-subtabs-group" data-cat="vision" style="display:none;">
        <button class="mobile-subtab-btn tab-btn" data-tab="news" onclick="switchTab('news');loadNews();">📰 新鲜事</button>
        <button class="mobile-subtab-btn tab-btn" data-tab="screensaver" onclick="switchTab('screensaver');loadScreensaver();">🎨 屏保名画</button>
      </div>

      <!-- Subtabs for Category 4: System -->
      <div class="mobile-subtabs-row mobile-subtabs-group" data-cat="system" style="display:none;">
        <button class="mobile-subtab-btn tab-btn" data-tab="features" onclick="switchTab('features');loadFeatures();">🛡️ 功能权限</button>
        <button class="mobile-subtab-btn tab-btn" data-tab="settings" onclick="switchTab('settings');loadSettings();">🤖 学伴AI</button>
        <button class="mobile-subtab-btn tab-btn" data-tab="menu" onclick="switchTab('menu');loadMenuOrder();">📱 菜单定制</button>
        <button class="mobile-subtab-btn tab-btn" data-tab="report" onclick="switchTab('report')">📊 学情报表</button>
      </div>
    </div>


    <!-- Content Body -->
    <div class="content-body">

  <!-- Tab 1: 作业清单 -->
  <div id="tab-tasks" class="tab-content active">
    <div class="feature-header-bar">
      <div style="display:flex; align-items:center; gap:8px;">
        <span style="font-weight:700; color:var(--text); font-size:14px;">📋 作业清单功能</span>
        <span id="tabBadge-task" class="badge" style="background:#dcfce7; color:#15803d;">🟢 本功能已启用</span>
      </div>
      <div style="display:flex; align-items:center; gap:8px;">
        <span style="font-size:12px; color:var(--muted);">允许孩子在 Note 4 上使用：</span>
        <label class="switch">
          <input type="checkbox" id="tabToggle-task" checked onchange="toggleFeatureDirect('task', this.checked)">
          <span class="slider"></span>
        </label>
      </div>
    </div>

    <div class="tab-grid-2col">
      <!-- Left Column: Input -->
      <div class="sticky-col">
        <div class="card" style="background: linear-gradient(135deg, #f8fafc 0%, #f1f5f9 100%); border-color: #cbd5e1;">
          <div class="card-header">
            <div>
              <div class="card-title">📱 微信班级群作业一键粘贴</div>
              <div class="card-subtitle">智能识别学科、序号与作业内容并推屏</div>
            </div>
          </div>
          <div class="form-group">
            <textarea id="rawTasksInput" style="height:140px;" placeholder="直接复制老师在微信群发的作业通知，例如：
1. 语文：抄写生字两遍
2. 数学：口算练习20题
3. 英语：大声朗读第一单元课文"></textarea>
          </div>
          <div class="btn-row">
            <button class="btn btn-primary" onclick="submitQuickInput(false)">🚀 一键录入并推屏</button>
            <button class="btn btn-secondary" onclick="submitQuickInput(true)">覆盖全部任务</button>
          </div>
        </div>

        <div class="card">
          <div class="card-title" style="margin-bottom:10px;">➕ 添加单项任务</div>
          <div style="display:flex; gap:8px;">
            <input type="text" id="singleTaskInput" placeholder="输入单项作业，如：背诵古诗《咏柳》" onkeydown="if(event.key==='Enter')addSingleTask()">
            <button class="btn btn-primary" onclick="addSingleTask()" style="flex-shrink:0;">+ 添加</button>
          </div>
        </div>
      </div>

      <!-- Right Column: Today's Tasks List -->
      <div>
        <div class="card">
          <div class="card-header">
            <div>
              <div class="card-title">📋 今日作业清单 <span class="badge" style="background:#dbeafe; color:#1d4ed8; margin-left:6px;">已完成 <span id="taskStats">0/0</span></span></div>
              <div class="card-subtitle">点击任务名称可直接在 Note 4 墨水屏划勾打卡</div>
            </div>
            <button class="btn btn-secondary btn-sm" onclick="clearDoneTasks()">清除已完成</button>
          </div>
          <div id="taskList"></div>
        </div>
      </div>
    </div>
  </div>

  
  <!-- Tab 2: 番茄专注远程控制 -->
  <div id="tab-focus" class="tab-content">
    <div class="feature-header-bar">
      <div style="display:flex; align-items:center; gap:8px;">
        <span style="font-weight:700; color:var(--text); font-size:14px;">🔥 番茄专注功能</span>
        <span id="tabBadge-focus" class="badge" style="background:#dcfce7; color:#15803d;">🟢 本功能已启用</span>
      </div>
      <div style="display:flex; align-items:center; gap:8px;">
        <span style="font-size:12px; color:var(--muted);">允许孩子在 Note 4 上使用：</span>
        <label class="switch">
          <input type="checkbox" id="tabToggle-focus" checked onchange="toggleFeatureDirect('focus', this.checked)">
          <span class="slider"></span>
        </label>
      </div>
    </div>

    <div class="card" style="background:linear-gradient(135deg, #fff7ed 0%, #ffedd5 100%); border-color:#fed7aa;">
      <div class="card-header" style="border-color:rgba(254,215,170,0.6);">
        <div>
          <div class="card-title" style="color:#9a3412;">🔥 番茄专注远程控制 · Note 4 墨水屏同频</div>
          <div class="card-subtitle" style="color:#c2410c;">启动后 Note 4 将全屏进入沉浸专注倒计时界面</div>
        </div>
        <div style="font-size:12px; color:#c2410c; background:rgba(255,255,255,0.7); padding:4px 10px; border-radius:12px; font-weight:600;">
          今日累计专注：<span id="focusTodayMinText" style="font-weight:700; color:#ea580c;">0</span> 分钟
        </div>
      </div>

      <div style="text-align:center; padding:16px 0;">
        <div id="focusStatusLabel" style="font-size:14px; font-weight:700; color:#c2410c; margin-bottom:10px;">🔥 花火 / 蜡烛番茄钟 · 100px 冬青黑体大字</div>
        <div id="focusTimerDisplay" style="font-size:64px; font-weight:800; font-family:monospace; color:#9a3412; letter-spacing:2px; margin-bottom:12px;">25:00</div>
        <div style="background:rgba(255,255,255,0.8); height:10px; border-radius:6px; overflow:hidden; max-width:440px; margin:0 auto 18px auto; box-shadow:inset 0 1px 2px rgba(0,0,0,0.1);">
          <div id="focusProgressBar" style="width:0%; height:100%; background:linear-gradient(90deg, #ea580c, #f97316); transition:width 0.5s;"></div>
        </div>
      </div>

      <div style="background:rgba(255,255,255,0.6); border-radius:12px; padding:16px; margin-bottom:16px;">
        <div style="font-size:12px; font-weight:700; color:#9a3412; margin-bottom:8px;">选择专注时长：</div>
        <div class="pill-group" style="margin-bottom:14px;">
          <button type="button" class="pill-btn" onclick="selectFocusMinutes(15, this)">15 分钟 (速练)</button>
          <button type="button" class="pill-btn active" onclick="selectFocusMinutes(25, this)">25 分钟 (标准)</button>
          <button type="button" class="pill-btn" onclick="selectFocusMinutes(35, this)">35 分钟 (深度)</button>
          <button type="button" class="pill-btn" onclick="selectFocusMinutes(45, this)">45 分钟 (大节)</button>
        </div>

        <div style="font-size:12px; font-weight:700; color:#9a3412; margin-bottom:8px;">选择墨水屏专注动画风格：</div>
        <div class="pill-group">
          <button type="button" class="pill-btn active" onclick="selectFocusStyle('spark', this)">🔥 火花引线 (经典动感)</button>
          <button type="button" class="pill-btn" onclick="selectFocusStyle('candle', this)">🕯️ 静谧蜡烛 (柔和护眼)</button>
        </div>
      </div>

      <div class="btn-row" style="justify-content:center;">
        <button id="btnFocusStart" class="btn btn-primary" style="background:#ea580c; border-color:#ea580c; padding:12px 28px; font-size:15px;" onclick="controlFocus('start')">🚀 启动 Note 4 专注</button>
        <div id="focusActiveControls" style="display:none; gap:8px;">
          <button id="btnFocusPauseResume" class="btn btn-secondary" onclick="controlFocus('pause_resume')">⏸ 暂停专注</button>
          <button id="btnFocusStop" class="btn btn-secondary" style="color:#ef4444;" onclick="controlFocus('stop')">⏹ 提前结束</button>
        </div>
      </div>
    </div>
  </div>

  
  <!-- Tab 3: 习惯打卡 -->
  <div id="tab-habits" class="tab-content">
    <div class="feature-header-bar">
      <div style="display:flex; align-items:center; gap:8px;">
        <span style="font-weight:700; color:var(--text); font-size:14px;">🌟 习惯打卡功能</span>
        <span id="tabBadge-habit" class="badge" style="background:#dcfce7; color:#15803d;">🟢 本功能已启用</span>
      </div>
      <div style="display:flex; align-items:center; gap:8px;">
        <span style="font-size:12px; color:var(--muted);">允许孩子在 Note 4 上使用：</span>
        <label class="switch">
          <input type="checkbox" id="tabToggle-habit" checked onchange="toggleFeatureDirect('habit', this.checked)">
          <span class="slider"></span>
        </label>
      </div>
    </div>

    <div class="tab-grid-2col">
      <div class="sticky-col">
        <div class="card">
          <div class="card-header">
            <div>
              <div class="card-title">🌟 培养良好学习习惯</div>
              <div class="card-subtitle">每完成一项打卡，为孩子奖励星星</div>
            </div>
            <span id="habitStarsBadge" class="badge" style="background:#fef3c7; color:#b45309;">⭐ 0 颗星</span>
          </div>
          <div class="form-group">
            <label class="form-label">习惯名称</label>
            <input type="text" id="newHabitInput" placeholder="如：睡前阅读20分钟">
          </div>
          <div class="form-group">
            <label class="form-label">图标 Emoji</label>
            <input type="text" id="newHabitIcon" placeholder="如 📖 / 🏃 / 🛏️" value="📖">
          </div>
          <button class="btn btn-primary btn-block" onclick="addHabit()">+ 添加打卡习惯</button>
        </div>
      </div>
      <div>
        <div class="card">
          <div class="card-header">
            <div>
              <div class="card-title">今日打卡进度 (<span id="habitStats">0/0</span>)</div>
              <div class="card-subtitle">可直接在手机上勾选，墨水屏同步响应</div>
            </div>
          </div>
          <div id="habitList"></div>
        </div>
      </div>
    </div>
  </div>

  <!-- Tab 4: 星星心愿存折 -->
  <div id="tab-stars" class="tab-content">
    <div class="tab-grid-2col">
      <div class="sticky-col">
        <div class="card" style="background: linear-gradient(135deg, #fffbeb 0%, #fef3c7 100%); border-color: #fde68a;">
          <div class="card-header" style="border-color:rgba(253,230,138,0.6);">
            <div class="card-title" style="color:#92400e;">⭐ 星星积分存折</div>
            <button class="btn btn-primary btn-sm" style="background:#d97706; border-color:#d97706;" onclick="openAddStarsModal()">+ 奖励星星</button>
          </div>
          <div style="text-align:center; padding:16px 0;">
            <div style="font-size:12px; color:#b45309; font-weight:600;">当前可用星星余额</div>
            <div id="starBalanceDisplay" style="font-size:52px; font-weight:800; color:#b45309; line-height:1.2;">0</div>
            <div style="font-size:11px; color:#b45309; margin-top:4px;">
              累计奖励 <span id="starTotalEarned" style="font-weight:700;">0</span> 颗 · 累计兑换 <span id="starTotalSpent" style="font-weight:700;">0</span> 颗
            </div>
          </div>
        </div>

        <div class="card">
          <div class="card-title" style="margin-bottom:12px;">📜 近期星星奖励流水</div>
          <div id="starHistoryList" style="max-height:280px; overflow-y:auto;"></div>
        </div>
      </div>

      <div>
        <div class="card">
          <div class="card-header">
            <div>
              <div class="card-title">🎁 心愿兑换梦想单</div>
              <div class="card-subtitle">孩子可用积攒的星星换取心仪奖励</div>
            </div>
            <button class="btn btn-secondary btn-sm" onclick="openAddWishModal()">+ 添加心愿</button>
          </div>
          <div id="wishCardsGrid" style="display:grid; grid-template-columns:repeat(auto-fill, minmax(200px, 1fr)); gap:12px;"></div>
        </div>
      </div>
    </div>
  </div>

  
  <!-- Tab 5: 桌面课表 -->
  <div id="tab-timetable" class="tab-content">
    <div class="feature-header-bar">
      <div style="display:flex; align-items:center; gap:8px;">
        <span style="font-weight:700; color:var(--text); font-size:14px;">📅 桌面课表功能</span>
        <span id="tabBadge-timetable" class="badge" style="background:#dcfce7; color:#15803d;">🟢 本功能已启用</span>
      </div>
      <div style="display:flex; align-items:center; gap:8px;">
        <span style="font-size:12px; color:var(--muted);">允许孩子在 Note 4 上使用：</span>
        <label class="switch">
          <input type="checkbox" id="tabToggle-timetable" checked onchange="toggleFeatureDirect('timetable', this.checked)">
          <span class="slider"></span>
        </label>
      </div>
    </div>

    <div class="card">
      <div class="card-header">
        <div>
          <div class="card-title">📅 桌面周课表</div>
          <div class="card-subtitle">展示当前保存或系统预设的课表，请核对实际课程；可直接修改整周后保存。</div>
        </div>
        <button id="saveTimetableWeekButton" class="btn btn-primary btn-sm" onclick="saveTimetableWeek()" disabled>💾 保存整周课表</button>
      </div>
      <label>拍照识别课表 <input type="file" id="weekPhotoInput" accept="image/*" capture="environment"></label><button type="button" class="btn btn-secondary btn-sm" onclick="recognizeTimetablePhoto()">识别并填入</button>
      <div style="overflow-x:auto;">
        <table style="width:100%;min-width:740px;border-collapse:collapse;table-layout:fixed;" aria-label="周一至周五课程表">
          <thead><tr><th scope="col" style="width:70px;padding:10px;">节次</th><th scope="col">周一</th><th scope="col">周二</th><th scope="col">周三</th><th scope="col">周四</th><th scope="col">周五</th></tr></thead>
          <tbody id="timetableWeekBody"></tbody>
        </table>
      </div>
      <p style="color:var(--muted);font-size:12px;margin-top:12px;">保存后，正在查看课表的在线设备会刷新画面。</p>
    </div>
  </div>

  
  <!-- Tab 6: 错题专练 -->
  <div id="tab-mistakes" class="tab-content">
    <div class="feature-header-bar">
      <div style="display:flex; align-items:center; gap:8px;">
        <span style="font-weight:700; color:var(--text); font-size:14px;">📕 错题专练功能</span>
        <span id="tabBadge-mistakes" class="badge" style="background:#dcfce7; color:#15803d;">🟢 本功能已启用</span>
      </div>
      <div style="display:flex; align-items:center; gap:12px;">
        <button class="btn btn-primary btn-sm" onclick="openAddMistakeModal()">➕ 手动录入新错题</button>
        <label class="switch">
          <input type="checkbox" id="tabToggle-mistakes" checked onchange="toggleFeatureDirect('mistakes', this.checked)">
          <span class="slider"></span>
        </label>
      </div>
    </div>

    <div class="tab-grid-2col">
      <div class="sticky-col">
        <div class="card">
          <div class="card-header">
            <div class="card-title">📕 录入新错题</div>
          </div>
          <div class="form-group">
            <label class="form-label">题目内容</label>
            <input type="text" id="newMistakeQ" placeholder="输入错题原题">
          </div>
          <div class="form-group">
            <label class="form-label">学科分类</label>
            <select id="newMistakeCat">
              <option value="数学">数学</option>
              <option value="语文">语文</option>
              <option value="英语">英语</option>
              <option value="科学">科学</option>
            </select>
          </div>
          <div class="form-group">
            <label class="form-label">孩子错误答案</label>
            <input type="text" id="newMistakeWrong" placeholder="记录孩子的错误思路">
          </div>
          <div class="form-group">
            <label class="form-label">正确解析</label>
            <input type="text" id="newMistakeExp" placeholder="解题思路与要点提示">
          </div>
          <button class="btn btn-primary btn-block" onclick="addManualMistake()">+ 录入错题本</button>
        </div>
      </div>

      <div>
        <div class="card">
          <div class="card-header">
            <div>
              <div class="card-title">待消灭错题列表 (<span id="mistakeCount">0</span> 题)</div>
              <div class="card-subtitle">孩子在 Note 4 上答对后将自动消灭</div>
            </div>
            <button class="btn btn-secondary btn-sm" onclick="clearAllMistakes()">清空全部</button>
          </div>
          <div id="mistakeList"></div>
        </div>
      </div>
    </div>
  </div>

  
  <!-- Tab 7: 每日成语 -->
  <div id="tab-idiom" class="tab-content">
    <div class="feature-header-bar">
      <div style="display:flex; align-items:center; gap:8px;">
        <span style="font-weight:700; color:var(--text); font-size:14px;">📜 每日成语功能</span>
        <span id="tabBadge-idiom" class="badge" style="background:#dcfce7; color:#15803d;">🟢 本功能已启用</span>
      </div>
      <div style="display:flex; align-items:center; gap:8px;">
        <span style="font-size:12px; color:var(--muted);">允许孩子在 Note 4 上使用：</span>
        <label class="switch">
          <input type="checkbox" id="tabToggle-idiom" checked onchange="toggleFeatureDirect('idiom', this.checked)">
          <span class="slider"></span>
        </label>
      </div>
    </div>

    <!-- 家长自定义成语库 -->
    <div class="card" style="margin-bottom:16px;">
      <div class="card-header">
        <div>
          <div class="card-title">📖 家长自定义成语备忘库</div>
          <div class="card-subtitle">录入孩子近期的重点成语积累，可修改、删除并一键设为今日墨水屏成语</div>
        </div>
        <button class="btn btn-primary btn-sm" onclick="openAddIdiomModal()">➕ 添加自定义成语</button>
      </div>
      <div id="customIdiomsList"></div>
    </div>

    <div class="card" style="background:linear-gradient(135deg, #fdf4ff 0%, #fae8ff 100%); border-color:#f5d0fe;">
      <div class="card-header" style="border-color:rgba(245,208,254,0.6);">
        <div>
          <div class="card-title" style="color:#86198f;">📜 今日推荐晨读成语</div>
          <div class="card-subtitle" style="color:#a21caf;">点击立即推屏，在 Note 4 墨水屏上精美呈现</div>
        </div>
        <button class="btn btn-primary btn-sm" style="background:#a21caf; border-color:#a21caf;" onclick="pushIdiomToDevice()">🚀 推送到 Note 4</button>
      </div>

      <div style="padding:12px 0;">
        <div style="display:flex; align-items:baseline; gap:10px;">
          <div id="curIdiomWord" style="font-size:32px; font-weight:800; color:#701a75;"></div>
          <div id="curIdiomPinyin" style="font-size:16px; color:#a21caf; font-weight:600;"></div>
        </div>
        <div id="curIdiomMeaning" style="font-size:14px; font-weight:600; color:#4a044e; margin-top:8px;"></div>
        <div id="curIdiomExample" style="font-size:13px; color:#701a75; margin-top:6px; font-style:italic;"></div>
        <div id="curIdiomSource" style="font-size:11px; color:#a21caf; margin-top:4px;"></div>
      </div>
    </div>

    <div class="card">
      <div class="card-title" style="margin-bottom:12px;">📚 成语精选库 (点击任一成语可立即换题)</div>
      <div id="idiomLibraryList" style="display:grid; grid-template-columns:repeat(auto-fill, minmax(200px, 1fr)); gap:10px;"></div>
    </div>
  </div>

  
  <!-- Tab 8: 生字听写 -->
  <div id="tab-dictation" class="tab-content">
    <div class="feature-header-bar">
      <div style="display:flex; align-items:center; gap:8px;">
        <span style="font-weight:700; color:var(--text); font-size:14px;">✍️ 生字听写功能</span>
        <span id="tabBadge-dictation" class="badge" style="background:#dcfce7; color:#15803d;">🟢 本功能已启用</span>
      </div>
      <div style="display:flex; align-items:center; gap:8px;">
        <span style="font-size:12px; color:var(--muted);">允许孩子在 Note 4 上使用：</span>
        <label class="switch">
          <input type="checkbox" id="tabToggle-dictation" checked onchange="toggleFeatureDirect('dictation', this.checked)">
          <span class="slider"></span>
        </label>
      </div>
    </div>

    <div class="tab-grid-2col">
      <div class="sticky-col">
        <div class="card">
          <div class="card-header">
            <div>
              <div class="card-title">✍️ 当前听写生词单</div>
              <div class="card-subtitle"><span id="curDictTitle">生字词听写</span> (<span id="dictCount">0</span> 词)</div>
            </div>
          </div>
          <div class="form-group">
            <label class="form-label">听写任务标题</label>
            <input type="text" id="dictationTitleInput" placeholder="如：二年级下册 第5课生字">
          </div>
          <div class="form-group">
            <label class="form-label">词汇内容 (空格或回车分隔)</label>
            <textarea id="dictationInput" placeholder="如：温暖 碧绿 吐出 寻找 姑娘"></textarea>
          </div>
          <div class="form-group"><button type="button" class="btn btn-secondary" onclick="togglePresetCatalog()">📚 按年级选择预设内容</button></div>
          <button class="btn btn-primary btn-block" onclick="saveDictation()">💾 保存词单并推送到墨水屏</button>

          <div style="margin-top:20px; padding-top:16px; border-top:1px dashed var(--border);">
            <div class="card-title" style="font-size:13px; margin-bottom:8px;">📷 拍照自动检查核对</div>
            <label class="btn btn-secondary btn-block" style="cursor:pointer;">
              <span>📸 手机拍照 / 上传答题本</span>
              <input type="file" id="dictPhotoInput" accept="image/*" capture="environment" style="display:none;" onchange="handleDictationPhoto(this)">
            </label>
            <div id="dictationPhotoStatus" style="font-size:11px; color:var(--muted); margin-top:6px; text-align:center;"></div>
            <div id="dictationPreview" style="margin-top:8px;"></div>
          </div>
        </div>
      </div>

      <div>
        <div class="card">
          <div class="card-title" style="margin-bottom:12px;">📚 预设与历史词单套题</div>
          <div id="dictationSetsList" style="display:grid; grid-template-columns:repeat(auto-fill, minmax(220px, 1fr)); gap:10px; margin-bottom:20px;"></div>
          
          <div class="card-title" style="margin-bottom:12px;">📊 听写批改与消错记录</div>
          <div id="dictationReviewList"></div>
        </div>
      </div>
    </div>
  </div>

  
  <!-- Tab 9: 课内古诗 -->
  <div id="tab-poem" class="tab-content">
    <div class="feature-header-bar">
      <div style="display:flex; align-items:center; gap:8px;">
        <span style="font-weight:700; color:var(--text); font-size:14px;">📜 课内古诗功能</span>
        <span id="tabBadge-recite" class="badge" style="background:#dcfce7; color:#15803d;">🟢 本功能已启用</span>
      </div>
      <div style="display:flex; align-items:center; gap:8px;">
        <span style="font-size:12px; color:var(--muted);">允许孩子在 Note 4 上使用：</span>
        <label class="switch">
          <input type="checkbox" id="tabToggle-recite" checked onchange="toggleFeatureDirect('recite', this.checked)">
          <span class="slider"></span>
        </label>
      </div>
    </div>

    <!-- 家长自定义古诗 -->
    <div class="card" style="margin-bottom:16px;">
      <div class="card-header">
        <div>
          <div class="card-title">📖 家长自定义古诗库</div>
          <div class="card-subtitle">支持自定义录入课外诗词或特定篇目，可修改、删除并一键推送到 Note 4 背诵</div>
        </div>
        <button class="btn btn-primary btn-sm" onclick="openAddPoemModal()">➕ 添加自定义古诗</button>
      </div>
      <div id="customPoemsList"></div>
    </div>

    <div class="card">
      <div class="card-header">
        <div>
          <div class="card-title">📜 课内必背古诗词</div>
          <div class="card-subtitle">点击任一古诗，Note 4 墨水屏将进入逐句背诵与语音复述模式</div>
        </div>
        <span id="curPoemBadge" class="badge" style="background:#ffedd5; color:#c2410c;">当前背诵：--</span>
      </div>
      <div id="poemGrid" style="display:grid; grid-template-columns:repeat(auto-fill, minmax(240px, 1fr)); gap:12px;"></div>
    </div>
  </div>

  
  <!-- Tab 10: 口算闯关 -->
  <div id="tab-arith" class="tab-content">
    <div class="feature-header-bar">
      <div style="display:flex; align-items:center; gap:8px;">
        <span style="font-weight:700; color:var(--text); font-size:14px;">🔢 口算闯关功能</span>
        <span id="tabBadge-arith" class="badge" style="background:#dcfce7; color:#15803d;">🟢 本功能已启用</span>
      </div>
      <div style="display:flex; align-items:center; gap:8px;">
        <span style="font-size:12px; color:var(--muted);">允许孩子在 Note 4 上使用：</span>
        <label class="switch">
          <input type="checkbox" id="tabToggle-arith" checked onchange="toggleFeatureDirect('arith', this.checked)">
          <span class="slider"></span>
        </label>
      </div>
    </div>

    <!-- 家长自定义靶向口算库 -->
    <div class="card" style="margin-bottom:16px;">
      <div class="card-header">
        <div>
          <div class="card-title">🎯 家长靶向口算题库</div>
          <div class="card-subtitle">录入孩子薄弱的特定混合运算或简便计算题，支持提示语与精细修改</div>
        </div>
        <button class="btn btn-primary btn-sm" onclick="openAddArithModal()">➕ 添加靶向算式</button>
      </div>
      <div id="customArithList"></div>
    </div>

    <div class="card">
      <div class="card-header">
        <div>
          <div class="card-title">🔢 口算每日闯关配置</div>
          <div class="card-subtitle">配置题型与题量后，孩子可在 Note 4 上按键答题打卡</div>
        </div>
        <button class="btn btn-primary btn-sm" onclick="saveArithConfig()">💾 保存配置并同步</button>
      </div>

      <div class="form-group">
        <label class="form-label">选择年级</label>
        <div class="pill-group" id="arithGradeGroup">
          <button type="button" class="pill-btn" data-type="grade" data-val="1" onclick="selectArithPill('grade', 1)">一年级 (20以内)</button>
          <button type="button" class="pill-btn active" data-type="grade" data-val="2" onclick="selectArithPill('grade', 2)">二年级 (百以内加减/乘法)</button>
          <button type="button" class="pill-btn" data-type="grade" data-val="3" onclick="selectArithPill('grade', 3)">三年级 (千以内乘除)</button>
          <button type="button" class="pill-btn" data-type="grade" data-val="4" onclick="selectArithPill('grade', 4)">四年级 (多位数/小数)</button>
        </div>
      </div>

      <div class="form-group">
        <label class="form-label">每日闯关题量</label>
        <div class="pill-group" id="arithCountGroup">
          <button type="button" class="pill-btn" data-type="count" data-val="5" onclick="selectArithPill('count', 5)">5 题 (快速热身)</button>
          <button type="button" class="pill-btn active" data-type="count" data-val="10" onclick="selectArithPill('count', 10)">10 题 (标准锻炼)</button>
          <button type="button" class="pill-btn" data-type="count" data-val="20" onclick="selectArithPill('count', 20)">20 题 (强化提升)</button>
        </div>
      </div>

      <div class="form-group">
        <label class="form-label">运算类型</label>
        <div class="pill-group" id="arithOpGroup">
          <button type="button" class="pill-btn active" data-type="op" data-val="all" onclick="selectArithPill('op', 'all')">综合混合</button>
          <button type="button" class="pill-btn" data-type="op" data-val="add_sub" onclick="selectArithPill('op', 'add_sub')">加减法专练</button>
          <button type="button" class="pill-btn" data-type="op" data-val="mul_div" onclick="selectArithPill('op', 'mul_div')">乘除法专练</button>
        </div>
      </div>
    </div>
  </div>

  
  <!-- Tab 11: 英语生词 -->
  <div id="tab-vocab" class="tab-content">
    <div class="feature-header-bar">
      <div style="display:flex; align-items:center; gap:8px;">
        <span style="font-weight:700; color:var(--text); font-size:14px;">🔤 英语生词本功能</span>
        <span id="tabBadge-vocab" class="badge" style="background:#dcfce7; color:#15803d;">🟢 本功能已启用</span>
      </div>
      <div style="display:flex; align-items:center; gap:8px;">
        <span style="font-size:12px; color:var(--muted);">允许孩子在 Note 4 上使用：</span>
        <label class="switch">
          <input type="checkbox" id="tabToggle-vocab" checked onchange="toggleFeatureDirect('vocab', this.checked)">
          <span class="slider"></span>
        </label>
      </div>
    </div>

    <div class="tab-grid-2col">
      <!-- Left Column: Add Word Form -->
      <div class="sticky-col">
        <div class="card">
          <div class="card-header">
            <div class="card-title">➕ 添加新单词</div>
          </div>
          <div class="form-row" style="margin-bottom:12px;">
            <div>
              <label class="form-label">英文单词</label>
              <input type="text" id="newWord" placeholder="例: apple">
            </div>
            <div>
              <label class="form-label">中文释义</label>
              <input type="text" id="newMeaning" placeholder="例: 苹果">
            </div>
          </div>
          <div class="form-group">
            <label class="form-label">英文例句 (可选)</label>
            <input type="text" id="newExample" placeholder="例: I eat a fresh apple.">
          </div>
          <div class="form-group">
            <label class="form-label">例句中文翻译 (可选)</label>
            <input type="text" id="newExampleCn" placeholder="例: 我吃了一个新鲜苹果。">
          </div>
          <button class="btn btn-primary btn-block" onclick="addVocabWord()">+ 添加到生词本</button>
          <div class="form-group" style="margin-top:16px"><label class="form-label">拍照录入生词</label><input type="file" id="vocabPhoto" accept="image/*" capture="environment"><button type="button" class="btn btn-secondary" onclick="recognizeVocabPhoto()">识别照片</button><p>每行一个词：单词 | 中文释义 | 例句 | 例句翻译。核对后保存。</p><textarea id="vocabPhotoPreview" rows="5" placeholder="识别结果会显示在这里，也可手动输入"></textarea><button type="button" class="btn btn-secondary" onclick="saveVocabPhoto()">保存核对后的词条</button></div>
          <div style="font-size:11px; color:var(--muted); margin-top:10px; line-height:1.4;">
            💡 提示：录入后单词将自动同步到 Note 4 英语生词卡与例句轮播。
          </div>
        </div>
      </div>

      <!-- Right Column: Vocab Library List -->
      <div>
        <div class="card">
          <div class="card-header">
            <div>
              <div class="card-title">📚 生词本词汇列表 <span class="badge" style="background:#eff6ff; color:#1d4ed8; margin-left:6px;"><span id="vocabCount">0</span> 词</span></div>
              <div class="card-subtitle">按字母排序，支持随时温故知新与清理熟词</div>
            </div>
            <input type="text" placeholder="🔍 快速过滤生词..." oninput="filterVocab(this.value)" style="max-width:180px; padding:6px 12px; font-size:12px; border-radius:20px;">
          </div>
          <div id="vocabList"></div>
        </div>
      </div>
    </div>
  </div>

  
  <!-- Tab 12: 英语短文 -->
  <div id="tab-reading" class="tab-content">
    <div class="feature-header-bar">
      <div style="display:flex; align-items:center; gap:8px;">
        <span style="font-weight:700; color:var(--text); font-size:14px;">📖 英语短文阅读功能</span>
        <span id="tabBadge-reading" class="badge" style="background:#dcfce7; color:#15803d;">🟢 本功能已启用</span>
      </div>
      <div style="display:flex; align-items:center; gap:12px;">
        <button class="btn btn-primary btn-sm" onclick="openAddReadingModal()">➕ 添加自定义短文故事</button>
        <label class="switch">
          <input type="checkbox" id="tabToggle-reading" checked onchange="toggleFeatureDirect('reading', this.checked)">
          <span class="slider"></span>
        </label>
      </div>
    </div>

    <div class="card">
      <div class="card-header">
        <div>
          <div class="card-title">📖 英语分级短文阅读</div>
          <div class="card-subtitle">短文精读、重点词汇高亮与朗读打卡</div>
        </div>
        <span id="curReadingBadge" class="badge" style="background:#ede9fe; color:#6d28d9;">当前阅读：--</span>
      </div>

      <div style="margin-bottom:16px;">
        <label class="btn btn-secondary btn-sm" style="cursor:pointer;">
          <span>📸 拍照上传孩子英文朗读与笔记批改</span>
          <input type="file" id="readingPhotoInput" accept="image/*" capture="environment" style="display:none;" onchange="handleReadingPhoto(this)">
        </label>
        <span id="readingPhotoStatus" style="font-size:12px; color:var(--muted); margin-left:8px;"></span>
      </div>

      <div id="readingArticlesList" style="display:grid; grid-template-columns:repeat(auto-fill, minmax(280px, 1fr)); gap:14px;"></div>
    </div>
  </div>

  <!-- Tab 13: 亲子留言 -->
  <div id="tab-message" class="tab-content">
    <div class="card" style="background:linear-gradient(135deg, #fff1f2 0%, #ffe4e6 100%); border-color:#fecdd3;">
      <div class="card-header" style="border-color:rgba(254,205,211,0.6);">
        <div>
          <div class="card-title" style="color:#9f1239;">💌 亲子暖心即时留言</div>
          <div class="card-subtitle" style="color:#be123c;">给孩子写一句鼓励的话，将秒级弹窗展现在 Note 4 墨水屏右下角</div>
        </div>
      </div>

      <div class="form-group">
        <textarea id="parentMsgInput" style="height:110px; background:#fff;" placeholder="写一句鼓励的话，比如：宝贝，今天专注做题非常认真，妈妈为你点赞！写完作业早点休息哦～"></textarea>
      </div>

      <div style="font-size:12px; font-weight:700; color:#9f1239; margin-bottom:8px;">快捷暖心模板：</div>
      <div class="pill-group" style="margin-bottom:16px;">
        <button type="button" class="pill-btn" onclick="setMessageText('宝贝学习辛苦了！喝口水休息一下眼睛吧～')">🥤 喝水休息眼睛</button>
        <button type="button" class="pill-btn" onclick="setMessageText('今天专注力特别棒，妈妈为你感到骄傲！')">🌟 为你骄傲</button>
        <button type="button" class="pill-btn" onclick="setMessageText('遇到难题不要怕，一步一步来，你一定能解出来！')">💪 鼓励加油</button>
        <button type="button" class="pill-btn" onclick="setMessageText('写完作业我们一起出去散步吃水果吧！')">🍎 散步吃水果</button>
      </div>

      <button class="btn btn-primary" style="background:#e11d48; border-color:#e11d48; padding:10px 24px;" onclick="sendParentMessage()">💌 发送鼓励留言 (实时推屏)</button>
    </div>
  </div>

  <!-- Tab 14: 学情报表 -->
  <div id="tab-report" class="tab-content">
    <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); gap:14px; margin-bottom:16px;">
      <div class="card" style="text-align:center; padding:20px;">
        <div style="font-size:12px; color:var(--muted); font-weight:600;">今日作业完成率</div>
        <div id="repTaskRate" style="font-size:36px; font-weight:800; color:var(--primary); margin:6px 0;">0%</div>
        <div style="font-size:11px; color:var(--muted);">每日作业按时完成情况</div>
      </div>
      <div class="card" style="text-align:center; padding:20px;">
        <div style="font-size:12px; color:var(--muted); font-weight:600;">今日专注时长</div>
        <div id="repFocusMin" style="font-size:36px; font-weight:800; color:#ea580c; margin:6px 0;">0m</div>
        <div style="font-size:11px; color:var(--muted);">番茄钟深度专注沉浸时间</div>
      </div>
      <div class="card" style="text-align:center; padding:20px;">
        <div style="font-size:12px; color:var(--muted); font-weight:600;">生词库总词数</div>
        <div id="repVocabCnt" style="font-size:36px; font-weight:800; color:#10b981; margin:6px 0;">0</div>
        <div style="font-size:11px; color:var(--muted);">英语生词本词汇储备量</div>
      </div>
    </div>

    <div class="card">
      <div class="card-header">
        <div class="card-title">🏆 伴学荣誉与成就勋章墙</div>
      </div>
      <div id="badgesGrid" style="display:grid; grid-template-columns:repeat(auto-fill, minmax(180px, 1fr)); gap:12px;"></div>
    </div>
  </div>

  
  <!-- Tab 15: 伴学屏保管理 -->
  <div id="tab-screensaver" class="tab-content">
    <div class="feature-header-bar">
      <div style="display:flex; align-items:center; gap:8px;">
        <span style="font-weight:700; color:var(--text); font-size:14px;">🎨 伴学屏保功能</span>
        <span id="tabBadge-screensaver" class="badge" style="background:#dcfce7; color:#15803d;">🟢 本功能已启用</span>
      </div>
      <div style="display:flex; align-items:center; gap:8px;">
        <span style="font-size:12px; color:var(--muted);">允许待机屏保轮播展示：</span>
        <label class="switch">
          <input type="checkbox" id="tabToggle-screensaver" checked onchange="toggleFeatureDirect('screensaver', this.checked)">
          <span class="slider"></span>
        </label>
      </div>
    </div>

    <div class="card">
      <div class="card-header">
        <div>
          <div class="card-title">🎨 Note 4 墨水屏艺术长廊与屏保管理</div>
          <div class="card-subtitle">待机超时将自动进入选定屏保；点击任一名画可立即推屏</div>
        </div>
        <button class="btn btn-primary btn-sm" onclick="saveScreensaverConfig()">💾 保存屏保配置</button>
      </div>

      <div class="form-row" style="margin-bottom:14px;">
        <div>
          <label class="form-label">默认屏保轮播模式</label>
          <select id="ssDefaultMode">
            <option value="art">🎨 世界名画与原创长廊 (14幅高清16灰阶)</option>
            <option value="quote">💬 名人名言励志语录</option>
            <option value="poem">📜 经典课内古诗词</option>
            <option value="countdown">⏳ 目标大事记倒计时</option>
            <option value="timetable">📅 桌面课表备忘</option>
            <option value="mixed">🔀 综合随机混合轮播</option>
          </select>
        </div>
        <div>
          <label class="form-label">待机超时进入屏保 (秒)</label>
          <input type="text" id="ssTimeout" placeholder="默认 120 秒">
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-header">
        <div>
          <div class="card-title">🖼️ 全库 14 幅 16 灰阶艺术长廊 (每次进入随机打乱顺序)</div>
          <div class="card-subtitle">点击「立即推屏」即刻上屏；点击「导览」伴随语音艺术鉴赏</div>
        </div>
      </div>
      <div id="artGalleryGrid" style="display:grid; grid-template-columns:repeat(auto-fill, minmax(280px, 1fr)); gap:16px;"></div>
    </div>

    <div class="card">
      <div class="card-header">
        <div>
          <div class="card-title">⏳ 目标倒计时事件管理</div>
        </div>
      </div>
      <div id="countdownList" style="margin-bottom:16px;"></div>
      <div class="form-row" style="margin-bottom:10px;">
        <div><input type="text" id="newCdTitle" placeholder="事件名称，如：期末考试"></div>
        <div><input type="date" id="newCdDate"></div>
      </div>
      <div class="form-group">
        <input type="text" id="newCdSlogan" placeholder="励志标语，如：全力以赴，查漏补缺！">
      </div>
      <button class="btn btn-primary btn-sm" onclick="addCountdownEvent()">+ 添加倒计时事件</button>
    </div>
  </div>

  <!-- Tab 16: 菜单排序 -->
  <div id="tab-menu" class="tab-content">
    <div class="card">
      <div class="card-header">
        <div>
          <div class="card-title">📱 Note 4 功能菜单导航排序定制</div>
          <div class="card-subtitle">当前模式：<span id="menuModeSubtitle" style="font-weight:700; color:var(--primary);">智能频次排序</span></div>
        </div>
        <div style="display:flex; gap:6px;">
          <button class="btn btn-primary btn-sm" onclick="saveCustomMenuOrder()">💾 保存自定义排序</button>
          <button class="btn btn-secondary btn-sm" onclick="resetMenuOrder(false)">🔄 恢复智能排序</button>
          <button class="btn btn-secondary btn-sm" onclick="resetMenuOrder(true)">出厂重置</button>
        </div>
      </div>
      <div id="menuList"></div>
    </div>
  </div>

  <!-- Tab 17: 学伴与AI设置 -->
  <div id="tab-settings" class="tab-content">
    <div class="card">
      <div class="card-title">孩子资料</div>
      <div class="card-subtitle">用于调整称呼、讲解难度和例子。使用在线模型时，资料会随对话发送给模型服务；可留空或随时清除。</div>
      <div class="form-row"><div><label class="form-label" for="childProfileName">名字或昵称</label><input id="childProfileName" maxlength="40"></div>
      <div><label class="form-label" for="childProfileGrade">年级</label><select id="childProfileGrade"><option value="0">未设置</option><option value="1">一年级</option><option value="2">二年级</option><option value="3">三年级</option><option value="4">四年级</option><option value="5">五年级</option><option value="6">六年级</option></select></div></div>
      <div class="form-row"><div><label class="form-label" for="childProfileGender">性别（选填）</label><select id="childProfileGender"><option value="">未设置</option><option>男</option><option>女</option><option>其他</option></select></div>
      <div><label class="form-label" for="childProfileInterests">兴趣爱好</label><input id="childProfileInterests" maxlength="200" placeholder="例如恐龙、画画"></div></div>
      <button class="btn btn-primary" onclick="saveChildProfile()">保存孩子资料</button>
    </div>
    <!-- Card 1: 🦞 小龙虾学伴人设定制 -->
    <div class="card">
      <div class="card-header">
        <div>
          <div class="card-title">🦞 小龙虾学伴人设与交流风格定制</div>
          <div class="card-subtitle">自由设定学伴对孩子的称呼、交流语气与人设特质，修改后实时同步 Note 4</div>
        </div>
      </div>

      <div class="form-row" style="margin-bottom:12px;">
        <div>
          <label class="form-label">学伴昵称</label>
          <input type="text" id="cfgCompName" value="虾崽" placeholder="如：虾崽、小龙老师">
        </div>
        <div>
          <label class="form-label">语言交流风格</label>
          <select id="cfgCompStyle">
            <option value="温柔耐心">🌸 温柔耐心 (鼓励倾听·适合低年级)</option>
            <option value="幽默风趣">😄 幽默风趣 (段子好玩·寓教于乐)</option>
            <option value="启发提问">🤔 启发提问 (苏格拉底·引导深思)</option>
            <option value="严谨清晰">📐 严谨清晰 (条理分明·重点明确)</option>
          </select>
        </div>
      </div>

      <div class="form-group">
        <label class="form-label">预设人设模板 (点击快速套用)</label>
        <div class="pill-group">
          <button type="button" class="pill-btn" onclick="applyPresetCompanion('classic')">经典虾崽</button>
          <button type="button" class="pill-btn" onclick="applyPresetCompanion('humor')">幽默大白</button>
          <button type="button" class="pill-btn" onclick="applyPresetCompanion('socratic')">启发教练</button>
          <button type="button" class="pill-btn" onclick="applyPresetCompanion('english')">双语外教</button>
        </div>
      </div>

      <div class="form-group">
        <label class="form-label">人设背景与性格特质 (Persona Prompt)</label>
        <textarea id="cfgCompPersona" style="height:90px;" placeholder="设定学伴的性格与身份特质..."></textarea>
      </div>

      <div class="form-group">
        <label class="form-label">墨水屏对话叮嘱与字数控制 (Guidance)</label>
        <textarea id="cfgCompGuidance" style="height:90px;" placeholder="针对墨水屏排版的字数与句式叮嘱..."></textarea>
      </div>
    </div>

    <!-- Card 2: 🧠 LLM 大语言模型引擎设置 -->
    <div class="card">
      <div class="card-header">
        <div>
          <div class="card-title">🧠 LLM 大语言模型引擎设置</div>
          <div class="card-subtitle">驱动 Note 4 诗词飞花令、折线拆题、事实核查与智能问答的认知引擎</div>
        </div>
        <span id="llmModeBadge" class="badge"></span>
      </div>

      <div class="form-group">
        <label class="form-label">运行版本选择</label>
        <div class="radio-card-group">
          <label class="radio-card" id="cardLlmLocal">
            <input type="radio" name="llmMode" value="local" onchange="toggleLlmMode('local')">
            <div>
              <div class="radio-card-text">🖥️ 服务器本地版本 (Local)</div>
              <div class="radio-card-sub">离线智能规则库 / 本地 Ollama 无需外网</div>
            </div>
          </label>
          <label class="radio-card" id="cardLlmOnline">
            <input type="radio" name="llmMode" value="online" checked onchange="toggleLlmMode('online')">
            <div>
              <div class="radio-card-text">☁️ 在线商业大模型 (Online)</div>
              <div class="radio-card-sub">多厂商直连 DeepSeek / 通义千问等</div>
            </div>
          </label>
        </div>
      </div>

      <div id="onlineLlmSettings">
        <div class="form-row" style="margin-bottom:12px;">
          <div>
            <label class="form-label">服务提供商 (Provider)</label>
            <select id="cfgLlmProvider">
              <option value="deepseek">DeepSeek (深度求索·推荐高性价比)</option>
              <option value="qwen">阿里通义千问 (Qwen)</option>
              <option value="openai">OpenAI (GPT-4o/o3-mini)</option>
              <option value="coze">字节扣子 Coze API</option>
              <option value="custom">自定义兼容接口 (Ollama/vLLM等)</option>
            </select>
          </div>
          <div>
            <label class="form-label">模型名称 (Model)</label>
            <input type="text" id="cfgLlmModel" placeholder="如 deepseek-chat">
          </div>
        </div>

        <div class="form-group">
          <label class="form-label">API Base URL</label>
          <input type="text" id="cfgLlmBaseUrl" placeholder="https://api.deepseek.com/v1">
        </div>

        <div class="form-group">
          <label class="form-label">API Key</label>
          <div style="position:relative;">
            <input type="password" id="cfgLlmApiKey" placeholder="sk-xxxx">
            <button type="button" onclick="togglePasswordVisibility('cfgLlmApiKey')" style="position:absolute; right:10px; top:50%; transform:translateY(-50%); border:none; background:transparent; cursor:pointer; font-size:16px;">👁️</button>
          </div>
        </div>

        <div style="display:flex; align-items:center; gap:12px; margin-top:10px;">
          <button class="btn btn-secondary btn-sm" onclick="testLlmConnection()">⚡ 测试模型连通性</button>
          <span id="llmTestResult" style="font-size:12px; font-weight:600;"></span>
        </div>
      </div>
    </div>

    <!-- Card 3: 👁️ 视觉 OCR 与拍照批改设置 -->
    <div class="card">
      <div class="card-header">
        <div>
          <div class="card-title">👁️ 视觉 OCR 与作业拍照批改引擎</div>
          <div class="card-subtitle">用于手机端拍摄听写本、课本古诗与错题时的智能切图与错字识别</div>
        </div>
        <span id="ocrModeBadge" class="badge"></span>
      </div>

      <div class="form-group">
        <label class="form-label">OCR 引擎选择</label>
        <div class="radio-card-group">
          <label class="radio-card" id="cardOcrLocal">
            <input type="radio" name="ocrMode" value="local" onchange="toggleOcrMode('local')">
            <div>
              <div class="radio-card-text">🖥️ 本地离线引擎 (轻量规则)</div>
              <div class="radio-card-sub">零外部 API 依赖，基础字词匹配</div>
            </div>
          </label>
          <label class="radio-card" id="cardOcrOnline">
            <input type="radio" name="ocrMode" value="online" checked onchange="toggleOcrMode('online')">
            <div>
              <div class="radio-card-text">☁️ 在线视觉模型 (高精度)</div>
              <div class="radio-card-sub">支持复杂笔迹、草稿与间架结构评分</div>
            </div>
          </label>
        </div>
      </div>

      <div id="onlineOcrSettings">
        <div class="form-group">
          <label class="form-label">视觉模型 (Vision Model)</label>
          <input type="text" id="cfgOcrModel" placeholder="如 qwen-vl-max / gpt-4o">
        </div>
        <div class="form-group">
          <label class="form-label">OCR Base URL</label>
          <input type="text" id="cfgOcrBaseUrl" placeholder="如 https://dashscope.aliyuncs.com/compatible-mode/v1">
        </div>
        <div class="form-group">
          <label class="form-label">OCR API Key</label>
          <div style="position:relative;">
            <input type="password" id="cfgOcrApiKey" placeholder="sk-xxxx">
            <button type="button" onclick="togglePasswordVisibility('cfgOcrApiKey')" style="position:absolute; right:10px; top:50%; transform:translateY(-50%); border:none; background:transparent; cursor:pointer; font-size:16px;">👁️</button>
          </div>
        </div>
      </div>
    </div>

    <!-- Bottom Actions -->
    <div class="card" style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px;">
      <button class="btn btn-primary" style="padding:11px 24px;" onclick="saveAllSettings()">💾 保存全部系统设置</button>
      <button class="btn btn-secondary" onclick="resetAllSettings()">🔄 恢复出厂设置</button>
    </div>
  </div>

  
  <!-- Tab 18: 小学新鲜事 -->
  <div id="tab-news" class="tab-content">
    <div class="feature-header-bar">
      <div style="display:flex; align-items:center; gap:8px;">
        <span style="font-weight:700; color:var(--text); font-size:14px;">📰 小学新鲜事资讯长廊</span>
        <span id="tabBadge-news" class="badge" style="background:#dcfce7; color:#15803d;">🟢 本功能已启用</span>
      </div>
      <div style="display:flex; align-items:center; gap:8px;">
        <span style="font-size:12px; color:var(--muted);">允许孩子在 Note 4 上浏览：</span>
        <label class="switch">
          <input type="checkbox" id="tabToggle-news" checked onchange="toggleFeatureDirect('news', this.checked)">
          <span class="slider"></span>
        </label>
      </div>
    </div>

    <div class="card">
      <div class="card-header">
        <div>
          <div class="card-title">📰 小学新鲜事 · 专属少儿资讯推送</div>
          <div class="card-subtitle">预设内容为体验示例，请核实活动信息；也可手动录入。</div>
        </div>
        <span id="newsRegionBadge" class="badge" style="background:#eff6ff; color:#1d4ed8;">当前关注地区：全国</span>
      </div>

      <div class="form-group">
        <label class="form-label">选择或输入孩子关注地区</label>
        <div class="pill-group" style="margin-bottom:8px;">
          <button type="button" class="pill-btn" onclick="setQuickNewsRegion('全国')">全国</button>
          <button type="button" class="pill-btn" onclick="setQuickNewsRegion('江苏')">江苏</button>
          <button type="button" class="pill-btn" onclick="setQuickNewsRegion('上海')">上海</button>
          <button type="button" class="pill-btn" onclick="setQuickNewsRegion('浙江')">浙江</button>
          <button type="button" class="pill-btn" onclick="setQuickNewsRegion('北京')">北京</button>
          <button type="button" class="pill-btn" onclick="setQuickNewsRegion('广东')">广东</button>
        </div>
        <div style="display:flex; gap:8px;">
          <input type="text" id="newsRegionInput" placeholder="输入省份或城市名称，如 江苏 / 南京">
          <button class="btn btn-primary btn-sm" onclick="saveNewsRegion()" style="flex-shrink:0;">保存地区</button>
        </div>
      </div>

      <div class="form-row" style="margin-top:14px; padding-top:14px; border-top:1px dashed var(--border);">
        <div>
          <label class="form-label">关键词白名单 (优先展示)</label>
          <input type="text" id="newsWhitelistInput" placeholder="以逗号分隔，如 机器人, 编程, 征文">
        </div>
        <div>
          <label class="form-label">关键词黑名单 (主动屏蔽)</label>
          <input type="text" id="newsBlacklistInput" placeholder="以逗号分隔，如 军训, 高中, 考研">
        </div>
      </div>
      <div class="btn-row">
        <button class="btn btn-secondary btn-sm" onclick="saveNewsKeywords()">💾 保存关键词配置</button>
      </div>
    </div>

    <div class="card">
      <div class="card-header">
        <div>
          <div class="card-title">📡 关注信源管理 <span class="card-subtitle" id="newsStatsSpan" style="margin-left:8px;"></span></div>
        </div>
        <button class="btn btn-secondary btn-sm" onclick="toggleAddSourceForm()">+ 关注新信源</button>
      </div>
      <div id="newsSourcesList" style="display:flex; flex-wrap:wrap; gap:8px; margin-bottom:12px;"></div>

      <div id="addSourceForm" style="display:none; background:#f8fafc; border:1px solid var(--border); border-radius:10px; padding:12px; margin-top:10px;">
        <div class="form-row" style="margin-bottom:8px;">
          <div><input type="text" id="newSourceName" placeholder="信源名称 (如：南京少儿科普网)"></div>
          <div><input type="text" id="newSourceCat" placeholder="分类 (如：科普/活动)"></div>
        </div>
        <div class="form-row" style="margin-bottom:8px;">
          <div><input type="text" id="newSourceReg" placeholder="覆盖地区 (如：江苏/南京)"></div>
          <div><input type="text" id="newSourceUrl" placeholder="网址或抓取链接 (可选)"></div>
        </div>
        <div class="btn-row">
          <button class="btn btn-primary btn-sm" onclick="submitNewSource()">确认添加信源</button>
          <button class="btn btn-secondary btn-sm" onclick="toggleAddSourceForm()">取消</button>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-header">
        <div>
          <div class="card-title">📋 新闻条目列表与校内活动录入</div>
          <div class="card-subtitle">支持家长录入校内活动并秒级推送到 Note 4 墨水屏</div>
        </div>
        <button class="btn btn-primary btn-sm" onclick="toggleAddNewsForm()">✍️ 录入自定义校内活动</button>
      </div>

      <div id="addNewsForm" style="display:none; background:#f8fafc; border:1px solid var(--border); border-radius:10px; padding:14px; margin-bottom:16px;">
        <div class="form-row" style="margin-bottom:8px;">
          <div><input type="text" id="customNewsTitle" placeholder="活动标题 (如：学校秋季科技节作品征集)"></div>
          <div><input type="text" id="customNewsCat" placeholder="分类 (默认: 活动)"></div>
        </div>
        <div class="form-group">
          <input type="text" id="customNewsReg" placeholder="所在地区 (默认: 全国)">
        </div>
        <div class="form-group">
          <textarea id="customNewsSummary" style="height:60px;" placeholder="简要介绍 (一句话概述要点)"></textarea>
        </div>
        <div class="form-group">
          <textarea id="customNewsContent" style="height:80px;" placeholder="活动详细说明、参赛要求或截止时间..."></textarea>
        </div>
        <div class="btn-row">
          <button class="btn btn-primary btn-sm" onclick="submitCustomNews()">保存活动</button>
          <button class="btn btn-secondary btn-sm" onclick="toggleAddNewsForm()">取消</button>
        </div>
      </div>

      <div id="newsItemsList"></div>
    </div>
  </div>

  
  <!-- Tab: 拓展素养题库 (费曼小老师、猜猜看、事实核查、飞花令) -->
  <div id="tab-thinking" class="tab-content">
    <div class="tab-grid-2col">
      <!-- 费曼小老师 -->
      <div class="card">
        <div class="card-header">
          <div>
            <div class="card-title">🗣️ 费曼小老师 · 讲解主题</div>
            <div class="card-subtitle">指定孩子给小龙虾讲懂的主题</div>
          </div>
          <div style="display:flex; align-items:center; gap:8px;">
            <button class="btn btn-primary btn-sm" onclick="openAddFeynmanModal()">➕ 添加主题</button>
            <label class="switch" title="开关此功能">
              <input type="checkbox" id="tabToggle-feynman" checked onchange="toggleFeatureDirect('feynman', this.checked)">
              <span class="slider"></span>
            </label>
          </div>
        </div>
        <div id="feynmanTopicsList"></div>
      </div>

      <!-- 趣味科普·猜猜看 -->
      <div class="card">
        <div class="card-header">
          <div>
            <div class="card-title">❓ 趣味科普 · 猜猜看谜题</div>
            <div class="card-subtitle">录入动物、植物、天体或历史人物线索</div>
          </div>
          <div style="display:flex; align-items:center; gap:8px;">
            <button class="btn btn-primary btn-sm" onclick="openAddGuessModal()">➕ 添加谜题</button>
            <label class="switch" title="开关此功能">
              <input type="checkbox" id="tabToggle-guess" checked onchange="toggleFeatureDirect('guess', this.checked)">
              <span class="slider"></span>
            </label>
          </div>
        </div>
        <div id="guessesList"></div>
      </div>

      <!-- 科学探究·事实核查 -->
      <div class="card">
        <div class="card-header">
          <div>
            <div class="card-title">🔬 科学常识 · 事实辨伪库</div>
            <div class="card-subtitle">录入日常科普误区与破谣原理说明</div>
          </div>
          <div style="display:flex; align-items:center; gap:8px;">
            <button class="btn btn-primary btn-sm" onclick="openAddFactModal()">➕ 添加辨伪</button>
            <label class="switch" title="开关此功能">
              <input type="checkbox" id="tabToggle-verify" checked onchange="toggleFeatureDirect('verify', this.checked)">
              <span class="slider"></span>
            </label>
          </div>
        </div>
        <div id="factsList"></div>
      </div>

      <!-- 国学素养·飞花令 -->
      <div class="card">
        <div class="card-header">
          <div>
            <div class="card-title">🌸 诗词飞花令 · 对仗句库</div>
            <div class="card-subtitle">自定义令字与对仗诗句补充</div>
          </div>
          <div style="display:flex; align-items:center; gap:8px;">
            <button class="btn btn-primary btn-sm" onclick="openAddFeihuaModal()">➕ 添加令字句</button>
            <label class="switch" title="开关此功能">
              <input type="checkbox" id="tabToggle-feihua" checked onchange="toggleFeatureDirect('feihua', this.checked)">
              <span class="slider"></span>
            </label>
          </div>
        </div>
        <div id="feihuaList"></div>
      </div>
    </div>
  </div>

  <!-- Tab: 19大功能权限与开关控制 (features) -->
  <div id="tab-features" class="tab-content">
    <div class="card" style="margin-bottom:16px;">
      <div class="card-header">
        <div>
          <div class="card-title">🛡️ Note 4 墨水屏全功能权限与家长总开关</div>
          <div class="card-subtitle">全站 19 大功能自由开启/停用。停用后墨水屏菜单自动过滤隐藏，语音切入时礼貌播报拦截。</div>
        </div>
        <span id="featuresCountBadge" class="badge" style="background:#dcfce7; color:#15803d; font-size:13px; font-weight:700;">已启用 19 / 共有 19 项</span>
      </div>
      <div style="background:#f8fafc; border-radius:10px; padding:12px 16px; margin-bottom:16px; font-size:12px; color:#475569; display:flex; align-items:center; gap:8px;">
        <span>💡 <b>实时协同机制</b>：家长在此处切换开关后，正在连线的 Note 4 硬件墨水屏将即时刷新菜单并生效，无需关机重启。</span>
      </div>
      <div id="featuresGrid" style="display:grid; grid-template-columns:repeat(auto-fill, minmax(280px, 1fr)); gap:12px;"></div>
    </div>
  </div>

  <!-- Modals for Full CRUD -->

  <!-- Modal: 奖励星星 -->
  <div id="modalAddStars" class="modal-overlay" style="display:none;" onclick="if(event.target===this)closeAddStarsModal()">
    <div class="modal-card">
      <div class="modal-title">
        <span>⭐ 奖励星星</span>
        <button type="button" onclick="closeAddStarsModal()" style="border:none; background:none; font-size:18px; cursor:pointer;">&times;</button>
      </div>
      <div class="form-group">
        <label class="form-label">奖励颗数</label>
        <div class="pill-group">
          <button type="button" class="pill-btn active" onclick="selectStarCount(1, this)">+1 颗星</button>
          <button type="button" class="pill-btn" onclick="selectStarCount(2, this)">+2 颗星</button>
          <button type="button" class="pill-btn" onclick="selectStarCount(5, this)">+5 颗星</button>
          <button type="button" class="pill-btn" onclick="selectStarCount(10, this)">+10 颗星</button>
        </div>
        <input type="hidden" id="inputStarCount" value="1">
      </div>
      <div class="form-group">
        <label class="form-label">奖励理由</label>
        <div class="pill-group">
          <button type="button" class="pill-btn active" onclick="selectStarReason('认真完成全部作业', this)">认真完成作业</button>
          <button type="button" class="pill-btn" onclick="selectStarReason('专注做题不走神', this)">专注不走神</button>
          <button type="button" class="pill-btn" onclick="selectStarReason('主动背诵古诗词', this)">主动背诵</button>
          <button type="button" class="pill-btn" onclick="selectStarReason('口算全对满分', this)">口算满分</button>
        </div>
        <input type="text" id="inputStarReason" placeholder="自定义奖励理由" value="认真完成全部作业">
      </div>
      <div class="btn-row" style="margin-top:16px;">
        <button class="btn btn-primary btn-block" onclick="confirmAddStars()">确认奖励并即时推屏</button>
      </div>
    </div>
  </div>

  <!-- Modal: 添加心愿 -->
  <div id="modalAddWish" class="modal-overlay" style="display:none;" onclick="if(event.target===this)closeAddWishModal()">
    <div class="modal-card">
      <div class="modal-title">
        <span>🎁 添加心愿梦想</span>
        <button type="button" onclick="closeAddWishModal()" style="border:none; background:none; font-size:18px; cursor:pointer;">&times;</button>
      </div>
      <div class="form-group">
        <label class="form-label">心愿名称</label>
        <input type="text" id="inputWishTitle" placeholder="如：去游乐场玩一次">
      </div>
      <div class="form-group">
        <label class="form-label">所需星星数</label>
        <div class="pill-group">
          <button type="button" class="pill-btn active" onclick="selectWishStars(10, this)">10 颗</button>
          <button type="button" class="pill-btn" onclick="selectWishStars(20, this)">20 颗</button>
          <button type="button" class="pill-btn" onclick="selectWishStars(50, this)">50 颗</button>
          <button type="button" class="pill-btn" onclick="selectWishStars(100, this)">100 颗</button>
        </div>
        <input type="hidden" id="inputWishStars" value="10">
      </div>
      <div class="form-group">
        <label class="form-label">心愿图标 Emoji</label>
        <div class="pill-group">
          <button type="button" class="pill-btn active" onclick="selectWishEmoji('🎡', this)">🎡 游乐园</button>
          <button type="button" class="pill-btn" onclick="selectWishEmoji('🍦', this)">🍦 冰淇淋</button>
          <button type="button" class="pill-btn" onclick="selectWishEmoji('🎮', this)">🎮 游戏时间</button>
          <button type="button" class="pill-btn" onclick="selectWishEmoji('📚', this)">📚 课外书</button>
          <button type="button" class="pill-btn" onclick="selectWishEmoji('🧸', this)">🧸 玩具</button>
        </div>
        <input type="hidden" id="inputWishIcon" value="🎡">
      </div>
      <div class="btn-row" style="margin-top:16px;">
        <button class="btn btn-primary btn-block" onclick="confirmAddWish()">确认添加心愿</button>
      </div>
    </div>
  </div>

  <!-- Modal: 修改作业任务 -->
  <div id="modalEditTask" class="modal-overlay" style="display:none;" onclick="if(event.target===this)closeModal('modalEditTask')">
    <div class="modal-card">
      <div class="modal-title">
        <span>✏️ 修改作业任务</span>
        <button type="button" onclick="closeModal('modalEditTask')" style="border:none; background:none; font-size:18px; cursor:pointer;">&times;</button>
      </div>
      <input type="hidden" id="editTaskId">
      <div class="form-group">
        <label class="form-label">作业内容</label>
        <input type="text" id="editTaskText" placeholder="作业内容">
      </div>
      <div class="form-group">
        <label class="form-label">学科分类</label>
        <select id="editTaskSubject" class="form-control" style="width:100%; height:38px; border:1px solid var(--border); border-radius:6px; padding:0 8px;">
          <option value="语文">语文</option>
          <option value="数学">数学</option>
          <option value="英语">英语</option>
          <option value="科学">科学</option>
          <option value="其他">其他</option>
        </select>
      </div>
      <div class="btn-row" style="margin-top:16px;">
        <button class="btn btn-primary" onclick="saveEditTask()">保存修改并同步</button>
        <button class="btn btn-secondary" onclick="closeModal('modalEditTask')">取消</button>
      </div>
    </div>
  </div>

  <!-- Modal: 修改英语单词 -->
  <div id="modalEditVocab" class="modal-overlay" style="display:none;" onclick="if(event.target===this)closeModal('modalEditVocab')">
    <div class="modal-card">
      <div class="modal-title">
        <span>✏️ 修改英语生词</span>
        <button type="button" onclick="closeModal('modalEditVocab')" style="border:none; background:none; font-size:18px; cursor:pointer;">&times;</button>
      </div>
      <input type="hidden" id="editVocabId">
      <div class="form-group">
        <label class="form-label">英文单词</label>
        <input type="text" id="editVocabWord">
      </div>
      <div class="form-group">
        <label class="form-label">中文释义</label>
        <input type="text" id="editVocabMeaning">
      </div>
      <div class="form-group">
        <label class="form-label">例句 (英文)</label>
        <input type="text" id="editVocabExample">
      </div>
      <div class="form-group">
        <label class="form-label">例句翻译 (中文)</label>
        <input type="text" id="editVocabExampleCn">
      </div>
      <div class="btn-row" style="margin-top:16px;">
        <button class="btn btn-primary" onclick="saveEditVocab()">保存单词</button>
        <button class="btn btn-secondary" onclick="closeModal('modalEditVocab')">取消</button>
      </div>
    </div>
  </div>

  <!-- Modal: 修改习惯 -->
  <div id="modalEditHabit" class="modal-overlay" style="display:none;" onclick="if(event.target===this)closeModal('modalEditHabit')">
    <div class="modal-card">
      <div class="modal-title">
        <span>✏️ 修改习惯打卡项</span>
        <button type="button" onclick="closeModal('modalEditHabit')" style="border:none; background:none; font-size:18px; cursor:pointer;">&times;</button>
      </div>
      <input type="hidden" id="editHabitId">
      <div class="form-group">
        <label class="form-label">习惯名称</label>
        <input type="text" id="editHabitName">
      </div>
      <div class="form-group">
        <label class="form-label">图标 Emoji</label>
        <input type="text" id="editHabitIcon" style="width:80px;">
      </div>
      <div class="form-group">
        <label class="form-label">坚持目标天数</label>
        <input type="number" id="editHabitDays" value="21" style="width:100px;">
      </div>
      <div class="btn-row" style="margin-top:16px;">
        <button class="btn btn-primary" onclick="saveEditHabit()">保存习惯</button>
        <button class="btn btn-secondary" onclick="closeModal('modalEditHabit')">取消</button>
      </div>
    </div>
  </div>

  <!-- Modal: 修改心愿 -->
  <div id="modalEditWish" class="modal-overlay" style="display:none;" onclick="if(event.target===this)closeModal('modalEditWish')">
    <div class="modal-card">
      <div class="modal-title">
        <span>✏️ 修改心愿内容</span>
        <button type="button" onclick="closeModal('modalEditWish')" style="border:none; background:none; font-size:18px; cursor:pointer;">&times;</button>
      </div>
      <input type="hidden" id="editWishId">
      <div class="form-group">
        <label class="form-label">心愿标题</label>
        <input type="text" id="editWishTitle">
      </div>
      <div class="form-group">
        <label class="form-label">所需星星数</label>
        <input type="number" id="editWishStars" value="20" style="width:100px;">
      </div>
      <div class="btn-row" style="margin-top:16px;">
        <button class="btn btn-primary" onclick="saveEditWish()">保存心愿</button>
        <button class="btn btn-secondary" onclick="closeModal('modalEditWish')">取消</button>
      </div>
    </div>
  </div>

  <!-- Modal: 添加/修改错题 -->
  <div id="modalAddMistake" class="modal-overlay" style="display:none;" onclick="if(event.target===this)closeModal('modalAddMistake')">
    <div class="modal-card">
      <div class="modal-title">
        <span>➕ 手动录入新错题</span>
        <button type="button" onclick="closeModal('modalAddMistake')" style="border:none; background:none; font-size:18px; cursor:pointer;">&times;</button>
      </div>
      <div class="form-group">
        <label class="form-label">学科分类</label>
        <select id="newMistakeSubject" class="form-control" style="width:100%; height:38px; border:1px solid var(--border); border-radius:6px; padding:0 8px;">
          <option value="数学">数学</option>
          <option value="语文">语文</option>
          <option value="英语">英语</option>
          <option value="科学">科学</option>
        </select>
      </div>
      <div class="form-group">
        <label class="form-label">题目内容</label>
        <textarea id="newMistakeQuestion" style="height:70px;" placeholder="错题题目详情"></textarea>
      </div>
      <div class="form-group">
        <label class="form-label">正确答案 / 解析提示</label>
        <textarea id="newMistakeAnswer" style="height:60px;" placeholder="正确思路或答案"></textarea>
      </div>
      <div class="btn-row" style="margin-top:16px;">
        <button class="btn btn-primary" onclick="saveNewMistake()">录入错题本</button>
        <button class="btn btn-secondary" onclick="closeModal('modalAddMistake')">取消</button>
      </div>
    </div>
  </div>

  <div id="modalEditMistake" class="modal-overlay" style="display:none;" onclick="if(event.target===this)closeModal('modalEditMistake')">
    <div class="modal-card">
      <div class="modal-title">
        <span>✏️ 修改错题</span>
        <button type="button" onclick="closeModal('modalEditMistake')" style="border:none; background:none; font-size:18px; cursor:pointer;">&times;</button>
      </div>
      <input type="hidden" id="editMistakeId">
      <div class="form-group">
        <label class="form-label">学科分类</label>
        <select id="editMistakeSubject" class="form-control" style="width:100%; height:38px; border:1px solid var(--border); border-radius:6px; padding:0 8px;">
          <option value="数学">数学</option>
          <option value="语文">语文</option>
          <option value="英语">英语</option>
          <option value="科学">科学</option>
        </select>
      </div>
      <div class="form-group">
        <label class="form-label">题目内容</label>
        <textarea id="editMistakeQuestion" style="height:70px;"></textarea>
      </div>
      <div class="form-group">
        <label class="form-label">正确答案 / 解析提示</label>
        <textarea id="editMistakeAnswer" style="height:60px;"></textarea>
      </div>
      <div class="btn-row" style="margin-top:16px;">
        <button class="btn btn-primary" onclick="saveEditMistake()">保存修改</button>
        <button class="btn btn-secondary" onclick="closeModal('modalEditMistake')">取消</button>
      </div>
    </div>
  </div>

  <!-- Modal: 修改倒计时 -->
  <div id="modalEditCountdown" class="modal-overlay" style="display:none;" onclick="if(event.target===this)closeModal('modalEditCountdown')">
    <div class="modal-card">
      <div class="modal-title">
        <span>✏️ 修改倒计时重要日</span>
        <button type="button" onclick="closeModal('modalEditCountdown')" style="border:none; background:none; font-size:18px; cursor:pointer;">&times;</button>
      </div>
      <input type="hidden" id="editCountdownId">
      <div class="form-group">
        <label class="form-label">事件标题</label>
        <input type="text" id="editCountdownTitle" placeholder="如：期末考试">
      </div>
      <div class="form-group">
        <label class="form-label">目标日期 (YYYY-MM-DD)</label>
        <input type="date" id="editCountdownTargetDate">
      </div>
      <div class="form-group">
        <label class="form-label">励志口号 / 寄语</label>
        <input type="text" id="editCountdownSlogan" placeholder="如：细心检查，笃行致远">
      </div>
      <div class="btn-row" style="margin-top:16px;">
        <button class="btn btn-primary" onclick="saveEditCountdown()">保存事件</button>
        <button class="btn btn-secondary" onclick="closeModal('modalEditCountdown')">取消</button>
      </div>
    </div>
  </div>

  <!-- Modal: 添加/修改自定义古诗 -->
  <div id="modalPoem" class="modal-overlay" style="display:none;" onclick="if(event.target===this)closeModal('modalPoem')">
    <div class="modal-card">
      <div class="modal-title">
        <span id="poemModalTitle">➕ 添加自定义古诗</span>
        <button type="button" onclick="closeModal('modalPoem')" style="border:none; background:none; font-size:18px; cursor:pointer;">&times;</button>
      </div>
      <input type="hidden" id="editPoemId">
      <div class="form-group">
        <label class="form-label">诗名 / 标题</label>
        <input type="text" id="poemTitleInput" placeholder="如：登鹳雀楼">
      </div>
      <div class="form-group" style="display:flex; gap:10px;">
        <div style="flex:1;">
          <label class="form-label">作者</label>
          <input type="text" id="poemAuthorInput" placeholder="王之涣">
        </div>
        <div style="width:100px;">
          <label class="form-label">朝代</label>
          <input type="text" id="poemDynastyInput" value="唐">
        </div>
      </div>
      <div class="form-group">
        <label class="form-label">诗句内容 (每行一句)</label>
        <textarea id="poemLinesInput" style="height:90px;" placeholder="白日依山尽
黄河入海流
欲穷千里目
更上一层楼"></textarea>
      </div>
      <div class="btn-row" style="margin-top:16px;">
        <button class="btn btn-primary" onclick="savePoem()">保存并存入古诗库</button>
        <button class="btn btn-secondary" onclick="closeModal('modalPoem')">取消</button>
      </div>
    </div>
  </div>

  <!-- Modal: 添加/修改自定义成语 -->
  <div id="modalIdiom" class="modal-overlay" style="display:none;" onclick="if(event.target===this)closeModal('modalIdiom')">
    <div class="modal-card">
      <div class="modal-title">
        <span id="idiomModalTitle">➕ 添加自定义成语</span>
        <button type="button" onclick="closeModal('modalIdiom')" style="border:none; background:none; font-size:18px; cursor:pointer;">&times;</button>
      </div>
      <input type="hidden" id="editIdiomId">
      <div class="form-group" style="display:flex; gap:10px;">
        <div style="flex:1;">
          <label class="form-label">成语名称</label>
          <input type="text" id="idiomInput" placeholder="闻鸡起舞">
        </div>
        <div style="flex:1;">
          <label class="form-label">拼音</label>
          <input type="text" id="idiomPinyinInput" placeholder="wén jī qǐ wǔ">
        </div>
      </div>
      <div class="form-group">
        <label class="form-label">释义解析</label>
        <input type="text" id="idiomMeaningInput" placeholder="听到鸡叫就起来舞剑，形容发奋图强。">
      </div>
      <div class="form-group">
        <label class="form-label">出处典故</label>
        <input type="text" id="idiomSourceInput" placeholder="《晋书·祖逖传》">
      </div>
      <div class="form-group">
        <label class="form-label">造句示例</label>
        <input type="text" id="idiomExampleInput" placeholder="我们要有闻鸡起舞的精神，刻苦钻研。">
      </div>
      <div class="btn-row" style="margin-top:16px;">
        <button class="btn btn-primary" onclick="saveIdiom()">保存成语</button>
        <button class="btn btn-secondary" onclick="closeModal('modalIdiom')">取消</button>
      </div>
    </div>
  </div>

  <!-- Modal: 添加/修改靶向算式 -->
  <div id="modalArith" class="modal-overlay" style="display:none;" onclick="if(event.target===this)closeModal('modalArith')">
    <div class="modal-card">
      <div class="modal-title">
        <span id="arithModalTitle">➕ 添加靶向算式</span>
        <button type="button" onclick="closeModal('modalArith')" style="border:none; background:none; font-size:18px; cursor:pointer;">&times;</button>
      </div>
      <input type="hidden" id="editArithId">
      <div class="form-group">
        <label class="form-label">算式表达式</label>
        <input type="text" id="arithExprInput" placeholder="如：25 × 4 或 100 - 37">
      </div>
      <div class="form-group">
        <label class="form-label">标准计算答案</label>
        <input type="text" id="arithAnsInput" placeholder="如：100 或 63">
      </div>
      <div class="form-group">
        <label class="form-label">解题提示 (可选)</label>
        <input type="text" id="arithHintInput" placeholder="如：简算技巧：25×4=100">
      </div>
      <div class="btn-row" style="margin-top:16px;">
        <button class="btn btn-primary" onclick="saveArith()">保存到靶向题库</button>
        <button class="btn btn-secondary" onclick="closeModal('modalArith')">取消</button>
      </div>
    </div>
  </div>

  <!-- Modal: 添加/修改阅读短文 -->
  <div id="modalReading" class="modal-overlay" style="display:none;" onclick="if(event.target===this)closeModal('modalReading')">
    <div class="modal-card" style="max-width:540px;">
      <div class="modal-title">
        <span id="readingModalTitle">➕ 添加自定义短文故事</span>
        <button type="button" onclick="closeModal('modalReading')" style="border:none; background:none; font-size:18px; cursor:pointer;">&times;</button>
      </div>
      <input type="hidden" id="editReadingId">
      <div class="form-group">
        <label class="form-label">短文标题</label>
        <input type="text" id="readingTitleInput" placeholder="如：The Ant and the Dove">
      </div>
      <div class="form-group">
        <label class="form-label">适合年级 / 难度</label>
        <input type="text" id="readingLevelInput" value="小学通用 (3-4年级)">
      </div>
      <div class="form-group">
        <label class="form-label">短文逐句内容 (英文 || 中文翻译，每行一句)</label>
        <textarea id="readingSentencesInput" style="height:120px;" placeholder="An ant was drinking at a river. || 一只蚂蚁在河边喝水。
A dove dropped a leaf to save it. || 一只鸽子扔下一片树叶救了它。"></textarea>
      </div>
      <div class="btn-row" style="margin-top:16px;">
        <button class="btn btn-primary" onclick="saveReading()">保存短文故事</button>
        <button class="btn btn-secondary" onclick="closeModal('modalReading')">取消</button>
      </div>
    </div>
  </div>

  <!-- Modal: 费曼主题 -->
  <div id="modalFeynman" class="modal-overlay" style="display:none;" onclick="if(event.target===this)closeModal('modalFeynman')">
    <div class="modal-card">
      <div class="modal-title">
        <span>🗣️ 费曼小老师 · 讲解主题</span>
        <button type="button" onclick="closeModal('modalFeynman')" style="border:none; background:none; font-size:18px; cursor:pointer;">&times;</button>
      </div>
      <input type="hidden" id="editFeynmanId">
      <div class="form-group">
        <label class="form-label">讲解主题</label>
        <input type="text" id="feynmanTopicInput" placeholder="如：为什么会有昼夜交替？">
      </div>
      <div class="form-group">
        <label class="form-label">小龙虾启发引导词</label>
        <input type="text" id="feynmanHintInput" placeholder="如：地球是怎么转的？太阳停在那里吗？">
      </div>
      <div class="btn-row" style="margin-top:16px;">
        <button class="btn btn-primary" onclick="saveFeynman()">保存主题</button>
        <button class="btn btn-secondary" onclick="closeModal('modalFeynman')">取消</button>
      </div>
    </div>
  </div>

  <!-- Modal: 猜猜看 -->
  <div id="modalGuess" class="modal-overlay" style="display:none;" onclick="if(event.target===this)closeModal('modalGuess')">
    <div class="modal-card">
      <div class="modal-title">
        <span>❓ 猜猜看谜题</span>
        <button type="button" onclick="closeModal('modalGuess')" style="border:none; background:none; font-size:18px; cursor:pointer;">&times;</button>
      </div>
      <input type="hidden" id="editGuessId">
      <div class="form-group" style="display:flex; gap:10px;">
        <div style="flex:1;">
          <label class="form-label">谜底名称</label>
          <input type="text" id="guessNameInput" placeholder="大熊猫">
        </div>
        <div style="width:120px;">
          <label class="form-label">分类</label>
          <input type="text" id="guessCatInput" value="动物">
        </div>
      </div>
      <div class="form-group">
        <label class="form-label">线索提示语</label>
        <textarea id="guessHintInput" style="height:60px;" placeholder="黑眼圈，爱吃竹子，是我们的国宝。"></textarea>
      </div>
      <div class="btn-row" style="margin-top:16px;">
        <button class="btn btn-primary" onclick="saveGuess()">保存谜题</button>
        <button class="btn btn-secondary" onclick="closeModal('modalGuess')">取消</button>
      </div>
    </div>
  </div>

  <!-- Modal: 科学辨伪 -->
  <div id="modalFact" class="modal-overlay" style="display:none;" onclick="if(event.target===this)closeModal('modalFact')">
    <div class="modal-card">
      <div class="modal-title">
        <span>🔬 科学常识辨伪</span>
        <button type="button" onclick="closeModal('modalFact')" style="border:none; background:none; font-size:18px; cursor:pointer;">&times;</button>
      </div>
      <input type="hidden" id="editFactId">
      <div class="form-group" style="display:flex; gap:10px;">
        <div style="flex:1;">
          <label class="form-label">关键词1</label>
          <input type="text" id="factKw1Input" placeholder="仙人掌">
        </div>
        <div style="flex:1;">
          <label class="form-label">关键词2</label>
          <input type="text" id="factKw2Input" placeholder="防辐射">
        </div>
      </div>
      <div class="form-group">
        <label class="form-label">是否属实</label>
        <select id="factIsTrueInput" class="form-control" style="width:100%; height:38px; border:1px solid var(--border); border-radius:6px; padding:0 8px;">
          <option value="false">❌ 误区谣言 (不属实)</option>
          <option value="true">✅ 科学事实 (属实)</option>
        </select>
      </div>
      <div class="form-group">
        <label class="form-label">科学解析与说明</label>
        <textarea id="factNoteInput" style="height:60px;" placeholder="仙人掌耐旱，但无法吸收电脑屏幕发射的电磁辐射。"></textarea>
      </div>
      <div class="btn-row" style="margin-top:16px;">
        <button class="btn btn-primary" onclick="saveFact()">保存常识</button>
        <button class="btn btn-secondary" onclick="closeModal('modalFact')">取消</button>
      </div>
    </div>
  </div>

  <!-- Modal: 飞花令 -->
  <div id="modalFeihua" class="modal-overlay" style="display:none;" onclick="if(event.target===this)closeModal('modalFeihua')">
    <div class="modal-card">
      <div class="modal-title">
        <span>🌸 诗词飞花令对仗句</span>
        <button type="button" onclick="closeModal('modalFeihua')" style="border:none; background:none; font-size:18px; cursor:pointer;">&times;</button>
      </div>
      <div class="form-group">
        <label class="form-label">令字</label>
        <input type="text" id="feihuaKeywordInput" placeholder="春">
      </div>
      <div class="form-group">
        <label class="form-label">对仗诗句</label>
        <input type="text" id="feihuaVerseInput" placeholder="春眠不觉晓，处处闻啼鸟。">
      </div>
      <div class="btn-row" style="margin-top:16px;">
        <button class="btn btn-primary" onclick="saveFeihua()">确认添加</button>
        <button class="btn btn-secondary" onclick="closeModal('modalFeihua')">取消</button>
      </div>
    </div>
  </div>

  <!-- Toast Notification -->
  <button class="btn btn-secondary" style="position:fixed;right:158px;bottom:20px;z-index:1500;" type="button" onclick="togglePresetCatalog()">📚 分年级内容</button>
  <section class="ocr-panel" id="presetCatalogPanel" hidden aria-label="分年级预设内容">
    <div style="display:flex;justify-content:space-between;"><strong>分年级预设内容</strong><button type="button" class="btn btn-secondary btn-sm" onclick="togglePresetCatalog()">关闭</button></div>
    <p>年级仅供参考，可手动勾选。导入诗词、成语不会覆盖已有自定义内容；生字词将保存为新的当前听写词单。</p>
    <label>内容 <select id="presetCategory" onchange="loadPresetCatalog()"><option value="words">生字词</option><option value="idioms">成语</option><option value="poems">古诗</option><option value="english">英语生词</option></select></label>
    <label>年级 <select id="presetGrade" onchange="loadPresetCatalog()"><option value="all">全部年级</option><option value="none">不选年级</option><option value="1">一年级</option><option value="2">二年级</option><option value="3">三年级</option><option value="4">四年级</option><option value="5">五年级</option><option value="6">六年级</option></select></label>
    <div style="display:flex;gap:8px;margin:10px 0;"><button class="btn btn-secondary btn-sm" onclick="selectAllPresets(true)">全选</button><button class="btn btn-secondary btn-sm" onclick="selectAllPresets(false)">不选</button></div>
    <div id="presetItems" style="max-height:280px;overflow:auto;"></div>
    <button class="btn btn-primary btn-sm" onclick="importSelectedPresets()">导入所选内容</button>
  </section>
  <button class="btn btn-primary ocr-float" type="button" onclick="toggleTextOcr()">📷 拍照识字</button>
  <section class="ocr-panel" id="textOcrPanel" hidden aria-label="拍照识字">
    <div style="display:flex;justify-content:space-between;align-items:center;"><strong>拍照识字</strong><button type="button" class="btn btn-secondary btn-sm" onclick="toggleTextOcr()">关闭</button></div>
    <p>先选择或拍摄图片，识别后请核对文字，再填入输入框或复制。图片会发送给已配置的视觉模型。</p>
    <label for="textOcrType">识别内容</label><select id="textOcrType"><option value="">普通文字</option><option>作业</option><option>习惯</option><option>生字词</option><option>古诗</option><option>成语</option><option>英语生词</option><option>英语短文</option><option>小学新鲜事</option><option>错题</option><option>费曼主题</option><option>猜猜看</option><option>口算题</option><option>事实核查</option><option>飞花令</option><option>心愿</option><option>亲子留言</option><option>倒计时</option></select><p>古诗、成语等内容请先打开对应的添加表单。识别只填入表单，检查后手动保存。</p>
    <input type="file" id="textOcrFile" accept="image/*" capture="environment">
    <button type="button" class="btn btn-primary btn-sm" onclick="recognizeTextPhoto()">识别照片</button>
    <textarea id="textOcrResult" placeholder="识别结果会显示在这里，可直接修改"></textarea>
    <div style="display:flex;gap:8px;"><button type="button" class="btn btn-secondary btn-sm" onclick="insertTextOcr()">填入刚才选中的输入框</button><button type="button" class="btn btn-secondary btn-sm" onclick="copyTextOcr()">复制文字</button></div>
  </section>
  <div id="toast" class="toast"></div>

    </div> <!-- End .content-body -->
  </main> <!-- End .app-main -->
</div> <!-- End .app-shell -->
<script>
let state = {
  currentTab: 'tasks',
  arithGrade: 2,
  arithCount: 10,
  arithOp: 'all',
  arithDirty: false,
  arithRevision: 0,
  focusMinutes: 25,
  focusStyle: 'spark',
  focusActive: false,
  focusPaused: false,
  focusRemainingS: 0,
  focusTotalS: 1500,
  curPoemTitle: '',
  curReadingId: '',
  curDay: '1',
  timetableAll: {},
  timetableDirty: false,
  timetableRevision: 0,
  curIdiom: {},
  allIdioms: [],
  artList: []
};

function formatMMSS(sec) {
  const s = Math.max(0, Math.floor(sec || 0));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return (m < 10 ? '0' + m : '' + m) + ':' + (r < 10 ? '0' + r : '' + r);
}

function updateFocusUI(fs, todayMin) {
  if (todayMin !== undefined) {
    const tEl = document.getElementById('focusTodayMinText');
    if (tEl) tEl.textContent = todayMin;
  }
  if (!fs) return;
  state.focusActive = !!fs.active;
  state.focusPaused = !!fs.paused;
  state.focusRemainingS = fs.remaining_s || 0;
  state.focusTotalS = fs.total_s || (state.focusMinutes * 60);

  const lbl = document.getElementById('focusStatusLabel');
  const timerEl = document.getElementById('focusTimerDisplay');
  const ctrlEl = document.getElementById('focusActiveControls');
  const pauseBtn = document.getElementById('btnFocusPauseResume');
  const progEl = document.getElementById('focusProgressBar');

  if (state.focusActive) {
    const styleName = fs.style === 'candle' ? '🕯️ 静谧蜡烛专注中' : '🔥 火花引线专注中';
    if (lbl) lbl.textContent = state.focusPaused ? (styleName + ' (已暂停)') : (styleName + ' · Note 4 正在倒计时');
    if (timerEl) timerEl.textContent = formatMMSS(state.focusRemainingS);
    if (ctrlEl) ctrlEl.style.display = 'flex';
    if (pauseBtn) pauseBtn.textContent = state.focusPaused ? '▶️ 继续专注' : '⏸ 暂停专注';
    const pct = state.focusTotalS > 0 ? Math.min(100, Math.max(0, Math.round((1 - state.focusRemainingS / state.focusTotalS) * 100))) : 0;
    if (progEl) progEl.style.width = pct + '%';
  } else {
    if (lbl) lbl.textContent = '🔥 花火 / 蜡烛番茄钟 · 100px 冬青黑体大字';
    if (timerEl) timerEl.textContent = formatMMSS(state.focusMinutes * 60);
    if (ctrlEl) ctrlEl.style.display = 'none';
    if (progEl) progEl.style.width = '0%';
  }
}

function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2500);
}



const TAB_CATEGORY_MAP = {
  tasks: 'daily', focus: 'daily', stars: 'daily', habits: 'daily', message: 'daily',
  dictation: 'study', poem: 'study', arith: 'study', vocab: 'study', reading: 'study', mistakes: 'study', idiom: 'study', thinking: 'study', timetable: 'study',
  news: 'vision', screensaver: 'vision',
  features: 'system', settings: 'system', menu: 'system', report: 'system'
};

const TAB_NAME_MAP = {
  tasks: { cat: '今日伴学', title: '📋 今日作业', sub: '微信群作业智能解析、一键排版并秒级推送到 Note 4 墨水屏' },
  focus: { cat: '今日伴学', title: '🔥 番茄专注', sub: '远程控制 Note 4 墨水屏专注倒计时与火花/蜡烛动画' },
  stars: { cat: '今日伴学', title: '⭐ 星星存折', sub: '学习奖励积分与心愿兑换储蓄中心' },
  habits: { cat: '今日伴学', title: '🌟 习惯打卡', sub: '每日好习惯打卡培养与星星激励' },
  message: { cat: '今日伴学', title: '💌 亲子留言', sub: '给宝贝写鼓励的话，即时展现在墨水屏' },
  dictation: { cat: '学科进阶', title: '✍️ 生字听写', sub: '同步课本生词题库，支持手机拍照智能批改' },
  poem: { cat: '学科进阶', title: '📜 课内古诗', sub: '小学必背古诗词点播、背诵与家长自定义诗库' },
  arith: { cat: '学科进阶', title: '🔢 口算闯关', sub: '加减乘除每日智能出题与家长靶向题库' },
  vocab: { cat: '学科进阶', title: '🔤 英语生词', sub: '课标核心词汇库、音标释义与生词卡编辑' },
  reading: { cat: '学科进阶', title: '📖 英语短文', sub: '分级阅读与少儿双语短文朗读打卡' },
  mistakes: { cat: '学科进阶', title: '📕 错题专练', sub: '薄弱知识点智能归纳、手动错题录入与消灭' },
  idiom: { cat: '学科进阶', title: '📜 每日成语', sub: '典故积累、晨读与家长自定义成语库' },
  thinking: { cat: '学科进阶', title: '💡 拓展素养题库', sub: '费曼小老师、趣味科普猜猜看、事实核查与诗词飞花令' },
  timetable: { cat: '学科进阶', title: '📅 桌面课表', sub: '周一至周五课程备忘一览与可视化编辑' },
  news: { cat: '视野长廊', title: '📰 小学新鲜事', sub: '专属少儿资讯、赛事活动与地区定制' },
  screensaver: { cat: '视野长廊', title: '🎨 名画与屏保', sub: '14幅 16灰阶名画与待机倒计时' },
  features: { cat: '智能管理', title: '🛡️ 功能权限与开关', sub: 'Note 4 墨水屏 19 大功能自由开启/停用与家长权限控制' },
  settings: { cat: '智能管理', title: '🤖 学伴与AI设置', sub: '小龙虾人设定制与大模型引擎设置' },
  menu: { cat: '智能管理', title: '📱 菜单排序', sub: 'Note 4 物理按键导航菜单自由定制与点击统计' },
  report: { cat: '智能管理', title: '📊 学情报表', sub: '专注时长、任务与生词学情汇总' }
};


function onMobileSelectCategory(cat) {
  document.querySelectorAll('.cat-pill').forEach(p => {
    if (p.getAttribute('data-cat') === cat) p.classList.add('active');
    else p.classList.remove('active');
  });
  document.querySelectorAll('.mobile-subtabs-group').forEach(g => {
    if (g.getAttribute('data-cat') === cat) g.style.display = 'flex';
    else g.style.display = 'none';
  });
  // Auto-switch to first tab in category
  const firstBtn = document.querySelector(`.mobile-subtabs-group[data-cat="${cat}"] .tab-btn`);
  if (firstBtn) {
    const tabName = firstBtn.getAttribute('data-tab');
    if (tabName) switchTab(tabName);
  }
}

function switchCategory(cat) {
  document.querySelectorAll('.cat-pill').forEach(p => {
    if (p.getAttribute('data-cat') === cat) p.classList.add('active');
    else p.classList.remove('active');
  });
  document.querySelectorAll('.mobile-subtabs-group').forEach(g => {
    if (g.getAttribute('data-cat') === cat) g.style.display = 'flex';
    else g.style.display = 'none';
  });
}

function switchTab(name) {
  state.currentTab = name;
  const meta = TAB_NAME_MAP[name];
  if (meta) {
    const titleEl = document.getElementById('topPageTitle');
    const subEl = document.getElementById('topPageSub');
    const catEl = document.getElementById('topPageCat');
    if (titleEl) titleEl.textContent = meta.title;
    if (subEl) subEl.textContent = meta.sub;
    if (catEl) catEl.textContent = meta.cat + ' / ';
    const cat = TAB_CATEGORY_MAP[name];
    if (cat) switchCategory(cat);
  }

  state.currentTab = name;
  document.querySelectorAll('.tab-btn').forEach(b => {
    if (b.getAttribute('data-tab') === name) {
      b.classList.add('active');
      if (b.scrollIntoView) {
        b.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' });
      }
    } else {
      b.classList.remove('active');
    }
  });
  document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
  const target = document.getElementById('tab-' + name);
  if (target) target.classList.add('active');
}

async function loadOverview() {
  const arithRevision = state.arithRevision;
  const timetableRevision = state.timetableRevision;
  try {
    const res = await fetch('/api/pocketclaw/mp/overview');
    const data = await res.json();
    if (!data.ok) return;

    // Header badges
    const bOnline = document.getElementById('badgeOnline');
    if (data.online) {
      bOnline.className = 'badge badge-online';
      bOnline.textContent = '🟢 设备在线';
    } else {
      bOnline.className = 'badge badge-offline';
      bOnline.textContent = '⚪ 待命中';
    }
    const bBat = document.getElementById('badgeBattery');
    bBat.textContent = '🔋 ' + (data.battery !== null ? data.battery + '%' : '已接电');
    const bMode = document.getElementById('badgeMode');
    if (bMode && data.current_mode_name) {
      if (data.focus_status && data.focus_status.active) {
        bMode.textContent = '🔥 专注 ' + formatMMSS(data.focus_status.remaining_s);
        bMode.style.background = '#ffedd5';
        bMode.style.color = '#c2410c';
      } else {
        bMode.textContent = '📺 ' + data.current_mode_name;
        bMode.style.background = '#eff6ff';
        bMode.style.color = '#1d4ed8';
      }
    }

    // Focus status
    if (data.focus_status) {
      updateFocusUI(data.focus_status, data.focus_today_min);
    }

    // Art gallery
    if (data.art_list && data.art_list.length > 0 && state.artList.length === 0) {
      state.artList = data.art_list;
      renderArtGallery(data.art_list);
    }

    // Arith config sync
    if (data.arith_config && !state.arithDirty && arithRevision === state.arithRevision) {
      if (data.arith_config.grade) selectArithPill('grade', data.arith_config.grade, true);
      if (data.arith_config.count) selectArithPill('count', data.arith_config.count, true);
      if (data.arith_config.op_type) selectArithPill('op', data.arith_config.op_type, true);
    }

    // Tasks tab
    document.getElementById('taskStats').textContent = data.task_stats.done + '/' + data.task_stats.total;
    // Sidebar badges
    const sideTasks = document.getElementById('sideBadgeTasks');
    if (sideTasks && data.task_stats) sideTasks.textContent = data.task_stats.done + '/' + data.task_stats.total;
    const sideStars = document.getElementById('sideBadgeStars');
    if (sideStars && data.star_bank) sideStars.textContent = '★ ' + (data.star_bank.balance || 0);
    const sideHabits = document.getElementById('sideBadgeHabits');
    if (sideHabits && data.habit_stats) sideHabits.textContent = data.habit_stats.done + '/' + data.habit_stats.total;
    const sideVocab = document.getElementById('sideBadgeVocab');
    if (sideVocab) sideVocab.textContent = (data.vocab_count || 0) + '词';

    renderTasks(data.tasks);

    // Habits tab
    if (data.habits) {
      document.getElementById('habitStats').textContent = data.habit_stats.done + '/' + data.habit_stats.total;
      document.getElementById('habitStarsBadge').textContent = '⭐ ' + data.habit_stats.stars + ' 颗星';
      renderHabits(data.habits);
    }

    // Stars tab
    if (data.star_bank) {
      renderStars(data.star_bank);
    }

    // Timetable tab
    if (data.timetable_all && !state.timetableDirty && timetableRevision === state.timetableRevision && !document.getElementById('timetableWeekBody').contains(document.activeElement)) {
      state.timetableAll = data.timetable_all;
      renderTimetableWeek();
    }

    // Mistakes tab
    if (data.mistakes) {
      document.getElementById('mistakeCount').textContent = data.mistakes_count;
      renderMistakes(data.mistakes);
    }

    // Idiom tab
    if (data.daily_idiom) {
      state.curIdiom = data.daily_idiom;
      renderDailyIdiomCard(data.daily_idiom);
    }

    // Dictation tab
    document.getElementById('dictCount').textContent = data.dictation_words.length;
    renderDictation(data.dictation_words);

    // Poem badge
    state.curPoemTitle = data.poem.title;
    document.getElementById('curPoemBadge').textContent = '当前背诵：' + data.poem.title;

    // Vocab count & words
    document.getElementById('vocabCount').textContent = data.vocab_count;
    renderVocab(data.vocab_words);

    // Reading badge
    state.curReadingId = data.reading.id;
    document.getElementById('curReadingBadge').textContent = '当前阅读：《' + data.reading.title + '》';

    // Report
    document.getElementById('repTaskRate').textContent =
      data.task_stats.total > 0 ? Math.round(data.task_stats.done / data.task_stats.total * 100) + '%' : '100%';
    document.getElementById('repFocusMin').textContent = data.focus_today_min + 'm';
    document.getElementById('repVocabCnt').textContent = data.vocab_count;
  } catch (e) {
    console.error('loadOverview error:', e);
  }
}


function renderTasks(tasks) {
  state.lastTasks = tasks;
  const listEl = document.getElementById('taskList');
  if (!tasks || tasks.length === 0) {
    listEl.innerHTML = '<div style="font-size:13px; color:var(--muted); text-align:center; padding:32px 16px; background:#fafafa; border-radius:12px; border:1px dashed var(--border);">今日暂无作业，可在左侧一键粘贴群通知。</div>';
    return;
  }
  listEl.innerHTML = tasks.map(t => `
    <div class="task-card ${t.done ? 'done' : ''}">
      <input type="checkbox" ${t.done ? 'checked' : ''} onchange="toggleTask('${t.id}')">
      <div class="task-content">
        <div class="task-text">${t.text}</div>
        <div class="task-meta">
          <span class="badge ${t.subject === '数学' ? 'badge-math' : (t.subject === '语文' ? 'badge-chinese' : 'badge-english')}">${t.subject || '其他'}</span>
          <span>${t.done ? '已完成' : '待办'}</span>
        </div>
      </div>
      <div style="display:flex; gap:6px; flex-shrink:0;">
        <button class="btn btn-secondary btn-sm" onclick="editTaskModal('${t.id}')" title="修改作业" style="padding:4px 8px;">✏️</button>
        <button class="btn btn-secondary btn-sm" onclick="deleteTask('${t.id}')" title="删除" style="color:#ef4444; border-color:#fee2e2; padding:4px 8px;">✕</button>
      </div>
    </div>
  `).join('');
}



function renderHabits(habits) {
  state.lastHabits = habits;
  const listEl = document.getElementById('habitList');
  if (!habits || habits.length === 0) {
    listEl.innerHTML = '<div style="font-size:13px; color:var(--muted); text-align:center; padding:32px 16px; background:#fafafa; border-radius:12px; border:1px dashed var(--border);">暂无习惯打卡项，可在左侧添加。</div>';
    return;
  }
  listEl.innerHTML = habits.map(h => `
    <div class="habit-card ${h.today_checked ? 'checked' : ''}">
      <div class="habit-icon">${h.icon || '⭐'}</div>
      <div class="habit-body">
        <div class="habit-name">${h.name}</div>
        <div class="habit-streak">已连续打卡 <span style="font-weight:700; color:var(--primary);">${h.streak || 0}</span> 天 · 目标 ${h.target_days || 21} 天</div>
      </div>
      <div style="display:flex; gap:6px; align-items:center;">
        <button class="btn btn-secondary btn-sm" onclick="editHabitModal('${h.id}')" title="修改习惯">✏️</button>
        <button class="btn btn-secondary btn-sm" onclick="deleteHabit('${h.id}')" style="color:#ef4444;" title="删除习惯">🗑️</button>
        <button class="btn btn-sm ${h.today_checked ? 'btn-success' : 'btn-primary'}" onclick="toggleHabit('${h.id}')">
          ${h.today_checked ? '✓ 今日已打卡' : '🌟 家长代打卡'}
        </button>
      </div>
    </div>
  `).join('');
}


async function recognizeTimetablePhoto() {
  const file=document.getElementById('weekPhotoInput').files[0], revision=state.timetableRevision;
  if(!file||file.size>8*1024*1024){showToast('请选择不超过 8 MB 的照片');return}
  try{const image=await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(r.result);r.onerror=reject;r.readAsDataURL(file)});showToast('正在识别课表');
    const response=await fetch('/api/pocketclaw/mp/ocr_text',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({image,target:'课表'})});const result=await response.json();if(!response.ok||!result.ok)throw Error(result.detail||'识别失败');
    if(revision!==state.timetableRevision){showToast('课表已修改，请重新识别');return}
    for(const day of result.fields.days||[])day.courses.forEach((course,i)=>{const el=document.getElementById('week_'+day.day+'_'+i);if(el&&course.trim()){el.value=course.trim();el.dispatchEvent(new Event('input',{bubbles:true}))}});
    showToast('请核对课表后保存');
  }catch(e){showToast(e.message||'识别失败')}
}

function renderTimetableWeek() {
  const body = document.getElementById('timetableWeekBody');
  document.getElementById('saveTimetableWeekButton').disabled = false;
  body.replaceChildren();
  for (let row = 0; row < 7; row++) {
    const tr = document.createElement('tr');
    const th = document.createElement('th');
    th.scope = 'row'; th.textContent = row === 6 ? '备忘' : '第 ' + (row + 1) + ' 节';
    th.style.padding = '8px'; tr.appendChild(th);
    for (let day = 1; day <= 5; day++) {
      const td = document.createElement('td'); td.style.padding = '4px';
      const input = document.createElement('input');
      input.style.cssText = 'width:100%;box-sizing:border-box;padding:9px 10px;border:1px solid #e2e8f0;border-radius:8px;font-size:14px;background:#f8fafc;';
      const data = state.timetableAll[String(day)] || {};
      input.value = row === 6 ? (data.note || '') : ((data.courses || [])[row] || '');
      input.placeholder = row === 6 ? '备忘' : '课程';
      input.setAttribute('aria-label', '周' + ['一','二','三','四','五'][day-1] + (row === 6 ? '备忘' : '第' + (row+1) + '节'));
      input.id = 'week_' + day + '_' + row;
      input.addEventListener('input', () => {state.timetableDirty = true; state.timetableRevision++;});
      td.appendChild(input); tr.appendChild(td);
    }
    body.appendChild(tr);
  }
}

function renderMistakes(mistakes) {
  state.lastMistakes = mistakes;
  const listEl = document.getElementById('mistakeList');
  if (!mistakes || mistakes.length === 0) {
    listEl.innerHTML = '<div style="font-size:13px; color:var(--muted); text-align:center; padding:32px 16px; background:#fafafa; border-radius:12px; border:1px dashed var(--border);">错题本空空如也，宝贝太棒啦！</div>';
    return;
  }
  listEl.innerHTML = mistakes.map(m => `
    <div class="card" style="margin-bottom:12px; border-left:4px solid #ef4444;">
      <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:8px;">
        <span class="badge ${m.subject === '数学' ? 'badge-math' : (m.subject === '语文' ? 'badge-chinese' : 'badge-english')}">${m.subject || '学科'}</span>
        <div style="display:flex; gap:6px;">
          <button class="btn btn-secondary btn-sm" onclick="editMistakeModal('${m.id}')">✏️ 修改</button>
          <button class="btn btn-secondary btn-sm" onclick="eliminateMistake('${m.id}')" style="color:#10b981; border-color:#d1fae5; background:#fff;">✓ 消灭错题</button>
        </div>
      </div>
      <div style="font-size:14px; font-weight:600; color:var(--text); margin-bottom:8px; line-height:1.5;">${m.question}</div>
      ${m.answer ? `<div style="font-size:13px; color:#475569; background:#f8fafc; padding:8px 12px; border-radius:6px; margin-bottom:4px;"><b>💡 正确解析:</b> ${m.answer}</div>` : ''}
    </div>
  `).join('');
}


function renderDailyIdiomCard(idiom) {
  document.getElementById('curIdiomWord').textContent = idiom.idiom || '';
  document.getElementById('curIdiomPinyin').textContent = idiom.pinyin ? '(' + idiom.pinyin + ')' : '';
  document.getElementById('curIdiomMeaning').textContent = '【释义】' + (idiom.meaning || '');
  document.getElementById('curIdiomSource').textContent = idiom.source ? '【出处】' + idiom.source : '';
  document.getElementById('curIdiomExample').textContent = idiom.example ? '【造句】' + idiom.example : '';
}

async function loadIdioms() {
  const res = await fetch('/api/pocketclaw/mp/idiom');
  const data = await res.json();
  if (!data.ok) return;
  state.allIdioms = data.all;
  const list = document.getElementById('idiomLibraryList');
  list.innerHTML = data.all.map(item => `
    <div class="grid-card ${item.idiom === state.curIdiom.idiom ? 'active' : ''}" onclick='selectIdiom(${JSON.stringify(item)})'>
      <div style="display:flex; justify-content:space-between; align-items:center;">
        <div>
          <span class="grid-card-title">${item.idiom}</span>
          <span style="font-size:11px; color:var(--muted); margin-left:6px;">(${item.pinyin})</span>
        </div>
        <button class="btn ${item.idiom === state.curIdiom.idiom ? 'btn-primary' : 'btn-secondary'} btn-sm">
          ${item.idiom === state.curIdiom.idiom ? '✓ 推荐中' : '设为今日成语'}
        </button>
      </div>
      <div class="grid-card-desc" style="margin-top:4px;">${item.meaning}</div>
    </div>
  `).join('');
}

function renderDictation(words) {
  const p = document.getElementById('dictationPreview');
  if (!words || words.length === 0) {
    p.textContent = '暂无自定义字词';
    return;
  }
  p.innerHTML = words.map((w, i) => `<span style="display:inline-block; margin-right:12px; background:#f1f5f9; padding:2px 8px; border-radius:6px; margin-bottom:4px;">${i+1}. ${w[0]}</span>`).join('');
}


let vocabSearchQuery = '';
function filterVocab(q) {
  vocabSearchQuery = (q || '').trim().toLowerCase();
  renderVocab(state.lastVocabWords || []);
}

function renderVocab(words) {
  state.lastVocabWords = words || [];
  const el = document.getElementById('vocabList');
  if (!words || words.length === 0) {
    el.innerHTML = '<div style="font-size:13px; color:var(--muted); text-align:center; padding:32px 16px; background:#fafafa; border-radius:12px; border:1px dashed var(--border);">生词本为空，可在左侧添加新词。</div>';
    return;
  }
  let filtered = words;
  if (vocabSearchQuery) {
    filtered = words.filter(w => (w.word && w.word.toLowerCase().includes(vocabSearchQuery)) || (w.meaning && w.meaning.includes(vocabSearchQuery)));
  }
  if (filtered.length === 0) {
    el.innerHTML = `<div style="font-size:13px; color:var(--muted); text-align:center; padding:24px 16px;">未找到匹配 “${vocabSearchQuery}” 的生词</div>`;
    return;
  }
  el.innerHTML = filtered.map(w => `
    <div class="vocab-card">
      <div style="flex:1; min-width:0;">
        <div class="vocab-word-title">
          <span>${w.word}</span>
          <span class="vocab-meaning">${w.meaning}</span>
        </div>
        ${w.example ? `
          <div class="vocab-example">
            <span>例: ${w.example}</span>
            ${w.example_cn ? `<span style="color:var(--muted-light); margin-left:6px;">(${w.example_cn})</span>` : ''}
          </div>
        ` : ''}
      </div>
      <div style="display:flex; gap:6px; flex-shrink:0;">
        <button class="btn btn-secondary btn-sm" onclick="editVocabModal('${w.id}')" title="修改生词" style="padding:4px 8px;">✏️</button>
        <button class="btn btn-secondary btn-sm" onclick="deleteVocab('${w.id}')" title="从生词本删除" style="color:#ef4444; border-color:#fee2e2; background:#fff; padding:4px 8px;">✕</button>
      </div>
    </div>
  `).join('');
}



async function loadPoems() {
  const res = await fetch('/api/pocketclaw/mp/poems');
  const data = await res.json();
  if (!data.ok) return;
  state.poems = data.poems;
  const grid = document.getElementById('poemGrid');
  grid.innerHTML = data.poems.map(p => `
    <div class="grid-card ${p.title === state.curPoemTitle ? 'active' : ''}" onclick="setPoemTarget('${p.title}')">
      <div>
        <div class="grid-card-title">${p.title} <span style="font-size:11px; font-weight:normal; color:var(--muted);">${p.author}〔${p.dynasty}〕</span></div>
        <div class="grid-card-desc">${p.lines[0]} ${p.lines[1] || ''}</div>
      </div>
      <button class="btn ${p.title === state.curPoemTitle ? 'btn-primary' : 'btn-secondary'} btn-sm" style="margin-top:8px;">
        ${p.title === state.curPoemTitle ? '✓ 考核中' : '设为背诵'}
      </button>
    </div>
  `).join('');
}

async function loadReadingArticles() {
  const res = await fetch('/api/pocketclaw/mp/reading');
  const data = await res.json();
  if (!data.ok) return;
  const list = document.getElementById('readingArticlesList');
  list.innerHTML = data.articles.map(a => `
    <div class="grid-card ${a.id === state.curReadingId ? 'active' : ''}" style="margin-bottom:8px;">
      <div style="display:flex; justify-content:space-between; align-items:center;">
        <div>
          <span class="grid-card-title">《${a.title}》</span>
          <span class="badge" style="background:#ede9fe; color:var(--tag-english); margin-left:6px;">${a.level}</span>
        </div>
        <button class="btn ${a.id === state.curReadingId ? 'btn-primary' : 'btn-secondary'} btn-sm" onclick="setReadingTarget('${a.id}')">
          ${a.id === state.curReadingId ? '✓ 阅读中' : '设为当前阅读'}
        </button>
      </div>
      <div style="font-size:12px; color:var(--muted); margin-top:6px; line-height:1.6;">
        ${a.sentences.slice(0, 2).map((s, i) => `<div>${i+1}. ${s[0]} (${s[1]})</div>`).join('')}
        ${a.sentences.length > 2 ? `<div style="color:var(--primary); font-size:11px;">共 ${a.sentences.length} 句，点击设为阅读即刻在 Note 4 上逐句学习...</div>` : ''}
      </div>
    </div>
  `).join('');
}

async function loadReport() {
  const res = await fetch('/api/pocketclaw/mp/report');
  const data = await res.json();
  if (!data.ok) return;
  const grid = document.getElementById('badgesGrid');
  grid.innerHTML = data.badges.map(b => `
    <div style="border:1px solid ${b.unlocked ? '#bbf7d0' : 'var(--border)'}; background:${b.unlocked ? '#f0fdf4' : '#f8fafc'}; border-radius:10px; padding:10px; text-align:center;">
      <div style="font-size:24px;">${b.icon}</div>
      <div style="font-size:13px; font-weight:700; color:${b.unlocked ? 'var(--text)' : 'var(--muted)'}; margin:4px 0 2px 0;">${b.title}</div>
      <div style="font-size:11px; color:var(--muted);">${b.desc}</div>
      <div style="font-size:10px; font-weight:600; margin-top:4px; color:${b.unlocked ? 'var(--success)' : 'var(--muted)'};">
        ${b.unlocked ? '★ 已点亮' : '○ 练习中'}
      </div>
    </div>
  `).join('');
}

// Actions
async function submitQuickInput(clearExisting) {
  const raw = document.getElementById('rawTasksInput').value;
  if (!raw.trim()) { showToast('请先输入作业内容'); return; }
  const res = await fetch('/api/pocketclaw/parent/quick_input', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ raw_tasks: raw, clear_existing: clearExisting })
  });
  const data = await res.json();
  if (data.ok) {
    document.getElementById('rawTasksInput').value = '';
    showToast('解析成功，已实时推送到 Note 4 墨水屏！');
    loadOverview();
  }
}

async function addSingleTask() {
  const inp = document.getElementById('singleTaskInput');
  const text = inp.value.trim();
  if (!text) return;
  await fetch('/api/pocketclaw/mp/tasks', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text: text })
  });
  inp.value = '';
  showToast('作业已添加并同步到设备！');
  loadOverview();
}

async function toggleTask(id) {
  const item = document.querySelector(`#taskList .task-item[data-id="${id}"]`);
  if (item) {
    const cb = item.querySelector('input[type="checkbox"]');
    const txt = item.querySelector('.task-text');
    if (cb) cb.checked = !cb.checked;
    if (txt) txt.classList.toggle('done');
  }
  try {
    const res = await fetch('/api/pocketclaw/mp/tasks/' + id + '/toggle', { method: 'POST' });
    const data = await res.json();
    if (!data.ok) throw new Error('toggle failed');
    loadOverview();
  } catch (e) {
    showToast('网络请求失败，请重试');
    loadOverview();
  }
}

async function deleteTask(id) {
  await fetch('/api/pocketclaw/mp/tasks/' + id, { method: 'DELETE' });
  loadOverview();
}

async function clearDoneTasks() {
  await fetch('/api/pocketclaw/mp/tasks/clear_done', { method: 'DELETE' });
  showToast('已清空打勾任务');
  loadOverview();
}

async function addHabit() {
  const inp = document.getElementById('newHabitInput');
  const icon = document.getElementById('newHabitIcon').value;
  const name = inp.value.trim();
  if (!name) return;
  await fetch('/api/pocketclaw/mp/habits', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: name, icon: icon })
  });
  inp.value = '';
  showToast('好习惯已添加！');
  loadOverview();
}

async function toggleHabit(id) {
  const item = document.querySelector(`#habitList .task-item[data-id="${id}"]`);
  if (item) {
    const cb = item.querySelector('input[type="checkbox"]');
    const txt = item.querySelector('.task-text');
    if (cb) cb.checked = !cb.checked;
    if (txt) txt.classList.toggle('done');
  }
  try {
    const res = await fetch('/api/pocketclaw/mp/habits/' + id + '/toggle', { method: 'POST' });
    const data = await res.json();
    if (!data.ok) throw new Error('toggle failed');
    loadOverview();
  } catch (e) {
    showToast('网络请求失败，请重试');
    loadOverview();
  }
}

async function deleteHabit(id) {
  await fetch('/api/pocketclaw/mp/habits/' + id, { method: 'DELETE' });
  loadOverview();
}

function renderStars(bank) {
  if (!bank) return;
  state.starBank = bank;
  const balEl = document.getElementById('starBalanceDisplay');
  if (balEl) balEl.textContent = bank.balance + ' ⭐';
  const earnEl = document.getElementById('starTotalEarned');
  if (earnEl) earnEl.textContent = bank.total_earned;
  const spentEl = document.getElementById('starTotalSpent');
  if (spentEl) spentEl.textContent = bank.spent;

  const grid = document.getElementById('wishCardsGrid');
  if (!grid) return;
  grid.innerHTML = '';
  (bank.wishes || []).forEach(w => {
    const card = document.createElement('div');
    const canRedeem = (bank.balance >= w.stars_needed && !w.redeemed);
    const progress = Math.min(100, Math.round((bank.balance / w.stars_needed) * 100));
    card.style = 'border:1px solid var(--border); border-radius:10px; padding:10px; background:' + (w.redeemed ? '#f8fafc' : '#ffffff') + '; display:flex; flex-direction:column; justify-content:space-between; gap:6px; box-shadow:0 1px 2px rgba(0,0,0,0.02);';
    card.innerHTML = `
      <div style="display:flex; justify-content:space-between; align-items:flex-start;">
        <span style="font-size:22px;">${w.icon || '🎁'}</span>
        <button onclick="deleteWish('${w.id}')" style="background:none; border:none; color:#94a3b8; font-size:14px; cursor:pointer;" title="删除心愿">✕</button>
      </div>
      <div style="font-size:13px; font-weight:700; color:var(--text);">${w.title}</div>
      <div style="font-size:11px; color:#b45309; font-weight:600;">需 ${w.stars_needed} 颗星星</div>
      ${w.redeemed ? `
        <div style="font-size:11px; color:var(--success); font-weight:700; padding:4px 0; text-align:center; background:#dcfce7; border-radius:6px;">✓ 已成功兑换</div>
      ` : `
        <div style="background:#f1f5f9; border-radius:4px; height:6px; overflow:hidden; margin:2px 0;">
          <div style="background:#f59e0b; width:${progress}%; height:100%;"></div>
        </div>
        <button class="btn btn-sm ${canRedeem ? 'btn-primary' : 'btn-secondary'}" style="width:100%; font-size:11px; padding:6px 0;" onclick="redeemWish('${w.id}')" ${canRedeem ? '' : 'disabled'}>
          ${canRedeem ? '✨ 帮孩子兑换' : '还差 ' + (w.stars_needed - bank.balance) + ' 颗星'}
        </button>
      `}
    `;
    grid.appendChild(card);
  });

  const hList = document.getElementById('starHistoryList');
  if (!hList) return;
  hList.innerHTML = '';
  const hist = (bank.history || []).slice().reverse();
  if (hist.length === 0) {
    hList.innerHTML = '<div style="font-size:12px; color:var(--muted); text-align:center; padding:10px;">暂无明细</div>';
  } else {
    hist.forEach(h => {
      const row = document.createElement('div');
      const isPlus = h.delta > 0;
      const tStr = new Date(h.ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      row.style = 'display:flex; justify-content:space-between; align-items:center; padding:6px 8px; border-radius:6px; background:#f8fafc; font-size:12px;';
      row.innerHTML = `
        <div style="display:flex; gap:8px; align-items:center;">
          <span style="color:var(--muted); font-size:11px;">${tStr}</span>
          <span style="font-weight:600; color:var(--text);">${h.reason || '自律奖励'}</span>
        </div>
        <span style="font-weight:700; color:${isPlus ? '#16a34a' : '#dc2626'};">${isPlus ? '+' : ''}${h.delta} ⭐</span>
      `;
      hList.appendChild(row);
    });
  }
}

async function redeemWish(wishId) {
  if (!confirm('确定要帮孩子兑换该心愿吗？兑换将扣除对应星星，并向 Note 4 墨水屏发送表扬便签！')) return;
  const res = await fetch('/api/pocketclaw/mp/stars/wishes/' + wishId + '/redeem', { method: 'POST' });
  const data = await res.json();
  if (data.ok) {
    showToast('兑换成功！已向 Note 4 墨水屏推送表扬便签！');
    loadOverview();
  } else {
    showToast(data.error || '兑换失败');
  }
}

async function deleteWish(wishId) {
  if (!confirm('确定要删除该心愿吗？')) return;
  await fetch('/api/pocketclaw/mp/stars/wishes/' + wishId, { method: 'DELETE' });
  showToast('心愿已删除');
  loadOverview();
}

function openAddStarsModal() {
  document.getElementById('modalAddStars').classList.add('show');
}
function closeAddStarsModal() {
  document.getElementById('modalAddStars').classList.remove('show');
}
function selectStarCount(n, btn) {
  document.getElementById('inputStarCount').value = n;
  btn.parentElement.querySelectorAll('.pill-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
}
function selectStarReason(r, btn) {
  document.getElementById('inputStarReason').value = r;
  btn.parentElement.querySelectorAll('.pill-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
}
async function confirmAddStars() {
  const count = parseInt(document.getElementById('inputStarCount').value);
  if (isNaN(count) || count <= 0) {
    showToast('请输入有效的星星数量');
    return;
  }
  const reason = document.getElementById('inputStarReason').value.trim() || '认真自律表扬';
  try {
    const res = await fetch('/api/pocketclaw/mp/stars/add', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ count: count, reason: reason })
    });
    const data = await res.json();
    if (data.ok) {
      closeAddStarsModal();
      showToast('已奖励 ' + count + ' 颗星星并即时推送到墨水屏！');
      loadOverview();
    } else {
      showToast('奖励失败: ' + (data.error || '未知错误'));
    }
  } catch (e) {
    showToast('网络请求失败，请重试');
  }
}

function openAddWishModal() {
  document.getElementById('modalAddWish').classList.add('show');
}
function closeAddWishModal() {
  document.getElementById('modalAddWish').classList.remove('show');
}
function selectWishStars(n, btn) {
  document.getElementById('inputWishStars').value = n;
  btn.parentElement.querySelectorAll('.pill-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
}
function selectWishEmoji(e, btn) {
  document.getElementById('inputWishIcon').value = e;
  btn.parentElement.querySelectorAll('.pill-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
}
async function confirmAddWish() {
  const title = document.getElementById('inputWishTitle').value.trim();
  if (!title) {
    showToast('请输入心愿名称');
    return;
  }
  const stars = parseInt(document.getElementById('inputWishStars').value) || 20;
  const icon = document.getElementById('inputWishIcon').value || '🎁';
  try {
    const res = await fetch('/api/pocketclaw/mp/stars/wishes', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: title, stars_needed: stars, icon: icon })
    });
    const data = await res.json();
    if (data.ok) {
      closeAddWishModal();
      document.getElementById('inputWishTitle').value = '';
      showToast('心愿奖品已成功上架！');
      loadOverview();
    } else {
      showToast('上架失败: ' + (data.error || '未知错误'));
    }
  } catch (e) {
    showToast('网络请求失败，请重试');
  }
}

async function saveTimetableWeek() {
  const revision = state.timetableRevision;
  const all = {...state.timetableAll};
  const names = ['周一','周二','周三','周四','周五'];
  for (let day = 1; day <= 5; day++) {
    const existing = all[String(day)] || {};
    const courses = Array.isArray(existing.courses) ? [...existing.courses] : [];
    for (let row = 0; row < 6; row++) courses[row] = document.getElementById('week_' + day + '_' + row).value.trim();
    all[String(day)] = {...existing, day_name: existing.day_name || names[day-1], courses, note: document.getElementById('week_' + day + '_6').value.trim()};
  }
  try {
    const response = await fetch('/api/pocketclaw/mp/timetable', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({timetable:all})});
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error('保存失败');
    if (state.timetableRevision === revision) {state.timetableAll = result.timetable; state.timetableDirty = false; state.timetableRevision++;}
    showToast('整周课表已保存');
  } catch (_) {showToast('课表保存失败，请重试');}
}

async function eliminateMistake(id) {
  await fetch('/api/pocketclaw/mp/mistakes/' + id, { method: 'DELETE' });
  showToast('错题已成功消灭！');
  loadOverview();
}

async function clearAllMistakes() {
  await fetch('/api/pocketclaw/mp/mistakes', { method: 'DELETE' });
  showToast('错题本已全部清空！');
  loadOverview();
}

async function selectIdiom(item) {
  await fetch('/api/pocketclaw/mp/idiom/target', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(item)
  });
  state.curIdiom = item;
  showToast('已设为今日成语：《' + item.idiom + '》并同步墨水屏！');
  loadOverview();
  loadIdioms();
}

function pushIdiomToDevice() {
  if (state.curIdiom && state.curIdiom.idiom) {
    selectIdiom(state.curIdiom);
  }
}

function setPresetWords(type) {
  const presets = {
    '三年级精选': '温暖, 认真, 遵守, 奇迹, 灿烂, 严寒, 准备, 丰富',
    '二年级字词': '春雨, 阳光, 树木, 朋友, 快乐, 仔细, 帮助, 寻找',
    '期中易错词': '仔细, 演变, 聚集, 迅速, 既然, 赞美, 敬佩, 勇敢'
  };
  document.getElementById('dictationInput').value = presets[type] || '';
}

async function saveDictation() {
  const text = document.getElementById('dictationInput').value.trim();
  if (!text) { showToast('请输入听写生词'); return; }
  await fetch('/api/pocketclaw/mp/dictation', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ raw_text: text })
  });
  showToast('听写词单已更新并同步到 Note 4！');
  loadOverview();
}

async function handleDictationPhoto(input) {
  const file = input.files && input.files[0];
  if (!file) return;
  const statusEl = document.getElementById('dictationPhotoStatus');
  statusEl.innerText = '正在 AI 识别生字词中... ⏳';
  const reader = new FileReader();
  reader.onload = async function(e) {
    try {
      const res = await fetch('/api/pocketclaw/mp/ocr_words', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image: e.target.result, push: true })
      });
      const data = await res.json();
      if (data.ok) {
        document.getElementById('dictationInput').value = data.raw_text;
        statusEl.innerText = `✅ 成功提取 ${data.count} 个生字并已推送到 Note 4！`;
        showToast(`成功提取 ${data.count} 个生字！`);
        loadOverview();
      } else {
        statusEl.innerText = '识别未成功，请重试';
      }
    } catch (err) {
      statusEl.innerText = '识别出错，请重试';
    } finally {
      input.value = '';
    }
  };
  reader.readAsDataURL(file);
}

async function setPoemTarget(title) {
  await fetch('/api/pocketclaw/mp/poems/target', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title: title })
  });
  state.curPoemTitle = title;
  showToast('已设为当前背诵目标：《' + title + '》');
  loadOverview();
  loadPoems();
}

function selectArithPill(type, val, fromServer = false) {
  if (!fromServer) { state.arithDirty = true; state.arithRevision++; }
  if (type === 'grade') {
    state.arithGrade = val;
    document.querySelectorAll('#arithGradeGroup .pill-btn').forEach(b => b.classList.toggle('active', b.dataset.val == val));
  } else if (type === 'count') {
    state.arithCount = val;
    document.querySelectorAll('#arithCountGroup .pill-btn').forEach(b => b.classList.toggle('active', b.dataset.val == val));
  } else if (type === 'op') {
    state.arithOp = val;
    document.querySelectorAll('#arithOpGroup .pill-btn').forEach(b => b.classList.toggle('active', b.dataset.val == val));
  }
}

async function saveArithConfig() {
  const revision = state.arithRevision;
  try {
  const response = await fetch('/api/pocketclaw/mp/arith', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ grade: state.arithGrade, count: state.arithCount, op_type: state.arithOp || 'all', push: true })
  });
  const result = await response.json();
  if (!response.ok || !result.ok) throw new Error('保存失败');
  if (state.arithRevision === revision) { state.arithDirty = false; state.arithRevision++; }
  showToast(result.pushed ? '口算配置已保存并推送到设备' : '口算配置已保存，设备未收到推送');
  await loadOverview();
  } catch (_) { showToast('口算配置保存失败，请重试'); }
}

async function addManualMistake() {
  const cat = document.getElementById('newMistakeCat').value;
  const q = document.getElementById('newMistakeQ').value.trim();
  const exp = document.getElementById('newMistakeExp').value.trim();
  const wrong = document.getElementById('newMistakeWrong').value.trim();
  if (!q || !exp) {
    showToast('请填写错题题目和正确答案');
    return;
  }
  await fetch('/api/pocketclaw/mp/mistakes', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ category: cat, question: q, expected: exp, wrong_answer: wrong })
  });
  document.getElementById('newMistakeQ').value = '';
  document.getElementById('newMistakeExp').value = '';
  document.getElementById('newMistakeWrong').value = '';
  showToast('错题已录入消灭本并同步 Note 4！');
  loadOverview();
}

function selectFocusMinutes(m, btn) {
  state.focusMinutes = m;
  if (btn) btn.parentElement.querySelectorAll('.pill-btn').forEach(b => b.classList.toggle('active', b === btn));
  if (!state.focusActive) {
    const timerEl = document.getElementById('focusTimerDisplay');
    if (timerEl) timerEl.textContent = formatMMSS(m * 60);
  }
}

function selectFocusStyle(st, btn) {
  state.focusStyle = st;
  if (btn) btn.parentElement.querySelectorAll('.pill-btn').forEach(b => b.classList.toggle('active', b === btn));
}

async function controlFocus(action) {
  const res = await fetch('/api/pocketclaw/mp/focus', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      action: action,
      minutes: state.focusMinutes,
      style: state.focusStyle,
      kind: 'pomodoro'
    })
  });
  const data = await res.json();
  if (data.ok) {
    updateFocusUI(data.focus_status, data.focus_today_min);
    if (action === 'start') {
      showToast('已在 Note 4 开启 ' + state.focusMinutes + ' 分钟番茄专注！');
    } else if (action === 'pause') {
      showToast('番茄专注已暂停');
    } else if (action === 'resume') {
      showToast('番茄专注已继续');
    } else {
      showToast('本轮番茄专注已结束');
    }
    loadOverview();
  }
}

function toggleFocusPause() {
  controlFocus(state.focusPaused ? 'resume' : 'pause');
}

async function switchDeviceMode(mode) {
  const res = await fetch('/api/pocketclaw/mp/mode', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode: mode })
  });
  const data = await res.json();
  if (data.ok) {
    showToast('已切换 Note 4 屏幕至：' + (data.mode_name || mode));
    loadOverview();
  }
}

async function pushScreensaverMode(mode) {
  const res = await fetch('/api/pocketclaw/mp/screensaver/push', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode: mode, speak: false })
  });
  const data = await res.json();
  if (data.ok) {
    const names = { art: '世界名画', quote: '名人语录', poem: '经典古诗', countdown: '目标倒计时', timetable: '桌面课表', cycle: '混合轮播' };
    showToast('已推送屏保【' + (names[mode] || mode) + '】到 Note 4 墨水屏！');
    loadOverview();
  }
}

async function pushArtToDevice(artId, title, speak) {
  const res = await fetch('/api/pocketclaw/mp/screensaver/push', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode: 'art', art_id: artId, speak: !!speak })
  });
  const data = await res.json();
  if (data.ok) {
    showToast('已将 16灰阶《' + title + '》推送到 Note 4 墨水屏！');
    loadOverview();
  }
}

function renderArtGallery(artList) {
  const grid = document.getElementById('artGalleryGrid');
  if (!grid || !artList) return;
  grid.innerHTML = artList.map(art => `
    <div style="border:1px solid var(--border); border-radius:12px; overflow:hidden; background:#fff; display:flex; flex-direction:column; justify-content:space-between; box-shadow:0 1px 3px rgba(0,0,0,0.04);">
      <div>
        <div style="background:#0f172a; position:relative; aspect-ratio:4/3; overflow:hidden;">
          <img src="/api/pocketclaw/mp/art_preview/${art.id}" alt="${art.title}" loading="lazy" style="width:100%; height:100%; object-fit:cover; display:block;">
          <span style="position:absolute; top:6px; right:6px; background:rgba(15,23,42,0.75); color:#fff; font-size:10px; padding:2px 7px; border-radius:10px; font-weight:600;">16灰阶·4bpp</span>
        </div>
        <div style="padding:10px 12px 6px 12px;">
          <div style="font-size:14px; font-weight:700; color:var(--text);">《${art.title}》</div>
          <div style="font-size:11px; color:var(--primary); font-weight:600; margin-top:2px;">${art.artist} · ${art.style}</div>
          <div style="font-size:11px; color:var(--muted); margin-top:4px; line-height:1.45; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden;">${art.appreciation}</div>
        </div>
      </div>
      <div style="padding:8px 12px 10px 12px; display:flex; gap:6px;">
        <button class="btn btn-primary btn-sm" style="flex:1; font-size:11px; padding:6px 8px;" onclick="pushArtToDevice('${art.id}', '${art.title}', false)">🚀 立即推屏</button>
        <button class="btn btn-secondary btn-sm" style="font-size:11px; padding:6px 8px;" onclick="pushArtToDevice('${art.id}', '${art.title}', true)" title="推屏并语音赏析">🔊 导览</button>
      </div>
    </div>
  `).join('');
}

async function recognizeVocabPhoto() {
  const file=document.getElementById('vocabPhoto').files[0], preview=document.getElementById('vocabPhotoPreview');
  if(!file || file.size>8*1024*1024){showToast('请选择不超过 8 MB 的照片');return}
  const before=preview.value;
  try {const image=await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(r.result);r.onerror=reject;r.readAsDataURL(file)});
    showToast('正在识别，请稍候');const res=await fetch('/api/pocketclaw/mp/ocr_text',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({image,target:'英语生词'})});const data=await res.json();if(!res.ok||!data.ok)throw Error(data.detail||'识别失败');
    if(preview.value!==before){showToast('内容已修改，请重新识别');return}
    preview.value=(data.fields.entries||[]).map(x=>[x.word,x.meaning,x.example,x.example_cn].join(' | ')).join('\\n');showToast('请核对识别结果');
  } catch(e){showToast(e.message||'识别失败');}
}
async function saveVocabPhoto(){
  const preview=document.getElementById('vocabPhotoPreview'), rows=preview.value.split(/\\n+/).map(x=>x.split('|').map(s=>s.trim())).filter(x=>x[0]);
  if(!rows.length){showToast('请先输入或识别生词');return}
  try{for(const [word,meaning='',example='',example_cn=''] of rows){const res=await fetch('/api/pocketclaw/mp/vocab',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({word,meaning,example,example_cn})});if(!res.ok)throw Error('部分词条保存失败，请核对后重试');}preview.value='';showToast('已保存 '+rows.length+' 个词条');loadOverview();}catch(e){showToast(e.message||'保存失败')}
}
async function addVocabWord() {
  const w = document.getElementById('newWord').value.trim();
  const m = document.getElementById('newMeaning').value.trim();
  const e = document.getElementById('newExample').value.trim();
  const ec = document.getElementById('newExampleCn').value.trim();
  if (!w) { showToast('请输入单词'); return; }
  await fetch('/api/pocketclaw/mp/vocab', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ word: w, meaning: m, example: e, example_cn: ec })
  });
  document.getElementById('newWord').value = '';
  document.getElementById('newMeaning').value = '';
  document.getElementById('newExample').value = '';
  document.getElementById('newExampleCn').value = '';
  showToast('单词已加入生词本！');
  loadOverview();
}

async function deleteVocab(id) {
  await fetch('/api/pocketclaw/mp/vocab/' + id, { method: 'DELETE' });
  showToast('单词已删除');
  loadOverview();
}

async function setReadingTarget(id) {
  await fetch('/api/pocketclaw/mp/reading/target', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ article_id: id })
  });
  state.curReadingId = id;
  showToast('已更新当前阅读篇目，Note 4 正在同步！');
  loadOverview();
  loadReadingArticles();
}

async function handleReadingPhoto(input) {
  const file = input.files && input.files[0];
  if (!file) return;
  const statusEl = document.getElementById('readingPhotoStatus');
  const modal=document.getElementById('modalReading');
  if(modal.style.display!=='none'){statusEl.innerText='请先保存或关闭正在编辑的短文';input.value='';return}
  statusEl.innerText = '正在识别短文，请稍候';
  const reader = new FileReader();
  reader.onload = async function(e) {
    try {
      const res = await fetch('/api/pocketclaw/mp/ocr_text', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image: e.target.result, target: '英语短文' })
      });
      const data = await res.json();
      if (data.ok) {
        if(modal.style.display!=='none'){statusEl.innerText='短文表单已打开，请重新识别';return}
        openAddReadingModal();
        document.getElementById('readingTitleInput').value=data.fields.title||'';
        document.getElementById('readingSentencesInput').value=data.fields.body||'';
        statusEl.innerText='识别完成，请核对后保存';
        showToast('请核对识别内容后保存');
      } else {
        statusEl.innerText = '识别未成功，请重试';
      }
    } catch (err) {
      statusEl.innerText = '解析出错，请重试';
    } finally {
      input.value = '';
    }
  };
  reader.readAsDataURL(file);
}

function setMessageText(t) {
  document.getElementById('parentMsgInput').value = t;
}

async function sendParentMessage() {
  const text = document.getElementById('parentMsgInput').value.trim();
  if (!text) { showToast('请先输入留言内容'); return; }
  const res = await fetch('/api/pocketclaw/mp/message', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text: text })
  });
  const data = await res.json();
  if (data.ok) {
    document.getElementById('parentMsgInput').value = '';
    showToast('留言已推送并即时展现在墨水屏！');
  }
}

async function loadScreensaver() {
  try {
    const res = await fetch('/api/pocketclaw/mp/screensaver');
    const data = await res.json();
    if (!data.ok) return;
    if (data.config) {
      if (data.config.default_mode) document.getElementById('ssDefaultMode').value = data.config.default_mode;
      if (data.config.auto_timeout_minutes !== undefined) document.getElementById('ssTimeout').value = data.config.auto_timeout_minutes;
    }
    if (data.art_list && data.art_list.length > 0) {
      state.artList = data.art_list;
      renderArtGallery(data.art_list);
    }
    const listEl = document.getElementById('countdownList');
    if (!listEl) return;
    listEl.innerHTML = '';
    (data.countdowns || []).forEach(ev => {
      const row = document.createElement('div');
      row.style = "background:#fff; border:1px solid var(--border); padding:10px 14px; border-radius:8px; display:flex; justify-content:space-between; align-items:center;";

      const targetDate = new Date(ev.target_date);
      const today = new Date();
      today.setHours(0,0,0,0);
      targetDate.setHours(0,0,0,0);
      const diffDays = Math.ceil((targetDate - today) / (1000 * 60 * 60 * 24));
      const daysText = diffDays > 0 ? `还有 <b style="color:#ea580c; font-size:16px;">${diffDays}</b> 天` : (diffDays === 0 ? `<b style="color:#16a34a;">就是今天！</b>` : `已过去 ${Math.abs(diffDays)} 天`);

      row.innerHTML = `
        <div>
          <div style="font-size:14px; font-weight:600; color:var(--text);">${ev.title} <span style="font-size:12px; color:var(--muted); font-weight:normal;">(${ev.target_date})</span></div>
          <div style="font-size:12px; color:var(--muted); margin-top:2px;">${ev.slogan || ''}</div>
        </div>
        <div style="display:flex; align-items:center; gap:12px;">
          <div style="font-size:13px;">${daysText}</div>
          <button class="btn btn-secondary btn-sm" style="color:#dc2626; border-color:#fecaca;" onclick="deleteCountdownEvent('${ev.id}')">删除</button>
        </div>
      `;
      listEl.appendChild(row);
    });
  } catch(e) { console.error(e); }
}

async function saveScreensaverConfig() {
  const mode = document.getElementById('ssDefaultMode').value;
  const timeout = parseInt(document.getElementById('ssTimeout').value, 10);
  await fetch('/api/pocketclaw/mp/screensaver/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ default_mode: mode, auto_timeout_minutes: timeout, enabled: timeout > 0, push: true })
  });
  showToast('屏保设置已保存，Note 4 墨水屏即时同步！');
}

async function addCountdownEvent() {
  const title = document.getElementById('newCdTitle').value.trim();
  const dateStr = document.getElementById('newCdDate').value;
  const slogan = document.getElementById('newCdSlogan').value.trim();
  if (!title || !dateStr) { showToast('请填写目标名称和日期'); return; }
  await fetch('/api/pocketclaw/mp/screensaver/countdown', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title: title, target_date: dateStr, slogan: slogan })
  });
  document.getElementById('newCdTitle').value = '';
  document.getElementById('newCdDate').value = '';
  document.getElementById('newCdSlogan').value = '';
  showToast('倒计时目标已添加！');
  loadScreensaver();
}

async function deleteCountdownEvent(id) {
  await fetch('/api/pocketclaw/mp/screensaver/countdown/' + id, { method: 'DELETE' });
  showToast('倒计时目标已删除');
  loadScreensaver();
}

let currentMenuItems = [];

async function loadMenuOrder() {
  try {
    const res = await fetch('/api/pocketclaw/mp/menu');
    const data = await res.json();
    if (!data.ok) return;
    currentMenuItems = data.items || [];
    const subTitle = document.getElementById('menuModeSubtitle');
    if (subTitle) {
      subTitle.innerText = data.is_custom ? '当前已应用家长自定义排序' : '当前已应用智能频次算法（选择越多越靠前）';
      subTitle.style.color = data.is_custom ? '#7c3aed' : '#16a34a';
    }
    renderMenuList();
  } catch(e) { console.error(e); }
}

function renderMenuList() {
  const container = document.getElementById('menuList');
  if (!container) return;
  container.innerHTML = '';
  currentMenuItems.forEach((item, idx) => {
    const row = document.createElement('div');
    row.style = "background:#fff; border:1px solid var(--border); padding:10px 14px; border-radius:8px; display:flex; justify-content:space-between; align-items:center;";
    row.innerHTML = `
      <div style="display:flex; align-items:center; gap:10px;">
        <span style="font-size:12px; font-weight:700; color:var(--muted); width:24px;">${idx + 1}.</span>
        <div>
          <div style="font-size:14px; font-weight:600; color:var(--text);">${item.name}
            <span style="font-size:11px; background:#eff6ff; color:#2563eb; padding:2px 6px; border-radius:4px; margin-left:6px;">已选 ${item.clicks || 0} 次</span>
          </div>
          <div style="font-size:12px; color:var(--muted); margin-top:2px;">${item.desc || ''}</div>
        </div>
      </div>
      <div style="display:flex; gap:6px;">
        <button class="btn btn-secondary btn-sm" onclick="moveMenuItem(${idx}, -1)" ${idx === 0 ? 'disabled' : ''}>▲ 上移</button>
        <button class="btn btn-secondary btn-sm" onclick="moveMenuItem(${idx}, 1)" ${idx === currentMenuItems.length - 1 ? 'disabled' : ''}>▼ 下移</button>
      </div>
    `;
    container.appendChild(row);
  });
}

function moveMenuItem(idx, delta) {
  const targetIdx = idx + delta;
  if (targetIdx < 0 || targetIdx >= currentMenuItems.length) return;
  const temp = currentMenuItems[idx];
  currentMenuItems[idx] = currentMenuItems[targetIdx];
  currentMenuItems[targetIdx] = temp;
  renderMenuList();
}

async function saveCustomMenuOrder() {
  const order = currentMenuItems.map(it => it.mode);
  await fetch('/api/pocketclaw/mp/menu/order', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ order: order })
  });
  showToast('菜单自定义顺序已保存，Note 4 墨水屏即时生效！');
  loadMenuOrder();
}

async function resetMenuOrder(resetClicks) {
  if (resetClicks && !confirm('确定要清空所有功能的历史点击频次并重置为课标默认顺序吗？')) return;
  await fetch('/api/pocketclaw/mp/menu/reset', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reset_clicks: resetClicks })
  });
  showToast(resetClicks ? '菜单顺序与频次统计已全部重置！' : '已重置回智能频次排序（高频优先）！');
  loadMenuOrder();
}

// ── 学伴人设与 LLM/OCR 设置 ──────────────────────
const COMPANION_PRESETS = {
  classic: {
    name: "虾崽",
    style: "温柔耐心",
    persona: "你的第一只伴学小龙虾，天生好奇、话里带笑。最爱把话头交回给小朋友，遇到有趣的知识会兴奋地举例子，讲完只留一个小问题等他接。遇到自己不擅长的领域，会大方地把专家朋友请出来。",
    guidance: "你在一块黑白墨水屏便利贴上和小朋友说话，屏幕很小、只能靠语音听。只输出要对小朋友讲的话本身，口语、亲切。**每次回复最多两句、总共不超过 40 个字**——说完就停，把话头留给孩子；英语练习时英文只说一句短句，再给一句中文意思。绝不要加脚注编号，绝不要出现「说明：」，也不要复述你的教学设计。结尾留一个短问题（不超过 12 个字）。"
  },
  humor: {
    name: "大白",
    style: "幽默风趣",
    persona: "风趣幽默的机器小学伴，幽默乐观，擅长用生活中的冷笑话或有趣比喻解释复杂问题，喜欢用轻松的方式鼓励孩子克服学习困难。",
    guidance: "在黑白墨水屏便利贴上回答，口吻幽默风趣，两句话以内不超过 40 字，结尾留一个趣味反问启发思考。"
  },
  socratic: {
    name: "小聪",
    style: "严谨学霸",
    persona: "苏格拉底式的思维导师，绝不直接给出题目的答案，善于通过分步拆解、循序渐进的启发式提问引导孩子自己推导出正确答案。",
    guidance: "绝不直接给最终答案！给出第一步思考的线索，每次回答两句以内（不超过40字），并反问孩子下一步该怎么算。"
  },
  english: {
    name: "Captain",
    style: "双语外教",
    persona: "Native English tutor for elementary students. Encouraging, vibrant, and fun. Speaks natural and simple English first, followed by a friendly Chinese explanation.",
    guidance: "每次回复先说一句地道简单的纯英文，再给一句温和中文译文。两句话以内不超过40字，结尾用英文鼓励孩子开口跟读。"
  }
};

const PROVIDER_PRESETS = {
  deepseek: { base_url: "https://api.deepseek.com/v1", model: "deepseek-chat", vision: "gpt-4o-mini" },
  qwen: { base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "qwen-plus", vision: "qwen-vl-plus" },
  doubao: { base_url: "https://ark.cn-beijing.volces.com/api/v3", model: "doubao-seed-2-1-turbo-260628", vision: "doubao-seed-1-5-vision" },
  zhipu: { base_url: "https://open.bigmodel.cn/api/paas/v4", model: "glm-4-flash", vision: "glm-4v-flash" },
  openai: { base_url: "https://api.openai.com/v1", model: "gpt-4o-mini", vision: "gpt-4o-mini" },
  ollama: { base_url: "http://localhost:11434/v1", model: "qwen2.5:7b", vision: "llava:7b" },
  custom: { base_url: "", model: "", vision: "" }
};

function onStylePresetChange(val) {
  if (val === 'custom') return;
  const p = Object.values(COMPANION_PRESETS).find(x => x.style === val);
  if (p) {
    document.getElementById('cfgCompPersona').value = p.persona;
    document.getElementById('cfgCompGuidance').value = p.guidance;
  }
}

function applyPresetCompanion(type) {
  const p = COMPANION_PRESETS[type];
  if (!p) return;
  document.getElementById('cfgCompName').value = p.name;
  document.getElementById('cfgCompStyle').value = p.style;
  document.getElementById('cfgCompPersona').value = p.persona;
  document.getElementById('cfgCompGuidance').value = p.guidance;
  showToast(`已套用「${p.name} · ${p.style}」预设`);
}

function onLlmProviderChange(prov) {
  const p = PROVIDER_PRESETS[prov];
  if (!p) return;
  if (p.base_url) document.getElementById('cfgLlmBaseUrl').value = p.base_url;
  if (p.model) document.getElementById('cfgLlmModel').value = p.model;
  if (p.vision) document.getElementById('cfgOcrModel').value = p.vision;
}

function toggleLlmMode(mode) {
  const onlineSec = document.getElementById('onlineLlmSettings');
  const badge = document.getElementById('llmModeBadge');
  if (mode === 'local') {
    onlineSec.style.opacity = '0.45';
    badge.innerText = '服务器本地版本 (Local)';
    badge.style.background = '#e2e8f0';
    badge.style.color = '#475569';
  } else {
    onlineSec.style.opacity = '1';
    badge.innerText = '在线版本 (Online)';
    badge.style.background = '#fef3c7';
    badge.style.color = '#d97706';
  }
}

function toggleOcrMode(mode) {
  const onlineSec = document.getElementById('onlineOcrSettings');
  const badge = document.getElementById('ocrModeBadge');
  if (mode === 'local') {
    onlineSec.style.opacity = '0.45';
    badge.innerText = '服务器本地规则 (Local)';
    badge.style.background = '#e2e8f0';
    badge.style.color = '#475569';
  } else {
    onlineSec.style.opacity = '1';
    badge.innerText = '在线视觉模型 (Vision)';
    badge.style.background = '#f0fdf4';
    badge.style.color = '#16a34a';
  }
}

function togglePasswordVisibility(id) {
  const el = document.getElementById(id);
  if (el.type === 'password') {
    el.type = 'text';
  } else {
    el.type = 'password';
  }
}

async function loadSettings() {
  loadChildProfile();
  try {
    const res = await fetch('/api/pocketclaw/mp/settings');
    const data = await res.json();
    if (!data.ok) return;

    // 1. Companion
    const comp = data.companion || {};
    document.getElementById('cfgCompName').value = comp.name || '虾崽';
    document.getElementById('cfgCompStyle').value = comp.style || '温柔耐心';
    document.getElementById('cfgCompPersona').value = comp.persona || '';
    document.getElementById('cfgCompGuidance').value = comp.guidance || '';

    // 2. LLM
    const llm = data.llm || {};
    const llmMode = llm.mode || 'online';
    const llmRadios = document.getElementsByName('llmMode');
    for (let r of llmRadios) {
      r.checked = (r.value === llmMode);
    }
    toggleLlmMode(llmMode);
    if (llm.provider) document.getElementById('cfgLlmProvider').value = llm.provider;
    if (llm.model) document.getElementById('cfgLlmModel').value = llm.model;
    if (llm.base_url) document.getElementById('cfgLlmBaseUrl').value = llm.base_url;
    if (llm.api_key) document.getElementById('cfgLlmApiKey').value = llm.api_key;

    // 3. OCR
    const ocr = data.ocr || {};
    const ocrMode = ocr.mode || 'online';
    const ocrRadios = document.getElementsByName('ocrMode');
    for (let r of ocrRadios) {
      r.checked = (r.value === ocrMode);
    }
    toggleOcrMode(ocrMode);
    if (ocr.vision_model) document.getElementById('cfgOcrModel').value = ocr.vision_model;
    if (ocr.base_url) document.getElementById('cfgOcrBaseUrl').value = ocr.base_url;
    if (ocr.api_key) document.getElementById('cfgOcrApiKey').value = ocr.api_key;
  } catch (e) {
    console.error('加载系统设置失败:', e);
  }
}

async function loadChildProfile() {
  try {
    const res = await fetch('/api/pocketclaw/mp/child_profile', {cache:'no-store'});
    if (!res.ok) throw new Error('读取失败');
    const p = (await res.json()).profile;
    document.getElementById('childProfileName').value = p.name;
    document.getElementById('childProfileGrade').value = String(p.grade);
    document.getElementById('childProfileGender').value = p.gender;
    document.getElementById('childProfileInterests').value = p.interests;
  } catch (_) { showToast('孩子资料读取失败'); }
}
async function saveChildProfile() {
  const p = {name: document.getElementById('childProfileName').value.trim(), grade: Number(document.getElementById('childProfileGrade').value), gender: document.getElementById('childProfileGender').value, interests: document.getElementById('childProfileInterests').value.trim()};
  try {
    const res = await fetch('/api/pocketclaw/mp/child_profile', {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(p)});
    if (!res.ok) throw new Error('保存失败');
    showToast('孩子资料已保存');
  } catch (_) { showToast('孩子资料保存失败'); }
}

async function saveAllSettings() {
  let llmMode = 'online';
  for (let r of document.getElementsByName('llmMode')) {
    if (r.checked) llmMode = r.value;
  }
  let ocrMode = 'online';
  for (let r of document.getElementsByName('ocrMode')) {
    if (r.checked) ocrMode = r.value;
  }

  const payload = {
    companion: {
      name: document.getElementById('cfgCompName').value.trim(),
      style: document.getElementById('cfgCompStyle').value,
      persona: document.getElementById('cfgCompPersona').value.trim(),
      guidance: document.getElementById('cfgCompGuidance').value.trim()
    },
    llm: {
      mode: llmMode,
      provider: document.getElementById('cfgLlmProvider').value,
      model: document.getElementById('cfgLlmModel').value.trim(),
      base_url: document.getElementById('cfgLlmBaseUrl').value.trim(),
      api_key: document.getElementById('cfgLlmApiKey').value.trim()
    },
    ocr: {
      mode: ocrMode,
      vision_model: document.getElementById('cfgOcrModel').value.trim(),
      base_url: document.getElementById('cfgOcrBaseUrl').value.trim(),
      api_key: document.getElementById('cfgOcrApiKey').value.trim()
    }
  };

  try {
    const res = await fetch('/api/pocketclaw/mp/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const d = await res.json();
    if (d.ok) {
      showToast('✅ 全部设置保存成功并已立即生效！');
      loadSettings();
    } else {
      showToast('❌ 保存失败: ' + (d.error || '未知错误'));
    }
  } catch (e) {
    showToast('❌ 保存请求异常: ' + e);
  }
}

async function testLlmConnection() {
  const resultSpan = document.getElementById('llmTestResult');
  resultSpan.innerText = '正在测速中... ⏳';
  resultSpan.style.color = 'var(--primary)';

  const payload = {
    base_url: document.getElementById('cfgLlmBaseUrl').value.trim(),
    model: document.getElementById('cfgLlmModel').value.trim(),
    api_key: document.getElementById('cfgLlmApiKey').value.trim()
  };

  try {
    const res = await fetch('/api/pocketclaw/mp/settings/test_llm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const d = await res.json();
    if (d.ok) {
      resultSpan.innerText = `✅ 连接成功 (${d.latency_ms}ms): 「${d.reply}」`;
      resultSpan.style.color = 'var(--success)';
    } else {
      resultSpan.innerText = `❌ 连接失败 (${d.latency_ms || 0}ms): ${d.error}`;
      resultSpan.style.color = '#ef4444';
    }
  } catch (e) {
    resultSpan.innerText = `❌ 请求异常: ${e}`;
    resultSpan.style.color = '#ef4444';
  }
}

async function resetAllSettings() {
  if (!confirm('确定要恢复学伴人设、LLM 和 OCR 到出厂默认设置吗？')) return;
  try {
    const res = await fetch('/api/pocketclaw/mp/settings/reset', { method: 'POST' });
    const d = await res.json();
    if (d.ok) {
      showToast('已恢复出厂设置');
      loadSettings();
    }
  } catch (e) {
    showToast('重置失败: ' + e);
  }
}

async function newsRequest(path, options = {}) {
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok || !result.ok) throw new Error(result.detail || '操作失败');
  return result;
}
function newsPost(path, data) { return newsRequest(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)}); }
function setQuickNewsRegion(region) {
  document.getElementById('newsRegionInput').value = region;
  document.querySelectorAll('#tab-news .pill-group .pill-btn').forEach(b => b.classList.toggle('active', b.textContent.trim() === region));
}
async function loadNews() {
  try {
    const data = await newsRequest('/api/pocketclaw/mp/news');
    document.getElementById('newsRegionBadge').textContent = '当前关注地区：' + data.region;
    setQuickNewsRegion(data.region);
    document.getElementById('newsWhitelistInput').value = data.whitelist.join('、');
    document.getElementById('newsBlacklistInput').value = data.blacklist.join('、');
    const sources = document.getElementById('newsSourcesList'); sources.replaceChildren();
    for (const src of data.sources) {
      const chip = document.createElement('span'); chip.className='badge badge-primary'; chip.textContent=src.name + ' · ' + src.region;
      if (src.id && src.id.startsWith('src_custom_')) {
        const remove = document.createElement('button'); remove.type='button'; remove.textContent='移除'; remove.setAttribute('aria-label','移除信源 ' + src.name);
        remove.onclick=async()=>{try{await newsPost('/api/pocketclaw/mp/news/source/delete',{source_id:src.id});await loadNews()}catch(_){showToast('信源移除失败')}};
        chip.append(' ',remove);
      }
      sources.appendChild(chip);
    }
    document.getElementById('newsStatsSpan').textContent = data.sources.length + ' 个信源';
    const items = document.getElementById('newsItemsList'); items.replaceChildren();
    for (const item of data.items) {
      const row=document.createElement('div'); row.className='card'; row.style.marginBottom='10px';
      const title=document.createElement('strong'); title.textContent=(item.is_example ? '示例 · ' : '') + (item.title || ''); row.appendChild(title);
      const details=document.createElement('p'); details.textContent=(item.region || '全国') + ' · ' + (item.summary || ''); row.appendChild(details);
      const hide=document.createElement('button'); hide.type='button'; hide.className='btn btn-secondary btn-sm'; hide.textContent='隐藏';
      hide.onclick=async()=>{try{await newsRequest('/api/pocketclaw/mp/news/'+encodeURIComponent(item.id),{method:'DELETE'});await loadNews()}catch(_){showToast('隐藏失败')}};
      row.appendChild(hide); items.appendChild(row);
    }
    if (!data.items.length) items.textContent='当前没有符合条件的资讯';
  } catch (_) { showToast('新鲜事加载失败'); }
}
async function saveNewsRegion() {
  try { await newsPost('/api/pocketclaw/mp/news/config',{region:document.getElementById('newsRegionInput').value.trim() || '全国'}); await loadNews(); showToast('关注地区已保存'); }
  catch (_) {showToast('地区保存失败');}
}
async function saveNewsKeywords() {
  const words=id=>document.getElementById(id).value.split(/[,，、]/).map(s=>s.trim()).filter(Boolean);
  try { await newsPost('/api/pocketclaw/mp/news/config',{whitelist:words('newsWhitelistInput'),blacklist:words('newsBlacklistInput')}); await loadNews(); showToast('关键词已保存'); }
  catch (_) {showToast('关键词保存失败');}
}
function toggleAddSourceForm(){const el=document.getElementById('addSourceForm');el.style.display=el.style.display==='none'?'block':'none';}
function toggleAddNewsForm(){const el=document.getElementById('addNewsForm');el.style.display=el.style.display==='none'?'block':'none';}
async function submitNewSource(){
  const val=id=>document.getElementById(id).value.trim(); const name=val('newSourceName');
  if(!name){showToast('请填写信源名称');return}
  try{await newsPost('/api/pocketclaw/mp/news/source',{name,url:val('newSourceUrl'),category:val('newSourceCat'),region:val('newSourceReg')});for(const id of ['newSourceName','newSourceUrl','newSourceCat','newSourceReg'])document.getElementById(id).value='';toggleAddSourceForm();await loadNews();showToast('信源已保存')}
  catch(_){showToast('信源保存失败')}
}
async function submitCustomNews(){
  const val=id=>document.getElementById(id).value.trim();const title=val('customNewsTitle');
  if(!title){showToast('请填写活动标题');return}
  try{await newsPost('/api/pocketclaw/mp/news',{title,category:val('customNewsCat')||'活动',region:val('customNewsReg')||'全国',summary:val('customNewsSummary'),content:val('customNewsContent')});for(const id of ['customNewsTitle','customNewsCat','customNewsReg','customNewsSummary','customNewsContent'])document.getElementById(id).value='';toggleAddNewsForm();await loadNews();showToast('活动已保存')}
  catch(_){showToast('活动保存失败')}
}

function togglePresetCatalog(){const panel=document.getElementById('presetCatalogPanel');panel.hidden=!panel.hidden;if(!panel.hidden)loadPresetCatalog();}
async function loadPresetCatalog(){
  const category=document.getElementById('presetCategory').value, grade=document.getElementById('presetGrade').value;
  try{const res=await fetch('/api/pocketclaw/mp/presets?category='+encodeURIComponent(category)+'&grades='+encodeURIComponent(grade));const data=await res.json();if(!res.ok||!data.ok)throw Error();
    const container=document.getElementById('presetItems');container.replaceChildren();
    for(const item of data.items){const label=document.createElement('label');label.style.cssText='display:block;padding:7px;border-bottom:1px solid #eef2f6';const box=document.createElement('input');box.type='checkbox';box.value=item.id;box.style.width='auto';label.append(box,document.createTextNode(' '+item.title+' · '+item.grade+'年级'));container.appendChild(label)}
    if(!data.items.length)container.textContent='未选择年级';
  }catch(_){showToast('预设内容加载失败')}
}
function selectAllPresets(checked){document.querySelectorAll('#presetItems input[type="checkbox"]').forEach(box=>{box.checked=checked})}
async function importSelectedPresets(){
  const ids=Array.from(document.querySelectorAll('#presetItems input:checked'),box=>box.value);if(!ids.length){showToast('请先选择内容');return}
  try{const res=await fetch('/api/pocketclaw/mp/presets/import',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({category:document.getElementById('presetCategory').value,ids})});const result=await res.json();if(!res.ok||!result.ok)throw Error();showToast('已导入 '+result.imported+' 项'+(result.skipped?'，已有 '+result.skipped+' 项':'') );loadOverview()}catch(_){showToast('导入失败，请重试')}
}

let textOcrTarget = null;
document.addEventListener('focusin', event => {
  const el = event.target;
  if (!el.closest('#textOcrPanel') && (el.tagName === 'TEXTAREA' || (el.tagName === 'INPUT' && ['text','search'].includes(el.type)))) textOcrTarget = el;
});
function toggleTextOcr() { const panel=document.getElementById('textOcrPanel');panel.hidden=!panel.hidden; }
async function recognizeTextPhoto() {
  const file=document.getElementById('textOcrFile').files[0];
  if(!file){showToast('请先选择照片');return} if(file.size>8*1024*1024){showToast('照片不能超过 8 MB');return}
  const type=document.getElementById('textOcrType').value;
  const mapping={'作业':['','rawTasksInput',''],'习惯':['newHabitInput','',''],'生字词':['dictationTitleInput','dictationInput',''],'古诗':['poemTitleInput','poemLinesInput','poemAuthorInput'],'成语':['idiomInput','idiomMeaningInput','idiomPinyinInput'],'英语生词':['newWord','newMeaning',''],'英语短文':['readingTitleInput','readingSentencesInput',''],'小学新鲜事':['customNewsTitle','customNewsSummary','customNewsReg'],'错题':['newMistakeQ','newMistakeExp',''],'费曼主题':['feynmanTopicInput','feynmanHintInput',''],'猜猜看':['guessNameInput','guessHintInput','guessCatInput'],'口算题':['arithExprInput','arithAnsInput','arithHintInput'],'事实核查':['factKw1Input','factKw2Input',''],'飞花令':['feihuaKeywordInput','feihuaVerseInput',''],'心愿':['inputWishTitle','',''],'亲子留言':['','parentMsgInput',''],'倒计时':['newCdTitle','newCdDate','newCdSlogan']};
  const ids=mapping[type]||[], elements=ids.map(id=>id?document.getElementById(id):null);
  if(type && elements.filter(Boolean).length===0){showToast('请在对应栏目手动录入或选择普通文字');return}
  if(elements.some(el=>el && el.closest('.modal-overlay') && el.closest('.modal-overlay').style.display==='none')){showToast('请先打开对应栏目的添加表单');return}
  const snapshot=elements.map(el=>el&&el.value), tab=state.currentTab;
  try {
    showToast('正在识别，请稍候');
    const data=await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(r.result);r.onerror=reject;r.readAsDataURL(file)});
    const response=await fetch('/api/pocketclaw/mp/ocr_text',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({image:data,target:type})});
    const result=await response.json(); if(!response.ok || !result.ok) throw new Error(result.detail || '识别失败');
    if(type){if(type!==document.getElementById('textOcrType').value||tab!==state.currentTab||elements.some((el,i)=>el&&el.value!==snapshot[i])){showToast('表单已修改，请重新识别');return}const f=result.fields;[f.title,f.body,f.extra].forEach((value,i)=>{if(elements[i]&&value){elements[i].value=value;elements[i].dispatchEvent(new Event('input',{bubbles:true}))}});if(type==='心愿'&&f.body)document.getElementById('textOcrResult').value='所需星数：'+f.body+'，请手动选择';if(type==='英语生词'&&(f.entries||[]).length){document.getElementById('vocabPhotoPreview').value=f.entries.map(x=>[x.word,x.meaning,x.example,x.example_cn].join(' | ')).join('\\n')}showToast('已填入，请核对后保存')}
    else {document.getElementById('textOcrResult').value=result.text;showToast('识别完成，请核对')}
  } catch(e) {showToast(e.message || '识别失败，请重试');}
}
function insertTextOcr(){
  const value=document.getElementById('textOcrResult').value;
  if(!textOcrTarget || !document.contains(textOcrTarget)){showToast('请先点击要填写的输入框');return}
  textOcrTarget.value=value;textOcrTarget.dispatchEvent(new Event('input',{bubbles:true}));textOcrTarget.focus();showToast('文字已填入，请核对后保存');
}
async function copyTextOcr(){try{await navigator.clipboard.writeText(document.getElementById('textOcrResult').value);showToast('已复制')}catch(_){showToast('复制失败，请手动选择文字')}}

// Initial load
window.addEventListener('DOMContentLoaded', () => {
  try {
    const urlParams = new URLSearchParams(window.location.search);
    const initTab = urlParams.get('tab') || window.location.hash.replace('#', '');
    if (initTab && TAB_NAME_MAP[initTab]) {
      switchTab(initTab);
      if (initTab === 'settings') loadSettings();
      if (initTab === 'screensaver') loadScreensaver();
      if (initTab === 'news') loadNews();
      if (initTab === 'menu') loadMenuOrder();
    }
  } catch (e) {
    console.error(e);
  }
  loadOverview();
  loadPoems();
  loadReadingArticles();
  loadReport();
  loadIdioms();
  loadNews();
  loadScreensaver();
  setInterval(loadOverview, 5000);
  setInterval(() => {
    if (state.focusActive && !state.focusPaused && state.focusRemainingS > 0) {
      state.focusRemainingS = Math.max(0, state.focusRemainingS - 1);
      updateFocusUI({
        active: true,
        paused: false,
        remaining_s: state.focusRemainingS,
        total_s: state.focusTotalS,
        style: state.focusStyle
      });
    }
  }, 1000);
});



// ── Universal Modal Helpers ──
function openModal(id) {
  const el = document.getElementById(id);
  if (el) el.style.display = 'flex';
}
function closeModal(id) {
  const el = document.getElementById(id);
  if (el) el.style.display = 'none';
}

// ── 19 大功能开关控制 ──
async function loadFeatures() {
  try {
    const res = await fetch('/api/pocketclaw/mp/features');
    const data = await res.json();
    if (!data.ok) return;
    state.features = data.features || [];
    state.disabledFeatures = new Set(data.disabled || []);
    renderFeatures(state.features, state.disabledFeatures);
    updateFeatureToggleBadges(state.features, state.disabledFeatures);
  } catch (e) {
    console.error('loadFeatures error:', e);
  }
}

function renderFeatures(features, disabledSet) {
  const el = document.getElementById('featuresGrid');
  if (!el) return;
  const total = features.length;
  const activeCount = features.filter(f => !disabledSet.has(f.key)).length;
  const badgeEl = document.getElementById('sideBadgeFeatures');
  if (badgeEl) badgeEl.textContent = `${activeCount}/${total}`;
  const titleBadge = document.getElementById('featuresCountBadge');
  if (titleBadge) titleBadge.textContent = `已启用 ${activeCount} / 共有 ${total} 项`;

  el.innerHTML = features.map(f => {
    const isEnabled = !disabledSet.has(f.key);
    return `
      <div class="feature-toggle-card ${isEnabled ? '' : 'disabled'}" id="feat-card-${f.key}">
        <div style="flex:1; min-width:0; padding-right:12px;">
          <div style="display:flex; align-items:center; gap:8px; margin-bottom:4px;">
            <span style="font-size:15px; font-weight:700; color:var(--text);">${f.name}</span>
            <span class="badge" style="background:#f1f5f9; color:#475569; font-size:10px;">${f.category}</span>
            <span id="feat-badge-${f.key}" class="badge" style="${isEnabled ? 'background:#dcfce7; color:#15803d;' : 'background:#fee2e2; color:#b91c1c;'} font-size:11px;">
              ${isEnabled ? '🟢 已启用' : '⚪ 已停用'}
            </span>
          </div>
          <div style="font-size:12px; color:var(--muted);">${f.desc}</div>
        </div>
        <label class="switch">
          <input type="checkbox" ${isEnabled ? 'checked' : ''} onchange="toggleFeatureDirect('${f.key}', this.checked)">
          <span class="slider"></span>
        </label>
      </div>
    `;
  }).join('');
}

async function toggleFeatureDirect(key, enabled) {
  try {
    const res = await fetch('/api/pocketclaw/mp/features/toggle', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ feature: key, enabled: enabled })
    });
    const data = await res.json();
    if (data.ok) {
      showToast(`${data.feature} 功能已${enabled ? '开启' : '停用'}，Note 4 墨水屏已同步`);
      if (enabled) {
        if (state.disabledFeatures) state.disabledFeatures.delete(key);
      } else {
        if (state.disabledFeatures) state.disabledFeatures.add(key);
      }
      if (state.features) {
        renderFeatures(state.features, state.disabledFeatures);
        updateFeatureToggleBadges(state.features, state.disabledFeatures);
      }
    } else {
      showToast('开关切换失败: ' + (data.error || '未知错误'));
    }
  } catch (e) {
    showToast('网络异常，请重试');
  }
}

function updateFeatureToggleBadges(features, disabledSet) {
  features.forEach(f => {
    const isEnabled = !disabledSet.has(f.key);
    const tabToggle = document.getElementById('tabToggle-' + f.key);
    if (tabToggle) tabToggle.checked = isEnabled;
    const tabBadge = document.getElementById('tabBadge-' + f.key);
    if (tabBadge) {
      tabBadge.textContent = isEnabled ? '🟢 本功能已启用' : '⚪ 本功能已停用';
      tabBadge.style.color = isEnabled ? '#15803d' : '#b91c1c';
      tabBadge.style.background = isEnabled ? '#dcfce7' : '#fee2e2';
    }
  });
}

// ── 全功能内容修改与删除端点 (CRUD) ──

// 1. 作业修改
function editTaskModal(taskId) {
  const t = (state.lastTasks || []).find(it => it.id === taskId);
  if (!t) return;
  document.getElementById('editTaskId').value = t.id;
  document.getElementById('editTaskText').value = t.text;
  document.getElementById('editTaskSubject').value = t.subject || '其他';
  openModal('modalEditTask');
}

async function saveEditTask() {
  const id = document.getElementById('editTaskId').value;
  const text = document.getElementById('editTaskText').value.trim();
  const subject = document.getElementById('editTaskSubject').value.trim();
  if (!text) { showToast('任务内容不能为空'); return; }
  await fetch('/api/pocketclaw/mp/tasks/' + id, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, subject })
  });
  closeModal('modalEditTask');
  showToast('作业已修改并推送到 Note 4！');
  loadOverview();
}

// 2. 生词卡修改
function editVocabModal(wordId) {
  const w = (state.lastVocabWords || []).find(it => it.id === wordId);
  if (!w) return;
  document.getElementById('editVocabId').value = w.id;
  document.getElementById('editVocabWord').value = w.word;
  document.getElementById('editVocabMeaning').value = w.meaning || '';
  document.getElementById('editVocabExample').value = w.example || '';
  document.getElementById('editVocabExampleCn').value = w.example_cn || '';
  openModal('modalEditVocab');
}

async function saveEditVocab() {
  const id = document.getElementById('editVocabId').value;
  const word = document.getElementById('editVocabWord').value.trim();
  const meaning = document.getElementById('editVocabMeaning').value.trim();
  const example = document.getElementById('editVocabExample').value.trim();
  const exampleCn = document.getElementById('editVocabExampleCn').value.trim();
  if (!word || !meaning) { showToast('单词和释义为必填项'); return; }
  await fetch('/api/pocketclaw/mp/vocab/' + id, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ word, meaning, example, example_cn: exampleCn })
  });
  closeModal('modalEditVocab');
  showToast('生词已修改保存！');
  loadOverview();
}

// 3. 习惯打卡修改
function editHabitModal(habitId) {
  const h = (state.lastHabits || []).find(it => it.id === habitId);
  if (!h) return;
  document.getElementById('editHabitId').value = h.id;
  document.getElementById('editHabitName').value = h.name;
  document.getElementById('editHabitIcon').value = h.icon || '⭐';
  document.getElementById('editHabitDays').value = h.target_days || 21;
  openModal('modalEditHabit');
}

async function saveEditHabit() {
  const id = document.getElementById('editHabitId').value;
  const name = document.getElementById('editHabitName').value.trim();
  const icon = document.getElementById('editHabitIcon').value.trim() || '⭐';
  const days = parseInt(document.getElementById('editHabitDays').value) || 21;
  if (!name) { showToast('习惯名称不能为空'); return; }
  await fetch('/api/pocketclaw/mp/habits/' + id, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, icon, target_days: days })
  });
  closeModal('modalEditHabit');
  showToast('习惯已更新！');
  loadOverview();
}

// 4. 心愿修改
function editWishModal(wishId, title, stars) {
  document.getElementById('editWishId').value = wishId;
  document.getElementById('editWishTitle').value = title;
  document.getElementById('editWishStars').value = stars;
  openModal('modalEditWish');
}

async function saveEditWish() {
  const id = document.getElementById('editWishId').value;
  const title = document.getElementById('editWishTitle').value.trim();
  const stars = parseInt(document.getElementById('editWishStars').value) || 20;
  if (!title) { showToast('心愿名称不能为空'); return; }
  await fetch('/api/pocketclaw/mp/stars/wishes/' + id, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title, stars_needed: stars })
  });
  closeModal('modalEditWish');
  showToast('心愿已修改！');
  loadOverview();
}

// 5. 错题新增与修改
function openAddMistakeModal() {
  document.getElementById('newMistakeQuestion').value = '';
  document.getElementById('newMistakeAnswer').value = '';
  document.getElementById('newMistakeSubject').value = '数学';
  openModal('modalAddMistake');
}

async function saveNewMistake() {
  const q = document.getElementById('newMistakeQuestion').value.trim();
  const a = document.getElementById('newMistakeAnswer').value.trim();
  const s = document.getElementById('newMistakeSubject').value;
  if (!q) { showToast('请输入题目'); return; }
  await fetch('/api/pocketclaw/mp/mistakes', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question: q, answer: a, subject: s, category: s })
  });
  closeModal('modalAddMistake');
  showToast('错题已录入错题本！');
  loadOverview();
}

function editMistakeModal(mistakeId) {
  const m = (state.lastMistakes || []).find(it => it.id === mistakeId);
  if (!m) return;
  document.getElementById('editMistakeId').value = m.id;
  document.getElementById('editMistakeQuestion').value = m.question || '';
  document.getElementById('editMistakeAnswer').value = m.answer || '';
  document.getElementById('editMistakeSubject').value = m.subject || '数学';
  openModal('modalEditMistake');
}

async function saveEditMistake() {
  const id = document.getElementById('editMistakeId').value;
  const q = document.getElementById('editMistakeQuestion').value.trim();
  const a = document.getElementById('editMistakeAnswer').value.trim();
  const s = document.getElementById('editMistakeSubject').value;
  if (!q) { showToast('题目不能为空'); return; }
  await fetch('/api/pocketclaw/mp/mistakes/' + id, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question: q, answer: a, subject: s })
  });
  closeModal('modalEditMistake');
  showToast('错题已修改！');
  loadOverview();
}

// 6. 屏保倒计时修改
function editCountdownModal(id, title, targetDate, slogan) {
  document.getElementById('editCountdownId').value = id;
  document.getElementById('editCountdownTitle').value = title;
  document.getElementById('editCountdownTargetDate').value = targetDate;
  document.getElementById('editCountdownSlogan').value = slogan || '';
  openModal('modalEditCountdown');
}

async function saveEditCountdown() {
  const id = document.getElementById('editCountdownId').value;
  const title = document.getElementById('editCountdownTitle').value.trim();
  const targetDate = document.getElementById('editCountdownTargetDate').value.trim();
  const slogan = document.getElementById('editCountdownSlogan').value.trim();
  if (!title || !targetDate) { showToast('标题和目标日期不能为空'); return; }
  await fetch('/api/pocketclaw/mp/screensaver/countdown/' + id, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title, target_date: targetDate, slogan })
  });
  closeModal('modalEditCountdown');
  showToast('倒计时事件已修改！');
  loadScreensaver();
}

// 7. 课内古诗自定义库 CRUD
async function loadCustomPoems() {
  try {
    const res = await fetch('/api/pocketclaw/mp/poems/custom');
    const data = await res.json();
    if (data.ok) renderCustomPoems(data.poems || []);
  } catch (e) {
    console.error(e);
  }
}

function renderCustomPoems(poems) {
  state.lastCustomPoems = poems;
  const el = document.getElementById('customPoemsList');
  if (!el) return;
  if (!poems.length) {
    el.innerHTML = '<div style="font-size:12px; color:var(--muted); text-align:center; padding:16px;">暂无家长自定义古诗，可点击上方按钮添加。</div>';
    return;
  }
  el.innerHTML = poems.map(p => `
    <div class="custom-item-card">
      <div style="flex:1; min-width:0;">
        <div style="font-weight:700; font-size:14px; color:var(--text);">${p.title} <span style="font-size:12px; color:var(--muted); font-weight:normal;">[${p.dynasty}] ${p.author}</span></div>
        <div style="font-size:12px; color:#475569; margin-top:4px;">${(p.lines || []).slice(0, 2).join('，')}...</div>
      </div>
      <div style="display:flex; gap:6px;">
        <button class="btn btn-secondary btn-sm" onclick="setPoemTarget('${p.title}')">📱 点播背诵</button>
        <button class="btn btn-secondary btn-sm" onclick="editPoemModal('${p.id}')">✏️</button>
        <button class="btn btn-secondary btn-sm" onclick="deleteCustomPoem('${p.id}')" style="color:#ef4444;">🗑️</button>
      </div>
    </div>
  `).join('');
}

function openAddPoemModal() {
  document.getElementById('editPoemId').value = '';
  document.getElementById('poemModalTitle').textContent = '➕ 添加自定义古诗';
  document.getElementById('poemTitleInput').value = '';
  document.getElementById('poemAuthorInput').value = '';
  document.getElementById('poemDynastyInput').value = '唐';
  document.getElementById('poemLinesInput').value = '';
  openModal('modalPoem');
}

function editPoemModal(id) {
  const p = (state.lastCustomPoems || []).find(it => it.id === id);
  if (!p) return;
  document.getElementById('editPoemId').value = p.id;
  document.getElementById('poemModalTitle').textContent = '✏️ 修改古诗';
  document.getElementById('poemTitleInput').value = p.title;
  document.getElementById('poemAuthorInput').value = p.author;
  document.getElementById('poemDynastyInput').value = p.dynasty;
  document.getElementById('poemLinesInput').value = (p.lines || []).join('\\n');
  openModal('modalPoem');
}

async function savePoem() {
  const id = document.getElementById('editPoemId').value;
  const title = document.getElementById('poemTitleInput').value.trim();
  const author = document.getElementById('poemAuthorInput').value.trim();
  const dynasty = document.getElementById('poemDynastyInput').value.trim();
  const lines = document.getElementById('poemLinesInput').value.split('\\n').map(l => l.trim()).filter(Boolean);
  if (!title || !lines.length) { showToast('标题和诗句不能为空'); return; }
  if (id) {
    await fetch('/api/pocketclaw/mp/poems/custom/' + id, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title, author, dynasty, lines })
    });
    showToast('古诗已修改！');
  } else {
    await fetch('/api/pocketclaw/mp/poems/custom', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title, author, dynasty, lines })
    });
    showToast('新古诗已添加！');
  }
  closeModal('modalPoem');
  loadCustomPoems();
  loadPoems();
}

async function deleteCustomPoem(id) {
  await fetch('/api/pocketclaw/mp/poems/custom/' + id, { method: 'DELETE' });
  showToast('已删除该自定义古诗');
  loadCustomPoems();
  loadPoems();
}

// 8. 每日成语自定义库 CRUD
async function loadCustomIdioms() {
  try {
    const res = await fetch('/api/pocketclaw/mp/idiom/custom');
    const data = await res.json();
    if (data.ok) renderCustomIdioms(data.idioms || []);
  } catch (e) {
    console.error(e);
  }
}

function renderCustomIdioms(idioms) {
  state.lastCustomIdioms = idioms;
  const el = document.getElementById('customIdiomsList');
  if (!el) return;
  if (!idioms.length) {
    el.innerHTML = '<div style="font-size:12px; color:var(--muted); text-align:center; padding:16px;">暂无家长自定义成语，可点击上方按钮添加。</div>';
    return;
  }
  el.innerHTML = idioms.map(it => `
    <div class="custom-item-card">
      <div style="flex:1; min-width:0;">
        <div style="font-weight:700; font-size:14px; color:var(--text);">${it.idiom} <span style="font-size:12px; color:var(--muted); font-weight:normal;">[${it.pinyin}]</span></div>
        <div style="font-size:12px; color:#475569; margin-top:4px;">${it.meaning}</div>
      </div>
      <div style="display:flex; gap:6px;">
        <button class="btn btn-secondary btn-sm" onclick="selectIdiom(${JSON.stringify(it).replace(/"/g, '&quot;')})">📱 设为今日</button>
        <button class="btn btn-secondary btn-sm" onclick="editIdiomModal('${it.id}')">✏️</button>
        <button class="btn btn-secondary btn-sm" onclick="deleteCustomIdiom('${it.id}')" style="color:#ef4444;">🗑️</button>
      </div>
    </div>
  `).join('');
}

function openAddIdiomModal() {
  document.getElementById('editIdiomId').value = '';
  document.getElementById('idiomModalTitle').textContent = '➕ 添加自定义成语';
  document.getElementById('idiomInput').value = '';
  document.getElementById('idiomPinyinInput').value = '';
  document.getElementById('idiomMeaningInput').value = '';
  document.getElementById('idiomSourceInput').value = '';
  document.getElementById('idiomExampleInput').value = '';
  openModal('modalIdiom');
}

function editIdiomModal(id) {
  const it = (state.lastCustomIdioms || []).find(x => x.id === id);
  if (!it) return;
  document.getElementById('editIdiomId').value = it.id;
  document.getElementById('idiomModalTitle').textContent = '✏️ 修改成语';
  document.getElementById('idiomInput').value = it.idiom;
  document.getElementById('idiomPinyinInput').value = it.pinyin || '';
  document.getElementById('idiomMeaningInput').value = it.meaning || '';
  document.getElementById('idiomSourceInput').value = it.source || '';
  document.getElementById('idiomExampleInput').value = it.example || '';
  openModal('modalIdiom');
}

async function saveIdiom() {
  const id = document.getElementById('editIdiomId').value;
  const idiom = document.getElementById('idiomInput').value.trim();
  const pinyin = document.getElementById('idiomPinyinInput').value.trim();
  const meaning = document.getElementById('idiomMeaningInput').value.trim();
  const source = document.getElementById('idiomSourceInput').value.trim();
  const example = document.getElementById('idiomExampleInput').value.trim();
  if (!idiom || !meaning) { showToast('成语与释义不能为空'); return; }
  if (id) {
    await fetch('/api/pocketclaw/mp/idiom/custom/' + id, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ idiom, pinyin, meaning, source, example })
    });
    showToast('成语已修改！');
  } else {
    await fetch('/api/pocketclaw/mp/idiom/custom', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ idiom, pinyin, meaning, source, example })
    });
    showToast('成语已添加！');
  }
  closeModal('modalIdiom');
  loadCustomIdioms();
  loadIdioms();
}

async function deleteCustomIdiom(id) {
  await fetch('/api/pocketclaw/mp/idiom/custom/' + id, { method: 'DELETE' });
  showToast('已删除成语');
  loadCustomIdioms();
  loadIdioms();
}

// 9. 靶向口算库 CRUD
async function loadCustomArith() {
  try {
    const res = await fetch('/api/pocketclaw/mp/arith/custom');
    const data = await res.json();
    if (data.ok) renderCustomArith(data.items || []);
  } catch (e) {
    console.error(e);
  }
}

function renderCustomArith(items) {
  state.lastCustomArith = items;
  const el = document.getElementById('customArithList');
  if (!el) return;
  if (!items.length) {
    el.innerHTML = '<div style="font-size:12px; color:var(--muted); text-align:center; padding:16px;">暂无家长靶向算式，支持输入特定混合运算或简算题。</div>';
    return;
  }
  el.innerHTML = items.map(q => `
    <div class="custom-item-card">
      <div style="flex:1; min-width:0;">
        <div style="font-weight:700; font-size:15px; color:#1e293b; font-family:monospace;">${q.expr} = <span style="color:#059669;">${q.ans}</span></div>
        ${q.hint ? `<div style="font-size:12px; color:#64748b; margin-top:2px;">💡 提示: ${q.hint}</div>` : ''}
      </div>
      <div style="display:flex; gap:6px;">
        <button class="btn btn-secondary btn-sm" onclick="editArithModal('${q.id}')">✏️</button>
        <button class="btn btn-secondary btn-sm" onclick="deleteCustomArith('${q.id}')" style="color:#ef4444;">🗑️</button>
      </div>
    </div>
  `).join('');
}

function openAddArithModal() {
  document.getElementById('editArithId').value = '';
  document.getElementById('arithModalTitle').textContent = '➕ 添加靶向算式';
  document.getElementById('arithExprInput').value = '';
  document.getElementById('arithAnsInput').value = '';
  document.getElementById('arithHintInput').value = '';
  openModal('modalArith');
}

function editArithModal(id) {
  const q = (state.lastCustomArith || []).find(it => it.id === id);
  if (!q) return;
  document.getElementById('editArithId').value = q.id;
  document.getElementById('arithModalTitle').textContent = '✏️ 修改靶向算式';
  document.getElementById('arithExprInput').value = q.expr;
  document.getElementById('arithAnsInput').value = q.ans;
  document.getElementById('arithHintInput').value = q.hint || '';
  openModal('modalArith');
}

async function saveArith() {
  const id = document.getElementById('editArithId').value;
  const expr = document.getElementById('arithExprInput').value.trim();
  const ans = document.getElementById('arithAnsInput').value.trim();
  const hint = document.getElementById('arithHintInput').value.trim();
  if (!expr || !ans) { showToast('算式和答案不能为空'); return; }
  if (id) {
    await fetch('/api/pocketclaw/mp/arith/custom/' + id, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ expr, ans, hint })
    });
    showToast('算式已修改！');
  } else {
    await fetch('/api/pocketclaw/mp/arith/custom', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ expr, ans, hint })
    });
    showToast('算式已录入靶向题库！');
  }
  closeModal('modalArith');
  loadCustomArith();
}

async function deleteCustomArith(id) {
  await fetch('/api/pocketclaw/mp/arith/custom/' + id, { method: 'DELETE' });
  showToast('已删除算式');
  loadCustomArith();
}

// 10. 英语阅读故事 CRUD
function openAddReadingModal() {
  document.getElementById('editReadingId').value = '';
  document.getElementById('readingModalTitle').textContent = '➕ 添加自定义短文故事';
  document.getElementById('readingTitleInput').value = '';
  document.getElementById('readingLevelInput').value = '小学中段 (3-4年级)';
  document.getElementById('readingSentencesInput').value = 'The sun rises in the east. || 太阳从东方升起。\\nBirds sing happily in the trees. || 小鸟在树上欢快地歌唱。';
  openModal('modalReading');
}

function editReadingModal(id) {
  const art = (state.lastReadingArticles || []).find(it => it.id === id);
  if (!art) return;
  document.getElementById('editReadingId').value = art.id;
  document.getElementById('readingModalTitle').textContent = '✏️ 修改阅读短文';
  document.getElementById('readingTitleInput').value = art.title;
  document.getElementById('readingLevelInput').value = art.level || '小学通用';
  const text = (art.sentences || []).map(s => (Array.isArray(s) ? `${s[0]} || ${s[1]}` : `${s.en || ''} || ${s.cn || ''}`)).join('\\n');
  document.getElementById('readingSentencesInput').value = text;
  openModal('modalReading');
}

async function saveReading() {
  const id = document.getElementById('editReadingId').value;
  const title = document.getElementById('readingTitleInput').value.trim();
  const level = document.getElementById('readingLevelInput').value.trim();
  const rawLines = document.getElementById('readingSentencesInput').value.split('\\n');
  const sentences = [];
  for (const l of rawLines) {
    if (!l.trim()) continue;
    const parts = l.split('||');
    if (parts.length >= 2) sentences.push([parts[0].trim(), parts[1].trim()]);
    else sentences.push([l.trim(), '']);
  }
  if (!title || !sentences.length) { showToast('标题与句子内容不能为空'); return; }
  if (id) {
    await fetch('/api/pocketclaw/mp/reading/custom/' + id, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title, level, sentences })
    });
    showToast('短文已修改！');
  } else {
    await fetch('/api/pocketclaw/mp/reading/custom', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title, level, sentences })
    });
    showToast('新短文已保存！');
  }
  closeModal('modalReading');
  loadReadingArticles();
}

async function deleteReading(id) {
  if (!confirm('确定要删除这篇阅读文章吗？')) return;
  await fetch('/api/pocketclaw/mp/reading/custom/' + id, { method: 'DELETE' });
  showToast('短文已删除');
  loadReadingArticles();
}

// 11. 拓展素养模块 (费曼、猜猜看、事实核查、飞花令) CRUD
async function loadThinkingModules() {
  loadFeynmanTopics();
  loadCustomGuesses();
  loadCustomFacts();
  loadCustomFeihua();
}

// 费曼小老师
async function loadFeynmanTopics() {
  try {
    const res = await fetch('/api/pocketclaw/mp/feynman/custom');
    const data = await res.json();
    if (data.ok) renderFeynmanTopics(data.topics || []);
  } catch (e) {
    console.error(e);
  }
}
function renderFeynmanTopics(topics) {
  state.lastFeynmanTopics = topics;
  const el = document.getElementById('feynmanTopicsList');
  if (!el) return;
  if (!topics.length) {
    el.innerHTML = '<div style="font-size:12px; color:var(--muted); text-align:center; padding:12px;">暂无自定义费曼主题，可点上方添加。</div>';
    return;
  }
  el.innerHTML = topics.map(t => `
    <div class="custom-item-card">
      <div style="flex:1; min-width:0;">
        <div style="font-weight:700; font-size:14px; color:var(--text);">${t.topic}</div>
        ${t.hint ? `<div style="font-size:12px; color:var(--muted); margin-top:2px;">引导提示: ${t.hint}</div>` : ''}
      </div>
      <div style="display:flex; gap:6px;">
        <button class="btn btn-secondary btn-sm" onclick="editFeynmanModal('${t.id}')">✏️</button>
        <button class="btn btn-secondary btn-sm" onclick="deleteFeynmanTopic('${t.id}')" style="color:#ef4444;">🗑️</button>
      </div>
    </div>
  `).join('');
}
function openAddFeynmanModal() {
  document.getElementById('editFeynmanId').value = '';
  document.getElementById('feynmanTopicInput').value = '';
  document.getElementById('feynmanHintInput').value = '';
  openModal('modalFeynman');
}
function editFeynmanModal(id) {
  const t = (state.lastFeynmanTopics || []).find(it => it.id === id);
  if (!t) return;
  document.getElementById('editFeynmanId').value = t.id;
  document.getElementById('feynmanTopicInput').value = t.topic;
  document.getElementById('feynmanHintInput').value = t.hint || '';
  openModal('modalFeynman');
}
async function saveFeynman() {
  const id = document.getElementById('editFeynmanId').value;
  const topic = document.getElementById('feynmanTopicInput').value.trim();
  const hint = document.getElementById('feynmanHintInput').value.trim();
  if (!topic) { showToast('讲解主题不能为空'); return; }
  if (id) {
    await fetch('/api/pocketclaw/mp/feynman/custom/' + id, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ topic, hint })
    });
    showToast('讲解主题已修改！');
  } else {
    await fetch('/api/pocketclaw/mp/feynman/custom', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ topic, hint })
    });
    showToast('讲解主题已添加！');
  }
  closeModal('modalFeynman');
  loadFeynmanTopics();
}
async function deleteFeynmanTopic(id) {
  await fetch('/api/pocketclaw/mp/feynman/custom/' + id, { method: 'DELETE' });
  showToast('已删除主题');
  loadFeynmanTopics();
}

// 猜猜看
async function loadCustomGuesses() {
  try {
    const res = await fetch('/api/pocketclaw/mp/guess/custom');
    const data = await res.json();
    if (data.ok) renderCustomGuesses(data.items || []);
  } catch (e) {
    console.error(e);
  }
}
function renderCustomGuesses(items) {
  state.lastGuesses = items;
  const el = document.getElementById('guessesList');
  if (!el) return;
  if (!items.length) {
    el.innerHTML = '<div style="font-size:12px; color:var(--muted); text-align:center; padding:12px;">暂无自定义谜题，可点上方添加。</div>';
    return;
  }
  el.innerHTML = items.map(g => `
    <div class="custom-item-card">
      <div style="flex:1; min-width:0;">
        <div style="font-weight:700; font-size:14px; color:var(--text);">${g.name} <span class="badge" style="font-size:10px;">${g.category}</span></div>
        <div style="font-size:12px; color:var(--muted); margin-top:2px;">线索: ${g.hint}</div>
      </div>
      <div style="display:flex; gap:6px;">
        <button class="btn btn-secondary btn-sm" onclick="editGuessModal('${g.id}')">✏️</button>
        <button class="btn btn-secondary btn-sm" onclick="deleteCustomGuess('${g.id}')" style="color:#ef4444;">🗑️</button>
      </div>
    </div>
  `).join('');
}
function openAddGuessModal() {
  document.getElementById('editGuessId').value = '';
  document.getElementById('guessNameInput').value = '';
  document.getElementById('guessCatInput').value = '动物';
  document.getElementById('guessHintInput').value = '';
  openModal('modalGuess');
}
function editGuessModal(id) {
  const g = (state.lastGuesses || []).find(it => it.id === id);
  if (!g) return;
  document.getElementById('editGuessId').value = g.id;
  document.getElementById('guessNameInput').value = g.name;
  document.getElementById('guessCatInput').value = g.category || '动物';
  document.getElementById('guessHintInput').value = g.hint || '';
  openModal('modalGuess');
}
async function saveGuess() {
  const id = document.getElementById('editGuessId').value;
  const name = document.getElementById('guessNameInput').value.trim();
  const category = document.getElementById('guessCatInput').value.trim();
  const hint = document.getElementById('guessHintInput').value.trim();
  if (!name || !hint) { showToast('谜底与线索不能为空'); return; }
  if (id) {
    await fetch('/api/pocketclaw/mp/guess/custom/' + id, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, category, hint })
    });
    showToast('谜题已修改！');
  } else {
    await fetch('/api/pocketclaw/mp/guess/custom', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, category, hint })
    });
    showToast('谜题已添加！');
  }
  closeModal('modalGuess');
  loadCustomGuesses();
}
async function deleteCustomGuess(id) {
  await fetch('/api/pocketclaw/mp/guess/custom/' + id, { method: 'DELETE' });
  showToast('已删除谜题');
  loadCustomGuesses();
}

// 事实核查
async function loadCustomFacts() {
  try {
    const res = await fetch('/api/pocketclaw/mp/facts/custom');
    const data = await res.json();
    if (data.ok) renderCustomFacts(data.items || []);
  } catch (e) {
    console.error(e);
  }
}
function renderCustomFacts(items) {
  state.lastFacts = items;
  const el = document.getElementById('factsList');
  if (!el) return;
  if (!items.length) {
    el.innerHTML = '<div style="font-size:12px; color:var(--muted); text-align:center; padding:12px;">暂无自定义常识，可点上方添加。</div>';
    return;
  }
  el.innerHTML = items.map(f => `
    <div class="custom-item-card">
      <div style="flex:1; min-width:0;">
        <div style="font-weight:700; font-size:14px; color:var(--text);">${f.kw1} & ${f.kw2} <span class="badge" style="${f.is_true ? 'background:#dcfce7; color:#15803d;' : 'background:#fee2e2; color:#b91c1c;'}">${f.is_true ? '✅ 属实' : '❌ 谣传/误区'}</span></div>
        <div style="font-size:12px; color:var(--muted); margin-top:2px;">${f.note}</div>
      </div>
      <div style="display:flex; gap:6px;">
        <button class="btn btn-secondary btn-sm" onclick="editFactModal('${f.id}')">✏️</button>
        <button class="btn btn-secondary btn-sm" onclick="deleteCustomFact('${f.id}')" style="color:#ef4444;">🗑️</button>
      </div>
    </div>
  `).join('');
}
function openAddFactModal() {
  document.getElementById('editFactId').value = '';
  document.getElementById('factKw1Input').value = '';
  document.getElementById('factKw2Input').value = '';
  document.getElementById('factIsTrueInput').value = 'false';
  document.getElementById('factNoteInput').value = '';
  openModal('modalFact');
}
function editFactModal(id) {
  const f = (state.lastFacts || []).find(it => it.id === id);
  if (!f) return;
  document.getElementById('editFactId').value = f.id;
  document.getElementById('factKw1Input').value = f.kw1;
  document.getElementById('factKw2Input').value = f.kw2;
  document.getElementById('factIsTrueInput').value = f.is_true ? 'true' : 'false';
  document.getElementById('factNoteInput').value = f.note || '';
  openModal('modalFact');
}
async function saveFact() {
  const id = document.getElementById('editFactId').value;
  const kw1 = document.getElementById('factKw1Input').value.trim();
  const kw2 = document.getElementById('factKw2Input').value.trim();
  const is_true = document.getElementById('factIsTrueInput').value === 'true';
  const note = document.getElementById('factNoteInput').value.trim();
  if (!kw1 || !note) { showToast('关键词与解析不能为空'); return; }
  if (id) {
    await fetch('/api/pocketclaw/mp/facts/custom/' + id, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kw1, kw2, is_true, note })
    });
    showToast('科学辨伪已修改！');
  } else {
    await fetch('/api/pocketclaw/mp/facts/custom', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kw1, kw2, is_true, note })
    });
    showToast('科学辨伪已录入！');
  }
  closeModal('modalFact');
  loadCustomFacts();
}
async function deleteCustomFact(id) {
  await fetch('/api/pocketclaw/mp/facts/custom/' + id, { method: 'DELETE' });
  showToast('已删除辨伪条目');
  loadCustomFacts();
}

// 飞花令
async function loadCustomFeihua() {
  try {
    const res = await fetch('/api/pocketclaw/mp/feihua/custom');
    const data = await res.json();
    if (data.ok) renderCustomFeihua(data.feihua || {});
  } catch (e) {
    console.error(e);
  }
}
function renderCustomFeihua(feihuaMap) {
  const el = document.getElementById('feihuaList');
  if (!el) return;
  const keys = Object.keys(feihuaMap);
  if (!keys.length) {
    el.innerHTML = '<div style="font-size:12px; color:var(--muted); text-align:center; padding:12px;">暂无自定义令字对仗句，可点上方添加。</div>';
    return;
  }
  el.innerHTML = keys.map(k => `
    <div style="background:#fff; border:1px solid var(--border); border-radius:8px; padding:10px 14px; margin-bottom:8px;">
      <div style="font-weight:700; color:#b45309; font-size:14px; margin-bottom:6px;">令字：【${k}】</div>
      <div style="display:flex; flex-direction:column; gap:4px;">
        ${(feihuaMap[k] || []).map(v => `
          <div style="display:flex; justify-content:space-between; align-items:center; font-size:13px; color:#334155;">
            <span>${v}</span>
            <button class="btn btn-secondary btn-sm" onclick="deleteFeihuaVerse('${k}', '${v}')" style="color:#ef4444; padding:2px 6px;">✕</button>
          </div>
        `).join('')}
      </div>
    </div>
  `).join('');
}
function openAddFeihuaModal() {
  document.getElementById('feihuaKeywordInput').value = '';
  document.getElementById('feihuaVerseInput').value = '';
  openModal('modalFeihua');
}
async function saveFeihua() {
  const kw = document.getElementById('feihuaKeywordInput').value.trim();
  const verse = document.getElementById('feihuaVerseInput').value.trim();
  if (!kw || !verse) { showToast('令字与诗句不能为空'); return; }
  await fetch('/api/pocketclaw/mp/feihua/custom', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ keyword: kw, verse })
  });
  showToast(`令字【${kw}】对仗句已添加！`);
  closeModal('modalFeihua');
  loadCustomFeihua();
}
async function deleteFeihuaVerse(kw, verse) {
  await fetch('/api/pocketclaw/mp/feihua/custom', {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ keyword: kw, verse })
  });
  showToast('已删除对仗句');
  loadCustomFeihua();
}

// Auto load features on startup
window.addEventListener('DOMContentLoaded', () => {
  loadFeatures();
  loadCustomPoems();
  loadCustomIdioms();
  loadCustomArith();
});
</script></body></html>"""


class _Caller:
    """一次家长 / 平板端请求是谁：管理后台口令（admin），或一把有效的家长 / 设备令牌（role + 名下的孩子）。"""
    __slots__ = ("admin", "role", "allowed")

    def __init__(self, *, admin: bool = False, role: str = "", allowed: Optional[List[str]] = None):
        self.admin = admin
        self.role = role
        self.allowed = list(allowed or [])

    def owns(self, child_id: Optional[str]) -> bool:
        """这个孩子在不在调用方名下。管理后台口令管全部；空串 / 未绑定身份（unbound-*）不属于任何人。
        家长令牌看 children、设备令牌看 child_id——authz.gate.identify 已把两者统一成 allowed。"""
        if not child_id or is_unbound(child_id):
            return False
        return self.admin or child_id in self.allowed


class BindPatch(BaseModel):
    # 必须显式写明绑哪个孩子：本路由不走中间件，没有「按令牌代填第一个孩子」这一步——
    # 多孩子家长省掉它就会绑到一个随手挑的孩子上。缺省 "" → 400「child_id 无效」（而不是 422）。
    child_id: str = ""


class PushPatch(BaseModel):
    title: str = ""
    body: str = ""
    mode: str = "chat"


class CardPatch(BaseModel):
    uid: str
    target: str            # child:<id> / skill:<id> / mode:<name>


class WordPatch(BaseModel):
    word: str
    reading: str = ""
    meaning: str = ""
    example: str = ""


class NotePatch(BaseModel):
    text: str
    tag: str = ""


class TaskPatch(BaseModel):
    text: str
    subject: str = ""


class TaskDonePatch(BaseModel):
    task_id: str = ""
    text: str = ""


class ModulesPatch(BaseModel):
    modules: list = []          # 启用的模块键子集：word/note/task/book


class WordReviewPatch(BaseModel):
    word_id: str
    remembered: bool


class FocusPatch(BaseModel):
    minutes: int = 25
    kind: str = "pomodoro"          # pomodoro（记成长证据）/ countdown（纯计时）
    style: str = "spark"            # spark=火花引线（默认）/ candle=蜡烛
    action: str = "start"           # start / stop / pause / resume


class ParentQuickInputPatch(BaseModel):
    child_id: str = ""
    raw_tasks: str = ""
    dictation_words: str = ""
    clear_existing: bool = False


class TaskCreatePatch(BaseModel):
    child_id: str = ""
    text: str
    subject: str = ""


class DictationSetPatch(BaseModel):
    child_id: str = ""
    words: List[str] = []
    raw_text: str = ""
    title: str = "生字词听写"
    save_as_set: bool = False
    push: bool = True


class PoemTargetPatch(BaseModel):
    child_id: str = ""
    title: str
    push: bool = True


class ArithConfigPatch(BaseModel):
    child_id: str = ""
    grade: int = 2
    count: int = 10
    op_type: str = "all"
    push: bool = True


class VocabWordPatch(BaseModel):
    child_id: str = ""
    word: str
    meaning: str = ""
    example: str = ""
    example_cn: str = ""


class ReadingTargetPatch(BaseModel):
    child_id: str = ""
    article_id: str
    push: bool = True


class OcrWordsPatch(BaseModel):
    child_id: str = ""
    image: str = ""
    image_b64: str = ""
    raw_text: str = ""
    push: bool = True
    target: Literal['', '作业', '习惯', '生字词', '古诗', '成语', '英语生词', '英语短文', '小学新鲜事', '错题', '费曼主题', '猜猜看', '课表', '口算题', '事实核查', '飞花令', '心愿', '亲子留言', '倒计时'] = ''


class OcrReadingPatch(BaseModel):
    child_id: str = ""
    image: str = ""
    image_b64: str = ""
    level: str = "小学英语"
    push: bool = True


class ParentMessagePatch(BaseModel):
    child_id: str = ""
    text: str


class HabitCreatePatch(BaseModel):
    child_id: str = ""
    name: str
    icon: str = "⭐"


class TimetablePatch(BaseModel):
    child_id: str = ""
    timetable: Dict[str, Any] = {}


class MistakePatch(BaseModel):
    child_id: str = ""
    category: str = "arith"
    question: str
    expected: str
    wrong_answer: str = ""


class IdiomPatch(BaseModel):
    child_id: str = ""
    idiom: str
    pinyin: str = ""
    meaning: str = ""
    source: str = ""
    example: str = ""
    push: bool = True


class MpFocusPatch(BaseModel):
    child_id: str = ""
    action: str = "start"           # start / pause / resume / cancel / stop
    minutes: int = 25
    style: str = "spark"            # spark / candle
    kind: str = "pomodoro"          # pomodoro / countdown


class MpModeSwitchPatch(BaseModel):
    child_id: str = ""
    mode: str = "task"
    art_id: str = ""
    sub_mode: str = ""
    speak: bool = False


class StarAddPatch(BaseModel):
    child_id: str = ""
    count: int = 1
    reason: str = "自律奖励"


class WishCreatePatch(BaseModel):
    child_id: str = ""
    title: str
    stars_needed: int = 20
    icon: str = "🎁"


def parse_raw_homework(raw: str) -> List[Tuple[str, str]]:
    """从家长粘贴的微信/钉钉作业通知中提取任务与学科。"""
    lines = raw.strip().splitlines()
    tasks = []
    ignore_keywords = ("今日作业", "作业通知", "各位家长", "请家长", "收到请回复", "完成请打卡", "签字", "打卡要求")
    for line in lines:
        l = line.strip()
        if not l or any(ig in l for ig in ignore_keywords):
            continue
        l = re.sub(r"^(\d+[\.、\s\-]+|\(\d+\)\s*|【\d+】\s*|[①②③④⑤⑥⑦⑧⑨⑩]\s*)", "", l).strip()
        if not l:
            continue
        sub = ""
        for s in ("语文", "数学", "英语", "科学", "道法", "道德与法治", "阅读", "美术", "体育"):
            if l.startswith(s + "：") or l.startswith(s + ":") or l.startswith(f"【{s}】") or l.startswith(f"[{s}]") or l.startswith(s + " "):
                sub = s
                l = re.sub(rf"^(\[{s}\]|【{s}】|{s}[:：\s])\s*", "", l).strip()
                break
        full_text = f"[{sub}] {l}" if sub else l
        tasks.append((full_text, sub))
    return tasks


def parse_dictation_words(raw: str) -> List[Tuple[str, str, str]]:
    """解析生字词：逗号、分号、换行分隔。"""
    tokens = [t.strip() for t in re.split(r"[,，;；\n\r\t]+", raw) if t.strip()]
    result = []
    for w in tokens:
        w = re.sub(r"^\d+[\.、\s]+", "", w).strip()
        if w:
            result.append((w, "", f"{w}的{w[0]}"))
    return result


def register_pocketclaw(app: FastAPI, runtime, *, controls=None,
                        notes_path: Optional[str] = None,
                        devices_path: Optional[str] = None,
                        voice=None, books=None,
                        grade_band: str = "g3_g6", on_focus_done=None, on_focus_today=None,
                        daily_spark=None, qbank=None, strict_identity: bool = True,
                        admin_check: Optional[Callable[[str], bool]] = None,
                        device_authenticate: Optional[Callable[[str, str], Optional[Tuple[str, str]]]] = None,
                        parent_authenticate=None, default_child=None, reading_path=None) -> Dict[str, Any]:
    """在既有 app 上装配 PocketClaw 的 WS + REST。返回其内部单例（便于测试/复用）。

    voice：主链路 VoiceService，用于复用平板同款云端火山 ASR（异步、快）。
    daily_spark / qbank：内容策略两样素材（今日探索题、错题本到期题）——早安推屏与
    「聊聊这个」「错题」语音入口靠它们。None = 不启用，但要在日志里说一声，别悄悄没了。
    strict_identity：P0-3「不信自报」（默认开，生产入口就是这里）——hello 自报的 child_id 不覆盖绑定、
    新设备进按设备隔离的「未绑定」身份、NFC 孩子卡只能在已认证方绑过的孩子之间切、hello 前的帧一律拒。
    关掉只为离线调试；关的时候启动日志会明说。
    admin_check：X-Admin-Token 请求头的值 → 是不是有效的管理后台会话（app.py 传 `_admin_tokens` 的成员判定）。
    None = 不认管理后台口令（只认家长 / 平板令牌），启动日志会说一声。
    """
    registry = DeviceRegistry(path=devices_path, strict=strict_identity)
    if strict_identity:
        log.info("pocketclaw 身份：严格模式（hello/NFC 自报的 child_id 不采信，绑定只认家长/平板令牌）")
    else:
        log.warning("pocketclaw 身份：非严格模式——设备 hello 自报的 child_id 会直接改绑，仅限离线调试")
    notes = NotesStore(path=notes_path)
    if daily_spark is None:
        log.info("pocketclaw: 未传 daily_spark → 便利贴早安「今日探索」推屏与「聊聊这个」不启用")
    if qbank is None:
        log.info("pocketclaw: 未传 qbank → 便利贴「错题」复习入口不启用、早安屏不显示到期错题数")
    # 活跃 WS 会话：device_id → session。给「远程开一轮花火番茄钟」这类**要推给在线设备**的
    # 操作用（推送队列 push/sync 是 T0 的拉模型，L2 固件不轮询它，推不动实时指令）。
    live: Dict[str, Any] = {}

    if admin_check is None:
        log.info("pocketclaw: 未传 admin_check → 管理后台口令不能管理便利贴（只认家长 / 平板令牌）")

    # ── 按设备 / 卡号寻址的家长端操作：处理器自校身份与归属（2026-09-28）──
    # 这几条路由（/devices 列表、bind / push / focus / push_tasks、cards/register）的请求里没有「这台设备是谁家的」，
    # authz 中间件只验得了「令牌有效」，验不了归属；管理后台口令在 guard 档下又到不了处理器（强制模式没带 Bearer 就 401）。
    # 所以整条由这里判（policy._pocket_self_guarded 让中间件放行），判据见模块头「鉴权」一节；
    # 拒不拒与中间件同一口径（authz.gate.deny_or_soft）：强制模式拒，观察模式放行 + ACLSOFT。
    def _deny_or_soft(request: Request, status: int, msg: str, why: str, **extra: Any) -> Optional[JSONResponse]:
        """该拒的一次访问：强制模式 → 返回 status 响应（并留一行 warning）；观察模式 → authz.gate.deny_or_soft 已留 ACLSOFT，
        返回 None＝放行。hard 取 policy.legacy_guarded（这几条都不在 legacy 强制档 → 逃生阀有效，与中间件一致）。"""
        try:
            from authz import gate as _gate, policy as _policy
        except ImportError:
            return JSONResponse({"ok": False, "error": msg}, status_code=status)
        path = request.url.path
        ip = request.client.host if request.client else "-"
        ex = " ".join(f"{k}={str(v)[:40]}" for k, v in extra.items())
        if _gate.deny_or_soft("http", path, why, ip=ip, extra=ex, hard=_policy.legacy_guarded(path)):
            log.warning("pocketclaw %s 被拒 %d（%s）%s ip=%s", path, status, why, ex, ip)
            return JSONResponse({"ok": False, "error": msg}, status_code=status)
        return None

    def _authenticate(request: Request) -> Tuple[Optional[_Caller], Optional[JSONResponse]]:
        """管理后台口令 → 管全部；否则要一把有效的家长 / 设备令牌，缺 / 无效（含已注销家长）→ 401（经 deny_or_soft：
        观察模式放行并留 ACLSOFT，按「名下没有孩子」的调用方往下走）。令牌判据与中间件同一份（authz.gate.check）。"""
        if admin_check is not None:
            try:
                if admin_check(request.headers.get("x-admin-token", "")):
                    return _Caller(admin=True, role="admin"), None
            except Exception as e:  # noqa: BLE001  判不了就当不是管理员（fail closed），并出声
                log.error("pocketclaw 管理后台口令判定失败，按非管理员处理：%s", e)
        if parent_authenticate:
            allowed = parent_authenticate(request.headers.get("authorization", ""))
            if allowed is not None:
                return _Caller(role="parent", allowed=allowed), None
        try:
            from authz import gate as _gate
        except ImportError:
            return None, JSONResponse({"ok": False, "error": "需要有效的管理令牌"}, status_code=401)
        ok, code, err, p, allowed = _gate.check(request.headers.get("authorization", ""), None)
        if not ok:
            denied = _deny_or_soft(request, code, err, err)
            if denied is not None:
                return None, denied
            return _Caller(role="anonymous"), None          # 观察模式：匿名放行（已留 ACLSOFT）
        return _Caller(role=str((p or {}).get("role") or ""), allowed=allowed), None

    def _unavailable(request: Request, what: str) -> JSONResponse:
        """设备表只读降级（registry.degraded）→ 503，家长看得懂的一句话。修前是写入抛 RuntimeError → 500
        「服务器出错」（明确失败但不友好）。降级本身的判定不在这里（DeviceRegistry._load），这里只管怎么说。"""
        log.warning("pocketclaw 设备表只读降级，%s未执行 → 503 path=%s", what, request.url.path)
        return JSONResponse({"ok": False, "error": _MSG_REGISTRY_DOWN}, status_code=503)

    def _own_device(request: Request, who: _Caller, device_id: str, what: str) -> Optional[JSONResponse]:
        """按 device_id 寻址：设备必须已绑定孩子，且那个孩子在调用方名下（管理后台口令管全部）。返回 None＝放行。
        **未绑定 / 不存在的设备只允许绑定流程**（/bind）——它不属于任何家庭，谁都不该往它上面推东西，管理员也一样
        （先绑再推）。对非管理员，不存在 / 未绑定 / 别家的一律 403、同一文案，不给「这个 device_id 存不存在」当探针。"""
        dev = registry.get(device_id)
        if dev is None or is_unbound(dev.child_id):
            return _deny_or_soft(request, 403, _MSG_ADMIN_UNBOUND if who.admin else _MSG_NOT_YOURS,
                                 f"{what}：设备不存在或尚未绑定孩子（只允许绑定流程）", device=device_id, role=who.role)
        if not who.owns(dev.child_id):
            return _deny_or_soft(request, 403, _MSG_NOT_YOURS, f"{what}：设备绑定的孩子不在令牌名下",
                                 device=device_id, bound=dev.child_id, role=who.role)
        return None

    @app.get("/api/pocketclaw/health")
    async def pc_health():
        return {"ok": True, "product": "PocketClaw", "device": "Zectrix Note 4",
                "profile": "epaper_4in2", "tier": "T1",
                "stt": make_stt(voice).__class__.__name__, "tts": make_tts().__class__.__name__,
                "devices": len(registry.list_devices()),
                "daily_spark": daily_spark is not None, "qbank": qbank is not None}

    @app.get("/api/pocketclaw/devices")
    async def pc_devices(request: Request):
        """只列**本家庭**的便利贴：绑定的孩子在这把令牌名下（家长令牌的 children / 设备令牌的 child_id）。
        2026-09-28 之前是全量列出——任何一把有效令牌都能看到所有家庭的设备 id、绑定的孩子、记事聚合，
        而 device_id 正是改绑/推送接口的寻址键。未绑定（unbound-*）的设备不属于任何家庭，不列。
        管理后台口令列全部（含未绑定的——绑定新设备要先看得到它）。
        设备表只读降级 → 503：此时内存里读不到任何绑定，照常回一张空表就是「看起来这家没有便利贴」。"""
        who, denied = _authenticate(request)
        if denied is not None:
            return denied
        if registry.degraded:
            return _unavailable(request, "列出便利贴")
        out = []
        for d in registry.list_devices():
            if not (who.admin or who.owns(d.child_id)):
                continue
            row = d.__dict__.copy()
            row["pending"] = registry.pending_count(d.device_id)
            row["notes_agg"] = notes.aggregate(d.child_id)   # 聚合，无逐字
            out.append(row)
        return {"devices": out}

    @app.post("/api/pocketclaw/devices/{device_id}/bind")
    async def pc_bind(device_id: str, p: BindPatch, request: Request):
        """已认证方（家长小程序 / 平板 / 管理后台）把便利贴绑到孩子——**唯一**建立绑定的入口（P0-3）。
        未绑定的设备只允许这一个动作。

        判据：① 有效令牌或管理后台口令（401）；② 目标孩子在调用方名下（403；原来由中间件按请求体 child_id 校）；
        ③ 设备**已经**绑在另一个真实孩子身上时，改绑方还得能管那个孩子——否则任何家长拿到别人家便利贴的 device_id
        （MAC 派生、局域网可见）就能把它改绑到自家孩子，对方孩子接下来说的话就进了别人家孩子的记忆。
        未绑定 / 存量缺省 kid 的设备不受 ③ 限（同修前）。①②③ 都经 deny_or_soft（观察模式放行 + ACLSOFT，同修前）。
        """
        who, denied = _authenticate(request)
        if denied is not None:
            return denied
        target = (p.child_id or "").strip()
        if not target or is_unbound(target):
            return JSONResponse({"ok": False, "error": "child_id 无效"}, status_code=400)
        if not who.owns(target):
            denied = _deny_or_soft(request, 403, "只能把便利贴绑定到你自己账号下的孩子", "绑定目标孩子不在令牌名下",
                                   device=device_id, target=target, role=who.role)
            if denied is not None:
                return denied
        if registry.degraded:
            return _unavailable(request, "绑定孩子")
        cur = registry.get(device_id)
        cur_child = cur.child_id if cur else ""
        if (registry.strict and cur_child and cur_child != target
                and cur_child != LEGACY_DEFAULT_CHILD and not is_unbound(cur_child)
                and not who.owns(cur_child)):
            denied = _deny_or_soft(request, 403, "这台便利贴已绑定到别的孩子，需由该孩子的家长（或已绑定的平板）来改绑",
                                   "改绑已绑定到另一个孩子的便利贴", device=device_id, bound=cur_child,
                                   target=target, role=who.role)
            if denied is not None:
                return denied
        try:
            registry.upsert(device_id)             # 确保存在（严格模式下新设备先落「未绑定」身份）；校验过了才建档
            d = registry.bind_child(device_id, target)
        except RegistryReadOnly:
            return _unavailable(request, "绑定孩子")
        log.info("pocketclaw 绑定 dev=%s %s → %s（%s）", device_id, cur_child or "-", target, who.role)
        return {"ok": d is not None, "device": (d.__dict__ if d else None)}

    @app.post("/api/pocketclaw/devices/{device_id}/push")
    async def pc_push(device_id: str, p: PushPatch, request: Request):
        """T0 推屏：把一屏内容排进该设备的推送队列。只许推给**自家**已绑定的便利贴（管理后台口令除外）。"""
        who, denied = _authenticate(request)
        if denied is not None:
            return denied
        if registry.degraded:
            return _unavailable(request, "推屏")
        denied = _own_device(request, who, device_id, "往便利贴推屏")
        if denied is not None:
            return denied
        page = display.render_lines(p.title or "留言", p.body, mode=p.mode)
        try:
            item = registry.push_content(device_id, page)
        except RegistryReadOnly:
            return _unavailable(request, "推屏")
        return {"ok": True, "queued": item["id"], "pending": registry.pending_count(device_id)}

    @app.post("/api/pocketclaw/devices/{device_id}/focus")
    async def pc_focus(device_id: str, p: FocusPatch, request: Request):
        """远程开/停一轮花火番茄钟（家长端「陪他专注一会儿」、以及本机自测用——自测带管理后台口令即可）。

        只作用于**在线**设备：便利贴的花火是设备本地跑秒绘屏，人不在跟前就只能从这里发起。
        归属校验排在在线判断**之前**：不然「设备不在线」与 403 的差别本身就能探出别家设备在不在线。
        """
        who, denied = _authenticate(request)
        if denied is not None:
            return denied
        if registry.degraded:
            return _unavailable(request, "远程开专注")      # 读不到绑定就判不了归属
        denied = _own_device(request, who, device_id, "远程开专注")
        if denied is not None:
            return denied
        sess = live.get(device_id)
        if sess is None:
            return {"ok": False, "error": "设备不在线", "online": False}
        act = (p.action or "start").lower()
        now = time.time()
        if act == "start":
            mins = max(1, min(60, int(p.minutes or 25)))
            await sess._start_focus({"kind": p.kind or "pomodoro", "minutes": mins,
                                     "style": p.style or "spark"}, now)
            return {"ok": True, "action": "start", "minutes": mins, "style": p.style or "spark"}
        if act in ("stop", "cancel"):
            await sess._focus_control("cancel", now)
        elif act == "pause":
            await sess._focus_control("pause", now)
        elif act == "resume":
            await sess._focus_control("resume", now)
        else:
            return {"ok": False, "error": f"未知动作 {act}"}
        return {"ok": True, "action": act}

    @app.get("/api/pocketclaw/devices/{device_id}/sync")
    async def pc_sync(device_id: str):
        """T0：设备（或 zectrix-desktop 式推送端）同步，取走待显示内容并清空。

        设备侧路由，**无令牌**（authz.policy 瘦端后缀 /sync）——便利贴固件没有任何凭据可带。残余风险（2026-09-28 记录）：
        知道 device_id（MAC 派生）的人能取走并清空这台设备的待推送内容（家长留言 / 作业清单）。收口要先给固件
        一把设备凭据（改固件，另议）；在库 L2 固件不轮询本路由（只走 WS /pocketclaw）。"""
        registry.upsert(device_id)
        return {"items": registry.drain_content(device_id)}

    @app.post("/api/pocketclaw/cards/register")
    async def pc_card(p: CardPatch, request: Request):
        """登记 / 改一张 NFC 卡的映射（卡上没写 NDEF 时，贴卡按这里登记的目标走）。判据：
          · `child:<id>` 卡指向的孩子必须在调用方名下；
          · 覆盖一张**已经**指向真实孩子的卡，调用方还得能管那个孩子（与 /bind「改绑需能管原孩子」同理——
            否则拿到别人家卡号就能把它的登录卡改掉）；存量缺省 kid 的卡不受此限（同修前）；
          · 这张卡记了主人（card_owners：登记方名下的孩子）→ 调用方名下至少有一个是主人。功能卡
            （mode:/skill:/subject:/word:/deck:）没有孩子维度，修前「有效令牌即可登记」＝谁都能改别人家的单词卡；
          · 没记主人的存量卡 / 新卡：有效令牌即可登记，登记后记上主人。令牌名下一个孩子都没有 → 403
            （这张卡没法归到任何一家，登上去就是一张谁都能改的卡）；
          · 以上 403 都经 deny_or_soft（观察模式放行 + ACLSOFT）；**观察模式放过去的那次不改主人**——主人只随一次
            真正有权的登记转移，免得灰度期一次越权就把卡的归属也让出去；
          · 管理后台口令管全部（不改原主人；改成孩子卡时主人就是那个孩子）。"""
        who, denied = _authenticate(request)
        if denied is not None:
            return denied
        uid = p.uid or ""
        if not uid.strip() or len(uid) > 128:
            return JSONResponse({"ok": False, "error": "卡号无效"}, status_code=400)
        tgt = _card_target(p.target)
        if tgt["kind"] == "child" and (not tgt["value"] or is_unbound(tgt["value"])):
            return JSONResponse({"ok": False, "error": "孩子卡的 child_id 无效"}, status_code=400)
        # 先判（不依赖设备表的）目标，再报只读降级，最后判（要读设备表的）旧映射与主人
        entitled = True                    # 这次登记是不是真正有权（观察模式放过去的越权 → False，不转移主人）
        if tgt["kind"] == "child" and not who.owns(tgt["value"]):
            entitled = False
            denied = _deny_or_soft(request, 403, "这张卡指向的孩子不在你的账号下", "登记指向别家孩子的 NFC 卡",
                                   uid=uid, target_child=tgt["value"], role=who.role)
            if denied is not None:
                return denied
        elif tgt["kind"] != "child" and not who.admin and not who.allowed:
            entitled = False
            denied = _deny_or_soft(request, 403, "当前登录还没有绑定孩子，请先完成亲子绑定",
                                   "名下无孩子的令牌登记功能卡", uid=uid, role=who.role)
            if denied is not None:
                return denied
        if registry.degraded:
            return _unavailable(request, "登记 NFC 卡")
        old = registry.card_target(uid)
        owners = registry.card_owners(uid)
        if (not who.admin and old["kind"] == "child" and old["value"] and old["value"] != LEGACY_DEFAULT_CHILD
                and not is_unbound(old["value"]) and not who.owns(old["value"])):
            entitled = False
            denied = _deny_or_soft(request, 403, "这张卡已登记给别的孩子，需由该孩子的家长来改", "覆盖别家孩子的 NFC 卡",
                                   uid=uid, old_child=old["value"], role=who.role)
            if denied is not None:
                return denied
        if not who.admin and owners and not any(who.owns(c) for c in owners):
            entitled = False
            denied = _deny_or_soft(request, 403, "这张卡已登记在别的家庭名下，需由那家的家长来改", "改别家登记的 NFC 卡",
                                   uid=uid, owners=",".join(owners), role=who.role)
            if denied is not None:
                return denied
        if who.admin:
            new_owners = [tgt["value"]] if tgt["kind"] == "child" else owners
        else:
            new_owners = who.allowed if entitled else owners
        try:
            registry.register_card(uid, p.target, owners=new_owners)
        except RegistryReadOnly:
            return _unavailable(request, "登记 NFC 卡")
        return {"ok": True, "uid": uid, "target": p.target}

    def _resolve_child(child_param: Optional[str] = None) -> str:
        if child_param and child_param.strip():
            c = child_param.strip()
            if c not in ("mp", "undefined", "null", "none"):
                allowed = family_allowed_children.get()
                if allowed is not None and c not in allowed:
                    raise HTTPException(status_code=403, detail='无权访问孩子内容')
                return c
        if default_child:
            return default_child
        devs = registry.list_devices()
        first_dev = devs[0] if devs else None
        return first_dev.child_id if first_dev and not is_unbound(first_dev.child_id) else "kid"

    def _profile_child(request: Request, child_id: Optional[str]):
        child = _resolve_child(child_id)
        if parent_authenticate:
            allowed = parent_authenticate(request.headers.get("authorization", ""))
            if allowed is None or child not in allowed:
                return None
        return child

    @app.get('/api/pocketclaw/mp/child_profile')
    async def get_child_profile(request: Request, child_id: Optional[str] = None):
        child = _profile_child(request, child_id)
        if child is None:
            return JSONResponse({'detail': '无权访问孩子资料'}, status_code=403)
        return JSONResponse({'ok': True, 'profile': notes.get_child_profile(child)}, headers={'Cache-Control': 'no-store'})

    @app.put('/api/pocketclaw/mp/child_profile')
    async def save_child_profile(request: Request, profile: ChildProfile, child_id: Optional[str] = None):
        child = _profile_child(request, child_id)
        if child is None:
            return JSONResponse({'detail': '无权修改孩子资料'}, status_code=403)
        values = profile.model_dump()
        values['name'] = values['name'].strip()
        values['interests'] = values['interests'].strip()
        return JSONResponse({'ok': True, 'profile': notes.set_child_profile(child, values)}, headers={'Cache-Control': 'no-store'})

    @app.get('/api/pocketclaw/mp/presets')
    async def list_grade_presets(category: Literal['words', 'idioms', 'poems', 'english'], grades: str = 'all'):
        from pocketclaw.grade_catalog import get_catalog
        try:
            selected = None if grades == 'all' else ([] if grades == 'none' else [int(g) for g in grades.split(',')])
            items = get_catalog(category, selected)
        except ValueError:
            return JSONResponse({'detail': '请选择一至六年级'}, status_code=400)
        return {'ok': True, 'items': items, 'note': '年级为建议难度，不代表教材的固定编排'}

    @app.post('/api/pocketclaw/mp/presets/import')
    async def import_grade_presets(request: Request, payload: PresetImport):
        from pocketclaw.grade_catalog import get_catalog
        child = _profile_child(request, payload.child_id)
        if child is None:
            return JSONResponse({'detail': '无权修改孩子内容'}, status_code=403)
        catalog = {item['id']: item for item in get_catalog(payload.category)}
        ids = list(dict.fromkeys(payload.ids))
        if any(item_id not in catalog for item_id in ids):
            return JSONResponse({'detail': '预设内容无效'}, status_code=400)
        items = [catalog[item_id] for item_id in ids]
        if not items:
            return {'ok': True, 'imported': 0, 'skipped': 0}
        imported = 0
        if payload.category == 'words':
            words = list(dict.fromkeys(item['word'] for item in items))
            notes.save_dictation_set(child, '分年级精选生字词', [(w, '', '') for w in words])
            imported = len(words)
        elif payload.category == 'poems':
            existing = {p.get('title') for p in notes.list_custom_poems(child)}
            for item in items:
                if item['title'] in existing: continue
                notes.add_custom_poem(child, item['title'], item['author'], item['dynasty'], item['lines'])
                existing.add(item['title']); imported += 1
        elif payload.category == 'english':
            for item in items:
                if notes.find_word(child, item['word']): continue
                notes.add_word(child, item['word'], meaning=item['meaning'], example=item['example'], example_cn=item['example_cn'])
                imported += 1
        else:
            existing = {p.get('idiom') for p in notes.list_custom_idioms(child)}
            for item in items:
                if item['idiom'] in existing: continue
                notes.add_custom_idiom(child, item['idiom'], item.get('pinyin', ''), item.get('meaning', ''), item.get('source', ''), item.get('example', ''))
                existing.add(item['idiom']); imported += 1
        return {'ok': True, 'imported': imported, 'skipped': len(items) - imported}

    _MODE_NAMES = {
        "task": "📋 今日作业",
        "focus": "🔥 番茄专注",
        "screensaver": "🎨 伴学屏保",
        "habit": "🌟 习惯打卡",
        "timetable": "📅 桌面课表",
        "mistakes": "📕 错题消灭",
        "idiom": "📜 每日成语",
        "dictation": "✍️ 生字听写",
        "recite": "📜 课内古诗",
        "arith": "🔢 口算闯关",
        "vocab": "🔤 英语生词",
        "reading": "📖 英语短文",
        "menu": "🧭 功能菜单",
        "chat": "💬 AI学伴",
        "parent_note": "💌 家长留言",
        "feihua": "🌸 诗词飞花令",
        "hanzi": "🔍 错字门诊",
        "feynman": "🎓 费曼讲课",
        "guess": "🕵️ 猜猜看",
        "verify": "⚖️ 事实核查",
    }

    def _get_live_focus_status(child_id: Optional[str] = None) -> Dict[str, Any]:
        now = time.time()
        for s in live.values():
            if child_id is not None and getattr(s, "child_id", None) != child_id:
                continue
            f = getattr(s, "_focus", None)
            if f is not None:
                rem = max(0, int(round(f.remaining(now))))
                return {
                    "active": True,
                    "paused": bool(f.paused),
                    "remaining_s": rem,
                    "total_s": int(f.total_s),
                    "minutes": int(f.minutes),
                    "style": str(f.style or "spark"),
                    "kind": str(f.kind or "pomodoro"),
                }
        return {
            "active": False,
            "paused": False,
            "remaining_s": 0,
            "total_s": 0,
            "minutes": 25,
            "style": "spark",
            "kind": "pomodoro",
        }

    # ── 微信小程序 / 家长端核心 API ──
    @app.get("/api/pocketclaw/mp/overview")
    async def pc_mp_overview(child_id: Optional[str] = None):
        """微信小程序/家长端综合态势概览看板：设备状态、作业进度、古诗、口算、生词、阅读一站式获取。"""
        from pocketclaw import screensaver as _ss
        child = _resolve_child(child_id)
        devs = [d for d in registry.list_devices() if d.child_id == child]
        first_dev = devs[0] if devs else None
        sessions = [s for s in live.values() if getattr(s, "child_id", None) == child]
        tasks = [t.__dict__ for t in notes.list_tasks(child)]
        stats = notes.task_stats(child)
        is_online = bool(sessions)
        bat = None
        cur_mode = "task"
        for s in sessions:
            if getattr(s, "mode", None):
                cur_mode = s.mode
            if s.battery is not None:
                bat = s.battery
                break
        if bat is None and first_dev:
            bat = first_dev.battery

        cur_poem_title = notes.get_poem_target(child)
        cur_poem = practice.POEM_MAP.get(cur_poem_title) or practice.CORE_POEMS[0]

        reading_store = reading.ReadingStore(path=reading_path)
        cur_reading_id = notes.get_reading_article(child)
        cur_reading = reading_store.get_article(cur_reading_id) or reading_store.articles[0]

        focus_min_today = 0
        if on_focus_today:
            try:
                focus_min_today = on_focus_today(child) or 0
            except Exception:
                pass
        focus_status = _get_live_focus_status(child)
        if focus_status["active"]:
            cur_mode = "focus"

        return {
            "ok": True,
            "child_id": child,
            "online": is_online,
            "battery": bat,
            "device_id": first_dev.device_id if first_dev else "",
            "current_mode": cur_mode,
            "current_mode_name": _MODE_NAMES.get(cur_mode, "📋 今日作业"),
            "focus_status": focus_status,
            "tasks": tasks,
            "task_stats": stats,
            "dictation_words": notes.get_dictation_words(child),
            "pending_dictation_count": sum(r.get("status") == "pending" for r in notes.list_dictation_records(child, limit=50)),
            "poem": {
                "title": cur_poem.title,
                "author": cur_poem.author,
                "dynasty": cur_poem.dynasty,
                "lines": cur_poem.lines
            },
            "arith_config": notes.get_arith_config(child),
            "vocab_count": notes.word_count(child),
            "vocab_words": [w.__dict__ for w in notes.list_words(child, limit=100)],
            "reading": {
                "id": cur_reading.id,
                "title": cur_reading.title,
                "level": cur_reading.level,
                "sentence_count": len(cur_reading.sentences),
                "sentences": cur_reading.sentences
            },
            "latest_message": notes.get_latest_parent_message(child),
            "focus_today_min": focus_min_today,
            "habits": [h.__dict__ for h in notes.list_habits(child)],
            "habit_stats": notes.habit_stats(child),
            "timetable_today": notes.get_today_schedule(child),
            "timetable_all": notes.get_timetable(child),
            "mistakes_count": len(notes.list_mistakes(child)),
            "mistakes": [m.__dict__ for m in notes.list_mistakes(child)],
            "daily_idiom": notes.get_daily_idiom(child),
            "star_bank": notes.get_star_bank(child),
            "art_list": _ss.ART_DB,
        }

    @app.post("/api/pocketclaw/mp/screen_snapshot")
    async def pc_screen_snapshot(request: Request, payload: Dict[str, Any]):
        # Temporary single-family access guard. Full family authorization is separate.
        token = request.headers.get("x-admin-token", "")
        parent_allowed = parent_authenticate(request.headers.get("authorization", "")) if parent_authenticate else None
        if parent_allowed is None and (not admin_check or not admin_check(token)):
            return JSONResponse({"detail": "需要管理令牌"}, status_code=401)
        child = _resolve_child(payload.get("child_id"))
        sessions = [s for s in live.values() if s.child_id == child]
        device_id = str(payload.get("device_id") or "")
        if device_id:
            sessions = [s for s in sessions if s.device_id == device_id]
        if not sessions:
            return JSONResponse({"detail": "设备未在线"}, status_code=404)
        if len(sessions) != 1:
            return JSONResponse({"detail": "有多台设备，请选择设备"}, status_code=409)
        try:
            png = await sessions[0].request_screen_snapshot()
        except asyncio.TimeoutError:
            return JSONResponse({"detail": "设备采集超时"}, status_code=504)
        except RuntimeError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=409)
        return {"ok": True, "device_id": sessions[0].device_id,
                "image": base64.b64encode(png).decode("ascii"), "captured_at": time.time()}

    # 1. 作业清单 API
    @app.get("/api/pocketclaw/mp/tasks")
    async def pc_mp_get_tasks(child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        return {"ok": True, "tasks": [t.__dict__ for t in notes.list_tasks(child)], "stats": notes.task_stats(child)}

    @app.post("/api/pocketclaw/mp/tasks")
    async def pc_mp_add_task(p: TaskCreatePatch):
        child = _resolve_child(p.child_id)
        t = notes.add_task(child, p.text, subject=p.subject)
        for s in list(live.values()):
            if s.child_id != child:
                continue
            try:
                if s.mode == "task":
                    await s._show_tasks()
            except Exception:
                pass
        return {"ok": True, "task": t.__dict__, "stats": notes.task_stats(child)}

    @app.post("/api/pocketclaw/mp/tasks/{task_id}/toggle")
    async def pc_mp_toggle_task(task_id: str, child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        t = notes.toggle_task(child, task_id)
        for s in list(live.values()):
            if s.child_id != child:
                continue
            try:
                if s.mode == "task":
                    await s._show_tasks()
            except Exception:
                pass
        return {"ok": t is not None, "task": t.__dict__ if t else None, "stats": notes.task_stats(child)}

    @app.delete("/api/pocketclaw/mp/tasks/{task_id}")
    async def pc_mp_delete_task(task_id: str, child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        ok = notes.delete_task(child, task_id)
        for s in list(live.values()):
            if s.child_id != child:
                continue
            try:
                if s.mode == "task":
                    await s._show_tasks()
            except Exception:
                pass
        return {"ok": ok, "stats": notes.task_stats(child)}

    @app.delete("/api/pocketclaw/mp/tasks/clear_done")
    async def pc_mp_clear_done(child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        cnt = notes.clear_done_tasks(child)
        for s in list(live.values()):
            if s.child_id != child:
                continue
            try:
                if s.mode == "task":
                    await s._show_tasks()
            except Exception:
                pass
        return {"ok": True, "cleared": cnt, "stats": notes.task_stats(child)}

    # 2. 生字听写 API
    @app.get("/api/pocketclaw/mp/dictation")
    async def pc_mp_get_dictation(child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        return {"ok": True, "words": notes.get_dictation_words(child), "title": notes.get_dictation_title(child), "sets": notes.list_dictation_sets(child), "records": notes.list_dictation_records(child)}

    @app.post("/api/pocketclaw/mp/dictation")
    async def pc_mp_set_dictation(p: DictationSetPatch):
        child = _resolve_child(p.child_id)
        words_tuple = []
        if p.raw_text:
            words_tuple = parse_dictation_words(p.raw_text)
        elif p.words:
            words_tuple = [(w, "", f"{w}的{w[0]}") for w in p.words]
        if p.save_as_set:
            notes.save_dictation_set(child, p.title, words_tuple)
        else:
            notes.set_dictation_words(child, words_tuple)
        for s in list(live.values()):
            if s.child_id != child:
                continue
            try:
                if p.push and s.child_id == child:
                    await s._start_dictation(time.time(), speak=True)
            except Exception:
                pass
        return {"ok": True, "count": len(words_tuple), "words": words_tuple}

    @app.post("/api/pocketclaw/mp/dictation/select_set")
    async def pc_mp_select_dictation_set(p: Dict[str, Any]):
        child = _resolve_child(p.get("child_id"))
        if not notes.select_dictation_set(child, str(p.get("set_id", ""))):
            return JSONResponse({"detail": "词单不存在"}, status_code=404)
        if p.get("push"):
            for session in list(live.values()):
                if session.child_id == child and session.mode == "dictation":
                    await session._start_dictation(time.time(), speak=True)
        return {"ok": True, "title": notes.get_dictation_title(child)}

    @app.post("/api/pocketclaw/mp/dictation/delete_set")
    async def pc_mp_delete_dictation_set(p: Dict[str, Any]):
        return {"ok": notes.delete_dictation_set(_resolve_child(p.get("child_id")), str(p.get("set_id", "")))}

    @app.post("/api/pocketclaw/mp/dictation/review")
    async def pc_mp_review_dictation(p: Dict[str, Any]):
        child = _resolve_child(p.get("child_id"))
        rec = next ( (r for r in notes.list_dictation_records(child, limit=50) if r["id"] == p.get("record_id")), None)
        if not rec:
            return JSONResponse({"detail": "听写记录不存在"}, status_code=404)
        if rec.get("status") != "pending":
            return JSONResponse({"detail": "该记录已批改"}, status_code=409)
        words = {w[0] for w in rec["words"]}
        mistakes = p.get("mistakes", [])
        irregular = p.get("irregular_words", [])
        if not isinstance(mistakes, list) or not isinstance(irregular, list) or any(not isinstance(w, str) or w not in words for w in mistakes) or any(not isinstance(w, (str, dict)) or (w if isinstance(w, str) else w.get("word")) not in words for w in irregular):
            return JSONResponse({"detail": "批改词语不在听写记录中"}, status_code=400)
        try:
            stars = max(0, min(10, int(p.get("stars", 1))))
        except (ValueError, TypeError):
            return JSONResponse({"detail": "星星数量无效"}, status_code=400)
        reviewed = notes.review_dictation_record(child, rec["id"], mistakes, stars=stars, comment=str(p.get("comment", "")), irregular_words=irregular)
        return {"ok": True, "record": reviewed}

    @app.post("/api/pocketclaw/mp/dictation/auto_check")
    async def pc_mp_auto_check_dictation(p: Dict[str, Any]):
        child = _resolve_child(p.get("child_id"))
        rec = next((r for r in notes.list_dictation_records(child, limit=50) if r["id"] == p.get("record_id")), None)
        if not rec:
            return JSONResponse({"detail": "听写记录不存在"}, status_code=404)
        if rec.get("status") != "pending":
            return JSONResponse({"detail": "该记录已批改"}, status_code=409)
        image = str(p.get("image", ""))
        image = image.split(",", 1)[1] if image.startswith("data:") and "," in image else image
        try:
            raw_image = base64.b64decode(image, validate=True)
            if not raw_image or len(raw_image) > 8 * 1024 * 1024:
                raise ValueError()
        except (binascii.Error, ValueError):
            return JSONResponse({"detail": "照片数据无效或超过 8 MB"}, status_code=400)
        words = [w[0] for w in rec["words"]]
        if not words or len(set(words)) != len(words) or not getattr(runtime, "llm", None):
            return JSONResponse({"detail": "词单无效或视觉模型不可用，请手动批改"}, status_code=503)
        prompt = ("识别听写照片，对照词单逐项检查。只返回 JSON，格式为 "
                  '{"items":[{"word":"词语","status":"correct|wrong|irregular","reason":"原因","suggestion":"建议"}]}。'
                  "每个词必须恰好一项，无法辨认时不要猜测，返回空 items。词单：" + json.dumps(words, ensure_ascii=False))
        try:
            raw = await asyncio.wait_for(runtime.llm.complete(prompt, "检查这张听写照片", image=image, max_tokens=2000), timeout=30)
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
            result = json.loads(raw)
            items = result["items"]
            if not isinstance(items, list) or len(items) != len(words) or any(not isinstance(i, dict) or i.get("word") not in words or i.get("status") not in ("correct", "wrong", "irregular") for i in items) or len({i["word"] for i in items}) != len(words):
                raise ValueError("incomplete result")
            stars = max(0, min(10, int(p.get("stars", 1))))
        except Exception as exc:
            log.warning("听写照片批改失败: %s", exc)
            return JSONResponse({"detail": "无法可靠识别照片，请重试或手动批改"}, status_code=503)
        mistakes = [i["word"] for i in items if i["status"] == "wrong"]
        irregular = [{"word": i["word"], "reason": str(i.get("reason", "")), "suggestion": str(i.get("suggestion", ""))} for i in items if i["status"] == "irregular"]
        result = {"items": items, "score": round(100 * (len(words) - len(mistakes) - len(irregular)) / len(words))}
        if rec.get("status") != "pending":
            return JSONResponse({"detail": "该记录已批改"}, status_code=409)
        reviewed = notes.review_dictation_record(child, rec["id"], mistakes, stars=stars, irregular_words=irregular, auto_checked=True, check_result=result)
        return {"ok": True, "record": reviewed, "check_result": result, "mistakes": mistakes, "irregular_words": irregular}

    @app.post("/api/pocketclaw/mp/ocr_text")
    async def pc_mp_ocr_text(p: OcrWordsPatch):
        """识别照片中的文字，仅供家长核对，绝不自动写入学习数据。"""
        from io import BytesIO
        from PIL import Image, ImageOps
        image = p.image or p.image_b64 or ''
        image = image.split(',', 1)[1] if image.startswith('data:') and ',' in image else image
        if len(image) > 12 * 1024 * 1024:
            return JSONResponse({'detail': '照片不能超过 8 MB'}, status_code=400)
        try:
            raw = base64.b64decode(image, validate=True)
            if not raw or len(raw) > 8 * 1024 * 1024:
                raise ValueError('invalid size')
            with Image.open(BytesIO(raw)) as img:
                if img.width * img.height > 24_000_000:
                    raise ValueError('image too large')
                img = ImageOps.exif_transpose(img).convert('RGB')
                img.thumbnail((2048, 2048))
                out = BytesIO(); img.save(out, format='JPEG', quality=88)
                image = base64.b64encode(out.getvalue()).decode('ascii')
        except Exception:
            return JSONResponse({'detail': '无法读取照片，请选择有效图片'}, status_code=400)
        llm = getattr(runtime, 'llm', None)
        if not llm or not callable(getattr(llm, '_call_api', None)) or (getattr(llm, 'ocr_mode', '') != 'online' and not any(host in getattr(llm, 'ocr_base_url', '') for host in ('localhost', '127.0.0.1'))):
            return JSONResponse({'detail': '请先配置可用的视觉模型'}, status_code=503)
        try:
            prompt = '请准确提取图片中的文字，保留换行；不补充或猜测，无法辨认处用[无法辨认]标注。只输出识别文字。'
            if p.target:
                prompt = (f'识别图片中与{p.target}相关的内容，只输出JSON对象，不要猜测或补充。'
                          '字段title、body、extra均为字符串，分别对应主要名称、内容、附加信息；没有则为空。'
                          '古诗的extra为作者，成语为拼音，小学新鲜事为地区，错题为学科，猜猜看为分类。口算题的title是算式、body是答案、extra是提示；事实核查的title和body是两个关键词，extra留空，由家长判定真假；飞花令的title是令字、body是诗句；心愿的title是名称、body是所需星数；亲子留言body是留言；倒计时title是事项、body是YYYY-MM-DD日期、extra是鼓励语。'
                          '英语短文body按每行英文 || 中文排列。英语生词另输出entries数组，每项包含word、meaning、example、example_cn字符串；课表另输出days数组，每项为day（周一到周五分别为1到5）和courses（按顺序排列六节课的字符串数组），未识别的课留空。只提取图片中实际出现的内容。')
            text = await asyncio.wait_for(llm._call_api(prompt, '识别图片文字', image=image, max_tokens=2000), timeout=60)
            if not isinstance(text, str) or not text.strip():
                raise ValueError('empty recognition')
            if p.target:
                import json
                import re
                raw_text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text.strip(), flags=re.I)
                parsed = json.loads(raw_text)
                if not isinstance(parsed, dict): raise ValueError('invalid recognition')
                fields = {key: parsed.get(key, '')[:10000] if isinstance(parsed.get(key, ''), str) else '' for key in ('title', 'body', 'extra')}
                if p.target == '事实核查':
                    fields['extra'] = ''  # 真伪必须由家长明确确认
                entries = parsed.get('entries', [])
                if not isinstance(entries, list): entries = []
                fields['entries'] = [{key: row.get(key, '')[:1000] if isinstance(row.get(key, ''), str) else '' for key in ('word', 'meaning', 'example', 'example_cn')} for row in entries[:100] if isinstance(row, dict) and isinstance(row.get('word'), str) and row['word'].strip()]
                days = parsed.get('days', [])
                fields['days'] = [{'day': row['day'], 'courses': [c[:100] if isinstance(c, str) else '' for c in row['courses'][:6]]} for row in days[:5] if isinstance(row, dict) and type(row.get('day')) is int and 1 <= row['day'] <= 5 and isinstance(row.get('courses'), list)] if isinstance(days, list) else []
                return JSONResponse({'ok': True, 'fields': fields}, headers={'Cache-Control': 'no-store'})
            return JSONResponse({'ok': True, 'text': text.strip()}, headers={'Cache-Control': 'no-store'})
        except Exception:
            return JSONResponse({'detail': '照片识别失败，请重试或手动输入'}, status_code=503)

    @app.post("/api/pocketclaw/mp/ocr_words")
    async def pc_mp_ocr_words(p: OcrWordsPatch):
        child = _resolve_child(p.child_id)
        img_raw = p.image or p.image_b64 or ""
        img_b64 = img_raw.split(",", 1)[1] if img_raw.startswith("data:") else img_raw
        words_tuple: List[Tuple[str, str, str]] = []

        if p.raw_text:
            words_tuple = parse_dictation_words(p.raw_text)
        elif img_b64:
            if getattr(runtime, "llm", None):
                try:
                    sys_prompt = (
                        "你是一位专业的小学名师助手。请从用户上传的课本生字表、词单或作业照片中，提取所有需要听写或抄写的生字词。\n"
                        "规则：\n"
                        "1. 仅提取字词本身，过滤掉课文标题、题号、页码等无关文字。\n"
                        "2. 每行一个词语，格式为「词语, 拼音, 组词提示」，例如：温暖, wēn nuǎn, 温暖的温\n"
                        "3. 直接输出生字词列表，不要输出 markdown 标记或任何其他客套话。"
                    )
                    raw = await asyncio.wait_for(
                        runtime.llm.complete(sys_prompt, "提取这张生字表图片里的全部生字词", image=img_b64, max_tokens=1000),
                        timeout=60.0
                    )
                    if raw and raw.strip():
                        lines = [l.strip() for l in raw.strip().splitlines() if l.strip()]
                        for l in lines:
                            parts = [pt.strip() for pt in re.split(r"[,，;；]+", l) if pt.strip()]
                            if parts:
                                w = re.sub(r"^\d+[\.、\s]+", "", parts[0]).strip()
                                py = parts[1] if len(parts) > 1 else ""
                                hint = parts[2] if len(parts) > 2 else f"{w}的{w[0]}"
                                if w:
                                    words_tuple.append((w, py, hint))
                except Exception as e:
                    log.warning("OCR 提取生字词异常: %s", e)
            if not words_tuple:
                return JSONResponse({"detail": "无法识别照片，请检查视觉模型配置或手动输入"}, status_code=503)

        if p.push and words_tuple:
            notes.set_dictation_words(child, words_tuple)
            for s in list(live.values()):
                if s.child_id != child:
                    continue
                try:
                    if s.mode == "dictation":
                        await s._start_dictation(time.time(), speak=True)
                except Exception:
                    pass

        raw_str = ", ".join(w[0] for w in words_tuple)
        return {
            "ok": True,
            "count": len(words_tuple),
            "words": words_tuple,
            "raw_text": raw_str
        }

    # 3. 古诗背诵 API
    @app.get("/api/pocketclaw/mp/poems")
    async def pc_mp_get_poems(child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        cur = notes.get_poem_target(child)
        return {
            "ok": True,
            "current_target": cur,
            "poems": [{"title": p.title, "author": p.author, "dynasty": p.dynasty, "lines": p.lines} for p in practice.CORE_POEMS]
        }

    @app.post("/api/pocketclaw/mp/poems/target")
    async def pc_mp_set_poem_target(p: PoemTargetPatch):
        child = _resolve_child(p.child_id)
        notes.set_poem_target(child, p.title)
        for s in list(live.values()):
            if s.child_id != child:
                continue
            try:
                if p.push or s.mode == "recite":
                    await s._start_recite(p.title, time.time())
            except Exception:
                pass
        return {"ok": True, "target": p.title}

    # 4. 口算闯关配置 API
    @app.get("/api/pocketclaw/mp/arith")
    async def pc_mp_get_arith(child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        return {"ok": True, "config": notes.get_arith_config(child)}

    @app.post("/api/pocketclaw/mp/arith")
    async def pc_mp_set_arith(p: ArithConfigPatch):
        child = _resolve_child(p.child_id)
        cfg = {"grade": p.grade, "count": p.count, "op_type": p.op_type}
        notes.set_arith_config(child, cfg)
        pushed = 0
        for s in list(live.values()):
            if s.child_id != child:
                continue
            try:
                if p.push or s.mode == "arith":
                    await s._start_arith(time.time())
                    pushed += 1
            except Exception:
                pass
        return {"ok": True, "config": cfg, "pushed": pushed}

    # 5. 英语生词本 API
    @app.get("/api/pocketclaw/mp/vocab")
    async def pc_mp_get_vocab(child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        notes.ensure_seeded(child)
        words = notes.list_words(child, limit=200)
        return {"ok": True, "words": [w.__dict__ for w in words]}

    @app.post("/api/pocketclaw/mp/vocab")
    async def pc_mp_add_vocab(p: VocabWordPatch):
        child = _resolve_child(p.child_id)
        w = notes.add_word(child, p.word, meaning=p.meaning, example=p.example, example_cn=p.example_cn)
        return {"ok": True, "word": w.__dict__}

    @app.delete("/api/pocketclaw/mp/vocab/{word_id}")
    async def pc_mp_delete_vocab(word_id: str, child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        ok = notes.delete_word(child, word_id)
        return {"ok": ok}

    # 6. 英语阅读篇目 API
    @app.get("/api/pocketclaw/mp/reading")
    async def pc_mp_get_reading(child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        store = reading.ReadingStore(path=reading_path)
        cur_id = notes.get_reading_article(child)
        return {
            "ok": True,
            "current_target": cur_id,
            "articles": [{
                "id": a.id, "title": a.title, "level": a.level,
                "sentences": a.sentences
            } for a in store.articles]
        }

    @app.post("/api/pocketclaw/mp/reading/target")
    async def pc_mp_set_reading_target(p: ReadingTargetPatch):
        child = _resolve_child(p.child_id)
        notes.set_reading_article(child, p.article_id)
        for s in list(live.values()):
            if s.child_id != child:
                continue
            try:
                if p.push or s.mode == "reading":
                    await s._start_reading(time.time(), speak=True)
            except Exception:
                pass
        return {"ok": True, "article_id": p.article_id}

    @app.post("/api/pocketclaw/mp/ocr_reading")
    async def pc_mp_ocr_reading(p: OcrReadingPatch):
        child = _resolve_child(p.child_id)
        img_raw = p.image or p.image_b64 or ""
        img_b64 = img_raw.split(",", 1)[1] if img_raw.startswith("data:") else img_raw

        art_id = f"art_photo_{int(time.time())}"
        title = "My English Story"
        level = p.level or "小学英语"
        sentences: List[Tuple[str, str]] = []

        if img_b64 and getattr(runtime, "llm", None):
            try:
                sys_prompt = (
                    "你是一位专业的小学英语名师。请从用户拍摄上传的英语绘本、课文或短文照片中，整理提取出标准的分级阅读篇目。\n"
                    "请严格按照以下 JSON 格式输出，不要输出任何其他说明或 markdown 格式：\n"
                    "{\n"
                    '  "title": "文章英文标题",\n'
                    '  "level": "三年级起步 或 四-五年级 或 六年级拓展",\n'
                    '  "sentences": [\n'
                    '    {"en": "第一句英文", "cn": "第一句中文翻译"},\n'
                    '    {"en": "第二句英文", "cn": "第二句中文翻译"}\n'
                    "  ]\n"
                    "}"
                )
                raw = await asyncio.wait_for(
                    runtime.llm.complete(sys_prompt, "识别并整理图片中的英文短文", image=img_b64, max_tokens=1500),
                    timeout=60.0
                )
                if raw and raw.strip():
                    clean_json = re.sub(r"^```(?:json)?\s*", "", raw.strip())
                    clean_json = re.sub(r"\s*```$", "", clean_json)
                    data = json.loads(clean_json)
                    if isinstance(data, dict):
                        title = str(data.get("title") or title)
                        level = str(data.get("level") or level)
                        for item in data.get("sentences", []):
                            en = str(item.get("en", "")).strip()
                            cn = str(item.get("cn", "")).strip()
                            if en:
                                sentences.append((en, cn))
            except Exception as e:
                log.warning("OCR 提取英文短文异常: %s", e)

        if not sentences:
            return JSONResponse({"detail": "无法识别照片，请检查视觉模型配置或重试"}, status_code=503)

        art = reading.ReadingArticle(id=art_id, title=title, level=level, sentences=sentences)
        store = reading.ReadingStore(path=reading_path)
        store.add_article(art)

        if p.push:
            notes.set_reading_article(child, art.id)
            for s in list(live.values()):
                if s.child_id != child:
                    continue
                try:
                    if s.mode == "reading":
                        await s._start_reading(time.time(), speak=True)
                except Exception:
                    pass

        return {
            "ok": True,
            "article_id": art.id,
            "title": art.title,
            "level": art.level,
            "sentences": art.sentences,
            "count": len(art.sentences)
        }

    # 7. 亲子留言便签推屏 API
    @app.post("/api/pocketclaw/mp/message")
    async def pc_mp_send_message(p: ParentMessagePatch):
        child = _resolve_child(p.child_id)
        now = time.time()
        msg = notes.add_parent_message(child, p.text, now=now)
        ts_str = time.strftime("%H:%M", time.localtime(now))
        for s in list(live.values()):
            if s.child_id != child:
                continue
            try:
                await s._show_parent_message(p.text, ts_str)
            except Exception as e:
                log.warning("推屏亲子留言失败: %s", e)
        return {"ok": True, "message": msg}

    # 8. 学习报告与成就勋章 API
    @app.get("/api/pocketclaw/mp/report")
    async def pc_mp_get_report(child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        ts = notes.task_stats(child)
        words_cnt = notes.word_count(child)
        dict_words = len(notes.get_dictation_words(child))
        focus_min_today = 0
        if on_focus_today:
            try:
                focus_min_today = on_focus_today(child) or 0
            except Exception:
                pass

        badges = [
            {"id": "b_discipline", "title": "自律小标兵", "icon": "⭐", "desc": "坚持打勾每日任务", "unlocked": ts["done"] > 0},
            {"id": "b_focus", "title": "番茄专注达人", "icon": "🔥", "desc": "完成深度学习专注", "unlocked": focus_min_today >= 25},
            {"id": "b_recite", "title": "古诗小名士", "icon": "📜", "desc": "熟练掌握课内必背古诗", "unlocked": True},
            {"id": "b_vocab", "title": "单词收藏家", "icon": "🔤", "desc": "累积英语词汇储备", "unlocked": words_cnt >= 5},
            {"id": "b_reading", "title": "阅读小先锋", "icon": "📖", "desc": "逐句跟读纯正英语", "unlocked": True},
        ]
        return {
            "ok": True,
            "child_id": child,
            "task_completion_rate": int(ts["done"] / ts["total"] * 100) if ts["total"] > 0 else 100,
            "task_stats": ts,
            "focus_minutes": focus_min_today,
            "dictation_words_count": dict_words,
            "vocab_count": words_cnt,
            "badges": badges
        }

    # 9. 好习惯打卡 API
    @app.get("/api/pocketclaw/mp/habits")
    async def pc_mp_get_habits(child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        return {"ok": True, "habits": [h.__dict__ for h in notes.list_habits(child)], "stats": notes.habit_stats(child)}

    @app.post("/api/pocketclaw/mp/habits")
    async def pc_mp_add_habit(p: HabitCreatePatch):
        child = _resolve_child(p.child_id)
        h = notes.add_habit(child, p.name, icon=p.icon)
        for s in list(live.values()):
            if s.child_id != child:
                continue
            try:
                if s.mode == "habit":
                    habits = notes.list_habits(child)
                    page = display.render_habits(habits, getattr(s, "_habit_idx", 0), footer={"battery": s.battery}, partial=True)
                    await s._display(page)
            except Exception:
                pass
        return {"ok": True, "habit": h.__dict__, "stats": notes.habit_stats(child)}

    @app.post("/api/pocketclaw/mp/habits/{habit_id}/toggle")
    async def pc_mp_toggle_habit(habit_id: str, child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        h = notes.toggle_habit(child, habit_id)
        for s in list(live.values()):
            if s.child_id != child:
                continue
            try:
                if s.mode == "habit":
                    habits = notes.list_habits(child)
                    page = display.render_habits(habits, getattr(s, "_habit_idx", 0), footer={"battery": s.battery}, partial=True)
                    await s._display(page)
            except Exception:
                pass
        return {"ok": h is not None, "habit": h.__dict__ if h else None, "stats": notes.habit_stats(child)}

    @app.delete("/api/pocketclaw/mp/habits/{habit_id}")
    async def pc_mp_delete_habit(habit_id: str, child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        ok = notes.delete_habit(child, habit_id)
        for s in list(live.values()):
            if s.child_id != child:
                continue
            try:
                if s.mode == "habit":
                    habits = notes.list_habits(child)
                    page = display.render_habits(habits, getattr(s, "_habit_idx", 0), footer={"battery": s.battery}, partial=True)
                    await s._display(page)
            except Exception:
                pass
        return {"ok": ok, "stats": notes.habit_stats(child)}

    # 9.1 星星存折与心愿兑换商场 API
    @app.get("/api/pocketclaw/mp/stars")
    async def pc_mp_get_stars(child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        return {"ok": True, "bank": notes.get_star_bank(child)}

    @app.post("/api/pocketclaw/mp/stars/add")
    async def pc_mp_add_stars(p: StarAddPatch):
        child = _resolve_child(p.child_id)
        new_balance = notes.add_stars(child, p.count, reason=p.reason)
        reason_str = f"（{p.reason}）" if p.reason else ""
        msg_text = f"太棒啦！家长奖励了 {p.count} 颗星星⭐{reason_str}！当前共有 {new_balance} 颗星！"
        for s in list(live.values()):
            if s.child_id != child:
                continue
            try:
                await s._show_parent_message(msg_text, ts_str=time.strftime("%H:%M"))
            except Exception:
                pass
        return {"ok": True, "balance": new_balance, "bank": notes.get_star_bank(child)}

    @app.post("/api/pocketclaw/mp/stars/wishes")
    async def pc_mp_add_wish(p: WishCreatePatch):
        child = _resolve_child(p.child_id)
        w = notes.add_wish(child, p.title, p.stars_needed, icon=p.icon)
        return {"ok": True, "wish": w, "bank": notes.get_star_bank(child)}

    @app.post("/api/pocketclaw/mp/stars/wishes/{wish_id}/redeem")
    async def pc_mp_redeem_wish(wish_id: str, child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        res = notes.redeem_wish(child, wish_id)
        if res.get("ok"):
            wish = res.get("wish", {})
            title = wish.get("title", "心愿")
            stars_needed = wish.get("stars_needed", 0)
            msg_text = f"恭喜宝贝！成功兑换心愿：【{title}】（扣减{stars_needed}星）！继续自律加油哦！"
            for s in list(live.values()):
                if s.child_id != child:
                    continue
                try:
                    await s._show_parent_message(msg_text, ts_str=time.strftime("%H:%M"))
                except Exception:
                    pass
        return res

    @app.delete("/api/pocketclaw/mp/stars/wishes/{wish_id}")
    async def pc_mp_delete_wish(wish_id: str, child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        ok = notes.delete_wish(child, wish_id)
        return {"ok": ok, "bank": notes.get_star_bank(child)}

    # 10. 桌面每周课表与备忘 API
    @app.get("/api/pocketclaw/mp/timetable")
    async def pc_mp_get_timetable(child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        return {"ok": True, "timetable": notes.get_timetable(child), "today": notes.get_today_schedule(child)}

    @app.post("/api/pocketclaw/mp/timetable")
    async def pc_mp_set_timetable(p: TimetablePatch):
        child = _resolve_child(p.child_id)
        if p.timetable:
            notes.set_timetable(child, p.timetable)
        for s in list(live.values()):
            if s.child_id != child:
                continue
            try:
                if s.mode == "timetable":
                    await s._show_timetable(time.time(), weekday=getattr(s, "_tt_day", 0))
            except Exception:
                pass
        return {"ok": True, "timetable": notes.get_timetable(child)}

    @app.get("/api/pocketclaw/mp/timetable/today")
    async def pc_mp_get_timetable_today(child_id: Optional[str] = None, weekday: Optional[int] = None):
        child = _resolve_child(child_id)
        return {"ok": True, "schedule": notes.get_today_schedule(child, weekday=weekday)}

    # 11. 错题消灭本 API
    @app.get("/api/pocketclaw/mp/mistakes")
    async def pc_mp_get_mistakes(child_id: Optional[str] = None, category: Optional[str] = None):
        child = _resolve_child(child_id)
        items = notes.list_mistakes(child, category=category)
        return {"ok": True, "count": len(items), "mistakes": [m.__dict__ for m in items]}

    @app.post("/api/pocketclaw/mp/mistakes")
    async def pc_mp_add_mistake(p: MistakePatch):
        child = _resolve_child(p.child_id)
        m = notes.add_mistake(child, p.category, p.question, p.expected, wrong_answer=p.wrong_answer)
        for s in list(live.values()):
            if s.child_id != child:
                continue
            try:
                if s.mode == "mistakes":
                    items = notes.list_mistakes(child)
                    page = display.render_mistakes(items, getattr(s, "_mistake_idx", 0), footer={"battery": s.battery}, partial=True)
                    await s._display(page)
            except Exception:
                pass
        return {"ok": True, "mistake": m.__dict__}

    @app.delete("/api/pocketclaw/mp/mistakes/{mistake_id}")
    async def pc_mp_delete_mistake(mistake_id: str, child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        ok = notes.delete_mistake(child, mistake_id)
        return {"ok": ok}

    @app.delete("/api/pocketclaw/mp/mistakes")
    async def pc_mp_clear_mistakes(child_id: Optional[str] = None, category: Optional[str] = None):
        child = _resolve_child(child_id)
        cnt = notes.clear_mistakes(child, category=category)
        return {"ok": True, "cleared": cnt}

    # 12. 每日国学成语 API
    @app.get("/api/pocketclaw/mp/idiom")
    async def pc_mp_get_idiom(child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        return {
            "ok": True,
            "current": notes.get_daily_idiom(child),
            "all": notes.list_idioms()
        }

    @app.post("/api/pocketclaw/mp/idiom/target")
    async def pc_mp_set_idiom(p: IdiomPatch):
        child = _resolve_child(p.child_id)
        idiom_data = {
            "idiom": p.idiom.strip(),
            "pinyin": p.pinyin.strip(),
            "meaning": p.meaning.strip(),
            "source": p.source.strip(),
            "example": p.example.strip(),
        }
        notes.set_daily_idiom(child, idiom_data)
        for s in list(live.values()):
            if s.child_id != child:
                continue
            try:
                if p.push or s.mode == "idiom":
                    await s._show_daily_idiom(time.time())
            except Exception:
                pass
        return {"ok": True, "idiom": idiom_data}

    # 13. 伴学屏保模式管理与世界名画推屏 API
    @app.get("/api/pocketclaw/mp/screensaver")
    async def pc_mp_get_screensaver(child_id: Optional[str] = None):
        from pocketclaw import screensaver as _ss
        child = _resolve_child(child_id)
        return {
            "ok": True,
            "config": notes.get_screensaver_config(child),
            "countdowns": notes.list_countdown_events(child),
            "art_list": _ss.ART_DB,
            "quotes_list": _ss.QUOTES_DB,
            "poems_list": _ss.POEMS_DB,
        }

    @app.post("/api/pocketclaw/mp/screensaver/push")
    async def pc_mp_push_screensaver(p: Dict[str, Any]):
        child = _resolve_child(p.get("child_id"))
        mode = p.get("mode")
        art_id = p.get("art_id")
        idx = p.get("idx")
        speak = bool(p.get("speak", False))
        pushed = 0
        now = time.time()
        sessions = [s for s in live.values() if s.child_id == child]
        for s in sessions:
            try:
                await s._start_screensaver(now, mode=mode, art_id=art_id, idx=idx, speak=speak)
                pushed += 1
            except Exception as e:
                log.warning("pc_mp_push_screensaver failed: %s", e)
        return {"ok": True, "pushed": pushed, "online": bool(sessions), "mode": mode or ("art" if art_id else "cycle"), "art_id": art_id}

    @app.get("/api/pocketclaw/mp/art_preview/{art_id}")
    async def pc_mp_art_preview(art_id: str):
        """将 400×300 4bpp(16灰阶) 或 1bpp 名画位图实时渲染为 PNG 预览图，供家长端网页和微信小程序展示。"""
        from fastapi.responses import Response
        from pocketclaw import screensaver as _ss
        import io
        clean_id = re.sub(r"[^a-zA-Z0-9_\-]", "", art_id.replace(".png", ""))
        raw_bytes = _ss.get_art_bitmap(clean_id)
        if not raw_bytes:
            return Response(status_code=404)
        try:
            from PIL import Image
            w, h = 400, 300
            if len(raw_bytes) == 60000:
                gray = bytearray(w * h)
                for i, b in enumerate(raw_bytes):
                    hi = (b >> 4) & 0x0F
                    lo = b & 0x0F
                    gray[2 * i] = hi * 17
                    gray[2 * i + 1] = lo * 17
                img = Image.frombytes("L", (w, h), bytes(gray))
            else:
                gray = bytearray(w * h)
                for y in range(h):
                    for x in range(w):
                        b = raw_bytes[y * 50 + (x // 8)]
                        bit = (b >> (7 - (x % 8))) & 1
                        gray[y * w + x] = 0 if bit else 255
                img = Image.frombytes("L", (w, h), bytes(gray))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return Response(content=buf.getvalue(), media_type="image/png",
                            headers={"Cache-Control": "public, max-age=3600"})
        except Exception as e:
            log.warning("pc_mp_art_preview error: %s", e)
            return Response(status_code=500)

    # 13.1 远程番茄钟专注控制 API (家长端网页 & 微信小程序通用)
    @app.get("/api/pocketclaw/mp/focus")
    async def pc_mp_get_focus(child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        focus_min_today = 0
        if on_focus_today:
            try:
                focus_min_today = on_focus_today(child) or 0
            except Exception:
                pass
        return {
            "ok": True,
            "online": any(s.child_id == child for s in live.values()),
            "focus_today_min": focus_min_today,
            "focus_status": _get_live_focus_status(child),
        }

    @app.post("/api/pocketclaw/mp/focus")
    async def pc_mp_control_focus(p: MpFocusPatch):
        child = _resolve_child(p.child_id)
        act = (p.action or "start").lower()
        if act not in ("start", "stop", "cancel", "pause", "resume"):
            return JSONResponse({"detail": "无效的专注操作"}, status_code=400)
        now = time.time()
        pushed = 0
        mins = max(1, min(60, int(p.minutes or 25)))
        style = p.style if p.style in ("spark", "candle") else "spark"
        kind = p.kind if p.kind in ("pomodoro", "countdown") else "pomodoro"
        for s in list(live.values()):
            if s.child_id != child:
                continue
            try:
                if act == "start":
                    await s._start_focus({"kind": kind, "minutes": mins, "style": style}, now)
                elif act in ("stop", "cancel"):
                    await s._focus_control("cancel", now)
                elif act == "pause":
                    await s._focus_control("pause", now)
                elif act == "resume":
                    await s._focus_control("resume", now)
                pushed += 1
            except Exception as e:
                log.warning("pc_mp_control_focus error: %s", e)
        focus_min_today = 0
        if on_focus_today:
            try:
                focus_min_today = on_focus_today(child) or 0
            except Exception:
                pass
        return {
            "ok": True,
            "pushed": pushed,
            "online": any(s.child_id == child for s in live.values()),
            "action": act,
            "focus_today_min": focus_min_today,
            "focus_status": _get_live_focus_status(child),
        }

    # 13.2 远程切换墨水屏当前模式 API (一键推屏展示指定模块)
    @app.post("/api/pocketclaw/mp/mode")
    async def pc_mp_switch_mode(p: MpModeSwitchPatch):
        child = _resolve_child(p.child_id)
        mode = (p.mode or "task").strip()
        now = time.time()
        pushed = 0
        for s in list(live.values()):
            if s.child_id != child:
                continue
            try:
                if mode == "screensaver" and (p.art_id or p.sub_mode):
                    await s._start_screensaver(now, mode=p.sub_mode or ("art" if p.art_id else None),
                                               art_id=p.art_id or None, speak=p.speak)
                else:
                    await s._switch_to_mode(mode, now)
                pushed += 1
            except Exception as e:
                log.warning("pc_mp_switch_mode error: %s", e)
        return {
            "ok": True,
            "mode": mode,
            "mode_name": _MODE_NAMES.get(mode, mode),
            "pushed": pushed,
            "online": any(s.child_id == child for s in live.values()),
        }

    @app.post("/api/pocketclaw/mp/screensaver/config")
    async def pc_mp_set_screensaver_config(p: Dict[str, Any]):
        child = _resolve_child(p.get("child_id"))
        cur = notes.get_screensaver_config(child)
        if "default_mode" in p and p["default_mode"] is not None:
            cur["default_mode"] = p["default_mode"]
        if "auto_timeout_minutes" in p and p["auto_timeout_minutes"] is not None:
            cur["auto_timeout_minutes"] = int(p["auto_timeout_minutes"])
        if "enabled" in p and p["enabled"] is not None:
            cur["enabled"] = bool(p["enabled"])
        notes.set_screensaver_config(child, cur)
        push_now = bool(p.get("push", False))
        for s in list(live.values()):
            if s.child_id != child:
                continue
            try:
                if push_now or s.mode == "screensaver":
                    await s._start_screensaver(time.time(), mode=cur.get("default_mode"), speak=False)
            except Exception:
                pass
        return {"ok": True, "config": cur}

    @app.post("/api/pocketclaw/mp/screensaver/countdown")
    async def pc_mp_add_countdown(p: Dict[str, Any]):
        child = _resolve_child(p.get("child_id"))
        title = p.get("title", "").strip()
        target_date = p.get("target_date", "").strip()
        slogan = p.get("slogan", "").strip()
        ev = notes.add_countdown_event(child, title, target_date, slogan)
        return {"ok": True, "event": ev}

    @app.delete("/api/pocketclaw/mp/screensaver/countdown/{event_id}")
    async def pc_mp_delete_countdown(event_id: str, child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        ok = notes.delete_countdown_event(child, event_id)
        return {"ok": ok}

    # 14. 导航功能菜单智能排序与家长端管理 API
    @app.get("/api/pocketclaw/mp/menu")
    async def pc_mp_get_menu(child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        sorted_items = notes.get_sorted_menu_items(child)
        clicks = notes.get_menu_clicks(child)
        custom_order = notes.get_custom_menu_order(child)
        return {
            "ok": True,
            "items": [
                {
                    "name": name,
                    "desc": desc,
                    "mode": mode,
                    "clicks": clicks.get(mode, 0),
                }
                for name, desc, mode in sorted_items
            ],
            "is_custom": bool(custom_order),
        }

    @app.post("/api/pocketclaw/mp/menu/order")
    async def pc_mp_set_menu_order(p: Dict[str, Any]):
        child = _resolve_child(p.get("child_id"))
        order = p.get("order", [])
        if isinstance(order, list):
            notes.set_custom_menu_order(child, order)
        return {"ok": True, "custom_order": notes.get_custom_menu_order(child)}

    @app.post("/api/pocketclaw/mp/menu/reset")
    async def pc_mp_reset_menu_order(p: Dict[str, Any]):
        child = _resolve_child(p.get("child_id"))
        reset_clicks = bool(p.get("reset_clicks", False))
        notes.reset_menu_order(child, reset_clicks=reset_clicks)
        return {"ok": True}

    # ── 孩子端：记想法 / 单词卡（逐字只在 child 维度）──
    @app.get("/api/pocketclaw/{child_id}/notes")
    async def pc_notes(child_id: str):
        return {"notes": [n.__dict__ for n in notes.list_notes(child_id)]}

    @app.post("/api/pocketclaw/{child_id}/notes")
    async def pc_add_note(child_id: str, p: NotePatch):
        n = notes.add_note(child_id, p.text, source="app", tag=p.tag)
        return {"ok": True, "note": n.__dict__}

    @app.get("/api/pocketclaw/{child_id}/words")
    async def pc_words(child_id: str):
        notes.ensure_seeded(child_id)      # 初始化示例作业，词汇由家长选择
        return {"due": [w.__dict__ for w in notes.due_words(child_id)],
                "all": [w.__dict__ for w in notes.list_words(child_id)],
                "count": notes.word_count(child_id)}

    @app.post("/api/pocketclaw/{child_id}/words")
    async def pc_add_word(child_id: str, p: WordPatch):
        w = notes.add_word(child_id, p.word, reading=p.reading, meaning=p.meaning, example=p.example)
        return {"ok": True, "word": w.__dict__}

    @app.post("/api/pocketclaw/{child_id}/words/review")
    async def pc_review_word(child_id: str, p: WordReviewPatch):
        """复习一张单词卡：记得→升 Leitner 盒、忘了→回 0 盒。"""
        w = notes.review_word(child_id, p.word_id, remembered=p.remembered)
        return {"ok": w is not None, "word": (w.__dict__ if w else None),
                "due": len(notes.due_words(child_id))}

    # ── 便利贴模块启用（平板 PocketClaw 设置卡）──
    @app.get("/api/pocketclaw/{child_id}/modules")
    async def pc_modules(child_id: str):
        return {"modules": registry.get_modules(child_id), "all": list(registry.MODULE_KEYS)}

    @app.post("/api/pocketclaw/{child_id}/modules")
    async def pc_set_modules(child_id: str, p: ModulesPatch, request: Request):
        # 按孩子寻址（路径 child_id），归属由 authz 中间件按路径参数校（guard 档，与其它涉童路由同一口径）。
        # 调用方：平板 PocketClaw 设置卡（client/src/api.ts setPocketclawModules，设备令牌）。
        try:
            return {"ok": True, "modules": registry.set_modules(child_id, p.modules)}
        except RegistryReadOnly:
            return _unavailable(request, "设置便利贴模块")

    # ── 作业清单（#11）──
    @app.get("/api/pocketclaw/{child_id}/tasks")
    async def pc_tasks(child_id: str):
        return {"tasks": [t.__dict__ for t in notes.list_tasks(child_id)],
                "stats": notes.task_stats(child_id)}

    @app.post("/api/pocketclaw/{child_id}/tasks")
    async def pc_add_task(child_id: str, p: TaskPatch):
        t = notes.add_task(child_id, p.text, subject=p.subject)
        return {"ok": True, "task": t.__dict__}

    @app.post("/api/pocketclaw/{child_id}/tasks/complete")
    async def pc_done_task(child_id: str, p: TaskDonePatch):
        t = notes.complete_task(child_id, task_id=p.task_id, text=p.text)
        return {"ok": t is not None, "task": (t.__dict__ if t else None),
                "stats": notes.task_stats(child_id)}

    @app.post("/api/pocketclaw/devices/{device_id}/push_tasks")
    async def pc_push_tasks(device_id: str, request: Request):
        """把该设备当前孩子的作业清单合成一屏，入 T0 推送队列（配合 RTC 定时唤醒取走）。"""
        who, denied = _authenticate(request)
        if denied is not None:
            return denied
        if registry.degraded:
            return _unavailable(request, "推作业清单")
        dev = registry.get(device_id)
        if dev is None or is_unbound(dev.child_id):
            # P0-3：原来 `dev.child_id if dev else "kid"`——给一个不存在的 device_id 推送，会把 kid 的作业清单
            # 排进这个 id 的队列，而 /devices/{id}/sync 是瘦端公开路由，谁都能把它取走。
            # （404 是既有口径，tests 钉着；它与下面的 403 有差别＝「这个 id 有没有绑定」可被有令牌的人探出来，
            #   /push 与 /focus 已统一成 403。）
            return JSONResponse({"ok": False, "error": "设备不存在或尚未绑定孩子"}, status_code=404)
        # 2026-09-28：别家的设备同样不许碰——修前任意令牌都能触发，响应里还带回那台设备绑定的 child_id。
        if not who.owns(dev.child_id):
            denied = _deny_or_soft(request, 403, _MSG_NOT_YOURS, "给别家便利贴推作业清单",
                                   device=device_id, bound=dev.child_id, role=who.role)
            if denied is not None:
                return denied
        child = dev.child_id
        items = [(t.text, t.done) for t in notes.list_tasks(child)]
        page = display.render_checklist("今日作业清单", items)
        try:
            item = registry.push_content(device_id, page, kind="tasks")
        except RegistryReadOnly:
            return _unavailable(request, "推作业清单")
        return {"ok": True, "queued": item["id"], "child_id": child,
                "pending": registry.pending_count(device_id)}

    # ── WS：T1 语音链路 ──
    @app.websocket("/pocketclaw")
    async def pc_ws(ws: WebSocket):
        await ws.accept()
        auth_header = ws.headers.get("authorization", "")
        token = auth_header[7:] if auth_header.startswith("Bearer ") else ""
        if device_authenticate and (not token or len(token) > 1024):
            await ws.close(code=1008)
            return
        authenticated_device = ""
        authenticated_child = ""

        async def send_json(s: str):
            await ws.send_text(s)

        async def send_bin(b: bytes):
            await ws.send_bytes(b)

        session = PocketClawSession(
            runtime, send_json=send_json, send_bin=send_bin,
            stt=make_stt(voice), tts=make_tts(),
            registry=registry, notes=notes, controls=controls, books=books, grade_band=grade_band,
            on_focus_done=on_focus_done, on_focus_today=on_focus_today,
            daily_spark=daily_spark, qbank=qbank, reading_path=reading_path)
        session.disconnect = lambda: ws.close(code=1008)
        try:
            while True:
                frame = await ws.receive()
                # Do not log screen pixels, transcribed text or other device payloads.
                log.debug("pc_ws frame type=%s text_bytes=%d binary_bytes=%d", frame.get("type"),
                          len(frame.get("text") or ""), len(frame.get("bytes") or b""))
                if frame.get("type") == "websocket.disconnect":
                    break
                if frame.get("text") is not None:
                    msg = protocol.parse_control(frame["text"])
                    if device_authenticate and not msg:
                        await ws.close(code=1008)
                        break
                    if msg:
                        if device_authenticate:
                            if not authenticated_device:
                                candidate = str(msg.get("device_id") or msg.get("mac") or "")
                                identity = device_authenticate(candidate, token) if msg.get("kind") == protocol.HELLO else None
                                if not identity:
                                    await ws.close(code=1008)
                                    break
                                authenticated_device, authenticated_child = candidate, identity[1]
                                registry.upsert(candidate)
                                registry.bind_child(candidate, authenticated_child)
                            elif msg.get("kind") == protocol.HELLO or device_authenticate(authenticated_device, token) != (identity[0], authenticated_child):
                                await ws.close(code=1008)
                                break
                            if msg.get("kind") == protocol.NFC:
                                # Legacy NFC mappings have no family boundary yet.
                                await ws.send_text(protocol.error("nfc_unavailable", "设备认证模式暂不支持 NFC 切换"))
                                continue
                        await session.on_control(msg)
                        if session.reject_close:          # 严格模式：hello 前的业务帧 → 已回 error，关连接
                            await ws.close()
                            break
                        # 登记活跃会话必须在 on_control **之后**：device_id 是 hello 里带来的，
                        # 处理之前它还是空字符串（第一版写在 receive 之后，于是永远登记不上）。
                        if session.device_id and live.get(session.device_id) is not session:
                            live[session.device_id] = session
                elif frame.get("bytes") is not None:
                    if device_authenticate and (not authenticated_device or device_authenticate(authenticated_device, token) != (identity[0], authenticated_child)):
                        await ws.close(code=1008)
                        break
                    await session.on_binary(frame["bytes"])
        except WebSocketDisconnect:
            pass
        except Exception as e:
            log.exception("pc_ws error: %s", e)
        finally:
            if session.device_id and live.get(session.device_id) is session:
                live.pop(session.device_id, None)
            await session.close()          # 断连时取消可能仍在跑的后台一轮 + 花火计时循环

    # ── 家长端极速录入中心（手机端/微信浏览器友好）──
    @app.get("/pocketclaw/parent", response_class=HTMLResponse)
    async def pc_parent_portal():
        """家长录入网页：微信班级群作业一键粘贴，秒级推送到 Note 4 墨水屏。"""
        return HTMLResponse(_HTML_PARENT_PORTAL)

    @app.get("/api/pocketclaw/parent/status")
    async def pc_parent_status():
        devs = registry.list_devices()
        first_dev = devs[0] if devs else None
        child = _resolve_child()
        tasks = [t.__dict__ for t in notes.list_tasks(child)]
        stats = notes.task_stats(child)
        is_online = bool(live)
        bat = None
        for s in live.values():
            if s.battery is not None:
                bat = s.battery
                break
        if bat is None and first_dev:
            bat = first_dev.battery
        return {
            "ok": True,
            "child_id": child,
            "online": is_online,
            "battery": bat,
            "tasks": tasks,
            "stats": stats,
            "dictation_words": notes.get_dictation_words(child)
        }

    @app.post("/api/pocketclaw/parent/push_raw")
    async def pc_parent_push_raw(request: Request):
        data = await request.json()
        b64 = data.get("b64", "")
        b64_4bpp = data.get("b64_4bpp", "")
        if not b64 and not b64_4bpp:
            return {"ok": False, "error": "missing b64 or b64_4bpp"}
        pushed = 0
        for s in list(live.values()):
            page = {
                "schema_version": "pc-0.1",
                "mode": "preview",
                "title": data.get("title", "高清图像预览"),
                "partial": False,
            }
            if b64_4bpp:
                page["raw_4bpp_b64"] = b64_4bpp
            else:
                page["raw_1bpp_b64"] = b64
            await s._display(page)
            pushed += 1
        return {"ok": True, "pushed": pushed}

    @app.post("/api/pocketclaw/parent/quick_input")
    async def pc_parent_quick_input(p: ParentQuickInputPatch):
        devs = registry.list_devices()
        first_dev = devs[0] if devs else None
        child = _resolve_child(p.child_id)

        tasks_added = 0
        if p.clear_existing:
            b = notes._bucket(child)
            b["tasks"] = []
            notes._save()

        if p.raw_tasks:
            parsed = parse_raw_homework(p.raw_tasks)
            for text, sub in parsed:
                notes.add_task(child, text, subject=sub)
                tasks_added += 1

        dictation_count = 0
        if p.dictation_words:
            d_words = parse_dictation_words(p.dictation_words)
            notes.set_dictation_words(child, d_words)
            dictation_count = len(d_words)

        # 实时推送到当前在线连接的 Note 4 墨水屏！
        for s in list(live.values()):
            if s.child_id != child:
                continue
            try:
                await s._show_tasks()
            except Exception as e:
                log.warning("实时刷新 Note 4 屏幕失败: %s", e)


        return {
            "ok": True,
            "child_id": child,
            "tasks_added": tasks_added,
            "dictation_count": dictation_count,
            "stats": notes.task_stats(child)
        }

    # ── 学伴人设与 LLM/OCR 系统配置接口 ─────────────────
    @app.get("/api/pocketclaw/mp/settings")
    async def get_system_settings(child_id: Optional[str] = None):
        child_id = _resolve_child(child_id)
        comp_cfg = notes.get_companion_config(child_id) if hasattr(notes, "get_companion_config") else {}
        ai_cfg = notes.get_ai_config() if hasattr(notes, "get_ai_config") else {}
        rt_cfg = runtime.get_config(mask_key=True) if hasattr(runtime, "get_config") else {"llm": {}, "ocr": {}}
        return {
            "ok": True,
            "companion": comp_cfg,
            "llm": rt_cfg.get("llm", ai_cfg.get("llm", {})),
            "ocr": rt_cfg.get("ocr", ai_cfg.get("ocr", {})),
            "presets": getattr(getattr(runtime, "llm", None), "PRESET_PROVIDERS", {}),
        }

    @app.post("/api/pocketclaw/mp/settings")
    async def save_system_settings(payload: Dict[str, Any], child_id: Optional[str] = None):
        child_id = _resolve_child(child_id)
        comp_res = None
        if "companion" in payload and isinstance(payload["companion"], dict):
            comp_res = notes.set_companion_config(child_id, payload["companion"])

        ai_res = None
        if "llm" in payload or "ocr" in payload:
            ai_res = notes.set_ai_config(payload)
            if hasattr(runtime, "update_config"):
                runtime.update_config(payload)

        return {
            "ok": True,
            "message": "配置已成功保存并立即生效",
            "companion": comp_res or (notes.get_companion_config(child_id) if hasattr(notes, "get_companion_config") else {}),
            "ai": runtime.get_config(mask_key=True) if hasattr(runtime, "get_config") else {},
        }

    @app.post("/api/pocketclaw/mp/settings/test_llm")
    async def test_llm_endpoint(payload: Optional[Dict[str, Any]] = None):
        llm = getattr(runtime, "llm", None)
        if not llm or not hasattr(llm, "test_llm_connection"):
            return {"ok": False, "error": "当前运行时不支持测试连接"}
        p = payload or {}
        key = p.get("api_key")
        if key and key.startswith("sk-***"):
            key = None
        return await llm.test_llm_connection(
            key=key,
            base_url=p.get("base_url"),
            model=p.get("model")
        )

    @app.post("/api/pocketclaw/mp/settings/reset")
    async def reset_system_settings(child_id: Optional[str] = None):
        child_id = _resolve_child(child_id)
        if hasattr(notes, "reset_companion_config"):
            notes.reset_companion_config(child_id)
        default_ai = {
            "llm": {"mode": "local", "provider": "deepseek", "api_key": "", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat"},
            "ocr": {"mode": "local", "vision_model": "gpt-4o-mini", "api_key": "", "base_url": ""}
        }
        if hasattr(notes, "set_ai_config"):
            notes.set_ai_config(default_ai)
        if hasattr(runtime, "update_config"):
            runtime.update_config(default_ai)
        return {"ok": True, "message": "已恢复全部出厂设置"}

    @app.get("/api/pocketclaw/mp/companion")
    async def get_companion_settings_compat(child_id: Optional[str] = None):
        child_id = _resolve_child(child_id)
        return {"ok": True, "config": notes.get_companion_config(child_id) if hasattr(notes, "get_companion_config") else {}}

    @app.post("/api/pocketclaw/mp/companion")
    async def save_companion_settings_compat(payload: Dict[str, Any], child_id: Optional[str] = None):
        child_id = _resolve_child(child_id)
        cfg = notes.set_companion_config(child_id, payload) if hasattr(notes, "set_companion_config") else {}
        return {"ok": True, "config": cfg, "message": "学伴人设已保存"}

    # ── 全功能开关与家长权限控制 ──
    @app.get("/api/pocketclaw/mp/features")
    async def pc_mp_get_features(child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        return {
            "ok": True,
            "features": notes.get_features_status(child) if hasattr(notes, "get_features_status") else [],
            "disabled": notes.get_disabled_features(child) if hasattr(notes, "get_disabled_features") else []
        }

    @app.post("/api/pocketclaw/mp/features/toggle")
    async def pc_mp_toggle_feature(payload: Dict[str, Any]):
        child = _resolve_child(payload.get("child_id"))
        feature = str(payload.get("feature", "")).strip()
        enabled = bool(payload.get("enabled", True))
        if not feature:
            return {"ok": False, "error": "feature required"}
        if hasattr(notes, "set_feature_enabled"):
            notes.set_feature_enabled(child, feature, enabled)
        for s in list(live.values()):
            if s.child_id != child:
                continue
            try:
                if not enabled and getattr(s, "mode", None) == feature:
                    await s._switch_to_mode("task", time.time(), push_to_history=False)
                elif getattr(s, "mode", None) == "menu":
                    await s._show_menu(time.time(), page=getattr(s, "menu_page", 0))
            except Exception:
                pass
        return {
            "ok": True,
            "feature": feature,
            "enabled": enabled,
            "features": notes.get_features_status(child) if hasattr(notes, "get_features_status") else [],
            "disabled": notes.get_disabled_features(child) if hasattr(notes, "get_disabled_features") else []
        }

    # ── 全功能内容修改与删除端点 (CRUD) ──

    # 1. 作业修改
    @app.put("/api/pocketclaw/mp/tasks/{task_id}")
    async def pc_mp_update_task(task_id: str, payload: Dict[str, Any]):
        child = _resolve_child(payload.get("child_id"))
        t = notes.update_task(
            child, task_id,
            text=payload.get("text"),
            subject=payload.get("subject"),
            done=payload.get("done")
        )
        for s in list(live.values()):
            if s.child_id != child:
                continue
            try:
                if getattr(s, "mode", None) == "task":
                    await s._show_tasks()
            except Exception:
                pass
        return {"ok": t is not None, "task": t.__dict__ if t else None, "stats": notes.task_stats(child)}

    # 2. 生词卡修改
    @app.put("/api/pocketclaw/mp/vocab/{word_id}")
    async def pc_mp_update_vocab(word_id: str, payload: Dict[str, Any]):
        child = _resolve_child(payload.get("child_id"))
        w = notes.update_word(
            child, word_id,
            word=payload.get("word"),
            meaning=payload.get("meaning") or payload.get("translation"),
            example=payload.get("example"),
            example_cn=payload.get("example_cn"),
            phonetic=payload.get("phonetic")
        )
        return {"ok": w is not None, "word": w.__dict__ if w else None}

    # 3. 听写词单修改与删除
    @app.put("/api/pocketclaw/mp/dictation/{set_id}")
    async def pc_mp_update_dictation_set(set_id: str, payload: Dict[str, Any]):
        child = _resolve_child(payload.get("child_id"))
        s = notes.update_dictation_set(child, set_id, title=payload.get("title"), words=payload.get("words"))
        return {"ok": s is not None, "set": s}

    @app.delete("/api/pocketclaw/mp/dictation/{set_id}")
    async def pc_mp_delete_dictation_set(set_id: str, child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        ok = notes.delete_dictation_set(child, set_id)
        return {"ok": ok}

    # 4. 习惯打卡修改
    @app.put("/api/pocketclaw/mp/habits/{habit_id}")
    async def pc_mp_update_habit(habit_id: str, payload: Dict[str, Any]):
        child = _resolve_child(payload.get("child_id"))
        h = notes.update_habit(
            child, habit_id,
            name=payload.get("name"),
            icon=payload.get("icon"),
            target_days=payload.get("target_days")
        )
        return {"ok": h is not None, "habit": h}

    # 5. 星星存折与心愿修改
    @app.put("/api/pocketclaw/mp/stars/wishes/{wish_id}")
    async def pc_mp_update_wish(wish_id: str, payload: Dict[str, Any]):
        child = _resolve_child(payload.get("child_id"))
        w = notes.update_wish(
            child, wish_id,
            title=payload.get("title"),
            stars_needed=payload.get("stars_needed")
        )
        return {"ok": w is not None, "wish": w}

    # 6. 错题修改与消灭
    @app.put("/api/pocketclaw/mp/mistakes/{mistake_id}")
    async def pc_mp_update_mistake(mistake_id: str, payload: Dict[str, Any]):
        child = _resolve_child(payload.get("child_id"))
        m = notes.update_mistake(
            child, mistake_id,
            question=payload.get("question"),
            subject=payload.get("subject"),
            answer=payload.get("answer")
        )
        return {"ok": m is not None, "mistake": m.__dict__ if m else None}

    # 7. 倒计时屏保修改
    @app.put("/api/pocketclaw/mp/screensaver/countdown/{event_id}")
    async def pc_mp_update_countdown(event_id: str, payload: Dict[str, Any]):
        child = _resolve_child(payload.get("child_id"))
        e = notes.update_countdown_event(
            child, event_id,
            title=payload.get("title"),
            target_date=payload.get("target_date"),
            slogan=payload.get("slogan")
        )
        return {"ok": e is not None, "event": e}

    # 8. 课内古诗自定义库 CRUD
    @app.get("/api/pocketclaw/mp/poems/custom")
    async def pc_mp_list_custom_poems(child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        return {"ok": True, "poems": notes.list_custom_poems(child) if hasattr(notes, "list_custom_poems") else []}

    @app.post("/api/pocketclaw/mp/poems/custom")
    async def pc_mp_add_custom_poem(payload: Dict[str, Any]):
        child = _resolve_child(payload.get("child_id"))
        p = notes.add_custom_poem(
            child,
            title=str(payload.get("title", "")).strip(),
            author=str(payload.get("author", "")).strip(),
            dynasty=str(payload.get("dynasty", "")).strip(),
            lines=payload.get("lines", [])
        )
        return {"ok": True, "poem": p}

    @app.put("/api/pocketclaw/mp/poems/custom/{poem_id}")
    async def pc_mp_update_custom_poem(poem_id: str, payload: Dict[str, Any]):
        child = _resolve_child(payload.get("child_id"))
        p = notes.update_custom_poem(
            child, poem_id,
            title=payload.get("title"),
            author=payload.get("author"),
            dynasty=payload.get("dynasty"),
            lines=payload.get("lines")
        )
        return {"ok": p is not None, "poem": p}

    @app.delete("/api/pocketclaw/mp/poems/custom/{poem_id}")
    async def pc_mp_delete_custom_poem(poem_id: str, child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        ok = notes.delete_custom_poem(child, poem_id)
        return {"ok": ok}

    # 9. 每日成语自定义库 CRUD
    @app.get("/api/pocketclaw/mp/idiom/custom")
    async def pc_mp_list_custom_idioms(child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        return {"ok": True, "idioms": notes.list_custom_idioms(child) if hasattr(notes, "list_custom_idioms") else []}

    @app.post("/api/pocketclaw/mp/idiom/custom")
    async def pc_mp_add_custom_idiom(payload: Dict[str, Any]):
        child = _resolve_child(payload.get("child_id"))
        item = notes.add_custom_idiom(
            child,
            idiom=str(payload.get("idiom", "")).strip(),
            pinyin=str(payload.get("pinyin", "")).strip(),
            meaning=str(payload.get("meaning", "")).strip(),
            source=str(payload.get("source", "")).strip(),
            example=str(payload.get("example", "")).strip()
        )
        return {"ok": True, "idiom": item}

    @app.put("/api/pocketclaw/mp/idiom/custom/{idiom_id}")
    async def pc_mp_update_custom_idiom(idiom_id: str, payload: Dict[str, Any]):
        child = _resolve_child(payload.get("child_id"))
        item = notes.update_custom_idiom(
            child, idiom_id,
            idiom=payload.get("idiom"),
            pinyin=payload.get("pinyin"),
            meaning=payload.get("meaning"),
            source=payload.get("source"),
            example=payload.get("example")
        )
        return {"ok": item is not None, "idiom": item}

    @app.delete("/api/pocketclaw/mp/idiom/custom/{idiom_id}")
    async def pc_mp_delete_custom_idiom(idiom_id: str, child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        ok = notes.delete_custom_idiom(child, idiom_id)
        return {"ok": ok}

    # 10. 口算闯关靶向题库 CRUD
    @app.get("/api/pocketclaw/mp/arith/custom")
    async def pc_mp_list_custom_arith(child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        return {"ok": True, "items": notes.list_custom_arith(child) if hasattr(notes, "list_custom_arith") else []}

    @app.post("/api/pocketclaw/mp/arith/custom")
    async def pc_mp_add_custom_arith(payload: Dict[str, Any]):
        child = _resolve_child(payload.get("child_id"))
        item = notes.add_custom_arith(
            child,
            expr=str(payload.get("expr", "")).strip(),
            ans=str(payload.get("ans", "")).strip(),
            hint=str(payload.get("hint", "")).strip()
        )
        return {"ok": True, "item": item}

    @app.put("/api/pocketclaw/mp/arith/custom/{item_id}")
    async def pc_mp_update_custom_arith(item_id: str, payload: Dict[str, Any]):
        child = _resolve_child(payload.get("child_id"))
        item = notes.update_custom_arith(
            child, item_id,
            expr=payload.get("expr"),
            ans=payload.get("ans"),
            hint=payload.get("hint")
        )
        return {"ok": item is not None, "item": item}

    @app.delete("/api/pocketclaw/mp/arith/custom/{item_id}")
    async def pc_mp_delete_custom_arith(item_id: str, child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        ok = notes.delete_custom_arith(child, item_id)
        return {"ok": ok}

    # 11. 英语分级阅读篇目 CRUD
    @app.post("/api/pocketclaw/mp/reading/custom")
    async def pc_mp_add_custom_reading(payload: Dict[str, Any]):
        _resolve_child(payload.get('child_id'))
        store = reading.ReadingStore(path=reading_path)
        art_id = payload.get("id") or f"read_{int(time.time())}"
        title = str(payload.get("title", "自定义故事")).strip()
        level = str(payload.get("level", "小学通用")).strip()
        raw_sentences = payload.get("sentences", [])
        sentences = []
        for s in raw_sentences:
            if isinstance(s, (list, tuple)) and len(s) >= 2:
                sentences.append((str(s[0]).strip(), str(s[1]).strip()))
            elif isinstance(s, dict):
                sentences.append((str(s.get("en", "")).strip(), str(s.get("cn", "")).strip()))
        art = reading.ReadingArticle(id=art_id, title=title, level=level, sentences=sentences)
        store.add_article(art)
        return {"ok": True, "article": {"id": art.id, "title": art.title, "level": art.level, "sentences": art.sentences}}

    @app.put("/api/pocketclaw/mp/reading/custom/{article_id}")
    async def pc_mp_update_custom_reading(article_id: str, payload: Dict[str, Any]):
        _resolve_child(payload.get('child_id'))
        store = reading.ReadingStore(path=reading_path)
        title = payload.get("title")
        level = payload.get("level")
        sentences = None
        if "sentences" in payload:
            sentences = []
            for s in payload["sentences"]:
                if isinstance(s, (list, tuple)) and len(s) >= 2:
                    sentences.append((str(s[0]).strip(), str(s[1]).strip()))
                elif isinstance(s, dict):
                    sentences.append((str(s.get("en", "")).strip(), str(s.get("cn", "")).strip()))
        art = store.update_article(article_id, title=title, level=level, sentences=sentences)
        return {"ok": art is not None, "article": {"id": art.id, "title": art.title, "level": art.level, "sentences": art.sentences} if art else None}

    @app.delete("/api/pocketclaw/mp/reading/custom/{article_id}")
    async def pc_mp_delete_custom_reading(article_id: str, child_id: Optional[str] = None):
        _resolve_child(child_id)
        store = reading.ReadingStore(path=reading_path)
        ok = store.delete_article(article_id)
        return {"ok": ok}

    # 12. 猜猜看科普谜题 CRUD
    @app.get("/api/pocketclaw/mp/guess/custom")
    async def pc_mp_list_custom_guess(child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        return {"ok": True, "items": notes.list_custom_guesses(child) if hasattr(notes, "list_custom_guesses") else []}

    @app.post("/api/pocketclaw/mp/guess/custom")
    async def pc_mp_add_custom_guess(payload: Dict[str, Any]):
        child = _resolve_child(payload.get("child_id"))
        item = notes.add_custom_guess(
            child,
            name=str(payload.get("name", "")).strip(),
            category=str(payload.get("category", "综合")).strip(),
            hint=str(payload.get("hint", "")).strip()
        )
        return {"ok": True, "item": item}

    @app.put("/api/pocketclaw/mp/guess/custom/{item_id}")
    async def pc_mp_update_custom_guess(item_id: str, payload: Dict[str, Any]):
        child = _resolve_child(payload.get("child_id"))
        item = notes.update_custom_guess(
            child, item_id,
            name=payload.get("name"),
            category=payload.get("category"),
            hint=payload.get("hint")
        )
        return {"ok": item is not None, "item": item}

    @app.delete("/api/pocketclaw/mp/guess/custom/{item_id}")
    async def pc_mp_delete_custom_guess(item_id: str, child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        ok = notes.delete_custom_guess(child, item_id)
        return {"ok": ok}

    # 13. 核实一下科学辨伪库 CRUD
    @app.get("/api/pocketclaw/mp/facts/custom")
    async def pc_mp_list_custom_facts(child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        return {"ok": True, "items": notes.list_custom_facts(child) if hasattr(notes, "list_custom_facts") else []}

    @app.post("/api/pocketclaw/mp/facts/custom")
    async def pc_mp_add_custom_fact(payload: Dict[str, Any]):
        child = _resolve_child(payload.get("child_id"))
        item = notes.add_custom_fact(
            child,
            kw1=str(payload.get("kw1", "")).strip(),
            kw2=str(payload.get("kw2", "")).strip(),
            is_true=bool(payload.get("is_true", False)),
            note=str(payload.get("note", "")).strip(),
            how=str(payload.get("how", "")).strip()
        )
        return {"ok": True, "item": item}

    @app.put("/api/pocketclaw/mp/facts/custom/{item_id}")
    async def pc_mp_update_custom_fact(item_id: str, payload: Dict[str, Any]):
        child = _resolve_child(payload.get("child_id"))
        item = notes.update_custom_fact(
            child, item_id,
            kw1=payload.get("kw1"),
            kw2=payload.get("kw2"),
            is_true=payload.get("is_true"),
            note=payload.get("note"),
            how=payload.get("how")
        )
        return {"ok": item is not None, "item": item}

    @app.delete("/api/pocketclaw/mp/facts/custom/{item_id}")
    async def pc_mp_delete_custom_fact(item_id: str, child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        ok = notes.delete_custom_fact(child, item_id)
        return {"ok": ok}

    # 14. 费曼小老师讲解主题 CRUD
    @app.get("/api/pocketclaw/mp/feynman/custom")
    async def pc_mp_list_feynman_topics(child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        return {"ok": True, "topics": notes.list_feynman_topics(child) if hasattr(notes, "list_feynman_topics") else []}

    @app.post("/api/pocketclaw/mp/feynman/custom")
    async def pc_mp_add_feynman_topic(payload: Dict[str, Any]):
        child = _resolve_child(payload.get("child_id"))
        item = notes.add_feynman_topic(
            child,
            topic=str(payload.get("topic", "")).strip(),
            hint=str(payload.get("hint", "")).strip()
        )
        return {"ok": True, "topic": item}

    @app.put("/api/pocketclaw/mp/feynman/custom/{topic_id}")
    async def pc_mp_update_feynman_topic(topic_id: str, payload: Dict[str, Any]):
        child = _resolve_child(payload.get("child_id"))
        item = notes.update_feynman_topic(
            child, topic_id,
            topic=payload.get("topic"),
            hint=payload.get("hint")
        )
        return {"ok": item is not None, "topic": item}

    @app.delete("/api/pocketclaw/mp/feynman/custom/{topic_id}")
    async def pc_mp_delete_feynman_topic(topic_id: str, child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        ok = notes.delete_feynman_topic(child, topic_id)
        return {"ok": ok}

    # 15. 诗词飞花令自定义句 CRUD
    @app.get("/api/pocketclaw/mp/feihua/custom")
    async def pc_mp_list_feihua(child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        return {"ok": True, "feihua": notes.list_custom_feihua(child) if hasattr(notes, "list_custom_feihua") else {}}

    @app.post("/api/pocketclaw/mp/feihua/custom")
    async def pc_mp_add_feihua(payload: Dict[str, Any]):
        child = _resolve_child(payload.get("child_id"))
        res = notes.add_custom_feihua_verse(
            child,
            keyword=str(payload.get("keyword", "")).strip(),
            verse=str(payload.get("verse", "")).strip()
        )
        return {"ok": True, "result": res}

    @app.delete("/api/pocketclaw/mp/feihua/custom")
    async def pc_mp_delete_feihua(payload: Dict[str, Any]):
        child = _resolve_child(payload.get("child_id"))
        ok = notes.delete_custom_feihua_verse(
            child,
            keyword=str(payload.get("keyword", "")).strip(),
            verse=str(payload.get("verse", "")).strip()
        )
        return {"ok": ok}

    # 16. 小学新鲜事资讯与活动 CRUD
    @app.get("/api/pocketclaw/mp/news")
    async def pc_mp_list_news(child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        items = notes.list_news(child)
        config = notes.get_news_config(child)
        return {"ok": True, "news": items, "items": items, "sources": notes.list_news_sources(child), **config}

    @app.post("/api/pocketclaw/mp/news/config")
    async def pc_mp_news_config(payload: Dict[str, Any]):
        child = _resolve_child(payload.get("child_id"))
        region, whitelist, blacklist = payload.get('region'), payload.get('whitelist'), payload.get('blacklist')
        if (region is not None and not isinstance(region, str)) or any(x is not None and (not isinstance(x, list) or any(not isinstance(v, str) for v in x)) for x in (whitelist, blacklist)):
            return JSONResponse({'detail': '地区或关键词格式无效'}, status_code=400)
        return {'ok': True, 'config': notes.set_news_config(child, region, whitelist, blacklist)}

    @app.post("/api/pocketclaw/mp/news/source")
    async def pc_mp_news_source(payload: Dict[str, Any]):
        child = _resolve_child(payload.get('child_id'))
        name = str(payload.get('name') or '').strip()
        if not name:
            return JSONResponse({'detail': '请输入信源名称'}, status_code=400)
        source = notes.add_news_source(child, name, url=str(payload.get('url') or ''), category=str(payload.get('category') or '综合'), region=str(payload.get('region') or '全国'))
        return {'ok': True, 'source': source}

    @app.post("/api/pocketclaw/mp/news/source/delete")
    async def pc_mp_news_source_delete(payload: Dict[str, Any]):
        return {'ok': notes.delete_news_source(_resolve_child(payload.get('child_id')), str(payload.get('source_id') or ''))}

    @app.post("/api/pocketclaw/mp/news/restore")
    async def pc_mp_news_restore(payload: Dict[str, Any]):
        return {'ok': notes.restore_news_item(_resolve_child(payload.get('child_id')), str(payload.get('item_id') or ''))}

    @app.post("/api/pocketclaw/mp/news")
    async def pc_mp_add_news(payload: Dict[str, Any]):
        child = _resolve_child(payload.get("child_id"))
        item = notes.add_news_item(
            child,
            title=str(payload.get("title", "")).strip(),
            summary=str(payload.get("summary", "")).strip(),
            content=str(payload.get("content", "")).strip(),
            category=str(payload.get("category", "活动")).strip(),
            region=str(payload.get("region", "全国")).strip()
        )
        return {"ok": True, "item": item}

    @app.put("/api/pocketclaw/mp/news/{item_id}")
    async def pc_mp_update_news(item_id: str, payload: Dict[str, Any]):
        child = _resolve_child(payload.get("child_id"))
        item = notes.update_news_item(
            child, item_id,
            title=payload.get("title"),
            summary=payload.get("summary"),
            content=payload.get("content"),
            category=payload.get("category"),
            region=payload.get("region"),
            date_str=payload.get("date")
        )
        return {"ok": item is not None, "item": item}

    @app.delete("/api/pocketclaw/mp/news/{item_id}")
    async def pc_mp_delete_news(item_id: str, child_id: Optional[str] = None):
        child = _resolve_child(child_id)
        ok = notes.delete_news_item(child, item_id)
        return {"ok": ok}

    return {"registry": registry, "notes": notes, "live": live}
