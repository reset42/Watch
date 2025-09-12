# /core/bt_manager.py — EventBus API v1.2, StatusStore aligned + Adv-Guards (non-blocking)
# - Idempotentes Advertising mit Cooldown (_adv_active, _adv_restart_guard_ms)
# - Pairing sichtbar nur auf Kommando oder wenn BLE_PAIRING_ON_START=True
# - Diag-Rate-Limit (BLE_DIAG_RATE_LIMIT_MS, default 250 ms pro Eventtyp)
# - Keine Diag-Flut bei DIM, wenn BLE an bleibt
# - IRQ → Queue → Publish im poll() (UI-safe)
# - Coalescing (16ms) + Status-Rate-Limit (≥500ms)
try:
    import bluetooth as _ble, json, time
    from micropython import const
except Exception:
    _ble = None
    import json, time

from core.logger import info, warn, debug
from core import eventbus as bus_mod
import struct

# --- IRQ IDs (µPy / NimBLE kompatibel) ---
_IRQ_CENTRAL_CONNECT    = const(1) if 'const' in globals() else 1
_IRQ_CENTRAL_DISCONNECT = const(2) if 'const' in globals() else 2
_IRQ_GATTS_WRITE        = const(3) if 'const' in globals() else 3

# --- GATT Flags ---
FLAG_READ     = 0x0002
FLAG_WRITE    = 0x0008
FLAG_WNR      = 0x0004
FLAG_NOTIFY   = 0x0010
FLAG_INDICATE = 0x0020

def _uuid128(u: str):
    u = u.replace("-", "")
    return bytes(int(u[i:i+2], 16) for i in range(0, 32, 2))

def _adv_payload(*, name=None, services=None, appearance=0):
    p = bytearray()
    def _append(t, v):
        p.extend(struct.pack("BB", len(v)+1, t)); p.extend(v)
    _append(0x01, b"\x06")  # Flags
    if name:
        _append(0x09, name.encode("utf-8"))
    if services:
        sv = bytearray()
        for s in services:
            sv.extend(bytes(reversed(s)))
        _append(0x07, sv)   # Complete List of 128-bit UUIDs
    if appearance:
        _append(0x19, struct.pack("<H", int(appearance)))
    return bytes(p)

def _ticks_ms():
    try:
        from time import ticks_ms
        return ticks_ms()
    except Exception:
        return int(time.time()*1000)

def _ticks_diff(a,b):
    try:
        from time import ticks_diff
        return ticks_diff(a,b)
    except Exception:
        return a-b

class BtState:
    OFF="off"; ON="on"; PAIRING="pairing"; CONNECTED="connected"; ERROR="error"

class BtManager:
    """
    BLE Peripheral (GATT Server) Bridge für den Eventbus.

    Publishes (only-on-change, rate-limited + coalesced):
      - status/bt {state, ts[, err]}    (TTL-Policy gem. Config)
      - diag/bt   {event, detail?, ts}

    Subscribes:
      - cmd/bt {"state":"on|off|pairing|toggle"} (v1.2)
      - Legacy: bt/enable|bt/disable|bt/toggle|bt/reconnect
      - power/will_dim|will_sleep|display/dim|display/wake|power/active|power/wake|sys/wake
      - bridge/out (Outbound-Bridge → TX Notify)

    Inbound:
      - erlaubte Topics (Allow-List) -> Bus
      - notif/new|notifications/new → status/notifications {count, flag} (TTL ~15 s, only-on-change)
    """

    def __init__(self, eventbus, cfg, logger=None):
        self.bus = eventbus
        self.cfg = cfg
        self.log = logger or info

        # --- Config (API v1.2 / StatusStore) ---
        self.enabled          = bool(getattr(cfg, "BLE_ENABLED", False))
        self.dev_name         = str(getattr(cfg, "BLE_DEVICE_NAME", "TWatchS3"))
        self.s_uuid           = str(getattr(cfg, "BLE_SERVICE_UUID", "12345678-1234-5678-9abc-def012345678"))
        self.rx_uuid          = str(getattr(cfg, "BLE_CHAR_RX_UUID", "12345678-1234-5678-9abc-def012345679"))
        self.tx_uuid          = str(getattr(cfg, "BLE_CHAR_TX_UUID", "12345678-1234-5678-9abc-def01234567a"))

        # Status/TTL-Policy
        self.ttl_default_ms   = int(getattr(cfg, "BLE_STATUS_TTL_MS", 8000))             # on/pairing/off/error
        self.rate_limit_ms    = int(getattr(cfg, "BLE_STATUS_RATE_LIMIT_MS", 500))       # min Abstand Status-Publishes
        self.coalesce_ms      = int(getattr(cfg, "BLE_STATUS_COALESCE_MS", 16))          # 1 Frame
        self.connected_sticky = bool(getattr(cfg, "BLE_STATUS_CONNECTED_STICKY", True))
        self.ttl_connected_ms = int(getattr(cfg, "BLE_STATUS_CONNECTED_TTL_MS", 300000)) # falls sticky=False

        # Pairing-Policy
        self.pairing_timeout_ms = int(getattr(cfg, "BLE_PAIRING_TIMEOUT_MS", 30000))
        self.pair_on_start      = bool(getattr(cfg, "BLE_PAIRING_ON_START", False))  # optional sichtbar beim Start

        # Bridge / Radio
        self.connect_to       = int(getattr(cfg, "BLE_CONNECT_TIMEOUT_MS", 12000))
        self.backoff_max      = int(getattr(cfg, "BLE_BACKOFF_MAX_MS", 60000))
        self.sleep_ble        = bool(getattr(cfg, "SLEEP_BLE", True))
        self.mtu_cfg          = int(getattr(cfg, "BLE_BRIDGE_MTU", 185))
        self.allow_in         = list(getattr(cfg, "BLE_BRIDGE_ALLOW_IN", ["notif/*","notifications/*"]))
        self.allow_out        = list(getattr(cfg, "BLE_BRIDGE_ALLOW_OUT", ["status/notifications","status/battery"]))
        self.bridging_enabled = bool(getattr(cfg, "BLE_BRIDGE_ENABLED", True))

        # Diag Drossel
        self.diag_rate_limit_ms = int(getattr(cfg, "BLE_DIAG_RATE_LIMIT_MS", 250))

        # --- Runtime ---
        self.state          = BtState.OFF
        self._last_state    = None
        self._last_pub_ms   = 0
        self._err_code      = None        # "hw"|"adv"|"proto" (nur bei state=ERROR)
        self.dimmed         = False

        self._ble           = None
        self._conn_handle   = None
        self._tx_handle     = None
        self._rx_handle     = None

        self._max_payload   = max(20, min(self.mtu_cfg-3, 180))
        self._deadline_ms   = 0
        self._backoff_ms    = 800

        # IRQ → Poll Entkopplung
        self._q = []              # kleine FIFO [(ev, data_dict)]
        self._q_max = 8

        # Pairing-Timeout-Tracking
        self._pair_deadline = 0   # 0 = inaktiv
        self._force_pairing = False

        # Advertising Guards
        self._adv_active = False
        self._adv_restart_guard_ms = 0

        # Coalescing
        self._coal_deadline = 0
        self._coal_pending  = None

        # Notifications de-dupe
        self._last_notif_sig = None  # (count, flag)

        # diag throttling state
        self._diag_last_ms = {}
        print("RX UUID =", self.rx_uuid)
    # ---------- Lifecycle ----------
    def start(self):
        self._subscribe()
        self._publish_status(BtState.OFF, force=True)
        if self.enabled:
            self._radio_on()

    def stop(self):
        self._radio_off()

    # ---------- Helpers ----------
    def _bind(self, topic, fn):
        return lambda ev=None, **kw: fn(topic, ev if isinstance(ev, dict) else (kw if kw else None))

    # ---------- EventBus wiring ----------
    def _subscribe(self):
        b = self.bus
        # v1.2 Kommandopfad
        b.subscribe("cmd/bt", self._bind("cmd/bt", self._on_cmd_bt))
        # Legacy-Kommandos
        for t in ("bt/enable","bt/disable","bt/toggle","bt/reconnect"):
            b.subscribe(t, self._bind(t, self._on_legacy_ctrl))
        # Power/Display
        for t in ("power/will_dim","display/dim","power/will_sleep",
                  "display/wake","power/active","power/wake","sys/wake"):
            b.subscribe(t, self._bind(t, self._on_power))
        # Bridge out
        b.subscribe("bridge/out", self._bind("bridge/out", self._on_bridge_out))

    # ---------- Poll (nicht blockierend) ----------
    def poll(self, now_ms:int):
        # Re-Adv Backoff (nur wenn NICHT aktiv am Werben)
        if self.state in (BtState.ON, BtState.ERROR) and self.enabled and (not self._adv_active):
            if _ticks_diff(now_ms, self._deadline_ms) >= 0:
                self._start_advertising()

        # Pairing-Timeout: einmalig auf 'on' zurück + diag
        if self.state == BtState.PAIRING and self._pair_deadline:
            if _ticks_diff(now_ms, self._pair_deadline) >= 0:
                try:
                    if self._ble:
                        self._ble.gap_advertise(None)
                except Exception:
                    pass
                self._pair_deadline = 0
                self._queue_state(BtState.ON)  # coalesced + rate-limited
                self._publish_diag("pairing_timeout")
                self._schedule_backoff()
                self._adv_active = False

        # IRQ-Queue verarbeiten (kleine Dosis pro Tick)
        for _ in range(2):
            if not self._q:
                break
            ev, data = self._q.pop(0)
            try:
                if ev == "connected":
                    self._pair_deadline = 0
                    self._queue_state(BtState.CONNECTED)
                    self._publish_diag("connected", **data)
                elif ev == "disconnected":
                    self._queue_state(BtState.ON)
                    self._publish_diag("disconnected", **data)
                    self._schedule_backoff()
                elif ev == "rx":
                    self._handle_inbound(data.get("raw", b""))
            except Exception:
                pass

        # Coalescing-Fenster bedienen
        if self._coal_pending is not None and _ticks_diff(now_ms, self._coal_deadline) >= 0:
            st = self._coal_pending
            self._coal_pending = None
            self._coal_deadline = 0
            self._set_state_immediate(st)

    # ---------- Power Policy ----------
    def _on_power(self, topic, _payload=None):
        if topic in ("power/will_dim","display/dim"):
            self.dimmed = True
            if self.sleep_ble:
                self._publish_diag("dim_radio_off")
                self._radio_off()
            else:
                # BLE bleibt an → kein Diag-Spam
                pass
        elif topic == "power/will_sleep":
            self.dimmed = True
            self._publish_diag("sleep_enter")
            self._radio_off()
        elif topic in ("display/wake","power/active","power/wake","sys/wake"):
            self.dimmed = False
            self._publish_diag("wake")
            if self.enabled:
                self._radio_on()

    # ---------- Controls ----------
    def _on_cmd_bt(self, _topic, payload=None):
        state = (payload or {}).get("state", "")
        if   state == "on":
            self.enabled = True;  self._radio_on()
        elif state == "off":
            self.enabled = False; self._radio_off()
        elif state == "pairing":
            self.enabled = True;  self._force_pairing = True; self._radio_on()
        elif state == "toggle":
            self.enabled = not self.enabled
            self._radio_on() if self.enabled else self._radio_off()

    def _on_legacy_ctrl(self, topic, _payload=None):
        if   topic.endswith("/enable"):    self.enabled = True;  self._radio_on()
        elif topic.endswith("/disable"):   self.enabled = False; self._radio_off()
        elif topic.endswith("/toggle"):    self.enabled = not self.enabled; (self._radio_on() if self.enabled else self._radio_off())
        elif topic.endswith("/reconnect"):
            if self._conn_handle is not None and self._ble:
                try: self._ble.gap_disconnect(self._conn_handle)
                except Exception: pass
            self._schedule_backoff()

    # ---------- Radio / Advertising ----------
    def _radio_on(self):
        if not _ble:
            warn("BLE: module not available")
            self._err_code = "hw"
            self._queue_state(BtState.ERROR)
            return
        try:
            if not self._ble:
                self._ble = _ble.BLE()
                self._ble.active(True)
                self._ble.irq(self._irq)

                svc_uuid = _uuid128(self.s_uuid)
                rx_uuid  = _uuid128(self.rx_uuid)
                tx_uuid  = _uuid128(self.tx_uuid)
                ((self._tx_handle, self._rx_handle),) = self._ble.gatts_register_services((
                    (_ble.UUID(svc_uuid), (
                        (_ble.UUID(tx_uuid), FLAG_NOTIFY | FLAG_READ),
                        (_ble.UUID(rx_uuid), FLAG_WRITE | FLAG_WNR),
                    )),
                ))
                try:
                    self._ble.gatts_set_buffer(self._tx_handle, 512, True)
                    self._ble.gatts_set_buffer(self._rx_handle, 512, False)
                except Exception:
                    pass

            # Beim Einschalten zunächst 'on'
            self._queue_state(BtState.ON)
            # Advertising starten (idempotent)
            self._start_advertising()
            self._publish_diag("radio_on", name=self.dev_name)
        except Exception as e:
            warn("BLE: radio_on failed: %r" % e)
            self._err_code = "hw"
            self._queue_state(BtState.ERROR)

    def _radio_off(self):
        try:
            if self._ble:
                if self._conn_handle is not None:
                    try: self._ble.gap_disconnect(self._conn_handle)
                    except Exception: pass
                try: self._ble.gap_advertise(None)
                except Exception: pass
            self._adv_active = False
            self._conn_handle = None
            self._pair_deadline = 0
            self._queue_state(BtState.OFF)
            self._publish_diag("radio_off")
        except Exception as e:
            warn("BLE: radio_off failed: %r" % e)
            self._err_code = "hw"
            self._queue_state(BtState.ERROR)

    def _enter_pairing(self, now_ms):
        self._queue_state(BtState.PAIRING)
        self._pair_deadline = now_ms + max(0, self.pairing_timeout_ms)

    def _start_advertising(self):
        if not self._ble: return
        try:
            # Idempotent Guard + Cooldown
            now = _ticks_ms()
            if self._adv_active and _ticks_diff(self._adv_restart_guard_ms, now) > 0:
                return

            try:
                self._ble.config(gap_name=self.dev_name)
            except Exception:
                pass
            adv = _adv_payload(name=self.dev_name, services=[_uuid128(self.s_uuid)])
            interval_us = int(getattr(self.cfg, "BLE_ADV_INTERVAL_MS", 300) * 1000)
            self._ble.gap_advertise(interval_us, adv_data=adv)

            self._adv_active = True
            self._adv_restart_guard_ms = now + 1000  # 1s Cooldown

            # Sichtbarkeit steuern:
            if self._force_pairing or self.pair_on_start:
                self._enter_pairing(now)
            else:
                self._queue_state(BtState.ON)
                self._pair_deadline = 0

            self._publish_diag("adv_start")
            self._force_pairing = False
        except Exception as e:
            warn("BLE: adv failed: %r" % e)
            self._err_code = "adv"
            self._queue_state(BtState.ERROR)
            self._schedule_backoff()
            self._adv_active = False

    # ---------- IRQ (leicht, ohne Bus-Publishes) ----------
    def _irq(self, event, data):
        try:
            if event == _IRQ_CENTRAL_CONNECT:
                self._conn_handle, addr_type, addr = data
                self._adv_active = False
                self._push_irq("connected", {"addr": self._fmt_addr(addr)})
            elif event == _IRQ_CENTRAL_DISCONNECT:
                ch, addr_type, addr = data
                self._adv_active = False
                self._push_irq("disconnected", {"addr": self._fmt_addr(addr)})
                self._conn_handle = None
            elif event == _IRQ_GATTS_WRITE:
                value_handle = data[1]
                if value_handle == self._rx_handle and self._ble:
                    raw = self._ble.gatts_read(self._rx_handle)
                    self._push_irq("rx", {"raw": raw})
        except Exception:
            pass

    def _push_irq(self, ev, d):
        if len(self._q) < self._q_max:
            self._q.append((ev, d))

    # ---------- Inbound Bridge ----------
    def _handle_inbound(self, raw: bytes):
        try:
            msg = json.loads(raw)
        except Exception as e:
            self._publish_diag("frame_parse_error", err=repr(e)); return
        topic = (msg.get("topic") or "").strip()
        payload = msg.get("payload", {}) or {}

        if not self._allowed(topic, self.allow_in):
            self._publish_diag("in_blocked", topic=topic); return

        # Original-Topic ins Bus
        self._bus_publish(topic, payload)

        # Notifications normalisieren → status/notifications (only-on-change)
        if topic in ("notif/new","notifications/new"):
            flag = int(payload.get("flag", 1) or 1)
            count = int(payload.get("count", 1) or 1)
            sig = (count, flag)
            if sig != self._last_notif_sig:
                self._last_notif_sig = sig
                self._bus_publish("status/notifications", {"count": count, "flag": flag}, ttl_ms=15000)

    # ---------- Outbound Bridge ----------
    def _on_bridge_out(self, _topic, payload=None):
        if not self.bridging_enabled or self._conn_handle is None or not self._ble:
            return
        try:
            t = (payload or {}).get("topic")
            p = (payload or {}).get("payload", {}) or {}
            if not self._allowed(t, self.allow_out):
                return
            frame = {"v":1,"seq":int(_ticks_ms() & 0xFFFFFFFF),"topic":t,"ts":int(time.time()),"ttl_ms":0,"payload":p}
            data = json.dumps(frame)
            for i in range(0, len(data), self._max_payload):
                self._ble.gatts_notify(self._conn_handle, self._tx_handle, data[i:i+self._max_payload])
        except Exception as e:
            self._publish_diag("tx_error", err=repr(e))

    # ---------- Helpers ----------
    def _allowed(self, topic: str, patterns):
        if not topic: return False
        for pat in patterns:
            if pat.endswith("/*"):
                if topic.startswith(pat[:-1]): return True
            elif pat == "*" or pat == topic:
                return True
        return False

    def _fmt_addr(self, addr: bytes):
        try:    return ":".join("{:02X}".format(b) for b in addr)
        except: return None

    def _schedule_backoff(self):
        self._deadline_ms = _ticks_ms() + self._backoff_ms
        self._backoff_ms = min(self._backoff_ms*2, self.backoff_max)
        self._publish_diag("readv_wait", ms=max(0, self._deadline_ms - _ticks_ms()))

    # ---- Coalescing State-Set ----
    def _queue_state(self, new_state):
        # innerhalb des Coalescing-Fensters nur den letzten Zustand puffern
        if self.coalesce_ms <= 0:
            self._set_state_immediate(new_state); return
        now = _ticks_ms()
        if self._coal_deadline == 0 or _ticks_diff(now, self._coal_deadline) >= 0:
            # neues Fenster starten
            self._coal_pending = new_state
            self._coal_deadline = now + self.coalesce_ms
        else:
            # innerhalb des Fensters einfach überschreiben (letzter gewinnt)
            self._coal_pending = new_state

    def _set_state_immediate(self, new_state):
        if new_state != self.state:
            self.state = new_state
            self._publish_status(new_state)

    # ---- Publish helpers ----
    def _publish_status(self, state, *, force=False):
        now = _ticks_ms()
        if (not force) and (state == self._last_state) and (_ticks_diff(now, self._last_pub_ms) < self.rate_limit_ms):
            return
        self._last_state = state
        self._last_pub_ms = now
        msg = {"state": state, "ts": int(time.time())}
        if state == BtState.ERROR and self._err_code:
            msg["err"] = str(self._err_code)

        # TTL-Policy:
        if state == BtState.CONNECTED:
            if self.connected_sticky:
                self._bus_publish("status/bt", msg)  # sticky (ohne ttl_ms)
            else:
                self._bus_publish("status/bt", msg, ttl_ms=self.ttl_connected_ms)
        else:
            self._bus_publish("status/bt", msg, ttl_ms=self.ttl_default_ms)

        debug("BT status %s %s", state, msg)

    def _publish_diag(self, event, **detail):
        now = _ticks_ms()
        last = self._diag_last_ms.get(event, 0)
        if _ticks_diff(now, last) < self.diag_rate_limit_ms:
            return
        self._diag_last_ms[event] = now
        self._bus_publish("diag/bt", {"event": event, "detail": detail, "ts": int(time.time())})
        debug("BT diag %s %s", event, detail)

    def _bus_publish(self, topic, payload=None, **kw):
        # kw kann ttl_ms enthalten (v1.2). Fallback: ohne kw aufrufen.
        try:
            self.bus.publish(topic, payload, **kw)
        except TypeError:
            try: self.bus.publish(topic, payload)
            except Exception:
                try: bus_mod.publish(topic, payload, **kw)
                except TypeError:
                    try: bus_mod.publish(topic, payload)
                    except Exception:
                        pass
        except Exception:
            try: bus_mod.publish(topic, payload, **kw)
            except Exception:
                pass
