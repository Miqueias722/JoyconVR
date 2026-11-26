"""
Recados do driver para a janela (UDP 127.0.0.1:27761):
  "JVRS" 1 flags            -> estado (bit 0 = braco esticado)  => luzes do Joy-Con 2
  "JVRH" 1 mao amp dur freq -> o jogo pediu vibracao             => vibra o Joy-Con 2

O Joy-Con 1 o proprio driver ja acende e vibra (ele tem o Joy-Con aberto).
"""

import asyncio
import socket
import struct

import joyconvr_switch2 as bridge

PORT = 27761
_last_recenter = None


def _joycon2_for(vr_hand):
    physical = bridge.out_hand(vr_hand)      # se "trocar maos" estiver ligado, a mao e a outra
    for jc in list(bridge.ACTIVE.values()):
        if type(jc).__name__ == "JoyCon2" and jc.hand == physical and getattr(jc, "alive", False):
            return jc
    return None


def _run(coro):
    if bridge.LOOP is not None:
        asyncio.run_coroutine_threadsafe(coro, bridge.LOOP)


def listen(stop_event):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", PORT))
    except OSError as e:
        bridge.log(f"(nao consegui ouvir o driver na porta {PORT}: {e})")
        return
    sock.settimeout(0.5)
    while not stop_event.is_set():
        try:
            data, _ = sock.recvfrom(64)
        except socket.timeout:
            continue
        except OSError:
            continue
        if len(data) >= 6 and data[:4] == b"JVRS" and data[4] == 1:
            if len(data) >= 7:
                global _last_recenter
                if _last_recenter is not None and data[6] != _last_recenter:
                    import joyconvr_head
                    joyconvr_head.TRACKER.recenter()      # toque no Captura zera a cabeca tambem
                _last_recenter = data[6]
            extend = bool(data[5] & 1)
            if extend != bridge.EXTEND:
                bridge.EXTEND = extend
                bridge.emit("extend", on=extend)
                for jc in list(bridge.ACTIVE.values()):
                    if type(jc).__name__ == "JoyCon2" and getattr(jc, "alive", False):
                        _run(jc.set_leds(extend))
        elif len(data) >= 18 and data[:4] == b"JVRH" and data[4] == 1:
            hand = data[5]
            amp, dur, _freq = struct.unpack_from("<fff", data, 6)
            jc = _joycon2_for(hand)
            if jc is not None:
                _run(jc.vibrate(amp))
