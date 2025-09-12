# core/assets.py – Striktes Laden & Instanziieren von Themes, Watchfaces und Fonts (MicroPython)
# - lädt ausschließlich aus: ui/themes, ui/watchfaces_digital, ui/watchfaces_analog, ui/fonts
# - gibt IMMER ein fertiges Objekt zurück (Theme/Face/Font), kein stilles Fallback

# --- Logging (optional) ---
try:
    from core.logger import info as log_info, warn as log_warn, debug as log_debug
except Exception:
    def log_info(*a, **k): pass
    def log_warn(*a, **k): pass
    def log_debug(*a, **k): pass


# -------- intern: Import + Objektauflösung --------
def _imp_strict(modpath, kind, name):
    """
    MicroPython-sicheres Import: __import__(..., fromlist=['*']).
    Hebt bei Fehler ImportError aus.
    """
    try:
        mod = __import__(modpath, None, None, ['*'])
        log_info("assets: %s loaded %s", kind, modpath)
        return mod
    except Exception as e:
        log_warn("assets: %s '%s' not found (%s): %r", kind, name, modpath, e)
        raise ImportError("%s '%s' not found" % (kind, name))


def _maybe_call(x):
    """Wenn x aufrufbar ist, versuche es ohne Argumente zu instanzieren – sonst x zurückgeben."""
    try:
        if callable(x):
            try:
                return x()
            except TypeError:
                # Konstruktor verlangt Argumente → nutze das Objekt selbst
                return x
        return x
    except Exception:
        return x


def _resolve_object(mod, kind):
    """
    Liefert ein benutzbares Objekt (kein reines Modul) zurück.
    Sucht in sinnvoller Reihenfolge nach Fabriken/Klassen/Instanzen.
    Für Themes sind auch reine Token-Module erlaubt.
    """
    # je nach Art andere übliche Namen bevorzugen
    if kind == "font":
        names = ("FONT", "font", "Font", "Face", "create", "get")
    elif kind == "theme":
        names = ("Theme", "create_theme", "create", "get_theme")
    else:  # watchface (digital/analog)
        names = ("Face", "create_face", "create", "get_face", "Theme")  # Face vor Theme

    # 1) bevorzugte Attribute/Fabriken im Modul
    for n in names:
        obj = getattr(mod, n, None)
        if obj is not None:
            inst = _maybe_call(obj)
            # akzeptiere Instanzen/Klassen (aber keine reinen Module)
            if not (hasattr(inst, "__name__") and hasattr(inst, "__file__")):
                return inst

    # 2) Sonderfall: Themes dürfen reine Token-Module sein
    if kind == "theme":
        # Heuristik: typische Felder in Token-Themes
        if any(hasattr(mod, k) for k in ("COLORS", "PALETTE", "TOKENS", "Tokens", "FG", "BG", "PRIMARY", "ACCENT")):
            return mod

    # 3) Module, die selbst wie Objekte nutzbar sind
    if kind == "font":
        if hasattr(mod, "_GLYPHS") or hasattr(mod, "draw_text") or hasattr(mod, "draw_char_aa"):
            return mod
    else:
        if hasattr(mod, "draw") or hasattr(mod, "render") or hasattr(mod, "on_show"):
            return mod

    # 4) Nichts Geeignetes gefunden → harter Fehler
    raise ImportError("assets: %s module did not expose a usable object" % kind)



# ---------------- Fonts ----------------
def load_font(name):
    """Font als fertiges Objekt aus ui/fonts/<name>.py laden (kein Fallback)."""
    if not name:
        raise ValueError("Fontname ist leer")
    mod = _imp_strict("ui.fonts.%s" % name, "font", name)
    return _resolve_object(mod, "font")


# --------------- Themes ----------------
def load_theme(name):
    """Theme als fertiges Objekt aus ui/themes/<name>.py laden (kein Fallback)."""
    if not name:
        raise ValueError("Theme-Name ist leer")
    mod = _imp_strict("ui.themes.%s" % name, "theme", name)
    return _resolve_object(mod, "theme")


# -------- Watchfaces: Digital ----------
def load_watchface_digital(name):
    """Digital-Watchface als fertiges Objekt aus ui/watchfaces_digital/<name>.py laden."""
    if not name:
        raise ValueError("Digital-Watchface-Name ist leer")
    mod = _imp_strict("ui.watchfaces_digital.%s" % name, "digital watchface", name)
    return _resolve_object(mod, "watchface")


# -------- Watchfaces: Analog -----------
def load_watchface_analog(name):
    """Analog-Watchface als fertiges Objekt aus ui/watchfaces_analog/<name>.py laden."""
    if not name:
        raise ValueError("Analog-Watchface-Name ist leer")
    mod = _imp_strict("ui.watchfaces_analog.%s" % name, "analog watchface", name)
    return _resolve_object(mod, "watchface")
