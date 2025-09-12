# power.py – AXP2101 Treiber (LilyGO T-Watch S3) + PMU-Button-IRQs
# I2C0: SDA=10, SCL=11, ADDR=0x34
# Rails: ALDO1=1.8V (IOVCC), ALDO2=3.3V (Backlight), ALDO3=3.3V (LCD)

from machine import I2C, Pin
import time

# ==== [I2C + Adresse] ========================================================
AXP_ADDR = 0x34
I2C_ID, SDA_PIN, SCL_PIN, FREQ = 0, 10, 11, 400_000

# ==== [Grund-Register] =======================================================
REG_CHIP_ID   = 0x03

# LDO/Rails
REG_LDO_EN   = 0x90   # Enable-Bits für LDOs
REG_ALDO1_V  = 0x92   # 1.8V (IOVCC)
REG_ALDO2_V  = 0x93   # 3.3V (Backlight-Rail)
REG_ALDO3_V  = 0x94   # 3.3V (LCD-Rail)

BIT_ALDO1 = 0x01
BIT_ALDO2 = 0x02
BIT_ALDO3 = 0x04

# Status/ADC
REG_STATUS1    = 0x00
REG_STATUS2    = 0x01
REG_ADC_EN1    = 0x30
# ADC-Daten (laut DB: 0x34–0x3D)
REG_VBAT_H     = 0x34; REG_VBAT_L = 0x35
REG_VBUS_H     = 0x36; REG_VBUS_L = 0x37
REG_VSYS_H     = 0x38; REG_VSYS_L = 0x39
REG_TS_H       = 0x3A; REG_TS_L   = 0x3B
REG_DIE_H      = 0x3C; REG_DIE_L  = 0x3D
REG_FG_PERCENT = 0xA4

# ==== [IRQ/Power-Button] =====================================================
# Enable-Masken
REG_INTEN1  = 0x40
REG_INTEN2  = 0x41
REG_INTEN3  = 0x42
# Status (write 0xFF to clear)
REG_INTSTS1 = 0x48
REG_INTSTS2 = 0x49
REG_INTSTS3 = 0x4A
# Timing/Levels (ON/OFF/IRQ-Level)
REG_IRQ_OFF_ON_LEVEL_CTRL = 0x27
# Bits in INTEN2 / INTSTS2 für Power-Key
BIT_PKEY_POS = 1 << 0   # positive edge
BIT_PKEY_NEG = 1 << 1   # negative edge
BIT_PKEY_LONG  = 1 << 2 # long press
BIT_PKEY_SHORT = 1 << 3 # short press

# REG 0x27 Kodierung:
#   [5:4] IRQ_LEVEL   0=1s, 1=1.5s, 2=2s, 3=2.5s
#   [3:2] OFF_LEVEL   0=4s, 1=6s,   2=8s, 3=10s
#   [1:0] ON_LEVEL    0=128ms, 1=512ms, 2=1s, 3=2s
def _pack_irq_levels(irq_level=1, off_level=2, on_level=1):
    irq = (irq_level & 0x3) << 4
    off = (off_level & 0x3) << 2
    on  = (on_level  & 0x3)
    return irq | off | on

# ==== [Skalen/Kodierungen] ===================================================
# VBAT/VSYS bei dir: 16-bit (H<<8|L) mit 1 mV/LSB
VBAT_LSHIFT_NIBBLE = False
LSB_mV_VBAT = 1
LSB_mV_VSYS = 1

# ==== [Treiber] ==============================================================
class PowerAXP2101:
    def __init__(self):
        self.i2c = I2C(I2C_ID, scl=Pin(SCL_PIN), sda=Pin(SDA_PIN), freq=FREQ)
        self._adc_ready = False
        try:
            self.chip_id = self._r8(REG_CHIP_ID)
        except Exception:
            self.chip_id = None

    # -- Low-Level I2C --------------------------------------------------------
    def _r8(self, a):
        return self.i2c.readfrom_mem(AXP_ADDR, a, 1)[0]
    def _w8(self, a, v):
        self.i2c.writeto_mem(AXP_ADDR, a, bytes([v]))
    def _rN(self, a, n):
        return self.i2c.readfrom_mem(AXP_ADDR, a, n)

    # -- Rails / Display-Power-Pfad -------------------------------------------
    def init_display_rails(self):
        # Spannungen setzen
        self._w8(REG_ALDO1_V, 0x0D)  # 1.8V
        self._w8(REG_ALDO2_V, 0x1C)  # 3.3V
        self._w8(REG_ALDO3_V, 0x1C)  # 3.3V
        # IOVCC + BL + LCD einschalten, LCD Reset-Sequenz
        self._w8(REG_LDO_EN, self._r8(REG_LDO_EN) | (BIT_ALDO1 | BIT_ALDO2 | BIT_ALDO3))
        time.sleep_ms(30)
        self._w8(REG_LDO_EN, self._r8(REG_LDO_EN) & ~BIT_ALDO3); time.sleep_ms(80)
        self._w8(REG_LDO_EN, self._r8(REG_LDO_EN) |  BIT_ALDO3); time.sleep_ms(150)

    def set_rails(self, iovcc=None, bl=None, lcd=None):
        val = self._r8(REG_LDO_EN)
        if iovcc is not None: val = (val | BIT_ALDO1) if iovcc else (val & ~BIT_ALDO1)
        if bl    is not None: val = (val | BIT_ALDO2) if bl    else (val & ~BIT_ALDO2)
        if lcd   is not None: val = (val | BIT_ALDO3) if lcd   else (val & ~BIT_ALDO3)
        self._w8(REG_LDO_EN, val)

    def sleep_display(self):
        self.set_rails(bl=False, lcd=False)

    def wakeup_display(self):
        self.set_rails(lcd=False); time.sleep_ms(80)
        self.set_rails(lcd=True);  time.sleep_ms(150)
        self.set_rails(bl=True)

    # -- PMU Button-IRQs ------------------------------------------------------
    def enable_pmu_button_irqs(self, *, short=True, long=True, posneg=True,
                               irq_level=1, off_level=2, on_level=1, debug=False):
        """
        short/long: kurze/lange Press-IRQs
        posneg:     Flanken (press/release) IRQs
        *_level:    0..3 (siehe _pack_irq_levels)
        """
        # Timings setzen
        lvl = _pack_irq_levels(irq_level, off_level, on_level)
        self._w8(REG_IRQ_OFF_ON_LEVEL_CTRL, lvl)
        # INTEN2 Bits konfigurieren
        inten2 = self._r8(REG_INTEN2)
        if posneg:
            inten2 |= (BIT_PKEY_POS | BIT_PKEY_NEG)
        else:
            inten2 &= ~(BIT_PKEY_POS | BIT_PKEY_NEG)
        if short:
            inten2 |= BIT_PKEY_SHORT
        else:
            inten2 &= ~BIT_PKEY_SHORT
        if long:
            inten2 |= BIT_PKEY_LONG
        else:
            inten2 &= ~BIT_PKEY_LONG
        self._w8(REG_INTEN2, inten2)
        if debug:
            print("AXP2101 Chip ID:", hex(self.chip_id) if self.chip_id is not None else "n/a")
            print("IRQ Level/On/Off gesetzt:", hex(lvl))
            print("INTEN2 =", hex(inten2))

    def read_clear_irqs(self):
        """Rohstatus holen und anschließend alle drei Status-Register löschen."""
        s1 = self._r8(REG_INTSTS1)
        s2 = self._r8(REG_INTSTS2)
        s3 = self._r8(REG_INTSTS3)
        # zum Löschen 0xFF zurückschreiben (schreibt 1 in jedes Bit → clear)
        self._w8(REG_INTSTS1, 0xFF)
        self._w8(REG_INTSTS2, 0xFF)
        self._w8(REG_INTSTS3, 0xFF)
        return s1, s2, s3

    def poll_pmu_button(self):
        """
        Liest die PMU-IRQ-Statusregister, löscht sie und gibt eine Liste
        von Events zurück, z.B. ["short"] oder ["pos","neg"] etc.
        Keine Events -> []
        """
        _, s2, _ = self.read_clear_irqs()
        events = []
        if s2 & BIT_PKEY_SHORT:   events.append("short")
        if s2 & BIT_PKEY_LONG:    events.append("long")
        if s2 & BIT_PKEY_POS:     events.append("pos")
        if s2 & BIT_PKEY_NEG:     events.append("neg")
        return events

    # -- ADC/Status -----------------------------------------------------------
    def _ensure_adc(self):
        if self._adc_ready: return
        try:
            en = self._r8(REG_ADC_EN1)
            en |= 0xFF  # konservativ alle ADC-Kanäle aktivieren
            self._w8(REG_ADC_EN1, en)
        except Exception:
            pass
        self._adc_ready = True
        time.sleep_ms(5)

    def _read16(self, h_addr, l_addr, nibble=False):
        h = self._r8(h_addr); l = self._r8(l_addr)
        return ((h << 4) | (l & 0x0F)) if nibble else ((h << 8) | l)

    def _read_vbat_mv(self):
        self._ensure_adc()
        raw = self._read16(REG_VBAT_H, REG_VBAT_L, nibble=VBAT_LSHIFT_NIBBLE)
        return raw * LSB_mV_VBAT

    def _read_vbus_mv(self):
        self._ensure_adc()
        raw16 = (self._r8(REG_VBUS_H) << 8) | self._r8(REG_VBUS_L)
        return int(raw16 * 0.1)

    def _read_vsys_mv(self):
        self._ensure_adc()
        raw16 = (self._r8(REG_VSYS_H) << 8) | self._r8(REG_VSYS_L)
        return raw16 * LSB_mV_VSYS

    def _read_die_temp_mv(self):
        return None
    def _read_ts_mv(self):
        return None

    def _read_percent(self):
        try:
            soc = self._r8(REG_FG_PERCENT)
            return 100 if soc > 100 else soc
        except Exception:
            return None

    def _usb_present(self):
        try:
            s1 = self._r8(REG_STATUS1)
            return bool(s1 & 0x60)  # Heuristik: VBUS good
        except Exception:
            return None

    def _charge_info(self):
        s2 = self._r8(REG_STATUS2)
        dir_bits = (s2 >> 5) & 0x03
        direction = ("standby", "charging", "discharging", "reserved")[dir_bits]
        st = s2 & 0x07
        chg_state = {
            0: "trickle",
            1: "precharge",
            2: "cc",
            3: "cv",
            4: "done",
            5: "not_charging",
        }.get(st, "reserved")
        return direction, chg_state

    def read_status(self, debug=False):
        vbat = self._read_vbat_mv()
        vbus = self._read_vbus_mv()
        vsys = self._read_vsys_mv()
        die_mv = self._read_die_temp_mv()
        ts_mv = self._read_ts_mv()
        percent = self._read_percent()
        direction, chg_state = self._charge_info()
        usb = self._usb_present()

        info = {
            "percent": percent,
            "vbat_mV": vbat,
            "vbus_mV": vbus,
            "vsys_mV":  vsys,
            "chip_temp_mV": die_mv,
            "bat_ntc_mV":  ts_mv,
            "charge_direction": direction,
            "charge_state": chg_state,
            "usb_present": usb,
        }

        if debug:
            try:
                info["raw"] = {
                    "STATUS1": self._r8(REG_STATUS1),
                    "STATUS2": self._r8(REG_STATUS2),
                    "ADC_EN1": self._r8(REG_ADC_EN1),
                    "VBAT_H":  self._r8(REG_VBAT_H), "VBAT_L": self._r8(REG_VBAT_L),
                    "VBUS_H":  self._r8(REG_VBUS_H), "VBUS_L": self._r8(REG_VBUS_L),
                    "VSYS_H":  self._r8(REG_VSYS_H), "VSYS_L": self._r8(REG_VSYS_L),
                    "DIE_H":   self._r8(REG_DIE_H),  "DIE_L":  self._r8(REG_DIE_L),
                    "TS_H":    self._r8(REG_TS_H),   "TS_L":   self._r8(REG_TS_L),
                    "FG_%":    self._r8(REG_FG_PERCENT),
                }
            except Exception:
                pass

        return info

# ==== [Convenience] ==========================================================
def create_power():
    p = PowerAXP2101()
    p.init_display_rails()
    # Button-IRQs sofort aktivieren (on:512ms, off:8s, irq:1.5s)
    try:
        p.enable_pmu_button_irqs(short=True, long=True, posneg=True,
                                 irq_level=1, off_level=2, on_level=1, debug=False)
    except Exception:
        pass
    return p
