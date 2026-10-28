"""2026-09-28 审查「便利贴按设备寻址的接口不校验归属」的回归闸（pocketclaw/endpoint.py + authz/policy.py）。

① 家长 / 平板端按设备或卡号寻址的六条——/devices 列表、/devices/{id}/bind|push|focus|push_tasks、/cards/register——
   由处理器自校：管理后台口令（X-Admin-Token）管全部；否则要有效的家长 / 设备令牌（无 → 401），且设备当前绑定的孩子
   在令牌名下（家长令牌看 children、设备令牌看 child_id；不属于 → 403）；未绑定 / 不存在的设备只许 bind。
   401 / 403 与中间件同一口径：强制模式拒；观察模式（ACL_SOFT=1）放行 + ACLSOFT。
   管理后台口令两种模式都能管（修前强制模式下它到不了处理器：guard 档没带 Bearer 直接 401）。
② NFC 卡映射被别家改不动：孩子卡（指向 / 覆盖别家孩子）与功能卡（记了主人 card_owners）都一样；
   观察模式放过去的越权不转移主人。
③ 设备表只读降级 → 503「设备登记暂时不可用」（修前写入抛 RuntimeError → 500）；强制模式匿名照样先 401；
   设备同步照常；降级本身的判定（读坏即留证、主文件不重建）不变。
④ 严格模式「hello / NFC 自报的 child_id 不采信」口径不变（生产 registry 对象上验）。
⑤ 墨水屏上屏按固件字库子集清洗（display.py），朗读文本不变。

隔离：tests.routehelp.make_app 把 app 落盘搬到 tmp_path；SQLite 单例换 tmp 库；SESSION_SECRET 用假值（conftest 已把
security._ENV_PATH 指到临时文件，绝不碰真实 .env）。便利贴设备表 / 记事本在仓库根、不在 routehelp 的搬法里——逐个改道到
tmp，**绝不碰仓库根的 pocketclaw_devices.json**。
"""
import asyncio
import json
import logging

import pytest
from fastapi.testclient import TestClient

from tests.routehelp import hdr, make_app

A = "own_a"
B = "own_b"
KID = "kid"
ADMIN = "t-admin-own-2026-09-28"
ADMIN_HDR = {"X-Admin-Token": ADMIN}

SELF_GUARDED = [
    ("GET", "/api/pocketclaw/devices"),
    ("POST", "/api/pocketclaw/devices/{d}/bind"),
    ("POST", "/api/pocketclaw/devices/{d}/push"),
    ("POST", "/api/pocketclaw/devices/{d}/focus"),
    ("POST", "/api/pocketclaw/devices/{d}/push_tasks"),
    ("POST", "/api/pocketclaw/cards/register"),
]
_BODY = {
    "/api/pocketclaw/devices/{d}/bind": {"child_id": A},
    "/api/pocketclaw/devices/{d}/push": {"title": "留言", "body": "早点睡"},
    "/api/pocketclaw/devices/{d}/focus": {"minutes": 5, "action": "start"},
    "/api/pocketclaw/cards/register": {"uid": "UID-ANON", "target": "mode:word"},
}


# ════════════════════════════════════════════════════════════════════════
# 夹具（同 test_hardening_2026_09_28）
# ════════════════════════════════════════════════════════════════════════
@pytest.fixture(scope="module", autouse=True)
def _restore_app_module():
    """make_app 会对进程里**共享的** `app` 模块做 reload + isolate；本文件结束后原样还原，
    免得紧跟着的、直接 `import app` 不 reload 的测试文件拿到一个被隔离过的模块。"""
    import importlib
    import pathlib
    import sys
    import tempfile
    mod = sys.modules.get("app")
    snap = dict(mod.__dict__) if mod is not None else None
    yield
    cur = sys.modules.get("app")
    if cur is None:
        return
    if snap is not None:
        cur.__dict__.clear()
        cur.__dict__.update(snap)
        return
    import security
    from store import db as _sdb
    tmpd = pathlib.Path(tempfile.mkdtemp(prefix="own_reload_"))
    old_env, old_db = security._ENV_PATH, _sdb._db
    security._ENV_PATH = tmpd / ".env.none"
    _sdb._db = _sdb.DB(str(tmpd / "reload.db"))
    try:
        importlib.reload(cur)
    finally:
        security._ENV_PATH = old_env
        _sdb._db = old_db


def _app(tmp_path, monkeypatch, *, soft: bool):
    monkeypatch.setenv("SESSION_SECRET", "own-2026-09-28-" + "s" * 32)
    from store import db as _sdb
    monkeypatch.setenv("POCKETCLAW_TEST_DB", str(tmp_path / "own.db"))
    monkeypatch.setattr(_sdb, "_db", _sdb.DB(str(tmp_path / "own.db")))
    appmod = make_app(tmp_path, monkeypatch, acl_soft=soft)
    from agent.llm import FakeLLM
    appmod.runtime.llm = FakeLLM(handler=lambda s, u: "好的")
    appmod.runtime.engine = None                    # 不打网络
    appmod.agent_engine._ark = lambda: None
    from authz import gate
    gate._soft_seen.clear()                         # ACLSOFT 60 秒去重是进程级的
    appmod._admin_tokens.add(ADMIN)
    return appmod


@pytest.fixture()
def forced(tmp_path, monkeypatch):
    return _app(tmp_path, monkeypatch, soft=False)


@pytest.fixture()
def soft(tmp_path, monkeypatch):
    return _app(tmp_path, monkeypatch, soft=True)


@pytest.fixture(params=["forced", "soft"])
def both(request, tmp_path, monkeypatch):
    return _app(tmp_path, monkeypatch, soft=(request.param == "soft"))


def _parent_token(*cids):
    import security
    return security.sign_token({"role": "parent", "pid": "own_parent", "children": list(cids)})


def _dev_token(m, cid, device_id=None):
    return m.auth.issue_device_token(cid, device_id=device_id)


def _pc(m, tmp_path):
    """PocketClaw 注册表在仓库根（不在 data/）——手工改道到 tmp，清空导入期读进来的存量，再摆四台设备：
    pc-a 绑 A、pc-b 绑 B、pc-new 未绑定（unbound-<hash> 隔离身份）、pc-kid 存量缺省 kid（2026-09-27 之前的老设备）。"""
    reg = m._pocketclaw["registry"]
    reg.path = str(tmp_path / "pc_devices.json")
    reg._devices, reg._queue, reg._cards, reg._modules, reg._card_owners = {}, {}, {}, {}, {}
    reg.degraded, reg._ro_yelled = "", False
    notes = m._pocketclaw["notes"]
    notes.path, notes._data = str(tmp_path / "pc_notes.json"), {}
    reg.upsert("pc-a")
    reg.bind_child("pc-a", A)
    reg.upsert("pc-b")
    reg.bind_child("pc-b", B)
    reg.upsert("pc-new")
    reg.upsert("pc-kid")
    reg._devices["pc-kid"]["child_id"] = KID           # 存量：旧缺省值，不是任何已认证方的绑定
    return reg


def _path(tpl: str, dev: str = "pc-a") -> str:
    return tpl.replace("{d}", dev)


def _call(c, method, tpl, dev="pc-a", headers=None, body=None):
    url = _path(tpl, dev)
    if method == "GET":
        return c.get(url, headers=headers or {})
    return c.post(url, json=(body if body is not None else _BODY.get(tpl, {})), headers=headers or {})


def _aclsoft(caplog, path: str) -> list:
    return [r.getMessage() for r in caplog.records if "ACLSOFT" in r.getMessage() and path in r.getMessage()]


# ════════════════════════════════════════════════════════════════════════
# 0. 分档：六条走处理器自校；设备侧 / 按孩子寻址的照旧；形状以外一律回落 guard
# ════════════════════════════════════════════════════════════════════════
def test_policy_classifies_the_six_as_self_guarded_and_nothing_else():
    from authz import policy
    for method, tpl in SELF_GUARDED:
        p = _path(tpl, "44:1B:F6:C4:D8:B0")
        assert policy.tier(p, method) == "public", p
        assert "处理器内部自校" in policy.exempt_reason(p, method), p
        assert not policy.legacy_guarded(p)          # 不在 legacy 强制档：观察模式的逃生阀对它们有效（同中间件）
    # 设备侧瘦端照旧（无令牌）；按孩子寻址的 modules 照旧走中间件
    assert policy.tier("/api/pocketclaw/devices/pc-a/sync", "GET") == "public"
    assert "瘦端" in policy.exempt_reason("/api/pocketclaw/devices/pc-a/sync", "GET")
    assert policy.tier(f"/api/pocketclaw/{A}/modules", "POST") == "guard"
    assert policy.tier(f"/api/pocketclaw/{A}/modules", "GET") == "guard"
    # 形状以外的（多一段 / 空 id / 没见过的动作）一律回落 guard＝默认拒绝，不会被顺手放行
    for p in ("/api/pocketclaw/devices/pc-a/rename", "/api/pocketclaw/devices/pc-a/push/x",
              "/api/pocketclaw/devices//push", "/api/pocketclaw/devices/", "/api/pocketclaw/devices/pc-a",
              "/api/pocketclaw/cards/register/x", "/api/pocketclaw/cards"):
        assert policy.tier(p, "POST") == "guard", p


def test_self_guarded_shapes_only_match_pocketclaw_handlers(forced):
    """policy 放行的形状必须恰好对应 pocketclaw/endpoint.py 里那六个处理器——别处（如 app.py）哪天在同形状上
    新加一条路由，它不会自校，却会被中间件整条放行；这条把它当场钉红。"""
    from fastapi.routing import APIRoute
    from authz import policy
    hits = {}
    for r in forced.app.routes:
        if not isinstance(r, APIRoute):
            continue
        probe = r.path.replace("{device_id}", "pc-probe")
        if policy._pocket_self_guarded(probe):
            for meth in r.methods:
                hits[(meth, probe)] = r.endpoint.__module__
    want = {(m, _path(t, "pc-probe")) for m, t in SELF_GUARDED}
    assert set(hits) == want, sorted(set(hits) ^ want)
    assert set(hits.values()) == {"pocketclaw.endpoint"}, hits


# ════════════════════════════════════════════════════════════════════════
# ① 强制模式：匿名 401、别家 403、自家与管理后台放行
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("method,tpl", SELF_GUARDED)
def test_forced_anonymous_and_bad_tokens_are_401(forced, tmp_path, method, tpl):
    reg = _pc(forced, tmp_path)
    c = TestClient(forced.app)
    for headers in ({}, {"Authorization": "Bearer not.a.real.token"}, {"X-Admin-Token": "guessed-admin-token"},
                    {"X-Admin-Token": ""}):
        r = _call(c, method, tpl, headers=headers)
        assert r.status_code == 401, (headers, r.text)
    assert reg.pending_count("pc-a") == 0 and reg.card_target("UID-ANON")["kind"] == ""
    assert reg.get("pc-a").child_id == A


def test_forced_push_ownership(forced, tmp_path):
    reg = _pc(forced, tmp_path)
    c = TestClient(forced.app)
    url = "/api/pocketclaw/devices/{}/push"
    body = {"title": "留言", "body": "早点睡"}
    # 反例：别家的家长令牌 / 别家的设备令牌 / 名下没孩子的令牌 → 403，队列不动
    for tok in (_parent_token(B), _dev_token(forced, B), _parent_token()):
        r = c.post(url.format("pc-a"), json=body, headers=hdr(tok))
        assert r.status_code == 403 and r.json()["ok"] is False, r.text
        assert A not in r.text                                         # 不回带别家孩子
    # 设备令牌自称的 device_id 恰好就是这台便利贴，也不算数：判据是孩子，不是令牌自称的设备
    r = c.post(url.format("pc-a"), json=body, headers=hdr(_dev_token(forced, B, device_id="pc-a")))
    assert r.status_code == 403
    assert reg.pending_count("pc-a") == 0
    # 未绑定 / 不存在 / 存量 kid（令牌名下没有 kid）/ 别家：同一个 403、同一句话，不给「设备存不存在」当探针
    texts = set()
    for dev in ("pc-new", "pc-ghost", "pc-kid", "pc-b"):
        r = c.post(url.format(dev), json=body, headers=hdr(_parent_token(A)))
        assert r.status_code == 403, (dev, r.text)
        texts.add(r.json()["error"])
    assert len(texts) == 1 and reg.get("pc-ghost") is None
    assert all(reg.pending_count(d) == 0 for d in ("pc-new", "pc-ghost", "pc-kid", "pc-b"))
    # 正例：自家家长令牌 / 自家平板（设备令牌）/ 两家都管的家长 / kid 平板推 kid 设备 / 管理后台口令
    assert c.post(url.format("pc-a"), json=body, headers=hdr(_parent_token(A))).status_code == 200
    assert c.post(url.format("pc-a"), json=body, headers=hdr(_dev_token(forced, A))).status_code == 200
    assert c.post(url.format("pc-b"), json=body, headers=hdr(_parent_token(A, B))).status_code == 200
    assert c.post(url.format("pc-kid"), json=body, headers=hdr(_dev_token(forced, KID))).status_code == 200
    r = c.post(url.format("pc-b"), json=body, headers=ADMIN_HDR)
    assert r.status_code == 200 and r.json()["ok"] is True
    assert reg.pending_count("pc-a") == 2 and reg.pending_count("pc-b") == 2 and reg.pending_count("pc-kid") == 1
    # 未绑定的设备只许绑定——管理后台也一样（先绑再推），但文案说实话
    r = c.post(url.format("pc-new"), json=body, headers=ADMIN_HDR)
    assert r.status_code == 403 and "绑定" in r.json()["error"] and reg.pending_count("pc-new") == 0


class _FakeSess:
    """在线会话替身：只记下远程指令（真会话要起 WS 事件循环，这里只验归属闸）。"""

    def __init__(self):
        self.calls = []

    async def _start_focus(self, spec, now):
        self.calls.append(("start", spec))

    async def _focus_control(self, act, now):
        self.calls.append((act,))


def test_forced_focus_ownership(forced, tmp_path, monkeypatch):
    _pc(forced, tmp_path)
    live = forced._pocketclaw["live"]
    c = TestClient(forced.app)
    url = "/api/pocketclaw/devices/{}/focus"
    start = {"minutes": 5, "action": "start"}
    # 离线：自家过了归属闸才拿到「不在线」；别家先 403——不给「在不在线」当探针
    r = c.post(url.format("pc-a"), json=start, headers=hdr(_parent_token(A)))
    assert r.status_code == 200 and r.json() == {"ok": False, "error": "设备不在线", "online": False}
    assert c.post(url.format("pc-a"), json=start, headers=hdr(_parent_token(B))).status_code == 403
    assert c.post(url.format("pc-ghost"), json=start, headers=hdr(_parent_token(A))).status_code == 403
    sess, ghost = _FakeSess(), _FakeSess()
    monkeypatch.setitem(live, "pc-a", sess)
    monkeypatch.setitem(live, "pc-new", ghost)
    for tok in (_dev_token(forced, B), _parent_token(B)):
        assert c.post(url.format("pc-a"), json=start, headers=hdr(tok)).status_code == 403
    assert sess.calls == []
    r = c.post(url.format("pc-a"), json=start, headers=hdr(_dev_token(forced, A)))
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["minutes"] == 5, r.text
    r = c.post(url.format("pc-a"), json={"action": "pause"}, headers=ADMIN_HDR)
    assert r.json() == {"ok": True, "action": "pause"} and sess.calls[-1] == ("pause",)
    # 在线但未绑定：谁都不能远程开（管理后台也不行，先绑定）
    for h in (hdr(_parent_token(A)), ADMIN_HDR):
        assert c.post(url.format("pc-new"), json=start, headers=h).status_code == 403
    assert ghost.calls == []
    assert c.post(url.format("pc-a"), json=start).status_code == 401


def test_forced_push_tasks_ownership(forced, tmp_path):
    reg = _pc(forced, tmp_path)
    forced._pocketclaw["notes"].add_task(A, "口算 20 题", subject="数学")
    c = TestClient(forced.app)
    url = "/api/pocketclaw/devices/{}/push_tasks"
    for tok in (_parent_token(B), _dev_token(forced, B)):
        r = c.post(url.format("pc-a"), headers=hdr(tok))
        assert r.status_code == 403 and A not in r.text and "口算" not in r.text, r.text
    assert reg.pending_count("pc-a") == 0
    assert c.post(url.format("pc-a")).status_code == 401
    r = c.post(url.format("pc-a"), headers=hdr(_dev_token(forced, A)))
    assert r.status_code == 200 and r.json()["child_id"] == A and reg.pending_count("pc-a") == 1
    r = c.post(url.format("pc-a"), headers=ADMIN_HDR)
    assert r.status_code == 200 and reg.pending_count("pc-a") == 2
    # 既有口径不变：不存在 / 未绑定 → 404
    assert c.post(url.format("pc-ghost"), headers=hdr(_parent_token(A))).status_code == 404
    assert c.post(url.format("pc-new"), headers=ADMIN_HDR).status_code == 404


def test_forced_bind_rules(forced, tmp_path):
    reg = _pc(forced, tmp_path)
    c = TestClient(forced.app)
    url = "/api/pocketclaw/devices/{}/bind"
    # 目标孩子不在令牌名下 → 403
    for tok in (_parent_token(A), _dev_token(forced, A), _parent_token()):
        r = c.post(url.format("pc-new"), json={"child_id": B}, headers=hdr(tok))
        assert r.status_code == 403, r.text
    # 被拒的请求不给注册表建档
    assert c.post(url.format("pc-ghost"), json={"child_id": B}, headers=hdr(_parent_token(A))).status_code == 403
    assert reg.get("pc-ghost") is None
    # 缺 child_id / 指向未绑定身份 → 400（本路由不走中间件，不再「按令牌代填第一个孩子」）
    for body in ({}, {"child_id": ""}, {"child_id": "unbound-0123456789ab"}):
        assert c.post(url.format("pc-new"), json=body, headers=hdr(_parent_token(A))).status_code == 400, body
    # 正例：自家平板把新设备绑到自己的孩子（未绑定的设备唯一允许的动作）
    r = c.post(url.format("pc-new"), json={"child_id": A}, headers=hdr(_dev_token(forced, A)))
    assert r.status_code == 200 and reg.get("pc-new").child_id == A and reg.bound_children("pc-new") == [A]
    # 别家不能把已绑到真实孩子的设备改绑走（家长令牌 / 设备令牌都不行），绑定不动
    for tok in (_parent_token(B), _dev_token(forced, B)):
        r = c.post(url.format("pc-a"), json={"child_id": B}, headers=hdr(tok))
        assert r.status_code == 403 and reg.get("pc-a").child_id == A, r.text
    # 存量 kid 设备：任何一家都能把它领到自己孩子名下（同修前）
    r = c.post(url.format("pc-kid"), json={"child_id": B}, headers=hdr(_parent_token(B)))
    assert r.status_code == 200 and reg.get("pc-kid").child_id == B
    # 管理后台口令管全部：可以把 A 家的设备改绑到 B
    r = c.post(url.format("pc-a"), json={"child_id": B}, headers=ADMIN_HDR)
    assert r.status_code == 200 and reg.get("pc-a").child_id == B
    # 绑定是落了盘的（重读还在）
    from pocketclaw.device_registry import DeviceRegistry
    again = DeviceRegistry(path=reg.path, strict=True)
    assert again.get("pc-new").child_id == A and again.get("pc-a").child_id == B


def test_forced_device_list(forced, tmp_path):
    _pc(forced, tmp_path)
    c = TestClient(forced.app)

    def ids(headers):
        r = c.get("/api/pocketclaw/devices", headers=headers)
        assert r.status_code == 200, r.text
        return sorted(d["device_id"] for d in r.json()["devices"])

    assert ids(hdr(_parent_token(A))) == ["pc-a"]
    assert ids(hdr(_dev_token(forced, B))) == ["pc-b"]
    assert ids(hdr(_parent_token(A, B))) == ["pc-a", "pc-b"]
    assert ids(hdr(_parent_token())) == []
    assert ids(ADMIN_HDR) == ["pc-a", "pc-b", "pc-kid", "pc-new"]           # 管理后台看全部（含未绑定）
    assert c.get("/api/pocketclaw/devices").status_code == 401


# ════════════════════════════════════════════════════════════════════════
# ② NFC 卡映射：别家改不动
# ════════════════════════════════════════════════════════════════════════
def test_forced_card_mapping_cannot_be_changed_by_another_family(forced, tmp_path):
    reg = _pc(forced, tmp_path)
    c = TestClient(forced.app)
    url = "/api/pocketclaw/cards/register"
    # 功能卡：A 家登记后记上主人
    r = c.post(url, json={"uid": "UID-F", "target": "mode:word"}, headers=hdr(_parent_token(A)))
    assert r.status_code == 200 and reg.card_owners("UID-F") == [A]
    # B 家（家长令牌 / 平板设备令牌）改不动——换成别的功能卡、换成指向自家孩子的卡都不行
    for tok in (_parent_token(B), _dev_token(forced, B)):
        for tgt in ("mode:note", "skill:math-tutor-01", f"child:{B}"):
            r = c.post(url, json={"uid": "UID-F", "target": tgt}, headers=hdr(tok))
            assert r.status_code == 403, (tgt, r.text)
    assert reg.card_target("UID-F") == {"kind": "mode", "value": "word"} and reg.card_owners("UID-F") == [A]
    # A 家自己的平板能改；管理后台能改且不改主人（B 家之后照样改不动）
    r = c.post(url, json={"uid": "UID-F", "target": "mode:task"}, headers=hdr(_dev_token(forced, A)))
    assert r.status_code == 200 and reg.card_target("UID-F")["value"] == "task"
    r = c.post(url, json={"uid": "UID-F", "target": "mode:book"}, headers=ADMIN_HDR)
    assert r.status_code == 200 and reg.card_owners("UID-F") == [A]
    assert c.post(url, json={"uid": "UID-F", "target": "mode:note"}, headers=hdr(_parent_token(B))).status_code == 403
    # 孩子卡：指向别家孩子 → 403 不落库；A 家的登录卡 B 家覆盖不了（改成功能卡也不行）
    r = c.post(url, json={"uid": "UID-C", "target": f"child:{B}"}, headers=hdr(_parent_token(A)))
    assert r.status_code == 403 and reg.card_target("UID-C")["kind"] == ""
    assert c.post(url, json={"uid": "UID-C", "target": f"child:{A}"}, headers=hdr(_parent_token(A))).status_code == 200
    for tgt in ("mode:word", f"child:{B}"):
        assert c.post(url, json={"uid": "UID-C", "target": tgt}, headers=hdr(_parent_token(B))).status_code == 403
    assert reg.card_target("UID-C") == {"kind": "child", "value": A}
    # 管理后台把它改成 B 的孩子卡 → 主人随之是 B
    assert c.post(url, json={"uid": "UID-C", "target": f"child:{B}"}, headers=ADMIN_HDR).status_code == 200
    assert reg.card_owners("UID-C") == [B]
    # 名下一个孩子都没有的令牌：登上去就是一张谁都能改的卡 → 403
    r = c.post(url, json={"uid": "UID-N", "target": "mode:word"}, headers=hdr(_parent_token()))
    assert r.status_code == 403 and reg.card_target("UID-N")["kind"] == ""
    # 没记主人的存量卡：有效令牌可以认领，认领后别家就改不动了
    reg._cards["UID-L"] = "mode:word"
    assert c.post(url, json={"uid": "UID-L", "target": "mode:note"}, headers=hdr(_parent_token(B))).status_code == 200
    assert reg.card_owners("UID-L") == [B]
    assert c.post(url, json={"uid": "UID-L", "target": "mode:task"}, headers=hdr(_parent_token(A))).status_code == 403
    # 卡号无效 → 400
    for uid in ("", "   ", "X" * 129):
        assert c.post(url, json={"uid": uid, "target": "mode:word"}, headers=hdr(_parent_token(A))).status_code == 400
    # 主人是落了盘的：重读注册表后照样挡
    from pocketclaw.device_registry import DeviceRegistry
    again = DeviceRegistry(path=reg.path, strict=True)
    assert again.card_owners("UID-F") == [A] and again.card_owners("UID-L") == [B]
    assert again.card_target("UID-F") == {"kind": "mode", "value": "book"}


# ════════════════════════════════════════════════════════════════════════
# ①′ 观察模式：该拒的照样放行、每条都留 ACLSOFT；自家与管理后台不留；放过去的越权不转移卡的主人
# ════════════════════════════════════════════════════════════════════════
def test_soft_mode_lets_violations_through_with_aclsoft(soft, tmp_path, monkeypatch, caplog):
    from authz import gate
    reg = _pc(soft, tmp_path)
    c = TestClient(soft.app)
    monkeypatch.setitem(soft._pocketclaw["live"], "pc-a", _FakeSess())
    assert c.post("/api/pocketclaw/cards/register", json={"uid": "UID-F", "target": "mode:word"},
                  headers=hdr(_parent_token(A))).status_code == 200
    foreign = hdr(_parent_token(B))
    cases = [                                            # (method, url, body, headers)：强制模式下每一条都会被拒
        ("GET", "/api/pocketclaw/devices", None, {}),
        ("POST", "/api/pocketclaw/devices/pc-a/push", {"body": "x"}, foreign),
        ("POST", "/api/pocketclaw/devices/pc-a/push", {"body": "x"}, {}),
        ("POST", "/api/pocketclaw/devices/pc-a/focus", {"action": "pause"}, foreign),
        ("POST", "/api/pocketclaw/devices/pc-a/push_tasks", None, foreign),
        ("POST", "/api/pocketclaw/devices/pc-new/bind", {"child_id": A}, foreign),
        ("POST", "/api/pocketclaw/cards/register", {"uid": "UID-F", "target": "mode:note"}, foreign),
    ]
    for method, url, body, headers in cases:
        gate._soft_seen.clear()
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            r = c.get(url, headers=headers) if method == "GET" else c.post(url, json=body or {}, headers=headers)
        assert r.status_code == 200, (url, headers, r.text)
        assert _aclsoft(caplog, url), (url, [x.getMessage() for x in caplog.records])
    assert reg.pending_count("pc-a") == 3 and reg.get("pc-new").child_id == A
    # 观察模式放过去的越权：映射照写（同修前），但**主人不转移**——之后 A 家照样能改回来、B 家在强制模式下照样改不动
    assert reg.card_target("UID-F")["value"] == "note" and reg.card_owners("UID-F") == [A]
    # 自家令牌、管理后台口令：不留 ACLSOFT
    for headers in (hdr(_parent_token(A)), hdr(_dev_token(soft, A)), ADMIN_HDR):
        gate._soft_seen.clear()
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            r = c.post("/api/pocketclaw/devices/pc-a/push", json={"body": "x"}, headers=headers)
        assert r.status_code == 200 and not _aclsoft(caplog, "/api/pocketclaw/"), \
            [x.getMessage() for x in caplog.records]


# ════════════════════════════════════════════════════════════════════════
# ③ 只读降级 → 503（先过身份闸）
# ════════════════════════════════════════════════════════════════════════
_PC_RAW = ('{"devices": {"pc-a": {"device_id": "pc-a", "child_id": "%s", "bound_children": ["%s"]}}, '
           '"cards": {"UID-1": "child:%s"}, "queue": {"pc-a": [{"id": "c1", "kind": "content", "page": {"body": ["半截'
           % (A, A, A))

_DEGRADED_CALLS = [
    ("GET", "/api/pocketclaw/devices", None),
    ("POST", "/api/pocketclaw/devices/pc-a/bind", {"child_id": A}),
    ("POST", "/api/pocketclaw/devices/pc-a/push", {"body": "早点睡"}),
    ("POST", "/api/pocketclaw/devices/pc-a/focus", {"minutes": 5}),
    ("POST", "/api/pocketclaw/devices/pc-a/push_tasks", None),
    ("POST", "/api/pocketclaw/cards/register", {"uid": "UID-9", "target": "mode:word"}),
    ("POST", f"/api/pocketclaw/{A}/modules", {"modules": ["word"]}),
]


def _degrade(m, tmp_path) -> tuple:
    """就地让生产路由闭包里的那个 registry 读坏一份文件（同 test_hardening_2026_09_28b）。"""
    reg = m._pocketclaw["registry"]
    p = tmp_path / "pc_devices.json"
    p.write_text(_PC_RAW, encoding="utf-8")
    reg.path = str(p)
    reg._devices, reg._queue, reg._cards, reg._modules, reg._card_owners = {}, {}, {}, {}, {}
    reg.degraded, reg._ro_yelled = "", False
    notes = m._pocketclaw["notes"]
    notes.path, notes._data = str(tmp_path / "pc_notes.json"), {}
    reg._load()
    assert reg.degraded
    ev = sorted(p.parent.glob(p.name + ".corrupt-*"))
    assert len(ev) == 1 and not p.exists()
    return reg, p, ev[0]


def _send(c, method, url, body, headers):
    return c.get(url, headers=headers) if method == "GET" else c.post(url, json=body or {}, headers=headers)


def test_degraded_registry_is_503_in_both_modes(both, tmp_path):
    reg, p, ev = _degrade(both, tmp_path)
    c = TestClient(both.app, raise_server_exceptions=False)
    for method, url, body in _DEGRADED_CALLS:
        for headers in (hdr(_parent_token(A)), hdr(_dev_token(both, A)), ADMIN_HDR):
            if url.endswith("/modules") and headers is ADMIN_HDR:
                continue                            # modules 按孩子寻址、走中间件，不认管理后台口令（同修前）
            r = _send(c, method, url, body, headers)
            assert r.status_code == 503, (url, r.status_code, r.text)
            assert r.json() == {"ok": False, "error": "便利贴的设备登记暂时不可用，请稍后再试"}, r.text
    # 设备侧照常：同步回空、不抛
    r = c.get("/api/pocketclaw/devices/pc-a/sync")
    assert r.status_code == 200 and r.json() == {"items": []}
    # 降级本身不变：内存没被改、主文件没重建、留证字节不变、health 点名
    assert reg._cards == {} and reg._card_owners == {} and reg._queue.get("pc-a", []) == [] and reg._modules == {}
    assert not p.exists() and ev.read_bytes() == _PC_RAW.encode("utf-8")
    assert "_pocketclaw.registry" in c.get("/api/health").json()["degraded"]


def test_degraded_registry_forced_anonymous_is_still_401_first(forced, tmp_path):
    _degrade(forced, tmp_path)
    c = TestClient(forced.app, raise_server_exceptions=False)
    for method, url, body in _DEGRADED_CALLS:
        r = _send(c, method, url, body, {})
        assert r.status_code == 401, (url, r.status_code, r.text)


def test_registry_read_only_error_is_a_runtimeerror_subclass(tmp_path):
    """REST 只把「只读降级」翻成 503：异常类型单列，但仍是 RuntimeError（既有 pytest.raises(RuntimeError) 照样成立）。"""
    from pocketclaw.device_registry import DeviceRegistry, RegistryReadOnly
    p = tmp_path / "pc_devices.json"
    p.write_text(_PC_RAW, encoding="utf-8")
    reg = DeviceRegistry(path=str(p), strict=True)
    assert reg.degraded and issubclass(RegistryReadOnly, RuntimeError)
    for fn in (lambda: reg.bind_child("pc-a", A), lambda: reg.register_card("UID-2", "mode:word", owners=[A]),
               lambda: reg.set_modules(A, ["word"]), lambda: reg.push_content("pc-a", {"body": ["x"]})):
        with pytest.raises(RegistryReadOnly):
            fn()
    assert reg._cards == {} and reg._card_owners == {} and not p.exists()


# ════════════════════════════════════════════════════════════════════════
# ④ 严格模式「自报不采信」不变
# ════════════════════════════════════════════════════════════════════════
class _Sink:
    def __init__(self):
        self.json = []

    async def send_json(self, s):
        self.json.append(json.loads(s))

    async def send_bin(self, b):
        pass

    def first(self, kind):
        return next((x for x in self.json if x.get("kind") == kind), None)

    def last(self, kind):
        return next((x for x in reversed(self.json) if x.get("kind") == kind), None)


class _RecRuntime:
    def __init__(self, reply="好的"):
        self.calls = []
        self.reply = reply

    async def turn(self, text, **kw):
        self.calls.append((text, kw.get("child_id")))
        return {"kind": "chat_turn", "text": self.reply, "banners": [], "blocks": [], "chips": []}


def test_strict_identity_unchanged_on_the_production_registry(forced, tmp_path):
    from pocketclaw.bridge import PocketClawSession
    from pocketclaw.device_registry import is_unbound
    from pocketclaw.notes import NotesStore
    from pocketclaw.voice import OfflineSTT, OfflineTTS
    reg = _pc(forced, tmp_path)
    assert reg.strict is True                                  # 生产入口默认严格
    c = TestClient(forced.app)
    # 管理后台把一张空卡登记成 B 的孩子卡（管理员能登），但它换不动一台没绑过 B 的设备
    assert c.post("/api/pocketclaw/cards/register", json={"uid": "UID-B", "target": f"child:{B}"},
                  headers=ADMIN_HDR).status_code == 200
    rt, sink = _RecRuntime(), _Sink()                          # daily_spark=None：不起早安推屏、不打网络
    sess = PocketClawSession(rt, send_json=sink.send_json, send_bin=sink.send_bin, stt=OfflineSTT(), tts=OfflineTTS(),
                             registry=reg, notes=NotesStore(path=":memory:"))
    asyncio.run(sess.on_control({"kind": "hello", "device_id": "pc-a", "child_id": B}))       # 自报 B
    assert sink.first("hello_ack")["child_id"] == A and reg.get("pc-a").child_id == A
    asyncio.run(sess.on_control({"kind": "nfc", "uid": "04AA", "ndef": "child:" + B}))        # 卡上写 B
    asyncio.run(sess.on_control({"kind": "nfc", "uid": "UID-B", "ndef": ""}))                 # 登记表映射到 B
    assert sess.child_id == A and reg.get("pc-a").child_id == A and reg.bound_children("pc-a") == [A]
    # 新设备：自报不采信，进隔离身份、不跑回合
    rt2, sink2 = _RecRuntime(), _Sink()
    s2 = PocketClawSession(rt2, send_json=sink2.send_json, send_bin=sink2.send_bin, stt=OfflineSTT(),
                           tts=OfflineTTS(), registry=reg, notes=NotesStore(path=":memory:"))
    asyncio.run(s2.on_control({"kind": "hello", "device_id": "pc-fresh", "child_id": A}))
    asyncio.run(s2.on_control({"kind": "text", "text": "讲讲光合作用"}))
    assert is_unbound(sink2.first("hello_ack")["child_id"]) and rt2.calls == []
    # 真 WS 入口同口径：hello 前的业务帧被拒
    with c.websocket_connect("/pocketclaw") as ws:
        ws.send_text(json.dumps({"kind": "text", "text": "没握手就想聊"}))
        m = json.loads(ws.receive_text())
        assert m["kind"] == "error" and m["code"] == "hello_required"


# ════════════════════════════════════════════════════════════════════════
# ⑤ 墨水屏上屏：按固件字库子集清洗，朗读文本不变
# ════════════════════════════════════════════════════════════════════════
_FONT_DIR = ("pocketclaw/firmware/zectrix/main/components/78__xiaozhi-fonts/src/")


def _lvgl_font_charset(path) -> set:
    """解析 lv_font_conv 生成的 LVGL 字库 C 文件的 cmap，返回它**真有字形**的码位集合。
    FORMAT0_TINY：整段都有；FORMAT0_FULL：偏移表里 0 不在首位＝缺字（LVGL lv_font_fmt_txt.c 的判法）；
    SPARSE_*：unicode_list 里列出的才有。"""
    import re
    src = open(path, encoding="utf-8", errors="replace").read()
    arrays = {}
    for m in re.finditer(r"static const (?:uint8_t|uint16_t) ((?:unicode_list|glyph_id_ofs_list)_\d+)\[\]\s*=\s*\{(.*?)\};",
                         src, re.S):
        body = re.sub(r"/\*.*?\*/", "", m.group(2), flags=re.S)
        arrays[m.group(1)] = [int(x, 0) for x in re.findall(r"0x[0-9a-fA-F]+|\d+", body)]
    table = re.search(r"static const lv_font_fmt_txt_cmap_t cmaps\[\]\s*=\s*\{(.*?)\n\};", src, re.S).group(1)
    cps = set()
    for rs, rl, ul, gl, ll, typ in re.findall(
            r"\.range_start = (\d+), \.range_length = (\d+), \.glyph_id_start = \d+,\s*"
            r"\.unicode_list = (\w+), \.glyph_id_ofs_list = (\w+), \.list_length = (\d+), \.type = (\w+)", table):
        rs, rl, ll = int(rs), int(rl), int(ll)
        if typ.endswith("FORMAT0_TINY"):
            cps.update(range(rs, rs + rl))
        elif typ.endswith("FORMAT0_FULL"):
            ofs = arrays[gl]
            assert len(ofs) == rl
            cps.update(rs + i for i, o in enumerate(ofs) if i == 0 or o != 0)
        else:
            lst = arrays[ul]
            assert len(lst) == ll and typ.split("_")[-2] == "SPARSE", typ
            cps.update(rs + x for x in lst)
    glyphs = len(re.findall(r"\{\.bitmap_index = ", src)) - 1          # 去掉 id 0 的保留项
    assert glyphs == len(cps), (path, glyphs, len(cps))                    # 一个字形恰好一个码位：解析没漏没多
    return cps


@pytest.mark.parametrize("font", ["SourceHanSansSC_Medium_slim.c", "SourceHanSansSC_Regular_slim.c"])
def test_screen_charset_is_exactly_the_firmware_font(font):
    """display.SCREEN_CHARSET 就是固件 slim 字库的真字表（ASCII 走 Montserrat，另算），固件换字库这里当场红。"""
    from pathlib import Path
    from pocketclaw import display
    p = Path(__file__).resolve().parent.parent / _FONT_DIR / font
    if not p.exists():
        pytest.skip(f"固件字库不在本机：{p}")
    cps = _lvgl_font_charset(p)
    assert {chr(c) for c in cps} == set(display.SCREEN_CHARSET)
    assert len(display.SCREEN_CHARSET) == display.SCREEN_GLYPH_COUNT == 6905
    # 审查点名的这些都**不在**字库里——换成子集里的写法或去掉
    for ch in "「」『』✓☑☐■●━▲▼⚠～–\u3000🎉👋😀☀\ufe0f\u200d":
        assert ch not in display.SCREEN_CHARSET, ch
    for ch in "“”‘’√×·—…《》【】（），。！？：；、":
        assert ch in display.SCREEN_CHARSET, ch


def _only_font_glyphs(s: str) -> bool:
    from pocketclaw import display
    return all(c in display.SCREEN_CHARSET or c in "\n\x01" for c in s)


@pytest.mark.parametrize("raw,want", [
    ("你好 👋", "你好"),
    ("全部完成 🎉", "全部完成"),
    ("🎉 太棒了", "太棒了"),
    ("太棒了 🎉 继续加油", "太棒了 继续加油"),
    ("第一行 🎉\n🎉 第二行", "第一行\n第二行"),
    ("记好啦 ✓", "记好啦 √"),
    ("✅ 对了 ❌ 错了", "√ 对了 × 错了"),
    ("说「记一下…」试试", "说“记一下…”试试"),
    ("『内层』", "‘内层’"),
    ("❤\ufe0f喜欢 👨\u200d👩\u200d👧 一家人", "喜欢 一家人"),                 # 变体选择符 / 零宽连接符一并去掉
    ("1\ufe0f\u20e3 第一步", "1 第一步"),
    ("ＡＢＣ１２３～", "ABC123~"),
    ("½ 个苹果", "1/2 个苹果"),
    ("café", "cafe"),
    ("\u3000\u3000例句", "    例句"),                                 # 全角空格 → 同宽的两个半角空格
    ("王喆同学", "王  同学"),                                          # 字库外的生僻字：留同宽空白，不让前后粘连
    ("\x01· 已完成\n· 未完成", "\x01· 已完成\n· 未完成"),               # 行首删除线哨兵原样保留
    ("普通的一句话。没有任何问题！", "普通的一句话。没有任何问题！"),
])
def test_screen_text_replaces_or_drops_out_of_font_glyphs(raw, want):
    from pocketclaw import display
    out = display.screen_text(raw)
    assert out == want and _only_font_glyphs(out)
    assert display.screen_text(out) == out                          # 幂等


def test_every_renderer_and_outgoing_frame_is_font_clean():
    from pocketclaw import display, focus, protocol
    from types import SimpleNamespace
    dirty = "好呀「加油」✓🎉 用『凑十法』：①先凑十\u3000②再算 👍"
    pages = [
        display.render_turn({"text": dirty, "banners": [{"text": "⚠ 已拦下代写 🚫"}], "chips": ["再来 🔁", "「提示」"]},
                            heard="我不会 😭", cols=display.CHAT_COLS),
        display.render_lines("记好啦 ✓", dirty),
        display.render_checklist("今日作业 📚", [("口算 ✏\ufe0f 20 题", False), ("背「静夜思」", True)]),
        display.render_wordcard(SimpleNamespace(word="café", meaning="咖啡馆 ☕", example="Let's meet at the café 🙂",
                                                example_cn="我们在咖啡馆见 😊")),
        display.render_spark({"title": "恐龙 🦖 为什么「灭绝」了？", "emoji": "🦖"}, due_n=2, date_label="9月28日"),
    ]
    for page in pages:
        for field in ("title", "banner"):
            assert _only_font_glyphs(page.get(field) or ""), (field, page.get(field))
        for field in ("heard", "body", "chips"):
            for ln in page.get(field) or []:
                assert _only_font_glyphs(ln), (field, ln)
    assert "“加油”" in "".join(pages[0]["body"]) and "√" in "".join(pages[0]["body"])
    # 作业清单：做完 √、没做完 ·（修前的 ☑/☐ 不在字库里，设备上那一列是空的）
    assert pages[2]["body"][0].startswith("· 口算") and pages[2]["body"][1].startswith("√ 背“静夜思”")
    assert pages[4]["emoji"] == "🦖"                                 # 早安屏 emoji 仍只作数据带下去（固件不上屏）
    # 出站帧：display.py 之外拼的页（花火屏）与识别文本帧同样清洗
    ask = json.loads(protocol.display_page(focus.render_ask_minutes_page(battery=80)))["page"]
    assert all(_only_font_glyphs(ln) for ln in ask["body"]) and "说“倒计时 10 分钟”" in ask["body"]
    t = focus.FocusTimer("pomodoro", 25, now=0.0)
    for raw in (focus.render_focus_page(t, now=600.0), focus.render_done_page(t)):
        pg = json.loads(protocol.display_page(raw))["page"]
        assert all(_only_font_glyphs(ln) for ln in [pg["title"]] + pg["body"]), pg
    assert json.loads(protocol.stt_result("讲讲🌈彩虹「为什么」"))["text"] == "讲讲彩虹“为什么”"
    # 调用方的 dict 不被改（screen_page 返回浅拷贝）
    raw = {"title": "你好 👋", "body": ["🎉"]}
    json.loads(protocol.display_page(raw))
    assert raw == {"title": "你好 👋", "body": ["🎉"]}


class _RecTTS:
    """记下被要求朗读的原文（无流式接口 → bridge 走整段合成）。"""
    sample_rate = 16000

    def __init__(self):
        self.texts = []

    async def synthesize(self, text):
        self.texts.append(text)
        return b"\x00\x00" * 160


def test_session_screen_is_clean_but_speech_text_is_unchanged(monkeypatch):
    from pocketclaw import bridge, cards
    from pocketclaw.bridge import PocketClawSession
    from pocketclaw.device_registry import DeviceRegistry
    from pocketclaw.notes import NotesStore
    from pocketclaw.voice import OfflineSTT
    monkeypatch.setattr(bridge, "_WORD_AUDIO_CACHE", {})            # 进程级发音缓存：本用例独享一份
    reply = "好呀「加油」✓🎉 我们一起想想。"
    rt, sink, tts = _RecRuntime(reply), _Sink(), _RecTTS()
    notes = NotesStore(path=":memory:")
    notes._bucket("kid")["_seeded"] = True                           # 不播种预设词表：单词包里只有本用例加的词
    sess = PocketClawSession(rt, send_json=sink.send_json, send_bin=sink.send_bin, stt=OfflineSTT(), tts=tts,
                             registry=DeviceRegistry(path=":memory:"), notes=notes)
    asyncio.run(sess.on_control({"kind": "hello", "device_id": "pc-screen"}))
    asyncio.run(sess.on_control({"kind": "text", "text": "讲讲🌈彩虹"}))
    assert rt.calls and rt.calls[-1][0].endswith("讲讲🌈彩虹")      # 喂给主脑的是孩子的原话
    assert sink.last("stt")["text"] == "讲讲彩虹"
    page = sink.last("display")["page"]
    body = "".join(page["body"])
    assert "“加油”" in body and "√" in body and all(_only_font_glyphs(ln) for ln in page["body"]), page
    assert not any(ch in body for ch in "「」✓🎉")
    assert tts.texts == [reply]                                      # 朗读的是原文，一个字没动
    # 单词卡 / 卡片轮播的端上缓存包：固件本地画上屏 → 同样清洗；发音照原词合成
    notes.add_word("kid", "café", meaning="咖啡馆 ☕", example="See you at the café 🙂")
    tts.texts.clear()
    asyncio.run(sess._serve_word(0.0))
    pack = sink.last("word_pack")["words"]
    for w in pack:
        assert all(_only_font_glyphs(w[k] or "") for k in ("w", "m", "e", "ec")), w
    assert any(w["w"] == "cafe" and w["m"] == "咖啡馆" for w in pack)
    assert "café" in tts.texts
    monkeypatch.setattr(cards, "painting_bitmap", lambda name: b"")   # 不推名画位图
    monkeypatch.setattr(cards, "build_deck", lambda: [{"type": "poem", "title": "「静夜思」🌙", "footer": "—— 李白",
                                                      "lines": ["床前明月光，✨", "疑是地上霜。"]}])
    asyncio.run(sess._serve_carousel())
    card = sink.last("card_pack")["cards"][0]
    assert card["title"] == "“静夜思”" and card["lines"] == ["床前明月光，", "疑是地上霜。"]
