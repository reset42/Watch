# /lib/lora_sx1262.py — SX1262 LoRa driver für T-Watch-S3 (Pins fest integriert)
# API (für LoraManager):
#   set_irq(cb) -> cb("tx_done", {}) | cb("rx", {"bytes": ...})
#   active(True|False), start_rx(), stop_rx(), send(bytes)
#
# Verhalten: Fehlt die Hardware, bleibt der Treiber passiv (have_hw=False) und blockiert nie die UI.

try:
    import machine, time, micropython
except Exception:
    machine = None
    class _Dummy: pass
    micropython = _Dummy()
    def _noop(*a, **k): pass
    micropython.schedule = _noop

from core.logger import info as log_info, warn as log_warn, debug as log_debug

# --- SX126x Opcodes ---
CMD_SET_SLEEP              = 0x84
CMD_SET_STANDBY            = 0x80  # 0x00 RC, 0x01 XOSC
CMD_SET_FS                 = 0xC1
CMD_SET_TX                 = 0x83  # + 3 bytes timeout
CMD_SET_RX                 = 0x82  # + 3 bytes timeout
CMD_SET_PACKET_TYPE        = 0x8A  # 0x01 LoRa
CMD_SET_RF_FREQUENCY       = 0x86
CMD_SET_TX_PARAMS          = 0x8E
CMD_SET_PA_CONFIG          = 0x95
CMD_SET_REGULATOR_MODE     = 0x96
CMD_SET_BUFFER_BASE        = 0x8F
CMD_SET_MODULATION_PARAMS  = 0x8B
CMD_SET_PACKET_PARAMS      = 0x8C
CMD_GET_IRQ_STATUS         = 0x12
CMD_CLEAR_IRQ_STATUS       = 0x02
CMD_SET_DIO_IRQ_PARAMS     = 0x08
CMD_WRITE_BUFFER           = 0x0E
CMD_READ_BUFFER            = 0x1E
CMD_GET_RX_BUFFER_STATUS   = 0x13

PACKET_TYPE_LORA = 0x01

# IRQ bits
IRQ_TX_DONE   = 0x0001
IRQ_RX_DONE   = 0x0002
IRQ_CRC_ERR   = 0x0040
IRQ_TIMEOUT   = 0x0200

# --- helpers ---
def _pll_steps(freq_hz: int) -> bytes:
    v = int((freq_hz * (1 << 25)) // 32000000)
    return bytes([(v >> 24) & 0xFF, (v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF])

def _bw_code(khz: int) -> int:
    table = {7:0x00, 8:0x00, 10:0x01, 15:0x02, 16:0x02, 20:0x03, 31:0x04, 32:0x04,
             41:0x05, 62:0x06, 63:0x06, 125:0x07, 250:0x08, 500:0x09}
    return table.get(int(khz), 0x07)

def _sf_code(sf: int) -> int:
    return max(5, min(12, int(sf)))

def _cr_code(cr: int) -> int:
    v = max(5, min(8, int(cr))) - 4
    return max(1, min(4, v))

class SX1262:
    def __init__(self):
        self.cb = None
        self.spi = self.cs = self.busy = self.rst = self.dio1 = None
        self.have_hw = False
        self._rx_cont = False
        self._last_len = 0

        if not machine:
            log_warn("LoRa(SX1262): machine module missing")
            return
        try:
            self._init_bus()
            self._hard_reset()
            self._cmd(CMD_SET_STANDBY, b"\x00")  # RC
            self._cmd(CMD_SET_PACKET_TYPE, bytes([PACKET_TYPE_LORA]))
            self._cmd(CMD_SET_REGULATOR_MODE, b"\x01")  # DCDC
            self._cmd(CMD_SET_BUFFER_BASE, b"\x00\x00")
            self._apply_defaults()
            mask = IRQ_TX_DONE | IRQ_RX_DONE | IRQ_TIMEOUT | IRQ_CRC_ERR
            self._set_dio_irq(mask, dio1=mask)
            self.have_hw = True
            log_info("LoRa(SX1262): init ok")
        except Exception as e:
            log_warn("LoRa(SX1262): init failed: %r", e)
            self.have_hw = False

    # ---------- public API ----------
    def set_irq(self, cb): self.cb = cb
    def active(self, on: bool):
        if not self.have_hw: return
        try:
            self._cmd(CMD_SET_STANDBY if on else CMD_SET_SLEEP, b"\x00")
        except Exception as e:
            log_warn("LoRa active() err: %r", e)

    def start_rx(self):
        if not self.have_hw: return
        self._rx_cont = True
        self._clear_irq(0xFFFF)
        self._cmd(CMD_SET_RX, b"\xFF\xFF\xFF")

    def stop_rx(self):
        if not self.have_hw: return
        self._rx_cont = False
        self._cmd(CMD_SET_STANDBY, b"\x00")

    def send(self, data: bytes):
        if not self.have_hw: return
        if not isinstance(data, (bytes, bytearray, memoryview)):
            data = bytes(data or b"")
        plen = len(data) & 0xFF
        self._cmd(CMD_WRITE_BUFFER, bytes([0x00]) + data)
        self._set_packet_params(payload_len=plen)
        self._clear_irq(0xFFFF)
        self._set_dio_irq(IRQ_TX_DONE | IRQ_TIMEOUT, dio1=(IRQ_TX_DONE | IRQ_TIMEOUT))
        self._cmd(CMD_SET_TX_PARAMS, bytes([self._tx_power_code(14), 0x02]))
        self._cmd(CMD_SET_TX, b"\x00\x00\x00")

    # ---------- bus ----------
    def _init_bus(self):
        # feste Pinbelegung für T-Watch S3
        RADIO_SPI_ID   = 1
        RADIO_SPI_BAUD = 8_000_000
        RADIO_SCK   = 3
        RADIO_MISO  = 4
        RADIO_MOSI  = 1
        RADIO_SS    = 5
        RADIO_DIO1  = 9
        RADIO_RST   = 8
        RADIO_BUSY  = 7

        self.spi = machine.SPI(
            RADIO_SPI_ID,
            baudrate=RADIO_SPI_BAUD, polarity=0, phase=0, bits=8,
            sck=machine.Pin(RADIO_SCK,  machine.Pin.OUT),
            mosi=machine.Pin(RADIO_MOSI, machine.Pin.OUT),
            miso=machine.Pin(RADIO_MISO, machine.Pin.IN),
        )
        self.cs   = machine.Pin(RADIO_SS,   machine.Pin.OUT);  self.cs.value(1)
        self.busy = machine.Pin(RADIO_BUSY, machine.Pin.IN)
        self.rst  = machine.Pin(RADIO_RST,  machine.Pin.OUT)
        self.dio1 = machine.Pin(RADIO_DIO1, machine.Pin.IN)
        try:
            self.dio1.irq(trigger=machine.Pin.IRQ_RISING, handler=self._hard_irq)
        except Exception:
            pass

    def _hard_reset(self):
        self.rst.value(0); time.sleep_ms(2)
        self.rst.value(1); time.sleep_ms(10)

    # ---------- command layer ----------
    def _wait_busy(self, tmo_ms=8):
        t0 = time.ticks_ms()
        while self.busy.value():
            if time.ticks_diff(time.ticks_ms(), t0) > tmo_ms:
                break

    def _cmd(self, opcode, payload=b"", read=0):
        self._wait_busy()
        self.cs.value(0)
        self.spi.write(bytes([opcode]))
        if payload: self.spi.write(payload)
        res = b""
        if read:
            self.spi.write(b"\x00")
            res = self.spi.read(read)
        self.cs.value(1)
        self._wait_busy()
        return res

    # ---------- radio params ----------
    def _apply_defaults(self):
        freq = 868100000; sf = 7; bw = 125; cr = 5
        self._cmd(CMD_SET_PA_CONFIG, b"\x04\x07\x00\x01")
        self._cmd(CMD_SET_RF_FREQUENCY, _pll_steps(freq))
        ldro = 0x01 if (bw <= 62 and sf >= 11) else 0x00
        mp = bytes([_sf_code(sf), _bw_code(bw), _cr_code(cr), ldro])
        self._cmd(CMD_SET_MODULATION_PARAMS, mp)
        self._set_packet_params(payload_len=0x40)

    def _set_packet_params(self, payload_len):
        pre = 8
        pp = bytes([
            (pre >> 8) & 0xFF, pre & 0xFF,
            0x01, payload_len & 0xFF,
            0x01, 0x00,
        ])
        self._cmd(CMD_SET_PACKET_PARAMS, pp)

    def _set_dio_irq(self, irq_mask, dio1=0, dio2=0, dio3=0):
        pl = bytes([
            (irq_mask >> 8) & 0xFF, irq_mask & 0xFF,
            (dio1 >> 8) & 0xFF, dio1 & 0xFF,
            (dio2 >> 8) & 0xFF, dio2 & 0xFF,
            (dio3 >> 8) & 0xFF, dio3 & 0xFF,
        ])
        self._cmd(CMD_SET_DIO_IRQ_PARAMS, pl)

    def _clear_irq(self, mask):
        self._cmd(CMD_CLEAR_IRQ_STATUS, bytes([(mask >> 8) & 0xFF, mask & 0xFF]))

    def _get_irq(self):
        st = self._cmd(CMD_GET_IRQ_STATUS, read=2)
        return (st[0] << 8) | st[1] if len(st) == 2 else 0

    def _tx_power_code(self, dbm):
        p = max(-17, min(14, int(dbm)))
        return p + 18

    # ---------- IRQ plumbing ----------
    def _hard_irq(self, _pin):
        try: micropython.schedule(self._soft_irq, 0)
        except Exception: self._soft_irq(0)

    def _soft_irq(self, _arg):
        try:
            irq = self._get_irq()
            if not irq: return
            self._clear_irq(irq)
            if irq & IRQ_TX_DONE and self.cb:
                try: self.cb("tx_done", {})
                except Exception: pass
            if (irq & IRQ_RX_DONE) and not (irq & IRQ_CRC_ERR):
                st = self._cmd(CMD_GET_RX_BUFFER_STATUS, read=2)
                if len(st) == 2:
                    length, start = st[0], st[1]
                    data = self._read_buffer(start, length) if length > 0 else b""
                    if self.cb and data is not None:
                        try: self.cb("rx", {"bytes": data})
                        except Exception: pass
                if self._rx_cont:
                    self._cmd(CMD_SET_RX, b"\xFF\xFF\xFF")
            if irq & IRQ_TIMEOUT and self._rx_cont:
                self._cmd(CMD_SET_RX, b"\xFF\xFF\xFF")
        except Exception as e:
            log_warn("LoRa(SX1262) irq err: %r", e)

    def _read_buffer(self, start, length):
        self._wait_busy()
        self.cs.value(0)
        try:
            self.spi.write(bytes([CMD_READ_BUFFER, start & 0xFF, 0x00]))
            buf = self.spi.read(length)
        finally:
            self.cs.value(1)
            self._wait_busy()
        return buf
