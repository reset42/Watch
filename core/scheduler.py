
# core/scheduler.py - minimal scheduler/timing helpers
try:
    import utime as _time
except ImportError:
    import time as _time

def sleep(ms):
    try:
        _time.sleep_ms(ms)
    except AttributeError:
        _time.sleep(ms / 1000.0)

def sleep_ms(ms):
    sleep(ms)

def sleep_us(us):
    try:
        _time.sleep_us(us)
    except AttributeError:
        _time.sleep(us / 1_000_000.0)

def monotonic_ms():
    try:
        return _time.ticks_ms()
    except AttributeError:
        return int(_time.time() * 1000)
