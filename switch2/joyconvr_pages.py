"""Abas "Controles" e "Ajustes" da janela do JoyconVR (Joy-Con 2)."""

import json
import os
import re
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
FONT = "Segoe UI"

# Botoes do Joy-Con 2: (nome mostrado, nome no driver, bit do Joy-Con 2)
JOYCON_BUTTONS = [
    [("ZL", "zl", 0x800000), ("L", "l", 0x400000), ("−  (menos)", "minus", 0x100),
     ("Clique analógico", "stick", 0x800), ("▲  cima", "up", 0x20000), ("▼  baixo", "down", 0x10000),
     ("◀  esquerda", "left", 0x80000), ("▶  direita", "right", 0x40000), ("SL", "sl", 0x200000),
     ("SR", "sr", 0x100000)],
    [("ZR", "zr", 0x80), ("R", "r", 0x40), ("+  (mais)", "plus", 0x200), ("Home", "home", 0x1000),
     ("Clique analógico", "stick", 0x400), ("A", "a", 0x8), ("B", "b", 0x4), ("X", "x", 0x2),
     ("Y", "y", 0x1), ("SL", "sl", 0x20), ("SR", "sr", 0x10)],
]
FIXED = [("Captura", 0x2000, "modo olhar / centralizar"), ("C", 0x4000, "(livre)")]

# Acoes de VR (controle do Index, que os jogos entendem)
VR_ACTIONS = [
    ("trigger_click", "Gatilho"), ("grip", "Grip (segurar)"), ("a", "Botão A"), ("b", "Botão B"),
    ("thumbstick_click", "Clique do analógico"), ("system", "Menu do SteamVR"),
    ("menu", "Menu (alguns jogos)"), ("x", "Botão X (poucos jogos)"), ("y", "Botão Y (poucos jogos)"),
    ("none", "— nada —"),
]
LABEL_OF = dict(VR_ACTIONS)
VALUE_OF = {v: k for k, v in VR_ACTIONS}


# ---------------------------------------------------------------------------
# config.json do driver (tem comentarios // que o json do Python nao aceita)
# ---------------------------------------------------------------------------
def strip_comments(text):
    out = []
    for line in text.splitlines():
        inside, i = False, 0
        while i < len(line):
            c = line[i]
            if c == '"' and (i == 0 or line[i - 1] != "\\"):
                inside = not inside
            elif c == "/" and not inside and line[i:i + 2] == "//":
                line = line[:i]
                break
            i += 1
        out.append(line)
    return "\n".join(out)


def driver_config_path(app_dir):
    return os.path.join(app_dir, "joyconvr", "config", "config.json")


def read_bindings(app_dir):
    with open(driver_config_path(app_dir), encoding="utf-8") as f:
        cfg = json.loads(strip_comments(f.read()))
    hands = cfg.get("hands", {})
    return [dict(hands.get("left", {}).get("button_bindings", {})),
            dict(hands.get("right", {}).get("button_bindings", {}))]


def write_bindings(app_dir, bindings):
    path = driver_config_path(app_dir)
    with open(path, encoding="utf-8") as f:
        text = f.read()
    pos = text.find('"hands"')
    if pos < 0:
        raise RuntimeError("config.json sem a parte \"hands\"")
    for hand_key, b in (("left", bindings[0]), ("right", bindings[1])):
        h = text.find(f'"{hand_key}"', pos)
        bb = text.find('"button_bindings"', h)
        start = text.find("{", bb)
        end = text.find("}", start)
        if min(h, bb, start, end) < 0:
            raise RuntimeError("nao achei button_bindings no config.json")
        indent = " " * (len(text[:bb]) - len(text[:bb].rstrip(" ")))
        inner = ",\n".join(f'{indent}  "{k}": "{v}"' for k, v in b.items() if v and v != "none")
        block = "{\n" + inner + "\n" + indent + "}"
        text = text[:start] + block + text[end + 1:]
        pos = start + len(block)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def read_number(app_dir, section, key, default):
    """Le "key": numero dentro da secao "section" do config.json do driver."""
    try:
        with open(driver_config_path(app_dir), encoding="utf-8") as f:
            text = f.read()
        s = text.find(f'"{section}"')
        m = re.compile(r'"' + re.escape(key) + r'"\s*:\s*(-?[0-9.]+)').search(text, s if s >= 0 else 0)
        return float(m.group(1)) if m else default
    except Exception:  # noqa: BLE001
        return default


def write_number(app_dir, section, key, value):
    """Troca so o numero, mantendo os comentarios do arquivo. O driver novo le na hora."""
    path = driver_config_path(app_dir)
    with open(path, encoding="utf-8") as f:
        text = f.read()
    s = text.find(f'"{section}"')
    if s < 0:
        raise RuntimeError(f"config.json sem a secao {section}")
    m = re.compile(r'("' + re.escape(key) + r'"\s*:\s*)(-?[0-9.]+)').search(text, s)
    if not m:
        raise RuntimeError(f"config.json sem {key}")
    text = text[:m.start(2)] + f"{value:g}" + text[m.end(2):]
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


# Valores originais, para os controles em "vezes o padrao"
ARM_DEFAULT = (0.28, 0.30)
GYRO1_DEFAULT = 0.070


# ---------------------------------------------------------------------------
# Ajustes da ponte (joycon2_config.json ao lado do .exe)
# ---------------------------------------------------------------------------
def settings_path(app_dir):
    return os.path.join(app_dir, "joycon2_config.json")


def load_settings(app_dir):
    try:
        with open(settings_path(app_dir), encoding="utf-8") as f:
            saved = json.load(f)
        for k in ("swap_hands", "gyro_sensitivity", "auto_steamvr", "cal2"):
            if k in saved:
                bridge.SETTINGS[k] = saved[k]
        for i in range(2):
            if i < len(saved.get("sticks", [])):
                bridge.SETTINGS["sticks"][i].update(saved["sticks"][i])
    except FileNotFoundError:
        pass
    except Exception:  # noqa: BLE001
        pass


def save_settings(app_dir):
    try:
        with open(settings_path(app_dir), "w", encoding="utf-8") as f:
            json.dump(bridge.SETTINGS, f, indent=2)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Aba Controles
# ---------------------------------------------------------------------------
class ControlsPage(tk.Frame):
    def __init__(self, parent, app_dir, on_message):
        super().__init__(parent, bg=BG)
        self.app_dir = app_dir
        self.on_message = on_message
        self.chips = [[], []]
        self.vars = [{}, {}]
        try:
            self.bindings = read_bindings(app_dir)
        except Exception as e:  # noqa: BLE001
            self.bindings = [{}, {}]
            on_message(f"Não consegui ler o config.json do driver: {e}")

        tk.Label(self, text="Aperte um botão no Joy-Con: ele acende aqui. Escolha o que ele faz no VR.",
                 bg=BG, fg=MUTED, font=(FONT, 9)).pack(anchor="w", pady=(0, 8))
        cols = tk.Frame(self, bg=BG)
        cols.pack()
        for hand in (0, 1):
            col = tk.Frame(cols, bg=CARD, highlightthickness=1, highlightbackground=CARD_EDGE)
            col.pack(side="left", padx=(0, 14) if hand == 0 else 0, fill="y")
            color = LEFT_COLOR if hand == 0 else RIGHT_COLOR
            head = tk.Frame(col, bg=CARD)
            head.pack(fill="x", padx=12, pady=(10, 6))
            tk.Frame(head, bg=color, width=5, height=22).pack(side="left")
            tk.Label(head, text=" Joy-Con " + ("ESQUERDO" if hand == 0 else "DIREITO"), bg=CARD, fg=TEXT,
                     font=(FONT, 11, "bold")).pack(side="left")
            tk.Label(head, text="→  VR", bg=CARD, fg=MUTED, font=(FONT, 9)).pack(side="right")
            for name, key, bit in JOYCON_BUTTONS[hand]:
                self._row(col, hand, name, key, bit, color)
            fixed_name, fixed_bit, fixed_text = FIXED[hand]
            row = tk.Frame(col, bg=CARD)
            row.pack(fill="x", padx=12, pady=(1, 10))
            chip = tk.Label(row, text=fixed_name, width=15, anchor="w", bg=BG, fg=MUTED, font=(FONT, 9), padx=6)
            chip.pack(side="left")
            tk.Label(row, text=fixed_text, bg=CARD, fg=MUTED, font=(FONT, 9)).pack(side="left", padx=8)
            self.chips[hand].append((fixed_bit, chip, color))

        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", pady=(10, 0))
        self.status = tk.Label(bar, text="", bg=BG, fg=MUTED, font=(FONT, 9))
        self.status.pack(side="left")
        tk.Button(bar, text="Voltar ao padrão", command=self.reset, bg=CARD, fg=TEXT, bd=0, padx=12, pady=5,
                  activebackground=CARD_EDGE, activeforeground=TEXT, font=(FONT, 9), cursor="hand2").pack(side="right")

    def _row(self, col, hand, name, key, bit, color):
        row = tk.Frame(col, bg=CARD)
        row.pack(fill="x", padx=12, pady=1)
        chip = tk.Label(row, text=name, width=15, anchor="w", bg=BG, fg=TEXT, font=(FONT, 9), padx=6)
        chip.pack(side="left")
        current = self.bindings[hand].get(key, "none")
        var = tk.StringVar(value=LABEL_OF.get(current, LABEL_OF["none"]))
        menu = tk.OptionMenu(row, var, *[label for _, label in VR_ACTIONS],
                             command=lambda _v, h=hand, k=key, vv=var: self.changed(h, k, vv))
        menu.configure(bg=BG, fg=TEXT, activebackground=CARD_EDGE, activeforeground=TEXT, bd=0,
                       highlightthickness=0, font=(FONT, 9), width=19, anchor="w", relief="flat")
        menu["menu"].configure(bg=CARD, fg=TEXT, activebackground=color, activeforeground="#061018", font=(FONT, 9))
        menu.pack(side="left", padx=(8, 0))
        self.vars[hand][key] = var
        self.chips[hand].append((bit, chip, color))

    def changed(self, hand, key, var):
        self.bindings[hand][key] = VALUE_OF.get(var.get(), "none")
        self.save()

    def save(self):
        try:
            write_bindings(self.app_dir, self.bindings)
            self.status.configure(text="Salvo! Feche e abra o SteamVR para valer.", fg=OK)
        except Exception as e:  # noqa: BLE001
            self.status.configure(text=f"Erro ao salvar: {e}", fg="#ff5b6b")

    def reset(self):
        defaults = [
            {"minus": "system", "zl": "trigger_click", "l": "grip", "stick": "thumbstick_click",
             "up": "b", "down": "a", "left": "none", "right": "none"},
            {"plus": "system", "home": "menu", "zr": "trigger_click", "r": "grip", "stick": "thumbstick_click",
             "x": "b", "b": "a", "y": "none", "a": "none"},
        ]
        self.bindings = defaults
        for hand in (0, 1):
            for key, var in self.vars[hand].items():
                var.set(LABEL_OF.get(defaults[hand].get(key, "none")))
        self.save()

    def show_buttons(self, hand, buttons):
        for bit, chip, color in self.chips[hand]:
            on = bool(buttons & bit)
            chip.configure(bg=color if on else BG, fg="#061018" if on else TEXT)


# ---------------------------------------------------------------------------
# Aba Ajustes
# ---------------------------------------------------------------------------
class SettingsPage(tk.Frame):
    def __init__(self, parent, app_dir, open_advanced):
        super().__init__(parent, bg=BG)
        self.app_dir = app_dir
        S = bridge.SETTINGS

        top = tk.Frame(self, bg=CARD, highlightthickness=1, highlightbackground=CARD_EDGE)
        top.pack(fill="x")
        inner = tk.Frame(top, bg=CARD)
        inner.pack(fill="x", padx=14, pady=10)
        tk.Label(inner, text="Geral", bg=CARD, fg=TEXT, font=(FONT, 11, "bold")).grid(row=0, column=0, sticky="w")
        self.swap = tk.BooleanVar(value=S["swap_hands"])
        self._check(inner, "Trocar mãos (use se o analógico ou a mão estiver do lado errado)", self.swap,
                    lambda: self.set("swap_hands", self.swap.get())).grid(row=1, column=0, columnspan=2, sticky="w")
        self.auto = tk.BooleanVar(value=S["auto_steamvr"])
        self._check(inner, "Abrir o SteamVR sozinho quando os dois Joy-Cons conectarem", self.auto,
                    lambda: self.set("auto_steamvr", self.auto.get())).grid(row=2, column=0, columnspan=2, sticky="w")
        tk.Label(inner, text="Sensibilidade do giro da mão", bg=CARD, fg=MUTED, font=(FONT, 9)).grid(
            row=3, column=0, sticky="w", pady=(6, 0))
        self._scale(inner, 0.5, 2.0, 0.05, S["gyro_sensitivity"],
                    lambda v: self.set("gyro_sensitivity", float(v))).grid(row=3, column=1, sticky="w", pady=(6, 0))

        cols = tk.Frame(self, bg=BG)
        cols.pack(fill="x", pady=(12, 0))
        self.stick_vars = []
        for hand in (0, 1):
            s = S["sticks"][hand]
            card = tk.Frame(cols, bg=CARD, highlightthickness=1, highlightbackground=CARD_EDGE)
            card.pack(side="left", padx=(0, 14) if hand == 0 else 0, fill="both", expand=True)
            f = tk.Frame(card, bg=CARD)
            f.pack(fill="both", padx=14, pady=10)
            color = LEFT_COLOR if hand == 0 else RIGHT_COLOR
            h = tk.Frame(f, bg=CARD)
            h.grid(row=0, column=0, columnspan=2, sticky="w")
            tk.Frame(h, bg=color, width=5, height=20).pack(side="left")
            tk.Label(h, text=" Analógico " + ("esquerdo" if hand == 0 else "direito"), bg=CARD, fg=TEXT,
                     font=(FONT, 11, "bold")).pack(side="left")
            tk.Label(f, text="Sensibilidade", bg=CARD, fg=MUTED, font=(FONT, 9)).grid(row=1, column=0, sticky="w")
            self._scale(f, 0.5, 2.0, 0.05, s["sensitivity"],
                        lambda v, i=hand: self.set_stick(i, "sensitivity", float(v))).grid(row=1, column=1)
            tk.Label(f, text="Zona morta", bg=CARD, fg=MUTED, font=(FONT, 9)).grid(row=2, column=0, sticky="w")
            self._scale(f, 0.0, 0.4, 0.01, s["deadzone"],
                        lambda v, i=hand: self.set_stick(i, "deadzone", float(v))).grid(row=2, column=1)
            tk.Label(f, text="Resposta", bg=CARD, fg=MUTED, font=(FONT, 9)).grid(row=3, column=0, sticky="w")
            curve = tk.StringVar(value={"linear": "Normal", "suave": "Suave (precisa)", "rapida": "Rápida"}.get(
                s.get("curve", "linear"), "Normal"))
            om = tk.OptionMenu(f, curve, "Normal", "Suave (precisa)", "Rápida",
                               command=lambda v, i=hand: self.set_stick(
                                   i, "curve", {"Normal": "linear", "Suave (precisa)": "suave", "Rápida": "rapida"}[v]))
            om.configure(bg=BG, fg=TEXT, activebackground=CARD_EDGE, activeforeground=TEXT, bd=0,
                         highlightthickness=0, font=(FONT, 9), width=16, relief="flat")
            om["menu"].configure(bg=CARD, fg=TEXT, activebackground=color, activeforeground="#061018")
            om.grid(row=3, column=1, sticky="w", pady=3)
            ix = tk.BooleanVar(value=s["invert_x"])
            iy = tk.BooleanVar(value=s["invert_y"])
            self._check(f, "Inverter esquerda/direita", ix,
                        lambda i=hand, v=ix: self.set_stick(i, "invert_x", v.get())).grid(row=4, column=0, columnspan=2, sticky="w")
            self._check(f, "Inverter cima/baixo", iy,
                        lambda i=hand, v=iy: self.set_stick(i, "invert_y", v.get())).grid(row=5, column=0, columnspan=2, sticky="w")
            self.stick_vars.append((ix, iy, curve))

        # Ajustes do driver (servem para Joy-Con 1 e 2; o driver novo aplica sem reiniciar)
        drv = tk.Frame(self, bg=CARD, highlightthickness=1, highlightbackground=CARD_EDGE)
        drv.pack(fill="x", pady=(12, 0))
        d = tk.Frame(drv, bg=CARD)
        d.pack(fill="x", padx=14, pady=8)
        tk.Label(d, text="Driver  (Joy-Con 1 e 2)", bg=CARD, fg=TEXT, font=(FONT, 11, "bold")).grid(
            row=0, column=0, columnspan=4, sticky="w")
        arm = read_number(app_dir, "arm", "upper_arm", ARM_DEFAULT[0]) / ARM_DEFAULT[0]
        yaw = read_number(app_dir, "look", "yaw_speed_deg", 140)
        gyro1 = read_number(app_dir, "imu", "gyro_scale_dps", GYRO1_DEFAULT) / GYRO1_DEFAULT
        tk.Label(d, text="Tamanho do braço", bg=CARD, fg=MUTED, font=(FONT, 9)).grid(row=1, column=0, sticky="w")
        self._scale(d, 0.5, 1.8, 0.05, arm, self.set_arm, 160).grid(row=1, column=1, sticky="w")
        tk.Label(d, text="Velocidade da câmera", bg=CARD, fg=MUTED, font=(FONT, 9)).grid(
            row=1, column=2, sticky="w", padx=(12, 0))
        self._scale(d, 0.4, 2.5, 0.05, yaw / 140, self.set_camera, 160).grid(row=1, column=3, sticky="w")
        tk.Label(d, text="Giro (Joy-Con 1)", bg=CARD, fg=MUTED, font=(FONT, 9)).grid(row=2, column=0, sticky="w")
        self._scale(d, 0.5, 2.0, 0.05, gyro1, self.set_gyro1, 160).grid(row=2, column=1, sticky="w")
        elbow = read_number(app_dir, "arm", "elbow_follow", 0.8)
        tk.Label(d, text="Levantar o braço", bg=CARD, fg=MUTED, font=(FONT, 9)).grid(
            row=2, column=2, sticky="w", padx=(12, 0))
        self._scale(d, 0.1, 1.0, 0.05, elbow, self.set_elbow, 160).grid(row=2, column=3, sticky="w")
        self.driver_status = tk.Label(d, text="", bg=CARD, fg=MUTED, font=(FONT, 8))
        self.driver_status.grid(row=3, column=0, columnspan=4, sticky="w")

        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", pady=(10, 0))
        tk.Label(bar, text="Estes ajustes valem na hora, sem reiniciar o SteamVR.", bg=BG, fg=OK,
                 font=(FONT, 9)).pack(side="left")
        tk.Button(bar, text="Config. avançada do driver", command=open_advanced, bg=CARD, fg=TEXT, bd=0, padx=12,
                  pady=5, activebackground=CARD_EDGE, activeforeground=TEXT, font=(FONT, 9),
                  cursor="hand2").pack(side="right")

    def set(self, key, value):
        bridge.SETTINGS[key] = value
        save_settings(self.app_dir)

    def set_stick(self, hand, key, value):
        bridge.SETTINGS["sticks"][hand][key] = value
        save_settings(self.app_dir)

    def _driver(self, writes):
        """Salva no config.json do driver depois que a pessoa para de arrastar."""
        if getattr(self, "_pending", None):
            self.after_cancel(self._pending)

        def go():
            try:
                for section, key, value in writes:
                    write_number(self.app_dir, section, key, value)
                self.driver_status.configure(text="salvo ✓", fg=OK)
            except Exception as e:  # noqa: BLE001
                self.driver_status.configure(text=f"erro: {e}", fg="#ff5b6b")
        self._pending = self.after(400, go)

    def set_arm(self, k):
        self._driver([("arm", "upper_arm", round(ARM_DEFAULT[0] * k, 3)),
                      ("arm", "forearm", round(ARM_DEFAULT[1] * k, 3))])

    def set_camera(self, k):
        self._driver([("look", "yaw_speed_deg", round(140 * k)), ("look", "pitch_speed_deg", round(90 * k))])

    def set_elbow(self, k):
        # quanto o cotovelo acompanha o Joy-Con: maior = a mao sobe mais quando voce aponta pra cima
        self._driver([("arm", "elbow_follow", round(k, 2))])

    def set_gyro1(self, k):
        self._driver([("imu", "gyro_scale_dps", round(GYRO1_DEFAULT * k, 4))])

    def _check(self, parent, text, var, cmd):
        return tk.Checkbutton(parent, text=text, variable=var, command=cmd, bg=CARD, fg=TEXT, selectcolor=BG,
                              activebackground=CARD, activeforeground=TEXT, font=(FONT, 9), bd=0,
                              highlightthickness=0, cursor="hand2")

    def _scale(self, parent, lo, hi, step, value, cmd, width=200):
        return Slider(parent, lo, hi, step, value, cmd, width)


class Slider(tk.Canvas):
    """Controle deslizante desenhado (o do tkinter fica feio no tema escuro)."""
    W, H, PAD = 200, 28, 10

    def __init__(self, parent, lo, hi, step, value, cmd, width=200):
        self.W = width
        super().__init__(parent, width=self.W, height=self.H, bg=CARD, highlightthickness=0, cursor="hand2")
        self.lo, self.hi, self.step, self.cmd = lo, hi, step, cmd
        self.x0, self.x1, self.y = self.PAD, self.W - 52, self.H / 2
        self.create_line(self.x0, self.y, self.x1, self.y, fill=FAINT, width=4, capstyle="round")
        self.fill = self.create_line(self.x0, self.y, self.x0, self.y, fill=LEFT_COLOR, width=4, capstyle="round")
        self.knob = self.create_oval(0, 0, 0, 0, fill=TEXT, outline="")
        self.label = self.create_text(self.W - 4, self.y, anchor="e", fill=TEXT, font=("Consolas", 9))
        self.value = None
        self.set(value, notify=False)
        self.bind("<Button-1>", self._drag)
        self.bind("<B1-Motion>", self._drag)

    def set(self, value, notify=True):
        v = min(max(float(value), self.lo), self.hi)
        v = round(round((v - self.lo) / self.step) * self.step + self.lo, 4)
        if v == self.value:
            return
        self.value = v
        x = self.x0 + (v - self.lo) / (self.hi - self.lo) * (self.x1 - self.x0)
        self.coords(self.fill, self.x0, self.y, x, self.y)
        self.coords(self.knob, x - 7, self.y - 7, x + 7, self.y + 7)
        self.itemconfig(self.label, text=f"{v:.2f}")
        if notify:
            self.cmd(v)

    def _drag(self, e):
        t = (e.x - self.x0) / (self.x1 - self.x0)
        self.set(self.lo + t * (self.hi - self.lo))
