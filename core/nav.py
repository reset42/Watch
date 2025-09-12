# core/nav.py
import json

# --- Debug/Wrapping-Konfiguration ---
try:
    import config
    _DBG_NAV = bool(getattr(config, "DEBUG_NAV", False))
    WRAP_VCHAIN = bool(getattr(config, "WRAP_VCHAIN", False))  # optionales Wrap am Rand
except Exception:
    _DBG_NAV = False
    WRAP_VCHAIN = False

# --- Logger (nutzt deinen Core-Logger) ---
try:
    from core.logger import debug as log_debug, info as log_info, warn as log_warn, error as log_error
except Exception:
    def log_debug(*a, **k): pass
    def log_info(*a, **k):  pass
    def log_warn(*a, **k):  pass
    def log_error(*a, **k): pass


class Nav:
    """Datengetriebene Navigation (kein Ring) mit konsistenter Vertikalreihenfolge.
       Swipe ↑  = vchain Index +1 (weiter 'nach unten' in der Liste)
       Swipe ↓  = vchain Index -1 (weiter 'nach oben' in der Liste)
    """
    def __init__(self, nav_json_path="/nav.json", start="clock_digital"):
        with open(nav_json_path, "r") as f:
            cfg = json.load(f)

        self.main   = list(cfg.get("main") or [])
        self.sub    = dict(cfg.get("sub") or {})
        self.hidden = dict(cfg.get("hidden") or {})
        self.upmap  = dict(cfg.get("upmap") or {})

        # Caches
        self._parent_cache = {}     # sid -> parent(main) | None
        self._vchain_cache = {}     # parent(main) -> list(chain)

        # Start-ID bestimmen
        self.start = start if start in self.all_ids() else (self.main[0] if self.main else None)

        # Sanity-Checks + optionales Debug-Inventar
        self._validate()
        if _DBG_NAV:
            log_info("NAV.init: main=%d sub.parents=%d hidden.parents=%d upmap=%d start=%s",
                     len(self.main), len(self.sub), len(self.hidden), len(self.upmap), self.start)
            for m in self.main:
                chain = self._vchain_for_parent(m)
                log_debug("NAV.vchain[%s]=%s", m, chain)

    # -------- Hilfen --------
    def all_ids(self):
        ids = set(self.main)
        for chain in self.sub.values():
            if chain:
                ids.update(chain)
        for chain in self.hidden.values():
            if chain:
                ids.update(chain)
        if self.upmap:
            ids.update(self.upmap.values())
        return ids

    def is_main_id(self, sid):
        return sid in self.main

    def _validate(self):
        """Sanity-Checks: verweist sub/upmap/hidden auf unbekannte IDs?"""
        known = self.all_ids()

        # sub: alle referenzierten IDs sollten existieren
        for parent, chain in (self.sub or {}).items():
            if parent not in self.main:
                log_warn("NAV: sub-parent '%s' ist nicht in main", parent)
            for sid in (chain or []):
                if sid not in known:
                    log_warn("NAV: sub '%s' referenziert unbekannte id '%s'", parent, sid)

        # upmap: target muss existieren
        for parent, up_id in (self.upmap or {}).items():
            if parent not in self.main:
                log_warn("NAV: upmap-parent '%s' ist nicht in main", parent)
            if up_id not in known:
                log_warn("NAV: upmap '%s' -> unbekannte id '%s'", parent, up_id)

        # hidden: targets sollten existieren
        for parent, chain in (self.hidden or {}).items():
            if parent not in self.main and parent not in known:
                log_warn("NAV: hidden-parent '%s' ist nicht bekannt", parent)
            for sid in (chain or []):
                if sid not in known:
                    log_warn("NAV: hidden '%s' referenziert unbekannte id '%s'", parent, sid)

        # start prüfen
        if self.start and self.start not in known:
            log_warn("NAV: start id '%s' ist nicht in all_ids()", self.start)

    def _main_parent_of(self, sid):
        """Parent-Hauptpunkt eines Sub- oder Upmap-Screens; None wenn keiner."""
        if sid in self._parent_cache:
            return self._parent_cache[sid]

        parent = None
        # Sub-Kind?
        for main_id, chain in self.sub.items():
            if chain and sid in chain:
                parent = main_id
                break
        # Upmap-Kind?
        if parent is None:
            for main_id, up_id in self.upmap.items():
                if sid == up_id:
                    parent = main_id
                    break

        self._parent_cache[sid] = parent
        return parent

    def parent_of(self, sid):
        """Öffentliche Parent-Suche (nur für Sichtbarkeits-/Hidden-Checks)."""
        return self._main_parent_of(sid)

    def _vchain_for_parent(self, parent):
        """Baue die vertikale Kette eines Hauptpunkts:
           [ upmap[parent] (falls vorhanden), parent, *sub[parent] ]
           Doppelte IDs werden gefiltert, Reihenfolge bleibt erhalten.
           Ergebnisse werden gecached.
        """
        if parent in self._vchain_cache:
            return self._vchain_cache[parent][:]

        chain = []
        up = self.upmap.get(parent)
        if up:
            chain.append(up)
        chain.append(parent)
        subs = self.sub.get(parent) or []
        chain.extend(subs)

        # Duplikate entfernen, Reihenfolge stabil
        seen = set()
        out = []
        for sid in chain:
            if sid and sid not in seen:
                seen.add(sid)
                out.append(sid)

        self._vchain_cache[parent] = out[:]
        return out

    def _vchain_of(self, sid):
        """Finde die passende vchain für einen beliebigen Screen (main/sub/upmap)."""
        if sid in self.main:
            return self._vchain_for_parent(sid)
        parent = self._main_parent_of(sid)
        if parent:
            return self._vchain_for_parent(parent)
        # sonst keine vchain (hidden o.ä.)
        return None

    # Öffentliche Variante (bequemer Helper)
    def vchain_of(self, sid):
        return self._vchain_of(sid) or []

    def index_in_chain(self, sid):
        """Gibt (parent, idx) zurück, wenn sid in einer vchain ist, sonst (None, -1)."""
        if sid in self.main:
            chain = self._vchain_for_parent(sid)
            try:
                return sid, chain.index(sid)
            except ValueError:
                return sid, -1
        parent = self._main_parent_of(sid)
        if not parent:
            return None, -1
        chain = self._vchain_for_parent(parent)
        try:
            return parent, chain.index(sid)
        except ValueError:
            return parent, -1

    def next_in_chain(self, sid, step):
        """Generischer vchain-Schritt (positiv=hoch/↑, negativ=runter/↓ je nach deiner Definition)."""
        chain = self._vchain_of(sid)
        if not chain:
            return None
        try:
            i = chain.index(sid)
        except ValueError:
            return None
        j = i + step
        if 0 <= j < len(chain):
            return chain[j]
        if WRAP_VCHAIN and chain:
            return chain[j % len(chain)]
        return None

    # -------- Horizontal (unverändert) --------
    def left_of(self, sid):
        if sid not in self.main:
            sid = self._main_parent_of(sid) or sid
        try:
            i = self.main.index(sid)
        except ValueError:
            if _DBG_NAV:
                log_debug("NAV.left_of: '%s' nicht in main", sid)
            return None
        res = self.main[i-1] if i > 0 else None
        if _DBG_NAV:
            log_debug("NAV.left_of(%s)->%s", sid, res)
        return res

    def right_of(self, sid):
        if sid not in self.main:
            sid = self._main_parent_of(sid) or sid
        try:
            i = self.main.index(sid)
        except ValueError:
            if _DBG_NAV:
                log_debug("NAV.right_of: '%s' nicht in main", sid)
            return None
        res = self.main[i+1] if i < len(self.main)-1 else None
        if _DBG_NAV:
            log_debug("NAV.right_of(%s)->%s", sid, res)
        return res

    # -------- Vertikal via vchain --------
    def up_of(self, sid):
        """Swipe ↑  → vchain Index +1 (weiter 'nach unten' in der Liste)."""
        chain = self._vchain_of(sid)
        if not chain:
            if _DBG_NAV:
                log_debug("NAV.up_of(%s)->None (keine vchain)", sid)
            return None
        try:
            i = chain.index(sid)
        except ValueError:
            if _DBG_NAV:
                log_debug("NAV.up_of(%s)->None (not in chain)", sid)
            return None

        if i < len(chain) - 1:
            res = chain[i+1]
        elif WRAP_VCHAIN:
            res = chain[0]
        else:
            res = None

        if _DBG_NAV:
            log_debug("NAV.up_of(%s)->%s", sid, res)
        return res

    def down_of(self, sid):
        """Swipe ↓  → vchain Index -1 (weiter 'nach oben' in der Liste)."""
        chain = self._vchain_of(sid)
        if not chain:
            if _DBG_NAV:
                log_debug("NAV.down_of(%s)->None (keine vchain)", sid)
            return None
        try:
            i = chain.index(sid)
        except ValueError:
            if _DBG_NAV:
                log_debug("NAV.down_of(%s)->None (not in chain)", sid)
            return None

        if i > 0:
            res = chain[i-1]
        elif WRAP_VCHAIN:
            res = chain[-1]
        else:
            res = None

        if _DBG_NAV:
            log_debug("NAV.down_of(%s)->%s", sid, res)
        return res

    # -------- Hidden-Zuordnung (unverändert) --------
    def hidden_for(self, from_id):
        parent = from_id if from_id in self.main else self._main_parent_of(from_id)
        return self.hidden.get(parent or "", [])


# -------- Loader Helpers (wie gehabt) --------
def _import_module(modname):
    return __import__(modname, None, None, ["*"])

def _find_class(mod, screen_id):
    for name in dir(mod):
        obj = getattr(mod, name)
        if isinstance(obj, type) and getattr(obj, "SCREEN_ID", None) == screen_id:
            return obj
    for name in dir(mod):
        if not name.startswith("Screen"):
            continue
        obj = getattr(mod, name)
        if isinstance(obj, type):
            return obj
    return None

def _instantiate(cls, display, manager):
    variants = (
        ((), {"d":display, "manager":manager}),
        ((), {"display":display, "manager":manager}),
        ((display, manager), {}),
        ((), {"d":display}),
        ((), {"display":display}),
        ((), {"manager":manager}),
        ((display,), {}),
        ((), {}),
    )
    inst = None
    for args, kw in variants:
        try:
            inst = cls(*args, **kw)
            break
        except TypeError:
            pass
    if inst is None:
        raise TypeError("Ctor inkompatibel (erwartet mind. display/d, optional manager)")
    if getattr(inst, "d", None) is None:
        try: setattr(inst, "d", display)
        except Exception: pass
    if getattr(inst, "manager", None) is None:
        try: setattr(inst, "manager", manager)
        except Exception: pass
    bus = getattr(manager, "eventbus", None)
    if bus is not None and getattr(inst, "eventbus", None) is None:
        try: setattr(inst, "eventbus", bus)
        except Exception: pass
    if getattr(inst, "SCREEN_ID", None) is None:
        try: setattr(inst, "SCREEN_ID", getattr(cls, "SCREEN_ID", None))
        except Exception: pass
    return inst

def load_screens(display, manager, ids):
    screens = {}
    for sid in ids:
        mod = _import_module("screens."+sid)
        cls = _find_class(mod, sid)
        if not cls:
            raise ImportError("Keine Screen-Klasse für '%s' gefunden" % sid)
        screens[sid] = _instantiate(cls, display, manager)
        if _DBG_NAV:
            log_debug("NAV.load: %s -> %s", sid, cls.__name__)
    return screens
