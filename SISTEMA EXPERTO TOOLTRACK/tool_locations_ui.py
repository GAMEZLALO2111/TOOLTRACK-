import tkinter as tk
from tkinter import messagebox, ttk

import requests

from workflow_ui import API_URL, SetupWorkflowWindow

MOVEMENT_LABELS = {
    "rack_to_cart_for_kit": "Rack → Carro (surtido)",
    "cart_to_machine_delivery": "Entrega a máquina",
    "machine_return": "Regreso desde máquina a su rack",
    "machine_return_sharpening": "Retorno por afilado",
    "machine_return_damage": "Retorno por daño",
    "removed_from_setup_pending_pickup": "Retirada del setup (pendiente de recoger)",
    "pending_pickup_to_rack": "Recogida de máquina a su rack",
    "status_danado": "Marcada como dañada",
    "status_baja": "Dada de baja",
    "status_disponible": "Liberada (disponible)",
}
CATEGORY_LABELS = {"punzon": "Punzón", "matriz": "Matriz", "punch": "Punzón", "die": "Matriz"}
SERVICE_REASONS = {"Afilado": "afilado", "Daño": "dano"}
SERVICE_TYPES = {"togu", "maintenance", "workshop"}
OK_COLOR = "#1b7f3b"
ERROR_COLOR = "#b3261e"


def movement_label(movement_type):
    if movement_type in MOVEMENT_LABELS:
        return MOVEMENT_LABELS[movement_type]
    if movement_type.endswith("_to_rack"):
        return "Guardada en su rack"
    if movement_type.startswith("pending_pickup_to_"):
        return "Recogida en máquina"
    return movement_type.replace("_", " ")


def preferred_service_destination(locations, reason):
    order = ("workshop", "togu", "maintenance") if reason == "afilado" else ("maintenance", "workshop", "togu")
    active = [loc for loc in locations if loc["activa"] and loc["tipo"] in SERVICE_TYPES]
    for kind in order:
        for location in active:
            if location["tipo"] == kind:
                return location["codigo"]
    return active[0]["codigo"] if active else ""


class ToolLocationWindow(SetupWorkflowWindow):
    """Tool tracking: where is each tool, rack storage by scan, returns and pickups."""

    def open(self):
        self.window = tk.Toplevel(self.parent)
        self.window.title("Tool Tracking - Localización y retornos")
        self.window.geometry("1280x800")
        self.window.minsize(1000, 660)

        self.search_var = tk.StringVar()
        self.search_summary_var = tk.StringVar()
        self.return_machine_var = tk.StringVar()
        self.return_part_var = tk.StringVar()
        self.return_status_var = tk.StringVar(value="Selecciona una máquina para ver sus herramientas.")
        self.service_reason_var = tk.StringVar(value="Afilado")
        self.service_destination_var = tk.StringVar()
        self.service_note_var = tk.StringVar()
        self.locations = []
        self.rack_var = tk.StringVar()
        self.tool_var = tk.StringVar()
        self.rack_status_var = tk.StringVar()
        self.rack_counter_var = tk.StringVar(value="Guardadas en esta sesión: 0")
        self.stored = 0

        self.notebook = ttk.Notebook(self.window)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self.search_tab = ttk.Frame(self.notebook, padding=10)
        self.rack_tab = ttk.Frame(self.notebook, padding=14)
        self.return_tab = ttk.Frame(self.notebook, padding=10)
        self.pending_tab = ttk.Frame(self.notebook, padding=10)
        self.locations_tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.search_tab, text="Buscar herramienta")
        self.notebook.add(self.rack_tab, text="Guardar en rack")
        self.notebook.add(self.return_tab, text="Retornos de máquina")
        self.notebook.add(self.pending_tab, text="Pendientes de recoger")
        self.notebook.add(self.locations_tab, text="Ubicaciones")

        self._build_search_tab()
        self._build_rack_tab()
        self._build_return_tab()
        self._build_pending_tab()
        self._build_locations_tab()
        ttk.Label(
            self.locations_tab,
            text="Catálogo de todas las localidades (racks, carros, máquinas, TOGU, mantenimiento y taller). "
            "Aquí también puedes registrar nuevas.",
        ).pack(anchor="w", pady=(0, 8), before=self.locations_tab.winfo_children()[0])
        self.refresh_locations(show_error=True)
        self.search_tools()
        self.refresh_pending()
        self.refresh_movable()
        return self.window

    def _after_locations_loaded_service(self, active):
        machines = [item["codigo"] for item in active if item["tipo"] == "machine"]
        self.return_machine_combo["values"] = machines
        if self.return_machine_var.get() not in machines:
            self.return_machine_var.set("")
        destinations = [item["codigo"] for item in active if item["tipo"] in SERVICE_TYPES]
        self.service_destination_combo["values"] = self._service_destinations()
        if self.service_destination_var.get() not in self._service_destinations():
            self._apply_service_destination()

    def _service_destinations(self):
        reason = SERVICE_REASONS.get(self.service_reason_var.get(), "afilado")
        kinds = ("togu", "workshop") if reason == "afilado" else ("maintenance", "workshop")
        return [
            item["codigo"] for item in self.locations
            if item["activa"] and item["tipo"] in kinds
        ]

    @staticmethod
    def _tree(parent, columns, height, selectmode="browse"):
        frame = ttk.Frame(parent)
        tree = ttk.Treeview(
            frame, columns=[c[0] for c in columns], show="headings", height=height, selectmode=selectmode
        )
        for key, label, width in columns:
            tree.heading(key, text=label)
            tree.column(key, width=width, anchor="center")
        scroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        return frame, tree

    def refresh_views(self):
        self.load_machine_tools()
        self.search_tools()
        self.refresh_pending()
        self.refresh_movable()

    # ---- Buscar ----
    def _build_search_tab(self):
        ttk.Label(
            self.search_tab,
            text="Busca una herramienta por ID físico o por Tool Number para ver dónde está ahora.",
        ).pack(anchor="w")
        bar = ttk.Frame(self.search_tab)
        bar.pack(fill=tk.X, pady=8)
        ttk.Label(bar, text="ID o TN").pack(side=tk.LEFT, padx=(0, 6))
        entry = ttk.Entry(bar, textvariable=self.search_var, width=32, font=("Segoe UI", 12))
        entry.pack(side=tk.LEFT, padx=4, ipady=3)
        entry.bind("<Return>", lambda _e: self.search_tools())
        ttk.Button(bar, text="Buscar", style="Accent.TButton", command=self.search_tools).pack(side=tk.LEFT, padx=6)
        ttk.Button(bar, text="Ver todas", command=self.show_all_tools).pack(side=tk.LEFT, padx=4)
        ttk.Label(bar, textvariable=self.search_summary_var).pack(side=tk.RIGHT)

        state_bar = ttk.Frame(self.search_tab)
        state_bar.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(state_bar, text="Herramienta seleccionada:").pack(side=tk.LEFT, padx=(0, 8))
        for label, target in (
            ("Marcar dañada", "danado"), ("Liberar (disponible)", "disponible"), ("Dar de baja", "baja"),
        ):
            ttk.Button(
                state_bar, text=label, command=lambda value=target: self.change_selected_status(value)
            ).pack(side=tk.LEFT, padx=3)

        columns = (
            ("tool", "ID físico", 80), ("tn", "TN", 100), ("category", "Componente", 80),
            ("estado", "Estado", 110),
            ("status", "Dónde está", 220), ("home", "Su rack", 90), ("drawing", "Número de parte", 170),
            ("station", "Estación", 70), ("clearance", "Clearance", 80), ("last", "Último mov.", 130),
        )
        frame, self.search_tree = self._tree(self.search_tab, columns, 12)
        frame.pack(fill=tk.BOTH, expand=True)
        self.search_tree.tag_configure("machine", background="#e3f0ff")
        self.search_tree.tag_configure("pending", background="#fff0d6")
        self.search_tree.tag_configure("service", background="#fde2e2")
        self.search_tree.tag_configure("baja", background="#d9d9d9", foreground="#666666")
        self.search_tree.bind("<<TreeviewSelect>>", self.show_tool_history)

        history = ttk.LabelFrame(self.search_tab, text="Historial de la herramienta seleccionada", padding=6)
        history.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        hist_columns = (
            ("when", "Fecha", 140), ("what", "Movimiento", 230), ("from", "Origen", 100),
            ("to", "Destino", 100), ("by", "Usuario", 100), ("details", "Detalle", 400),
        )
        frame, self.history_tree = self._tree(history, hist_columns, 6)
        frame.pack(fill=tk.BOTH, expand=True)

    def show_all_tools(self):
        self.search_var.set("")
        self.search_tools()

    def search_tools(self):
        rows = self._api("GET", "/tools/locate", params={"q": self.search_var.get().strip()})
        if rows is None:
            return
        for child in self.search_tree.get_children():
            self.search_tree.delete(child)
        for child in self.history_tree.get_children():
            self.history_tree.delete(child)
        for row in rows:
            tag = ()
            if row["location_type"] == "machine":
                tag = ("pending",) if row["status"].startswith("Pendiente") else ("machine",)
            elif row["location_type"] in SERVICE_TYPES:
                tag = ("service",)
            if row.get("estado") == "baja":
                tag = ("baja",)
            self.search_tree.insert(
                "", tk.END, iid=row["tool_id"],
                values=(
                    row["tool_id"], row["tn"] or "—",
                    CATEGORY_LABELS.get(row["category"], row["category"] or "—"),
                    row.get("estado_label") or "Disponible",
                    row["status"], row.get("home_rack") or "—", row["drawing_name"] or "—",
                    row["station"] or "—", row["clearance"] or "—",
                    (row["last_movement_at"] or "")[:16].replace("T", " ") or "—",
                ),
                tags=tag,
            )
        self.search_summary_var.set(f"{len(rows)} herramienta(s)")
        if self.search_var.get().strip() and not rows:
            self.search_summary_var.set("Sin resultados para esa búsqueda")
        elif len(rows) == 1:
            self.search_tree.selection_set(rows[0]["tool_id"])

    def change_selected_status(self, target):
        selected = self.search_tree.selection()
        if not selected:
            messagebox.showwarning("Selecciona herramienta", "Selecciona una herramienta de la lista.", parent=self.window)
            return
        tool_id = selected[0]
        if target == "baja" and not messagebox.askyesno(
            "Dar de baja", f"¿Dar de baja la herramienta {tool_id}? No se podrá volver a surtir.", parent=self.window
        ):
            return
        if self._api("POST", f"/tools/{tool_id}/status", {"estado": target, "realizado_por": self.actor}) is not None:
            self.refresh_views()

    def show_tool_history(self, _event=None):
        selected = self.search_tree.selection()
        if not selected:
            return
        rows = self._api("GET", "/movements", params={"tool_id": selected[0]})
        if rows is None:
            return
        for child in self.history_tree.get_children():
            self.history_tree.delete(child)
        for row in rows:
            self.history_tree.insert(
                "", tk.END,
                values=(
                    row["created_at"][:16].replace("T", " "), movement_label(row["type"]),
                    row["from"] or "—", row["to"] or "—", row["by"] or "—", row["details"] or "",
                ),
            )

    # ---- Guardar en rack ----
    def _build_rack_tab(self):
        body = self.rack_tab
        ttk.Label(
            body,
            text="Elige el rack y escanea el ID de cada herramienta que regresa (desde máquina, pendientes, "
            "TOGU, mantenimiento o taller). Solo se acepta en el rack al que pertenece.",
            wraplength=1100, justify=tk.LEFT,
        ).pack(anchor="w", pady=(0, 12))
        form = ttk.LabelFrame(body, text="Captura", padding=14)
        form.pack(fill=tk.X)
        ttk.Label(form, text="1. Rack").grid(row=0, column=0, sticky="w", padx=4, pady=6)
        self.rack_combo = ttk.Combobox(
            form, textvariable=self.rack_var, state="readonly", width=20, font=("Segoe UI", 12)
        )
        self.rack_combo.grid(row=0, column=1, padx=8, ipady=3)
        self.rack_combo.bind("<<ComboboxSelected>>", lambda _e: self.tool_entry.focus_set())
        ttk.Label(form, text="2. Herramienta (ID)").grid(row=1, column=0, sticky="w", padx=4, pady=6)
        self.tool_entry = ttk.Entry(form, textvariable=self.tool_var, width=22, font=("Segoe UI", 12))
        self.tool_entry.grid(row=1, column=1, padx=8, ipady=3)
        self.tool_entry.bind("<Return>", lambda _e: self.store_tool())
        ttk.Button(form, text="Guardar", style="Accent.TButton", command=self.store_tool).grid(
            row=1, column=2, padx=8
        )
        ttk.Label(form, text="Resultado (si viene de afilado o daño)").grid(
            row=2, column=0, sticky="w", padx=4, pady=6
        )
        self.result_var = tk.StringVar()
        ttk.Combobox(
            form, textvariable=self.result_var, state="readonly", width=22, font=("Segoe UI", 11),
            values=("Lista para uso", "Dar de baja"),
        ).grid(row=2, column=1, padx=8)
        self.rack_status_label = tk.Label(
            body, textvariable=self.rack_status_var, anchor="w", justify=tk.LEFT,
            wraplength=1100, font=("Segoe UI", 12, "bold"),
        )
        self.rack_status_label.pack(fill=tk.X, pady=(14, 6))
        ttk.Label(body, textvariable=self.rack_counter_var).pack(anchor="w")
        frame, self.rack_log_tree = self._tree(
            body,
            (
                ("tool", "ID físico", 110), ("tn", "TN", 130), ("from", "Venía de", 140),
                ("to", "Guardada en", 140), ("kind", "Origen del movimiento", 240),
            ),
            10,
        )
        frame.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

    def _after_locations_loaded(self, active):
        racks = [item["codigo"] for item in active if item["tipo"] == "rack"]
        self.rack_combo["values"] = racks
        if self.rack_var.get() not in racks:
            self.rack_var.set("")
        self._after_locations_loaded_service(active)

    def _rack_message(self, text, ok):
        self.rack_status_var.set(text)
        self.rack_status_label.configure(fg=OK_COLOR if ok else ERROR_COLOR)

    def store_tool(self):
        tool_id = self.tool_var.get().strip()
        if not tool_id:
            return
        rack = self.rack_var.get()
        if not rack:
            self._rack_message("✖ Primero elige el rack.", False)
            self.tool_var.set("")
            self.rack_combo.focus_set()
            return
        try:
            response = requests.post(
                f"{API_URL}/racks/{rack}/store",
                json={
                    "tool_id": tool_id, "realizado_por": self.actor,
                    "resultado": {"Lista para uso": "disponible", "Dar de baja": "baja"}.get(self.result_var.get()),
                },
                timeout=15,
            )
        except requests.RequestException as error:
            self._rack_message(f"✖ Sin conexión con TOOLTRACK: {error}", False)
            return
        self.tool_var.set("")
        if not response.ok:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            self._rack_message(f"✖ {detail}", False)
            self.tool_entry.focus_set()
            return
        result = response.json()
        self.result_var.set("")
        if result["kind"] == "baja":
            self._rack_message(f"✔ {result['tool_id']} dada de baja (no vuelve al rack).", True)
            self.refresh_views()
            self.tool_entry.focus_set()
            return
        self.stored += 1
        self.rack_counter_var.set(f"Guardadas en esta sesión: {self.stored}")
        self._rack_message(f"✔ {result['tool_id']} (TN {result['tn']}) guardada en {result['to']}", True)
        self.rack_log_tree.insert(
            "", 0,
            values=(result["tool_id"], result["tn"], result["from"], result["to"], result["kind"]),
        )
        self.refresh_views()
        self.tool_entry.focus_set()

    # ---- Retornos ----
    def _build_return_tab(self):
        ttk.Label(
            self.return_tab,
            text="Aquí ves lo montado en la máquina y puedes enviar piezas a afilado o por daño. "
            "Para regresar una herramienta a su rack usa la pestaña «Guardar en rack».",
            wraplength=1150, justify=tk.LEFT,
        ).pack(anchor="w")
        bar = ttk.Frame(self.return_tab)
        bar.pack(fill=tk.X, pady=8)
        ttk.Label(bar, text="Máquina").pack(side=tk.LEFT, padx=(0, 6))
        self.return_machine_combo = ttk.Combobox(
            bar, textvariable=self.return_machine_var, state="readonly", width=12
        )
        self.return_machine_combo.pack(side=tk.LEFT, padx=4)
        self.return_machine_combo.bind("<<ComboboxSelected>>", lambda _e: self.load_machine_tools())
        ttk.Button(bar, text="Actualizar", command=self.load_machine_tools).pack(side=tk.LEFT, padx=6)
        ttk.Label(bar, textvariable=self.return_part_var, font=("Segoe UI", 11, "bold")).pack(side=tk.LEFT, padx=16)

        columns = (
            ("tool", "ID físico", 90), ("tn", "TN", 120), ("category", "Componente", 100),
            ("station", "Estación", 80), ("angle", "Ángulo", 80), ("clearance", "Clearance", 90),
            ("home", "Su rack", 110),
        )
        frame, self.return_tree = self._tree(self.return_tab, columns, 14, selectmode="extended")
        frame.pack(fill=tk.BOTH, expand=True)

        service = ttk.LabelFrame(
            self.return_tab, text="Enviar seleccionadas a afilado o por daño (Ctrl/Shift + clic para varias)", padding=8
        )
        service.pack(fill=tk.X, pady=8)
        ttk.Label(service, text="Motivo").grid(row=0, column=0, sticky="w", padx=4)
        reason_combo = ttk.Combobox(
            service, textvariable=self.service_reason_var, values=list(SERVICE_REASONS), state="readonly", width=12
        )
        reason_combo.grid(row=0, column=1, padx=4)
        reason_combo.bind("<<ComboboxSelected>>", lambda _e: self._apply_service_destination())
        ttk.Label(service, text="Destino").grid(row=0, column=2, sticky="w", padx=(14, 4))
        self.service_destination_combo = ttk.Combobox(
            service, textvariable=self.service_destination_var, state="readonly", width=18
        )
        self.service_destination_combo.grid(row=0, column=3, padx=4)
        ttk.Label(service, text="Nota").grid(row=0, column=4, sticky="w", padx=(14, 4))
        ttk.Entry(service, textvariable=self.service_note_var, width=30).grid(row=0, column=5, padx=4)
        ttk.Button(service, text="Enviar seleccionadas", style="Accent.TButton", command=self.send_to_service).grid(
            row=0, column=6, padx=10
        )
        ttk.Label(self.return_tab, textvariable=self.return_status_var).pack(anchor="w")

    def _apply_service_destination(self):
        destinations = self._service_destinations()
        self.service_destination_combo["values"] = destinations
        self.service_destination_var.set(destinations[0] if destinations else "")

    def load_machine_tools(self):
        machine = self.return_machine_var.get()
        for child in self.return_tree.get_children():
            self.return_tree.delete(child)
        if not machine:
            self.return_part_var.set("")
            return
        data = self._api("GET", f"/machines/{machine}/tools")
        if data is None:
            return
        self.return_part_var.set(
            f"Número de parte en máquina: {data['drawing_name']}" if data["drawing_name"] else "Sin setup activo"
        )
        homes = {row["tool_id"]: row.get("home_rack") for row in self._api(
            "GET", "/tools/locate", params={"q": ""}) or []}
        for tool in data["tools"]:
            self.return_tree.insert(
                "", tk.END, iid=tool["tool_id"],
                values=(
                    tool["tool_id"], tool["tn"], CATEGORY_LABELS.get(tool["category"], tool["category"]),
                    tool["station"], tool["angle"] or "—", tool["clearance"] or "—",
                    homes.get(tool["tool_id"]) or "—",
                ),
            )
        count = len(data["tools"])
        self.return_status_var.set(
            f"{count} herramienta(s) montadas con ID físico." if count
            else "Esta máquina no tiene herramientas con ID físico registradas."
        )

    def send_to_service(self):
        machine = self.return_machine_var.get()
        tool_ids = list(self.return_tree.selection())
        destination = self.service_destination_var.get()
        if not tool_ids:
            messagebox.showwarning("Sin selección", "Selecciona las herramientas que salen.", parent=self.window)
            return
        if not machine or not destination:
            messagebox.showwarning("Faltan datos", "Selecciona máquina y destino.", parent=self.window)
            return
        reason = self.service_reason_var.get()
        if not messagebox.askyesno(
            "Confirmar",
            f"¿Enviar {len(tool_ids)} herramienta(s) de {machine} a {destination} por {reason.lower()}?",
            parent=self.window,
        ):
            return
        result = self._api(
            "POST",
            f"/machines/{machine}/return",
            {
                "tool_ids": tool_ids,
                "motivo": SERVICE_REASONS[reason],
                "destino_codigo": destination,
                "realizado_por": self.actor,
                "nota": self.service_note_var.get().strip() or None,
            },
        )
        if result is None:
            return
        self.service_note_var.set("")
        self.refresh_views()
        self.return_status_var.set(
            f"{len(result['returned'])} enviada(s) a {result['destination']}. Quedan en máquina: {result['remaining']}."
        )

    # ---- Pendientes ----
    def _build_pending_tab(self):
        ttk.Label(
            self.pending_tab,
            text="Cuando se entrega un kit, las herramientas del setup anterior pasan solas a esta lista: siguen "
            "físicamente en la máquina hasta que alguien las recoge. Se quitan al guardarlas en su rack "
            "(pestaña «Guardar en rack»). A las 72 h se marcan VENCIDO, salvo en máquinas estándar (torreta), "
            "donde las herramientas se reutilizan y solo se avisa «ESTÁNDAR».",
            wraplength=1150, justify=tk.LEFT,
        ).pack(anchor="w", pady=(0, 4))
        self.pending_summary_var = tk.StringVar()
        ttk.Label(
            self.pending_tab, textvariable=self.pending_summary_var, font=("Segoe UI", 10, "bold")
        ).pack(anchor="w", pady=(0, 6))
        columns = (
            ("tool", "ID físico", 90), ("tn", "TN", 110), ("category", "Componente", 100),
            ("machine", "Máquina", 90), ("drawing", "Setup anterior", 190), ("home", "Su rack", 100),
            ("age", "Antigüedad (h)", 110), ("status", "Aviso", 170),
        )
        frame, self.pending_tree = self._tree(self.pending_tab, columns, 9)
        frame.pack(fill=tk.BOTH, expand=True)
        self.pending_tree.tag_configure("overdue", background="#ffd6d6")
        self.pending_tree.tag_configure("standard", background="#fff4cc")

        ttk.Label(
            self.pending_tab,
            text="Herramientas en TOGU, mantenimiento o taller (por regresar a su rack):",
        ).pack(anchor="w", pady=(10, 4))
        columns = (
            ("tool", "ID físico", 90), ("tn", "TN", 110), ("category", "Componente", 100),
            ("clearance", "Clearance", 90), ("location", "Ubicación actual", 130),
            ("type", "Tipo", 110), ("home", "Su rack", 100),
        )
        frame, self.movable_tree = self._tree(self.pending_tab, columns, 7)
        frame.pack(fill=tk.BOTH, expand=True)

    def refresh_pending(self):
        rows = self._api("GET", "/pending-pickups")
        if rows is None:
            return
        for child in self.pending_tree.get_children():
            self.pending_tree.delete(child)
        overdue = standard = 0
        for row in rows:
            if row["overdue"]:
                status, tags = "VENCIDO", ("overdue",)
                overdue += 1
            elif row.get("standard_machine"):
                status, tags = "ESTÁNDAR · reuso esperado", ("standard",)
                standard += 1
            else:
                status, tags = "Pendiente", ()
            self.pending_tree.insert(
                "", tk.END,
                values=(
                    row["tool_id"], row["tn"], CATEGORY_LABELS.get(row["category"], row["category"]),
                    row["machine"], row["drawing_name"] or "—", row.get("home_rack") or "—",
                    row["age_hours"], status,
                ),
                tags=tags,
            )
        self.pending_summary_var.set(
            f"Total: {len(rows)}  ·  Vencidos: {overdue}  ·  En máquina estándar (reuso): {standard}"
        )

    def refresh_movable(self):
        rows = self._api("GET", "/tools/movable")
        if rows is None:
            return
        for child in self.movable_tree.get_children():
            self.movable_tree.delete(child)
        for row in rows:
            self.movable_tree.insert(
                "", tk.END,
                values=(
                    row["tool_id"], row["tn"], CATEGORY_LABELS.get(row["category"], row["category"]),
                    row["clearance"], row["location"], row["location_type"], row.get("home_rack") or "—",
                ),
            )
