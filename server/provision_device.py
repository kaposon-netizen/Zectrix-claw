#!/usr/bin/env python3
"""Configure a device token over a physically connected provisioning console.

The token is entered through getpass, never as a command-line argument or file.
Requires a temporary CONFIG_POCKETCLAW_USB_PROVISION firmware build.
"""
import argparse
import getpass
import os
import re
import select
import sys
import termios
import time
import tty

MAC = re.compile(r'^[0-9a-f]{2}(?::[0-9a-f]{2}){5}$')
TOKEN = re.compile(r'^[A-Za-z0-9_-]{32,127}$')


def configure(port, expected_device_id, token, timeout=90):
    if not MAC.fullmatch(expected_device_id) or not TOKEN.fullmatch(token):
        raise ValueError('设备 ID 或令牌格式无效')
    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    old = None
    try:
        old = termios.tcgetattr(fd)
        tty.setraw(fd)
        config = termios.tcgetattr(fd)
        config[4] = config[5] = termios.B115200
        termios.tcsetattr(fd, termios.TCSANOW, config)
        deadline = time.monotonic() + timeout
        buffer = bytearray()
        sent = False
        while time.monotonic() < deadline:
            if not select.select([fd], [], [], min(1, max(0, deadline-time.monotonic())))[0]:
                continue
            chunk = os.read(fd, 512)
            if not chunk:
                continue
            buffer.extend(chunk)
            if len(buffer) > 8192:
                del buffer[:-1024]
            while b'\n' in buffer:
                line, _, rest = buffer.partition(b'\n')
                buffer = bytearray(rest)
                line = line.strip()
                if not sent and line.startswith(b'PCPROV1 READY '):
                    actual = line[len(b'PCPROV1 READY '):].decode('ascii', 'replace')
                    if actual != expected_device_id:
                        raise ValueError('连接的设备 ID 与目标设备不一致')
                    data = (token + '\n').encode('ascii')
                    while data:
                        _, writable, _ = select.select([], [fd], [], 2)
                        if not writable:
                            raise TimeoutError('串口写入超时')
                        n = os.write(fd, data)
                        data = data[n:]
                    sent = True
                elif sent and line == b'PCPROV1 OK':
                    return
                elif sent and line.startswith(b'PCPROV1 ERROR'):
                    raise RuntimeError('设备未能保存凭据')
        raise TimeoutError('等待设备超时，请在工具启动后重启已连接的设备')
    finally:
        if old is not None:
            termios.tcsetattr(fd, termios.TCSANOW, old)
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description='通过物理串口配置 Note 4 设备凭据')
    parser.add_argument('--port', required=True, help='本机串口路径')
    parser.add_argument('--device-id', required=True, help='设备 Wi-Fi MAC 地址')
    args = parser.parse_args()
    token = getpass.getpass('设备令牌（输入不回显）: ').strip()
    print('等待设备启动配对窗口；请现在重启设备。')
    try:
        configure(args.port, args.device_id.lower(), token)
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        print('配置失败：' + str(exc), file=sys.stderr)
        return 1
    finally:
        token = None
    print('设备已保存凭据。请换回关闭 USB 配置选项的正常固件。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
