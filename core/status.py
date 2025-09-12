# core/status.py
# StatusStore v1.0 - minimal, performant Last-Known-Good snapshot for `status/*` topics.
# Works on MicroPython and CPython.

try:
    import utime as _time
except ImportError:
    import time as _time

def _ticks_ms():
    if hasattr(_time, "ticks_ms"):
        return _time.ticks_ms()
    return int(_time.time() * 1000)

def _ticks_diff(a, b):
    if hasattr(_time, "ticks_diff"):
        return _time.ticks_diff(a, b)
    return a - b

class StatusStore:
    """
    Passive snapshot store for `status/*` topics with TTL.
    Attach with `attach(eventbus)` to wrap bus.publish(); no background loop, no timers.
    Read with get()/has()/age_ms()/topics().
    """
    def __init__(self, ttl_defaults=None, prefix_whitelist=("status/",)):
        # topic -> {"payload": any, "ts": int_ms, "ttl": int_ms_or_None}
        self._store = {}
        self._ttl_defaults = dict(ttl_defaults or {})
        self._prefix_whitelist = tuple(prefix_whitelist or ())
        self._bus = None
        self._orig_publish = None

    # ---------- Attach / Detach ----------
    def attach(self, bus):
        """Wrap bus.publish to cache whitelisted topics. Idempotent."""
        if self._bus is bus and self._orig_publish:
            return True
        if not hasattr(bus, "publish"):
            raise RuntimeError("StatusStore.attach: eventbus has no publish()")
        self._bus = bus
        self._orig_publish = bus.publish

        def _wrapped_publish(topic, payload=None, **kwargs):
            ttl_ms = kwargs.get("ttl_ms", None)
            # Cache before forwarding only if whitelisted
            if self._is_whitelisted(topic):
                if ttl_ms is None:
                    ttl_ms = self._ttl_defaults.get(topic, self._ttl_defaults.get("*", None))
                self._store[topic] = {"payload": payload, "ts": _ticks_ms(), "ttl": ttl_ms}
            # Forward to real publish
            return self._orig_publish(topic, payload, **kwargs)

        bus.publish = _wrapped_publish
        return True

    def detach(self):
        if self._bus and self._orig_publish:
            self._bus.publish = self._orig_publish
        self._bus = None
        self._orig_publish = None

    # ---------- Reads ----------
    def get(self, topic, fresh_only=True):
        """Return payload or None (if missing or expired when fresh_only=True)."""
        ent = self._store.get(topic)
        if not ent:
            return None
        ttl = ent.get("ttl", None)
        if fresh_only and (ttl is not None):
            if _ticks_diff(_ticks_ms(), ent["ts"]) > int(ttl):
                return None
        return ent.get("payload")

    def has(self, topic, fresh_only=True):
        return self.get(topic, fresh_only=fresh_only) is not None

    def age_ms(self, topic):
        ent = self._store.get(topic)
        if not ent:
            return None
        return max(0, _ticks_diff(_ticks_ms(), ent["ts"]))

    def topics(self):
        return list(self._store.keys())

    # ---------- Config ----------
    def set_default_ttl(self, topic, ttl_ms):
        self._ttl_defaults[topic] = None if ttl_ms is None else int(ttl_ms)

    def defaults(self):
        return dict(self._ttl_defaults)

    def whitelist_prefixes(self):
        return tuple(self._prefix_whitelist)

    # ---------- Debug Snapshot (optional use) ----------
    def snapshot(self, include_payload=False):
        """Return a lightweight snapshot for debugging or UI (avoid large payloads)."""
        now = _ticks_ms()
        out = []
        for topic, ent in self._store.items():
            ttl = ent.get("ttl", None)
            age = max(0, _ticks_diff(now, ent["ts"]))
            fresh = (ttl is None) or (age <= int(ttl))
            item = {
                "topic": topic,
                "age_ms": age,
                "ttl_ms": ttl,
                "fresh": bool(fresh),
            }
            if include_payload:
                item["payload"] = ent.get("payload")
            out.append(item)
        return out

    # ---------- Helpers ----------
    def _is_whitelisted(self, topic):
        if not self._prefix_whitelist:
            return True
        t = str(topic or "")
        for p in self._prefix_whitelist:
            if t.startswith(p):
                return True
        return False