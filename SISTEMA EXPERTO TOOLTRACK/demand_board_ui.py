import tkinter as tk
from datetime import datetime, timedelta
from tkinter import messagebox, simpledialog, ttk

import requests

import api_client
DEMANDAS_URL = "http://127.0.0.1:8000/workflow/demandas"
PROGRAMAS_URL = "http://127.0.0.1:8000/workflow/programs"
REFRESH_MS = 30000
FONT = "Segoe UI"
CARD_MIN_WIDTH = 340

NIVELES = {
    "ROJO": {"bg": "#C0392B", "fg": "white", "label": "PARA PRONTO", "chip": "pronto a parar"},
    "AMARILLO": {"bg": "#F2B01E", "fg": "#1B1B1B", "label": "ATENCIÓN", "chip": "atención"},
    "VERDE": {"bg": "#2E9E5B", "fg": "white", "label": "OK", "chip": "corriendo bien"},
    "PARO": {"bg": "#5B6B8C", "fg": "white", "label": "PARO PROGRAMADO", "chip": "en paro"},
    "SIN_COLA": {"bg": "#9AA3AD", "fg": "white", "label": "SIN COLA", "chip": "sin cola"},
}
ESTADO_COLORS = {
    "TERMINADO": "#C6EFCE",
    "CORRIENDO": "#BDD7EE",
    "EN MÁQUINA": "#CDEBC5",
    "SURTIENDO": "#FFE699",
    "FALTA HERRAMIENTAS": "#F4B183",
    "NO INICIADO": "#E7E9ED",
}
MARCA_COLORS = {
    "URGENTE": "#C0392B",
    "LÁSER": "#7D3C98",
    "CORTE DE BLANCK": "#CA6F1E",
    "PARO PROGRAMADO": "#5B6B8C",
}
APP_BG = "#EEF1F5"


def fmt_dur(hr):
    """Horas decimales a texto corto: 1 h 24 min / 45 min."""
    if hr is None:
        return "—"
    minutes = round(hr * 60)
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60:02d} min"


def fmt_eta(hr):
    if hr is None:
        return None
    moment = datetime.now() + timedelta(hours=hr)
    text = moment.strftime("%H:%M")
    return text if moment.date() == datetime.now().date() else f"{text} (+{(moment.date() - datetime.now().date()).days}d)"


def _chip(parent, text, bg, fg="#1B1B1B", size=9):
    return tk.Label(parent, text=text, bg=bg, fg=fg, font=(FONT, size, "bold"), padx=6, pady=1)


def _scrollable(parent, bg):
    canvas = tk.Canvas(parent, bg=bg, highlightthickness=0)
    bar = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=canvas.yview)
    inner = tk.Frame(canvas, bg=bg)
    window_id = canvas.create_window((0, 0), window=inner, anchor="nw")
    inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window_id, width=e.width))
    canvas.configure(yscrollcommand=bar.set)
    bar.pack(side=tk.RIGHT, fill=tk.Y)
    canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    return canvas, inner


def _bind_wheel(window, canvas):
    window.bind("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))


def _bind_click(widget, callback):
    if not isinstance(widget, (tk.Button, ttk.Button)):
        widget.bind("<Button-1>", lambda _e: callback())
    for child in widget.winfo_children():
        _bind_click(child, callback)


def _progress(parent, item, color):
    bar = tk.Canvas(parent, height=10, bg="#DDE2E8", highlightthickness=0)
    fraction = min(item["piezas_producidas"] / item["cantidad"], 1) if item["cantidad"] else 0

    def draw(event):
        bar.delete("all")
        bar.create_rectangle(0, 0, event.width * fraction, 10, fill=color, width=0)
    bar.bind("<Configure>", draw)
    return bar


class DemandBoardWindow:
    """Tablero de secuencia: una tarjeta por máquina con semáforo y clic para ver/editar su cola."""

    def __init__(self, parent, user, on_surtir, edificio="B1"):
        self.parent = parent
        self.user = user or {}
        self.actor = self.user.get("username") or "operator"
        self.on_surtir = on_surtir
        self.edificio = edificio
        self.window = None
        self.detail = None
        self.detail_machine = None
        self.machines = []
        self.marcas = []
        self.resumen = {}
        self.cards = []
        self.cols = 0

    # ---------- ventana principal ----------
    def open(self):
        self.window = tk.Toplevel(self.parent)
        self.window.title(f"Tablero de secuencia - {self.edificio}")
        self.window.geometry("1300x800")
        self.window.minsize(720, 480)
        self.window.configure(bg=APP_BG)
        try:
            self.window.state("zoomed")
        except tk.TclError:
            pass

        top = tk.Frame(self.window, bg=APP_BG)
        top.pack(fill=tk.X, padx=12, pady=(10, 2))
        tk.Label(top, text=f"Tablero de secuencia {self.edificio}", bg=APP_BG,
                 font=(FONT, 15, "bold")).pack(side=tk.LEFT)
        self.status_var = tk.StringVar()
        tk.Label(top, textvariable=self.status_var, bg=APP_BG, fg="#555").pack(side=tk.LEFT, padx=12)
        ttk.Button(top, text="Actualizar", command=lambda: self.refresh()).pack(side=tk.RIGHT)
        if api_client.can("demand.manage"):
            ttk.Button(top, text="Nueva demanda…", command=lambda: self.new_demand(self.window)).pack(
                side=tk.RIGHT, padx=6)

        self.summary = tk.Frame(self.window, bg=APP_BG)
        self.summary.pack(fill=tk.X, padx=12, pady=(2, 0))
        tk.Label(self.window, anchor="w", bg=APP_BG, fg="#666", font=(FONT, 9),
                 text="Semáforo según las horas que le faltan a la NP que corre: rojo < 2 h · "
                      "amarillo 2–4 h · verde > 4 h. Clic en una tarjeta para ver su cola.").pack(
            fill=tk.X, padx=12, pady=(0, 4))

        holder = tk.Frame(self.window, bg=APP_BG)
        holder.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))
        self.canvas, self.inner = _scrollable(holder, APP_BG)
        _bind_wheel(self.window, self.canvas)
        self.canvas.bind("<Configure>", self._on_canvas_configure, add="+")

        self.refresh()
        self._schedule_refresh()
        return self.window

    def _on_canvas_configure(self, event):
        cols = max(1, event.width // CARD_MIN_WIDTH)
        if cols != self.cols:
            self.cols = cols
            self._layout_cards()

    def _schedule_refresh(self):
        def tick():
            if self.window is None or not self.window.winfo_exists():
                return
            self.refresh(silent=True)
            self.window.after(REFRESH_MS, tick)
        self.window.after(REFRESH_MS, tick)

    def _api(self, method, url, payload=None, params=None, silent=False, parent=None):
        parent = parent or self.window
        try:
            response = requests.request(method, url, json=payload, params=params, timeout=15)
        except requests.RequestException as error:
            if not silent:
                messagebox.showerror("Error de conexión",
                                     f"No se pudo conectar con TOOLTRACK:\n{error}", parent=parent)
            return None
        if not response.ok:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            if not silent:
                messagebox.showerror("Error de operación", f"{detail or response.reason}", parent=parent)
            return None
        return response.json() if response.content else {}

    def refresh(self, silent=False):
        data = self._api("GET", f"{DEMANDAS_URL}/board", params={"edificio": self.edificio}, silent=silent)
        if data is None:
            self.status_var.set("⚠ Sin conexión con el backend; se reintenta solo")
            return
        self.machines = data["maquinas"]
        self.marcas = data.get("marcas", [])
        self.resumen = data.get("resumen", {})
        scroll_position = self.canvas.yview()[0]
        self._render_summary()
        self._render_cards()
        self.canvas.after_idle(lambda: self.canvas.yview_moveto(scroll_position))
        self.status_var.set(f"Actualizado {datetime.now():%H:%M:%S}")
        if self.detail is not None and self.detail.winfo_exists():
            self._render_detail()

    def _render_summary(self):
        for child in self.summary.winfo_children():
            child.destroy()
        for nivel, style in NIVELES.items():
            count = self.resumen.get(nivel, 0)
            _chip(self.summary, f"{count}  {style['chip']}", style["bg"], style["fg"], 10).pack(
                side=tk.LEFT, padx=(0, 6), pady=2)
        total = sum(m["tie_total"] for m in self.machines)
        tk.Label(self.summary, bg=APP_BG, fg="#444", font=(FONT, 9),
                 text=f"TIE total del edificio: {total:.1f} HR").pack(side=tk.RIGHT)

    # ---------- tarjetas ----------
    def _render_cards(self):
        for child in self.inner.winfo_children():
            child.destroy()
        self.cards = [self._build_card(self.inner, machine) for machine in self.machines]
        self._layout_cards()

    def _layout_cards(self):
        cols = self.cols or 1
        for column in range(12):
            self.inner.columnconfigure(column, weight=0, uniform="")
        for index, card in enumerate(self.cards):
            card.grid(row=index // cols, column=index % cols, sticky="nsew", padx=6, pady=6)
        for column in range(cols):
            self.inner.columnconfigure(column, weight=1, uniform="card")

    @staticmethod
    def _item(machine, key):
        return next((i for i in machine["demandas"] if i[key]), None)

    def _np_line(self, parent, caption, item, with_estado=False):
        line = tk.Frame(parent, bg="white")
        line.pack(fill=tk.X, pady=(2, 0))
        tk.Label(line, text=caption, bg="white", fg="#7A8591", font=(FONT, 8, "bold"),
                 width=11, anchor="w").pack(side=tk.LEFT)
        tk.Label(line, text=item["numero_parte"], bg="white", font=(FONT, 11, "bold")).pack(side=tk.LEFT)
        if with_estado or item["marca"]:
            chips = tk.Frame(parent, bg="white")
            chips.pack(fill=tk.X, pady=(2, 0))
            tk.Label(chips, bg="white", width=11).pack(side=tk.LEFT)
            if with_estado:
                _chip(chips, item["estado"], ESTADO_COLORS.get(item["estado"], "#EEE")).pack(side=tk.LEFT)
            if item["marca"]:
                _chip(chips, item["marca"], MARCA_COLORS.get(item["marca"], "#555"), "white", 8).pack(
                    side=tk.LEFT, padx=(6, 0))
        return line

    def _build_card(self, parent, machine):
        style = NIVELES[machine["nivel"]]
        urgent = machine["surtir_urgente"]
        border = "#7B1E14" if urgent else style["bg"]
        card = tk.Frame(parent, bg="white", highlightthickness=4 if urgent else 2,
                        highlightbackground=border, highlightcolor=border, cursor="hand2")

        head = tk.Frame(card, bg=style["bg"])
        head.pack(fill=tk.X)
        tk.Label(head, text=f"{machine['maquina']}  ·  {machine['tipo']}", bg=style["bg"], fg=style["fg"],
                 font=(FONT, 13, "bold")).grid(row=0, column=0, sticky="w", padx=10, pady=(6, 0))
        big = fmt_dur(machine["hr_restante"]) if machine["hr_restante"] is not None else style["label"]
        tk.Label(head, text=big, bg=style["bg"], fg=style["fg"], font=(FONT, 13, "bold")).grid(
            row=0, column=1, sticky="e", padx=10, pady=(6, 0))
        tk.Label(head, text=machine["motivo"], bg=style["bg"], fg=style["fg"], font=(FONT, 9),
                 anchor="w", justify="left", wraplength=CARD_MIN_WIDTH - 40).grid(
            row=1, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 6))
        head.columnconfigure(0, weight=1)

        body = tk.Frame(card, bg="white", padx=10, pady=6)
        body.pack(fill=tk.BOTH, expand=True)
        actual = self._item(machine, "es_actual")
        siguiente = self._item(machine, "es_siguiente")

        if actual:
            self._np_line(body, "CORRIENDO", actual)
            _progress(body, actual, style["bg"]).pack(fill=tk.X, pady=(4, 2))
            eta = None if machine["nivel"] == "PARO" else fmt_eta(actual["hr_pendiente"])
            detail = f"{actual['piezas_producidas']}/{actual['cantidad']} pzas"
            if eta:
                detail += f"  ·  para ≈ {eta}"
            tk.Label(body, text=detail, bg="white", fg="#555", font=(FONT, 9), anchor="w").pack(fill=tk.X)
        else:
            text = f"En máquina: {machine['corriendo']}" if machine["corriendo"] else "Máquina sin NP montado"
            tk.Label(body, text=text, bg="white", fg="#555", font=(FONT, 10), anchor="w").pack(fill=tk.X)

        tk.Frame(body, bg="#E3E7EC", height=1).pack(fill=tk.X, pady=6)
        if siguiente:
            self._np_line(body, "SIGUIENTE", siguiente, with_estado=True)
        else:
            tk.Label(body, text="Sin siguiente en la cola", bg="white", fg="#7A8591",
                     font=(FONT, 10), anchor="w").pack(fill=tk.X)
        pending = [i for i in machine["demandas"] if i["pendiente"] > 0]
        more = len(pending) - (1 if actual else 0) - (1 if siguiente else 0)
        summary = f"Cola pendiente: {fmt_dur(machine['hr_cola'])}" if pending else ""
        if more > 0:
            summary += f"  ·  +{more} más"
        tk.Label(body, text=summary, bg="white", fg="#555", font=(FONT, 9), anchor="w").pack(
            fill=tk.X, pady=(4, 0))

        if siguiente and siguiente["estado"] != "EN MÁQUINA" and api_client.can("kitting.operate"):
            urgent_btn = urgent
            tk.Button(card, text="⚠ SURTIR SIGUIENTE YA" if urgent_btn else "Surtir siguiente",
                      bg="#C0392B" if urgent_btn else "#1F3864", fg="white", relief=tk.FLAT,
                      font=(FONT, 10, "bold"), cursor="hand2", pady=4,
                      command=lambda m=machine["maquina"], np=siguiente["numero_parte"]: self.on_surtir(m, np)
                      ).pack(fill=tk.X, padx=10, pady=(0, 10))
        elif siguiente and siguiente["estado"] != "EN MÁQUINA":
            tk.Label(card, text="⚠ Siguiente por surtir" if urgent else "Siguiente por surtir", bg="white",
                     fg="#C0392B" if urgent else "#555", font=(FONT, 9, "bold")).pack(pady=(0, 10))
        elif not siguiente and machine["nivel"] == "SIN_COLA" and api_client.can("demand.manage"):
            tk.Button(card, text="Agregar demanda", bg="#1F3864", fg="white", relief=tk.FLAT,
                      font=(FONT, 10, "bold"), cursor="hand2", pady=4,
                      command=lambda m=machine["maquina"]: self.new_demand(self.window, m)
                      ).pack(fill=tk.X, padx=10, pady=(0, 10))
        elif siguiente:
            tk.Label(card, text="✔ Siguiente lista en máquina", bg="white", fg="#2E7D32",
                     font=(FONT, 9, "bold")).pack(pady=(0, 10))
        else:
            tk.Frame(card, bg="white", height=8).pack()

        _bind_click(card, lambda code=machine["maquina"]: self.open_detail(code))
        return card

    # ---------- detalle de una máquina ----------
    def open_detail(self, code):
        self.detail_machine = code
        if self.detail is None or not self.detail.winfo_exists():
            self.detail = tk.Toplevel(self.window)
            self.detail.geometry("860x680")
            self.detail.minsize(640, 420)
            self.detail.configure(bg=APP_BG)
        self.detail.lift()
        self._render_detail()

    def _machine(self, code):
        return next((m for m in self.machines if m["maquina"] == code), None)

    def _render_detail(self):
        machine = self._machine(self.detail_machine)
        if machine is None:
            self.detail.destroy()
            return
        win = self.detail
        for child in win.winfo_children():
            child.destroy()
        win.title(f"Cola de {machine['maquina']}")
        style = NIVELES[machine["nivel"]]

        head = tk.Frame(win, bg=style["bg"])
        head.pack(fill=tk.X)
        tk.Label(head, text=f"{machine['maquina']}  ·  {machine['tipo']}", bg=style["bg"], fg=style["fg"],
                 font=(FONT, 16, "bold")).pack(side=tk.LEFT, padx=14, pady=10)
        big = fmt_dur(machine["hr_restante"]) if machine["hr_restante"] is not None else style["label"]
        tk.Label(head, text=big, bg=style["bg"], fg=style["fg"], font=(FONT, 16, "bold")).pack(
            side=tk.RIGHT, padx=14)
        sub = tk.Frame(win, bg=APP_BG)
        sub.pack(fill=tk.X, padx=14, pady=(6, 0))
        tk.Label(sub, text=machine["motivo"], bg=APP_BG, font=(FONT, 10, "bold"), anchor="w").pack(side=tk.LEFT)
        tk.Label(sub, bg=APP_BG, fg="#555", font=(FONT, 9),
                 text=f"TIE total {machine['tie_total']:.1f} HR · cubierto {machine['tie_cubierto']:.1f} HR").pack(
            side=tk.RIGHT)

        bottom = tk.Frame(win, bg=APP_BG)
        bottom.pack(side=tk.BOTTOM, fill=tk.X, padx=14, pady=10)
        if api_client.can("demand.manage"):
            ttk.Button(bottom, text=f"+ Agregar demanda a {machine['maquina']}",
                       command=lambda: self.new_demand(win, machine["maquina"])).pack(side=tk.LEFT)
        ttk.Button(bottom, text="Cerrar", command=win.destroy).pack(side=tk.RIGHT)

        holder = tk.Frame(win, bg=APP_BG)
        holder.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)
        canvas, inner = _scrollable(holder, APP_BG)
        _bind_wheel(win, canvas)

        pending = [i for i in machine["demandas"] if i["pendiente"] > 0]
        done = [i for i in machine["demandas"] if i["pendiente"] == 0]
        if not machine["demandas"]:
            tk.Label(inner, text="Esta máquina no tiene demandas. Agrega la primera con el botón de abajo.",
                     bg=APP_BG, fg="#555", font=(FONT, 11)).pack(pady=30)
        for position, item in enumerate(pending, start=1):
            self._queue_row(inner, machine, item, position, pending)
        if done:
            tk.Label(inner, text=f"Terminadas ({len(done)})", bg=APP_BG, fg="#555",
                     font=(FONT, 10, "bold"), anchor="w").pack(fill=tk.X, padx=10, pady=(12, 2))
            for item in done:
                self._queue_row(inner, machine, item, None, pending)

    def _queue_row(self, parent, machine, item, position, pending):
        finished = position is None
        row = tk.Frame(parent, bg="#F6F8FA" if finished else "white", highlightthickness=1,
                       highlightbackground="#D5DAE0")
        row.pack(fill=tk.X, padx=8, pady=3)
        tk.Frame(row, bg=ESTADO_COLORS.get(item["estado"], "#EEE"), width=10).pack(side=tk.LEFT, fill=tk.Y)
        bg = row["bg"]
        if not finished:
            tk.Label(row, text=str(position), bg=bg, fg="#7A8591", font=(FONT, 16, "bold"),
                     width=3).pack(side=tk.LEFT)

        info = tk.Frame(row, bg=bg)
        info.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6, pady=6)
        title = tk.Frame(info, bg=bg)
        title.pack(fill=tk.X)
        tk.Label(title, text=item["numero_parte"], bg=bg, font=(FONT, 12, "bold")).pack(side=tk.LEFT)
        _chip(title, item["estado"], ESTADO_COLORS.get(item["estado"], "#EEE")).pack(side=tk.LEFT, padx=6)
        if item["es_actual"]:
            _chip(title, "ACTUAL", "#1F3864", "white", 8).pack(side=tk.LEFT)
        elif item["es_siguiente"]:
            _chip(title, "SIGUIENTE", "#5B6B8C", "white", 8).pack(side=tk.LEFT)
        if item["marca"]:
            _chip(title, item["marca"], MARCA_COLORS.get(item["marca"], "#555"), "white", 8).pack(
                side=tk.LEFT, padx=6)
        text = (f"{item['piezas_producidas']}/{item['cantidad']} pzas  ·  "
                f"faltan {fmt_dur(item['hr_pendiente'])}  ·  total {fmt_dur(item['hr'])}")
        if item["sin_programa"]:
            text += "  ·  ⚠ sin programa"
        tk.Label(info, text=text, bg=bg, fg="#555", font=(FONT, 9), anchor="w").pack(fill=tk.X)
        _progress(info, item, "#2E9E5B" if finished else "#3D6FB6").pack(fill=tk.X, pady=3)
        if item["comentario"]:
            tk.Label(info, text=f"💬 {item['comentario']}", bg=bg, fg="#444", font=(FONT, 9, "italic"),
                     anchor="w", justify="left", wraplength=480).pack(fill=tk.X)

        actions = tk.Frame(row, bg=bg)
        actions.pack(side=tk.RIGHT, padx=8)
        code = machine["maquina"]
        if not finished and item["estado"] != "CORRIENDO" and api_client.can("kitting.operate"):
            tk.Button(actions, text="Surtir", bg="#1F3864", fg="white", relief=tk.FLAT, padx=10,
                      font=(FONT, 9, "bold"), cursor="hand2",
                      command=lambda: self.on_surtir(code, item["numero_parte"])).grid(
                row=0, column=0, columnspan=2, sticky="we", pady=(0, 3))
        if api_client.can("demand.progress"):
            ttk.Button(actions, text="Producido", width=10,
                       command=lambda: self.set_produced(item)).grid(row=1, column=0, columnspan=2, pady=1)
        if any(api_client.can(p) for p in ("demand.manage", "demand.mark", "demand.progress")):
            ttk.Button(actions, text="Editar", width=10,
                       command=lambda: self.edit_item(machine, item)).grid(row=2, column=0, columnspan=2, pady=1)
        if not finished and api_client.can("demand.manage"):
            ttk.Button(actions, text="▲", width=4,
                       command=lambda: self.move_item(item, pending, -1)).grid(row=3, column=0, pady=1)
            ttk.Button(actions, text="▼", width=4,
                       command=lambda: self.move_item(item, pending, 1)).grid(row=3, column=1, pady=1)
        if api_client.can("demand.manage"):
            ttk.Button(actions, text="Quitar", width=10,
                       command=lambda: self.delete_item(machine, item)).grid(row=4, column=0, columnspan=2, pady=1)

    # ---------- acciones ----------
    def _action_parent(self):
        if self.detail is not None and self.detail.winfo_exists():
            return self.detail
        return self.window

    def set_produced(self, item):
        parent = self._action_parent()
        value = simpledialog.askinteger(
            "Piezas producidas",
            f"Total producido de {item['numero_parte']} (demanda: {item['cantidad']} pzas):",
            initialvalue=item["piezas_producidas"], minvalue=0, parent=parent)
        if value is None:
            return
        if self._api("PATCH", f"{DEMANDAS_URL}/{item['id']}", {"piezas_producidas": value}, parent=parent) is not None:
            self.refresh()

    def edit_item(self, machine, item):
        self._demand_dialog(self._action_parent(), "Editar demanda", None, (machine["maquina"], item))

    def new_demand(self, parent, code=None):
        if not self.machines:
            return
        self._demand_dialog(parent, "Nueva demanda", code, None)

    def move_item(self, item, pending, direction):
        index = next((k for k, i in enumerate(pending) if i["id"] == item["id"]), None)
        target = None if index is None else index + direction
        if target is None or not 0 <= target < len(pending):
            return
        other = pending[target]
        parent = self._action_parent()
        self._api("PATCH", f"{DEMANDAS_URL}/{item['id']}", {"orden": other["orden"]}, parent=parent)
        self._api("PATCH", f"{DEMANDAS_URL}/{other['id']}", {"orden": item["orden"]}, parent=parent)
        self.refresh()

    def delete_item(self, machine, item):
        parent = self._action_parent()
        if not messagebox.askyesno("Quitar demanda",
                                   f"¿Quitar {item['numero_parte']} de {machine['maquina']}?", parent=parent):
            return
        if self._api("DELETE", f"{DEMANDAS_URL}/{item['id']}", parent=parent) is not None:
            self.refresh()

    def _demand_dialog(self, parent, title, preset_code, editing):
        dialog = tk.Toplevel(parent)
        dialog.title(title)
        dialog.transient(parent)
        dialog.grab_set()
        form = ttk.Frame(dialog, padding=12)
        form.pack(fill=tk.BOTH, expand=True)
        row = 0
        vars_ = {}

        def field(label, key, value="", widget=None):
            nonlocal row
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", pady=4, padx=(0, 8))
            var = tk.StringVar(value=str(value))
            if widget is None:
                ttk.Entry(form, textvariable=var, width=36).grid(row=row, column=1, sticky="we")
            else:
                widget(var).grid(row=row, column=1, sticky="we")
            vars_[key] = var
            row += 1
            return var

        if editing is None:
            codes = [m["maquina"] for m in self.machines]
            types = {m["maquina"]: m["tipo"] for m in self.machines}
            codes.sort()
            machine_var = field("Máquina", "maquina", preset_code or "",
                                lambda v: ttk.Combobox(form, textvariable=v, values=codes,
                                                       state="readonly", width=34))
            ttk.Label(form, text="Número de parte").grid(row=row, column=0, sticky="w", pady=4, padx=(0, 8))
            part_combo = ttk.Combobox(form, width=34)
            part_combo.grid(row=row, column=1, sticky="we")
            vars_["numero_parte"] = part_combo
            row += 1

            def load_parts(*_args):
                code = machine_var.get()
                if not code:
                    return
                programs = self._api("GET", PROGRAMAS_URL, params={"tipo_maquina": types[code]},
                                     parent=dialog) or []
                part_combo["values"] = sorted(p["numero_parte"] for p in programs)
            machine_var.trace_add("write", load_parts)
            load_parts()
            field("Cantidad (piezas)", "cantidad", "")
        else:
            code, item = editing
            can_qty = api_client.can("demand.manage")
            can_progress = api_client.can("demand.progress")
            can_mark = api_client.can("demand.mark")

            def entry(enabled):
                return lambda v: ttk.Entry(form, textvariable=v, width=36, state="normal" if enabled else "disabled")

            field("Máquina", "maquina", code, entry(False))
            field("Número de parte", "numero_parte", item["numero_parte"], entry(False))
            field("Cantidad (piezas)", "cantidad", item["cantidad"], entry(can_qty))
            field("Producido (piezas)", "piezas_producidas", item["piezas_producidas"], entry(can_progress))
            marca_values = [""] + list(self.marcas)
            field("Marca", "marca", item["marca"] or "",
                  lambda v: ttk.Combobox(form, textvariable=v, values=marca_values, width=34,
                                         state="readonly" if can_mark else "disabled"))
            ttk.Label(form, text="Comentario").grid(row=row, column=0, sticky="nw", pady=4, padx=(0, 8))
            comment = tk.Text(form, width=36, height=3)
            comment.insert("1.0", item["comentario"] or "")
            if not can_mark:
                comment.configure(state="disabled")
            comment.grid(row=row, column=1, sticky="we")
            row += 1

        def save():
            if editing is None:
                try:
                    cantidad = int(vars_["cantidad"].get())
                except ValueError:
                    messagebox.showwarning("Cantidad", "Escribe una cantidad en piezas.", parent=dialog)
                    return
                if not vars_["maquina"].get() or not vars_["numero_parte"].get().strip():
                    messagebox.showwarning("Faltan datos", "Elige la máquina y el número de parte.",
                                           parent=dialog)
                    return
                payload = {
                    "maquina_codigo": vars_["maquina"].get(),
                    "numero_parte": vars_["numero_parte"].get().strip(),
                    "cantidad": cantidad,
                    "creado_por": self.actor,
                }
                result = self._api("POST", DEMANDAS_URL, payload, parent=dialog)
            else:
                item = editing[1]
                payload = {}
                try:
                    if can_qty and int(vars_["cantidad"].get()) != item["cantidad"]:
                        payload["cantidad"] = int(vars_["cantidad"].get())
                    if can_progress and int(vars_["piezas_producidas"].get() or 0) != item["piezas_producidas"]:
                        payload["piezas_producidas"] = int(vars_["piezas_producidas"].get() or 0)
                except ValueError:
                    messagebox.showwarning("Número inválido", "Cantidad y producido deben ser números enteros.",
                                           parent=dialog)
                    return
                if can_mark:
                    new_marca = vars_["marca"].get() or None
                    new_comment = comment.get("1.0", tk.END).strip() or None
                    if new_marca != item["marca"]:
                        payload["marca"] = new_marca
                    if new_comment != item["comentario"] or "marca" in payload:
                        payload["comentario"] = new_comment
                if not payload:
                    dialog.destroy()
                    return
                result = self._api("PATCH", f"{DEMANDAS_URL}/{item['id']}", payload, parent=dialog)
            if result is None:
                return
            dialog.destroy()
            self.refresh()

        buttons = ttk.Frame(form)
        buttons.grid(row=row, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="Cancelar", command=dialog.destroy).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="Guardar", command=save).pack(side=tk.RIGHT, padx=6)
        form.columnconfigure(1, weight=1)
