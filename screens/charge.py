# screens/charge.py — v0.2.4
import sys, time, config

try:
    from core.logger import info as log_info, warn as log_warn, debug as log_debug
except Exception:
    def log_info(*a, **k):  pass
    def log_warn(*a, **k):  pass
    def log_debug(*a, **k): pass

class _BusAdapter:
    def __init__(self, b): self._b=b
    def subscribe(self, topic, cb):
        b=self._b; tok=None
        try:
            if hasattr(b,'subscribe') and callable(b.subscribe):
                tok=b.subscribe(topic, cb)
            else:
                import types
                if isinstance(b,types.ModuleType) and hasattr(b,'subscribe'):
                    tok=b.subscribe(topic, cb)
        except Exception as e:
            try: log_warn('Bus subscribe failed %s: %r', topic, e)
            except Exception: pass
        return {'topic':topic,'cb':cb,'token':tok}
    def unsubscribe(self, h):
        if not h: return
        b=self._b
        topic=h.get('topic') if isinstance(h,dict) else None
        cb   =h.get('cb') if isinstance(h,dict) else None
        token=h.get('token') if isinstance(h,dict) else h
        try:
            if hasattr(b,'unsubscribe') and callable(b.unsubscribe):
                try: b.unsubscribe(token); return
                except TypeError: pass
            import types
            if isinstance(b,types.ModuleType) and hasattr(b,'unsubscribe'):
                try: b.unsubscribe(token); return
                except TypeError: pass
        except Exception: pass
        try:
            if hasattr(b,'unsubscribe') and callable(b.unsubscribe) and topic and cb:
                b.unsubscribe(topic, cb); return
        except Exception: pass
        try:
            import types
            if isinstance(b,types.ModuleType) and hasattr(b,'unsubscribe') and topic and cb:
                b.unsubscribe(topic, cb)
        except Exception: pass

class ScreenCharge:
    SCREEN_ID='charge'
    KEEP_AWAKE=False

    def __init__(self, d=None, manager=None, eventbus=None, **kw):
        self.d=d; self.manager=manager
        self.eb=self._resolve_bus(eventbus, manager)
        self.face=None; self._visible=False
        self._tok={'batt':None,'usb':None,'scr':None,'redraw':None}
        if self.eb:
            def _cb_scr(*a, **k):
                tgt=None
                if a and isinstance(a[0], str): tgt=a[0]
                elif a and isinstance(a[0], dict): tgt=a[0].get('id') or a[0].get('to') or a[0].get('screen')
                elif 'payload' in k and isinstance(k['payload'], dict):
                    p=k['payload']; tgt=p.get('id') or p.get('to') or p.get('screen')
                if tgt=='charge': self._prime()
            try: self._tok['scr']=self.eb.subscribe('screen/changed', _cb_scr)
            except Exception: pass
            try: self._tok['redraw']=self.eb.subscribe('sys/redraw_full', lambda *_a, **_k: self._on_redraw())
            except Exception: pass

    def _resolve_bus(self, eventbus, manager):
        cand = eventbus or getattr(manager,'eventbus',None) or getattr(manager,'bus',None)
        if not cand:
            try:
                import core.eventbus as eb_mod; cand=eb_mod
            except Exception: cand=None
        return _BusAdapter(cand) if cand else None

    def _status_store(self):
        for getter in (
            lambda: getattr(self.manager,'status',None),
            lambda: getattr(getattr(self.manager,'nav',None),'status',None),
            lambda: getattr(self,'status',None),
            lambda: getattr(self.face,'status',None),
        ):
            try:
                s=getter()
                if s: return s
            except Exception: pass
        return None

    def _load_face(self):
        pkg='ui.charge_face.gold_black.main'
        try:
            if getattr(config,'DEV_HOTRELOAD_WF',False) and pkg in sys.modules:
                del sys.modules[pkg]
        except Exception: pass
        try:
            mod=__import__(pkg,None,None,('*',))
        except Exception as e:
            raise ImportError('Charge face konnte nicht geladen werden: %r' % (e,))
        face_cls=None
        for n in dir(mod):
            o=getattr(mod,n)
            if isinstance(o,type) and hasattr(o,'render_full'):
                face_cls=o; break
        if not face_cls: raise ImportError('Face-Klasse nicht gefunden.')
        inst=face_cls(self.d)
        self.face=inst
        try: setattr(self.face,'manager',self.manager)
        except Exception: pass
        try:
            import core.eventbus as eb_mod; setattr(self.face,'bus',eb_mod)
        except Exception:
            try: setattr(self.face,'bus',getattr(self.manager,'eventbus',None))
            except Exception: pass
        try:
            s=self._status_store()
            if s: setattr(self.face,'status',s)
        except Exception: pass

    def on_show(self, *a, **k):
        self._visible=True
        if self.face is None: self._load_face()
        self._prime()
        try: self.face.render_full()
        except Exception as e: log_warn('charge face full render error: %r', e)
        if self.eb:
            def _x(*args, **kw):
                if args:
                    if isinstance(args[-1], dict): return args[-1]
                    if isinstance(args[0], dict):  return args[0]
                if kw:
                    p=kw.get('payload')
                    if isinstance(p, dict): return p
                    return kw if isinstance(kw, dict) else None
                return None
            def _b(*args, **kw): p=_x(*args, **kw) or {}; self._on_battery(p)
            def _u(*args, **kw): p=_x(*args, **kw) or {}; self._on_usb(p)
            self._tok['batt']=self.eb.subscribe('status/battery', _b)
            self._tok['usb'] =self.eb.subscribe('status/usb', _u)

    def _prime(self):
        s=self._status_store()
        if not (s and self.face): return
        try:
            batt=s.get('status/battery', fresh_only=False) or {}
            usb =s.get('status/usb',     fresh_only=False) or {}
        except Exception:
            batt={}; usb={}
        self._on_battery(batt, draw=False)
        self._on_usb(usb, draw=False)

    def on_hide(self, *a, **k):
        self._visible=False
        if self.eb:
            for k,t in list(self._tok.items()):
                if t is not None:
                    try: self.eb.unsubscribe(t)
                    except Exception: pass
                    self._tok[k]=None

    def _on_redraw(self):
        if not self._visible or not self.face: return
        try: self.face.render_full()
        except Exception as e: log_warn('charge face redraw error: %r', e)

    def _on_battery(self, p, draw=True):
        try:
            pct = p.get('percent', None)
            mv  = p.get('vbat_mV', None)  # keine Fallbacks
            chg = p.get('charging', None)
        except Exception:
            pct=mv=chg=None
        try:
            self.face.set_battery(percent=pct, vbat_mv=mv, charging=chg)
            if draw: self.face.render_full()
        except Exception as e: log_warn('charge _on_battery error: %r', e)

    def _on_usb(self, p, draw=True):
        try:
            st = p.get('state', None)  # erwartet: 'charging' | 'data' | 'on' | 'off'
        except Exception:
            st=None
        try:
            self.face.set_usb(state=st)
            if draw: self.face.render_full()
        except Exception as e: log_warn('charge _on_usb error: %r', e)

class Screen(ScreenCharge): pass
