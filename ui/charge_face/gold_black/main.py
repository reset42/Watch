
# ui/charge_face/gold_black/main.py — v0.2.6
# Change: Battery fill now grows RIGHT → LEFT.
try:
    import uos as _os
except Exception:
    import os as _os

W, H = 240, 240
NAME = "gold_black"
DRAWS_FULL_BG = True

# Inner fill rect (from mockup)
BAT_INNER    = (54, 87, 134, 67)
USB_RECT     = (198, 100, 40, 40)
BOLT_RECT    = (24,  168, 40, 40)

def _join(a, b):
    if not a: return b
    if a.endswith('/'):
        return a + b
    return a + '/' + b

def _read(path):
    try:
        with open(path, 'rb') as f:
            return f.read()
    except Exception:
        return None

def _rgb565(r, g, b):
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)

GOLD  = _rgb565(232,198,87)
BLACK = 0x0000

ICON_RECTS = {"usb": USB_RECT, "bolt": BOLT_RECT}

class Face:
    def __init__(self, d, *args):
        self.d = d
        self.manager = None
        self.bus = None
        self.status = None

        base = 'ui/charge_face/%s' % NAME
        assets = _join(base, 'assets')
        self.bg    = _read(_join(assets, 'bg_full.bin'))
        self.atlas = _read(_join(assets, 'icons_atlas.bin'))
        meta_raw   = _read(_join(assets, 'icons_meta.json'))
        self.icons_meta = None
        if meta_raw:
            try:
                import ujson as _json
            except Exception:
                import json as _json
            try:
                self.icons_meta = (_json.loads(meta_raw)).get('icons')
            except Exception:
                self.icons_meta = None

        self.glyph_atlas = _read(_join(assets, 'glyph_atlas.bin'))
        self.glyph_meta  = None
        gm_raw = _read(_join(assets, 'glyph_meta.json'))
        if gm_raw:
            try:
                import ujson as _json
            except Exception:
                import json as _json
            try:
                self.glyph_meta = (_json.loads(gm_raw)).get('glyphs')
            except Exception:
                self.glyph_meta = None

        # dynamic state
        self.percent = None
        self.vbat_mv = None
        self.usb_state = None
        self.charging = None

        self._solid_cache = {}
        self._last_fill_w = -1

    # ---- utils ----------------------------------------------------------
    def request_draw(self):
        try:
            getattr(self.manager, 'request_draw', lambda: None)()
        except Exception:
            pass

    def _bg_slice(self, x, y, w, h):
        if not self.bg: return None
        if x < 0: w += x; x = 0
        if y < 0: h += y; y = 0
        if x + w > W: w = W - x
        if y + h > H: h = H - y
        if w <= 0 or h <= 0: return None
        row = W * 2
        out = bytearray(w*h*2)
        for j in range(h):
            src = (y + j) * row + x * 2
            out[j*w*2:(j+1)*w*2] = self.bg[src:src + w*2]
        return bytes(out)

    def _solid_buf(self, w, h, color565):
        key = (w, h, color565)
        b = self._solid_cache.get(key)
        if b: return b
        data = bytearray(w*h*2)
        hi = (color565 >> 8) & 0xFF; lo = color565 & 0xFF
        for i in range(0, len(data), 2):
            data[i] = hi; data[i+1] = lo
        self._solid_cache[key] = bytes(data)
        return self._solid_cache[key]

    def _fill_rect(self, x, y, w, h, color565):
        if w<=0 or h<=0: return
        try:
            self.d.blit_rgb565(x, y, w, h, self._solid_buf(w, h, color565))
        except Exception:
            pass

    # ---- icons ----------------------------------------------------------
    def _atlas_draw(self, group, state='on'):
        if not (self.icons_meta and self.atlas): return
        rect = ICON_RECTS.get(group)
        if not rect: return
        x, y, w, h = rect
        cand = None
        for it in self.icons_meta:
            if it.get('group')==group and it.get('state')==state:
                cand = it; break
        if cand is None: return
        off = int(cand.get('offset',0)); ln=int(cand.get('length',0))
        tw  = int(cand.get('w', w));     th=int(cand.get('h', h))
        try:
            view = memoryview(self.atlas)[off:off+ln]
            self.d.blit_rgb565(x, y, tw, th, view)
        except Exception:
            pass

    def _clear_rect(self, rect):
        x,y,w,h = rect
        buf = self._bg_slice(x,y,w,h)
        if buf: self.d.blit_rgb565(x,y,w,h,buf)

    # ---- glyph rendering ------------------------------------------------
    def _glyph_info(self, ch):
        if not self.glyph_meta: return None
        for g in self.glyph_meta:
            if g.get('ch') == ch:
                return g
        return None

    def _glyph_text_size(self, s):
        w = 0; h = 0
        for ch in s:
            g = self._glyph_info(ch)
            if not g:
                w += 4; h = max(h, 12); continue
            w += int(g.get('w',0)); h = max(h, int(g.get('h',0)))
        return w, h

    def _draw_glyph_text(self, x, y, s):
        if not (self.glyph_atlas and self.glyph_meta): return
        cx = x
        for ch in s:
            g = self._glyph_info(ch)
            if not g:
                cx += 4
                continue
            off = int(g.get('offset',0)); ln = int(g.get('length',0))
            gw  = int(g.get('w',0));      gh = int(g.get('h',0))
            try:
                view = memoryview(self.glyph_atlas)[off:off+ln]
                self.d.blit_rgb565(cx, y, gw, gh, view)
            except Exception:
                pass
            cx += gw

    # ---- setters --------------------------------------------------------
    def set_battery(self, percent=None, vbat_mv=None, charging=None):
        ch=False
        if percent  is not None and percent  != self.percent:  self.percent  = percent;  ch=True
        if vbat_mv  is not None and vbat_mv  != self.vbat_mv:  self.vbat_mv  = vbat_mv;  ch=True
        if charging is not None and charging != self.charging: self.charging = charging; ch=True
        if ch:
            self._draw_percent()
            self._draw_voltage()
            self._draw_usb_and_bolt()
            self._draw_fill(force=False)

    def set_usb(self, state=None):
        if state != self.usb_state:
            self.usb_state = state
            self._draw_usb_and_bolt()

    # ---- layout rects ---------------------------------------------------
    def _percent_rect_centered(self, text):
        bx,by,bw,bh = BAT_INNER
        tw, th = self._glyph_text_size(text)
        x = bx + (bw - tw)//2
        y = by - (th + 16)
        return (x, y, tw, th)

    def _voltage_rect(self, text):
        tw, th = self._glyph_text_size(text)
        x = 120 - tw//2
        y = 176
        return (x, y, tw, th)

    # ---- draws ----------------------------------------------------------
    def _draw_fill(self, force=False):
        """Right-to-left fill inside BAT_INNER."""
        if self.percent is None:
            self._last_fill_w=-1
            self._clear_rect(BAT_INNER)
            return
        bx,by,bw,bh = BAT_INNER
        new_w = int((self.percent if self.percent>0 else 0) * bw / 100)
        if (not force) and new_w == self._last_fill_w:
            return
        self._clear_rect(BAT_INNER)
        if new_w > 0:
            start_x = bx + (bw - new_w)   # right-aligned
            self._fill_rect(start_x, by, new_w, bh, GOLD)
        self._last_fill_w = new_w

    def _draw_percent(self):
        if self.percent is None: return
        s = "{:d}%".format(int(self.percent))
        x,y,w,h = self._percent_rect_centered(s)
        self._clear_rect((x,y,w,h))
        self._draw_glyph_text(x, y, s)

    def _draw_voltage(self):
        if self.vbat_mv is None: return
        s = "{:.2f} V".format(self.vbat_mv/1000.0)
        x,y,w,h = self._voltage_rect(s)
        self._clear_rect((x,y,w,h))
        self._draw_glyph_text(x, y, s)

    def _draw_usb_and_bolt(self):
        self._clear_rect(ICON_RECTS["usb"])
        self._clear_rect(ICON_RECTS["bolt"])
        st = self.usb_state or 'off'
        if st not in ('off','disconnected',None):
            self._atlas_draw('usb','on')
        if (st == 'charging') or bool(self.charging):
            self._atlas_draw('bolt','on')

    def render_full(self, *args):
        if self.bg:
            self.d.blit_rgb565(0,0,W,H,self.bg)
        self._draw_percent()
        self._draw_voltage()
        self._draw_usb_and_bolt()
        self._draw_fill(force=True)
