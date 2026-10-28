"""Opt-in family isolation: independent PocketClaw app and data directory per family."""
from __future__ import annotations

import asyncio
import re
import io
import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, RedirectResponse
from pydantic import BaseModel, Field

from pocketclaw.identity import IdentityStore


class Login(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=512)


class DeviceIssue(BaseModel):
    child_id: str = Field(min_length=1, max_length=128)
    device_id: str = Field(min_length=1, max_length=128)


def _bearer(scope) -> str:
    headers = dict(scope.get('headers') or [])
    value = headers.get(b'authorization', b'').decode('latin1')
    return value[7:] if value.startswith('Bearer ') else ''


class MultiTenantApp:
    def __init__(self, data_dir, factory, *, allow_insecure=False):
        self.data_dir = Path(data_dir)
        self.store = IdentityStore(str(self.data_dir / 'identities.db'))
        self.factory = factory
        self.allow_insecure = allow_insecure
        self.apps = {}
        self.login_attempts = defaultdict(deque)
        self.root = FastAPI()

        @self.root.get('/')
        async def index():
            return RedirectResponse('/pocketclaw/parent')

        @self.root.get('/pocketclaw/parent')
        async def family_portal():
            html = Path(__file__).with_name('family_portal.html').read_text(encoding='utf-8')
            workspace = Path(__file__).with_name('family_workspace.js').read_text(encoding='utf-8')
            html = html.replace('</body></html>', '<script>' + workspace + '</script></body></html>')
            return HTMLResponse(html, headers={'Cache-Control': 'no-store',
                'Content-Security-Policy': "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'", 
                'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer'})

        @self.root.get('/health')
        async def health():
            return {'status': 'ok', 'service': 'PocketClaw', 'mode': 'family'}

        @self.root.get('/api/pocketclaw/mp/art_preview/{art_id}')
        async def public_art(art_id: str):
            from PIL import Image
            from pocketclaw.screensaver import get_art_bitmap
            if not re.fullmatch(r'[A-Za-z0-9_-]+', art_id):
                return Response(status_code=404)
            data = get_art_bitmap(art_id)
            if not data or len(data) not in (15000, 60000):
                return Response(status_code=404)
            if len(data) == 60000:
                pixels = bytes(value for b in data for value in ((b >> 4) * 17, (b & 15) * 17))
                image = Image.frombytes('L', (400, 300), pixels)
            else:
                # Art wire format uses 1=black; device framebuffer uses the inverse.
                image = Image.frombytes('1', (400, 300), bytes(b ^ 255 for b in data)).convert('L')
            out = io.BytesIO()
            image.save(out, format='PNG')
            return Response(out.getvalue(), media_type='image/png', headers={'Cache-Control': 'public, max-age=3600'})

        @self.root.post('/api/session/login')
        async def login(payload: Login, request: Request):
            ip = request.client.host if request.client else 'unknown'
            now = time.monotonic()
            attempts = self.login_attempts[ip]
            while attempts and now - attempts[0] >= 60:
                attempts.popleft()
            if len(attempts) >= 5:
                return JSONResponse({'detail': '尝试次数过多，请稍后再试'}, status_code=429)
            attempts.append(now)
            token = await asyncio.to_thread(self.store.login, payload.name, payload.password)
            if not token:
                return JSONResponse({'detail': '账号或密码错误'}, status_code=401)
            attempts.clear()
            family = self.store.session_family(token)
            return JSONResponse({'ok': True, 'token': token, 'children': self.store.children_for_family(family)}, headers={'Cache-Control': 'no-store'})

        @self.root.post('/api/session/logout')
        async def logout(request: Request):
            self.store.revoke_session(_bearer(request.scope))
            return {'ok': True}

        @self.root.get('/api/session/me')
        async def me(request: Request):
            family = self.store.session_family(_bearer(request.scope))
            if not family:
                return JSONResponse({'detail': '请先登录'}, status_code=401)
            return {'ok': True, 'children': self.store.children_for_family(family)}

        @self.root.post('/api/session/devices')
        async def issue(payload: DeviceIssue, request: Request):
            family = self.store.session_family(_bearer(request.scope))
            if not family:
                return JSONResponse({'detail': '请先登录'}, status_code=401)
            try:
                device_id = payload.device_id.strip()
                token = self.store.issue_device(family, payload.child_id, device_id)
            except (ValueError, PermissionError):
                return JSONResponse({'detail': '孩子或设备无权绑定'}, status_code=403)
            tenant = self.apps.get(family)
            live = tenant.state.pocketclaw['live'].get(device_id) if tenant else None
            if live:
                try: await live.disconnect()
                except RuntimeError: pass
            return JSONResponse({'ok': True, 'device_token': token}, headers={'Cache-Control': 'no-store'})

        @self.root.post('/api/session/devices/revoke')
        async def revoke_device(payload: DeviceIssue, request: Request):
            family = self.store.session_family(_bearer(request.scope))
            if not family:
                return JSONResponse({'detail': '请先登录'}, status_code=401)
            if not self.store.owns_child(family, payload.child_id):
                return JSONResponse({'detail': '孩子或设备无权操作'}, status_code=403)
            device_id = payload.device_id.strip()
            removed = self.store.revoke_device(family, device_id)
            tenant = self.apps.get(family)
            live = tenant.state.pocketclaw['live'].get(device_id) if tenant else None
            if removed and live:
                try: await live.disconnect()
                except RuntimeError: pass
            return {'ok': removed}

    def _family_app(self, family_id):
        if not re.fullmatch(r'fam_[0-9a-f]{32}', family_id):
            raise ValueError('invalid family id')
        if family_id not in self.apps:
            self.apps[family_id] = self.factory(data_dir=self.data_dir / 'families' / family_id,
                family_id=family_id, identity_store=self.store)
        return self.apps[family_id]

    async def __call__(self, scope, receive, send):
        kind, path = scope['type'], scope.get('path', '')
        if kind == 'lifespan':
            return await self.root(scope, receive, send)
        if kind not in ('http', 'websocket'):
            return
        if not self.allow_insecure and scope.get('scheme') not in ('https', 'wss'):
            if kind == 'websocket':
                return await send({'type': 'websocket.close', 'code': 1008})
            return await JSONResponse({'detail': '家庭模式需要 HTTPS'}, status_code=403)(scope, receive, send)
        if kind == 'http' and (path == '/health' or path.startswith('/api/session/') or (scope.get('method') == 'GET' and (path in ('/', '/pocketclaw/parent') or re.fullmatch(r'/api/pocketclaw/mp/art_preview/[A-Za-z0-9_-]+', path)))):
            return await self.root(scope, receive, send)
        token = _bearer(scope)
        if kind == 'http' and path in ('/api/pocketclaw/mp/settings', '/api/pocketclaw/mp/settings/test_llm', '/api/pocketclaw/mp/settings/reset'):
            return await JSONResponse({'detail': '家庭模式下模型配置由服务管理员管理'}, status_code=403)(scope, receive, send)
        if kind == 'http':
            family = self.store.session_family(token)
            if not family:
                return await JSONResponse({'detail': '请先登录'}, status_code=401)(scope, receive, send)
        else:
            device = self.store.device_for_token(token) if path == '/pocketclaw' else None
            if not device:
                return await send({'type': 'websocket.close', 'code': 1008})
            family = device[1]
        if kind == 'http':
            from pocketclaw.endpoint import family_allowed_children
            marker = family_allowed_children.set(frozenset(self.store.children_for_family(family)))
            try:
                return await self._family_app(family)(scope, receive, send)
            finally:
                family_allowed_children.reset(marker)
        return await self._family_app(family)(scope, receive, send)
