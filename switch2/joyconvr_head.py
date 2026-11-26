"""
Camera pela cabeca (webcam).

PRIVACIDADE (importante):
  * a camera so liga quando a pessoa aperta "Ligar" na aba Cabeca, depois de dar permissao;
  * cada imagem da camera e analisada NA MEMORIA e jogada fora na hora -- nada e salvo;
  * nada vai para a internet: o unico envio e o angulo da cabeca (dois numeros) para o driver
    do SteamVR, no proprio computador (127.0.0.1);
  * a janela nunca mostra a imagem da camera, so os pontos do rosto que o programa encontrou.

Como funciona: o MediaPipe (Google, roda no PC) acha 478 pontos do rosto e para onde a cabeca
esta virada. A visao do jogo gira MAIS que a cabeca (multiplicador), para a pessoa continuar
olhando o monitor.
"""

import math
import os
import socket
import struct
import sys
import threading
import time

import joyconvr_switch2 as bridge

DEFAULTS = {
    "consent": False,       # a pessoa ja aceitou o aviso de privacidade
    "enabled": False,       # ligar sozinho ao abrir a janela
    "camera": 0,
    "yaw_mult": 3.0,        # 10 graus de cabeca -> 30 graus de visao
    "pitch_mult": 2.0,
    "deadzone": 3.0,        # graus de cabeca ignorados perto do centro
    "smooth": 0.5,          # 0 = sem suavizar, 0.9 = bem suave (mais lento)
    "pitch_on": True,       # tambem olhar para cima/baixo
    "invert_yaw": False,
    "invert_pitch": False,
}


def settings():
    h = bridge.SETTINGS.setdefault("head", {})
    for k, v in DEFAULTS.items():
        h.setdefault(k, v)
    return h


def model_path():
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "models", "face_landmarker.task")


class HeadTracker:
    TARGET_FPS = 20          # suficiente para a visao e deixa CPU para o jogo

    def __init__(self):
        self.lock = threading.Lock()
        self.thread = None
        self.stop_flag = threading.Event()
        self.points = []         # [(x, y)] 0..1, do ultimo rosto encontrado (para desenhar)
        self.head = (0.0, 0.0)   # graus da cabeca (relativo ao centro)
        self.view = (0.0, 0.0)   # graus da visao enviados ao driver
        self.fps = 0.0
        self.status = "desligada"
        self.base = None
        self.out = [0.0, 0.0]
        self.recenter_flag = False
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    # --- controle
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self):
        if self.running():
            return
        self.stop_flag.clear()
        self.base = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_flag.set()

    def recenter(self):
        self.recenter_flag = True

    def snapshot(self):
        with self.lock:
            return list(self.points), self.head, self.view, self.fps, self.status

    # --- trabalho
    def _set(self, **kw):
        with self.lock:
            for k, v in kw.items():
                setattr(self, k, v)

    def _run(self):
        self._set(status="ligando a câmera...")
        try:
            import cv2
            import numpy as np
            import mediapipe as mp
            from mediapipe.tasks import python as mpp
            from mediapipe.tasks.python import vision
        except Exception as e:  # noqa: BLE001
            self._set(status=f"erro carregando o rastreador: {e}")
            return
        cfg = settings()
        cap = cv2.VideoCapture(int(cfg.get("camera", 0)), cv2.CAP_DSHOW)
        if not cap.isOpened():
            self._set(status="não achei a webcam (veja se outro programa está usando)")
            return
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        try:
            opts = vision.FaceLandmarkerOptions(
                base_options=mpp.BaseOptions(model_asset_path=model_path()),
                running_mode=vision.RunningMode.VIDEO, num_faces=1,
                output_facial_transformation_matrixes=True)
            lm = vision.FaceLandmarker.create_from_options(opts)
        except Exception as e:  # noqa: BLE001
            cap.release()
            self._set(status=f"erro no rastreador: {e}")
            return
        self._set(status="procurando seu rosto...")
        t0 = time.perf_counter()
        last_ts = -1
        frames, fps_t = 0, time.perf_counter()
        lost_since = None
        try:
            while not self.stop_flag.is_set():
                loop_start = time.perf_counter()
                ok, frame = cap.read()
                if not ok:
                    self._set(status="a webcam parou de mandar imagem")
                    time.sleep(0.2)
                    continue
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                del frame                              # a imagem nao fica guardada
                ts = int((time.perf_counter() - t0) * 1000)
                if ts <= last_ts:
                    ts = last_ts + 1
                last_ts = ts
                res = lm.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb)), ts)
                del rgb
                frames += 1
                if time.perf_counter() - fps_t >= 1.0:
                    self._set(fps=frames / (time.perf_counter() - fps_t))
                    frames, fps_t = 0, time.perf_counter()
                if res.face_landmarks and res.facial_transformation_matrixes:
                    lost_since = None
                    R = np.array(res.facial_transformation_matrixes[0])[:3, :3]
                    yaw = math.degrees(math.atan2(R[0, 2], R[2, 2]))      # + = pessoa virou para a esquerda
                    pitch = math.degrees(math.asin(max(-1.0, min(1.0, R[1, 2]))))  # + = olhou para cima
                    pts = [(p.x, p.y) for p in res.face_landmarks[0]]
                    self._update_view(yaw, pitch)
                    self._set(points=pts, status="rastreando ✓")
                else:
                    if lost_since is None:
                        lost_since = time.perf_counter()
                    if time.perf_counter() - lost_since > 0.5:
                        self._set(points=[], status="não estou vendo seu rosto")
                spare = 1.0 / self.TARGET_FPS - (time.perf_counter() - loop_start)
                if spare > 0:
                    time.sleep(spare)
        finally:
            cap.release()
            lm.close()
            self._set(points=[], status="desligada", fps=0.0)

    def _update_view(self, yaw, pitch):
        cfg = settings()
        if cfg.get("invert_yaw"):
            yaw = -yaw
        if cfg.get("invert_pitch"):
            pitch = -pitch
        if self.base is None or self.recenter_flag:
            self.base = (yaw, pitch)
            self.recenter_flag = False
            self.out = [0.0, 0.0]
        rel = [yaw - self.base[0], pitch - self.base[1]]
        dz = float(cfg.get("deadzone", 3.0))
        target = []
        for i, v in enumerate(rel):
            v = math.copysign(max(0.0, abs(v) - dz), v)
            mult = float(cfg.get("yaw_mult" if i == 0 else "pitch_mult", 2.0))
            target.append(v * mult)
        if not cfg.get("pitch_on", True):
            target[1] = 0.0
        target[0] = max(-170.0, min(170.0, target[0]))
        target[1] = max(-75.0, min(75.0, target[1]))
        a = 1.0 - max(0.0, min(0.95, float(cfg.get("smooth", 0.5))))
        self.out = [self.out[i] + (target[i] - self.out[i]) * a for i in range(2)]
        self._set(head=(rel[0], rel[1]), view=(self.out[0], self.out[1]))
        pkt = b"JVRK\x01" + struct.pack("<ff", math.radians(self.out[0]), math.radians(self.out[1]))
        try:
            self.sock.sendto(pkt, bridge.DRIVER_ADDR)
        except OSError:
            pass


TRACKER = HeadTracker()
