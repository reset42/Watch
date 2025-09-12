try:
    from core.logger import info as log_info, warn as log_warn, debug as log_debug
except Exception:
    def log_info(*a, **k):  pass
    def log_warn(*a, **k):  pass
    def log_debug(*a, **k): pass

_FACE = None

# gold_waves_orbitron v0.5.3 — tighter spacing, DDAY, immediate icons
try:
    import ujson as json
except Exception:
    import json
try:
    import utime as _time
except Exception:
    import time as _time

ASSET_DIR="ui/watchfaces_digital/gold_waves_orbitron/assets"
_BG=None; _BGM=None; _DA=None; _DM=None; _IA=None; _IM=None

def _b(p):
    with open(p,"rb") as f: return f.read()
def _j(p):
    with open(p,"r",encoding="utf-8") as f: return json.loads(f.read())

def _ensure():
    global _BG,_BGM,_DA,_DM,_IA,_IM
    if _BG is None:
        _BG=_b(ASSET_DIR+"/bg_full.bin")
        try: _BGM=memoryview(_BG)
        except: _BGM=None
    if _DA is None or _DM is None:
        _DA=_b(ASSET_DIR+"/digit_atlas.bin"); _DM={}
        for t in _j(ASSET_DIR+"/digit_meta.json").get("tiles",[]):
            _DM[(t.get("slot"), str(t.get("char")))]=(t["x"],t["y"],t["w"],t["h"],t["offset"],t["length"])
    if _IA is None or _IM is None:
        _IA=_b(ASSET_DIR+"/icons_atlas.bin"); _IM={}
        for t in _j(ASSET_DIR+"/icons_meta.json").get("icons",[]):
            _IM[(t.get("group"), str(t.get("state")))]=(t["x"],t["y"],t["w"],t["h"],t["offset"],t["length"])

class Face:

    WANTS_SECONDS = True
    ICON_RECTS = {
        "wifi": (10, 5, 20, 20),
        "bt": (34, 5, 20, 20),
        "msg": (58, 5, 20, 20),
        "lora": (82, 5, 20, 20),
        "charge": (185, 5, 20, 20),
        "battery": (209, 5, 26, 20),
    }

    def _icon_state_init(self):
        if getattr(self, "_icon_state", None) is None:
            self._icon_state = {}

    def _update_icon(self, group, tag):
        self._icon_state_init()
        prev = self._icon_state.get(group)
        if prev == tag:
            return
        self._icon_state[group] = tag
        try:
            self.set_icon(group, tag)
        except Exception:
            pass
        x, y, w, h = self.ICON_RECTS.get(group, (0, 0, 0, 0))
        if w and h:
            try: self.invalidate_region(x, y, w, h)
            except Exception: pass
        try: self.request_draw()
        except Exception:
            try: self.render()
            except Exception:
                try: self.render_full()
                except Exception: pass

    def _restore_icons_after_full_draw(self):
        st = getattr(self, "_icon_state", None) or {}
        if not st:
            return
        for g, t in st.items():
            try:
                self.set_icon(g, t)
            except Exception:
                pass

    def on_bt(self, payload=None):
        s = (payload or {}).get("state")
        if   s == "connected":
            tag = "connected"
        elif s in ("on", "pairing", "connecting"):
            tag = "on"
        elif s == "error":
            tag = "error"
        else:
            tag = "off"
        self._update_icon("bt", tag)

    def on_wifi(self, payload=None):
        s = (payload or {}).get("state")
        if   s == "error":
            tag = "error"
        elif s in ("on", "connected", "ap"):
            tag = "connected"
        elif s in ("search", "connecting"):
            tag = "connecting"
        else:
            tag = "off"
        self._update_icon("wifi", tag)

    def on_notif(self, payload=None):
        flag = 0
        try:
            if isinstance(payload, dict):
                if "flag" in payload:
                    flag = int(payload.get("flag") or 0)
                elif "count" in payload:
                    flag = 1 if int(payload.get("count") or 0) > 0 else 0
        except Exception:
            flag = 0
        self._update_icon("msg", "on" if flag else "off")

    def on_battery(self, payload=None):
        p = 0; charging = False
        try: p = int((payload or {}).get("percent", 0) or 0)
        except Exception: p = 0
        charging = bool((payload or {}).get("charging", False))
        self._update_icon("charge", "on" if charging else "off")
        if   p <= 10:   lvl = "0"
        elif p <= 30:   lvl = "20"
        elif p <= 50:   lvl = "40"
        elif p <= 70:   lvl = "60"
        elif p <= 90:   lvl = "80"
        else:           lvl = "100"
        self._update_icon("battery", lvl)

    def invalidate(self):
        pass


    NAME="gold_waves_orbitron"
    DRAWS_FULL_BG=True
    WANTS_SECONDS=True
    def __init__(self, d):
        _ensure(); self.d=d
        self.last={"H1":"","H2":"","M1":"","M2":"","SS":"","WD":"","DDAY":"","MON":"","YEAR":""}
        self.icons={"wifi":"off","bt":"off","lora":"off","msg":"off","charge":"off","battery":"100"}
    def _blit(self,buf,x,y,w,h): self.d.blit_rgb565(x,y,w,h,buf)
    def _bg(self,x,y,w,h):
        stride=240*2; mv=_BGM or _BG
        for r in range(h):
            off=(y+r)*stride + x*2
            self._blit(mv[off:off+w*2], x,y+r,w,1)
    def _tile(self,slot,key): return _DM.get((slot,str(key)))
    def _icon(self,grp,st): return _IM.get((grp,str(st)))
    def _draw_slot(self,slot,key):
        t=self._tile(slot,key); 
        if not t: return
        x,y,w,h,off,ln=t
        self._blit(_DA[off:off+ln], x,y,w,h)
    def _draw_icon(self,grp,st):
        t=self._icon(grp,st); 
        if not t: return
        x,y,w,h,off,ln=t
        self._blit(_IA[off:off+ln], x,y,w,h)
    def on_show(self):
        self._bg(0,0,240,240); t=_time.localtime(); self.render_full(t[3],t[4],t[5])
        # Repaint icons with whatever state we currently hold
        for g,s in self.icons.items():
            self._draw_icon(g,s)
    def render_full(self,hh,mm,ss=None):
        self._bg(0,0,240,240); self._draw_middle(force=True)
        sHH="{:02d}".format(int(hh)); sMM="{:02d}".format(int(mm))
        for sl,ch in (("H1",sHH[0]),("H2",sHH[1]),("M1",sMM[0]),("M2",sMM[1])):
            self._draw_slot(sl,ch); self.last[sl]=ch
        if ss is None: ss=_time.localtime()[5]
        self._draw_seconds(ss)
        for g,s in self.icons.items(): self._draw_icon(g,s)
        try: self._restore_icons_after_full_draw()
        except Exception: pass
    def render(self,hh,mm,ss=None):
        sHH="{:02d}".format(int(hh)); sMM="{:02d}".format(int(mm))
        if not self.last["WD"] or not self.last["DDAY"] or not self.last["MON"] or not self.last["YEAR"]:
            self._draw_middle(force=True)
        if self.last["M2"]!=sMM[1]: self._draw_slot("M2",sMM[1]); self.last["M2"]=sMM[1]
        if self.last["M1"]!=sMM[0]: self._draw_slot("M1",sMM[0]); self.last["M1"]=sMM[0]
        if sMM=="00" and self.last["H2"]!=sHH[1]: self._draw_slot("H2",sHH[1]); self.last["H2"]=sHH[1]
        if sMM=="00" and self.last["H1"]!=sHH[0]: self._draw_slot("H1",sHH[0]); self.last["H1"]=sHH[0]
        if ss is not None: self._draw_seconds(ss)
        if sHH=="00" and sMM=="00": self._draw_middle(force=True)
    def render_seconds(self,hh,mm,ss): self._draw_seconds(ss)
    def _draw_seconds(self,ss):
        key="{:02d}".format(int(ss))
        if self.last["SS"]==key: return
        self._draw_slot("SS",key); self.last["SS"]=key
    def _draw_middle(self, force=False):
        lt=_time.localtime()
        wd=str((lt[6])%7)
        if force or self.last["WD"]!=wd: self._draw_slot("WD",wd); self.last["WD"]=wd
        day="{:02d}".format(lt[2])
        if force or self.last["DDAY"]!=day: self._draw_slot("DDAY",day); self.last["DDAY"]=day
        mons=["Jan","Feb","Mär","Apr","Mai","Jun","Jul","Aug","Sep","Okt","Nov","Dez"]
        mon=mons[max(0,min(11,lt[1]-1))]
        if force or self.last["MON"]!=mon: self._draw_slot("MON",mon); self.last["MON"]=mon
        year="{:04d}".format(lt[0])
        if force or self.last["YEAR"]!=year: self._draw_slot("YEAR",year); self.last["YEAR"]=year
    # icons API (immediate draw)
    def set_icon(self,grp,st):
        st=str(st)
        if self.icons.get(grp)!=st:
            self.icons[grp]=st
        self._draw_icon(grp,st)
    def on_lora(self,p):
        s=(p or {}).get("state","off")
        self.set_icon("lora","on" if s!="off" else "off")
    def on_notif(self,p): self.set_icon("msg","on" if (p or {}).get("flag",0) else "off")
    def on_usb(self,p): self.set_icon("charge","on" if (p or {}).get("state")=="charging" else "off")
_FACE=None
def on_show(display,*a,**k):
    global _FACE; _ensure(); _FACE=Face(display); _FACE.on_show(); return _FACE
def render_full(hh,mm,ss=None):
    if _FACE: _FACE.render_full(hh,mm,ss)
def render(hh,mm,ss=None):
    if _FACE: _FACE.render(hh,mm,ss)


def on_bt(p):
    f = globals().get('_FACE')
    return f.on_bt(p) if f else None


def on_wifi(p):
    f = globals().get('_FACE')
    return f.on_wifi(p) if f else None


def on_battery(p):
    f = globals().get('_FACE')
    return f.on_battery(p) if f else None


def on_charge(p):
    f = globals().get('_FACE')
    return f.on_charge(p) if f else None
