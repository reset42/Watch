# power_manager.py – FSM mit PMU-Button-Toggle (short), Events & KEEP_AWAKE
# - Fix: _publish spiegelt display/* sauber auf power/* (kein _emit_both)
# - Power-Treiber wird bei pwr=None automatisch erzeugt (AXP2101)
# - Public kick(): Timer-Reset ohne Seiteneffekte (für main.py-Wake-Flow)

# --- Logging-Setup mit DEBUG_PM-Schalter -------------------------------
try:
    import config
    _DBG_PM = bool(getattr(config, "DEBUG_PM", False))
except Exception:
    _DBG_PM = False

from core.logger import info as log_info, warn as log_warn
from core.logger import debug as _log_debug
def log_debug(*a, **k):
    if _DBG_PM:
        _log_debug(*a, **k)
# ----------------------------------------------------------------------

from core import eventbus  # nutzt den globalen EventBus (publish/subscribe)

try:
    import utime as _time
except ImportError:
    import time as _time

try:
    from machine import Pin
except Exception:
    Pin = None

try:
    import machine
except Exception:
    machine = None

try:
    import esp32
except Exception:
    esp32 = None

# Optionaler Power-Treiber-Fabrik (AXP2101)
try:
    from lib.power_axp2101 import create_power as _create_power
except Exception:
    _create_power = None

# ----------------------------------------------------------------------

def _ticks_ms():
    try:
        return _time.ticks_ms()
    except Exception:
        return int(_time.time() * 1000)

def _publish(topic, payload=None):
    """
    Publish auf den (globalen) EventBus.
    Zusätzlich: einfache, kompatible Spiegelung ausgewählter display/* Topics.
    """
    try:
        pub = getattr(eventbus, "publish", None)
        if not callable(pub):
            return
        # Original-Topic publizieren
        pub(topic, payload)
        # Kompatible Spiegelung
        if isinstance(topic, str) and topic.startswith("display/"):
            if topic == "display/dim":
                pub("power/will_dim", payload)
            elif topic == "display/wake":
                pub("power/active", payload)
    except Exception:
        pass

# Zustände
ACTIVE, DIM, SLEEP = "ACTIVE", "DIM", "SLEEP"

# ---- Wake-Konfiguration für die T‑Watch S3 ----
_WAKE_TOUCH_ENABLED = True
_WAKE_BTN_ENABLED   = True
_TOUCH_IRQ_PIN      = 16   # FT6236 IRQ
_AXP_IRQ_GPIO       = 21   # AXP Button/IRQ

# ----------------------------------------------------------------------

class PowerManager:
    """
    ACTIVE --(dim_timeout)--> DIM --(sleep_timeout)--> SLEEP
    ping()/Touch -> ACTIVE (+ wake)
    PMU-Button 'short' fungiert als Toggle: ACTIVE -> dim/sleep, DIM -> wake
    Events:
      power/state, power/will_dim, power/will_sleep
      display/dim, display/wake, sys/redraw_full, sys/wake
    """

    def __init__(self, disp=None, pwr=None, cfg=None, on_wake=None, display=None, touch=None):
        if display is None and disp is not None:
            display = disp
        self.display = display
        self.touch   = touch
        # Treiber ggf. automatisch erzeugen
        self.pwr     = pwr if pwr is not None else (_create_power() if _create_power else None)
        self.cfg     = cfg if cfg is not None else config
        self.on_wake = on_wake

        self.sleep_mode    = str(getattr(self.cfg, "SLEEP_MODE", "off")).lower()
        self.dim_timeout   = int(getattr(self.cfg, "DIM_TIMEOUT_MS", 30000))
        self.sleep_timeout = int(getattr(self.cfg, "SLEEP_TIMEOUT_MS", 90000))

        self.pre_dim_ms    = int(getattr(self.cfg, "PRE_DIM_NOTICE_MS", 2000))
        self.pre_sleep_ms  = int(getattr(self.cfg, "PRE_SLEEP_NOTICE_MS", 5000))

        self.wake_touch = bool(getattr(self.cfg, "WAKEUP_TOUCH", True))
        self.wake_btn   = bool(getattr(self.cfg, "WAKEUP_HW_BUTTON", True))
        self.gpio_touch = _TOUCH_IRQ_PIN
        self.gpio_axp   = _AXP_IRQ_GPIO

        self._state   = ACTIVE
        self._last    = _ticks_ms()
        self._dimmed  = False
        self._keep_awake = False

        self._notified_dim   = False
        self._notified_sleep = False

        log_info("PowerManager init:", self.sleep_mode, self.dim_timeout, self.sleep_timeout)
        _publish("power/state", {"state": self._state})

        # PMU-Button-IRQs aktivieren (falls vorhanden)
        try:
            if self.pwr and hasattr(self.pwr, "enable_pmu_button_irqs"):
                self.pwr.enable_pmu_button_irqs()
        except Exception as e:
            log_warn("enable_pmu_button_irq failed:", e)

    # ------------------- Public API -------------------
    def service(self):
        """Regelmäßig im main loop aufrufen."""
        now = _ticks_ms()
        diff = now - self._last
        log_debug(f"[PM] now={now} last={self._last} diff={diff} keep_awake={self._keep_awake} dimmed={self._dimmed}")

        # PMU-Button abfragen
        self._poll_pmu_button()

        if self.sleep_mode == "off":
            return

        if self._keep_awake:
            self._ensure_active_from_keep_awake()
            self._last = now
            self._clear_notices()
            return

        self._maybe_notice(now)

        if (not self._dimmed
            and self.sleep_mode in ("dim", "lightsleep", "deepsleep")
            and diff > self.dim_timeout):
            log_debug("[PM] DIM TRIGGER")
            self._enter_dim()

        if (self.sleep_mode in ("lightsleep", "deepsleep")
            and diff > self.sleep_timeout):
            log_debug("[PM] SLEEP TRIGGER")
            self._enter_sleep(self.sleep_mode)
            self._last = _ticks_ms()
            self._clear_notices()

    def ping(self):
        """Benutzeraktivität – z. B. von ScreenManager bei Touch/Swipe aufrufen."""
        self._last = _ticks_ms()
        _publish("sys/activity")
        if self._dimmed:
            self.display_wake()
        if self._state != ACTIVE:
            self._set_state(ACTIVE)
        self._clear_notices()

    def kick(self):
        """Nur Timer zurücksetzen (für Wake-Handler in main.py), ohne Display-Seiteneffekte."""
        self._last = _ticks_ms()
        self._clear_notices()

    def set_keep_awake(self, enabled: bool):
        enabled = bool(enabled)
        if enabled != self._keep_awake:
            self._keep_awake = enabled
            log_debug("KEEP_AWAKE =", enabled)
            if enabled:
                if self._dimmed:
                    self.display_wake()
                if self._state != ACTIVE:
                    self._set_state(ACTIVE)
                self._clear_notices()

    def get_state(self):
        return self._state

    def is_dimmed(self):
        return self._dimmed

    # ------------------- Battery helpers for main.py -------------------
    def get_percent(self):
        try:
            if self.pwr and hasattr(self.pwr, "read_status"):
                st = self.pwr.read_status(False)
                return st.get("percent")
        except Exception:
            pass
        return None

    def is_charging(self):
        try:
            if self.pwr and hasattr(self.pwr, "read_status"):
                st = self.pwr.read_status(False)
                return (st.get("charge_direction") == "charging")
        except Exception:
            pass
        return None

    # ------------------- State helpers -------------------
    def _set_state(self, st):
        if st == self._state:
            return
        self._state = st
        _publish("power/state", {"state": st})

    def _clear_notices(self):
        self._notified_dim = False
        self._notified_sleep = False

    def _maybe_notice(self, now_ms):
        if (not self._dimmed and self.pre_dim_ms > 0
            and self.sleep_mode in ("dim","lightsleep","deepsleep")):
            eta = self.dim_timeout - (now_ms - self._last)
            if eta <= self.pre_dim_ms and not self._notified_dim and eta > 0:
                _publish("power/will_dim", {"eta_ms": eta})
                self._notified_dim = True

        if (self.pre_sleep_ms > 0 and self.sleep_mode in ("lightsleep","deepsleep")):
            eta = self.sleep_timeout - (now_ms - self._last)
            if eta <= self.pre_sleep_ms and not self._notified_sleep and eta > 0:
                _publish("power/will_sleep", {"eta_ms": eta})
                self._notified_sleep = True

    def _ensure_active_from_keep_awake(self):
        if self._dimmed:
            self.display_wake()
        if self._state != ACTIVE:
            self._set_state(ACTIVE)

    # ------------------- Actions -------------------
    def display_dim(self):
        try:
            _publish("display/dim")
            log_debug("display dim requested")
        except Exception as e:
            log_warn("display_dim failed:", e)

    def display_wake(self):
        try:
            self._clear_touch_irq()
            _publish("display/wake")
            _publish("sys/redraw_full")
            log_debug("display wake requested")
        except Exception as e:
            log_warn("display_wake failed:", e)
        finally:
            self._dimmed = False

    def handle_wake(self):
        try:
            self._clear_touch_irq()
            _publish("sys/wake")
            _publish("sys/redraw_full")
            log_debug("system woke")
            self._set_state(ACTIVE)
            if self.on_wake:
                try:
                    self.on_wake()
                except Exception as e:
                    log_warn("on_wake failed:", e)
        except Exception as e:
            log_warn("handle_wake failed:", e)
        finally:
            self._dimmed = False
            self._clear_notices()

    # ------------------- PMU Button -------------------
    def _poll_pmu_button(self):
        """Liest PMU-Button-IRQs aus lib.power und reagiert auf 'short'."""
        if not self.pwr:
            return
        try:
            fn = getattr(self.pwr, "poll_pmu_button", None)
            if not callable(fn):
                return
            ev = fn()  # Liste wie ['short','pos'] oder []
            if not ev:
                return
            _publish("pmu/button", {"events": ev})
            log_debug(f"[PM] PMU button events: {ev}")

            if "short" in ev:
                self._on_button_short()

        except Exception as e:
            log_warn("poll_pmu_button failed:", e)

    def _on_button_short(self):
        now = _ticks_ms()
        # DIM -> Wake (hell + ACTIVE)
        if self._state == DIM or self._dimmed:
            self.display_wake()
            self._set_state(ACTIVE)
            self._last = now
            self._clear_notices()
            return

        # ACTIVE -> je nach SLEEP_MODE
        if self._state == ACTIVE:
            if self.sleep_mode == "dim":
                self._enter_dim()
                self._last = now  # Timer neu starten
            elif self.sleep_mode in ("lightsleep", "deepsleep"):
                self._enter_sleep(self.sleep_mode)
                self._last = _ticks_ms()
            return
        # SLEEP: wird HW-seitig aufgeweckt; nichts zu tun

    # ------------------- Internals -------------------
    def _enter_dim(self):
        self.display_dim()
        self._dimmed = True
        self._set_state(DIM)

    def _prepare_for_sleep(self):
        try:
            self.display_dim()
        except Exception as e:
            log_warn("prepare_for_sleep dim failed:", e)

    def _enable_wake_sources(self):
        if not esp32 or not Pin:
            log_warn("esp32/Pin not available; wake sources not configured")
            return
        pins = []
        try:
            if self.wake_touch and self.gpio_touch is not None:
                pins.append(Pin(self.gpio_touch, Pin.IN))
            if self.wake_btn and self.gpio_axp is not None:
                pins.append(Pin(self.gpio_axp, Pin.IN))
        except Exception as e:
            log_warn("Pin setup failed:", e)
            pins = []
        if not pins:
            log_warn("No wake pins configured")
            return
        try:
            esp32.wake_on_ext1(pins=tuple(pins), level=esp32.WAKEUP_ALL_LOW)
        except Exception:
            mask = 0
            for p in pins:
                try:
                    pid = p.id() if hasattr(p, "id") else None
                    if isinstance(pid, int):
                        mask |= (1 << pid)
                except Exception:
                    pass
            try:
                esp32.wake_on_ext1(pins=mask, level=esp32.WAKEUP_ALL_LOW)
            except Exception as e:
                log_warn("wake_on_ext1 failed:", e)

    def _enter_sleep(self, mode):
        if not machine:
            log_warn("machine not available; skip sleep")
            return
        self._prepare_for_sleep()
        self._enable_wake_sources()
        self._set_state(SLEEP)
        if mode == "lightsleep":
            log_info("Entering LIGHTSLEEP…")
            try:
                machine.lightsleep()
            except Exception as e:
                log_warn("lightsleep failed:", e)
            self.handle_wake()
        elif mode == "deepsleep":
            log_info("Entering DEEPSLEEP…")
            try:
                machine.deepsleep()
            except Exception as e:
                log_warn("deepsleep failed:", e)

    def _clear_touch_irq(self):
        try:
            if self.touch and hasattr(self.touch, "clear_irq"):
                self.touch.clear_irq()
                return
            _publish("touch/clear_irq")
        except Exception:
            pass
