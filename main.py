# main.py – RTC-Sync (PCF8563), vchain-Navigation, EventBus-only Backlight/Sleep
# Patched v0.9.4:
# - Manager erzeugung verwendet jetzt das modul-level eventbus (gewrappt von StatusStore)
# - Dadurch akzeptieren alle publishes ttl_ms und keine TypeError mehr
# - LoRa-Init delayed und fehlertolerant wie zuvor

import sys, time
for p in ("/", "/screens", "/lib", "/core"):
    if p not in sys.path:
        sys.path.append(p)

from core.logger import info as log_info, warn as log_warn, set_level
import config
from lib import display_st7789
from core.power_manager import PowerManager
from touch import Touch
# >>> WICHTIG: wir importieren das modul-level eventbus und binden es später
import core.eventbus as eventbus_mod
from core.nav import Nav, load_screens
from core.screen_manager import ScreenManager

# --- WIFI: Neu
try:
    from core.wifi_manager import WifiManager
except Exception:
    WifiManager = None
# --- WIFI: Ende

# --- BT: Neu
try:
    from core.bt_manager import BtManager
except Exception:
    BtManager = None
# --- BT: Ende

# --- LORA: Import, Instanz deferred (siehe weiter unten) ---
try:
    from core.lora_manager import LoraManager
except Exception:
    LoraManager = None
# --- LORA: Ende

try:
    from lib.rtc_pcf8563 import PCF8563
except Exception:
    PCF8563 = None
try:
    import machine
except Exception:
    machine = None


def _sync_time_from_pcf():
    if not PCF8563 or not machine:
        return
    rtc = machine.RTC()
    try:
        sys_now = time.localtime()
        pcf = PCF8563()
        pcf_dt = pcf.datetime()
        if sys_now[0] < 2023:
            rtc.datetime(pcf_dt)
            log_info("RTC sync: system <- PCF8563 %r" % (pcf_dt,))
            return
        try:
            import utime as _u
        except ImportError:
            import time as _u

        def _to_secs(dt, is_sys):
            if is_sys: y,m,d,H,M,S = dt[:6]
            else:      y,m,d,wd,H,M,S,_sub = dt
            return _u.mktime((y,m,d,H,M,S,0,0))

        try:
            drift = abs(_to_secs(sys_now, True) - _to_secs(pcf_dt, False))
        except Exception:
            drift = 999999

        if drift > 120:
            y,m,d,H,M,S,wd,_yd = sys_now
            pcf.set_datetime((y,m,d,wd,H,M,S))
            log_info("RTC sync: PCF8563 <- system (drift=%ss)" % drift)
        else:
            log_info("RTC sync: ok (drift<=120s)")
    except Exception as e:
        log_warn("RTC sync failed: %r" % e)


def boot():
    # --- Logging-Level setzen ---
    try:
        set_level(int(getattr(config, "LOG_LEVEL", 20)))
    except Exception:
        pass

    # --- Zeit via RTC synchronisieren ---
    _sync_time_from_pcf()

    # --- EIN BUS FÜR ALLES: Instanz erzeugen und ins Modul spiegeln ---
    from core.eventbus import EventBus
    bus = EventBus()

    # vorläufige Modulbindung (wird nach StatusStore.attach erneut gesetzt)
    eventbus_mod.publish = bus.publish
    eventbus_mod.subscribe = bus.subscribe

    # --- StatusStore (LKG) an denselben Bus hängen ---
    status_store = None
    try:
        from core.status import StatusStore
        status_store = StatusStore(
            ttl_defaults={
                "status/wifi": 8000,
                "status/bt": 8000,
                "status/battery": 30000,
                "status/usb": 30000,
                "status/notifications": 15000,
                "time/min": 65000,
                "status/brightness": None,
                "status/dim": None,
                "status/mode": None,
                "status/phone": None,
            },
            prefix_whitelist=("status/", "time/"),
        )
        status_store.attach(bus)

        # WICHTIG: Nach Attach setzen wir die modul-level bindings *erneut*
        # sodass eventbus_mod.publish jetzt auf die gewrappten Funktionen zeigt
        eventbus_mod.publish   = bus.publish
        eventbus_mod.subscribe = bus.subscribe
        try:
            eventbus_mod.unsubscribe = bus.unsubscribe
        except Exception:
            pass
    except Exception as e:
        log_warn("StatusStore init failed: %r", e)

    # --- Display ---
    disp = display_st7789.create_display(power_on=True, rotation=0)

    # --- PowerManager (Treiber intern) ---
    pm = PowerManager(display=disp, pwr=None, cfg=config, on_wake=None, touch=None)

    # --- Battery/USB: einmaliger Sofort-Snapshot (vor Screens) ----------------
    try:
        st = pm.pwr.read_status(False) if hasattr(pm, "pwr") and pm.pwr else None
        if st:
            charging = (st.get("charge_direction") == "charging")
            # verwende modul-level eventbus_mod (gewrappt) für Status Publishes
            eventbus_mod.publish("status/battery", {
                "percent":  st.get("percent"),
                "charging": charging,
                "vbat_mV":  st.get("vbat_mV"),
            })
            usb_state = "charging" if charging else ("on" if st.get("usb_present") else "off")
            eventbus_mod.publish("status/usb", {"state": usb_state})
    except Exception as e:
        log_warn("Initial battery/usb snapshot failed: %r" % e)
    # --------------------------------------------------------------------------

    # --- WIFI: Manager erzeugen & starten (modul-level eventbus verwenden) ---
    wifi = None
    if WifiManager:
        try:
            wifi = WifiManager(eventbus=eventbus_mod, cfg=config, logger=log_info)
            wifi.start()
        except Exception as e:
            log_warn("WifiManager init failed: %r" % e)

    # --- BT: Manager erzeugen & starten (modul-level eventbus verwenden) ---
    bt = None
    if BtManager:
        try:
            bt = BtManager(eventbus=eventbus_mod, cfg=config, logger=log_info)
            bt.start()
        except Exception as e:
            log_warn("BtManager init failed: %r" % e)

    # --- LORA: NOTE: wir verzögern Erzeugung/Start auf AFTER UI is up (siehe weiter unten) ---
    lora = None
    # --- LORA: Ende (keine Start/Init hier!)

    # --- DIM/BRIGHT-Levels laden + clampen ---
    try:
        dim_lvl = int(getattr(config, "BACKLIGHT_DIM", 32))
    except Exception:
        dim_lvl = 32
    if dim_lvl < 0: dim_lvl = 0
    if dim_lvl > 255: dim_lvl = 255

    try:
        bright_lvl = int(getattr(config, "BACKLIGHT_BRIGHT",
                         getattr(config, "BRIGHTNES_DIM", 255)))
    except Exception:
        bright_lvl = 255
    if bright_lvl < 0: bright_lvl = 0
    if bright_lvl > 255: bright_lvl = 255

    # --- Backlight-Backend-Erkennung (Display-only) ---
    _bl_backend = None
    for obj, names in (
        (disp, ("set_backlight","set_brightness","backlight",
                "set_lcd_backlight","set_bl","set_light","brightness","setBrightness")),
    ):
        if obj:
            for meth in names:
                if hasattr(obj, meth):
                    _bl_backend = ("call", obj, meth); break
        if _bl_backend: break
    if not _bl_backend:
        for name in ("bl","backlight","lcd_bl","bl_pwm"):
            pwm_obj = getattr(disp, name, None)
            if pwm_obj:
                if   hasattr(pwm_obj, "duty_u16"): _bl_backend = ("pwm", pwm_obj, "duty_u16")
                elif hasattr(pwm_obj, "duty"):     _bl_backend = ("pwm", pwm_obj, "duty")
                elif hasattr(pwm_obj, "value"):    _bl_backend = ("pwm", pwm_obj, "value")
                if _bl_backend: break

    def _bl_set(level_0_255: int):
        lvl = 0 if level_0_255 < 0 else 255 if level_0_255 > 255 else int(level_0_255)
        if not _bl_backend:
            log_warn("[BL] no backend -> NO-OP"); return False
        kind, obj, meth = _bl_backend
        try:
            if kind == "call":
                getattr(obj, meth)(lvl)
            else:
                if meth == "duty_u16":
                    obj.duty_u16(int(lvl * 257))
                elif meth == "duty":
                    obj.duty(int((lvl * 1023) // 255))
                elif hasattr(obj, "value") and meth == "value":
                    obj.value(1 if lvl >= 128 else 0)
            from core.logger import info as _i; _i(f"[BL] set {lvl} via {kind}.{meth} OK")
            return True
        except Exception as e:
            log_warn(f"[BL] set {lvl} via {kind}.{meth} FAILED: {e!r}")
            return False

    def _panel_sleep(do_sleep: bool):
        if hasattr(disp, "sleep"):
            try:
                disp.sleep(bool(do_sleep))
                from core.logger import info as _i; _i(f"[PANEL] sleep({bool(do_sleep)}) OK")
                return True
            except Exception as e:
                log_warn(f"[PANEL] sleep failed: {e!r}")
        return False

    # Backend-Info + kurzer Self-Test (sichtbarer Pulse)
    if _bl_backend:
        kind, _obj, meth = _bl_backend
        log_info(f"BL backend: {kind}.{meth}")
    else:
        log_warn("BL backend: NONE")
    _panel_sleep(False)
    _bl_set(bright_lvl)
    try:
        _bl_set(max(8, dim_lvl)); time.sleep_ms(120); _bl_set(bright_lvl)
    except Exception:
        pass

    # --- Zentrale Handler-Logik (Topic kommt jetzt aus dem Wrapper) ---
    def _handle(topic: str, payload=None):
        try:
            from core.logger import debug as _dbg
            _dbg(f"[BL-HANDLER] topic={topic} payload={payload}")
        except Exception:
            pass

        if topic in ("power/active", "display/wake", "sys/wake"):
            _panel_sleep(False)
            _bl_set(bright_lvl)
            if hasattr(pm, "kick"):
                try: pm.kick()
                except Exception: pass

        elif topic in ("power/will_dim", "display/dim"):
            _bl_set(dim_lvl)

        elif topic == "power/will_sleep":
            _bl_set(0)
            _panel_sleep(True)

    def _bind(topic):
        return lambda ev=None, **kw: _handle(topic, ev if ev is not None else kw if kw else None)

    # subscribe using modul-level eventbus (gewrappt)
    eventbus_mod.subscribe("power/active",     _bind("power/active"))
    eventbus_mod.subscribe("power/will_dim",   _bind("power/will_dim"))
    eventbus_mod.subscribe("power/will_sleep", _bind("power/will_sleep"))
    eventbus_mod.subscribe("display/dim",      _bind("display/dim"))
    eventbus_mod.subscribe("display/wake",     _bind("display/wake"))
    eventbus_mod.subscribe("sys/wake",         _bind("sys/wake"))

    # --- Direkte Set-Topics (für Slider/Settings) ---
    def _extract(ev=None, **kw):
        if isinstance(ev, dict): return ev
        if "payload" in kw and isinstance(kw["payload"], dict): return kw["payload"]
        return kw if kw else {}

    def _on_set_brightness(ev=None, **kw):
        nonlocal bright_lvl
        p = _extract(ev, **kw)
        try:
            lvl = int(p.get("level", bright_lvl))
        except Exception:
            return
        if   lvl < 0:   lvl = 0
        elif lvl > 255: lvl = 255
        bright_lvl = lvl
        _bl_set(bright_lvl)

    def _on_set_dim_level(ev=None, **kw):
        nonlocal dim_lvl
        p = _extract(ev, **kw)
        try:
            lvl = int(p.get("level", dim_lvl))
        except Exception:
            return
        if   lvl < 0:   lvl = 0
        elif lvl > 255: lvl = 255
        dim_lvl = lvl
        _bl_set(dim_lvl)

    eventbus_mod.subscribe("display/set_brightness", _on_set_brightness)
    eventbus_mod.subscribe("display/set_dim_level",  _on_set_dim_level)

    # --- Loopback-Probe ---
    eventbus_mod.publish("display/dim",  {"probe": True})
    time.sleep_ms(120)
    eventbus_mod.publish("display/wake", {"probe": True})

    # --- Navigation & Screens ---
    start_id = getattr(config, "START_SCREEN", "clock_digital")
    nav = Nav("/nav.json", start=start_id)

    sm = ScreenManager(nav=nav, touch=Touch(), eventbus=eventbus_mod, pm=pm)
    pm.touch = sm.touch  # IRQ-Clear nach Wake

    # StatusStore für Screens/Watchfaces verfügbar machen
    if 'status_store' in locals() and status_store:
        sm.status = status_store
        try:
            nav.status = status_store  # optional
        except Exception:
            pass

    all_ids = list(nav.all_ids())
    screens = load_screens(disp, sm, all_ids)
    sm.register(screens)

    if nav.start in screens:
        sm.show(nav.start)
    else:
        first_main = nav.main[0] if nav.main else None
        if first_main and first_main in screens:
            sm.show(first_main)
        elif screens:
            sm.show(next(iter(screens.keys())))

    # --- Jetzt: LoRa erst NACH UI/STAGE hochziehen; Konstruktion kann blockieren, also safe try/except
    try:
        time.sleep_ms(120)
    except Exception:
        pass

    if LoraManager:
        try:
            lora = LoraManager(eventbus=eventbus_mod, cfg=config, logger=log_info)
            try:
                lora.start()
            except Exception as e:
                log_warn("LoraManager start failed (nonfatal): %r" % e)
        except Exception as e:
            log_warn("LoraManager init failed (nonfatal): %r" % e)

    # --- TimePublisher ---
    last_h = last_m = last_s = None
    try:
        lt0 = time.localtime()
        eventbus_mod.publish("time/sec", {"hh": lt0[3], "mm": lt0[4], "ss": lt0[5], "ts": lt0})
        eventbus_mod.publish("time/min", {"hh": lt0[3], "mm": lt0[4], "ts": lt0})
        last_h, last_m, last_s = lt0[3], lt0[4], lt0[5]
    except Exception:
        pass

    poll_interval_ms = 200
    last_poll = time.ticks_ms()
    last_batt = time.ticks_ms()
    batt_interval = int(getattr(config, "BATTERY_UPDATE_MS", 15000))

    log_info("Boot done. Enter main loop.")
    while True:
        sm.update()
        pm.service()

        now_ms = time.ticks_ms()

        # --- WIFI: nicht-blockierende State-Maschine ticken lassen
        if wifi:
            try:
                wifi.poll(now_ms)
            except Exception as e:
                log_warn("wifi.poll error: %r" % e)
        # --- WIFI: Ende

        # --- BT: nicht-blockierende State-Maschine ticken lassen
        if bt:
            try:
                bt.poll(now_ms)
            except Exception as e:
                log_warn("bt.poll error: %r", e)
        # --- BT: Ende

        # --- LORA: nicht-blockierende State-Maschine ticken lassen (nur wenn Instanz existiert)
        if lora:
            try:
                lora.poll(now_ms)
            except Exception as e:
                log_warn("lora.poll error: %r" % e)
        # --- LORA: Ende

        if time.ticks_diff(now_ms, last_poll) >= poll_interval_ms:
            last_poll = now_ms
            try:
                lt = time.localtime()
                h, m, s = lt[3], lt[4], lt[5]
                if (s != last_s) or (m != last_m) or (h != last_h):
                    eventbus_mod.publish("time/sec", {"hh": h, "mm": m, "ss": s, "ts": lt})
                    last_s = s
                    if (m != last_m) or (h != last_h):
                        eventbus_mod.publish("time/min", {"hh": h, "mm": m, "ts": lt})
                        last_m = m; last_h = h
            except Exception:
                pass

        # --- Battery/USB periodisch aktualisieren (nur echte Keys, keine Fallbacks)
        if time.ticks_diff(now_ms, last_batt) >= batt_interval:
            last_batt = now_ms
            try:
                st = pm.pwr.read_status(False) if hasattr(pm, "pwr") and pm.pwr else None
                if st:
                    charging = (st.get("charge_direction") == "charging")
                    eventbus_mod.publish("status/battery", {
                        "percent":  st.get("percent"),
                        "charging": charging,
                        "vbat_mV":  st.get("vbat_mV"),
                    })
                    usb_state = "charging" if charging else ("on" if st.get("usb_present") else "off")
                    eventbus_mod.publish("status/usb", {"state": usb_state})
            except Exception as e:
                log_warn("battery/usb poll failed: %r" % e)

        time.sleep_ms(10)


if __name__ == "__main__":
    boot()
