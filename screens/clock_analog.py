# screens/clock_analog.py — v0.4.8
# Fixes:
# - Priming aus StatusStore vor erstem Draw (fresh_only=False)
# - Sofortiges Icon-Replay nach Priming (hartes Zeichnen/Flush)
# - Kein _defer_icon_draw mehr (kann Zeichnen verhindern, je nach Face)
# - Robustere Logs zum Debuggen
import sys, time
import config

try:
    from core.logger import info as log_info, warn as log_warn, debug as log_debug
except Exception:
    def log_info(*a, **k):  pass
    def log_warn(*a, **k):  pass
    def log_debug(*a, **k): pass


class _BusAdapter:
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
        try:
            if hasattr(b, "unsubscribe") and callable(b.unsubscribe):
                try: b.unsubscribe(token); return
                except TypeError: pass
            import types
            if isinstance(b, types.ModuleType) and hasattr(b, "unsubscribe"):
                try: b.unsubscribe(token); return
                except TypeError: pass
        except Exception:
            pass
        try:
            if hasattr(b, "unsubscribe") and callable(b.unsubscribe) and topic and cb:
                b.unsubscribe(topic, cb); return
        except Exception:
            pass
        try:
            import types
            if isinstance(b, types.ModuleType) and hasattr(b, "unsubscribe") and topic and cb:
                b.unsubscribe(topic, cb)
        except Exception:
            pass


class ScreenClockAnalog:
    SCREEN_ID  = "clock_analog"
    KEEP_AWAKE = False

    def __init__(self, d=None, manager=None, eventbus=None, **kw):
        self.d = d
        self.manager = manager
        self.eb = self._resolve_bus(eventbus, manager)

        self.face = None
        self._face_id = None
        self._visible = False
        self._last_hms = (-1, -1, -1)
        self._primed = False

        self._tok = {
            "min": None, "sec": None, "tick": None,
            "wifi": None, "bt": None, "lora": None,
            "batt": None, "usb": None, "notif": None, "notif2": None,
            "scr": None,
        }

        # Bereits jetzt auf "screen/changed" hören (kommt kurz nach on_show)
        if self.eb:
            def _extract_payload(*args, **kw):
                if args:
                    if isinstance(args[-1], dict): return args[-1]
                    if isinstance(args[0], dict):  return args[0]
                    if isinstance(args[0], str):   return {"id": args[0]}
                if kw:
                    p = kw.get("payload")
                    if isinstance(p, dict): return p
                    if isinstance(p, str):  return {"id": p}
                return {}
            def _cb_screen_changed(*a, **k):
                p = _extract_payload(*a, **k) or {}
                target = p.get("id") or p.get("to") or p.get("screen")
                if target == self.SCREEN_ID:
                    log_debug("clock_analog: screen/changed → re-prime")
                    self._prime_icons_from_store()
                    self._replay_icons()
            try:
                self._tok["scr"] = self.eb.subscribe("screen/changed", _cb_screen_changed)
            except Exception:
                pass

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
        # 1) Manager.status
        m = getattr(self, "manager", None)
        if m:
            s = getattr(m, "status", None)
            if s: return s
            nav = getattr(m, "nav", None)
            if nav:
                s = getattr(nav, "status", None)
                if s: return s
        # 3) Screen.status
        s = getattr(self, "status", None)
        if s: return s
        # 4) Face.status
        f = getattr(self, "face", None)
        if f:
            s = getattr(f, "status", None)
            if s: return s
        return None

    # ---------- mapping helpers ----------

    def _map_wifi(self, payload):
        s = (payload or {}).get("state", "off")
        if s in ("connected", "ap"): return "connected"
        if s in ("off","disabled"):  return "off"
        return "connecting"

    def _map_bt(self, payload):
        s = (payload or {}).get("state", "off")
        if s == "connected": return "connected"
        if s in ("on","pairing","connecting"): return "on"
        if s == "error": return "error"
        return "off"

    def _map_battery_level(self, payload):
        p = 0
        try: p = int((payload or {}).get("percent", 0) or 0)
        except Exception: p = 0
        if   p <= 10: return "0"
        elif p <= 30: return "20"
        elif p <= 50: return "40"
        elif p <= 70: return "60"
        elif p <= 90: return "80"
        else: return "100"

    def _map_msg(self, payload):
        try:
            if isinstance(payload, dict):
                if "count" in payload:
                    return "on" if int(payload.get("count") or 0) > 0 else "off"
                if "flag" in payload:
                    return "on" if int(payload.get("flag") or 0) == 1 else "off"
        except Exception:
            pass
        return "off"

    # ---------- priming + replay ----------

    def _prime_icons_from_store(self):
        s = self._status_store()
        if not s or not self.face:
            log_debug("clock_analog: no store/face for priming")
            return False
        try:
            wifi_p  = s.get("status/wifi",    fresh_only=False)
            bt_p    = s.get("status/bt",      fresh_only=False)
            batt_p  = s.get("status/battery", fresh_only=False)
            usb_p   = s.get("status/usb",     fresh_only=False)
            notif_p = s.get("status/notifications", fresh_only=False) or s.get("status/notif", fresh_only=False)
        except Exception as e:
            log_warn("clock_analog: store.get failed: %r", e)
            wifi_p = bt_p = batt_p = usb_p = notif_p = None

        # per Handler ausführen → zeichnet nur Dirty-Rects
        if wifi_p  is not None: self._on_wifi(wifi_p)
        if bt_p    is not None: self._on_bt(bt_p)
        if batt_p  is not None: self._on_batt(batt_p)
        if usb_p   is not None: self._on_usb(usb_p)
        if notif_p is not None: self._on_notif(notif_p)

        return True

    def _replay_icons(self):
        """Erzwinge sichtbares Zeichnen der bereits gesetzten Icon-States."""
        try:
            groups = ("wifi","bt","battery","charge","msg","lora","usb")
            if hasattr(self.face, "set_icon"):
                for g in groups:
                    s = None
                    if hasattr(self.face, "icons") and isinstance(self.face.icons, dict):
                        s = self.face.icons.get(g)
                    if s is not None:
                        try: self.face.set_icon(g, s)
                        except Exception: pass
            if hasattr(self.face, "request_draw"):
                self.face.request_draw()
            else:
                t = time.localtime()
                if hasattr(self.face, "render"):
                    self.face.render(int(t[3]), int(t[4]), int(t[5]))
        except Exception:
            pass

    # ---------- drawing helpers ----------

    def _now_hms(self):
        t = time.localtime()
        return int(t[3]), int(t[4]), int(t[5])

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
            return s or "classic_black_analog"
        except Exception:
            return "classic_black_analog"

    def _load_face(self, face_id):
        pkg = "ui.watchfaces_analog.%s.main" % face_id
        try:
            if getattr(config, "DEV_HOTRELOAD_WF", False) and pkg in sys.modules:
                del sys.modules[pkg]
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
            if br is None: raise
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
            s = self._status_store()
            if s: setattr(self.face, "status", s)
        except Exception:
            pass

    # ---------- lifecycle ----------

    def on_show(self, *a, **kw):
        self._visible = True

        # 1) Face laden/wechseln
        face_id = self._normalize_face_id(getattr(config, "ACTIVE_WATCHFACE_ANALOG", "classic_black_analog"))
        if (self.face is None) or (self._face_id != face_id):
            self._load_face(face_id)

        # 2) Zeichenfläche vorbereiten
        draws_full_bg = bool(getattr(self.face, "DRAWS_FULL_BG", False))
        if not draws_full_bg:
            self._hard_clear()

        # 3) Priming aus Store (vor dem ersten sichtbaren Render)
        primed = self._prime_icons_from_store()
        log_debug("clock_analog: primed=%s", primed)

        # 4) Erstes Render (BG + Hände), Hände direkt „ziehen“
        hh, mm, ss = self._now_hms()
        try:
            if hasattr(self.face, "render_full"):
                self.face.render_full(hh, mm, ss)
            elif hasattr(self.face, "render"):
                self.face.render(hh, mm, ss)
            if hasattr(self.face, "render"):
                self.face.render(hh, mm, ss)
        except Exception as e:
            try: log_warn("face initial draw error: %r", e)
            except Exception: pass
        self._last_hms = (hh, mm, ss)

        # 5) Icons aktiv sichtbar machen
        self._replay_icons()

        # 6) Live-Events abonnieren
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

        if self.eb:
            self._tok["min"]   = self.eb.subscribe("time/min", _cb_min)
            self._tok["sec"]   = self.eb.subscribe("time/sec", _cb_sec)
            self._tok["tick"]  = self.eb.subscribe("time/sec", _cb_tick)

            self._tok["wifi"]  = self.eb.subscribe("status/wifi",          _cb_wifi)
            self._tok["bt"]    = self.eb.subscribe("status/bt",            _cb_bt)
            self._tok["lora"]  = self.eb.subscribe("status/lora",          _cb_lora)
            self._tok["batt"]  = self.eb.subscribe("status/battery",       _cb_batt)
            self._tok["usb"]   = self.eb.subscribe("status/usb",           _cb_usb)
            self._tok["notif"] = self.eb.subscribe("status/notifications", _cb_notif)
            self._tok["notif2"]= self.eb.subscribe("status/notif",         _cb_notif)  # Legacy
        else:
            try: log_warn("clock_analog: no EventBus found")
            except Exception: pass

        self._primed = True

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
        hh, mm, ss = self._now_hms()
        try:
            self.face.render_full(hh, mm, ss)
        except Exception as e:
            log_warn("face.render_full error: %r", e)
        try:
            if hasattr(self.face, "render"):
                self.face.render(hh, mm, ss)
        except Exception:
            pass
        self._last_hms = (hh, mm, ss)

    def render(self, *a, **kw):
        if not self._visible: return
        hh, mm, ss = self._now_hms()
        try:
            self.face.render(hh, mm, ss)
        except Exception as e:
            log_warn("face.render error: %r", e)
        self._last_hms = (hh, mm, ss)

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
        ss = self._last_hms[2]
        if hh is None or mm is None:
            hh, mm, ss = self._now_hms()
        try:
            self.face.render(hh, mm, ss)
        except Exception as e:
            log_warn("face.render(min) error: %r", e)
        self._last_hms = (hh, mm, ss)

    def _on_sec(self, payload=None):
        if not self._visible: return
        try:
            t = time.localtime()
            hh, mm, ss = int(t[3]), int(t[4]), int(t[5])
            if hasattr(self.face, "render_sec") and callable(getattr(self.face, "render_sec")):
                self.face.render_sec(hh, mm, ss)
            elif hasattr(self.face, "render_seconds") and callable(getattr(self.face, "render_seconds")):
                self.face.render_seconds(hh, mm, ss)
            else:
                self.face.render(hh, mm, ss)
            self._last_hms = (hh, mm, ss)
        except Exception:
            pass

    def _on_tick_1hz(self, payload=None):
        if not self._visible or not self._primed: return
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

    def _invalidate_icon_rect(self, group):
        face = self.face
        try:
            rects = getattr(face, "ICON_RECTS", None)
            if isinstance(rects, dict) and group in rects:
                x,y,w,h = rects[group]
                if hasattr(face, "invalidate_region"):
                    face.invalidate_region(x,y,w,h)
                if hasattr(face, "request_draw"):
                    face.request_draw()
                return True
        except Exception:
            pass
        return False

    def _set_icon(self, group, state):
        fn = getattr(self.face, "set_icon", None)
        if callable(fn):
            try: fn(group, state)
            except Exception: pass
        if not self._invalidate_icon_rect(group):
            try:
                t = time.localtime()
                self.face.render(int(t[3]), int(t[4]), int(t[5]))
            except Exception:
                pass

    # ---- domain handlers (live) ----

    def _on_wifi(self, payload=None):
        if self._forward("on_wifi", payload): return
        self._set_icon("wifi", self._map_wifi(payload))

    def _on_bt(self, payload=None):
        if self._forward("on_bt", payload): return
        self._set_icon("bt", self._map_bt(payload))

    def _on_lora(self, payload=None):
        if self._forward("on_lora", payload): return
        s = (payload or {}).get("state", "off")
        state = "error" if s == "error" else ("on" if s in ("on","link","tx","rx") else "off")
        self._set_icon("lora", state)

    def _on_usb(self, payload=None):
        if self._forward("on_usb", payload): return
        s = (payload or {}).get("state", "disconnected")
        self._set_icon("charge", "on" if s == "charging" else "off")

    def _on_batt(self, payload=None):
        if self._forward("on_battery", payload): return
        lvl = self._map_battery_level(payload)
        self._set_icon("battery", lvl)
        ch = "on" if (isinstance(payload, dict) and payload.get("charging")) else "off"
        self._set_icon("charge", ch)

    def _on_notif(self, payload=None):
        if self._forward("on_notif", payload): return
        self._set_icon("msg", self._map_msg(payload))


class ScreenClock(ScreenClockAnalog): pass
class Screen(ScreenClockAnalog): pass