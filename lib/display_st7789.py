# display_st7789.py — ST7789-Treiber für LilyGO T-Watch S3 (ESP32-S3)
# HW-SPI only, AXP2101 Power-Seq, PWM-Backlight, Zeichenprims + 5x7 Font
from machine import Pin, I2C, SPI, PWM
import time, struct
from core.logger import warn as log_warn
from lib.power_axp2101 import create_power

# ---- Konfiguration (SPI-Hz aus config.py, robust geladen) -------------------
try:
    import config
    _CFG_SPI_HZ = int(getattr(config, "DISPLAY_HZ", 40_000_000))
except Exception:
    _CFG_SPI_HZ = 40_000_000

# Physische Panelgröße
LCD_W = 240
LCD_H = 240

# ST7789 Commands
_SWRESET=0x01; _SLPIN=0x10; _SLPOUT=0x11; _NORON=0x13
_INVOFF=0x20; _INVON=0x21; _DISPON=0x29
_CASET =0x2A; _RASET=0x2B; _RAMWR=0x2C
_MADCTL=0x36; _COLMOD=0x3A; _PORCTRL=0xB2; _GCTRL=0xB7
_VCOMS =0xBB; _LCMCTRL=0xC0; _VDVVRHEN=0xC2; _VRHS=0xC3
_VDVSET=0xC4; _FRCTRL2=0xC6; _PWCTRL1=0xD0; _VSCRDEF=0x33

# MADCTL Bits
_MADCTL_MY=0x80; _MADCTL_MX=0x40; _MADCTL_MV=0x20
_MADCTL_ML=0x10; _MADCTL_BGR=0x08; _MADCTL_MH=0x04

# Farben
def rgb565(r,g,b): return ((r&0xF8)<<8)|((g&0xFC)<<3)|(b>>3)
BLACK=rgb565(0,0,0); WHITE=rgb565(255,255,255); RED=rgb565(255,0,0)
GREEN=rgb565(0,255,0); BLUE=rgb565(0,0,255); CYAN=rgb565(0,255,255)
MAGENTA=rgb565(255,0,255); YELLOW=rgb565(255,255,0)

# ---- ST7789 ----
class ST7789Display:
    def __init__(self,
                 cal_hw=True,
                 sck=18, mosi=13, cs=12, dc=38,
                 miso_opt=41, try_miso=True,
                 bl_pin=45, bl_pwm_freq=2000,
                 invert_colors=True, bgr=True,
                 rotation=0,
                 # Bevorzugt: config.DISPLAY_HZ; dann weitere Fallbacks
                 spi_bauds=None,
                 spi_modes=((0,0),(1,0))):
        self._cal_hw=cal_hw; self._sck=sck; self._mosi=mosi
        self._cs=Pin(cs,Pin.OUT,value=1); self._dc=Pin(dc,Pin.OUT,value=1)
        self._miso_opt=miso_opt; self._try_miso=try_miso
        self._invert=invert_colors; self._bgr=bgr
        self._spi=None
        # logische Maße
        self._w=LCD_W; self._h=LCD_H
        # Panel-Offsets (je Rotation)
        self._xoff=0; self._yoff=0
        # Backlight
        self._bl_pwm=PWM(Pin(bl_pin,Pin.OUT), freq=bl_pwm_freq, duty_u16=0)
        self._brightness=1.0
        # Zeilenbuffer
        self._linebuf=bytearray(LCD_W*2); self._mv_line=memoryview(self._linebuf)
        # Default-Font (extern), falls vorhanden
        self._default_face = None
        self._default_style = None
        # SPI-Baudraten: config.DISPLAY_HZ zuerst, dann gängige Fallbacks
        if spi_bauds is None:
            # unique & sinnvoll sortiert (hoch->runter)
            fallback = (40_000_000, 20_000_000, 10_000_000, 5_000_000)
            if _CFG_SPI_HZ in fallback:
                order = (_CFG_SPI_HZ,) + tuple(b for b in fallback if b != _CFG_SPI_HZ)
            else:
                order = (_CFG_SPI_HZ,) + fallback
            spi_bauds = order
        # Init
        self._setup_spi(spi_bauds, spi_modes)
        self._init_panel()
        self.set_rotation(rotation)
        self.set_invert(self._invert)
        self.set_backlight(1.0)

    # --- Low-level ---
    def _cmd(self,c,data=None):
        self._cs.off(); self._dc.off(); self._spi.write(bytes([c]))
        if data: self._dc.on(); self._spi.write(data)
        self._cs.on()

    def _set_window(self, x0, y0, x1, y1):
        # Clipping in logischen Koordinaten
        if x0<0: x0=0
        if y0<0: y0=0
        if x1>=self._w: x1=self._w-1
        if y1>=self._h: y1=self._h-1
        # ST7789: immer X->CASET, Y->RASET senden und OFFSETS addieren
        x0o = x0 + self._xoff; x1o = x1 + self._xoff
        y0o = y0 + self._yoff; y1o = y1 + self._yoff
        self._cmd(_CASET, struct.pack(">HH", x0o, x1o))
        self._cmd(_RASET, struct.pack(">HH", y0o, y1o))
        self._cs.off(); self._dc.off(); self._spi.write(bytes([_RAMWR])); self._dc.on()
        time.sleep_us(8)

    def _init_panel(self):
        self._cmd(_SWRESET); time.sleep_ms(150)
        self._cmd(_SLPOUT);  time.sleep_ms(150)
        self._cmd(_COLMOD, b"\x55")
        self._cmd(0xB0, b"\x00")
        # MADCTL wird durch set_rotation gesetzt; hier nur Basis (BGR ggf. an)
        self._cmd(_MADCTL, bytes([_MADCTL_BGR if self._bgr else 0x00]))
        self._cmd(_PORCTRL, b"\x0C\x0C\x00\x33\x33")
        self._cmd(_GCTRL,   b"\x35")
        self._cmd(_VCOMS,   b"\x28")
        self._cmd(_LCMCTRL, b"\x0C")
        self._cmd(_VDVVRHEN,b"\x01\xFF")
        self._cmd(_VRHS,    b"\x10")
        self._cmd(_VDVSET,  b"\x20")
        self._cmd(_FRCTRL2, b"\x0F")
        self._cmd(_PWCTRL1, b"\xA4\xA1")
        self._cmd(_VSCRDEF, b"\x00\x00\x00\xF0\x00\x00")
        self._cmd(_NORON)
        self._cmd(_INVON if self._invert else _INVOFF)
        self._cmd(_DISPON); time.sleep_ms(120)

    def _setup_spi(self, bauds, modes):
        ok=False
        for pol,pha in (modes or ((0,0),(0,1),(1,0),(1,1))):
            for bd in (bauds or (5_000_000,10_000_000,20_000_000,40_000_000)):
                for use_miso in ([False,True] if self._try_miso else [False]):
                    try:
                        if use_miso:
                            self._spi=SPI(1, baudrate=bd, polarity=pol, phase=pha,
                                          sck=Pin(self._sck), mosi=Pin(self._mosi), miso=Pin(self._miso_opt))
                        else:
                            self._spi=SPI(1, baudrate=bd, polarity=pol, phase=pha,
                                          sck=Pin(self._sck), mosi=Pin(self._mosi))
                        ok=True; raise StopIteration
                    except StopIteration:
                        pass
                    except Exception:
                        try:
                            if self._spi: self._spi.deinit()
                        except Exception: pass
                        self._spi=None; ok=False
        if not ok: raise RuntimeError("HW-SPI Init fehlgeschlagen.")

    # --- Öffentliche API ---
    def width(self):  return self._w
    def height(self): return self._h

    def set_backlight(self, level):
        """
        Akzeptiert 0..1 (float) ODER 0..255 (int). Werte werden geclamped.
        """
        # 0..255 -> 0..1 umsetzen
        if isinstance(level, int) and level > 1:
            level = level / 255.0
        try:
            level = float(level)
        except Exception:
            level = 1.0
        if level < 0: level = 0.0
        if level > 1: level = 1.0
        self._brightness = level
        self._bl_pwm.duty_u16(int(level * 65535))

    def set_invert(self, enable: bool):
        self._invert=bool(enable)
        self._cmd(_INVON if self._invert else _INVOFF)

    def set_rotation(self, rot:int):
        rot = rot%4
        mad = _MADCTL_BGR if self._bgr else 0
        # MADCTL + Offsets je Rotation (ST7789V 240x240)
        if   rot==0:
            mad |= (_MADCTL_MX | _MADCTL_MY)   # 0°
            self._xoff, self._yoff = 0, 80
            self._w, self._h = LCD_W, LCD_H
        elif rot==1:
            mad |= (_MADCTL_MV | _MADCTL_MY)   # 90° CW
            self._xoff, self._yoff = 80, 0
            self._w, self._h = LCD_H, LCD_W   # swap
        elif rot==2:
            mad |= 0                           # 180°
            self._xoff, self._yoff = 0, 0
            self._w, self._h = LCD_W, LCD_H
        else: # rot==3
            mad |= (_MADCTL_MX | _MADCTL_MV)   # 270° CW
            self._xoff, self._yoff = 0, 0
            self._w, self._h = LCD_H, LCD_W   # swap

        self._cmd(_MADCTL, bytes([mad]))
        # Vollbildfenster setzen (mit Offsets)
        self._set_window(0,0,self._w-1,self._h-1)
        time.sleep_ms(2)

    def sleep(self, enable=True):
        if enable: self._cmd(_SLPIN)
        else: self._cmd(_SLPOUT); time.sleep_ms(120)

    def fill_screen(self, c565): self.fill_rect(0,0,self._w,self._h,c565)

    def fill_rect(self,x,y,w,h,c565):
        if w<=0 or h<=0: return
        x2,y2=x+w-1,y+h-1
        self._set_window(x,y,x2,y2)
        hi,lo=(c565>>8)&0xFF, c565&0xFF
        # Zeilenpuffer füllen
        for i in range(0,w*2,2):
            self._mv_line[i]=hi; self._mv_line[i+1]=lo
        for _ in range(h):
            self._spi.write(self._mv_line[:w*2])
        self._cs.on()

    # --- Convenience-Linien & -Rahmen (nutzen fill_rect) ---
    def hline(self, x, y, w, color):
        if w > 0:
            self.fill_rect(x, y, w, 1, color)

    def vline(self, x, y, h, color):
        if h > 0:
            self.fill_rect(x, y, 1, h, color)

    def draw_rect(self, x, y, w, h, color):
        if w <= 0 or h <= 0:
            return
        self.hline(x, y, w, color)               # oben
        self.hline(x, y + h - 1, w, color)       # unten
        self.vline(x, y, h, color)               # links
        self.vline(x + w - 1, y, h, color)       # rechts

    def draw_pixel(self,x,y,c565):
        if 0<=x<self._w and 0<=y<self._h:
            self._set_window(x,y,x,y)
            self._spi.write(bytes([(c565>>8)&0xFF, c565&0xFF]))
            self._cs.on()

    def draw_line(self,x0,y0,x1,y1,c565):
        dx=abs(x1-x0); sx=1 if x0<x1 else -1
        dy=-abs(y1-y0); sy=1 if y0<y1 else -1
        err=dx+dy
        while True:
            self.draw_pixel(x0,y0,c565)
            if x0==x1 and y0==y1: break
            e2=2*err
            if e2>=dy: err+=dy; x0+=sx
            if e2<=dx: err+=dx; y0+=sy

    def blit_rgb565(self, x, y, w, h, buf):
        # Robust gegen Fenster-Clipping (unten/rechts). Sendet nur sichtbare Bytes.
        if w <= 0 or h <= 0:
            return

        # Effektives Fenster in logischen Koordinaten berechnen
        x0 = 0 if x < 0 else x
        y0 = 0 if y < 0 else y
        x1 = x + w - 1
        y1 = y + h - 1
        if x1 >= self._w: x1 = self._w - 1
        if y1 >= self._h: y1 = self._h - 1

        w_eff = x1 - x0 + 1
        h_eff = y1 - y0 + 1
        if w_eff <= 0 or h_eff <= 0:
            return

        # Fenster setzen (inkl. Offsets)
        self._set_window(x0, y0, x1, y1)

        mv = buf if isinstance(buf, memoryview) else memoryview(buf)

        # Falls nur unten geclippt wurde (Standardfall), kann ein Prefix gesendet werden
        if w_eff == w and x0 == x:
            # y-Shift (falls y < 0) berücksichtigen
            start_row = max(0, y0 - y)
            row_stride = w * 2
            start_off = start_row * row_stride
            self._spi.write(mv[start_off : start_off + (w * h_eff * 2)])
        else:
            # Allgemeiner Fall (auch rechts/links Clipping): zeilenweise senden
            row_stride = w * 2
            start_row = max(0, y0 - y)
            start_col = max(0, x0 - x)
            for r in range(h_eff):
                off = (start_row + r) * row_stride + start_col * 2
                self._spi.write(mv[off : off + (w_eff * 2)])

        self._cs.on()

    _FONT_5x7 = {
        '0':(0x3E,0x51,0x49,0x45,0x3E),'1':(0x00,0x42,0x7F,0x40,0x00),
        '2':(0x42,0x61,0x51,0x49,0x46),'3':(0x21,0x41,0x45,0x4B,0x31),
        '4':(0x18,0x14,0x12,0x7F,0x10),'5':(0x27,0x45,0x45,0x45,0x39),
        '6':(0x3C,0x4A,0x49,0x49,0x30),'7':(0x01,0x71,0x09,0x05,0x03),
        '8':(0x36,0x49,0x49,0x49,0x36),'9':(0x06,0x49,0x49,0x29,0x1E),
        '%':(0x62,0x64,0x08,0x13,0x23),'V':(0x07,0x38,0x40,0x38,0x07),
        'B':(0x7F,0x49,0x49,0x49,0x36),'A':(0x7E,0x11,0x11,0x11,0x7E),
        'T':(0x01,0x01,0x7F,0x01,0x01),'U':(0x3F,0x40,0x40,0x40,0x3F),
        'S':(0x26,0x49,0x49,0x49,0x32),'C':(0x3E,0x41,0x41,0x41,0x22),
        'H':(0x7F,0x08,0x08,0x08,0x7F),'G':(0x3E,0x41,0x49,0x49,0x3A),
        'm':(0x7C,0x04,0x18,0x04,0x78),' ':(0x00,0x00,0x00,0x00,0x00),
        ':':(0x00,0x36,0x36,0x00,0x00),'.':(0x00,0x40,0x60,0x00,0x00),
        '/':(0x40,0x30,0x0C,0x03,0x00),
    }
    def draw_char_5x7(self,x,y,ch,color=WHITE,bg=None,scale=1):
            g=self._FONT_5x7.get(ch, self._FONT_5x7[' ']); w,h=5,7
            for cx in range(w):
                col=g[cx]
                for cy in range(h):
                    if (col>>cy)&1:
                        if scale==1: self.draw_pixel(x+cx,y+cy,color)
                        else: self.fill_rect(x+cx*scale,y+cy*scale,scale,scale,color)
                    elif bg is not None:
                        if scale==1: self.draw_pixel(x+cx,y+cy,bg)
                        else: self.fill_rect(x+cx*scale,y+cy*scale,scale,scale,bg)
    def draw_text_5x7(self,x,y,text,color=WHITE,bg=None,scale=1,spacing=1):
            cx=x; step=(5*scale)+spacing
            for ch in text:
                self.draw_char_5x7(cx,y,ch,color,bg,scale); cx+=step
    def draw_text(self, x, y, s, color=WHITE, bg=None, face=None, style=None,
                      shadow=None, outline=None):
            return self.text(x, y, s, color=color, bg=bg, face=face, style=style,
                             shadow=shadow, outline=outline)
    def measure_text_5x7(self,text,scale=1,spacing=1):
            return len(text)*((5*scale)+spacing)-spacing, 7*scale

        # ---- Externe Font-API (Roboto & Co. aus /fonts) ----
    def set_default_font(self, face_module, style=None):
            """face_module: z.B. fonts.roboto; style: z.B. fonts.roboto.medium"""
            self._default_face  = face_module
            self._default_style = style or (face_module.medium if face_module else None)

    def text(self, x, y, s, color=WHITE, bg=None, face=None, style=None,
                 shadow=None, outline=None):
            """
            Zeichnet Text mit einem externen Fontmodul (z.B. fonts.roboto).
            Fällt auf eingebauten 5x7-Font zurück, falls kein Modul vorhanden.
            """
            f = face or self._default_face
            st = style or self._default_style
            if f and hasattr(f, 'draw_text'):
                # Roboto & Co. erwarten: (disp, x, y, text, color, bg, font=style, shadow=..., outline=...)
                f.draw_text(self, x, y, s, color=color, bg=bg, font=st,
                            shadow=shadow, outline=outline)
            else:
                # Fallback: 5x7
                sc = 2  # sinnvoller Default
                self.draw_text_5x7(x, y, s, color=color, bg=bg, scale=sc, spacing=1)

    def text_right(self, x_right, y, s, color=WHITE, bg=None, face=None, style=None,
                       shadow=None, outline=None):
            f = face or self._default_face
            st = style or self._default_style
            if f and hasattr(f, 'measure_text') and hasattr(f, 'draw_text'):
                w, _ = f.measure_text(s, font=st)
                f.draw_text(self, x_right - w, y, s, color=color, bg=bg, font=st,
                            shadow=shadow, outline=outline)
            else:
                # Fallback: 5x7, scale=2
                w, _ = self.measure_text_5x7(s, scale=2, spacing=1)
                self.draw_text_5x7(x_right - w, y, s, color=color, bg=bg, scale=2, spacing=1)

    def measure_text(self, s, face=None, style=None):
            f = face or self._default_face
            st = style or self._default_style
            if f and hasattr(f, 'measure_text'):
                return f.measure_text(s, font=st)
            # Fallback 5x7, scale=2
            return self.measure_text_5x7(s, scale=2, spacing=1)

    # ---- Factory ----
def create_display(power_on=True, **kwargs):
    if power_on:
        try:
            pwr = create_power()
            if hasattr(pwr, "power_on_lcd"):
                pwr.power_on_lcd()
        except Exception as e:
            log_warn("AXP2101 LCD Power-On fehlgeschlagen: %r" % e)
    return ST7789Display(**kwargs)