"""PocketClaw 测试通用夹具。"""
import asyncio
import os
import tempfile

# Set before test-module imports: app creates its stores during collection.
_test_data_dir = tempfile.TemporaryDirectory(prefix="pocketclaw-tests-")
os.environ["POCKETCLAW_DATA_DIR"] = _test_data_dir.name
# Never load local credentials or contact real providers in tests.
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
for name in list(os.environ):
    if name.startswith(("OPENAI_", "OCR_", "VOLC_", "POCKETCLAW_STT_", "POCKETCLAW_TTS_", "POCKETCLAW_VOLC_")) or name in ("LLM_MODE", "LLM_PROVIDER", "POCKETCLAW_ADMIN_TOKEN", "POCKETCLAW_DEVICE_AUTH", "POCKETCLAW_MULTI_TENANT"):
        os.environ.pop(name, None)
import pytest

@pytest.fixture(autouse=True)
def _restore_event_loop():
    """保证每个测试执行前都有可用的 asyncio 事件循环（适配 Python 3.10-3.12）。"""
    try:
        loop = asyncio.get_event_loop_policy().get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except Exception:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    yield
    try:
        if not loop.is_closed():
            loop.close()
    except Exception:
        pass
    asyncio.set_event_loop(asyncio.new_event_loop())
