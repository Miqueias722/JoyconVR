"""Aba "Cabeca": liga a webcam (com permissao) e mostra os pontos do rosto, nunca a imagem."""

import tkinter as tk

import joyconvr_head as head

BG = "#0e1016"
CARD = "#181b24"
CARD_EDGE = "#252a36"
TEXT = "#eef1f7"
MUTED = "#8a93a6"
FAINT = "#3a4152"
ACCENT = "#00c3ff"
OK = "#3ddc84"
BAD = "#ff5b6b"
FONT = "Segoe UI"

PRIVACY = (
    "O JoyconVR vai usar a sua webcam só para ver para onde a sua cabeça está virada.\n\n"
    "•  Tudo acontece aqui no seu computador.\n"
    "•  Nenhuma foto ou vídeo é salvo.\n"
    "•  Nada é enviado pela internet — nem para quem fez o programa.\n"
    "•  A tela mostra só os pontinhos do rosto, nunca a imagem da câmera.\n"
    "•  A luz da webcam fica acesa enquanto estiver ligado.\n"
    "•  Você pode desligar quando quiser, nesta mesma aba."
)


class HeadPage(tk.Frame):
    W = 300

    def __init__(self, parent, save_settings):
        super().__init__(parent, bg=BG)
        self.save_settings = save_settings
        cfg = head.settings()

        left = tk.Frame(self, bg=BG)
        left.pack(side="left", fill="y")
        self.canvas = tk.Canvas(left, width=self.W, height=self.W, bg=CARD, highlightthickness=1,
                                highlightbackground=CARD_EDGE)
        self.canvas.pack()
        self.dots = []
        self.readout = tk.Label(left, text="", bg=BG, fg=MUTED, font=("Consolas", 9))
        self.readout.pack(pady=(6, 0))

        right = tk.Frame(self, bg=BG)
        right.pack(side="left", fill="both", expand=True, padx=(18, 0))
        tk.Label(right, text="Câmera pela cabeça", bg=BG, fg=TEXT, font=(FONT, 15, "bold"), anchor="w").pack(fill="x")
        tk.Label(right, text="Vire um pouco a cabeça e a visão do jogo vira bem mais —\n"
                             "assim você continua olhando o monitor.",
                 bg=BG, fg=MUTED, font=(FONT, 9), justify="left", anchor="w").pack(fill="x", pady=(2, 6))
        lock = tk.Label(right, text="🔒 Só no seu PC. Nada é salvo nem enviado.", bg="#10261b", fg=OK,
                        font=(FONT, 9, "bold"), anchor="w", padx=8, pady=4, cursor="hand2")
        lock.pack(fill="x")
        lock.bind("<Button-1>", lambda e: self.show_privacy(info_only=True))

        row = tk.Frame(right, bg=BG)
        row.pack(fill="x", pady=(10, 6))
        self.toggle_btn = tk.Label(row, text="", font=(FONT, 11, "bold"), padx=16, pady=7, cursor="hand2")
        self.toggle_btn.pack(side="left")
        self.toggle_btn.bind("<Button-1>", lambda e: self.toggle())
        tk.Label(row, text="  webcam nº", bg=BG, fg=MUTED, font=(FONT, 9)).pack(side="left")
        self.cam = tk.StringVar(value=str(cfg.get("camera", 0)))
        om = tk.OptionMenu(row, self.cam, "0", "1", "2", command=lambda v: self._set("camera", int(v)))
        om.configure(bg=CARD, fg=TEXT, bd=0, highlightthickness=0, activebackground=CARD_EDGE,
                     activeforeground=TEXT, width=2, font=(FONT, 9))
        om.pack(side="left")

        grid = tk.Frame(right, bg=BG)
        grid.pack(fill="x")
        self._slider(grid, 0, "Quanto a visão gira", 1.0, 6.0, 0.25, cfg["yaw_mult"], "yaw_mult", "x")
        self._slider(grid, 1, "Pra cima/baixo", 1.0, 4.0, 0.25, cfg["pitch_mult"], "pitch_mult", "x")
        self._slider(grid, 2, "Zona morta", 0.0, 10.0, 0.5, cfg["deadzone"], "deadzone", "°")
        self._slider(grid, 3, "Suavizar", 0.0, 0.9, 0.05, cfg["smooth"], "smooth", "")
        checks = tk.Frame(right, bg=BG)
        checks.pack(fill="x", pady=(4, 0))
        self._check(checks, "Também olhar pra cima/baixo", "pitch_on")
        self._check(checks, "Inverter esquerda/direita", "invert_yaw")
        self._check(checks, "Inverter cima/baixo", "invert_pitch")
        self._check(checks, "Ligar sozinho quando abrir o JoyconVR", "enabled")
        tk.Label(right, text="Dica: toque no Captura para a cabeça voltar ao centro.",
                 bg=BG, fg=FAINT, font=(FONT, 8), anchor="w").pack(fill="x", pady=(6, 0))
        self.refresh_button()
        self.draw()

    # --- widgets
    def _slider(self, parent, row, label, lo, hi, step, value, key, unit):
        import joyconvr_pages as pages
        tk.Label(parent, text=label, bg=BG, fg=MUTED, font=(FONT, 9), anchor="w").grid(row=row, column=0, sticky="w")
        sl = pages.Slider(parent, lo, hi, step, value, lambda v, k=key: self._set(k, round(v, 3)), 190)
        sl.configure(bg=BG)
        sl.grid(row=row, column=1, padx=(6, 0), sticky="w")

    def _check(self, parent, label, key):
        var = tk.BooleanVar(value=bool(head.settings().get(key)))
        tk.Checkbutton(parent, text=label, variable=var, command=lambda: self._set(key, var.get()),
                       bg=BG, fg=TEXT, selectcolor=CARD, activebackground=BG, activeforeground=TEXT,
                       font=(FONT, 9), bd=0, highlightthickness=0, cursor="hand2").pack(anchor="w")

    def _set(self, key, value):
        head.settings()[key] = value
        self.save_settings()

    # --- ligar / desligar
    def refresh_button(self):
        on = head.TRACKER.running()
        self.toggle_btn.configure(text="Desligar câmera" if on else "Ligar câmera",
                                  bg=BAD if on else ACCENT, fg="#061018")

    def toggle(self):
        if head.TRACKER.running():
            head.TRACKER.stop()
            self.after(300, self.refresh_button)
            return
        if not head.settings().get("consent"):
            self.show_privacy()
            return
        head.TRACKER.start()
        self.after(200, self.refresh_button)

    def show_privacy(self, info_only=False):
        win = tk.Toplevel(self)
        self.privacy_win = win
        win.title("Usar a webcam?")
        win.configure(bg=BG)
        win.resizable(False, False)
        win.transient(self.winfo_toplevel())
        tk.Label(win, text="📷  Permitir usar a webcam?", bg=BG, fg=TEXT, font=(FONT, 14, "bold")).pack(
            anchor="w", padx=22, pady=(18, 8))
        tk.Label(win, text=PRIVACY, bg=BG, fg=TEXT, font=(FONT, 10), justify="left", wraplength=430).pack(
            anchor="w", padx=22)
        bar = tk.Frame(win, bg=BG)
        bar.pack(fill="x", padx=22, pady=18)

        def allow():
            self._set("consent", True)
            win.destroy()
            head.TRACKER.start()
            self.after(200, self.refresh_button)

        if info_only:
            tk.Button(bar, text="Fechar", command=win.destroy, bg=CARD, fg=TEXT, bd=0, padx=14, pady=6,
                      font=(FONT, 10)).pack(side="right")
            if head.settings().get("consent"):
                def revoke():
                    head.TRACKER.stop()
                    self._set("consent", False)
                    self._set("enabled", False)
                    win.destroy()
                    self.after(300, self.refresh_button)
                tk.Button(bar, text="Tirar a permissão", command=revoke, bg=CARD, fg=BAD, bd=0, padx=14, pady=6,
                          font=(FONT, 10)).pack(side="left")
        else:
            tk.Button(bar, text="Entendi, pode usar", command=allow, bg=ACCENT, fg="#061018", bd=0, padx=14, pady=6,
                      font=(FONT, 10, "bold")).pack(side="right")
            tk.Button(bar, text="Agora não", command=win.destroy, bg=CARD, fg=TEXT, bd=0, padx=14, pady=6,
                      font=(FONT, 10)).pack(side="right", padx=8)
        win.grab_set()

    # --- desenho (chamado pela janela ~15 vezes por segundo)
    def draw(self):
        pts, (hy, hp), (vy, vp), fps, status = head.TRACKER.snapshot()
        c = self.canvas
        W = self.W
        if not self.dots:
            for _ in range(478):
                self.dots.append(c.create_oval(0, 0, 0, 0, fill=ACCENT, outline=""))
            self.arrow = c.create_line(W / 2, W - 30, W / 2, W - 30, fill=OK, width=4, arrow="last")
            self.msg = c.create_text(W / 2, W / 2, text="", fill=MUTED, font=(FONT, 10), justify="center")
        if pts:
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
            span = max(max(xs) - min(xs), max(ys) - min(ys), 1e-3)
            k = (W * 0.7) / span
            for item, (x, y) in zip(self.dots, pts):
                px = W / 2 + (cx - x) * k        # espelhado: fica como um espelho para a pessoa
                py = W / 2 - 10 + (y - cy) * k
                c.coords(item, px - 1.2, py - 1.2, px + 1.2, py + 1.2)
            c.itemconfig(self.msg, text="")
        else:
            for item in self.dots:
                c.coords(item, 0, 0, 0, 0)
            c.itemconfig(self.msg, text=("Câmera desligada.\nClique em \"Ligar câmera\"." if status == "desligada"
                                         else status))
        # seta mostra para onde a VISAO esta virando
        ax = W / 2 - max(-1, min(1, vy / 90)) * 110
        ay = W - 30 - max(-1, min(1, vp / 60)) * 40
        c.coords(self.arrow, W / 2, W - 30, ax, ay)
        self.readout.configure(text=(f"cabeça {hy:+5.0f}° → visão {vy:+5.0f}°   ({fps:.0f}/s)" if pts else status))
        if bool(head.TRACKER.running()) != (self.toggle_btn.cget("text") == "Desligar câmera"):
            self.refresh_button()
