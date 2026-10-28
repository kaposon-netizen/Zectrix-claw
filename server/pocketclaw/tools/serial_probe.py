"""USB 串口探针：识别插在本机上的 PocketClaw（ESP32-S3），读一段串口日志。

用途：确认便利贴已通过 USB 接入、拿到它的 MAC（官方云/本地都按 MAC 认领设备）、
看固件在串口打印什么（当前工厂测试固件会打印自检日志）。

不依赖 pyserial：macOS 上用 ioreg 识别 Espressif 设备 + 直接读 /dev/cu.usbmodem*。
  python -m pocketclaw.tools.serial_probe            # 识别 + 读 3 秒
  python -m pocketclaw.tools.serial_probe --seconds 8 --port /dev/cu.usbmodem1101
"""
from __future__ import annotations

import argparse
import glob
import os
import select
import subprocess
import sys
import time


def identify_esp():
    """用 ioreg 找 Espressif USB 设备，返回 [(product, serial/mac)]。"""
    out = []
    try:
        txt = subprocess.check_output(["ioreg", "-p", "IOUSB", "-l", "-w", "0"],
                                      text=True, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        return out
    block, vendor, product, serial = [], "", "", ""
    for line in txt.splitlines():
        if '"USB Vendor Name"' in line:
            vendor = line.split("=", 1)[1].strip().strip('"')
        elif '"USB Product Name"' in line:
            product = line.split("=", 1)[1].strip().strip('"')
        elif '"USB Serial Number"' in line:
            serial = line.split("=", 1)[1].strip().strip('"')
            if "espressif" in vendor.lower() or "jtag" in product.lower():
                out.append((product or vendor, serial))
            vendor = product = serial = ""
    return out


def list_ports():
    return sorted(glob.glob("/dev/cu.usbmodem*") + glob.glob("/dev/cu.usbserial*")
                  + glob.glob("/dev/cu.SLAB*") + glob.glob("/dev/cu.wchusbserial*"))


def read_serial(port: str, seconds: float):
    """直接读 CDC 串口（无需 pyserial）；打印收到的文本行。"""
    try:
        fd = os.open(port, os.O_RDONLY | os.O_NONBLOCK)
    except OSError as e:
        print("打不开 %s：%s" % (port, e), file=sys.stderr)
        return
    print("── 读 %s %.0f 秒（Ctrl-C 停）──" % (port, seconds))
    end = time.time() + seconds
    buf = b""
    try:
        while time.time() < end:
            r, _, _ = select.select([fd], [], [], 0.3)
            if fd in r:
                try:
                    chunk = os.read(fd, 4096)
                except OSError:
                    continue
                if not chunk:
                    continue
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    sys.stdout.write("  " + line.decode("utf-8", "replace") + "\n")
                    sys.stdout.flush()
    except KeyboardInterrupt:
        pass
    finally:
        os.close(fd)
    if not buf and end:
        print("（这段时间串口没有输出——工厂固件可能已过自检静默，或波特率不符）")


def main():
    ap = argparse.ArgumentParser(description="PocketClaw USB 串口探针")
    ap.add_argument("--port", default="", help="串口设备，默认自动挑第一个 usbmodem")
    ap.add_argument("--seconds", type=float, default=3.0)
    a = ap.parse_args()

    esp = identify_esp()
    if esp:
        print("检测到 Espressif 设备：")
        for prod, ser in esp:
            print("  · %s  MAC/序列号=%s" % (prod, ser))
    else:
        print("未检测到 Espressif USB 设备（便利贴可能未插好，或非 S3 原生 USB）。")

    ports = list_ports()
    print("可用串口：", ", ".join(ports) or "（无）")
    port = a.port or (ports[0] if ports else "")
    if not port:
        print("没有可读串口。把便利贴用 USB 数据线接上再试。")
        return
    read_serial(port, a.seconds)


if __name__ == "__main__":
    main()
