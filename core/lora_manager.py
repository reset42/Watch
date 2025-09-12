# core/lora_manager.py – v1.0
# LoraManager für SX1262 auf T-Watch S3
# - EventBus-Anbindung (status/lora, diag/lora, cmd/lora)
# - States: off | on | rx | tx | link | error
# - DIM-Policy: RX immer an, TX optional blockiert (config.LORA_PAUSE_ON_DIM)
# - IRQ Wake: RX_DONE holt Payload, setzt state=link, publisht sys/wake
# - DutyCycle/Backoff/Ratelimit analog zu Wi-Fi/BT
# - Keine Legacy-Topics, nur cmd/lora

import time, config
from core import eventbus as bus
from core.logger import debug as log_debug, warn as log_warn
from lib.lora_sx1262 import SX1262

class LoraManager:
    def __init__(self, eventbus=bus, cfg=config, logger=log_debug):
        self.bus     = eventbus
        self.cfg     = cfg
        self.log     = logger
        self.radio   = SX1262()
        self.enabled = False
        self._last_state = None
        self._dim_paused = False
        self.last_poll   = 0
        self.backoff_ms  = 0
        self.cooldown_ms = getattr(cfg, "LORA_TX_COOLDOWN_MS", 0)
        self.last_tx     = 0
        self.last_diag   = 0
        self.diag_limit  = getattr(cfg, "LORA_DIAG_RATE_LIMIT_MS", 250)

        # IRQ callback
        if self.radio.have_hw:
            self.radio.set_irq(self._on_irq)

        # subscribe to cmds + power events
        self.bus.subscribe("cmd/lora", self._on_cmd)
        self.bus.subscribe("display/dim", self._on_dim)
        self.bus.subscribe("display/wake", self._on_wake)
        self.bus.subscribe("power/will_sleep", self._on_sleep)
        self.bus.subscribe("sys/wake", self._on_wake)

    # ------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------
    def start(self):
        if not self.radio.have_hw:
            self._publish("error"); return
        self.enabled = True
        try:
            self.radio.active(True)
            self.radio.start_rx()
            self._publish("on")
            self._publish("rx")
        except Exception as e:
            log_warn("LoraManager start failed: %r" % e)
            self._publish("error")

    def stop(self):
        self.enabled = False
        try:
            self.radio.stop_rx()
            self.radio.active(False)
        except Exception:
            pass
        self._publish("off")

    # ------------------------------------------------------------
    # Poll (non-blocking, im main-loop)
    # ------------------------------------------------------------
    def poll(self, now_ms):
        if not self.enabled or not self.radio.have_hw:
            return
        # Backoff warten
        if self.backoff_ms > 0:
            if time.ticks_diff(now_ms, self.last_poll) >= self.backoff_ms:
                self.backoff_ms = 0
            else:
                return
        # Periodisch RX-State halten
        if time.ticks_diff(now_ms, self.last_poll) >= 1000:
            self.last_poll = now_ms
            self._publish("rx")

    # ------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------
    def _on_cmd(self, ev=None, **kw):
        p = ev or kw
        state = (p or {}).get("state")
        if not state: return
        if state == "on":
            self.start()
        elif state == "off":
            self.stop()
        elif state == "toggle":
            if self.enabled: self.stop()
            else: self.start()
        elif state == "rx":
            if self.enabled: self.radio.start_rx(); self._publish("rx")
        elif state == "tx":
            if not self.enabled or not self.radio.have_hw: return
            if self._dim_paused and getattr(self.cfg, "LORA_PAUSE_ON_DIM", True):
                self._diag("tx_blocked"); return
            # cooldown?
            now = time.ticks_ms()
            if self.cooldown_ms and time.ticks_diff(now, self.last_tx) < self.cooldown_ms:
                self._diag("tx_cooldown"); return
            payload = p.get("payload", b"")
            try:
                self.radio.send(payload)
                self.last_tx = now
                self._publish("tx")
            except Exception as e:
                log_warn("LoraManager tx failed: %r" % e)
                self._publish("error")

    # ------------------------------------------------------------
    # Power / Display Handling
    # ------------------------------------------------------------
    def _on_dim(self, ev=None, **kw):
        if not self.enabled: return
        if getattr(self.cfg, "LORA_PAUSE_ON_DIM", True):
            self._dim_paused = True
            self._diag("dim_radio_off")
        else:
            self._diag("dim_radio_on")

    def _on_wake(self, ev=None, **kw):
        if not self.enabled: return
        self._dim_paused = False
        self._diag("wake")
        try:
            self.radio.start_rx()
            self._publish("rx")
        except Exception as e:
            log_warn("LoraManager wake rx failed: %r" % e)

    def _on_sleep(self, ev=None, **kw):
        if not self.enabled: return
        if getattr(self.cfg, "SLEEP_LORA", True):
            try:
                self.radio.stop_rx(); self.radio.active(False)
                self._publish("off")
            except Exception:
                pass
        else:
            # nur TX blockieren
            self._dim_paused = True
            self._diag("sleep_radio_rx_only")

    # ------------------------------------------------------------
    # IRQ Callback (radio → manager)
    # ------------------------------------------------------------
    def _on_irq(self, event, payload=None):
        if event == "tx_done":
            self._publish("link")
        elif event == "rx":
            data = (payload or {}).get("bytes", b"")
            self._publish("link")
            self._diag("rx", extra={"len": len(data)})
            # sys/wake damit UI reagiert
            self.bus.publish("sys/wake", {"src":"lora"})
            # optional Payload weiterreichen
            self.bus.publish("lora/rx", {"data": data})

    # ------------------------------------------------------------
    # Publish Helpers
    # ------------------------------------------------------------
    def _publish(self, state: str):
        if state == self._last_state:
            return
        self._last_state = state
        payload = {"state": state, "ts": time.time()}
        ttl = getattr(config, "LORA_STATUS_TTL_MS", 8000)
        try:
            self.bus.publish("status/lora", payload, ttl_ms=ttl)
            self._diag(state)
        except Exception as e:
            log_warn("LoraManager publish failed: %r" % e)
        if getattr(self.cfg, "DEBUG_LORA", False):
            self.log(f"[LORA] {state}")

    def _diag(self, event, extra=None):
        now = time.ticks_ms()
        if time.ticks_diff(now, self.last_diag) < self.diag_limit:
            return
        self.last_diag = now
        payload = {"event": event, "ts": time.time()}
        if extra: payload.update(extra)
        self.bus.publish("diag/lora", payload)
