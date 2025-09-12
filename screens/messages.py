# screens/messages.py — placeholder to keep navigation working
import config
SCREEN_ID = "messages"

try:
    from lib.display import WHITE, BLACK
except Exception:
    WHITE = 0xFFFF; BLACK = 0x0000

class ScreenMessages:
    KEEP_AWAKE = False

    def __init__(self, d=None, manager=None, eventbus=None, **kw):
        self.d = d; self.manager = manager
        self.bus = eventbus or (getattr(manager, "eventbus", None) if manager else None)
        self._painted = False
        self._clear_on_open = bool(getattr(config, "NOTIF_CLEAR_ON_OPEN", True))

    def on_show(self):
        if self._clear_on_open and self.bus:
            try:
                self.bus.publish("notif/clear_indicator", {"source": "messages"})
            except Exception:
                pass
        self.render_full()

    def render_full(self):
        d = self.d
        try:
            if hasattr(d, "fill"): d.fill(0)
            elif hasattr(d, "fill_rect"): d.fill_rect(0,0,240,240,0)
        except Exception: pass
        try:
            # best-effort title
            if hasattr(d, "draw_text"):
                d.draw_text(8, 8, "messages")
            elif hasattr(d, "text"):
                try: d.text(8, 8, "messages")
                except TypeError: d.text(8, 8, "messages", WHITE)
        except Exception: pass
        try:
            if hasattr(d, "update"): d.update()
        except Exception: pass
        self._painted = True

    def render(self):
        if not self._painted:
            self.render_full()

    def on_hide(self):
        pass

    def on_swipe(self, direction):
        return False
