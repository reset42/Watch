# touch.py – Auto-Detect Touch (FT6236U / CST816 / GT911), feste Pins fürs Board

# --- Logging (mit Debug-Gating über DEBUG_TOUCH) -----------------------
try:
    import config
    _DBG_TOUCH = bool(getattr(config, "DEBUG_TOUCH", False))
except Exception:
    _DBG_TOUCH = False

from core.logger import info as log_info, warn as log_warn
from core.logger import debug as _raw_debug
def log_debug(*a, **k):
    # nur sprechen, wenn DEBUG_TOUCH aktiv ist
    if _DBG_TOUCH:
        _raw_debug(*a, **k)
# ----------------------------------------------------------------------

from core import eventbus

try:
    from machine import I2C, Pin
    import utime as time
except Exception:
    I2C = None; Pin = None
    import time

# ---- feste Hardware-Pins/BUS (kein config mehr) ----
_TOUCH_I2C_ID  = 1
_TOUCH_SDA_PIN = 39
_TOUCH_SCL_PIN = 40
_TOUCH_IRQ_PIN = 16

# ---- bekannte Touch-Controller ----
_FT_ADDR   = 0x38
_FT_TD     = 0x02
_FT_P1_XH  = 0x03; _FT_P1_XL = 0x04
_FT_P1_YH  = 0x05; _FT_P1_YL = 0x06

_CST_ADDR  = 0x15
_CST_GEST  = 0x01
_CST_FING  = 0x02
_CST_XH    = 0x03; _CST_XL = 0x04
_CST_YH    = 0x05; _CST_YL = 0x06

_GT_ADDRS  = (0x5D, 0x14)
_GT_P1     = 0x81
_GT_RDY    = 0x814E  # (nicht genutzt, minimal)

# ---- Axis-Mapping (optional aus config) ----
_SWAP   = bool(getattr(config, "TOUCH_SWAP_XY", False))
_FLIP_X = bool(getattr(config, "TOUCH_FLIP_X", False))
_FLIP_Y = bool(getattr(config, "TOUCH_FLIP_Y", False))

def _pm_ping():
    #eventbus.publish("sys/activity")
    pass

def _map_xy(x, y, w, h):
    if _SWAP:
        x, y = y, x; w, h = h, w
    if _FLIP_X:
        x = (w - 1) - x
    if _FLIP_Y:
        y = (h - 1) - y
    x = 0 if x < 0 else (w - 1 if x >= w else x)
    y = 0 if y < 0 else (h - 1 if y >= h else y)
    return x, y

class _FT6236:
    def __init__(self, i2c): self.i2c = i2c
    def _u8(self, r):
        try: return self.i2c.readfrom_mem(_FT_ADDR, r, 1)[0]
        except Exception: return 0
    def read_point(self):
        try:
            n = self._u8(_FT_TD) & 0x0F
            if n == 0: return None, None
            xh=self._u8(_FT_P1_XH); xl=self._u8(_FT_P1_XL)
            yh=self._u8(_FT_P1_YH); yl=self._u8(_FT_P1_YL)
            x=((xh & 0x0F)<<8)|xl; y=((yh & 0x0F)<<8)|yl
            return x,y
        except Exception:
            return None,None

class _CST816:
    def __init__(self, i2c): self.i2c = i2c
    def _u8(self, r):
        try: return self.i2c.readfrom_mem(_CST_ADDR, r, 1)[0]
        except Exception: return 0
    def read_point(self):
        try:
            xh=self._u8(_CST_XH); xl=self._u8(_CST_XL)
            yh=self._u8(_CST_YH); yl=self._u8(_CST_YL)
            x=((xh & 0x0F)<<8)|xl; y=((yh & 0x0F)<<8)|yl
            if x==0 and y==0 and (self._u8(_CST_FING)&0x0F)==0:
                return None,None
            return x,y
        except Exception:
            return None,None

class _GT911:
    def __init__(self, i2c, addr):
        self.i2c = i2c; self.addr = addr
    def _mem_read(self, reg, n):
        try:
            ra = bytes([(reg>>8)&0xFF, reg&0xFF])
            self.i2c.writeto(self.addr, ra)
            return self.i2c.readfrom(self.addr, n)
        except Exception:
            return None
    def read_point(self):
        try:
            buf = self._mem_read(_GT_P1, 4)
            if not buf: return None,None
            x = buf[0] | (buf[1]<<8)
            y = buf[2] | (buf[3]<<8)
            if x==0 and y==0: return None,None
            return x,y
        except Exception:
            return None,None

class Touch:
    def __init__(self, width=240, height=240, irq_pin=None):
        self.width  = width
        self.height = height
        self.i2c_id = _TOUCH_I2C_ID
        self.sda    = _TOUCH_SDA_PIN
        self.scl    = _TOUCH_SCL_PIN
        self.irq_pin_num = irq_pin if irq_pin is not None else _TOUCH_IRQ_PIN

        self._i2c=None; self._dev=None
        self._irq=None; self._irq_pending=False
        self._poll_mode=False

        # swipe/tap state
        self._down=False; self._x0=self._y0=0; self._t0=0
        self._last_x=self._last_y=0

        if I2C is None or Pin is None:
            log_warn("Touch: machine API not available – stub mode")
            return

        # dedizierter I2C (SDA=39, SCL=40)
        try:
            self._i2c = I2C(self.i2c_id, sda=Pin(self.sda), scl=Pin(self.scl), freq=400_000)
        except Exception as e:
            log_warn("Touch: I2C init failed (bus{}, sda{}, scl{}):".format(self.i2c_id, self.sda, self.scl), e)
            return

        # Auto-detect: FT -> CST -> GT911
        dev=None
        try:
            self._i2c.readfrom_mem(_FT_ADDR, _FT_TD, 1)
            dev=_FT6236(self._i2c)
            log_info("Touch: FT6236U detected @0x38 on bus{}".format(self.i2c_id))
        except Exception:
            try:
                self._i2c.readfrom_mem(_CST_ADDR, _CST_FING, 1)
                dev=_CST816(self._i2c)
                log_info("Touch: CST816 detected @0x15 on bus{}".format(self.i2c_id))
            except Exception:
                for a in _GT_ADDRS:
                    try:
                        self._i2c.readfrom_mem(a, 0, 1)  # ping
                        dev=_GT911(self._i2c, a)
                        log_info("Touch: GT911 detected @0x%02X on bus%s"%(a, self.i2c_id))
                        break
                    except Exception:
                        pass
        if dev is None:
            log_warn("Touch: no device on bus{} sda{} scl{}".format(self.i2c_id, self.sda, self.scl))
        self._dev=dev

        # IRQ
        try:
            p = Pin(self.irq_pin_num, Pin.IN, Pin.PULL_UP)
            self._irq = p.irq(trigger=Pin.IRQ_FALLING, handler=self._on_irq)
            log_info("Touch IRQ on pin {}".format(self.irq_pin_num))
        except Exception as e:
            log_warn("Touch: IRQ init failed – polling fallback:", e)
            self._poll_mode=True

    def _on_irq(self, *_):
        self._irq_pending=True

    def _read_xy(self):
        if not self._dev: return None,None
        x,y = self._dev.read_point()
        if x is None or y is None: return None,None
        x,y = _map_xy(x,y,self.width,self.height)
        return x,y

    def _release_eval(self, now, x1, y1):
        SWIPE_THR = 20; TAP_TIME = 300; LONG_TIME = 600; MOVE_THR = 10
        dt = time.ticks_diff(now, self._t0)
        dx = x1 - self._x0; dy = y1 - self._y0
        self._down=False; _pm_ping()

        if abs(dx)>SWIPE_THR or abs(dy)>SWIPE_THR:
            dir_ = "left" if abs(dx)>=abs(dy) and dx<0 else \
                   "right" if abs(dx)>=abs(dy) and dx>0 else \
                   "up" if dy < 0 else "down"
            evt={"type":"swipe","dir":dir_,"dx":dx,"dy":dy}
            log_debug("touch evt:", evt); return evt
        if dt>=LONG_TIME:
            evt={"type":"long_press","x":x1,"y":y1}
            log_debug("touch evt:", evt); return evt
        if dt<=TAP_TIME and abs(dx)<MOVE_THR and abs(dy)<MOVE_THR:
            evt={"type":"tap","x":x1,"y":y1}
            log_debug("touch evt:", evt); return evt
        return None

    def get_event(self):
        if self._dev is None:
            return None

        if not self._poll_mode:
            if not self._irq_pending:
                return None
            self._irq_pending=False

        now = time.ticks_ms()
        x,y = self._read_xy()

        if x is None:
            if self._down:
                return self._release_eval(now, self._last_x, self._last_y)
            return None

        if not self._down:
            self._down=True; self._x0,self._y0=x,y; self._t0=now
        self._last_x,self._last_y=x,y
        _pm_ping()
        return None
