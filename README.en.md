# PocketClaw · A Learning Companion for ZECTRIX Note 4

**Version 1.0** · [简体中文](README.md) | English

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![ESP-IDF](https://img.shields.io/badge/ESP--IDF-v5.3-green.svg)](https://idf.espressif.com/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com/)

PocketClaw turns a ZECTRIX Note 4 e-paper device into a learning companion for primary-school children. It combines ESP32-S3 firmware, a standalone FastAPI server, a parent web console, and a WeChat mini program. Children can use the device's three buttons and push-to-talk voice interaction; parents can manage learning content from a browser or the mini program.

The server can run on a computer on your home network without StudyClaw. Online AI chat, image recognition, speech recognition, and speech synthesis require the corresponding services and configuration. Store credentials in local configuration files, not in the repository.

## Contents

- [Features](#features)
- [Screenshots](#screenshots)
- [Architecture](#architecture)
- [Getting started](#getting-started)
- [Device controls and voice commands](#device-controls-and-voice-commands)
- [Configuration](#configuration)
- [Testing](#testing)
- [License and acknowledgments](#license-and-acknowledgments)

## Features

| Feature | Description |
| --- | --- |
| Homework | Display tasks on Note 4 and manage them from the parent interfaces. |
| Chinese dictation | Practice word lists, then review results manually or with configured image recognition. |
| Poetry recitation | Practice selected Chinese poems with the verses initially hidden. |
| Arithmetic | Practice age-appropriate mental arithmetic. |
| English reading and vocabulary | Read short passages, practice pronunciation, and study vocabulary. |
| Focus timer | Start a device-side Pomodoro timer with spark or candle visuals. |
| Screensavers | Browse artwork, quotations, poems, countdowns, and the day's timetable. |
| Poetry game | Play *feihualing*, a Chinese poetry matching game. |
| Guessing game and fact checking | Ask questions and explore age-appropriate explanations. |
| Habits and rewards | Track habits, stars, and family wishes. |
| Timetable | View one day at a time on Note 4; edit the week from the parent interfaces. |
| Mistakes and Chinese characters | Review mistakes and distinguish similar characters. |
| Feynman-style teaching | Explain a topic to the learning companion. |
| AI companion | Ask questions by voice, with optional child-profile personalization. |
| Family messages and news | Send encouragement and browse school-oriented news. |
| Content entry | Enter content manually or use photo recognition where supported; review recognition results before saving. |
| Parent management | Configure learning content, device features, profiles, and family access. |

Grade-filtered presets include Chinese characters, idioms, poems, and English vocabulary. The grade groupings are suggested difficulty levels, not a guarantee of alignment with a specific textbook.

## Screenshots

The Note 4 images were captured from the device and use example learning content. Actual screens depend on the configured content and device state.

| | |
| :---: | :---: |
| **Homework**<br><img src="docs/images/note4/01-tasks.png" alt="Homework" width="400"> | **Poetry recitation**<br><img src="docs/images/note4/03-poems.png" alt="Poetry recitation" width="400"> |
| **Arithmetic**<br><img src="docs/images/note4/04-arithmetic.png" alt="Arithmetic" width="400"> | **English reading**<br><img src="docs/images/note4/05-english-reading.png" alt="English reading" width="400"> |
| **Focus timer**<br><img src="docs/images/note4/06-focus.png" alt="Focus timer" width="400"> | **Screensaver**<br><img src="docs/images/note4/07-gallery.png" alt="Quotation screensaver" width="400"> |
| **Poetry game**<br><img src="docs/images/note4/08-feihua.png" alt="Poetry game" width="400"> | **Guessing game**<br><img src="docs/images/note4/09-guess.png" alt="Guessing game" width="400"> |
| **Fact checking**<br><img src="docs/images/note4/10-fact-check.png" alt="Fact checking" width="400"> | **Habits**<br><img src="docs/images/note4/11-habits.png" alt="Habit tracking" width="400"> |
| **Daily timetable**<br><img src="docs/images/note4/12-timetable.png" alt="Daily timetable" width="400"> | **Chinese character practice**<br><img src="docs/images/note4/13-hanzi.png" alt="Chinese character practice" width="400"> |
| **English vocabulary**<br><img src="docs/images/note4/14-english-words.png" alt="English vocabulary" width="400"> | **Feynman-style teaching**<br><img src="docs/images/note4/15-feynman.png" alt="Feynman-style teaching" width="400"> |
| **AI companion**<br><img src="docs/images/note4/16-companion.png" alt="AI companion" width="400"> | **Feature menu**<br><img src="docs/images/note4/18-menu.png" alt="Feature menu" width="400"> |
| **School news**<br><img src="docs/images/note4/19-news.png" alt="School news" width="400"> | **Daily idiom**<br><img src="docs/images/note4/20-idiom.png" alt="Daily idiom" width="400"> |

The parent web and WeChat mini program images were captured using isolated example data. The interfaces and learning content are primarily in Chinese.

| Parent web console | WeChat mini program |
| :---: | :---: |
| ![Parent web console](docs/images/parent-web-overview.png) | ![WeChat mini program](docs/images/miniprogram-overview.png) |

## Architecture

```text
Parent web console / WeChat mini program
              │ HTTP(S) / REST
              ▼
   Standalone FastAPI server
      │ WebSocket / audio
      ▼
  ZECTRIX Note 4 (ESP32-S3)
```

- `zectrix/`: Note 4 firmware, display drivers, audio, and board support.
- `server/`: standalone service, parent web console, data storage, and tests.
- `miniprogram/`: WeChat mini program for parents.
- `docs/images/`: README screenshots.

## Getting started

### 1. Start the server

Use Python 3.10, 3.11, or 3.12. From the repository root on macOS or Linux:

```bash
cd server
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # Only if you do not already have a .env file.
# Edit .env to configure the services you need.
python serve.py
```

Once the service is ready, it attempts to open the parent console in the local default browser. You can also open [http://127.0.0.1:8000/pocketclaw/parent](http://127.0.0.1:8000/pocketclaw/parent). Set `OPEN_BROWSER=0` in `server/.env` to disable automatic opening.

On **Windows**, use PowerShell and a supported Python version:

```powershell
cd "C:\path\to\Zectrix-claw\server"
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env  # Only if .env does not already exist.
notepad .env
.\.venv\Scripts\python.exe serve.py
```

Replace `-3.12` if using another supported Python version. Use `requirements.txt` on Windows: `requirements-lock.txt` includes `uvloop`, which is intended for macOS/Linux. The server can run on Windows, but the USB device-token tool and some tests require POSIX interfaces and have not been verified on native Windows; use macOS, Linux, or WSL for those workflows.

The default port is 8000. If it is occupied, you can run the server on another port and update the device and mini program addresses accordingly:

```bash
PORT=8001 .venv/bin/python serve.py
```

```powershell
$env:PORT="8001"
.\.venv\Scripts\python.exe serve.py
```

The service listens on the local network. Use the computer's LAN IP address instead of `127.0.0.1` when connecting from another device; `LAN_IP` can be set in `.env` if automatic detection selects the wrong interface.

- Parent console: `http://<server-ip>:8000/pocketclaw/parent`
- Note 4 WebSocket: `ws://<server-ip>:8000/pocketclaw`
- Mini program API: `http://<server-ip>:8000/api/pocketclaw/*`
- Health check: `http://<server-ip>:8000/health`

Replace the port with the one you actually use.

### 2. Build the Note 4 firmware

Install [ESP-IDF](https://docs.espressif.com/projects/esp-idf/en/latest/esp32s3/get-started/) (the Chinese README specifies v5.3 or later) and its prerequisites. The following example assumes macOS or Linux and an ESP-IDF installation under `~/esp/esp-idf`:

```bash
source ~/esp/esp-idf/export.sh
cd zectrix
idf.py set-target esp32s3
idf.py build
idf.py -p <serial-port> flash monitor
```

Replace `<serial-port>` with the actual Note 4 serial port. Before flashing, back up the device and verify that the firmware and partition layout match the hardware. Flashing can replace existing firmware and settings.

The device needs a reachable WebSocket URL for this server. The firmware can read a saved server address from NVS before using its built-in default; changing only a default URL does not necessarily replace an existing saved address. Configure the actual server address and port for your network before deploying the firmware.

### 3. Open the WeChat mini program

1. Install [WeChat Developer Tools](https://developers.weixin.qq.com/miniprogram/dev/devtools/download.html).
2. Import the `miniprogram/` directory and configure your own AppID or test account.
3. Configure the mini program's server address to use the computer's reachable IP and port.
4. For local development, adjust domain-validation settings as described in the Chinese README. Use HTTPS/WSS for credentials and remote access.
5. Run the simulator or use the developer tools' preview feature.

The mini program currently uses Chinese labels and content.

## Device controls and voice commands

The Note 4 uses three physical buttons: `▲` and `▼` navigate; `●` confirms, reads, or starts voice interaction depending on the current page. Hold `●`, speak, and release it to ask the companion a question or open a feature directly.

The current feature-switching phrases are Chinese. For example:

| Action | Example phrase |
| --- | --- |
| Open homework | `看作业` |
| Start dictation | `开始听写` |
| Start arithmetic | `做口算` |
| Recite poetry | `背古诗` |
| View the timetable | `看课表` |
| Open the menu | `功能菜单` |

Additional commands and context-dependent button behavior are listed in the [Chinese README](README.md#七-note-4-机身交互与语音指令速查). Voice recognition and synthesis require suitable service configuration. The experimental wake-word option is disabled by default and is not part of the delivered feature set.

## Configuration

- **AI and OCR:** Copy `server/.env.example` to `server/.env` and configure the desired providers and models. A compatible vision model is required for actual photo recognition. Check recognized content before saving it.
- **Child profile:** Parents can optionally set a name, grade, gender, and interests. The companion uses the profile to adapt its explanations. When using an online model, profile information is sent to the configured provider; offline personalization is more limited.
- **Screen capture:** A firmware build with `screen_capture` support is required. Configure `POCKETCLAW_ADMIN_TOKEN` in `server/.env` and use the mini program's screen refresh feature. Capture is on demand; the device must be online and available.
- **Family isolation:** Use `identity_admin.py create-family` and set `POCKETCLAW_MULTI_TENANT=required` to enable the single-process family isolation mode. Configure HTTPS/WSS and the trusted reverse proxy as appropriate. Parents can manage family content and issue or revoke device credentials.
- **Device credentials:** The USB provisioning workflow requires a temporary firmware build with `CONFIG_POCKETCLAW_USB_PROVISION` enabled and trusted physical access. Consult the [Chinese deployment instructions](README.md#八-快速启动与全链路部署指南) before use; firmware compatibility, backup, and the physical serial connection must be checked for the device.
- **Voice:** `POCKETCLAW_VOLC_ASR_STREAMING=1` enables streaming speech recognition when configured, with fallback to non-streaming recognition. Supported language-model streams can start speech synthesis sentence by sentence.
- **Remote access:** A reverse proxy, tunnel, or private network can provide access outside the home LAN. Protect credentials in transit with HTTPS/WSS and configure device endpoints for the chosen deployment.

`server/.env`, local data under `server/data/`, and device backups are intended to remain local and are ignored by Git. Do not commit credentials or personal family data.

## Testing

Run server tests from `server/` with a temporary data directory so tests do not touch family data:

```bash
cd server
POCKETCLAW_DATA_DIR="$(mktemp -d)" .venv/bin/python -m pytest -v
```

On Windows PowerShell, set `POCKETCLAW_DATA_DIR` to a temporary directory before running pytest; some POSIX-dependent tests are not supported on native Windows. Simulated devices and tests do not replace checks on physical Note 4 hardware or a real WeChat client.

## License and acknowledgments

The project is distributed under the [MIT License](LICENSE). Third-party components and resources may have their own licenses and attribution requirements; retain the notices provided with them.

PocketClaw uses the ZECTRIX Note 4 hardware platform, Espressif ESP-IDF, FastAPI, and WebSocket technologies. The [Chinese README](README.md#十一-开源教育资源引用与致谢) also lists the educational resources and projects acknowledged by the repository.
