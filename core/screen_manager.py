# screen_manager.py – Screen/Lifecycle/Navigation-Manager mit Logging
import time

# --- Config-Flags laden ---
try:
    import config
    _DBG_TOUCH = bool(getattr(config, "DEBUG_TOUCH", False))
    _DBG_NAV   = bool(getattr(config, "DEBUG_NAV",   False))
    _DBG_SM    = bool(getattr(config, "DEBUG_SM",    False))  # sehr spammy
except Exception:
    _DBG_TOUCH = False
    _DBG_NAV   = False
    _DBG_SM    = False

# --- Logger-API aus deinem Core-Logger ---
try:
    from core.logger import debug as log_debug, info as log_info, warn as log_warn, error as log_error
except Exception:
    # Fallbacks (sollte nicht nötig sein)
    def log_debug(*a, **k): pass
    def log_info(*a, **k):  pass
    def log_warn(*a, **k):  pass
    def log_error(*a, **k): pass


class ScreenManager:
    """
    Verwaltet aktiven Screen, Gestenrouting und Hidden-Back-Stack.
    - Links/Rechts: Hauptpunkte
    - Hoch/Runter:  Unterpunkte (via Nav vchain)
    - Hidden:       kein Swipe (nur Back-Button)
    """
    def __init__(self, nav, touch, eventbus=None, pm=None):
        self.nav = nav
        self.touch = touch
        self.eventbus = eventbus
        self._screens = {}      # id -> instance
        self._active = None
        self._stack = []        # für Hidden Screens (Back)
        self.pm = pm  # optional PowerManager

        if _DBG_NAV:
            log_info("SM.init: nav=%s touch=%s bus=%s", type(nav).__name__, type(touch).__name__ if touch else None, type(eventbus).__name__ if eventbus else None)

    # --- Registrierung & Anzeigen ---
    def register(self, screens_dict):
        self._screens.update(screens_dict)
        if _DBG_NAV:
            log_info("SM.register: %d screens (%s)", len(self._screens), ", ".join(sorted(self._screens.keys())))

    def show(self, screen_id, *, push=False):
        if _DBG_NAV:
            log_info("SM.show -> %s (push=%s)", screen_id, push)

        # aktuellen Screen verstecken
        if self._active and self._active in self._screens:
            old = self._screens[self._active]
            if _DBG_NAV:
                log_debug("SM.on_hide: %s", self._active)
            try:
                if hasattr(old, "on_hide"):
                    old.on_hide()
            except Exception as e:
                log_warn("SM.on_hide error for %s: %r", self._active, e)

        # Back-Stack für Hidden
        if push and self._active:
            self._stack.append(self._active)
            if _DBG_NAV:
                log_debug("SM.stack push: %s (depth=%d)", self._active, len(self._stack))

        self._active = screen_id
        scr = self._screens.get(screen_id)
        if not scr:
            log_warn("SM.show: screen id not found: %s", screen_id)
            return

        # KEEP_AWAKE an PowerManager weitergeben (wenn vorhanden)
        if self.pm and hasattr(self.pm, 'set_keep_awake'):
            try:
                ka = bool(getattr(scr, "KEEP_AWAKE", False))
                self.pm.set_keep_awake(ka)
                if _DBG_NAV:
                    log_debug("SM.keep_awake(%s) for %s", ka, screen_id)
            except Exception as e:
                log_warn("SM.keep_awake error: %r", e)

        # Canvas säubern & initial rendern
        self._hard_clear(scr)
        self._initial_paint(scr)

        # Eventbus melden
        if self.eventbus:
            try:
                self.eventbus.publish("screen/changed", {"id": screen_id})
                if _DBG_NAV:
                    log_debug("SM.bus publish: screen/changed -> %s", screen_id)
            except Exception as e:
                log_warn("SM.bus publish error: %r", e)

    # --- Hidden Screens ---
    def open_hidden(self, target_id):
        if _DBG_NAV:
            log_info("SM.open_hidden -> %s", target_id)
        self.show(target_id, push=True)

    def back(self):
        if not self._stack:
            if _DBG_NAV:
                log_debug("SM.back: stack empty")
            return
        prev = self._stack.pop()
        if _DBG_NAV:
            log_info("SM.back -> %s (depth now %d)", prev, len(self._stack))
        self.show(prev)

    # --- Main Loop Tick ---
    def update(self):
        scr = self._screens.get(self._active)
        if _DBG_SM:
            log_debug("SM.update: current=%s", self._active)

        # Touch lesen
        evt = self._read_touch()
        if _DBG_TOUCH and evt is not None:
            log_debug("[touch evt] %s", evt)

        # Nur bei echter Aktivität pm.ping()
        if evt and self.pm and hasattr(self.pm, 'ping'):
            try:
                self.pm.ping()
                if _DBG_SM:
                    log_debug("SM.pm_ping")
            except Exception as e:
                log_warn("SM.pm_ping error: %r", e)

        # Screen-spezifische Touch-Handler
        if evt and scr and hasattr(scr, "on_touch"):
            try:
                scr.on_touch(evt)
                if _DBG_SM:
                    log_debug("SM.on_touch dispatched to %s", self._active)
            except Exception as e:
                log_warn("SM.on_touch error for %s: %r", self._active, e)

        # Swipe-Routing
        dirn = self._swipe_dir(evt)
        if dirn in ("left", "right", "up", "down"):
            consumed = False
            if scr and hasattr(scr, "on_swipe"):
                try:
                    consumed = bool(scr.on_swipe(dirn))
                    if _DBG_TOUCH:
                        log_debug("SM.on_swipe(%s) -> consumed=%s by %s", dirn, consumed, self._active)
                except Exception as e:
                    log_warn("SM.on_swipe error for %s: %r", self._active, e)
                    consumed = False
            if not consumed:
                if _DBG_TOUCH:
                    log_info("[swipe] %s", dirn)
                self._route_swipe(dirn)

        # Screen Tick
        if scr and hasattr(scr, "tick"):
            try:
                scr.tick()
                if _DBG_SM:
                    log_debug("SM.tick -> %s", self._active)
            except Exception as e:
                log_warn("SM.tick error for %s: %r", self._active, e)

        #time.sleep_ms(50)

    # --- Hilfen ---
    def is_hidden(self, sid=None):
        sid = sid or self._active
        if sid in self.nav.main:
            return False
        if self.nav.parent_of(sid):
            return False
        return True

    def _hard_clear(self, scr):
        try:
            d = getattr(scr, "d", None)
            if not d:
                return
            if _DBG_NAV:
                log_debug("SM.clear canvas for %s", getattr(scr, "SCREEN_ID", "?"))
            if hasattr(d, "fill"):
                d.fill(0)
            elif hasattr(d, "clear"):
                d.clear()
            elif hasattr(d, "cls"):
                d.cls()
            if hasattr(d, "update"):
                d.update()
        except Exception as e:
            log_warn("SM._hard_clear error: %r", e)

    def _initial_paint(self, scr):
        """
        Volle Initialisierung: bevorzugte Reihenfolge:
        1) on_show(), 2) render_full(), 3) render(), 4) tick()
        -> So ist sichergestellt, dass Subscriptions zuerst aktiv sind.
        """
        order = ("on_show", "render_full", "render", "tick")
        for fn in order:
            f = getattr(scr, fn, None)
            if callable(f):
                try:
                    if _DBG_NAV:
                        log_debug("SM.init call %s.%s", getattr(scr, "SCREEN_ID", "?"), fn)
                    f()
                    return
                except Exception as e:
                    log_warn("SM.init %s.%s error: %r", getattr(scr, "SCREEN_ID", "?"), fn, e)

    def _maybe_poll_touch(self):
        t = self.touch
        if not t:
            return
        for name in ("update", "poll", "refresh"):
            fn = getattr(t, name, None)
            if callable(fn):
                try:
                    fn()
                    if _DBG_SM:
                        log_debug("SM.touch.%s()", name)
                except Exception as e:
                    log_warn("SM.touch.%s error: %r", name, e)

    def _read_touch(self):
        t = self.touch
        if not t:
            return None
        self._maybe_poll_touch()

        for name in ("get_event", "read_event", "read", "next", "event"):
            fn = getattr(t, name, None)
            if callable(fn):
                try:
                    ev = fn()
                    if ev is not None:
                        return ev
                except Exception as e:
                    log_warn("SM.touch.%s error: %r", name, e)
        return None

    def _swipe_dir(self, evt):
        if evt is None:
            return None
        if isinstance(evt, dict):
            if evt.get("type") == "swipe":
                return evt.get("dir") or evt.get("direction")
            return None
        for a in ("swipe", "dir", "direction"):
            v = getattr(evt, a, None)
            if v:
                return v
        return None

    def _route_swipe(self, direction):
        cur = self._active
        if not cur:
            return
        if self.is_hidden(cur):
            if _DBG_NAV:
                log_debug("SM.route: ignore swipe on hidden %s", cur)
            return

        nxt = None
        if direction == "left":
            nxt = self.nav.right_of(cur)
        elif direction == "right":
            nxt = self.nav.left_of(cur)
        elif direction == "down":
            nxt = self.nav.down_of(cur)   # ↓ = in vchain nach oben (Index -1)
        elif direction == "up":
            nxt = self.nav.up_of(cur)     # ↑ = in vchain nach unten (Index +1)

        if _DBG_NAV:
            log_info("SM.route %s: %s -> %s", direction, cur, nxt)

        if nxt:
            self.show(nxt)