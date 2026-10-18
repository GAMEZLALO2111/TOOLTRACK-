import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from tool_locations_ui import ToolLocationWindow

STATE_LABELS = {"disponible": "Disponible", "danada": "Dañada"}
SIZES = ["A", "B", "C", "D", "E"]


class MachineWindow(ToolLocationWindow):
    """Machine catalog: profile (type, line, capacity) and numbered stations."""

    def open(self):
        self.window = tk.Toplevel(self.parent)
        self.window.title("Máquinas - Catálogo y estaciones")
        self.window.geometry("1280x800")
        self.window.minsize(1000, 660)

        self.selected_code = None
        self.profile_vars = {
            key: tk.StringVar()
            for key in ("tipo_maquina", "edificio", "linea", "capacidad", "espesor_min", "espesor_max", "blank_max", "area_pallet")
        }
        self.notes_var = tk.StringVar()
        self.header_var = tk.StringVar(value="Selecciona una máquina de la lista.")
        self.station_number_var = tk.StringVar()
        self.station_size_var = tk.StringVar()
        self.station_position_var = tk.StringVar()
        self.station_auto_var = tk.BooleanVar(value=False)
        self.station_note_var = tk.StringVar()

        body = ttk.Frame(self.window, padding=10)
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=4)
        body.rowconfigure(0, weight=1)

        left = ttk.LabelFrame(body, text="Máquinas", padding=8)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        columns = [
            ("codigo", "Máquina", 80),
            ("tipo", "Tipo", 110),
            ("edificio", "Edificio", 70),
            ("linea", "Línea", 70),
            ("total", "Estac.", 55),
            ("disp", "Disp.", 55),
            ("dan", "Dañ.", 55),
        ]
        frame, self.machine_tree = self._tree(left, columns, 18)
        frame.pack(fill=tk.BOTH, expand=True)
        self.machine_tree.bind("<<TreeviewSelect>>", lambda _e: self.on_machine_selected())
        buttons = ttk.Frame(left)
        buttons.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(buttons, text="Nueva máquina", style="Accent.TButton", command=self.new_machine).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Actualizar", command=self.refresh_machines).pack(side=tk.LEFT, padx=6)

        right = ttk.Frame(body)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)

        profile = ttk.LabelFrame(right, text="Ficha de la máquina", padding=8)
        profile.grid(row=0, column=0, sticky="ew")
        ttk.Label(profile, textvariable=self.header_var, font=("Segoe UI", 12, "bold")).grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 6)
        )
        fields = (
            ("tipo_maquina", "Tipo / modelo"),
            ("edificio", "Edificio (ej. B1)"),
            ("linea", "Línea o área"),
            ("capacidad", "Capacidad (ej. 30 ton)"),
            ("espesor_min", "Espesor mín. (in)"),
            ("espesor_max", "Espesor máx. (in)"),
            ("blank_max", "Blank máx. (ej. 96x56)"),
            ("area_pallet", "Área pallet (ej. 80x48)"),
        )
        for index, (key, label) in enumerate(fields):
            row, col = 1 + index // 2, (index % 2) * 2
            ttk.Label(profile, text=label).grid(row=row, column=col, sticky="w", padx=4, pady=3)
            ttk.Entry(profile, textvariable=self.profile_vars[key], width=24).grid(row=row, column=col + 1, padx=4)
        ttk.Label(profile, text="Notas").grid(row=5, column=0, sticky="w", padx=4, pady=3)
        ttk.Entry(profile, textvariable=self.notes_var, width=62).grid(row=5, column=1, columnspan=3, sticky="ew", padx=4)
        ttk.Button(profile, text="Guardar ficha", style="Accent.TButton", command=self.save_profile).grid(
            row=6, column=3, sticky="e", pady=(8, 0), padx=4
        )

        stations = ttk.LabelFrame(right, text="Estaciones", padding=8)
        stations.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        stations.rowconfigure(0, weight=1)
        stations.columnconfigure(0, weight=1)
        frame, self.station_tree = self._tree(
            stations,
            [
                ("numero", "Estación", 80), ("tamano", "Tamaño", 70), ("posicion", "Posición", 90),
                ("auto", "Autoindex", 80), ("estado", "Estado", 100), ("nota", "Nota", 220),
            ],
            10,
            selectmode="extended",
        )
        frame.grid(row=0, column=0, sticky="nsew")
        self.station_tree.tag_configure("danada", background="#fbe3e1", foreground="#b3261e")

        add = ttk.Frame(stations)
        add.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(add, text="Estación").pack(side=tk.LEFT)
        number_entry = ttk.Entry(add, textvariable=self.station_number_var, width=8)
        number_entry.pack(side=tk.LEFT, padx=4)
        ttk.Label(add, text="Tamaño").pack(side=tk.LEFT, padx=(8, 0))
        ttk.Combobox(add, textvariable=self.station_size_var, values=SIZES, state="readonly", width=4).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Label(add, text="Posición").pack(side=tk.LEFT, padx=(8, 0))
        ttk.Combobox(
            add, textvariable=self.station_position_var, values=["DENTRO", "CENTRO", "AFUERA"],
            state="readonly", width=8,
        ).pack(side=tk.LEFT, padx=4)
        ttk.Checkbutton(add, text="Autoindex", variable=self.station_auto_var).pack(side=tk.LEFT, padx=4)
        ttk.Label(add, text="Nota").pack(side=tk.LEFT, padx=(8, 0))
        note_entry = ttk.Entry(add, textvariable=self.station_note_var, width=18)
        note_entry.pack(side=tk.LEFT, padx=4)
        ttk.Button(add, text="Agregar estación", style="Accent.TButton", command=self.add_station).pack(
            side=tk.LEFT, padx=8
        )
        number_entry.bind("<Return>", lambda _e: self.add_station())
        note_entry.bind("<Return>", lambda _e: self.add_station())

        actions = ttk.Frame(stations)
        actions.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(actions, text="Marcar dañada", command=lambda: self.set_station_state("danada")).pack(side=tk.LEFT)
        ttk.Button(actions, text="Marcar disponible", command=lambda: self.set_station_state("disponible")).pack(
            side=tk.LEFT, padx=6
        )
        ttk.Button(actions, text="Eliminar seleccionadas", command=self.delete_stations).pack(side=tk.LEFT)

        self.refresh_machines()
        return self.window

    def refresh_machines(self, keep=None):
        machines = self._api("GET", "/machine-profiles")
        if machines is None:
            return
        keep = keep or self.selected_code
        self.machine_tree.delete(*self.machine_tree.get_children())
        for machine in machines:
            self.machine_tree.insert(
                "", tk.END, iid=machine["codigo"],
                values=(
                    machine["codigo"], machine["tipo_maquina"] or "—", machine["edificio"] or "—",
                    machine["linea"] or "—",
                    machine["estaciones_total"], machine["estaciones_disponibles"],
                    machine["estaciones_danadas"],
                ),
            )
        if keep and self.machine_tree.exists(keep):
            self.machine_tree.selection_set(keep)
        else:
            self.selected_code = None

    def on_machine_selected(self):
        selection = self.machine_tree.selection()
        if not selection:
            return
        self.selected_code = selection[0]
        machine = self._api("GET", f"/machine-profiles/{self.selected_code}")
        if machine is None:
            return
        self.header_var.set(
            f"{machine['codigo']} — {machine['estaciones_total']} estaciones "
            f"({machine['estaciones_disponibles']} disponibles, {machine['estaciones_danadas']} dañadas)"
        )
        for key, var in self.profile_vars.items():
            var.set(machine[key] or "")
        self.notes_var.set(machine["notas"] or "")
        self.station_tree.delete(*self.station_tree.get_children())
        for station in machine["estaciones"]:
            self.station_tree.insert(
                "", tk.END, iid=station["numero"],
                values=(
                    station["numero"], station["tamano"] or "—", station.get("posicion") or "—",
                    "Sí" if station.get("autoindex") else "",
                    STATE_LABELS.get(station["estado"], station["estado"]), station["nota"] or "",
                ),
                tags=("danada",) if station["estado"] != "disponible" else (),
            )

    def _require_machine(self):
        if not self.selected_code:
            messagebox.showwarning("Máquina", "Selecciona una máquina de la lista.", parent=self.window)
        return self.selected_code

    def new_machine(self):
        code = simpledialog.askstring("Nueva máquina", "Código de la máquina (ej. T-118):", parent=self.window)
        if not code or not code.strip():
            return
        code = code.strip().upper()
        created = self._api("POST", "/locations", {"codigo": code, "nombre": f"Machine {code}", "tipo": "machine"})
        if created is None:
            return
        self.refresh_machines(keep=code)
        self.on_machine_selected()

    def save_profile(self):
        code = self._require_machine()
        if not code:
            return
        payload = {key: var.get() for key, var in self.profile_vars.items()}
        payload["notas"] = self.notes_var.get()
        if self._api("PUT", f"/machine-profiles/{code}", payload) is None:
            return
        self.refresh_machines(keep=code)
        messagebox.showinfo("Máquinas", f"Ficha de {code} guardada.", parent=self.window)

    def add_station(self):
        code = self._require_machine()
        if not code:
            return
        number = self.station_number_var.get().strip()
        if not number:
            messagebox.showwarning("Estación", "Escribe el número de estación.", parent=self.window)
            return
        result = self._api("POST", f"/machine-profiles/{code}/stations", {
            "numero": number, "tamano": self.station_size_var.get(), "nota": self.station_note_var.get(),
            "posicion": self.station_position_var.get(), "autoindex": self.station_auto_var.get(),
        })
        if result is None:
            return
        self.station_number_var.set("")
        self.station_note_var.set("")
        self.refresh_machines(keep=code)
        self.on_machine_selected()

    def _selected_stations(self):
        selection = self.station_tree.selection()
        if not selection:
            messagebox.showwarning("Estación", "Selecciona al menos una estación.", parent=self.window)
        return list(selection)

    def set_station_state(self, state):
        code = self._require_machine()
        numbers = self._selected_stations() if code else []
        if not numbers:
            return
        note = self.station_note_var.get().strip() if state == "danada" else ""
        for number in numbers:
            payload = {"estado": state}
            if state == "danada" and note:
                payload["nota"] = note
            if state == "disponible":
                payload["nota"] = ""
            if self._api("PATCH", f"/machine-profiles/{code}/stations/{number}", payload) is None:
                break
        self.refresh_machines(keep=code)
        self.on_machine_selected()

    def delete_stations(self):
        code = self._require_machine()
        numbers = self._selected_stations() if code else []
        if not numbers:
            return
        if not messagebox.askyesno(
            "Eliminar", f"¿Eliminar {len(numbers)} estación(es) de {code}?", parent=self.window
        ):
            return
        for number in numbers:
            if self._api("DELETE", f"/machine-profiles/{code}/stations/{number}") is None:
                break
        self.refresh_machines(keep=code)
        self.on_machine_selected()
