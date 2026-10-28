"""PocketClaw Note 4 独立伴学服务端应用入口 (FastAPI)
提供：
1. Note 4 墨水屏双工 WebSocket: /pocketclaw
2. 家长端 Web 管理后台: /pocketclaw/parent
3. 微信小程序与外部 REST API: /api/pocketclaw/*
4. 拍照 OCR 识别接口: /api/pocketclaw/mp/ocr_words, /api/pocketclaw/mp/ocr_reading
"""
from __future__ import annotations

import os
import hmac
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from pocketclaw.endpoint import register_pocketclaw
from runtime import StandaloneRuntime, StandaloneLLM
from pocketclaw.identity import IdentityStore

# 初始化数据目录
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
DATA_DIR = Path(os.environ.get("POCKETCLAW_DATA_DIR", str(BASE_DIR / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)

NOTES_FILE = str(DATA_DIR / "pocketclaw_notes.json")
DEVICES_FILE = str(DATA_DIR / "pocketclaw_devices.json")

def create_app(*, data_dir=None, family_id=None, identity_store=None) -> FastAPI:
    app = FastAPI(
        title="PocketClaw Note 4 Server",
        description="ZECTRIX Note 4 墨水屏伴学掌机独立服务引擎",
        version="2.0.0",
    )

    # 跨域配置：支持微信开发者工具及各种局域网 Web 访问
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 实例化独立运行时
    runtime = StandaloneRuntime(llm=StandaloneLLM())

    # Optional device authentication; family API authorization is still separate.
    app_data = Path(data_dir) if data_dir else DATA_DIR
    app_data.mkdir(parents=True, exist_ok=True)
    device_auth_required = bool(family_id) or os.getenv("POCKETCLAW_DEVICE_AUTH", "").lower() == "required"
    identities = identity_store or (IdentityStore(str(DATA_DIR / "identities.db")) if device_auth_required else None)
    children = identities.children_for_family(family_id) if family_id and identities else []
    if family_id and not children:
        raise RuntimeError("家庭没有可用的孩子账户")

    def parent_authenticate(header):
        token = header[7:] if header.startswith("Bearer ") else ""
        if identities and family_id and identities.session_family(token) == family_id:
            return identities.children_for_family(family_id)
        return None

    # 注册 PocketClaw 全部端点 (WebSocket / REST / Web 门户)
    pocketclaw_instance = register_pocketclaw(
        app,
        runtime=runtime,
        notes_path=str(app_data / "pocketclaw_notes.json"),
        devices_path=str(app_data / "pocketclaw_devices.json"),
        reading_path=str(app_data / "english_readings.json"),
        default_child=children[0] if children else None,
        parent_authenticate=parent_authenticate if family_id else None,
        strict_identity=device_auth_required,
        device_authenticate=identities.authenticate_device if identities else None,
        admin_check=lambda value: bool(os.getenv("POCKETCLAW_ADMIN_TOKEN")) and hmac.compare_digest(
            value.encode("utf-8"), os.getenv("POCKETCLAW_ADMIN_TOKEN", "").encode("utf-8")),
    )

    app.state.pocketclaw = pocketclaw_instance
    app.state.runtime = runtime
    app.state.identities = identities

    @app.get("/", include_in_schema=False)
    async def index():
        """根路径自动跳转到家长端管理后台。"""
        return RedirectResponse(url="/pocketclaw/parent")

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "service": "PocketClaw Note 4 Companion Engine",
            "devices_online": len(pocketclaw_instance.get("live", {})),
        }

    return app

if os.getenv("POCKETCLAW_MULTI_TENANT", "").lower() == "required":
    from pocketclaw.multitenant import MultiTenantApp
    app = MultiTenantApp(DATA_DIR, create_app)
else:
    app = create_app()
