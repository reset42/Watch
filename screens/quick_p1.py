# screens/quick_p1.py — Quick Panel 1 (no optimistic flip)
# Gold if radio is ON-ish (on/search/connecting/connected/ap); Grey if off/error.
# 240x240; buttons 110x110 laid out with 5/10px gutters.

import json

try:
    from core import eventbus as bus
except Exception:
    class _Bus:
        def publish(self, *a, **k): pass
        def subscribe(self, *a, **k): pass
        def unsubscribe(self, *a, **k): pass
    bus = _Bus()

class QuickP1:
    SCREEN_ID = "quick_p1"

    RECTS = {
        "wifi": (5,   5,   110,110),
        "bt":   (125, 5,   110,110),
        "lora": (5,   125, 110,110),
        "mqtt": (125, 125, 110,110),
    }

    META = "/ui/themes/standard/quick_p1/quick_p1_meta.json"
    BIN  = "/ui/themes/standard/quick_p1/quick_p1_atlas.bin"

    def __init__(self, d=None, manager=None, **kw):
        self.d = d
        self.manager = manager
        self.meta = None
        self.bin = None
        self._atlas_w = 0
        self.state = {"wifi": False, "bt": False, "lora": False, "mqtt": False}
        self._subbed = False

    # --- FS helpers
    def _open(self, path, mode="rb"):
        fs = getattr(self.manager, "fs", None)
        if fs and hasattr(fs, "open"):
            return fs.open(path, mode)
        return open(path, mode)

    def _load_assets(self):
        if self.meta is None:
            with self._open(self.META, "r") as f:
                self.meta = json.loads(f.read())
            self._atlas_w = int(self.meta.get("atlas", {}).get("w") or 0)
        if self.bin is None:
            with self._open(self.BIN, "rb") as f:
                self.bin = f.read()

    # --- Drawing
    def _draw_sprite(self, group, is_on, x, y):
        g = self.meta["groups"].get(group, {})
        sp = g.get("on" if is_on else "off")
        if not sp:
            return
        off = sp["offset"]; w = sp["w"]; h = sp["h"]
        stride_bytes = self._atlas_w * 2
        row_bytes = w * 2
        mv = memoryview(self.bin)
        for yy in range(h):
            row_start = off + yy * stride_bytes
            self.d.blit_rgb565(x, y + yy, w, 1, mv[row_start: row_start + row_bytes])

    def _fill_black(self):
        try:
            for y in range(0, 240, 16):
                self.d.fill_rect(0, y, 240, min(16, 240 - y), 0x0000)
        except Exception:
            pass

    # --- State mapping
    @staticmethod
    def _active_from_state(payload):
        s = (payload or {}).get("state")
        if not isinstance(s, str):
            return False
        s = s.lower()
        return s in ("on", "search", "connecting", "connected", "ap")

    # --- Render
    def _render_all(self):
        self._fill_black()
        for name, (x, y, w, h) in self.RECTS.items():
            self._draw_sprite(name, self.state[name], x, y)

    # --- Lifecycle
    
    def on_show(self):
        self._load_assets()

        # 1) subscribe first so we don't miss late events
        if not self._subbed:
            bus.subscribe("status/wifi", self.on_wifi)
            bus.subscribe("status/bt",   self.on_bt)
            bus.subscribe("status/lora", self.on_lora)
            bus.subscribe("status/mqtt", self.on_mqtt)
            self._subbed = True

        # 2) prime local state from StatusStore (fresh_only=False)
        s = getattr(self.manager, "status", None)
        if s:
            self.state["wifi"] = self._active_from_state(s.get("status/wifi", fresh_only=False) or {})
            self.state["bt"]   = self._active_from_state(s.get("status/bt",   fresh_only=False) or {})
            self.state["lora"] = self._active_from_state(s.get("status/lora", fresh_only=False) or {})
            self.state["mqtt"] = self._active_from_state(s.get("status/mqtt", fresh_only=False) or {})

        # 3) clear + full render so visuals always reflect current state on re-entry
        self._fill_black()
        self._render_all()


    def on_hide(self, *args, **kwargs):
        if self._subbed:
            bus.unsubscribe("status/wifi", self.on_wifi)
            bus.unsubscribe("status/bt",   self.on_bt)
            bus.unsubscribe("status/lora", self.on_lora)
            bus.unsubscribe("status/mqtt", self.on_mqtt)
            self._subbed = False

    # --- Status handlers (only redraw on change)
    def _apply_and_draw(self, name, payload):
        st = self._active_from_state(payload)
        if st != self.state[name]:
            self.state[name] = st
            x, y, w, h = self.RECTS[name]
            self._draw_sprite(name, st, x, y)

    def on_wifi(self, payload): self._apply_and_draw("wifi", payload)
    def on_bt(self, payload):   self._apply_and_draw("bt",   payload)
    def on_lora(self, payload): self._apply_and_draw("lora", payload)
    def on_mqtt(self, payload): self._apply_and_draw("mqtt", payload)

    # --- Input
    def on_touch(self, ev):
        try:
            x = int(ev.get("x")); y = int(ev.get("y"))
        except Exception:
            return False
        for name, (rx, ry, rw, rh) in self.RECTS.items():
            if rx <= x < rx + rw and ry <= y < ry + rh:
                # NO optimistic flip: wait for real status/<name>
                bus.publish("cmd/" + name, {"state": "toggle"})
                return True
        return False

    # Expire stale to off
    def on_tick_1hz(self):
        s = getattr(self.manager, "status", None)
        if not s:
            return
        for name, topic in (("wifi", "status/wifi"),
                            ("bt",   "status/bt"),
                            ("lora", "status/lora"),
                            ("mqtt", "status/mqtt")):
            val = s.get(topic, fresh_only=True)
            if val is None and self.state[name] is True:
                self.state[name] = False
                x, y, w, h = self.RECTS[name]
                self._draw_sprite(name, False, x, y)

def create(d=None, manager=None, **kw):
    return QuickP1(d=d, manager=manager, **kw)
