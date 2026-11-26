"""
JoyconVR - Joy-Con 1 (Switch 1) na janela.

O Joy-Con 1 conecta pelo Bluetooth do Windows (como antes). Esta parte da janela le ele
pelo HID, aplica os mesmos ajustes do Joy-Con 2 (sensibilidade, zona morta, trocar maos...)
e manda para o driver pelo mesmo caminho (UDP 127.0.0.1:27760), marcado como "Joy-Con 1".

Se a janela estiver fechada, o driver continua lendo o Joy-Con 1 sozinho, como sempre fez.
"""

import json
import os
import socket
import threading
import time

import joyconvr_switch2 as bridge

try:
    import hid  # pacote "hidapi"
except Exception:  # noqa: BLE001
    hid = None

NINTENDO = 0x057E
PIDS = {0x2006: 0, 0x2007: 1}        # Joy-Con (L) / (R) do Switch 1
NAMES = {0: "Joy-Con 1 ESQUERDO", 1: "Joy-Con 1 DIREITO"}
RUMBLE_NEUTRAL = bytes([0x00, 0x01, 0x40, 0x40, 0x00, 0x01, 0x40, 0x40])
IMU_FRAME_US = 5000                   # o Joy-Con 1 manda 3 amostras por pacote, ~200 por segundo


def _read_driver_json(app_dir, name):
    try:
        with open(os.path.join(app_dir, "joyconvr", "config", name), encoding="utf-8") as f:
            text = f.read()
        if name == "config.json":
            import joyconvr_pages as pages
            text = pages.strip_comments(text)
        return json.loads(text)
    except Exception:  # noqa: BLE001
        return {}


class JoyCon1:
    def __init__(self, path, hand, sock, app_dir):
        self.path = path
        self.hand = hand
        self.sock = sock
        self.app_dir = app_dir
        self.dev = None
        self.counter = 0
        self.seq = 0
        self.ts = 0
        self.alive = False
        self.last_report = b""
        cfg = _read_driver_json(app_dir, "config.json")
        hand_cfg = cfg.get("hands", {}).get("left" if hand == 0 else "right", {})
        self.base_invert_y = bool(hand_cfg.get("invert_y", True))
        self.base_invert_x = bool(hand_cfg.get("invert_x", False))
        # calibracao feita no CALIBRAR-ANALOGICOS.bat (joyconcal.json); sem ela, calibra sozinho
        self.cal = bridge.StickCalibration.default()
        self.cal_info = "calibracao automatica"
        c = _read_driver_json(app_dir, "joyconcal.json").get("left" if hand == 0 else "right")
        if isinstance(c, dict) and all(k in c for k in ("x_center", "x_min", "x_max", "y_center", "y_min", "y_max")):
            self.cal.center = (c["x_center"], c["y_center"])
            self.cal._init_live()
            self.cal.live_center = [c["x_center"], c["y_center"]]
            self.cal.pos = [max(200, c["x_max"] - c["x_center"]), max(200, c["y_max"] - c["y_center"])]
            self.cal.neg = [max(200, c["x_center"] - c["x_min"]), max(200, c["y_center"] - c["y_min"])]
            self.cal_info = f"calibracao do joyconcal.json: centro=({c['x_center']},{c['y_center']})"

    # --- comandos
    def subcommand(self, cmd, args=b""):
        self.counter = (self.counter + 1) & 0x0F
        self.dev.write(bytes([0x01, self.counter]) + RUMBLE_NEUTRAL + bytes([cmd]) + args)
        time.sleep(0.03)

    def open(self):
        self.dev = hid.device()
        self.dev.open_path(self.path)
        self.dev.set_nonblocking(False)
        self.subcommand(0x40, b"\x01")      # liga o giroscopio/acelerometro
        self.subcommand(0x03, b"\x30")      # modo completo (analogico + movimento)
        self.subcommand(0x30, bytes([0x01 if self.hand == 0 else 0x02]))   # luz do jogador
        self.alive = True

    def close(self):
        self.alive = False
        try:
            if self.dev:
                self.dev.close()
        except Exception:  # noqa: BLE001
            pass

    # --- leitura
    def handle(self, r):
        if len(r) < 49 or r[0] != 0x30:
            return
        self.last_report = bytes(r)
        buttons = r[3] | (r[4] << 8) | (r[5] << 16)      # mesma ordem de bits do Joy-Con 2
        o = 6 if self.hand == 0 else 9
        raw = (r[o] | ((r[o + 1] & 0x0F) << 8), (r[o + 1] >> 4) | (r[o + 2] << 4))
        sx, sy = self.cal.apply(raw)
        if self.base_invert_x:
            sx = -sx
        if self.base_invert_y:
            sy = -sy
        sx, sy = bridge.process_stick(self.hand, sx, sy)
        g = float(bridge.SETTINGS.get("gyro_sensitivity", 1.0))
        out = bridge.out_hand(self.hand)
        sent_buttons = bridge.mirror_buttons(buttons) if bridge.SETTINGS.get("swap_hands") else buttons
        self.seq += 1
        if self.seq % 3 == 0:
            bridge.emit("input", hand=self.hand, buttons=buttons, x=sx, y=sy, rx=raw[0], ry=raw[1])
        step_us = self.sample_step_us(r[1])
        for k in range(3):
            p = 13 + k * 12
            val = [int.from_bytes(bytes(r[p + 2 * i:p + 2 * i + 2]), "little", signed=True) for i in range(6)]
            accel, gyro = val[0:3], val[3:6]
            if g != 1.0:
                gyro = [max(-32768, min(32767, int(v * g))) for v in gyro]
            self.ts = (self.ts + step_us) & 0xFFFFFFFF
            pkt = bytearray(bridge.build_packet(out, True, sent_buttons, sx, sy, accel, gyro, self.ts, self.seq))
            pkt[7] = 1                                  # "este pacote e de um Joy-Con 1"
            try:
                self.sock.sendto(bytes(pkt), bridge.DRIVER_ADDR)
            except OSError:
                pass

    def sample_step_us(self, timer):
        """Tempo entre as amostras do giroscopio, em microssegundos.

        O Joy-Con manda 3 amostras por pacote, mas pelo Bluetooth do PC os pacotes chegam
        a ~45-55 por segundo (nao 66), entao cada amostra "vale" mais que 5 ms. O byte 1 do
        pacote e um relogio do proprio Joy-Con (~5 ms por passo): usamos ele, medindo a
        velocidade exata contra o relogio do PC."""
        now = time.perf_counter()
        if not hasattr(self, "t_last"):
            self.t_last, self.t_ticks, self.t_start, self.sec_per_tick = timer, 0, now, 0.005
            return IMU_FRAME_US
        d = (timer - self.t_last) & 0xFF
        self.t_last = timer
        self.t_ticks += d
        elapsed = now - self.t_start
        if elapsed > 2.0 and self.t_ticks > 0 and elapsed < 30:
            rate = elapsed / self.t_ticks
            if 0.003 < rate < 0.008:
                self.sec_per_tick = rate
        if d == 0 or d > 40:                 # pacote repetido ou perdemos muitos: usa o normal
            return IMU_FRAME_US
        return int(min(max(d * self.sec_per_tick / 3 * 1e6, 1000), 25000))

    def run(self):
        try:
            while self.alive:
                r = self.dev.read(64, 200)
                if r:
                    self.handle(r)
        except Exception as e:  # noqa: BLE001
            bridge.log(f"{NAMES[self.hand]}: leitura parou ({e})")
        finally:
            self.close()
            try:
                pkt = bytearray(bridge.build_packet(bridge.out_hand(self.hand), False))
                pkt[7] = 1
                self.sock.sendto(bytes(pkt), bridge.DRIVER_ADDR)
            except OSError:
                pass
            bridge.emit("status", hand=self.hand, state="disconnected", model="Joy-Con 1")
            bridge.log(f"{NAMES[self.hand]} desconectou.")

    def diagnostics(self):
        return "\n".join([
            f"{NAMES[self.hand]}:",
            "  " + self.cal_info,
            f"  pacotes: {self.seq}  inverter base x/y: {self.base_invert_x}/{self.base_invert_y}",
            "  ultimo pacote: " + self.last_report[:49].hex(" "),
        ])


def watch(app_dir, stop_event):
    """Procura Joy-Cons 1 pareados no Windows e le cada um numa thread."""
    if hid is None:
        bridge.log("(Joy-Con 1: biblioteca HID nao disponivel)")
        return
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    active = {}
    warned = False
    while not stop_event.is_set():
        try:
            for d in hid.enumerate(NINTENDO, 0):
                hand = PIDS.get(d.get("product_id"))
                path = d.get("path")
                if hand is None or path in active:
                    continue
                jc = JoyCon1(path, hand, sock, app_dir)
                try:
                    jc.open()
                except Exception as e:  # noqa: BLE001
                    if not warned:
                        bridge.log(f"{NAMES[hand]}: nao consegui abrir ({e})")
                        warned = True
                    continue
                active[path] = jc
                bridge.ACTIVE[f"jc1-{path}"] = jc
                bridge.emit("status", hand=hand, state="connected", model="Joy-Con 1")
                bridge.log(f"{NAMES[hand]} CONECTADO (Bluetooth do Windows).")
                threading.Thread(target=jc.run, daemon=True).start()
            for path, jc in list(active.items()):
                if not jc.alive:
                    active.pop(path, None)
                    bridge.ACTIVE.pop(f"jc1-{path}", None)
        except Exception as e:  # noqa: BLE001
            bridge.log(f"(Joy-Con 1: erro procurando: {e})")
        stop_event.wait(2.0)
