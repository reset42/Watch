# rtc_pcf8563.py – Minimaltreiber PCF8563 (I2C0: SDA=10, SCL=11), INT optional auf GPIO17
from machine import I2C, Pin
import time

PCF_ADDR = 0x51

def _bcd2i(b): return (b >> 4) * 10 + (b & 0x0F)
def _i2bcd(i): return ((i // 10) << 4) | (i % 10)

class PCF8563:
    def __init__(self, i2c=None, sda=10, scl=11, freq=400_000, int_pin=17):
        self.i2c = i2c or I2C(0, sda=Pin(sda), scl=Pin(scl), freq=freq)
        try:
            self.int_pin = Pin(int_pin, Pin.IN, Pin.PULL_UP)
        except Exception:
            self.int_pin = None

    def datetime(self):
        # Reg 0x02..0x08: sec,min,hour,day,weekday,month,year
        data = self.i2c.readfrom_mem(PCF_ADDR, 0x02, 7)
        ss = _bcd2i(data[0] & 0x7F)
        mm = _bcd2i(data[1] & 0x7F)
        hh = _bcd2i(data[2] & 0x3F)
        d  = _bcd2i(data[3] & 0x3F)
        wd = data[4] & 0x07
        mo = _bcd2i(data[5] & 0x1F)
        yr = 2000 + _bcd2i(data[6])
        return (yr, mo, d, wd, hh, mm, ss, 0)

    def set_datetime(self, dt):
        # dt = (Y, M, D, wd, h, m, s)
        yr, mo, d, wd, hh, mm, ss = dt
        buf = bytes([
            _i2bcd(ss & 0x7F),
            _i2bcd(mm & 0x7F),
            _i2bcd(hh & 0x3F),
            _i2bcd(d  & 0x3F),
            wd & 0x07,
            _i2bcd(mo & 0x1F),
            _i2bcd(yr % 100)
        ])
        self.i2c.writeto_mem(PCF_ADDR, 0x02, buf)
