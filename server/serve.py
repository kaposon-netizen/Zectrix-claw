#!/usr/bin/env python3
"""PocketClaw Note 4 独立服务端启动器。
运行方式：
    python serve.py
"""
import errno
import ipaddress
import os
import subprocess
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

# 确保 server/ 目录在 sys.path 中
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

try:
    from dotenv import load_dotenv
    load_dotenv(_HERE / ".env")
except ImportError:
    pass

import uvicorn


def _is_lan_ipv4(value: str) -> bool:
    try:
        ip = ipaddress.IPv4Address(value)
        return any(ip in ipaddress.IPv4Network(cidr) for cidr in (
            "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
    except ipaddress.AddressValueError:
        return False


def get_local_ip() -> str:
    """Prefer a real private interface over VPN/TUN benchmark addresses."""
    configured = os.getenv("LAN_IP", "").strip()
    if configured:
        return configured
    if sys.platform == "darwin":
        for interface in ("en0", "en1", "en2"):
            try:
                ip = subprocess.run(["/usr/sbin/ipconfig", "getifaddr", interface],
                                    capture_output=True, text=True, timeout=2, check=False).stdout.strip()
                if _is_lan_ipv4(ip):
                    return ip
            except (OSError, subprocess.TimeoutExpired):
                pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
            if _is_lan_ipv4(ip):
                return ip
    except OSError:
        pass
    return "127.0.0.1"


def port_available(host: str, port: int) -> bool:
    """Check for an existing listener before advertising a successful start."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
            return True
        except OSError as exc:
            if exc.errno == errno.EADDRINUSE:
                return False
            raise


def open_parent_when_ready(port: int, timeout: float = 30.0) -> bool:
    """Open the local parent portal once the server's health check responds."""
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{base}/health", timeout=1) as response:
                if response.status == 200:
                    return bool(webbrowser.open(f"{base}/pocketclaw/parent"))
        except (OSError, TimeoutError):
            pass
        time.sleep(0.25)
    print(f"服务尚未就绪，请手动打开 {base}/pocketclaw/parent", file=sys.stderr)
    return False


def main():
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    public_url = os.getenv("PUBLIC_URL") or os.getenv("REMOTE_URL") or os.getenv("REMOTE_DOMAIN") or ""
    if not port_available(host, port):
        print(f"端口 {port} 已被占用，服务未启动。", file=sys.stderr)
        print(f"查看占用进程: lsof -nP -iTCP:{port} -sTCP:LISTEN", file=sys.stderr)
        print("如需同时运行，可指定另一个端口，例如：PORT=8001 .venv/bin/python serve.py", file=sys.stderr)
        print("更改端口后，请同时更新设备和小程序连接地址。", file=sys.stderr)
        return 1
    local_ip = get_local_ip()

    print("=" * 70)
    print(" 🚀 PocketClaw · Note 4 墨水屏少儿伴学独立服务端")
    print("=" * 70)
    print(f" * 服务监听地址:        http://{host}:{port}")
    if local_ip == "127.0.0.1":
        print(" * 未检测到家庭局域网地址；可在 .env 中设置 LAN_IP。")
    print(f" * [局域网同网段直连]:")
    print(f"     · Note 4 WebSocket: ws://{local_ip}:{port}/pocketclaw")
    print(f"     · 家长 Web 控制台:  http://{local_ip}:{port}/pocketclaw/parent")
    print(f"     · 微信小程序 API:   http://{local_ip}:{port}/api/pocketclaw/*")

    if public_url:
        clean_url = public_url.rstrip("/")
        ws_scheme = "wss" if clean_url.startswith("https") else "ws"
        clean_host = clean_url.split("://")[-1]
        print(f" * [跨 Wi-Fi / 外网远程访问 (已配置反向代理/隧道)]:")
        print(f"     · Note 4 远程 WS:   {ws_scheme}://{clean_host}/pocketclaw")
        print(f"     · 远程家长控制台:   {clean_url}/pocketclaw/parent")
        print(f"     · 远程小程序 API:   {clean_url}/api/pocketclaw/*")
    else:
        print(f" * [跨 Wi-Fi / 外网移动伴学]:")
        print(f"     · 支持 Cloudflare Tunnel / frp / DDNS / Tailscale Funnel 反代")
        print(f"     · 可在 .env 中设置 REMOTE_URL=https://your-domain.com 启用远程展示")

    print(f" * 系统健康检查:        http://{local_ip}:{port}/health")
    print(f" * 本机家长页面:        http://127.0.0.1:{port}/pocketclaw/parent")
    print("=" * 70)

    if os.getenv("OPEN_BROWSER", "1").strip().lower() not in {"0", "false", "no", "off"}:
        threading.Thread(target=open_parent_when_ready, args=(port,), daemon=True).start()

    # Only trust forwarding headers from explicitly configured proxy addresses.
    uvicorn.run(
        "app:app",
        host=host,
        port=port,
        reload=True,
        proxy_headers=True,
        forwarded_allow_ips=os.getenv("FORWARDED_ALLOW_IPS", "127.0.0.1"),
        ws_ping_interval=20.0,
        ws_ping_timeout=20.0,
    )


if __name__ == "__main__":
    sys.exit(main())
