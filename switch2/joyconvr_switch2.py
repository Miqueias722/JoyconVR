"""
JoyconVR - ponte para Joy-Con 2 (Nintendo Switch 2).

Conecta nos Joy-Cons 2 por Bluetooth LE, liga o giroscopio e manda os dados para o
driver JoyconVR do SteamVR (UDP 127.0.0.1:27760).

Protocolo baseado no trabalho de Nadeflore (switch2-controllers, MIT), TommyWabg/CareyScott
(switch2controllerpc, MIT) e no driver Switch 2 do SDL (zlib).
"""

import asyncio
import socket
import struct
import sys
import time

try:
    from bleak import BleakClient, BleakScanner
except ImportError:
    print("Faltou instalar a biblioteca 'bleak'. Use o arquivo INICIAR-JOYCON2.bat.")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Protocol constants
# ---------------------------------------------------------------------------
NINTENDO_MANUFACTURER_ID = 0x0553
NINTENDO_VENDOR_ID = 0x057E
JOYCON2_RIGHT_PID = 0x2066
JOYCON2_LEFT_PID = 0x2067
HANDS = {JOYCON2_LEFT_PID: 0, JOYCON2_RIGHT_PID: 1}
HAND_NAMES = {0: "Joy-Con 2 ESQUERDO", 1: "Joy-Con 2 DIREITO"}

INPUT_REPORT_UUID = "ab7de9be-89fe-49ad-828f-118f09df7fd2"
COMMAND_WRITE_UUID = "649d4ac9-8eb7-4e6c-af44-1ea54fe5f005"
COMMAND_RESPONSE_UUID = "c765a961-d9d8-4d36-a20a-5315b111836a"

CMD_LEDS, SUB_LEDS_SET_PLAYER = 0x09, 0x07
CMD_MEMORY, SUB_MEMORY_READ = 0x02, 0x04
CMD_PAIR = 0x15
SUB_PAIR_SET_MAC, SUB_PAIR_LTK1, SUB_PAIR_LTK2, SUB_PAIR_FINISH = 0x01, 0x04, 0x02, 0x03
CMD_FEATURE, SUB_FEATURE_INIT, SUB_FEATURE_ENABLE = 0x0C, 0x02, 0x04
FEATURE_MOTION, FEATURE_MOUSE = 0x04, 0x10

# Stick calibration addresses (from SDL's Switch 2 driver): the LEFT and RIGHT sticks
# have different addresses. User calibration starts with the magic bytes B2 A1.
CAL_FACTORY_STICK = {0: 0x0130A8, 1: 0x0130E8}
CAL_USER_STICK = {0: 0x1FC040, 1: 0x1FC080}

PAIR_LTK1 = bytes([0x00, 0xEA, 0xBD, 0x47, 0x13, 0x89, 0x35, 0x42, 0xC6, 0x79, 0xEE, 0x07, 0xF2, 0x53, 0x2C, 0x6C, 0x31])
PAIR_LTK2 = bytes([0x00, 0x40, 0xB0, 0x8A, 0x5F, 0xCD, 0x1F, 0x9B, 0x41, 0x12, 0x5C, 0xAC, 0xC6, 0x3F, 0x38, 0xA0, 0x73])

DRIVER_ADDR = ("127.0.0.1", 27760)
FALLBACK_HOST_MAC = 0xAABBCCDDEEFF

# Rode com --analogico para ver os valores do analogico na janela (para achar problemas).
DEBUG_STICK = "--analogico" in sys.argv


# A interface grafica (joyconvr_gui.py) coloca uma funcao aqui para receber eventos.
listener = None
ACTIVE = {}
LOOP = None          # loop da ponte (para mandar luz/vibracao de outra thread)
EXTEND = False       # braco esticado (o driver avisa)

# Ajustes que a janela muda ao vivo (sem reiniciar o SteamVR).
SETTINGS = {
    "swap_hands": False,          # o Joy-Con esquerdo vira a mao direita e vice-versa
    "gyro_sensitivity": 1.0,      # quanto a mao gira em relacao ao pulso
    "auto_steamvr": False,        # abrir o SteamVR sozinho quando os dois conectarem
    "sticks": [                   # 0 = Joy-Con esquerdo, 1 = direito
        {"invert_x": False, "invert_y": False, "deadzone": 0.10, "sensitivity": 1.0, "curve": "linear"},
        {"invert_x": False, "invert_y": False, "deadzone": 0.10, "sensitivity": 1.0, "curve": "linear"},
    ],
}


def process_stick(hand, x, y):
    """Inverter, zona morta, curva e sensibilidade do analogico."""
    s = SETTINGS["sticks"][hand]
    if s.get("invert_x"):
        x = -x
    if s.get("invert_y"):
        y = -y
    r = (x * x + y * y) ** 0.5
    dz = min(max(float(s.get("deadzone", 0.1)), 0.0), 0.6)
    if r <= dz or r < 1e-6:
        return 0.0, 0.0
    t = (min(r, 1.0) - dz) / (1.0 - dz)
    curve = s.get("curve", "linear")
    if curve == "suave":
        t = t ** 1.8
    elif curve == "rapida":
        t = t ** 0.6
    t = min(1.0, t * float(s.get("sensitivity", 1.0)))
    k = t / r
    return max(-1.0, min(1.0, x * k)), max(-1.0, min(1.0, y * k))


def out_hand(hand):
    return 1 - hand if SETTINGS.get("swap_hands") else hand


# Ao trocar as maos, cada botao vira o "irmao" do outro lado (ZL<->ZR, L<->R, setas<->ABXY...)
# para o gatilho continuar sendo o gatilho.
_MIRROR = [
    (0x800000, 0x80),      # ZL <-> ZR
    (0x400000, 0x40),      # L <-> R
    (0x100, 0x200),        # - <-> +
    (0x2000, 0x1000),      # Captura <-> Home
    (0x800, 0x400),        # clique analogico L <-> R
    (0x20000, 0x2),        # cima <-> X
    (0x10000, 0x4),        # baixo <-> B
    (0x80000, 0x1),        # esquerda <-> Y
    (0x40000, 0x8),        # direita <-> A
    (0x200000, 0x20),      # SL <-> SL
    (0x100000, 0x10),      # SR <-> SR
]


def mirror_buttons(b):
    out = b & ~0xFFFFFF | (b & 0x4000)   # bits fora da lista ficam (C fica como C)
    for a, c in _MIRROR:
        if b & a:
            out |= c
        if b & c:
            out |= a
    return out


def emit(kind, **data):
    if listener is not None:
        try:
            listener(kind, data)
        except Exception:  # noqa: BLE001
            pass


def log(msg):
    line = time.strftime("[%H:%M:%S] ") + msg
    if listener is None:
        print(line, flush=True)
    emit("log", text=line)


def u(data, signed=False):
    return int.from_bytes(data, "little", signed=signed)


def stick_xy(data3):
    v = u(data3)
    return v & 0xFFF, v >> 12


class StickCalibration:
    def __init__(self, data):
        self.center = stick_xy(data[0:3])
        self.max = stick_xy(data[3:6])
        self.min = stick_xy(data[6:9])
        # Sanity: fall back to defaults if the controller returned garbage
        if not (1500 < self.center[0] < 2600 and 1500 < self.center[1] < 2600):
            self.center = (2048, 2048)
        self.max = tuple(m if 300 < m < 2048 else 1400 for m in self.max)
        self.min = tuple(m if 300 < m < 2048 else 1400 for m in self.min)

    @staticmethod
    def default():
        c = StickCalibration.__new__(StickCalibration)
        c.center, c.max, c.min = (2048, 2048), (1400, 1400), (1400, 1400)
        return c

    # The calibration read from the controller is not always right (one axis can end up
    # stuck or crooked). So we also calibrate live: the center is measured from the first
    # reports while the stick is at rest, and the range grows to whatever the player
    # actually reaches.
    REST_SAMPLES = 25
    START_RANGE = 1000

    def _init_live(self):
        self.rest = []
        self.live_center = None
        self.pos = [self.START_RANGE, self.START_RANGE]
        self.neg = [self.START_RANGE, self.START_RANGE]
        self.recent = []

    # Correcao automatica de "drift": analogico solto fica MUITO parado (varia 2-3 numeros),
    # dedo segurando treme bem mais. Se ele ficar parado perto do meio por ~2 s, esse ponto
    # passa a ser o meio. Assim um analogico que nao volta 100% para o centro nao fica
    # andando/virando a camera sozinho.
    REST_WINDOW = 90          # ~2 s de pacotes
    REST_MAX_SPREAD = 14      # variacao maxima (numeros brutos) para contar como "solto"
    REST_MAX_OFFSET = 0.25    # so corrige se estiver perto do meio (nao quando a pessoa segura)

    def _follow_rest(self, raw):
        if self.live_center is None:
            return
        self.recent.append(raw)
        if len(self.recent) > self.REST_WINDOW:
            self.recent.pop(0)
        if len(self.recent) < self.REST_WINDOW:
            return
        for i in range(2):
            vals = [r[i] for r in self.recent]
            if max(vals) - min(vals) > self.REST_MAX_SPREAD:
                return
        for i in range(2):
            mean = sum(r[i] for r in self.recent) / len(self.recent)
            off = mean - self.live_center[i]
            span = self.pos[i] if off >= 0 else self.neg[i]
            if abs(off) / max(span, 1) > self.REST_MAX_OFFSET:
                return
        for i in range(2):
            mean = sum(r[i] for r in self.recent) / len(self.recent)
            self.live_center[i] += (mean - self.live_center[i]) * 0.2   # ajusta devagar

    def apply(self, raw):
        if not hasattr(self, "rest"):
            self._init_live()
        if self.live_center is None:
            self.rest.append(raw)
            if len(self.rest) < self.REST_SAMPLES:
                return [0.0, 0.0]
            avg = [sum(r[i] for r in self.rest) / len(self.rest) for i in range(2)]
            spread = max(max(r[i] for r in self.rest) - min(r[i] for r in self.rest) for i in range(2))
            if spread < 150 and all(1400 < a < 2700 for a in avg):
                self.live_center = avg            # stick was still: trust what we measured
            else:
                self.live_center = list(self.center)  # stick was being moved: use the controller's
            if DEBUG_STICK:
                log(f"centro do analogico: medido={[round(a) for a in avg]} "
                    f"do controle={list(self.center)} usando={[round(c) for c in self.live_center]}")
        self._follow_rest(raw)
        out = []
        for i in range(2):
            v = raw[i] - self.live_center[i]
            if v >= 0:
                self.pos[i] = max(self.pos[i], v)
                out.append(min(1.0, v / self.pos[i]))
            else:
                self.neg[i] = max(self.neg[i], -v)
                out.append(max(-1.0, v / self.neg[i]))
        return out


STICK_SLOTS = (10, 13)   # byte offsets of the two stick fields in the input report


def parse_report(data, slot, cal):
    """Returns (buttons, stick_x, stick_y, accel[3], gyro[3], timestamp_us) or None."""
    if len(data) < 60:
        return None
    buttons = u(data[4:8])
    o = STICK_SLOTS[slot]
    raw_stick = stick_xy(data[o:o + 3])
    sx, sy = cal.apply(raw_stick)
    timestamp = u(data[42:46])
    accel = [u(data[48 + 2 * i:50 + 2 * i], True) for i in range(3)]
    gyro = [u(data[54 + 2 * i:56 + 2 * i], True) for i in range(3)]
    return buttons, sx, sy, accel, gyro, timestamp


def build_packet(hand, connected, buttons=0, sx=0.0, sy=0.0, accel=(0, 0, 0), gyro=(0, 0, 0), timestamp=0, seq=0):
    return struct.pack("<4sBBBBIff3h3hII", b"JC2V", 1, hand, 1 if connected else 0, 0,
                       buttons & 0xFFFFFFFF, sx, sy, *accel, *gyro,
                       timestamp & 0xFFFFFFFF, seq & 0xFFFFFFFF)


async def get_host_mac():
    try:
        from winrt.windows.devices.bluetooth import BluetoothAdapter
        adapter = await BluetoothAdapter.get_default_async()
        if adapter is not None and adapter.bluetooth_address:
            return int(adapter.bluetooth_address)
    except Exception as e:  # noqa: BLE001
        log(f"(aviso) nao consegui ler o endereco Bluetooth do PC: {e}")
    return FALLBACK_HOST_MAC


class JoyCon2:
    def __init__(self, device, hand, host_mac, sock, on_gone):
        self.device = device
        self.hand = hand
        self.host_mac = host_mac
        self.sock = sock
        self.on_gone = on_gone
        self.client = None
        self.response = None
        self.cal = StickCalibration.default()
        self.seq = 0
        self.alive = False

    async def command(self, cmd, sub, payload=b"", timeout=3.0):
        # um comando por vez (a resposta chega num lugar so)
        if not hasattr(self, "cmd_lock"):
            self.cmd_lock = asyncio.Lock()
        async with self.cmd_lock:
            return await self._command(cmd, sub, payload, timeout)

    async def _command(self, cmd, sub, payload=b"", timeout=3.0):
        buf = bytes([cmd, 0x91, 0x01, sub, 0x00, len(payload), 0x00, 0x00]) + payload
        self.response = asyncio.get_running_loop().create_future()
        await self.client.write_gatt_char(COMMAND_WRITE_UUID, buf)
        resp = await asyncio.wait_for(self.response, timeout)
        if len(resp) < 8 or resp[0] != cmd or resp[1] != 0x01:
            raise RuntimeError(f"resposta inesperada ao comando {cmd:#04x}: {bytes(resp).hex()}")
        return bytes(resp[8:])

    async def read_memory(self, length, address):
        data = await self.command(CMD_MEMORY, SUB_MEMORY_READ,
                                  bytes([length, 0x7E, 0, 0]) + address.to_bytes(4, "little"))
        if data[0] != length or u(data[4:8]) != address:
            raise RuntimeError("leitura de memoria inesperada")
        return data[8:]

    async def read_calibration(self):
        try:
            user = await self.read_memory(0x0B, CAL_USER_STICK[self.hand])
            if user[0] == 0xB2 and user[1] == 0xA1:
                data, source = user[2:], "do usuario"
            else:
                data, source = await self.read_memory(0x0B, CAL_FACTORY_STICK[self.hand]), "de fabrica"
            self.cal = StickCalibration(data)
            self.cal_info = f"calibracao {source}: centro={self.cal.center} max={self.cal.max} min={self.cal.min}"
            log(f"{HAND_NAMES[self.hand]}: {self.cal_info}")
        except Exception as e:  # noqa: BLE001
            log(f"(aviso) {HAND_NAMES[self.hand]}: usando calibracao padrao do analogico ({e})")

    async def pair(self):
        mac = self.host_mac.to_bytes(6, "little")
        await self.command(CMD_PAIR, SUB_PAIR_SET_MAC, b"\x00\x02" + mac + mac)
        await self.command(CMD_PAIR, SUB_PAIR_LTK1, PAIR_LTK1)
        await self.command(CMD_PAIR, SUB_PAIR_LTK2, PAIR_LTK2)
        await self.command(CMD_PAIR, SUB_PAIR_FINISH, b"\x00")

    def on_response(self, _sender, data):
        if self.response is not None and not self.response.done():
            self.response.set_result(bytes(data))

    def pick_stick_slot(self, data):
        """The stick of a single Joy-Con may live in either stick field of the report.
        Watch how much each field moves and switch to the one that actually moves."""
        if not hasattr(self, "slot"):
            self.slot = 0 if self.hand == 0 else 1
            self.slot_min = [[4095, 4095], [4095, 4095]]
            self.slot_max = [[0, 0], [0, 0]]
            self.slot_locked = False
        if self.slot_locked or len(data) < 16:
            return
        moved = []
        for s, o in enumerate(STICK_SLOTS):
            raw = stick_xy(data[o:o + 3])
            for a in range(2):
                self.slot_min[s][a] = min(self.slot_min[s][a], raw[a])
                self.slot_max[s][a] = max(self.slot_max[s][a], raw[a])
            moved.append(sum(self.slot_max[s][a] - self.slot_min[s][a] for a in range(2)))
        other = 1 - self.slot
        if moved[other] > 600 and moved[other] > moved[self.slot] + 400:
            self.slot = other
            self.cal = StickCalibration.default()   # recalibrate on the right field
            log(f"{HAND_NAMES[self.hand]}: analogico encontrado no outro campo, trocando.")
        if moved[self.slot] > 1200:
            self.slot_locked = True

    def base_leds(self):
        return 0x01 if self.hand == 0 else 0x03

    async def set_leds(self, extend):
        try:
            await self.command(CMD_LEDS, SUB_LEDS_SET_PLAYER, bytes([0x0F if extend else self.base_leds(), 0, 0, 0]),
                               timeout=1.0)
        except Exception:  # noqa: BLE001
            pass

    async def vibrate(self, amplitude):
        # Joy-Con 2: "vibracao pronta" (comando 0x0A, sub 0x02, tipos 1-7). Usamos fraca/forte.
        now = time.perf_counter()
        if now - getattr(self, "last_vibe", 0) < 0.2:
            return
        self.last_vibe = now
        preset = 3 if amplitude > 0.6 else (2 if amplitude > 0.3 else 1)
        try:
            await self.command(0x0A, 0x02, bytes([preset, 0, 0, 0]), timeout=1.0)
        except Exception:  # noqa: BLE001
            pass

    def read_battery(self, data):
        if len(data) < 33:
            return
        mv = u(data[31:33])
        if 3000 <= mv <= 4500:   # so aceita valores que fazem sentido (volts de bateria de litio)
            pct = int(max(0, min(100, (mv - 3500) / (4150 - 3500) * 100)))
            emit("battery", hand=self.hand, percent=pct, charging=False)

    def on_input(self, _sender, data):
        data = bytes(data)
        self.pick_stick_slot(data)
        parsed = parse_report(data, self.slot, self.cal)
        if parsed is None:
            return
        buttons, sx, sy, accel, gyro, ts = parsed
        sx, sy = process_stick(self.hand, sx, sy)
        g = float(SETTINGS.get("gyro_sensitivity", 1.0))
        if g != 1.0:
            gyro = [max(-32768, min(32767, int(v * g))) for v in gyro]
        self.seq += 1
        if self.seq % 3 == 0:
            o = STICK_SLOTS[self.slot]
            raw = stick_xy(data[o:o + 3])
            emit("input", hand=self.hand, buttons=buttons, x=sx, y=sy, rx=raw[0], ry=raw[1])
        if DEBUG_STICK and self.seq % 30 == 0:
            o = STICK_SLOTS[self.slot]
            raw = stick_xy(data[o:o + 3])
            log(f"{HAND_NAMES[self.hand]} analogico: bruto X={raw[0]} Y={raw[1]}  ->  X={sx:+.2f} Y={sy:+.2f}")
        self.last_report = data
        if self.seq % 300 == 1:
            self.read_battery(data)
        ts_us = self.timestamp_us(ts)
        try:
            sent_buttons = mirror_buttons(buttons) if SETTINGS.get("swap_hands") else buttons
            self.sock.sendto(build_packet(out_hand(self.hand), True, sent_buttons, sx, sy, accel, gyro, ts_us, self.seq), DRIVER_ADDR)
        except OSError:
            pass

    def timestamp_us(self, ts):
        """The driver integrates the gyro using the time between samples, in microseconds.
        The unit of the Joy-Con 2 clock is not fixed (SDL computes it per controller), and
        Windows delivers Bluetooth packets in bursts, so we measure the clock rate against the
        PC clock and send a clean microsecond timestamp."""
        now = time.perf_counter()
        if not hasattr(self, "ts_last"):
            self.ts_last, self.ts_ticks, self.ts_t0 = ts, 0, now
            self.ts_out, self.sec_per_tick, self.arrival_ema = 0.0, None, 0.008
            self.ts_prev_arrival = now
            return 0
        d_ticks = (ts - self.ts_last) & 0xFFFFFFFF
        self.ts_last = ts
        self.ts_ticks += d_ticks
        gap = now - self.ts_prev_arrival
        self.ts_prev_arrival = now
        self.arrival_ema += (min(gap, 0.05) - self.arrival_ema) * 0.02
        elapsed = now - self.ts_t0
        if elapsed > 1.5 and self.ts_ticks > 0:
            rate = elapsed / self.ts_ticks
            # Keep refining for the first ~20 s, then hold.
            if self.sec_per_tick is None or elapsed < 20:
                self.sec_per_tick = rate
        if self.sec_per_tick is not None and 0 < d_ticks < 0x7FFFFFFF:
            step = d_ticks * self.sec_per_tick
        else:
            step = self.arrival_ema
        step_us = min(max(step * 1e6, 500.0), 50000.0)
        self.ts_out += step_us
        return int(self.ts_out) & 0xFFFFFFFF

    def diagnostics(self):
        lines = [f"{HAND_NAMES[self.hand]}:"]
        lines.append("  " + getattr(self, "cal_info", "calibracao: nao lida"))
        cal = self.cal
        lines.append(f"  centro medido: {getattr(cal, 'live_center', None)}  alcance +: {getattr(cal, 'pos', None)}  alcance -: {getattr(cal, 'neg', None)}")
        if hasattr(self, "slot"):
            lines.append(f"  campo do analogico: {self.slot}  variacao vista: min={self.slot_min} max={self.slot_max}")
        if hasattr(self, "ts_t0"):
            el = time.perf_counter() - self.ts_t0
            lines.append(f"  pacotes: {self.seq} em {el:.1f}s ({self.seq / max(el, 0.001):.0f}/s)  intervalo medio: {self.arrival_ema * 1000:.1f}ms  "
                         f"relogio: {self.sec_per_tick * 1e6 if self.sec_per_tick else 0:.3f} us/tick")
        rep = getattr(self, "last_report", b"")
        lines.append("  ultimo pacote: " + rep[:64].hex(" "))
        return "\n".join(lines)

    def on_disconnect(self, _client):
        self.alive = False
        try:
            self.sock.sendto(build_packet(out_hand(self.hand), False), DRIVER_ADDR)
        except OSError:
            pass
        emit("status", hand=self.hand, state="disconnected")
        log(f"{HAND_NAMES[self.hand]} desconectou. Aperte um botao nele para reconectar.")
        self.on_gone(self)

    async def start(self, needs_pairing):
        self.client = BleakClient(self.device, disconnected_callback=self.on_disconnect)
        await self.client.connect()

        # Ask Windows for a faster connection (Windows 11; ignored elsewhere).
        try:
            from winrt.windows.devices.bluetooth import BluetoothLEPreferredConnectionParameters
            backend = getattr(self.client, "_backend", None)
            requester = getattr(backend, "_requester", None)
            if requester is not None:
                requester.request_preferred_connection_parameters(
                    BluetoothLEPreferredConnectionParameters.throughput_optimized)
        except Exception:  # noqa: BLE001
            pass

        await self.client.start_notify(COMMAND_RESPONSE_UUID, self.on_response)
        await self.read_calibration()
        saved = (SETTINGS.get("cal2") or [None, None])[self.hand]
        if saved:
            import joyconvr_calibrate
            joyconvr_calibrate.apply_manual_cal(self.cal, saved)
            self.cal_info = getattr(self, "cal_info", "") + " | calibracao manual da janela"
        if needs_pairing:
            await self.pair()
            log(f"{HAND_NAMES[self.hand]} pareado com este PC.")
        await self.client.start_notify(INPUT_REPORT_UUID, self.on_input)
        flags = FEATURE_MOTION | FEATURE_MOUSE
        await self.command(CMD_FEATURE, SUB_FEATURE_INIT, bytes([flags, 0, 0, 0]))
        await self.command(CMD_FEATURE, SUB_FEATURE_ENABLE, bytes([flags, 0, 0, 0]))
        try:
            await self.command(CMD_LEDS, SUB_LEDS_SET_PLAYER, bytes([0x01 if self.hand == 0 else 0x03, 0, 0, 0]))
        except Exception:  # noqa: BLE001
            pass
        self.alive = True
        await self.set_leds(EXTEND)
        emit("status", hand=self.hand, state="connected")
        log(f"{HAND_NAMES[self.hand]} CONECTADO e mandando movimento para o SteamVR.")


async def main():
    print("=" * 64)
    print(" JoyconVR - Joy-Con 2")
    print(" Deixe esta janela ABERTA enquanto joga.")
    print("")
    print(" Primeira vez: segure o botaozinho de sincronizar (na lateral")
    print(" do Joy-Con, no trilho) ate as luzes ficarem correndo.")
    print(" Proximas vezes: e so apertar qualquer botao do Joy-Con.")
    print(" NAO mexa nos analogicos enquanto conecta (ele mede o centro).")
    print("=" * 64, flush=True)

    global LOOP
    LOOP = asyncio.get_running_loop()
    host_mac = await get_host_mac()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    active = ACTIVE      # address -> JoyCon2 (shared with the window, for the diagnostics)
    connecting = set()
    warned = set()

    def gone(jc):
        active.pop(jc.device.address, None)

    async def connect(device, hand, needs_pairing):
        jc = None
        try:
            emit("status", hand=hand, state="connecting")
            log(f"Conectando no {HAND_NAMES[hand]}...")
            jc = JoyCon2(device, hand, host_mac, sock, gone)
            active[device.address] = jc
            await jc.start(needs_pairing)
        except Exception as e:  # noqa: BLE001
            active.pop(device.address, None)
            emit("status", hand=hand, state="failed")
            log(f"Nao deu para conectar no {HAND_NAMES[hand]}: {e}. Tente de novo.")
            try:
                if jc is not None and jc.client is not None:
                    await jc.client.disconnect()
            except Exception:  # noqa: BLE001
                pass
        finally:
            connecting.discard(device.address)

    def on_advert(device, adv):
        data = adv.manufacturer_data.get(NINTENDO_MANUFACTURER_ID)
        if not data or len(data) < 16:
            return
        vendor, product = u(data[3:5]), u(data[5:7])
        if vendor != NINTENDO_VENDOR_ID or product not in HANDS:
            return
        if device.address in active or device.address in connecting:
            return
        hand = HANDS[product]
        reconnect_mac = u(data[10:16])
        if reconnect_mac == 0:
            connecting.add(device.address)
            asyncio.ensure_future(connect(device, hand, True))
        elif reconnect_mac == host_mac:
            connecting.add(device.address)
            asyncio.ensure_future(connect(device, hand, False))
        elif device.address not in warned:
            warned.add(device.address)
            emit("status", hand=hand, state="other_host")
            log(f"Vi um {HAND_NAMES[hand]} pareado com outro aparelho (o Switch?). "
                "Segure o botao de sincronizar dele para parear com o PC.")

    async with BleakScanner(on_advert):
        emit("scanning")
        log("Procurando Joy-Cons 2...")
        while True:
            await asyncio.sleep(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:  # noqa: BLE001
        print(f"\nErro: {e}")
        if "bluetooth" in str(e).lower() or "adapter" in str(e).lower():
            print("Confira se o Bluetooth do PC esta ligado.")
