"""设备注册与绑定：MAC/device_id ⇄ child_id，NFC 贴卡换孩子，T0 内容推送队列。

按 MAC 认领设备模型与 child_id 切换：
  · 一台便利贴属于一个家庭，可在多个孩子间切换「当前使用者」。
  · 每个孩子发一张 NFC 卡（NDEF 写 `child:<id>`），贴一下即切换登录 → 正合 NFC BSP 现成能力。
  · 功能卡（NDEF 写 `skill:<id>` / `mode:word` 等）贴一下直接进对应技能/模式。

T0 内容推送：家长端/服务把「AI 回复 / 家长留言 / 每日一词」当作一条「待显示内容」入队，
设备下次同步（深睡→定时唤醒）时取走并整屏显示。零固件即可先跑（走 REST）。
持久化 pocketclaw_devices.json。

身份（P0-3，记忆审查 2026-09-27）—— `strict=True`（生产 `/pocketclaw` 由 endpoint 打开）：
  · **不信自报**：设备 hello 里的 child_id、NFC 卡上写的 `child:<id>` 都是「谁拿到设备/谁写的卡谁说了算」，
    原来前者直接覆盖绑定、后者直接改绑，随后就读写那个孩子的 L3/L4 记忆。
  · 绑定只能走 `bind_child`（REST `/api/pocketclaw/devices/{id}/bind`，过 authz 中间件 = 家长或平板令牌）；
  · 新设备先落在**按设备隔离**的「未绑定」身份 `unbound-<hash>`，不进任何孩子的记忆；
  · NFC `child:` 卡只能在「已经由已认证方绑定过这台设备的孩子」之间切换。
  `strict=False`（直接构造时的缺省）保留旧语义，只给既有单测与离线工具用——生产入口一律严格。
"""
from __future__ import annotations

import glob
import hashlib
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import os
import secrets

try:
    import security
    atomic_write_text = security.atomic_write_text
except ImportError:
    def atomic_write_text(path, text: str, encoding: str = "utf-8") -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".{p.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
        try:
            with open(tmp, "w", encoding=encoding) as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, p)
        finally:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass

log = logging.getLogger("pocketclaw")

# 持久化各段里「每一项」该是什么类型（结构对不上＝损坏）：devices 每项要能 Device(**d)、cards 是 NDEF 文本、
# queue / modules 是列表。修前读进来什么就用什么，`"devices": []` 这类文件会在第一次心跳里抛 AttributeError。
# card_owners（2026-09-28）：卡号 → 登记方名下的孩子列表（这张卡「是谁家的」，见 register_card）。
_SECTION_ITEM_TYPE = {"devices": dict, "cards": str, "queue": list, "modules": list, "card_owners": list}


class RegistryReadOnly(RuntimeError):
    """设备表只读降级（曾读坏、已留证）时，家长 / 平板主动的写被挡下。

    单独一个类型（仍是 RuntimeError 的子类，既有 `pytest.raises(RuntimeError)` 照样成立），是为了让 REST 层
    **只**把「只读降级」翻成 503「设备登记暂时不可用」——别的 RuntimeError 还是真 bug，照常 500，不许被一起吞掉。"""


def _prior_quarantine(path: Path) -> str:
    """主文件不在、同目录却有 `<名>.corrupt-*` 留证 → 上一次运行读坏过它（quarantine_corrupt 改名留证）、至今没人处理。
    这时不能当「首次启动」从空表开写：各台便利贴的绑定、已登记的 NFC 卡、待推送内容都在留证里，另起一张新表＝全丢。"""
    if path.exists():
        return ""
    ev = sorted(path.parent.glob(glob.escape(path.name) + ".corrupt-*"))
    return str(ev[-1]) if ev else ""

# 「未绑定」身份前缀。按设备各自一个（不是全体未绑定设备共用一个桶——共用就又是串号）。
UNBOUND_PREFIX = "unbound-"
# 存量设备的旧缺省值：2026-09-27 之前新设备一律落 kid（演示账号），那是缺省值不是任何已认证方的绑定。
LEGACY_DEFAULT_CHILD = "kid"


def unbound_child(device_id: str) -> str:
    """某台设备专属的「未绑定」身份（稳定、按设备隔离、文件名安全）。"""
    return UNBOUND_PREFIX + hashlib.sha256((device_id or "").encode("utf-8")).hexdigest()[:12]


def is_unbound(child_id: Optional[str]) -> bool:
    return not child_id or str(child_id).startswith(UNBOUND_PREFIX)


@dataclass
class Device:
    device_id: str
    mac: str = ""
    family_id: str = "family"
    child_id: str = "kid"                    # 当前使用者（严格模式下新设备为 unbound-<hash>）
    name: str = "便利贴"
    fw: str = ""
    last_seen: float = 0.0
    battery: Optional[int] = None
    sync_period_s: int = 3600
    bound_children: List[str] = field(default_factory=list)   # 已认证方绑定过的孩子（NFC 只在其中切换）


# NFC 卡约定（NDEF 文本前缀）：
#   child:<id>    换孩子（贴卡登录）
#   subject:<名>  学科卡（#18）：切到数学/语文/英语… 上下文
#   word:<词>     单词收集卡（#19）：贴一下载入该词的音频+例句
#   mode:<名>     功能卡：word=进单词卡, task=作业清单, note=记想法
#   skill:<id>    技能卡：直接调用某技能
_CARD_KINDS = ("child", "subject", "word", "mode", "skill", "deck")


def _card_target(ndef: str) -> Dict[str, str]:
    """解析 NFC NDEF 文本 → {kind, value}。"""
    s = (ndef or "").strip()
    for kind in _CARD_KINDS:
        pref = kind + ":"
        if s.startswith(pref):
            return {"kind": kind, "value": s[len(pref):].strip()}
    return {"kind": "", "value": s}


class DeviceRegistry:
    def __init__(self, path: Optional[str] = None, *, strict: bool = False):
        # path=None → 默认落 pocketclaw_devices.json；path=":memory:" → 纯内存不落盘（测试用）
        self.path = path or str(Path(__file__).parent.parent / "pocketclaw_devices.json")
        self._persist = path != ":memory:"
        self.strict = bool(strict)                   # 身份不信自报（见模块头「身份」一节）
        self._devices: Dict[str, dict] = {}          # device_id → Device dict
        self._cards: Dict[str, str] = {}             # nfc_uid → "child:<id>" 等（回退映射）
        self._card_owners: Dict[str, List[str]] = {}  # nfc_uid → 登记方名下的孩子（这张卡是谁家的；空 = 无主存量）
        self._queue: Dict[str, List[dict]] = {}      # device_id → [content...]（T0 推送）
        self._modules: Dict[str, List[str]] = {}     # child_id → 启用的便利贴模块（平板设置卡配置）
        # 只读降级（2026-09-28b，同 AssistantStore / BillingStore 口径）：值＝留证路径或失败说明，"" = 正常
        # （/api/health 的 degraded 按这个属性点名：`_pocketclaw.registry`）。
        # 修前 `except (FileNotFoundError, JSONDecodeError): pass`：读坏＝空表，下一次心跳 upsert 就拿空表整文件覆盖掉
        # **全部**设备的绑定（bound_children）、NFC 卡登记与待推送内容，不可逆。现在读坏即改名留证（字节不变），此后：
        #   · 家长 / 平板主动的写（bind_child / register_card / set_modules / push_content）→ 动内存之前抛
        #     RegistryReadOnly（RuntimeError 子类；REST 层翻成 503「设备登记暂时不可用」，见 endpoint）；
        #   · 设备自己的在线信息（upsert 握手/心跳、drain_content 同步取走、NFC 在已授权孩子之间切换）→ 内存照常、
        #     不落盘、error 吼一次。严格模式下读不到绑定＝每台设备都按「未绑定」隔离身份上线，不进任何孩子的记忆。
        #   「主文件不在、留证还在」（重启后）照样只读，不另起新表；人工恢复主文件或移走留证后重启即恢复。
        self.degraded = ""
        self._ro_yelled = False
        self._load()

    # ── 持久化 ──
    def _load(self) -> None:
        if not self._persist:
            return
        p = Path(self.path)
        if not p.exists():
            ev = _prior_quarantine(p)
            if ev:
                self.degraded = ev
                log.error("便利贴设备表 %s 不在、留证 %s 还在（上次读坏后没人处理）→ 只读降级，不另起新表；"
                          "人工恢复主文件或移走留证后重启", p.name, Path(ev).name)
            return
        try:
            raw = p.read_text(encoding="utf-8")
            if not raw.strip():
                return                      # 0 字节/纯空白＝还没数据，不是损坏
            blob = json.loads(raw)
            if not isinstance(blob, dict):
                raise ValueError(f"顶层不是对象而是 {type(blob).__name__}")
            for sec, want in _SECTION_ITEM_TYPE.items():
                box = blob.get(sec, {})
                if not isinstance(box, dict):
                    raise ValueError(f"{sec} 段不是对象而是 {type(box).__name__}")
                bad = next((k for k, v in box.items() if not isinstance(v, want)), None)
                if bad is not None:
                    raise ValueError(f"{sec}[{str(bad)[:40]!r}] 不是 {want.__name__}")
        except (OSError, ValueError) as e:  # 半截 JSON、结构不对、编码（UnicodeDecodeError）、权限……一律按损坏处理
            # Preserve the corrupt file and fail closed without depending on an external memory service.
            try:
                evidence = p.with_name(f"{p.name}.corrupt-{time.time_ns()}")
                os.replace(p, evidence)
                self.degraded = str(evidence)
            except OSError as quarantine_error:
                self.degraded = f"留证失败：{quarantine_error!r}"
            log.error("设备表损坏，已进入只读降级: %s", e)
            return
        self._devices = blob.get("devices", {})
        self._cards = blob.get("cards", {})
        self._card_owners = blob.get("card_owners", {})
        self._queue = blob.get("queue", {})
        self._modules = blob.get("modules", {})

    def _yell(self, what: str) -> None:
        """只读降级下被挡 / 不落盘的写：error 日志每个实例只吼一次（心跳每台设备每几分钟就来一次，别刷屏）。"""
        if not self._ro_yelled:
            self._ro_yelled = True
            log.error("便利贴设备表只读降级（%s）：%s等写一律不落盘，家长/平板发起的改动直接报错——人工处理留证后重启",
                      self.degraded, what)

    def _refuse_if_degraded(self, what: str) -> None:
        """家长 / 平板主动的写在动内存**之前**先挡：只读降级时抛 RegistryReadOnly（RuntimeError 子类），内存与磁盘都不变。"""
        if self.degraded:
            self._yell(what)
            raise RegistryReadOnly(f"便利贴设备表曾读坏（{self.degraded}），当前只读：{what}未执行")

    def _save(self, what: str = "写回", *, soft: bool = False) -> bool:
        """原子写回（security.atomic_write_text：临时文件 + fsync + 改名；修前是固定名 `.tmp`、不 fsync）。返回是否落了盘。

        只读降级：soft=True（设备自己的在线信息）→ 不落盘、吼一次、返回 False；soft=False → 抛 RuntimeError
        （主动写的入口已在动内存前挡过，这里是兜底——留证之外绝不出现一张新表）。
        写失败（磁盘满 / 权限）→ error 日志后照抛 OSError（修前同样会抛，只是没留日志）。"""
        if not self._persist:
            return True
        if self.degraded:
            if soft:
                self._yell(what)
                return False
            raise RegistryReadOnly(f"便利贴设备表曾读坏（{self.degraded}），当前只读：{what}未执行")
        try:
            atomic_write_text(self.path, json.dumps(
                {"devices": self._devices, "cards": self._cards, "card_owners": self._card_owners,
                 "queue": self._queue, "modules": self._modules},
                ensure_ascii=False, indent=2))
        except OSError as e:
            log.error("便利贴设备表写回失败（%s）path=%s err=%r", what, self.path, e)
            raise
        return True

    # ── 便利贴功能模块启用（平板 PocketClaw 设置卡写入）──
    #   word=单词卡  note=记事清单  task=作业清单  book=我的书本
    #   轮播是设备闲时(>10min)自动进入的保留功能，不在此配置。
    MODULE_KEYS = ("word", "note", "task", "book")

    def get_modules(self, child_id: str) -> List[str]:
        """该孩子启用的模块。未配置过 → 默认四个全开。"""
        mods = self._modules.get(str(child_id))
        if mods is None:
            return list(self.MODULE_KEYS)
        return [m for m in mods if m in self.MODULE_KEYS]

    def set_modules(self, child_id: str, modules: List[str]) -> List[str]:
        self._refuse_if_degraded("设置便利贴模块")
        clean = [m for m in dict.fromkeys(modules or []) if m in self.MODULE_KEYS]
        self._modules[str(child_id)] = clean
        self._save("设置便利贴模块")
        return clean

    # ── 设备生命周期 ──
    def upsert(self, device_id: str, *, mac: str = "", fw: str = "",
               child_id: Optional[str] = None, battery: Optional[int] = None,
               now: Optional[float] = None) -> Device:
        """握手/心跳时登记设备；已存在则更新在线信息。

        严格模式：`child_id` 一律不采信（它来自设备自报）——新设备落「未绑定」身份，
        已有设备的绑定原样保留；改绑只能走 `bind_child`。非严格模式保留旧语义（自报即更新）。"""
        d = self._devices.get(device_id)
        if d is None:
            first = unbound_child(device_id) if self.strict else (child_id or LEGACY_DEFAULT_CHILD)
            d = asdict(Device(device_id=device_id, mac=mac, fw=fw, child_id=first))
            self._devices[device_id] = d
        if mac:
            d["mac"] = mac
        if fw:
            d["fw"] = fw
        if child_id and self.strict:
            if child_id != d.get("child_id"):
                log.warning("pocketclaw 设备自报 child_id 与绑定不符，已忽略（严格模式不信自报）"
                            "dev=%s claim=%s bound=%s", device_id, str(child_id)[:40], d.get("child_id"))
        elif child_id:                   # 非严格（旧语义）：设备主动声明使用者才更新
            d["child_id"] = child_id
        if battery is not None:
            d["battery"] = battery
        d["last_seen"] = now if now is not None else time.time()
        self._save("设备握手/心跳", soft=True)    # 只读降级：设备照常在线（内存），不落盘
        return Device(**d)

    def get(self, device_id: str) -> Optional[Device]:
        d = self._devices.get(device_id)
        return Device(**d) if d else None

    def by_mac(self, mac: str) -> Optional[Device]:
        for d in self._devices.values():
            if d.get("mac") and d["mac"].upper() == mac.upper():
                return Device(**d)
        return None

    def list_devices(self) -> List[Device]:
        return [Device(**d) for d in self._devices.values()]

    def bind_child(self, device_id: str, child_id: str) -> Optional[Device]:
        """**已认证方**把设备绑到某个孩子（REST bind，过 authz 中间件）。同时记入 bound_children，
        之后该孩子的 NFC 卡才能在这台设备上切换。不要从设备自报/卡片内容直接调它（见 on_nfc）。
        只读降级 → 抛 RuntimeError（绑定不能「看起来绑上了」、重启就没了）。"""
        self._refuse_if_degraded("绑定孩子")
        d = self._devices.get(device_id)
        if not d:
            return None
        d["child_id"] = child_id
        if not is_unbound(child_id):
            bc = [c for c in (d.get("bound_children") or []) if c != child_id]
            d["bound_children"] = (bc + [child_id])[-8:]      # 一台便利贴一个家庭，8 个足够
        self._save("绑定孩子")
        return Device(**d)

    def bound_children(self, device_id: str) -> List[str]:
        d = self._devices.get(device_id) or {}
        return list(d.get("bound_children") or [])

    def _switch_current(self, device_id: str, child_id: str) -> Optional[Device]:
        """只切「当前使用者」，不扩大授权（NFC 在已授权的孩子之间切换用）。"""
        d = self._devices.get(device_id)
        if not d:
            return None
        d["child_id"] = child_id
        self._save("NFC 切换当前使用者", soft=True)
        return Device(**d)

    # ── NFC 贴卡 ──
    def on_nfc(self, device_id: str, uid: str, ndef: str = "") -> Dict[str, Any]:
        """处理一次贴卡，返回动作 {kind, value, child_id?}。

        优先用卡里的 NDEF；没有 NDEF 时用 uid→映射（家长端预先登记的卡）。"""
        target = _card_target(ndef)
        if not target["kind"] and uid in self._cards:
            target = _card_target(self._cards[uid])
        result: Dict[str, Any] = dict(target)
        if target["kind"] == "child" and target["value"]:
            if self.strict:
                # 卡上的 `child:<id>` 谁都能用手机写——它只能在「已认证方绑过这台设备」的孩子之间切，
                # 不能把设备改绑到一个新孩子身上（那等于自报身份，随后读写该孩子的记忆）。
                if target["value"] in self.bound_children(device_id):
                    dev = self._switch_current(device_id, target["value"])
                    result["child_id"] = dev.child_id if dev else None
                else:
                    log.warning("pocketclaw NFC 孩子卡被拒：该孩子未经家长/平板绑定到本设备 dev=%s card_child=%s",
                                device_id, target["value"][:40])
                    result["child_id"] = None
                    result["refused"] = "not_bound"
                return result
            dev = self.bind_child(device_id, target["value"])
            result["child_id"] = dev.child_id if dev else target["value"]
        return result

    def register_card(self, uid: str, target: str, *, owners: Optional[List[str]] = None) -> None:
        """家长端把一张空卡登记为 child:/skill:/mode: 目标（无 NDEF 时的回退映射）。
        归属校验（卡指向的孩子 / 被覆盖的旧卡指向的孩子 / 这张卡原来的主人都得在令牌名下）在 endpoint 做，这里只落库。

        owners：这张卡「是谁家的」——登记方名下的孩子（家长令牌的 children / 设备令牌的 child_id）。功能卡
        （mode:/skill:/subject:/word:/deck:）没有孩子维度，不记主人的话，任何一把有效令牌都能把别人家的
        「单词卡」改成别的（2026-09-28 审查）。None = 不改原有主人（直接调用 / 旧调用方）；[] = 清成无主。"""
        self._refuse_if_degraded("登记 NFC 卡")
        self._cards[uid] = target
        if owners is not None:
            clean = [c for c in dict.fromkeys(str(c) for c in owners) if c][:16]
            if clean:
                self._card_owners[uid] = clean
            else:
                self._card_owners.pop(uid, None)
        self._save("登记 NFC 卡")

    def card_target(self, uid: str) -> Dict[str, str]:
        """某张卡当前登记的目标 → {kind, value}；没登记过 → {kind: "", value: ""}。"""
        raw = self._cards.get(uid)
        return _card_target(raw) if raw else {"kind": "", "value": ""}

    def card_owners(self, uid: str) -> List[str]:
        """某张卡登记方名下的孩子（这张卡是谁家的）；没记过（存量 / 无主）→ []。"""
        return list(self._card_owners.get(uid) or [])

    # ── T0 内容推送队列 ──
    def push_content(self, device_id: str, page: Dict[str, Any], *,
                     kind: str = "content", now: Optional[float] = None) -> dict:
        self._refuse_if_degraded("推送内容")             # 家长推的留言不能「看起来进了队列」、重启就没了
        item = {"id": f"c{int((now or time.time())*1000)}", "kind": kind,
                "page": page, "ts": now if now is not None else time.time()}
        self._queue.setdefault(device_id, []).append(item)
        self._save("推送内容")
        return item

    def drain_content(self, device_id: str) -> List[dict]:
        """设备同步时取走并清空待推送内容。"""
        items = self._queue.get(device_id, [])
        self._queue[device_id] = []
        self._save("同步取走待推送内容", soft=True)
        return items

    def pending_count(self, device_id: str) -> int:
        return len(self._queue.get(device_id, []))
