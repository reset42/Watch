
# core/logger.py - ultra-light logger for MicroPython / CPython
LEVEL_ERROR = 40
LEVEL_WARN  = 30
LEVEL_INFO  = 20
LEVEL_DEBUG = 10

_level = LEVEL_INFO

def set_level(level):
    global _level
    _level = int(level)

def get_level():
    return _level

def _emit(tag, *args):
    try:
        print(tag, *args)
    except Exception:
        pass

def error(*args):
    if _level <= LEVEL_ERROR:
        _emit("[E]", *args)

def warn(*args):
    if _level <= LEVEL_WARN:
        _emit("[W]", *args)

def info(*args):
    if _level <= LEVEL_INFO:
        _emit("[I]", *args)

def debug(*args):
    if _level <= LEVEL_DEBUG:
        _emit("[D]", *args)
