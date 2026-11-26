"""Aba "Calibrar": calibra o analogico com desenhos, sem cmd.

Passos:
  1. escolher o Joy-Con (esquerdo ou direito)
  2. soltar o analogico (mede o meio)
  3. girar o analogico em circulos ate o fim (mede ate onde ele vai)
  4. pronto: salva e mostra o antes/depois para testar

Joy-Con 1: salva no joyconcal.json (o mesmo do CALIBRAR-ANALOGICOS.bat).
Joy-Con 2: salva nos ajustes da janela (joycon2_config.json).
Nos dois casos vale na hora para quem usa a janela.
"""

import json
import math
import os
import time
import tkinter as tk

import joyconvr_switch2 as bridge

BG = "#0e1016"
CARD = "#181b24"
CARD_EDGE = "#252a36"
TEXT = "#eef1f7"
MUTED = "#8a93a6"
FAINT = "#3a4152"
LEFT_COLOR = "#00c3ff"
RIGHT_COLOR = "#ff4d5e"
OK = "#3ddc84"
WARN = "#ffb547"
FONT = "Segoe UI"

SECTORS = 16
RANGE_SCALE = 0.92        # usa 92% do maximo medido, para o "todo pra frente" chegar facil a 1.0


def apply_manual_cal(cal, c):
    """Coloca uma calibracao feita a mao dentro de um StickCalibration da ponte."""
    cal.center = (int(c["x_center"]), int(c["y_center"]))
    cal._init_live()
    cal.live_center = [c["x_center"], c["y_center"]]
    cal.pos = [max(200, c["x_max"] - c["x_center"]), max(200, c["y_max"] - c["y_center"])]
    cal.neg = [max(200, c["x_center"] - c["x_min"]), max(200, c["y_center"] - c["y_min"])]


def save_calibration(app_dir, hand, model, c):
    side = "left" if hand == 0 else "right"
    if model == "Joy-Con 1":
        path = os.path.join(app_dir, "joyconvr", "config", "joyconcal.json")
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:  # noqa: BLE001
            data = {}
        data[side] = {k: int(c[k]) for k in ("x_min", "x_center", "x_max", "y_min", "y_center", "y_max", "deadzone")}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    else:
        cal2 = bridge.SETTINGS.setdefault("cal2", [None, None])
        cal2[hand] = {k: int(c[k]) for k in ("x_min", "x_center", "x_max", "y_min", "y_center", "y_max", "deadzone")}
    # aplica na hora em quem estiver conectado nessa mao
    for jc in list(bridge.ACTIVE.values()):
        if getattr(jc, "hand", None) == hand and hasattr(jc, "cal"):
            is_jc1 = type(jc).__name__ == "JoyCon1"
            if is_jc1 == (model == "Joy-Con 1"):
                apply_manual_cal(jc.cal, c)


class CalibratePage(tk.Frame):
    W = 300

    def __init__(self, parent, app_dir, save_settings):
        super().__init__(parent, bg=BG)
        self.app_dir = app_dir
        self.save_settings = save_settings
        self.models = ["", ""]           # "Joy-Con 1" / "Joy-Con 2" por mao, quando conectado
        self.hand = None
        self.step = "choose"
        self.samples = []
        self.last_raw = None

        left = tk.Frame(self, bg=BG)
        left.pack(side="left", fill="y")
        self.canvas = tk.Canvas(left, width=self.W, height=self.W, bg=CARD, highlightthickness=1,
                                highlightbackground=CARD_EDGE)
        self.canvas.pack()

        right = tk.Frame(self, bg=BG)
        right.pack(side="left", fill="both", expand=True, padx=(20, 0))
        self.step_label = tk.Label(right, text="", bg=BG, fg=MUTED, font=(FONT, 9, "bold"), anchor="w")
        self.step_label.pack(fill="x")
        self.title = tk.Label(right, text="", bg=BG, fg=TEXT, font=(FONT, 17, "bold"), anchor="w",
                              justify="left", wraplength=300)
        self.title.pack(fill="x", pady=(4, 6))
        self.body = tk.Label(right, text="", bg=BG, fg=MUTED, font=(FONT, 11), anchor="w", justify="left",
                             wraplength=300)
        self.body.pack(fill="x")
        self.bar = tk.Canvas(right, width=300, height=14, bg=BG, highlightthickness=0)
        self.bar.pack(anchor="w", pady=(14, 0))
        self.buttons = tk.Frame(right, bg=BG)
        self.buttons.pack(fill="x", pady=(16, 0))
        self.show_choose()

    # ------------------------------------------------------------------ helpers
    def _button(self, text, cmd, color=None, enabled=True):
        b = tk.Label(self.buttons, text=text, bg=(color or CARD) if enabled else CARD,
                     fg=("#061018" if color and enabled else (TEXT if enabled else FAINT)),
                     font=(FONT, 10, "bold"), padx=12, pady=8, cursor="hand2" if enabled else "")
        b.pack(side="left", padx=(0, 8))
        if enabled:
            b.bind("<Button-1>", lambda e: cmd())
        return b

    def _clear_buttons(self):
        for w in self.buttons.winfo_children():
            w.destroy()

    def _progress(self, t, color=OK):
        self.bar.delete("all")
        self.bar.create_rectangle(0, 3, 300, 11, fill=FAINT, outline="")
        if t > 0:
            self.bar.create_rectangle(0, 3, 300 * min(1.0, t), 11, fill=color, outline="")

    def _texts(self, step, title, body):
        self.step_label.configure(text=step)
        self.title.configure(text=title)
        self.body.configure(text=body)

    def _color(self):
        return LEFT_COLOR if self.hand == 0 else RIGHT_COLOR

    # ------------------------------------------------------------------ desenho
    def draw_stick(self, nx, ny, trace=None, sectors=None, ring_color=None):
        c = self.canvas
        c.delete("all")
        cx = cy = self.W / 2
        r = 110
        color = ring_color or self._color()
        if sectors is not None:
            for i in range(SECTORS):
                start = i * 360 / SECTORS
                c.create_arc(cx - r - 14, cy - r - 14, cx + r + 14, cy + r + 14, start=start + 1,
                             extent=360 / SECTORS - 2, style="arc", width=8,
                             outline=color if sectors[i] else FAINT)
        c.create_oval(cx - r, cy - r, cx + r, cy + r, outline=FAINT, width=2)
        c.create_oval(cx - r * 0.5, cy - r * 0.5, cx + r * 0.5, cy + r * 0.5, outline=CARD_EDGE)
        c.create_line(cx - r, cy, cx + r, cy, fill=CARD_EDGE)
        c.create_line(cx, cy - r, cx, cy + r, fill=CARD_EDGE)
        if trace and len(trace) > 1:
            pts = []
            for tx, ty in trace[-400:]:
                pts += [cx + tx * r, cy - ty * r]
            c.create_line(*pts, fill=color, width=2, smooth=True)
        px, py = cx + nx * r, cy - ny * r
        c.create_line(cx, cy, px, py, fill=color, width=3)
        c.create_oval(px - 13, py - 13, px + 13, py + 13, fill=color, outline="")

    def draw_choose(self):
        c = self.canvas
        c.delete("all")
        for hand, x in ((0, 80), (1, 220)):
            color = LEFT_COLOR if hand == 0 else RIGHT_COLOR
            on = bool(self.models[hand])
            c.create_rectangle(x - 34, 70, x + 34, 230, fill=color if on else FAINT, outline="", width=0)
            c.create_oval(x - 18, 100 if hand == 0 else 160, x + 18, 136 if hand == 0 else 196,
                          fill=CARD, outline="")
            c.create_text(x, 255, text="ESQUERDO" if hand == 0 else "DIREITO", fill=TEXT, font=(FONT, 10, "bold"))
            c.create_text(x, 275, text=self.models[hand] or "desconectado", fill=MUTED if on else FAINT,
                          font=(FONT, 9))

    # ------------------------------------------------------------------ passos
    def show_choose(self):
        self.step = "choose"
        self.hand = None
        self._texts("CALIBRAR O ANALÓGICO", "Qual Joy-Con você quer calibrar?",
                    "Calibrar ensina o programa onde fica o meio e até onde o analógico vai. "
                    "Use se o boneco anda sozinho ou não anda até o fim.")
        self._progress(0)
        self._clear_buttons()
        for hand in (0, 1):
            name = "Esquerdo" if hand == 0 else "Direito"
            self._button(name, lambda h=hand: self.start(h),
                         LEFT_COLOR if hand == 0 else RIGHT_COLOR, enabled=bool(self.models[hand]))
        if not any(self.models):
            self.body.configure(text="Conecte um Joy-Con primeiro (veja a aba Início). "
                                     "Depois volte aqui.")
        self.draw_choose()

    def start(self, hand):
        self.hand = hand
        self.model = self.models[hand]
        # no Joy-Con 1 o numero bruto do 'cima' e menor (o driver inverte), entao o desenho inverte tambem
        self.flip = -1.0 if self.model == "Joy-Con 1" else 1.0
        self.step = "center"
        self.samples = []
        self.center_started = time.time()
        self._texts("PASSO 1 DE 2", "Solte o analógico",
                    "Não encoste no analógico.\nDeixe o Joy-Con parado na mesa por 2 segundos.")
        self._clear_buttons()
        self._button("Cancelar", self.show_choose)
        self._progress(0)
        self.draw_stick(0, 0)

    def start_circle(self):
        self.step = "circle"
        self.min = list(self.center)
        self.max = list(self.center)
        self.trace = []
        self.sectors = [False] * SECTORS
        self.full_since = None
        self._texts("PASSO 2 DE 2", "Gire o analógico em círculos",
                    "Empurre até o fim e faça voltas bem devagar, como se desenhasse um círculo.\n"
                    "Cada pedaço do anel acende quando você chega nele.")
        self._clear_buttons()
        self._button("Cancelar", self.show_choose)
        self._progress(0)
        self.draw_stick(0, 0, [], self.sectors)

    def finish(self):
        cx, cy = self.center
        c = {
            "x_center": cx, "y_center": cy,
            "x_max": cx + (self.max[0] - cx) * RANGE_SCALE, "x_min": cx - (cx - self.min[0]) * RANGE_SCALE,
            "y_max": cy + (self.max[1] - cy) * RANGE_SCALE, "y_min": cy - (cy - self.min[1]) * RANGE_SCALE,
            "deadzone": max(40, min(200, self.noise * 1.5)),
        }
        self.result = c
        try:
            save_calibration(self.app_dir, self.hand, self.model, c)
            self.save_settings()
            saved = True
        except Exception as e:  # noqa: BLE001
            saved = False
            self.body.configure(text=f"Não consegui salvar: {e}")
        self.step = "done"
        name = "esquerdo" if self.hand == 0 else "direito"
        if saved:
            note = ("Já está valendo na janela. Se usar o SteamVR sem a janela, feche e abra o SteamVR."
                    if self.model == "Joy-Con 1" else "Já está valendo.")
            self._texts("PRONTO!", f"Analógico {name} calibrado ✓",
                        "Mexa o analógico: a bolinha deve ficar no meio quando você solta "
                        "e encostar na borda quando empurra até o fim.\n\n" + note)
        self._progress(1)
        self._clear_buttons()
        other = 1 - self.hand
        if self.models[other]:
            self._button("Fazer o outro", lambda: self.start(other), LEFT_COLOR if other == 0 else RIGHT_COLOR)
        self._button("Refazer", lambda: self.start(self.hand))
        self._button("Voltar", self.show_choose)

    # ------------------------------------------------------------------ dados ao vivo
    def set_model(self, hand, model):
        self.models[hand] = model or ""
        if self.step == "choose":
            self.show_choose()

    def feed(self, hand, rx, ry):
        if rx is None or hand != self.hand:
            return
        if self.step == "center":
            now = time.time()
            self.samples.append((rx, ry))
            xs = [s[0] for s in self.samples]
            ys = [s[1] for s in self.samples]
            spread = max(max(xs) - min(xs), max(ys) - min(ys))
            if spread > 120:
                self.samples = [(rx, ry)]
                self.center_started = now
                self.body.configure(text="Opa, mexeu! Solte o analógico e não encoste. 🙂", fg=WARN)
            elapsed = now - self.center_started
            self._progress(elapsed / 2.0)
            self.draw_stick(0, 0)
            if elapsed >= 2.0 and len(self.samples) >= 10:
                self.center = (sum(xs) / len(xs), sum(ys) / len(ys))
                self.noise = spread
                self.body.configure(fg=MUTED)
                self.start_circle()
        elif self.step == "circle":
            cx, cy = self.center
            self.min = [min(self.min[0], rx), min(self.min[1], ry)]
            self.max = [max(self.max[0], rx), max(self.max[1], ry)]
            dx = (rx - cx) / max(200, (self.max[0] - cx) if rx >= cx else (cx - self.min[0]))
            dy = (ry - cy) / max(200, (self.max[1] - cy) if ry >= cy else (cy - self.min[1]))
            r = math.hypot(dx, dy)
            far = r > 0.85 and math.hypot(rx - cx, ry - cy) > 600
            if far:
                ang = math.degrees(math.atan2(dy, dx)) % 360
                self.sectors[int(ang / (360 / SECTORS)) % SECTORS] = True
            n = max(1e-6, r)
            self.trace.append((dx / max(1.0, n), self.flip * dy / max(1.0, n)))
            done = sum(self.sectors) / SECTORS
            self._progress(done, self._color())
            self.draw_stick(dx / max(1.0, n), self.flip * dy / max(1.0, n), self.trace, self.sectors)
            if done >= 1.0:
                if self.full_since is None:
                    self.full_since = time.time()
                    self.body.configure(text="Isso! Mais uma volta e pronto...")
                elif time.time() - self.full_since > 1.5:
                    self.finish()
        elif self.step == "done":
            c = self.result
            nx = (rx - c["x_center"]) / ((c["x_max"] - c["x_center"]) if rx >= c["x_center"] else (c["x_center"] - c["x_min"]))
            ny = (ry - c["y_center"]) / ((c["y_max"] - c["y_center"]) if ry >= c["y_center"] else (c["y_center"] - c["y_min"]))
            dz = c["deadzone"] / 2048.0
            if math.hypot(nx, ny) < dz:
                nx = ny = 0.0
            m = max(1.0, math.hypot(nx, ny))
            self.draw_stick(nx / m, self.flip * ny / m, ring_color=OK)
