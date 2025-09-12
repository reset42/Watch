# core/eventbus.py — winziger, µPy-freundlicher Pub/Sub
class EventBus:
    def __init__(self):
        self._subs = {}   # event -> list[(id, cb, once)]
        self._next_id = 1

    def subscribe(self, event, cb, *, once=False):
        """Gibt ein Token (event, id) zurück, das für unsubscribe genutzt wird."""
        lst = self._subs.setdefault(event, [])
        token = (event, self._next_id)
        self._next_id += 1
        lst.append((token[1], cb, bool(once)))
        return token

    def unsubscribe(self, token):
        event, id_ = token
        lst = self._subs.get(event, [])
        self._subs[event] = [(i,cb,o) for (i,cb,o) in lst if i != id_]

    def publish(self, event, payload=None):
        lst = self._subs.get(event, [])
        # Kopie iterieren, damit once-Abos entfernt werden können
        remove_ids = []
        for (i, cb, once) in list(lst):
            try:
                cb(payload)
            except Exception:
                # Fehler im Handler schlucken (stabil bleiben)
                pass
            if once:
                remove_ids.append(i)
        if remove_ids:
            self._subs[event] = [(i,cb,o) for (i,cb,o) in self._subs.get(event, []) if i not in remove_ids]
