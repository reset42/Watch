try:
    import network, time
    from time import ticks_ms, ticks_diff
except Exception:
    network = None
    from time import time as _time
    def ticks_ms(): return int(_time()*1000)
    def ticks_diff(a,b): return a-b
    time = __import__("time")

from core.logger import info, warn, debug
from core import eventbus as bus_mod  # fallback, falls jemand publish() am Modul nutzt

class WifiState:
    OFF="off"; ON="on"; SEARCH="search"; CONNECTING="connecting"
    CONNECTED="connected"; AP="ap"; ERROR="error"

class WifiManager:
    """
    Single owner für Wi-Fi. Redet ausschließlich über Eventbus.

    Published:
      - status/wifi {state, rssi?, ssid?, ip?, ts}
        (TTL per kw: ttl_ms → StatusStore)
      - diag/wifi   {event, detail, ts}

    Subscribed:
      - wifi/enable | wifi/disable | wifi/toggle | wifi/reconnect
      - power/will_dim | display/dim | power/will_sleep
      - display/wake | power/active | power/wake | sys/wake
    """

    # ---------- lifecycle ----------
    def __init__(self, eventbus, cfg, logger=None):
        self.bus = eventbus
        self.cfg = cfg
        self.log = logger or info

        # Konfig
        self._enabled = bool(getattr(cfg, "WIFI_ENABLED", True))
        self._ttl_connected_ms = int(getattr(cfg, "WIFI_CONNECTED_TTL_MS", 60000))
        self._ttl_status_ms    = int(getattr(cfg, "WIFI_STATUS_TTL_MS", 8000))
        self._rssi_pub_iv_ms   = int(getattr(cfg, "WIFI_RSSI_PUBLISH_INTERVAL_MS", 15000))
        self._connect_to_ms    = int(getattr(cfg, "WIFI_CONNECT_TIMEOUT_MS", 12000))
        self._dhcp_to_ms       = int(getattr(cfg, "WIFI_DHCP_TIMEOUT_MS", 8000))
        self._backoff_max_ms   = int(getattr(cfg, "WIFI_BACKOFF_MAX_MS", 60000))
        self._ap_fallback      = bool(getattr(cfg, "WIFI_AP_FALLBACK", False))
        self._sleep_wifi       = bool(getattr(cfg, "SLEEP_WIFI", True))

        self._ssid = str(getattr(cfg, "WIFI_SSID", "")) or None
        self._psk  = str(getattr(cfg, "WIFI_PASS", "")) or None

        self._use_dhcp = bool(getattr(cfg, "USE_DHCP", True))
        self._static = (
            getattr(cfg, "STATIC_IP", None),
            getattr(cfg, "NETMASK",   None),
            getattr(cfg, "GATEWAY",   None),
            getattr(cfg, "DNS",       None),
        )

        # Laufzeit
        self.state = WifiState.OFF
        self._wlan = None
        self._want_connect = False
        self._deadline_ms = 0
        self._last_rssi = None
        self._last_rssi_pub = 0
        self._reconnects = 0
        self._backoff_ms = 1000  # exponential backoff start
        self._last_ip = None

        # Diag RL
        self._diag_last_ms = {}
        self._diag_rl_ms = int(getattr(self.cfg, "WIFI_DIAG_RATE_LIMIT_MS", 250))

        # DIM/SLEEP-Flags
        self._dimmed = False          # im DIM?
        self._dim_had_radio = False   # hat DIM das Radio abgeschaltet?

        # Subscription handles (optional)
        self._subs = []

    def start(self):
        self._subscribe()
        self._publish_snapshot(WifiState.OFF)
        if self._enabled:
            self._radio_on()

    def stop(self):
        self._radio_off()
        # optional: unsubscribe all
        try:
            for tok in self._subs:
                try:
                    # token oder (topic,cb) — beides versuchen
                    self._unsubscribe(tok)
                except Exception:
                    pass
        except Exception:
            pass

    # ---------- eventbus helpers ----------
    def _bus_publish(self, topic, payload=None, **kw):
        # ttl_ms etc. als Keywords; robustes Fallback auf Modul
        try:
            self.bus.publish(topic, payload, **kw)
        except TypeError:
            try:
                self.bus.publish(topic, payload)
            except Exception:
                try:
                    bus_mod.publish(topic, payload, **kw)
                except TypeError:
                    try:
                        bus_mod.publish(topic, payload)
                    except Exception:
                        pass
        except Exception:
            try:
                bus_mod.publish(topic, payload, **kw)
            except Exception:
                pass

    def _bind(self, topic, fn):
        # Annahme: Bus ruft (payload) ODER (topic,payload)
        def _wrap(*args, **kw):
            if args:
                # (topic, payload) oder (payload,)
                if len(args) >= 2 and isinstance(args[1], dict):
                    return fn(topic, args[1])
                if isinstance(args[0], dict):
                    return fn(topic, args[0])
            p = kw.get("payload") if isinstance(kw, dict) else None
            if isinstance(p, dict):
                return fn(topic, p)
            return fn(topic, {})
        return _wrap

    def _subscribe(self):
        b = self.bus
        def sub(topic, fn):
            try:
                tok = b.subscribe(topic, self._bind(topic, fn))
            except Exception:
                tok = None
            self._subs.append(tok if tok is not None else {"topic": topic, "cb": fn})
        # Controls
        sub('cmd/wifi',       self._on_cmd)   # unified
        sub("wifi/enable",    self._on_ctrl)
        sub("wifi/disable",   self._on_ctrl)
        sub("wifi/toggle",    self._on_ctrl)
        sub("wifi/reconnect", self._on_ctrl)
        # Power / Display
        for t in ("power/will_dim","display/dim","power/will_sleep",
                  "display/wake","power/active","power/wake","sys/wake"):
            sub(t, self._on_power)

    def _unsubscribe(self, token_or_handle):
        b = self.bus
        try:
            b.unsubscribe(token_or_handle); return
        except Exception:
            pass
        try:
            # v1.2: (topic, cb)
            if isinstance(token_or_handle, dict):
                b.unsubscribe(token_or_handle.get("topic"), token_or_handle.get("cb"))
        except Exception:
            pass

    # ---------- poll step ----------
    def poll(self, now_ms: int):
        # CONNECTING watchdog / completion
        if self.state == WifiState.CONNECTING:
            if self._is_connected():
                ip = self._safe_ip()
                rssi = self._safe_rssi()
                self._last_ip = ip or self._last_ip
                self._set_state(WifiState.CONNECTED, ssid=self._ssid, ip=ip, rssi=rssi, ttl_ms=self._ttl_connected_ms)
                self._backoff_ms = 1000
                self._publish_diag("dhcp_ok" if self._use_dhcp else "link_ok", ip=ip)
            elif ticks_diff(now_ms, self._deadline_ms) >= 0:
                self._publish_diag("connect_timeout")
                self._set_state(WifiState.ERROR, ttl_ms=self._ttl_status_ms)
                self._schedule_backoff(now_ms)

        # CONNECTED: Health + RSSI (ggf. gedrosselt/aus im DIM)
        elif self.state == WifiState.CONNECTED:
            if self._dimmed and not self._sleep_wifi:
                # Policy: Radio bleibt an; keine Reconnects/Scans; RSSI-Publish pausieren.
                # Nur Link-Health minimal prüfen: bei Verlust NICHT auto-reconnecten.
                if not self._is_connected():
                    self._publish_diag("link_lost_dim")
                    # Radio bleibt an (ON), aber kein Connect-Versuch bis WAKE:
                    self._set_state(WifiState.ON, ttl_ms=self._ttl_status_ms)
                    self._want_connect = False
                return
            # Normalbetrieb (nicht dimmed oder Radio aus bei DIM)
            if not self._is_connected():
                self._publish_diag("link_lost")
                self._reconnects += 1
                self._set_state(WifiState.CONNECTING, ttl_ms=self._ttl_status_ms)
                self._start_connect(now_ms)
            else:
                if self._rssi_pub_iv_ms and ticks_diff(now_ms, self._last_rssi_pub) >= self._rssi_pub_iv_ms:
                    self._last_rssi_pub = now_ms
                    rssi = self._safe_rssi()
                    # nur Publish wenn sich RSSI merklich änderte
                    if (rssi is not None) and (self._last_rssi is None or abs(rssi - self._last_rssi) >= 5):
                        self._set_state(WifiState.CONNECTED, ssid=self._ssid, ip=self._safe_ip(), rssi=rssi, ttl_ms=self._ttl_connected_ms)

        # ON/ERROR: ggf. Backoff abwarten und (re)connecten
        elif self.state in (WifiState.ON, WifiState.ERROR):
            if self._enabled and self._want_connect and ticks_diff(now_ms, self._deadline_ms) >= 0:
                # Im DIM + Policy=Radio-an: NICHT neu verbinden
                if self._dimmed and not self._sleep_wifi:
                    return
                self._start_connect(now_ms)

        # AP-Mode (Platzhalter)
        elif self.state == WifiState.AP:
            pass

    # ---------- controls ----------
    def _on_ctrl(self, topic, payload):
        cmd = topic.split("/", 1)[1]
        if cmd == "enable":
            self._enabled = True
            self._dim_had_radio = False   # Toggle bricht DIM-Einschränkung
            self._radio_on()
        elif cmd == "disable":
            self._enabled = False
            self._radio_off()
        elif cmd == "toggle":
            self._enabled = not self._enabled
            self._dim_had_radio = False
            if self._enabled: self._radio_on()
            else: self._radio_off()
        elif cmd == "reconnect":
            self._reconnect(forced=True)

    def _on_cmd(self, topic, payload):
        # Minimal bridge: map {'state': 'on|off|toggle|reconnect'} to legacy control topics
        s = (payload or {}).get('state', '').lower()
        if   s == 'on':        return self._on_ctrl('wifi/enable', payload)
        elif s == 'off':       return self._on_ctrl('wifi/disable', payload)
        elif s == 'toggle':    return self._on_ctrl('wifi/toggle', payload)
        elif s == 'reconnect': return self._on_ctrl('wifi/reconnect', payload)
        # ignore unknown
        return None

    # ---------- power/display ----------
    def _on_power(self, topic, payload):
        # DIM ein (entprellt)
        if topic in ("power/will_dim", "display/dim"):
            if not self._dimmed:
                self._dimmed = True
                if self._sleep_wifi:
                    # Policy: Radio bei DIM aus
                    self._dim_had_radio = (self.state != WifiState.OFF)
                    self._publish_diag("dim_radio_off")
                    self._radio_off()  # pusht status/wifi: off (ttl=0)
                else:
                    # Policy: Radio bleibt an, aber Aktivität drosseln (kein Reconnect, keine RSSI-Publishes)
                    self._publish_diag("dim_radio_on")
            return

        # Voll-Schlaf
        if topic == "power/will_sleep":
            self._publish_diag("sleep_enter")
            self._dimmed = True
            self._dim_had_radio = False
            self._radio_off()  # immer aus – unabhängig von Policy
            return

        # WAKE/ACTIVE
        if topic in ("display/wake", "power/active", "power/wake", "sys/wake"):
            was_dim = self._dimmed
            self._dimmed = False
            self._publish_diag("wake", was_dim=was_dim)
            if self._enabled:
                # Wenn DIM das Radio ausgeschaltet hatte → wieder hochfahren.
                if self._sleep_wifi and self._dim_had_radio:
                    self._radio_on()
                else:
                    # Radio war an; ggf. zurück zu CONNECTING, falls vorher auf ON gefallen
                    if self.state in (WifiState.ON, WifiState.ERROR):
                        self._want_connect = True
                        self._deadline_ms = ticks_ms()
            self._dim_had_radio = False
            return

    # ---------- radio ----------
    def _radio_on(self):
        if not network:
            warn("Wifi: network module not available")
            self._set_state(WifiState.ERROR, ttl_ms=self._ttl_status_ms); return
        try:
            if not self._wlan:
                self._wlan = network.WLAN(network.STA_IF)
            self._wlan.active(True)
            self._set_state(WifiState.ON, ttl_ms=self._ttl_status_ms)
            self._want_connect = True
            self._deadline_ms = ticks_ms()  # sofortiger Versuch in poll()
            self._publish_diag("radio_on")
        except Exception as e:
            warn("Wifi: radio_on failed: %r" % e)
            self._set_state(WifiState.ERROR, ttl_ms=self._ttl_status_ms)

    def _radio_off(self):
        try:
            if self._wlan:
                try:
                    if hasattr(self._wlan, "disconnect"):
                        self._wlan.disconnect()
                except Exception:
                    pass
                self._wlan.active(False)
            self._want_connect = False
            self._set_state(WifiState.OFF, ttl_ms=0)  # sticky OFF (no TTL)
            self._publish_diag("radio_off")
        except Exception as e:
            warn("Wifi: radio_off failed: %r" % e)
            self._set_state(WifiState.ERROR, ttl_ms=self._ttl_status_ms)

    def _reconnect(self, forced=False):
        if not self._wlan:
            return
        try:
            if hasattr(self._wlan, "disconnect"):
                self._wlan.disconnect()
        except Exception:
            pass
        self._reconnects += 1
        self._set_state(WifiState.CONNECTING, ttl_ms=self._ttl_status_ms)
        self._start_connect(ticks_ms(), forced=forced)

    # ---------- connect workflow ----------
    def _start_connect(self, now_ms: int, forced=False):
        if not self._ssid:
            self._publish_diag("no_ssid")
            self._set_state(WifiState.ERROR, ttl_ms=self._ttl_status_ms)
            return
        try:
            # Static IP vor connect
            if not self._use_dhcp and self._static[0]:
                try:
                    self._wlan.ifconfig(self._static)
                except Exception as e:
                    warn("Wifi: ifconfig(static) failed: %r" % e)

            self._wlan.connect(self._ssid, self._psk if self._psk else None)
            self._set_state(WifiState.CONNECTING, ttl_ms=self._ttl_status_ms)
            # watchdog: connect + dhcp Zeit
            self._deadline_ms = now_ms + self._connect_to_ms + (self._dhcp_to_ms if self._use_dhcp else 0)
            self._publish_diag("connecting", ssid=self._ssid)
            self._want_connect = True
        except Exception as e:
            warn("Wifi: connect trigger failed: %r" % e)
            self._set_state(WifiState.ERROR, ttl_ms=self._ttl_status_ms)
            self._schedule_backoff(now_ms)

    def _schedule_backoff(self, now_ms: int):
        self._deadline_ms = now_ms + self._backoff_ms
        self._backoff_ms = min(self._backoff_ms * 2, self._backoff_max_ms)
        self._want_connect = True
        self._publish_diag("reconnect_wait", ms=self._deadline_ms - now_ms)

    # ---------- helpers ----------
    def _is_connected(self) -> bool:
        try:
            return bool(self._wlan and self._wlan.isconnected())
        except Exception:
            return False

    def _safe_ip(self):
        try:
            return self._wlan.ifconfig()[0] if self._wlan else None
        except Exception:
            return None

    def _safe_rssi(self):
        try:
            if hasattr(self._wlan, "status"):
                r = self._wlan.status("rssi")
            else:
                r = None
            if r is None and hasattr(self._wlan, "config"):
                r = self._wlan.config("rssi")
            return int(r) if r is not None else None
        except Exception:
            return None

    # ---------- publish ----------
    def _set_state(self, new_state, *, rssi=None, ssid=None, ip=None, ttl_ms=0):
        changed = (new_state != self.state) or (rssi is not None and rssi != self._last_rssi)                   or (ip is not None and ip != self._last_ip and new_state==WifiState.CONNECTED)
        self.state = new_state
        if rssi is not None:
            self._last_rssi = rssi
        if ip is not None:
            self._last_ip = ip
        if changed:
            self._publish_status(new_state, rssi=rssi, ssid=ssid, ip=ip, ttl_ms=ttl_ms)

    def _publish_snapshot(self, state):
        self._publish_status(state, rssi=None, ssid=None, ip=None, ttl_ms=0)

    def _publish_status(self, state, *, rssi, ssid, ip, ttl_ms):
        msg = {
            "state": state,
            "rssi": rssi,
            "ssid": ssid,
            "ip": ip,
            "ts": int(time.time()),
        }
        # TTL-Policy: connected → connected TTL, sonst Status-Default (falls 0 übergeben wurde)
        ttl = ttl_ms if ttl_ms else (self._ttl_connected_ms if state == WifiState.CONNECTED else self._ttl_status_ms)
        self._bus_publish("status/wifi", msg, ttl_ms=ttl)
        debug("WIFI status %s %s", state, msg)

    def _publish_diag(self, event, **detail):
        now = ticks_ms()
        last = self._diag_last_ms.get(event, 0)
        if ticks_diff(now, last) < self._diag_rl_ms:
            return
        self._diag_last_ms[event] = now
        payload = {"event": event, "detail": detail, "ts": int(time.time())}
        self._bus_publish("diag/wifi", payload)
        debug("WIFI diag %s %s", event, detail)
