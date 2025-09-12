# gold_classic_analog — v0.6.6
# - set_icon() blitted sofort in den Icon-Rect (kein Defer)
# - Wi‑Fi-Mapping: connected/ap→"connected", off/disabled→"off", sonst→"connecting"
# - Render-Order: Minute-Clear → Stunde → Minute (kein Verschwinden der Stunde)
try:
    import uos as _os
except Exception:
    import os as _os

W, H = 240, 240
NAME = "gold_classic_analog"
DRAWS_FULL_BG = True
DEBUG = False

def _join(a, b):
    if not a: return b
    if a.endswith("/"):
        return a + b
    return a + "/" + b

def _read(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except Exception:
        return None

def _rgb565(r, g, b):
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)

ICON_RECTS = {
    "wifi":   (10,   5, 20, 20),
    "battery":(206,  5, 20, 20),
    "charge": (186,  5, 16, 20),
    "bt":     (  8, 212, 20, 20),
    "msg":    (212, 212, 20, 20),
}

class Face:
    def __init__(self, *args, **kwargs):
        self.d = kwargs.get("display")
        self.manager = kwargs.get("manager")
        for a in args:
            if getattr(a, "blit_rgb565", None) and self.d is None:
                self.d = a
            elif getattr(a, "status", None) is not None and self.manager is None:
                self.manager = a
        self.status = kwargs.get("status")
        self.bus    = kwargs.get("bus")

        base = "ui/watchfaces_analog/%s" % NAME
        assets = _join(base, "assets")

        # Hintergrund (RGB565 240x240)
        self.bg = _read(_join(assets, "bg_full.bin"))

        # Icons-Atlas + Meta
        self.icons_meta = None
        meta_raw = _read(_join(assets, "icons_meta.json")) \
                or _read(_join(assets, "icons_manifest.json")) \
                or _read(_join(assets, "icons.json"))
        if meta_raw:
            try:
                import ujson as _json
            except Exception:
                import json as _json
            try:
                meta = _json.loads(meta_raw)
                self.icons_meta = meta.get("icons") or meta.get("items")
            except Exception:
                self.icons_meta = None
        self.atlas = _read(_join(assets, "icons_atlas.bin"))

        # State
        self.icons = {"wifi":"off","bt":"off","battery":"100","charge":"off","msg":"off"}
        self._last_hh = None; self._last_mm = None
        self._bbox_hr = None; self._bbox_min = None

        # Zeiger-Style
        self.cx, self.cy = 120, 120
        self.min_len, self.min_th = 88, 6
        self.hr_len,  self.hr_th  = 60, 8
        self.col_gold   = _rgb565(232, 198, 87)
        self.col_shadow = _rgb565(60, 45, 10)

        # Buffer-Cache
        self._solid_cache = {}

    # -------- small helpers --------
    def request_draw(self):
        try:
            getattr(self.manager, "request_draw", lambda: None)()
        except Exception:
            pass

    def invalidate_region(self, x, y, w, h):
        self.request_draw()

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
        buf = self._solid_cache.get(key)
        if buf: return buf
        b = bytearray(w*h*2)
        hi = (color565 >> 8) & 0xFF; lo = color565 & 0xFF
        for i in range(0, len(b), 2):
            b[i] = hi; b[i+1] = lo
        self._solid_cache[key] = bytes(b)
        return self._solid_cache[key]

    def _fill_rect(self, x, y, w, h, color565):
        if w<=0 or h<=0: return
        try:
            self.d.blit_rgb565(x, y, w, h, self._solid_buf(w, h, color565))
        except Exception:
            pass

    # -------- icons --------
    def _atlas_draw(self, group, state):
        if not (self.icons_meta and self.atlas): return
        rect = ICON_RECTS.get(group)
        if not rect: return
        x, y, w, h = rect
        cand = None
        for it in self.icons_meta:
            if it.get("group")==group and it.get("state")==state:
                cand = it; break
        if cand is None:
            return
        off = int(cand.get("offset", 0)); ln = int(cand.get("length", 0))
        tw  = int(cand.get("w", w));      th = int(cand.get("h", h))
        try:
            view = memoryview(self.atlas)[off:off+ln]
            self.d.blit_rgb565(x, y, tw, th, view)
        except Exception:
            pass

    def set_icon(self, group, tag):
        self.icons[group] = tag
        x,y,w,h = ICON_RECTS.get(group, (0,0,0,0))
        if not w or not h: return
        if tag == "off" or tag is None:
            buf = self._bg_slice(x,y,w,h)
            if buf: self.d.blit_rgb565(x,y,w,h,buf)
        else:
            self._atlas_draw(group, tag)
        self.request_draw()

    # Domain-Mappings
    def on_wifi(self, payload=None):
        s = (payload or {}).get("state", "off")
        if s in ("connected","ap"):
            tag = "connected"
        elif s in ("off","disabled"):
            tag = "off"
        else:
            tag = "connecting"
        self.set_icon("wifi", tag)

    def on_bt(self, payload=None):
        s = (payload or {}).get("state", "off")
        if s == "connected": tag = "connected"
        elif s in ("on","pairing","connecting"): tag = "on"
        elif s == "error": tag = "error"
        else: tag = "off"
        self.set_icon("bt", tag)

    def on_battery(self, payload=None):
        p = 0; charging = False
        try: p = int((payload or {}).get("percent", 0) or 0)
        except Exception: p = 0
        try: charging = bool((payload or {}).get("charging") or (payload or {}).get("state") in ("on","charge"))
        except Exception: charging = False
        lvl = "100" if p>=90 else "80" if p>=70 else "60" if p>=50 else "40" if p>=30 else "20" if p>=10 else "0"
        self.set_icon("battery", lvl)
        self.set_icon("charge", "on" if charging else "off")

    def on_charge(self, payload=None):
        s = (payload or {}).get("state", "off")
        self.set_icon("charge", "on" if s=="on" else "off")

    def on_notif(self, payload=None):
        cnt = 0; flg = 0
        try:
            if isinstance(payload, dict):
                if "count" in payload: cnt = int(payload.get("count") or 0)
                if "flag"  in payload: flg = int(payload.get("flag")  or 0)
        except Exception: pass
        self.set_icon("msg", "on" if (cnt>0 or flg==1) else "off")

    # -------- hands --------
    def _bbox(self, angle_deg, length, thickness):
        r = length + thickness + 2
        x = int(self.cx - r); y = int(self.cy - r)
        w = int(2*r); h = int(2*r)
        if x<0: w+=x; x=0
        if y<0: h+=y; y=0
        if x+w>W: w=W-x
        if y+h>H: h=H-y
        return x,y,w,h

    def _draw_capsule_core(self, x0,y0,x1,y1,thick,color,clip,taper_px):
        dx=x1-x0; dy=y1-y0
        ax=dx if dx>=0 else -dx; ay=dy if dy>=0 else -dy
        r0=thick//2;  r0 = r0 if r0>=1 else 1
        L=ax if ax>=ay else ay
        if taper_px>L: taper_px=L
        if clip: bx,by,bw,bh=clip
        else: bx,by,bw,bh=self._bbox(0, max(ax,ay), thick)
        if ax>=ay:
            step_y=1 if dy>=0 else -1; err=0; y=y0; sx=1 if dx>=0 else -1
            for i in range(0, ax+1):
                x=x0+i*sx
                r = r0 - ((i-(ax-taper_px))*(r0-1)//(taper_px if taper_px else 1)) if i>ax-taper_px else r0
                rx=x; ry=y-r; rw=1; rh=2*r+1
                if not (rx+rw<=bx or ry+rh<=by or rx>=bx+bw or ry>=by+bh):
                    cx1=rx if rx>bx else bx; cy1=ry if ry>by else by
                    cw=(rx+rw if rx+rw<bx+bw else bx+bw)-cx1
                    ch=(ry+rh if ry+rh<by+bh else by+bh)-cy1
                    if cw>0 and ch>0: self._fill_rect(cx1,cy1,cw,ch,color)
                err+=ay
                if (err<<1)>=ax: y+=step_y; err-=ax
        else:
            step_x=1 if dx>=0 else -1; err=0; x=x0; sy=1 if dy>=0 else -1
            for i in range(0, ay+1):
                y=y0+i*sy
                r = r0 - ((i-(ay-taper_px))*(r0-1)//(taper_px if taper_px else 1)) if i>ay-taper_px else r0
                rx=x-r; ry=y; rw=2*r+1; rh=1
                if not (rx+rw<=bx or ry+rh<=by or rx>=bx+bw or ry>=by+bh):
                    cx1=rx if rx>bx else bx; cy1=ry if ry>by else by
                    cw=(rx+rw if rx+rw<bx+bw else bx+bw)-cx1
                    ch=(ry+rh if ry+rh<by+bh else by+bh)-cy1
                    if cw>0 and ch>0: self._fill_rect(cx1,cy1,cw,ch,color)
                err+=ax
                if (err<<1)>=ay: x+=step_x; err-=ay

    def _draw_capsule_line(self, x0,y0,x1,y1,thick,color,clip=None,taper_px=10):
        self._draw_capsule_core(x0,y0,x1,y1,thick+2,self.col_shadow,clip,taper_px)
        self._draw_capsule_core(x0,y0,x1,y1,thick,  color,         clip,taper_px)

    def _erase_bbox(self, bbox):
        if not bbox: return
        x,y,w,h = bbox
        buf = self._bg_slice(x,y,w,h)
        if buf: self.d.blit_rgb565(x,y,w,h,buf)

    def _draw_hour(self, hh, mm, clip=None):
        import math
        ang = ((hh%12)/12.0)*360.0 + (mm/60.0)*30.0 - 90.0
        ex = self.cx + int(math.cos(ang*math.pi/180.0)*self.hr_len)
        ey = self.cy + int(math.sin(ang*math.pi/180.0)*self.hr_len)
        bbox = clip if clip else self._bbox(ang, self.hr_len, self.hr_th)
        self._draw_capsule_line(self.cx,self.cy,ex,ey,self.hr_th,self.col_gold,bbox,8)
        return bbox

    def _draw_minute(self, hh, mm, clip=None):
        import math
        ang = (mm/60.0)*360.0 - 90.0
        ex = self.cx + int(math.cos(ang*math.pi/180.0)*self.min_len)
        ey = self.cy + int(math.sin(ang*math.pi/180.0)*self.min_len)
        bbox = clip if clip else self._bbox(ang, self.min_len, self.min_th)
        self._draw_capsule_line(self.cx,self.cy,ex,ey,self.min_th,self.col_gold,bbox,12)
        return bbox

    # -------- render API --------
    def render_full(self, hh, mm, ss):
        if self.bg:
            self.d.blit_rgb565(0,0,W,H,self.bg)
        # Icons nach aktuellem State
        for g,tag in self.icons.items():
            if tag and tag!="off":
                self._atlas_draw(g, tag)
        # Zeiger
        self._bbox_hr  = self._draw_hour(hh, mm)
        self._bbox_min = self._draw_minute(hh, mm)
        self._last_hh, self._last_mm = hh, mm

    def render(self, hh, mm, ss):
        if self._last_mm is None:
            self.render_full(hh, mm, ss); return
        # Minute wechselt → Clear alte Minute, dann Stunde + Minute neu
        if mm != self._last_mm:
            self._erase_bbox(self._bbox_min)
            self._bbox_hr  = self._draw_hour(hh, mm)
            self._bbox_min = self._draw_minute(hh, mm)
            self._last_mm = mm; self._last_hh = hh
            return
        # Stunde wechselt (selten) → beide neu, damit Reihenfolge stimmt
        if hh != self._last_hh:
            self._erase_bbox(self._bbox_hr)
            self._bbox_hr  = self._draw_hour(hh, mm)
            self._erase_bbox(self._bbox_min)
            self._bbox_min = self._draw_minute(hh, mm)
            self._last_hh = hh; self._last_mm = mm
