"""
JoyconVR - Joy-Con 2: janela bonitinha em vez do prompt de comando.

Roda a ponte (joyconvr_switch2.py) num thread de fundo e mostra o estado de cada
Joy-Con, o analogico ao vivo e os botoes.
"""

import asyncio
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser

import joyconvr_calibrate as calibrate
import joyconvr_pages as pages
import joyconvr_feedback as feedback
import joyconvr_switch1 as switch1
import joyconvr_update as updater
import joyconvr_switch2 as bridge

# ---------------------------------------------------------------------------
# Visual
# ---------------------------------------------------------------------------
BG = "#0e1016"
CARD = "#181b24"
CARD_EDGE = "#252a36"
TEXT = "#eef1f7"
MUTED = "#8a93a6"
FAINT = "#3a4152"
LEFT_COLOR = "#00c3ff"    # azul neon (Joy-Con esquerdo)
RIGHT_COLOR = "#ff4d5e"   # vermelho neon (Joy-Con direito)
OK = "#3ddc84"
WARN = "#ffb547"
BAD = "#ff5b6b"

FONT = "Segoe UI"

STATES = {
    "waiting":      ("Procurando...",            MUTED, "Segure o botãozinho de sincronizar\n(na lateral, no trilho)"),
    "connecting":   ("Conectando...",            WARN,  "Não toque nos analógicos agora"),
    "connected":    ("Conectado",                OK,    "Pronto! Mexa o analógico para testar"),
    "disconnected": ("Desconectou",              BAD,   "Aperte qualquer botão nele\npara reconectar"),
    "failed":       ("Falhou, tentando de novo", BAD,   "Segure o botão de sincronizar de novo"),
    "other_host":   ("Pareado com o Switch",     WARN,  "Desligue o Switch 2 e segure\no botão de sincronizar"),
}


def app_dir():
    """Pasta principal do JoyconVR (onde ficam INSTALAR.bat etc.)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def rounded(canvas, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(pts, smooth=True, **kw)


def mix(c1, c2, t):
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{int(a[i] + (b[i] - a[i]) * t):02x}" for i in range(3))


class Button(tk.Canvas):
    def __init__(self, parent, text, command, primary=False, width=170, height=42):
        super().__init__(parent, width=width, height=height, bg=BG, highlightthickness=0, cursor="hand2")
        self.command = command
        self.base = LEFT_COLOR if primary else CARD
        self.hover = mix(self.base, "#ffffff", 0.15)
        self.fg = "#061018" if primary else TEXT
        self.shape = rounded(self, 1, 1, width - 1, height - 1, 14, fill=self.base,
                             outline="" if primary else CARD_EDGE)
        self.create_text(width / 2, height / 2, text=text, fill=self.fg, font=(FONT, 10, "bold"))
        self.bind("<Enter>", lambda e: self.itemconfig(self.shape, fill=self.hover))
        self.bind("<Leave>", lambda e: self.itemconfig(self.shape, fill=self.base))
        self.bind("<Button-1>", lambda e: self.command())


class HandCard(tk.Canvas):
    W, H = 300, 330

    def __init__(self, parent, hand):
        super().__init__(parent, width=self.W, height=self.H, bg=BG, highlightthickness=0)
        self.hand = hand
        self.color = LEFT_COLOR if hand == 0 else RIGHT_COLOR
        W, H = self.W, self.H
        rounded(self, 2, 2, W - 2, H - 2, 22, fill=CARD, outline=CARD_EDGE)
        self.create_rectangle(24, 22, 30, 50, fill=self.color, outline="")
        self.model = self.create_text(42, 26, anchor="nw", text="Joy-Con", fill=MUTED, font=(FONT, 9))
        self.create_text(42, 40, anchor="nw", text="ESQUERDO" if hand == 0 else "DIREITO",
                         fill=TEXT, font=(FONT, 14, "bold"))

        # status pill
        self.pill = rounded(self, 24, 74, W - 24, 104, 14, fill=BG, outline="")
        self.dot = self.create_oval(38, 84, 48, 94, fill=MUTED, outline="")
        self.status = self.create_text(58, 89, anchor="w", text="", fill=TEXT, font=(FONT, 10, "bold"))

        # stick
        self.cx, self.cy, self.r = W / 2, 190, 58
        cx, cy, r = self.cx, self.cy, self.r
        self.create_oval(cx - r, cy - r, cx + r, cy + r, outline=FAINT, width=2)
        self.create_oval(cx - r * 0.5, cy - r * 0.5, cx + r * 0.5, cy + r * 0.5, outline=CARD_EDGE)
        self.create_line(cx - r, cy, cx + r, cy, fill=CARD_EDGE)
        self.create_line(cx, cy - r, cx, cy + r, fill=CARD_EDGE)
        self.trail = self.create_line(cx, cy, cx, cy, fill=mix(self.color, CARD, 0.5), width=3, capstyle="round")
        self.knob = self.create_oval(cx - 11, cy - 11, cx + 11, cy + 11, fill=FAINT, outline="")
        self.values = self.create_text(cx, cy + r + 14, text="X +0.00   Y +0.00", fill=MUTED, font=("Consolas", 9))

        # buttons led
        self.led = self.create_oval(W - 42, 32, W - 28, 46, fill=FAINT, outline="")
        self.led_label = self.create_text(W - 48, 39, anchor="e", text="botões", fill=MUTED, font=(FONT, 8))
        self.battery = self.create_text(W - 28, 60, anchor="e", text="", fill=MUTED, font=(FONT, 9, "bold"))

        self.hint = self.create_text(W / 2, H - 30, text="", fill=MUTED, font=(FONT, 9), justify="center")
        self.set_state("waiting")

    def set_battery(self, percent, charging):
        if percent is None:
            self.itemconfig(self.battery, text="")
            return
        color = BAD if percent <= 15 else (WARN if percent <= 40 else OK)
        text = f"bateria {percent}%" + (" ⚡" if charging else "")
        if percent <= 15 and not charging:
            text = f"bateria fraca! {percent}%"
        self.itemconfig(self.battery, text=text, fill=color)

    def set_state(self, state):
        label, color, hint = STATES.get(state, STATES["waiting"])
        self.state = state
        self.itemconfig(self.status, text=label)
        self.itemconfig(self.dot, fill=color)
        self.itemconfig(self.hint, text=hint)
        self.itemconfig(self.knob, fill=self.color if state == "connected" else FAINT)
        if state != "connected":
            self.set_input(0, 0.0, 0.0)

    def set_input(self, buttons, x, y, rx=None, ry=None):
        cx, cy, r = self.cx, self.cy, self.r
        px, py = cx + x * r, cy - y * r
        self.coords(self.knob, px - 11, py - 11, px + 11, py + 11)
        self.coords(self.trail, cx, cy, px, py)
        raw = f"   bruto {rx}/{ry}" if rx is not None else ""
        self.itemconfig(self.values, text=f"X {x:+.2f}  Y {y:+.2f}{raw}")
        self.itemconfig(self.led, fill=self.color if buttons else FAINT)
        capture = buttons & 0x2000 or (self.hand == 1 and buttons & 0x4000)
        self.itemconfig(self.led_label, text="Captura!" if capture else "botões",
                        fill=TEXT if capture else MUTED)


class App:
    def __init__(self):
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:  # noqa: BLE001
            pass

        self.events = queue.Queue()
        bridge.listener = lambda kind, data: self.events.put((kind, data))

        self.root = root = tk.Tk()
        root.title("JoyconVR - Joy-Con 2")
        root.configure(bg=BG)
        root.resizable(False, False)
        try:
            root.iconbitmap(default="")
        except Exception:  # noqa: BLE001
            pass

        pad = tk.Frame(root, bg=BG)
        pad.pack(padx=26, pady=10)

        # header
        head = tk.Frame(pad, bg=BG)
        head.pack(fill="x")
        tk.Label(head, text="JoyconVR", bg=BG, fg=TEXT, font=(FONT, 22, "bold")).pack(side="left")
        tk.Label(head, text="  Joy-Con 1 e 2 para SteamVR", bg=BG, fg=MUTED, font=(FONT, 11)).pack(side="left", pady=(10, 0))
        self.top_status = tk.Label(head, text="Iniciando Bluetooth...", bg=BG, fg=MUTED, font=(FONT, 9))
        self.top_status.pack(side="right", pady=(10, 0))
        tk.Label(head, text=f"  v{updater.VERSION}", bg=BG, fg=FAINT, font=(FONT, 9)).pack(side="left", pady=(12, 0))

        # aviso de atualizacao (aparece so quando existe versao nova)
        self.update_bar = tk.Frame(pad, bg="#123a2a", highlightthickness=1, highlightbackground=OK)
        self.update_text = tk.Label(self.update_bar, text="", bg="#123a2a", fg=TEXT, font=(FONT, 10))
        self.update_text.pack(side="left", padx=12, pady=8)
        self.update_btn = tk.Label(self.update_bar, text="Atualizar agora", bg=OK, fg="#061018",
                                   font=(FONT, 10, "bold"), padx=14, pady=4, cursor="hand2")
        self.update_btn.pack(side="right", padx=10, pady=6)
        self.update_btn.bind("<Button-1>", lambda e: self.start_update())
        self.update_info = None

        threading.Thread(target=self.check_update, daemon=True).start()

        pages.load_settings(app_dir())

        # abas
        tabs = tk.Frame(pad, bg=BG)
        tabs.pack(fill="x", pady=(8, 0))
        self.tab_buttons = {}
        for key, label in (("home", "Início"), ("controls", "Controles"), ("calibrate", "Calibrar"), ("settings", "Ajustes")):
            b = tk.Label(tabs, text=label, bg=BG, fg=MUTED, font=(FONT, 10, "bold"), padx=14, pady=6, cursor="hand2")
            b.pack(side="left")
            b.bind("<Button-1>", lambda e, k=key: self.show_page(k))
            self.tab_buttons[key] = b
        self.content = tk.Frame(pad, bg=BG, width=626, height=496)
        self.content.pack(fill="x")
        self.content.pack_propagate(False)
        self.page_home = tk.Frame(self.content, bg=BG)
        self.page_controls = pages.ControlsPage(self.content, app_dir(), self.add_log)
        self.page_settings = pages.SettingsPage(self.content, app_dir(), self.configure)
        self.page_calibrate = calibrate.CalibratePage(self.content, app_dir(), lambda: pages.save_settings(app_dir()))
        self.pages = {"home": self.page_home, "controls": self.page_controls, "calibrate": self.page_calibrate,
                      "settings": self.page_settings}

        # steps
        steps = tk.Canvas(self.page_home, width=626, height=62, bg=BG, highlightthickness=0)
        steps.pack(pady=(8, 10))
        self.steps = steps
        # aviso quando o SteamVR esta usando o driver de outra pasta (ou nenhum)
        self.warn_bar = tk.Frame(self.page_home, bg="#3a1418", highlightthickness=1, highlightbackground=BAD)
        self.warn_text = tk.Label(self.warn_bar, text="", bg="#3a1418", fg=TEXT, font=(FONT, 9),
                                  justify="left", wraplength=470, anchor="w")
        self.warn_text.pack(side="left", padx=12, pady=8)
        warn_btn = tk.Label(self.warn_bar, text="Instalar driver", bg=BAD, fg="#061018",
                            font=(FONT, 9, "bold"), padx=10, pady=4, cursor="hand2")
        warn_btn.pack(side="right", padx=10, pady=6)
        warn_btn.bind("<Button-1>", lambda e: self.install())
        self.warn_shown = None
        rounded(steps, 1, 1, 625, 61, 16, fill=CARD, outline=CARD_EDGE)
        items = ["Desligue o\nSwitch 2", "Segure o botão\nde sincronizar", "Não toque nos\nanalógicos", "Abra o\nSteamVR"]
        for i, t in enumerate(items):
            x = 22 + i * 152
            steps.create_oval(x, 19, x + 24, 43, fill=LEFT_COLOR if i % 2 == 0 else RIGHT_COLOR, outline="")
            steps.create_text(x + 12, 31, text=str(i + 1), fill="#061018", font=(FONT, 10, "bold"))
            steps.create_text(x + 34, 31, anchor="w", text=t, fill=TEXT, font=(FONT, 9))

        # cards
        cards = tk.Frame(self.page_home, bg=BG)
        cards.pack()
        self.cards_frame = cards
        self.cards = [HandCard(cards, 0), HandCard(cards, 1)]
        self.cards[0].pack(side="left")
        tk.Frame(cards, bg=BG, width=26).pack(side="left")
        self.cards[1].pack(side="left")

        # buttons
        bar = tk.Frame(pad, bg=BG)
        bar.pack(fill="x", pady=(10, 0))
        Button(bar, "Abrir SteamVR", self.open_steamvr, primary=True, width=170).pack(side="left")
        tk.Frame(bar, bg=BG, width=12).pack(side="left")
        Button(bar, "Instalar driver", self.install, width=140).pack(side="left")
        tk.Frame(bar, bg=BG, width=12).pack(side="left")
        self.log_btn = Button(bar, "Mensagens", self.toggle_log, width=140)
        self.log_btn.pack(side="left")
        tk.Frame(bar, bg=BG, width=12).pack(side="left")
        Button(bar, "Diagnóstico", self.copy_diagnostics, width=140).pack(side="left")
        self.auto_started = False
        self.show_page("home")

        tk.Label(pad, text="Deixe esta janela aberta enquanto joga. Pode minimizar.",
                 bg=BG, fg=MUTED, font=(FONT, 9)).pack(pady=(6, 0))

        # log (escondido no comeco)
        self.log_frame = tk.Frame(pad, bg=BG)
        self.log = tk.Text(self.log_frame, height=9, width=86, bg=CARD, fg=MUTED, bd=0,
                           font=("Consolas", 9), insertbackground=TEXT, highlightthickness=1,
                           highlightbackground=CARD_EDGE, padx=10, pady=8)
        self.log.pack()
        self.log.configure(state="disabled")
        self.log_visible = False

        root.protocol("WM_DELETE_WINDOW", self.quit)
        threading.Thread(target=self.run_bridge, daemon=True).start()
        self.stop_jc1 = threading.Event()
        threading.Thread(target=switch1.watch, args=(app_dir(), self.stop_jc1), daemon=True).start()
        threading.Thread(target=feedback.listen, args=(self.stop_jc1,), daemon=True).start()
        threading.Thread(target=self.watch_driver, daemon=True).start()
        root.after(30, self.poll)

    # --- bridge thread
    def run_bridge(self):
        try:
            asyncio.run(bridge.main())
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "bluetooth" in msg.lower() or "adapter" in msg.lower() or "radio" in msg.lower():
                msg += "  → Confira se o Bluetooth do PC está ligado."
            self.events.put(("error", {"text": msg}))

    def poll(self):
        latest_input = {}
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == "input":
                    latest_input[data["hand"]] = data
                elif kind == "status":
                    self.cards[data["hand"]].set_state(data["state"])
                    self.cards[data["hand"]].itemconfig(self.cards[data["hand"]].model,
                                                        text=data.get("model", "Joy-Con 2"))
                    connected = data["state"] == "connected"
                    self.page_calibrate.set_model(data["hand"], data.get("model", "Joy-Con 2") if connected else "")
                    self.update_top()
                elif kind == "scanning":
                    self.update_top()
                elif kind == "battery":
                    self.cards[data["hand"]].set_battery(data["percent"], data.get("charging"))
                elif kind == "driver_warn":
                    self.show_warn(data["text"])
                elif kind == "update":
                    self.show_update(data["info"])
                elif kind == "update_progress":
                    self.update_btn.configure(text=f"Baixando... {int(data['p'] * 100)}%")
                elif kind == "update_done":
                    self.update_btn.configure(text="Reiniciando...")
                    self.root.after(300, self.quit)
                elif kind == "update_error":
                    self.updating = False
                    self.update_btn.configure(text="Tentar de novo")
                    self.add_log("Erro na atualização: " + data["text"])
                elif kind == "log":
                    self.add_log(data["text"])
                elif kind == "error":
                    self.add_log("ERRO: " + data["text"])
                    self.top_status.configure(text="Erro no Bluetooth — veja Mensagens", fg=BAD)
                    if not self.log_visible:
                        self.toggle_log()
        except queue.Empty:
            pass
        for hand, d in latest_input.items():
            card = self.cards[hand]
            if card.state != "connected":
                card.set_state("connected")
                self.update_top()
            card.set_input(d["buttons"], d["x"], d["y"], d.get("rx"), d.get("ry"))
            self.page_controls.show_buttons(hand, d["buttons"])
            self.page_calibrate.feed(hand, d.get("rx"), d.get("ry"))
        self.root.after(30, self.poll)

    def update_top(self):
        n = sum(c.state == "connected" for c in self.cards)
        if n == 2 and bridge.SETTINGS.get("auto_steamvr") and not self.auto_started:
            self.auto_started = True
            self.open_steamvr()
        if n == 2:
            self.top_status.configure(text="Os dois conectados — pode abrir o SteamVR", fg=OK)
        elif n == 1:
            self.top_status.configure(text="1 de 2 conectado", fg=WARN)
        else:
            self.top_status.configure(text="Procurando Joy-Cons 2...", fg=MUTED)

    def add_log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def toggle_log(self):
        if self.log_visible:
            self.log_frame.pack_forget()
        else:
            self.log_frame.pack(pady=(12, 0))
        self.log_visible = not self.log_visible

    # --- buttons
    def open_steamvr(self):
        try:
            os.startfile("steam://rungameid/250820")
        except Exception:  # noqa: BLE001
            webbrowser.open("steam://rungameid/250820")

    def run_bat(self, name):
        path = os.path.join(app_dir(), name)
        if not os.path.exists(path):
            self.add_log(f"Não achei {path}")
            if not self.log_visible:
                self.toggle_log()
            return
        subprocess.Popen(["cmd", "/c", path], cwd=app_dir(), creationflags=subprocess.CREATE_NEW_CONSOLE)

    # --- atualizacao automatica
    # --- conferir se o SteamVR carregou o driver DESTA pasta
    def watch_driver(self):
        import subprocess
        import time as _t
        mine = os.path.normcase(os.path.normpath(os.path.join(app_dir(), "joyconvr")))
        log_path = os.path.join(r"C:\Program Files (x86)\Steam", "logs", "vrserver.txt")
        while True:
            text = ""
            try:
                out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq vrserver.exe"], capture_output=True,
                                     text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
                if "vrserver.exe" in out.lower() and os.path.exists(log_path):
                    with open(log_path, encoding="utf-8", errors="replace") as f:
                        lines = f.readlines()[-6000:]
                    start = max((i for i, ln in enumerate(lines) if "[vrserver] watchdogs" in ln or "Startup Complete" in ln
                                 or "VRServer will wait" in ln), default=0)
                    folder = None
                    for ln in lines[start:]:
                        if "[joyconvr] driver folder:" in ln:
                            folder = ln.split("driver folder:", 1)[1].strip()
                    if folder and os.path.normcase(os.path.normpath(folder)) != mine:
                        text = ("O SteamVR está usando o JoyconVR de OUTRA pasta:\n" + folder +
                                "\nFeche o SteamVR e clique em Instalar driver.")
            except Exception:  # noqa: BLE001
                pass
            self.events.put(("driver_warn", {"text": text}))
            _t.sleep(8)

    def show_warn(self, text):
        if text == self.warn_shown:
            return
        self.warn_shown = text
        # o aviso fica no lugar das dicas, na aba Inicio (a janela precisa caber em monitores baixos)
        if text:
            self.warn_text.configure(text=text)
            self.steps.pack_forget()
            self.warn_bar.pack(fill="x", pady=(8, 10), before=self.cards_frame)
            self.show_page("home")
        else:
            self.warn_bar.pack_forget()
            if not self.steps.winfo_ismapped():
                self.steps.pack(pady=(8, 10), before=self.cards_frame)

    def check_update(self):
        try:
            info = updater.check_latest()
        except Exception as e:  # noqa: BLE001  (sem internet etc.: so ignora)
            self.events.put(("log", {"text": f"(nao consegui ver se ha atualizacao: {e})"}))
            return
        if info:
            self.events.put(("update", {"info": info}))

    def show_update(self, info):
        self.update_info = info
        self.update_text.configure(text=f"Versão nova disponível: v{info[0]}  (você tem v{updater.VERSION})")
        self.update_bar.pack(fill="x", pady=(12, 0), after=self.update_bar.master.winfo_children()[0])

    def start_update(self):
        if not self.update_info or getattr(self, "updating", False):
            return
        from tkinter import messagebox
        if not messagebox.askyesno(
                "Atualizar o JoyconVR",
                f"Baixar e instalar a versão v{self.update_info[0]}?\n\n"
                "O SteamVR será fechado (se estiver aberto) e esta janela vai reabrir sozinha.\n"
                "Seus ajustes e a calibração dos analógicos são mantidos."):
            return
        if not getattr(sys, "frozen", False):
            messagebox.showinfo("Atualizar", "A atualização automática só funciona no .exe.")
            return
        self.updating = True
        self.update_btn.configure(text="Baixando...")

        def work():
            try:
                z = updater.download(self.update_info[1], lambda p: self.events.put(
                    ("update_progress", {"p": p})))
                updater.close_steamvr()
                updater.apply_and_restart(z, app_dir(), sys.executable)
                self.events.put(("update_done", {}))
            except Exception as e:  # noqa: BLE001
                self.events.put(("update_error", {"text": str(e)}))
        threading.Thread(target=work, daemon=True).start()

    def show_page(self, key):
        for k, page in self.pages.items():
            page.pack_forget()
            self.tab_buttons[k].configure(fg=MUTED, bg=BG)
        self.pages[key].pack(fill="both", expand=True, pady=(10, 0))
        self.tab_buttons[key].configure(fg=TEXT, bg=CARD)

    def copy_diagnostics(self):
        """Junta tudo que eu preciso para achar um problema e copia para colar no Discord."""
        parts = ["=== JoyconVR diagnostico ==="]
        for jc in list(bridge.ACTIVE.values()):
            try:
                parts.append(jc.diagnostics())
            except Exception as e:  # noqa: BLE001
                parts.append(f"(erro lendo {jc}: {e})")
        if not bridge.ACTIVE:
            parts.append("Nenhum Joy-Con conectado na janela agora.")
        try:
            cfg = open(os.path.join(app_dir(), "joyconvr", "config", "config.json"), encoding="utf-8").read()
            i = cfg.find('"switch2"')
            parts.append("config switch2: " + " ".join(cfg[i:i + 700].split()) if i >= 0 else "config: sem secao switch2")
            i = cfg.find('"stick_hand"')
            parts.append("look: " + cfg[i:i + 30].split(",")[0] if i >= 0 else "")
        except Exception as e:  # noqa: BLE001
            parts.append(f"config: nao li ({e})")
        try:
            logp = r"C:\Program Files (x86)\Steam\logs\vrserver.txt"
            with open(logp, encoding="utf-8", errors="replace") as f:
                lines = [ln.strip() for ln in f if "joyconvr" in ln]
            parts.append("--- driver (ultimas linhas) ---")
            parts.extend(ln[-160:] for ln in lines[-25:])
        except Exception as e:  # noqa: BLE001
            parts.append(f"log do SteamVR: nao li ({e})")
        parts.append("--- mensagens ---")
        parts.append(self.log.get("1.0", "end").strip()[-2500:])
        text = "\n".join(parts)
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()
        try:
            with open(os.path.join(app_dir(), "diagnostico.txt"), "w", encoding="utf-8") as f:
                f.write(text)
        except Exception:  # noqa: BLE001
            pass
        self.top_status.configure(text="Diagnóstico copiado! Cole (Ctrl+V) no Discord", fg=OK)

    def install(self):
        self.run_bat("INSTALAR.bat")

    def configure(self):
        self.run_bat("CONFIGURAR.bat")

    def quit(self):
        self.root.destroy()
        os._exit(0)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    App().run()
