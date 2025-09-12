# screens/clock_digital.py — v0.3.9
# Changes vs v0.3.8:
# - EventBus-Kompat: Wrapper für ALLE Subscriptions (inkl. time/min, time/sec)
# - _BusAdapter speichert (topic, cb, token); unsubscribe() funktioniert für v1.2 (topic,cb) und v1.3 (token)
# - StatusStore-Prime, 1 Hz Expiry, Forwarding & Icon-Mapping unverändert
import sys, time
import config

try:
    from core.logger import info as log_info, warn as log_warn, debug as log_debug
except Exception:
    def log_info(*a, **k):  pass
    def log_warn(*a, **k):  pass
    def log_debug(*a, **k): pass


class _BusAdapter:
    """
    Akzeptiert Bus-Instanz ODER core.eventbus-Modul.
    Liefert einen Handle {"topic":str, "cb":callable, "token":Any|None}.
    unsubscribe(handle) versucht zuerst token, dann (topic, cb).
    """
    def __init__(self, bus_like):
        self._b = bus_like

    def subscribe(self, topic, cb):
        b = self._b
        token = None
        try:
            if hasattr(b, "subscribe") and callable(b.subscribe):
                token = b.subscribe(topic, cb)
            else:
                import types
                if isinstance(b, types.ModuleType) and hasattr(b, "subscribe"):
                    token = b.subscribe(topic, cb)
        except Exception as e:
            try: log_warn("Bus subscribe failed %s: %r", topic, e)
            except Exception: pass
        return {"topic": topic, "cb": cb, "token": token}

    def unsubscribe(self, handle):
        if not handle:
            return
        b = self._b
        topic = handle.get("topic") if isinstance(handle, dict) else None
        cb    = handle.get("cb") if isinstance(handle, dict) else None
        token = handle.get("token") if isinstance(handle, dict) else handle
        # Reihenfolge: token → (topic, cb)
        try:
            if hasattr(b, "unsubscribe") and callable(b.unsubscribe):
                try:
                    b.unsubscribe(token)
                    return
                except TypeError:
                    pass
            import types
            if isinstance(b, types.ModuleType) and hasattr(b, "unsubscribe"):
                try:
                    b.unsubscribe(token)
                    return
                except TypeError:
                    pass
        except Exception:
            pass
        # Fallback: v1.2-Signatur
        try:
            if hasattr(b, "unsubscribe") and callable(b.unsubscribe) and topic and cb:
                b.unsubscribe(topic, cb)
                return
        except Exception:
            pass
        try:
            import types
            if isinstance(b, types.ModuleType) and hasattr(b, "unsubscribe") and topic and cb:
                b.unsubscribe(topic, cb)
        except Exception:
            pass


class ScreenClockDigital:
    SCREEN_ID  = "clock_digital"
    KEEP_AWAKE = False

    def __init__(self, d=None, manager=None, eventbus=None, **kw):
        self.d = d
        self.manager = manager
        self.eb = self._resolve_bus(eventbus, manager)

        self.face = None
        self._face_id = None
        self._visible = False
        self._last_hm = (-1, -1)

        self._tok = {
            "min": None, "sec": None, "tick": None,
            "wifi": None, "bt": None, "lora": None,
            "batt": None, "usb": None, "notif": None, "notif2": None,
        }

    # ---------- bus/status helpers ----------

    def _resolve_bus(self, eventbus, manager):
        cand = eventbus
        if not cand and manager:
            cand = getattr(manager, "eventbus", None) or getattr(manager, "bus", None)
        if not cand:
            try:
                import core.eventbus as eb_mod
                cand = eb_mod
            except Exception:
                cand = None
        return _BusAdapter(cand) if cand else None

    def _status_store(self):
        return getattr(self.manager, "status", None) or getattr(self, "status", None)

    # ---------- drawing helpers ----------

    def _now_hm(self):
        t = time.localtime()
        return int(t[3]), int(t[4])

    def _hard_clear(self):
        d = self.d
        if not d: return
        try:
            from lib.display import BLACK
        except Exception:
            BLACK = 0x00
        try:
            W = getattr(d, "WIDTH", None) or getattr(d, "width", lambda: 240)
            H = getattr(d, "HEIGHT", None) or getattr(d, "height", lambda: 240)
            W = int(W() if callable(W) else W or 240)
            H = int(H() if callable(H) else H or 240)
        except Exception:
            W, H = 240, 240
        try:
            if hasattr(d, "fill"):
                d.fill(BLACK)
            elif hasattr(d, "fill_rect"):
                d.fill_rect(0, 0, W, H, BLACK)
        except Exception:
            pass

    def _normalize_face_id(self, x):
        if isinstance(x, str) and x:
            return x
        for attr in ("NAME", "__name__"):
            v = getattr(x, attr, None)
            if isinstance(v, str) and v:
                return v
        try:
            s = str(x).split(".")[-1]
            if "'" in s:
                s = s.split("'")[0]
            return s or "classic_black"
        except Exception:
            return "classic_black"

    def _load_face(self, face_id):
        pkg = "ui.watchfaces_digital.%s.main" % face_id
        if getattr(config, "DEV_HOTRELOAD_WF", False):
            if pkg in sys.modules:
                try:
                    del sys.modules[pkg]
                    log_debug("DEV hotreload: removed %s", pkg)
                except Exception:
                    pass
        try:
            mod = __import__(pkg, None, None, ("*",))
        except Exception as e:
            raise ImportError("Watchface '%s' konnte nicht geladen werden: %r" % (face_id, e))

        face_cls = None
        for name in dir(mod):
            obj = getattr(mod, name)
            if isinstance(obj, type) and getattr(obj, "NAME", "") == face_id:
                face_cls = obj; break
        if not face_cls:
            for name in dir(mod):
                obj = getattr(mod, name)
                if isinstance(obj, type) and (hasattr(obj, "render_full") or hasattr(obj, "render")):
                    face_cls = obj; break
        if not face_cls:
            raise ImportError("Watchface-Klasse für '%s' nicht gefunden" % face_id)

        try:
            inst = face_cls(self.d)
        except TypeError:
            br = getattr(self.d, "blit_rgb565", None)
            if br is None:
                raise
            inst = face_cls(self.d, br)

        self.face = inst
        self._face_id = face_id

        try: setattr(self.face, "manager", self.manager)
        except Exception: pass
        try:
            import core.eventbus as eb_mod
            setattr(self.face, "bus", eb_mod)
        except Exception:
            try: setattr(self.face, "bus", getattr(self.manager, "eventbus", None))
            except Exception: pass
        try:
            s = getattr(self.manager, "status", None)
            setattr(self.face, "status", s)
        except Exception:
            pass

    # ---------- lifecycle ----------

    def on_show(self, *a, **kw):
        self._visible = True
        face_id = self._normalize_face_id(getattr(config, "ACTIVE_WATCHFACE_DIGITAL", "classic_black"))
        if (self.face is None) or (self._face_id != face_id):
            self._load_face(face_id)

        draws_full_bg = bool(getattr(self.face, "DRAWS_FULL_BG", False))
        if not draws_full_bg:
            self._hard_clear()

        hh, mm = self._now_hm()
        try:
            if hasattr(self.face, "render_full"):
                self.face.render_full(hh, mm)
            elif hasattr(self.face, "render"):
                self.face.render(hh, mm)
        except Exception as e:
            log_warn("face initial draw error: %r", e)
        self._last_hm = (hh, mm)

        # --- robuste Callback-Wrapper ---
        def _extract_payload(*args, **kw):
            if args:
                if isinstance(args[-1], dict): return args[-1]
                if isinstance(args[0], dict):  return args[0]
            if kw:
                p = kw.get("payload")
                if isinstance(p, dict): return p
                return kw if isinstance(kw, dict) else None
            return None

        def _cb_min(*args, **kw):   self._on_min(_extract_payload(*args, **kw) or {})
        def _cb_sec(*args, **kw):   self._on_sec(_extract_payload(*args, **kw) or {})
        def _cb_tick(*args, **kw):  self._on_tick_1hz({})

        def _cb_bt(*args, **kw):    self._on_bt(_extract_payload(*args, **kw) or {})
        def _cb_wifi(*args, **kw):  self._on_wifi(_extract_payload(*args, **kw) or {})
        def _cb_lora(*args, **kw):  self._on_lora(_extract_payload(*args, **kw) or {})
        def _cb_batt(*args, **kw):  self._on_batt(_extract_payload(*args, **kw) or {})
        def _cb_usb(*args, **kw):   self._on_usb(_extract_payload(*args, **kw) or {})
        def _cb_notif(*args, **kw): self._on_notif(_extract_payload(*args, **kw) or {})

        # --- Subscriptions (über Adapter → v1.2/v1.3 safe) ---
        if self.eb:
            self._tok["min"]   = self.eb.subscribe("time/min", _cb_min)
            if bool(getattr(self.face, "WANTS_SECONDS", False)):
                self._tok["sec"] = self.eb.subscribe("time/sec", _cb_sec)
            self._tok["tick"]  = self.eb.subscribe("time/sec", _cb_tick)

            self._tok["wifi"]  = self.eb.subscribe("status/wifi",    _cb_wifi)
            self._tok["bt"]    = self.eb.subscribe("status/bt",      _cb_bt)
            self._tok["lora"]  = self.eb.subscribe("status/lora",    _cb_lora)
            self._tok["batt"]  = self.eb.subscribe("status/battery", _cb_batt)
            self._tok["usb"]   = self.eb.subscribe("status/usb",     _cb_usb)
            self._tok["notif"] = self.eb.subscribe("status/notif",   _cb_notif)
            self._tok["notif2"]= self.eb.subscribe("status/notifications", _cb_notif)
        else:
            try: log_warn("clock_digital: no EventBus found")
            except Exception: pass

        # --- StatusStore Prime: sofortige Anfangswerte ---
        s = self._status_store()
        if s:
            for t, fn in (("status/wifi", self._on_wifi),
                          ("status/bt",   self._on_bt),
                          ("status/battery", self._on_batt),
                          ("status/notif", self._on_notif),
                          ("status/notifications", self._on_notif)):
                try:
                    p = s.get(t, fresh_only=False)
                    if p is not None:
                        fn(p)
                except Exception:
                    pass

    def on_hide(self, *a, **kw):
        self._visible = False
        if self.eb:
            for k, tok in list(self._tok.items()):
                if tok is not None:
                    try: self.eb.unsubscribe(tok)
                    except Exception: pass
                    self._tok[k] = None

    # ---------- rendering ----------

    def render_full(self, *a, **kw):
        if not self._visible: return
        draws_full_bg = bool(getattr(self.face, "DRAWS_FULL_BG", False))
        if not draws_full_bg:
            self._hard_clear()
        hh, mm = self._now_hm()
        try:
            self.face.render_full(hh, mm)
        except Exception as e:
            log_warn("face.render_full error: %r", e)
        self._last_hm = (hh, mm)

    def render(self, *a, **kw):
        if not self._visible: return
        hh, mm = self._now_hm()
        if (hh, mm) != self._last_hm:
            try:
                self.face.render(hh, mm)
            except Exception as e:
                log_warn("face.render error: %r", e)
            self._last_hm = (hh, mm)

    # ---------- event forwarding ----------

    def _forward(self, meth, payload):
        fn = getattr(self.face, meth, None)
        if callable(fn):
            try:
                fn(payload); return True
            except Exception as e:
                log_warn("%s error: %r", meth, e)
                return False
        return False

    # ---------- handlers ----------

    def _on_min(self, payload=None):
        if not self._visible: return
        self._forward("on_time", payload or {})
        try:
            hh = int(payload.get("hh")) if isinstance(payload, dict) else None
            mm = int(payload.get("mm")) if isinstance(payload, dict) else None
        except Exception:
            hh = mm = None
        if hh is None or mm is None:
            hh, mm = self._now_hm()
        if (hh, mm) != self._last_hm:
            try:
                self.face.render(hh, mm)
            except Exception as e:
                log_warn("face.render error: %r", e)
            self._last_hm = (hh, mm)

    def _on_sec(self, payload=None):
        if not self._visible: return
        if hasattr(self.face, "render_seconds"):
            try:
                t = time.localtime()
                self.face.render_seconds(int(t[3]), int(t[4]), int(t[5]))
            except Exception:
                pass

    def _on_tick_1hz(self, payload=None):
        if not self._visible: return
        s = self._status_store()
        if not s: return
        try:
            if s.get("status/bt", fresh_only=True) is None:
                self._set_icon("bt", "off")
        except Exception:
            pass
        try:
            if s.get("status/wifi", fresh_only=True) is None:
                self._set_icon("wifi", "off")
        except Exception:
            pass
        try:
            if (s.get("status/notifications", fresh_only=True) is None and
                s.get("status/notif", fresh_only=True) is None):
                self._set_icon("msg", "off")
        except Exception:
            pass

    # ---- icon helpers ----

    def _set_icon(self, group, state):
        fn = getattr(self.face, "set_icon", None)
        if callable(fn):
            try: fn(group, state)
            except Exception: pass

    # ---- domain handlers ----

    def _on_wifi(self, payload=None):
        if self._forward("on_wifi", payload): return
        s = (payload or {}).get("state", "off")
        if   s == "error":                      state = "error"
        elif s in ("on", "connected", "ap"):    state = "connected"
        elif s in ("search", "connecting"):     state = "connecting"
        else:                                   state = "off"
        self._set_icon("wifi", state)

    def _on_bt(self, payload=None):
        if self._forward("on_bt", payload): return
        s = (payload or {}).get("state", "off")
        if   s == "error":                          state = "error"
        elif s == "connected":                      state = "connected"
        elif s in ("on", "pairing", "connecting"):  state = "on"
        else:                                       state = "off"
        self._set_icon("bt", state)

    def _on_lora(self, payload=None):
        if self._forward("on_lora", payload): return
        s = (payload or {}).get("state", "off")
        if s == "error":      state = "error"
        elif s in ("on", "link", "tx", "rx"): state = "on"
        else:                  state = "off"
        self._set_icon("lora", state)

    def _on_usb(self, payload=None):
        if self._forward("on_usb", payload): return
        s = (payload or {}).get("state", "disconnected")
        self._set_icon("charge", "on" if s == "charging" else "off")

    def _on_batt(self, payload=None):
        if self._forward("on_battery", payload): return
        p = 0; charging = False
        try: p = int((payload or {}).get("percent", 0) or 0)
        except Exception: p = 0
        charging = bool((payload or {}).get("charging", False))
        self._set_icon("charge", "on" if charging else "off")
        if   p <= 10:   lvl = "0"
        elif p <= 30:   lvl = "20"
        elif p <= 50:   lvl = "40"
        elif p <= 70:   lvl = "60"
        elif p <= 90:   lvl = "80"
        else:           lvl = "100"
        self._set_icon("battery", lvl)

    def _on_notif(self, payload=None):
        if self._forward("on_notif", payload): return
        flag = 0
        try:
            if isinstance(payload, dict):
                if "flag" in payload:
                    flag = int(payload.get("flag") or 0)
                elif "count" in payload:
                    flag = 1 if int(payload.get("count") or 0) > 0 else 0
        except Exception:
            flag = 0
        self._set_icon("msg", "on" if flag else "off")


class ScreenClock(ScreenClockDigital): pass
class Screen(ScreenClockDigital): pass

