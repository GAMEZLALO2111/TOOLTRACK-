import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import requests

API_URL = "http://127.0.0.1:8000/workflow"


def fetch_rack_codes(parent=None):
    """Active rack codes from the location catalog, for dropdown lists."""
    try:
        response = requests.get(f"{API_URL}/locations", timeout=10)
        response.raise_for_status()
        return sorted(
            item["codigo"] for item in response.json() if item["tipo"] == "rack" and item["activa"]
        )
    except (requests.RequestException, ValueError, KeyError) as error:
        messagebox.showerror(
            "Racks", f"No se pudo cargar la lista de racks:\n{error}", parent=parent
        )
        return []


def build_operator_pick_list(comparison):
    new_setup = comparison["new_setup"]
    clearance_min = new_setup.get("clearance_min")
    clearance_max = new_setup.get("clearance_max")
    clearance_spec = (
        f"{clearance_min}–{clearance_max} in"
        if clearance_min is not None and clearance_max is not None
        else "Revisión manual requerida"
    )
    items = []

    for row in comparison["added"]:
        for category in ("Punzón", "Matriz"):
            items.append({
                "kind": "supply",
                "station": row["station"] or "—",
                "tn": row["tn"],
                "angle": row["angle"] or "—",
                "category": category,
                "specification": clearance_spec if category == "Matriz" else "—",
                "action": "Surtir al carro",
            })

    for row in comparison["unchanged"]:
        if row["die_action"] not in {"verify_clearance", "manual_die_selection"}:
            continue
        items.append({
            "kind": "verify",
            "station": row["station"] or "—",
            "tn": row["tn"],
            "angle": row["angle"] or "—",
            "category": "Matriz",
            "specification": clearance_spec,
            "action": (
                "Validar; surtir reemplazo si no cumple"
                if row["die_action"] == "verify_clearance"
                else "Confirmar matriz con ingeniería"
            ),
        })

    items.sort(key=lambda item: (str(item["station"]), item["tn"], item["category"]))
    return items


def find_open_kit_for_location(kits, machine, cart):
    return next(
        (
            kit for kit in kits
            if kit["machine"] == machine or kit["cart"] == cart
        ),
        None,
    )


class SetupWorkflowWindow:
    def __init__(self, parent, user):
        self.parent = parent
        self.user = user or {}
        self.actor = self.user.get("username") or "operator"
        self.kit_id = None
        self.locations = []
        self.window = None
        self.kit_components = []
        self.machine_open_kit = None

    def open(self):
        self.window = tk.Toplevel(self.parent)
        self.window.protocol("WM_DELETE_WINDOW", self._on_close)
        self.window.title("Tool Dispensing - Surtido")
        self.window.geometry("1280x800")
        self.window.minsize(1000, 660)

        self.setup_tab = ttk.Frame(self.window, padding=12)
        self.setup_tab.pack(fill=tk.BOTH, expand=True)
        self.kit_tab = self.setup_tab

        self.machine_var = tk.StringVar()
        self.cart_var = tk.StringVar()
        self.setup_files = {"current": None, "new": None}
        self._build_setup_tab()
        self._build_kit_tab()
        self.refresh_locations(show_error=True)
        return self.window

    def _api(self, method, path, payload=None, params=None):
        try:
            response = requests.request(
                method,
                f"{API_URL}{path}",
                json=payload,
                params=params,
                timeout=15,
            )
        except requests.RequestException as error:
            messagebox.showerror(
                "Error de conexión",
                f"No se pudo conectar con TOOLTRACK:\n{error}",
                parent=self.window,
            )
            return None
        if not response.ok:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            messagebox.showerror(
                "Error de operación",
                f"{detail or response.reason} (HTTP {response.status_code})",
                parent=self.window,
            )
            return None
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as error:
            messagebox.showerror(
                "Respuesta inválida",
                f"La API devolvió una respuesta que no es JSON:\n{error}",
                parent=self.window,
            )
            return None

    def _build_setup_tab(self):
        self._pick_list = []
        self.flow_frame = ttk.Frame(self.setup_tab)
        self.flow_frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(
            self.flow_frame,
            text="Preparar surtido",
            font=("Segoe UI", 17, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            self.flow_frame,
            text="Selecciona la máquina, busca el número de parte actual y el nuevo, revisa qué se necesita y comienza el surtido.",
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(2, 7))

        machine_frame = ttk.LabelFrame(self.flow_frame, text="1. ¿Para qué máquina?", padding=6)
        machine_frame.pack(fill=tk.X, pady=(0, 5))
        ttk.Label(machine_frame, text="Máquina").pack(side=tk.LEFT, padx=(3, 5))
        self.machine_combo = ttk.Combobox(
            machine_frame,
            textvariable=self.machine_var,
            state="readonly",
            width=15,
        )
        self.machine_combo.pack(side=tk.LEFT, padx=4)
        self.machine_combo.bind("<<ComboboxSelected>>", self._machine_selected)
        ttk.Button(
            machine_frame,
            text="Actualizar ubicaciones",
            command=self.refresh_locations,
        ).pack(side=tk.LEFT, padx=(10, 3))
        ttk.Button(
            machine_frame,
            text="Retomar surtido abierto",
            command=self.resume_open_kit,
        ).pack(side=tk.RIGHT, padx=3)
        ttk.Button(
            machine_frame,
            text="Máquinas en tiempo real / Traspaso",
            command=self.show_running_board,
        ).pack(side=tk.RIGHT, padx=3)
        self.running_var = tk.StringVar(value="")
        ttk.Label(
            machine_frame,
            textvariable=self.running_var,
            font=("Segoe UI", 10, "bold"),
            foreground="#174c7e",
        ).pack(side=tk.LEFT, padx=12)

        self.machine_banner = ttk.Frame(self.flow_frame)
        self.machine_banner_var = tk.StringVar()
        ttk.Label(
            self.machine_banner,
            textvariable=self.machine_banner_var,
            foreground="#7a4b00",
            font=("Segoe UI", 10, "bold"),
        ).pack(side=tk.LEFT, padx=6, pady=4)
        ttk.Button(
            self.machine_banner, text="Cancelar ese surtido", command=self._cancel_machine_open_kit
        ).pack(side=tk.RIGHT, padx=4)
        ttk.Button(
            self.machine_banner, text="Retomarlo", command=self._resume_machine_open_kit
        ).pack(side=tk.RIGHT, padx=4)

        setups_frame = ttk.LabelFrame(
            self.flow_frame,
            text="2. Número de parte",
            padding=6,
        )
        self.setups_frame = setups_frame
        setups_frame.pack(fill=tk.X, pady=(0, 5))
        self.part_current_var = tk.StringVar()
        self.part_new_var = tk.StringVar()
        self.part_combos = {}
        self.part_suggestions = {}
        self.stored_parts = []
        self.stored_machine_type = ""
        self.setup_path_vars = {
            "current": tk.StringVar(value="Sin archivo cargado"),
            "new": tk.StringVar(value="Sin archivo cargado"),
        }
        for column, key, title, part_var in (
            (0, "current", "Setup actual", self.part_current_var),
            (1, "new", "Nuevo setup", self.part_new_var),
        ):
            panel = ttk.LabelFrame(setups_frame, text=title, padding=5)
            panel.grid(
                row=0,
                column=column,
                sticky="nsew",
                padx=(0, 4) if column == 0 else (4, 0),
            )
            setups_frame.columnconfigure(column, weight=1)
            ttk.Label(panel, text="Número de parte").grid(row=0, column=0, sticky="w")
            part_entry = ttk.Combobox(panel, textvariable=part_var, width=25)
            part_entry.grid(row=0, column=1, sticky="ew", padx=4)
            part_entry.bind("<KeyRelease>", lambda event, setup_key=key: self._part_typed(setup_key, event))
            part_entry.bind(
                "<<ComboboxSelected>>",
                lambda _event, setup_key=key: self._select_stored_program(setup_key),
            )
            self.part_combos[key] = part_entry
            suggestions = tk.Listbox(panel, height=5, exportselection=False)
            suggestions.bind(
                "<<ListboxSelect>>", lambda _event, setup_key=key: self._suggestion_chosen(setup_key)
            )
            self.part_suggestions[key] = suggestions
            ttk.Label(
                panel,
                textvariable=self.setup_path_vars[key],
                foreground="#555555",
                wraplength=400,
            ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(3, 0))
            panel.columnconfigure(1, weight=1)

        compare_action = ttk.Frame(self.flow_frame)
        compare_action.pack(fill=tk.X, pady=(0, 4))
        self.compare_button = ttk.Button(
            compare_action,
            text="3. Comparar setups",
            command=self.compare_setup_files,
            state=tk.DISABLED,
        )
        self.compare_button.pack(side=tk.LEFT)
        self.workflow_status_var = tk.StringVar(
            value="Elige la máquina y busca los dos números de parte."
        )
        ttk.Label(
            compare_action,
            textvariable=self.workflow_status_var,
            font=("Segoe UI", 9),
        ).pack(side=tk.LEFT, padx=10)

        self.comparison_meta_var = tk.StringVar(value="Aún no hay una comparación cargada.")
        self.comparison_meta_label = ttk.Label(
            self.flow_frame,
            textvariable=self.comparison_meta_var,
            font=("Segoe UI", 10, "bold"),
            wraplength=1100,
        )
        self.comparison_meta_label.pack(anchor="w", pady=(2, 4))

        self.pick_counts = {}
        summary_bar = ttk.Frame(self.flow_frame)
        summary_bar.pack(fill=tk.X, pady=(0, 4))
        for key, label, color in (
            ("supply", "HERRAMIENTAS PARA SURTIR", "#145b9e"),
            ("verify", "MATRICES POR VALIDAR", "#9a6a00"),
        ):
            card = ttk.LabelFrame(summary_bar, text=label, padding=(10, 5))
            card.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
            value = tk.StringVar(value="—")
            self.pick_counts[key] = value
            ttk.Label(
                card,
                textvariable=value,
                font=("Segoe UI", 16, "bold"),
                foreground=color,
            ).pack(anchor="center")

        comparison_frame = ttk.LabelFrame(
            self.flow_frame,
            text="4. Qué necesita el operador",
            padding=5,
        )
        comparison_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 4))
        columns = ("quantity", "station", "tn", "angle", "category", "specification", "action")
        ttk.Style(self.window).configure("Comparison.Treeview", rowheight=28, font=("Segoe UI", 10))
        ttk.Style(self.window).configure("Comparison.Treeview.Heading", font=("Segoe UI", 10, "bold"))
        self.comparison_tree = ttk.Treeview(
            comparison_frame,
            columns=columns,
            show="headings",
            height=6,
            style="Comparison.Treeview",
        )
        headings = {
            "quantity": "Cant.",
            "station": "Estación",
            "tn": "Tool Number",
            "angle": "Ángulo",
            "category": "Herramienta",
            "specification": "Clearance requerido",
            "action": "Acción del operador",
        }
        widths = {
            "quantity": 60,
            "station": 90,
            "tn": 145,
            "angle": 75,
            "category": 115,
            "specification": 180,
            "action": 245,
        }
        for column in columns:
            self.comparison_tree.heading(column, text=headings[column])
            self.comparison_tree.column(
                column,
                width=widths[column],
                anchor="w" if column == "action" else "center",
                stretch=column == "action",
            )
        self.comparison_tree.tag_configure("verify", background="#fff2cf", foreground="#765300")
        self.comparison_tree.tag_configure("supply", background="#e9f2fb", foreground="#174c7e")
        compare_scroll = ttk.Scrollbar(
            comparison_frame,
            orient=tk.VERTICAL,
            command=self.comparison_tree.yview,
        )
        self.comparison_tree.configure(yscrollcommand=compare_scroll.set)
        self.comparison_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        compare_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.start_kit_button = ttk.Button(
            self.flow_frame,
            text="Iniciar surtido",
            command=self.start_kit,
            state=tk.DISABLED,
        )
        carts_frame = ttk.LabelFrame(
            self.flow_frame,
            text="Estado de carros — selecciona uno para retomarlo o liberarlo",
            padding=4,
        )
        carts_frame.pack(fill=tk.X, pady=(0, 3))
        self.carts_tree = ttk.Treeview(
            carts_frame,
            columns=("cart", "status", "machine", "progress", "missing"),
            show="headings",
            height=3,
            selectmode="browse",
        )
        for column, title, width, anchor in (
            ("cart", "Carro", 70, "center"),
            ("status", "Estado", 140, "center"),
            ("machine", "Máquina", 80, "center"),
            ("progress", "Avance", 70, "center"),
            ("missing", "Falta por escanear", 560, "w"),
        ):
            self.carts_tree.heading(column, text=title)
            self.carts_tree.column(column, width=width, anchor=anchor, stretch=column == "missing")
        self.carts_tree.tag_configure("free", background="#e8f5e9")
        self.carts_tree.tag_configure("busy", background="#fff2cf")
        self.carts_tree.tag_configure("ready", background="#e9f2fb")
        self.carts_tree.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.carts_tree.bind("<<TreeviewSelect>>", self._cart_row_selected)
        self.carts_tree.bind("<Double-1>", lambda _event: self._resume_selected_cart())
        cart_buttons = ttk.Frame(carts_frame)
        cart_buttons.pack(side=tk.RIGHT, padx=(6, 0))
        self.cart_resume_button = ttk.Button(
            cart_buttons, text="Retomar", command=self._resume_selected_cart, state=tk.DISABLED
        )
        self.cart_resume_button.pack(fill=tk.X, pady=1)
        self.cart_release_button = ttk.Button(
            cart_buttons, text="Liberar (cancelar)", command=self._release_selected_cart, state=tk.DISABLED
        )
        self.cart_release_button.pack(fill=tk.X, pady=1)
        self.cart_rows = {}

        start_frame = ttk.LabelFrame(self.flow_frame, text="5. ¿En qué carro se va a surtir?", padding=5)
        start_frame.pack(fill=tk.X, pady=(0, 3))
        ttk.Label(start_frame, text="Carro").pack(side=tk.LEFT, padx=(3, 5))
        self.cart_combo = ttk.Combobox(
            start_frame,
            textvariable=self.cart_var,
            state="readonly",
            width=13,
        )
        self.cart_combo.pack(side=tk.LEFT, padx=4)
        self.cart_combo.bind("<<ComboboxSelected>>", self._cart_selected)
        self.start_kit_button.pack(side=tk.RIGHT, padx=4)

    def _build_kit_tab(self):
        kit_frame = ttk.LabelFrame(
            self.kit_tab,
            text="6. Surtido en proceso",
            padding=6,
        )
        self.kit_frame = kit_frame
        kit_header = ttk.Frame(kit_frame)
        kit_header.pack(fill=tk.X, pady=(0, 4))
        self.active_kit_summary_var = tk.StringVar(value="Surtido activo")
        ttk.Label(
            kit_header,
            textvariable=self.active_kit_summary_var,
            font=("Segoe UI", 11, "bold"),
        ).pack(side=tk.LEFT, anchor="w")
        ttk.Button(
            kit_header,
            text="Volver a la comparación",
            command=self._show_setup_stage,
        ).pack(side=tk.RIGHT)
        self.setup_status_var = tk.StringVar(
            value="Completa los pasos anteriores para iniciar el surtido."
        )
        ttk.Label(
            kit_frame,
            textvariable=self.setup_status_var,
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", pady=(0, 3))

        components_frame = ttk.LabelFrame(
            kit_frame,
            text="Herramientas pendientes del kit — doble clic para elegir manualmente",
            padding=4,
        )
        components_frame.pack(fill=tk.BOTH, expand=True)
        columns = (
            "station", "tn", "angle", "category", "action",
            "previous", "tool", "state", "component_id",
        )
        self.components_tree = ttk.Treeview(
            components_frame,
            columns=columns,
            show="headings",
            height=5,
        )
        self.components_tree.tag_configure("ready", background="#e8f5e9")
        self.components_tree.tag_configure("missing", background="#e9f2fb")
        headers = {
            "station": "Estación",
            "tn": "Tool Number",
            "angle": "Ángulo",
            "category": "Herramienta",
            "action": "Acción",
            "previous": "ID anterior",
            "tool": "ID asignado",
            "state": "Estado",
            "component_id": "Componente ID",
        }
        widths = {
            "station": 80, "tn": 115, "angle": 80, "category": 100,
            "action": 100, "previous": 115, "tool": 115, "state": 100,
            "component_id": 100,
        }
        for column in columns:
            self.components_tree.heading(column, text=headers[column])
            self.components_tree.column(
                column,
                width=widths[column],
                anchor="center",
            )
        self.components_tree.column("component_id", width=0, stretch=False)
        kit_scroll = ttk.Scrollbar(
            components_frame,
            orient=tk.VERTICAL,
            command=self.components_tree.yview,
        )
        self.components_tree.configure(yscrollcommand=kit_scroll.set)
        self.components_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        kit_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.components_tree.bind("<Double-1>", self.select_component_tool)

        scan_bar = ttk.LabelFrame(
            kit_frame,
            text="Escanea el ID físico de la herramienta — TOOLTRACK valida TN, tipo y clearance",
            padding=5,
        )
        scan_bar.pack(fill=tk.X, pady=(4, 2))
        ttk.Label(scan_bar, text="ID físico").grid(row=0, column=0, sticky="w")
        self.physical_id_var = tk.StringVar()
        self.physical_id_entry = ttk.Entry(
            scan_bar,
            textvariable=self.physical_id_var,
            width=32,
            state=tk.DISABLED,
        )
        self.physical_id_entry.grid(row=0, column=1, sticky="ew", padx=4)
        self.physical_id_entry.bind("<Return>", self.scan_kit_tool)
        self.assign_scanned_button = ttk.Button(
            scan_bar,
            text="Validar y asignar al carro",
            command=self.scan_kit_tool,
            state=tk.DISABLED,
        )
        self.assign_scanned_button.grid(row=0, column=2, padx=(4, 0))
        self.scan_status_var = tk.StringVar(value="Retoma o inicia un surtido para escanear un ID físico.")
        ttk.Label(
            scan_bar,
            textvariable=self.scan_status_var,
            foreground="#555555",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 0))
        scan_bar.columnconfigure(1, weight=1)

        action_bar = ttk.Frame(kit_frame)
        action_bar.pack(fill=tk.X, pady=(4, 2))
        self.kit_action_buttons = {}
        for key, label, command in (
            ("pause", "Pausar kit", self.pause_kit),
            ("resume", "Reanudar kit", self.resume_kit),
            ("cancel", "Cancelar kit", self.cancel_kit),
            ("qr", "Generar QR final", self.generate_qr),
            ("refresh", "Actualizar kit", self.refresh_kit),
        ):
            button = ttk.Button(action_bar, text=label, command=command)
            button.pack(side=tk.LEFT, padx=3)
            self.kit_action_buttons[key] = button

        delivery = ttk.LabelFrame(
            kit_frame,
            text="Entrega en Manufactura — 1) escanea el QR de la máquina, 2) escanea el QR del kit",
            padding=6,
        )
        delivery.pack(fill=tk.X, pady=(3, 0))
        self.delivery_machine_var = tk.StringVar()
        ttk.Label(delivery, text="1. Máquina").pack(side=tk.LEFT, padx=(0, 4))
        self.delivery_machine_entry = ttk.Entry(delivery, textvariable=self.delivery_machine_var, width=22)
        self.delivery_machine_entry.pack(side=tk.LEFT, padx=(0, 10))
        self.delivery_machine_entry.bind("<Return>", lambda _event: self.delivery_entry.focus_set())
        ttk.Label(delivery, text="2. Kit").pack(side=tk.LEFT, padx=(0, 4))
        self.delivery_scan_var = tk.StringVar()
        self.delivery_entry = ttk.Entry(delivery, textvariable=self.delivery_scan_var)
        self.delivery_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        self.delivery_entry.bind("<Return>", lambda _event: self.confirm_delivery())
        self.delivery_button = ttk.Button(
            delivery,
            text="Confirmar entrega",
            command=self.confirm_delivery,
        )
        self.delivery_button.pack(side=tk.RIGHT)
        self._set_kit_controls_enabled(False)

    def _show_setup_stage(self):
        self.kit_frame.pack_forget()
        self.flow_frame.pack(fill=tk.BOTH, expand=True)

    def _show_kit_stage(self):
        self.flow_frame.pack_forget()
        self.kit_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 4))

    def _set_kit_controls_enabled(self, enabled, kit_state=None, complete=False):
        states = {
            "pause": enabled and kit_state == "preparing",
            "resume": enabled and kit_state == "paused",
            "cancel": enabled and kit_state in {"preparing", "paused", "ready"},
            "qr": enabled and complete and kit_state in {"preparing", "paused", "ready"},
            "refresh": enabled,
        }
        for key, button in self.kit_action_buttons.items():
            button.configure(state=tk.NORMAL if states[key] else tk.DISABLED)
        scanning_enabled = enabled and kit_state in {"preparing", "paused"}
        self.physical_id_entry.configure(
            state=tk.NORMAL if scanning_enabled else tk.DISABLED
        )
        self.assign_scanned_button.configure(
            state=tk.NORMAL if scanning_enabled else tk.DISABLED
        )
        delivery_enabled = enabled and kit_state == "ready"
        self.delivery_entry.configure(state=tk.NORMAL if delivery_enabled else tk.DISABLED)
        self.delivery_button.configure(state=tk.NORMAL if delivery_enabled else tk.DISABLED)

    def _build_pending_tab(self):
        pending_frame = ttk.LabelFrame(
            self.pending_tab, text="Herramientas salientes pendientes de recoger", padding=6
        )
        pending_frame.pack(fill=tk.BOTH, expand=True)
        columns = ("tool", "tn", "category", "machine", "drawing", "age", "status", "pending_id")
        self.pending_tree = ttk.Treeview(pending_frame, columns=columns, show="headings", height=9)
        labels = {
            "tool": "ID físico", "tn": "TN", "category": "Componente", "machine": "Máquina",
            "drawing": "Setup anterior", "age": "Antigüedad (h)", "status": "Aviso",
            "pending_id": "Pendiente ID",
        }
        for column in columns:
            self.pending_tree.heading(column, text=labels[column])
            self.pending_tree.column(column, width=115, anchor="center")
        self.pending_tree.column("pending_id", width=0, stretch=False)
        self.pending_tree.tag_configure("overdue", background="#ffd6d6")
        pending_scroll = ttk.Scrollbar(
            pending_frame, orient=tk.VERTICAL, command=self.pending_tree.yview
        )
        self.pending_tree.configure(yscrollcommand=pending_scroll.set)
        self.pending_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        pending_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        move_bar = ttk.Frame(self.pending_tab, padding=(0, 6))
        move_bar.pack(fill=tk.X)
        self.pending_destination_var = tk.StringVar()
        self.pending_destination_combo = ttk.Combobox(
            move_bar, textvariable=self.pending_destination_var, state="readonly", width=22
        )
        self.pending_destination_combo.pack(side=tk.LEFT, padx=4)
        ttk.Button(
            move_bar, text="Registrar recogida directa", command=self.move_pending_pickup
        ).pack(side=tk.LEFT, padx=4)
        ttk.Button(move_bar, text="Actualizar pendientes", command=self.refresh_pending).pack(
            side=tk.LEFT, padx=4
        )

        movable_frame = ttk.LabelFrame(
            self.pending_tab,
            text="Herramientas actualmente en TOGU, mantenimiento o taller",
            padding=6,
        )
        movable_frame.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        self.movable_tree = ttk.Treeview(
            movable_frame,
            columns=("tool", "tn", "category", "clearance", "location", "type"),
            show="headings",
            height=7,
        )
        movable_labels = {
            "tool": "ID físico", "tn": "TN", "category": "Componente",
            "clearance": "Clearance", "location": "Ubicación actual", "type": "Tipo",
        }
        for column in self.movable_tree["columns"]:
            self.movable_tree.heading(column, text=movable_labels[column])
            self.movable_tree.column(column, width=150, anchor="center")
        movable_scroll = ttk.Scrollbar(
            movable_frame, orient=tk.VERTICAL, command=self.movable_tree.yview
        )
        self.movable_tree.configure(yscrollcommand=movable_scroll.set)
        self.movable_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        movable_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        movable_bar = ttk.Frame(self.pending_tab, padding=(0, 6))
        movable_bar.pack(fill=tk.X)
        self.movable_destination_var = tk.StringVar()
        self.movable_destination_combo = ttk.Combobox(
            movable_bar, textvariable=self.movable_destination_var, state="readonly", width=22
        )
        self.movable_destination_combo.pack(side=tk.LEFT, padx=4)
        ttk.Button(
            movable_bar, text="Mover herramienta seleccionada", command=self.move_available_tool
        ).pack(side=tk.LEFT, padx=4)
        ttk.Button(movable_bar, text="Actualizar movimientos", command=self.refresh_movable).pack(
            side=tk.LEFT, padx=4
        )

    def _build_locations_tab(self):
        form = ttk.LabelFrame(self.locations_tab, text="Registrar ubicación", padding=8)
        form.pack(fill=tk.X)
        self.location_code_var = tk.StringVar()
        self.location_name_var = tk.StringVar()
        self.location_type_var = tk.StringVar(value="rack")
        ttk.Label(form, text="Código").grid(row=0, column=0, sticky="w", padx=5)
        ttk.Entry(form, textvariable=self.location_code_var, width=18).grid(row=0, column=1, padx=5)
        ttk.Label(form, text="Nombre").grid(row=0, column=2, sticky="w", padx=5)
        ttk.Entry(form, textvariable=self.location_name_var, width=28).grid(row=0, column=3, padx=5)
        ttk.Label(form, text="Tipo").grid(row=0, column=4, sticky="w", padx=5)
        ttk.Combobox(
            form,
            textvariable=self.location_type_var,
            values=("rack", "cart", "machine", "togu", "maintenance", "workshop"),
            state="readonly",
            width=16,
        ).grid(row=0, column=5, padx=5)
        self.location_standard_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            form, text="Máquina estándar (torreta)", variable=self.location_standard_var
        ).grid(row=1, column=0, columnspan=4, sticky="w", padx=5, pady=(6, 0))
        ttk.Button(form, text="Guardar ubicación", command=self.create_location).grid(
            row=0, column=6, padx=8
        )

        columns = ("code", "name", "type", "active", "standard")
        self.locations_tree = ttk.Treeview(
            self.locations_tab, columns=columns, show="headings", height=18
        )
        for column, label, width in (
            ("code", "Código", 160), ("name", "Nombre", 320),
            ("type", "Tipo", 150), ("active", "Activa", 80), ("standard", "Estándar", 110),
        ):
            self.locations_tree.heading(column, text=label)
            self.locations_tree.column(column, width=width, anchor="center")
        self.locations_tree.pack(fill=tk.BOTH, expand=True, pady=10)
        actions = ttk.Frame(self.locations_tab)
        actions.pack(fill=tk.X)
        ttk.Button(
            actions, text="Marcar / quitar máquina estándar", command=self.toggle_standard_machine
        ).pack(side=tk.LEFT)
        ttk.Button(
            actions, text="QR de la máquina seleccionada", command=self.show_selected_machine_qr
        ).pack(side=tk.LEFT, padx=8)
        ttk.Button(
            actions, text="Actualizar ubicaciones", command=self.refresh_locations
        ).pack(side=tk.RIGHT)

    def show_selected_machine_qr(self):
        selected = self.locations_tree.selection()
        values = self.locations_tree.item(selected[0], "values") if selected else None
        if not values or values[2] != "machine":
            messagebox.showwarning("Selecciona máquina", "Selecciona una máquina de la lista.", parent=self.window)
            return
        self.show_machine_qr(values[0])

    def toggle_standard_machine(self):
        selected = self.locations_tree.selection()
        if not selected:
            messagebox.showwarning("Selecciona máquina", "Selecciona una máquina de la lista.", parent=self.window)
            return
        values = self.locations_tree.item(selected[0], "values")
        if values[2] != "machine":
            messagebox.showwarning("Solo máquinas", "Solo las máquinas pueden ser estándar.", parent=self.window)
            return
        result = self._api(
            "PATCH", f"/locations/{values[0]}/standard", {"estandar": values[4] != "Sí"}
        )
        if result is not None:
            self.refresh_locations()

    def _selected_location(self, kind):
        return next(
            (
                location["codigo"]
                for location in self.locations
                if location["tipo"] == kind and location["codigo"] == self.machine_var.get()
            ),
            "",
        )

    def _after_locations_loaded(self, active):
        """Hook for windows that need the active locations."""

    def refresh_locations(self, show_error=False):
        data = self._api("GET", "/locations")
        if data is None:
            if not show_error:
                return
            return
        self.locations = data
        active = [location for location in data if location["activa"]]
        self._after_locations_loaded(active)
        if hasattr(self, "machine_combo"):
            machines = [item["codigo"] for item in active if item["tipo"] == "machine"]
            carts = [item["codigo"] for item in active if item["tipo"] == "cart"]
            self.machine_combo["values"] = machines
            self.cart_combo["values"] = carts
            if self.machine_var.get() not in machines:
                self.machine_var.set("")
            if self.cart_var.get() not in carts:
                self.cart_var.set("")
            self._refresh_open_state()
        if hasattr(self, "pending_destination_combo"):
            move_destinations = [
                item["codigo"] for item in active if item["tipo"] in {"rack", "togu", "maintenance"}
            ]
            all_destinations = [
                item["codigo"] for item in active
                if item["tipo"] in {"rack", "togu", "maintenance", "workshop"}
            ]
            self.pending_destination_combo["values"] = move_destinations
            self.movable_destination_combo["values"] = all_destinations
            if self.pending_destination_var.get() not in move_destinations:
                self.pending_destination_var.set(move_destinations[0] if move_destinations else "")
            if self.movable_destination_var.get() not in all_destinations:
                self.movable_destination_var.set(all_destinations[0] if all_destinations else "")
        if not hasattr(self, "locations_tree"):
            return
        for item in self.locations_tree.get_children():
            self.locations_tree.delete(item)
        for location in data:
            self.locations_tree.insert(
                "", tk.END,
                values=(
                    location["codigo"], location["nombre"], location["tipo"],
                    "Sí" if location["activa"] else "No",
                    "Sí" if location.get("estandar") else "",
                ),
            )
        self.refresh_pending()
        self.refresh_movable()

    def create_location(self):
        result = self._api("POST", "/locations", {
            "codigo": self.location_code_var.get(),
            "nombre": self.location_name_var.get(),
            "tipo": self.location_type_var.get(),
            "estandar": self.location_standard_var.get(),
        })
        if result is None:
            return
        self.location_standard_var.set(False)
        self.location_code_var.set("")
        self.location_name_var.set("")
        self.refresh_locations()
        messagebox.showinfo("Ubicación registrada", result["codigo"], parent=self.window)

    def _read_setup_file(self, title="Seleccionar archivo de setup"):
        path = filedialog.askopenfilename(
            parent=self.window,
            title=title,
            filetypes=(("Archivos de setup", "*.stp"), ("Todos los archivos", "*.*")),
        )
        if not path:
            return None
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as source:
                return path, source.read()
        except OSError as error:
            messagebox.showerror("No se pudo leer el archivo", str(error), parent=self.window)
            return None

    def _machine_type(self):
        code = self.machine_var.get().strip()
        if not code:
            return ""
        profile = self._api("GET", f"/machine-profiles/{code}")
        raw = ((profile or {}).get("tipo_maquina") or "").upper().replace(" ", "")
        if raw in {"4I", "2I", "45STA"}:
            return raw
        if "45" in raw:
            return "45STA"
        if raw.startswith("2"):
            return "2I"
        if raw.startswith("4"):
            return "4I"
        return ""

    def _machine_selected(self, _event=None):
        machine_type = self._machine_type()
        self.stored_machine_type = machine_type
        parts = []
        if machine_type:
            programs = self._api("GET", "/programs", params={"tipo_maquina": machine_type})
            parts = sorted(item["numero_parte"] for item in (programs or []))
        self.stored_parts = parts
        for side, combo in self.part_combos.items():
            combo["values"] = parts
            self.part_suggestions[side].grid_remove()
        # A loaded program belongs to the previous machine type; drop it.
        for side in ("current", "new"):
            self.setup_files[side] = None
            (self.part_current_var if side == "current" else self.part_new_var).set("")
            self.setup_path_vars[side].set("Sin programa seleccionado")
        self._invalidate_setup_flow()
        self._refresh_open_state()
        if not machine_type:
            self.workflow_status_var.set(
                "Esta máquina no tiene tipo (4I/2I/45STA); defínelo en Máquinas."
            )
            return
        self._prefill_current_program()

    def show_running_board(self):
        dialog = tk.Toplevel(self.window)
        dialog.title("Máquinas en tiempo real")
        dialog.transient(self.window)
        dialog.geometry("780x460")
        ttk.Label(
            dialog,
            text="Número de parte corriendo en cada máquina",
            font=("Segoe UI", 11, "bold"),
        ).pack(anchor="w", padx=12, pady=(12, 4))
        columns = ("machine", "tipo", "part", "thickness", "kit")
        tree = ttk.Treeview(dialog, columns=columns, show="headings", selectmode="browse")
        for column, title, width in (
            ("machine", "Máquina", 80), ("tipo", "Tipo", 70), ("part", "Número de parte", 230),
            ("thickness", "Espesor", 70), ("kit", "Surtido", 250),
        ):
            tree.heading(column, text=title)
            tree.column(column, width=width, anchor="center")
        tree.pack(fill=tk.BOTH, expand=True, padx=12)
        rows = {}

        def load():
            data = self._api("GET", "/machines/running") or []
            for child in tree.get_children():
                tree.delete(child)
            rows.clear()
            labels = {"preparing": "en proceso", "paused": "pausado", "ready": "listo p/ entregar"}
            for row in data:
                tree.insert("", tk.END, iid=row["machine"], values=(
                    row["machine"], row["tipo"] or "—", row["numero_parte"] or "(vacía)",
                    row["thickness"] or "—",
                    f"{labels.get(row['kit_state'], row['kit_state'])} en {row['kit_cart']}"
                    if row["kit_state"] else "—",
                ))
                rows[row["machine"]] = row

        def transfer():
            selection = tree.selection()
            if not selection:
                messagebox.showinfo("Traspaso", "Selecciona la máquina de origen.", parent=dialog)
                return
            origin = rows[selection[0]]
            if not origin["numero_parte"]:
                messagebox.showinfo("Traspaso", f"{origin['machine']} no tiene número de parte.", parent=dialog)
                return
            free = [
                row["machine"] for row in rows.values()
                if not row["numero_parte"] and row["tipo"] == origin["tipo"]
                and not row["kit_state"] and row["machine"] != origin["machine"]
            ]
            if not free:
                messagebox.showinfo(
                    "Sin destino",
                    f"No hay máquinas {origin['tipo']} vacías. Traspasa primero el número que ocupa el destino.",
                    parent=dialog,
                )
                return
            ask = tk.Toplevel(dialog)
            ask.title("Traspaso")
            ask.transient(dialog)
            ask.grab_set()
            ttk.Label(
                ask, text=f"Pasar {origin['numero_parte']} de {origin['machine']} a:", padding=10
            ).pack()
            destination = tk.StringVar(value=free[0])
            ttk.Combobox(ask, textvariable=destination, values=free, state="readonly").pack(padx=10)
            ttk.Label(ask, text="Motivo (obligatorio):").pack(anchor="w", padx=10, pady=(8, 0))
            reason = tk.StringVar()
            ttk.Entry(ask, textvariable=reason, width=44).pack(padx=10, pady=(0, 8))

            def confirm():
                if not reason.get().strip():
                    messagebox.showwarning("Motivo requerido", "Escribe el motivo.", parent=ask)
                    return
                result = self._api("POST", "/machines/transfer", {
                    "origen": origin["machine"], "destino": destination.get(),
                    "motivo": reason.get().strip(), "realizado_por": self.actor,
                })
                if result is None:
                    return
                ask.destroy()
                load()
                self._refresh_open_state()
                if self.machine_var.get():
                    self._machine_selected()
                messagebox.showinfo(
                    "Traspaso hecho",
                    f"{result['numero_parte']} ahora está en {result['destino']}. "
                    f"Herramientas movidas: {result['herramientas_movidas']}.",
                    parent=dialog,
                )

            ttk.Button(ask, text="Traspasar", command=confirm).pack(pady=(0, 10))

        buttons = ttk.Frame(dialog)
        buttons.pack(fill=tk.X, padx=12, pady=10)
        ttk.Button(buttons, text="Traspasar número de parte…", command=transfer).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Actualizar", command=load).pack(side=tk.LEFT, padx=6)
        ttk.Button(buttons, text="Cerrar", command=dialog.destroy).pack(side=tk.RIGHT)
        load()

    def _prefill_current_program(self):
        """The program mounted on the machine is the natural 'current' setup."""
        active = self._api(
            "GET", f"/machines/{self.machine_var.get()}/active-setup"
        ) or {}
        name = (active.get("nombre_archivo") or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
        if name.lower().endswith(".stp"):
            name = name[:-4]
        self.running_var.set(
            f"▶ Corriendo en {self.machine_var.get()}: {name}" if name
            else f"{self.machine_var.get()} sin número de parte registrado"
        )
        match = next(
            (p for p in self.stored_parts if p.upper() == name.upper()), None
        ) or next((p for p in self.stored_parts if p.upper() in name.upper()), None)
        if match:
            self.part_current_var.set(match)
            self._select_stored_program("current")
            self.workflow_status_var.set(
                f"{self.machine_var.get()} tiene montado {match}. Busca el número de parte nuevo."
            )
        elif name:
            self.workflow_status_var.set(
                f"El setup montado ({name}) no está guardado en el sistema; "
                "elige el actual manualmente."
            )

    def _refresh_open_state(self):
        """Keeps the cart list and machine banner in sync with unfinished kits."""
        kits = self._api("GET", "/kits")
        if kits is None:
            return
        state_labels = {"preparing": "en proceso", "paused": "pausado", "ready": "listo para entregar"}
        busy_carts = {kit["cart"] for kit in kits}
        free = [
            item["codigo"] for item in self.locations
            if item["tipo"] == "cart" and item["activa"] and item["codigo"] not in busy_carts
        ]
        self.cart_combo["values"] = free
        if self.cart_var.get() not in free:
            self.cart_var.set(free[0] if free else "")
        self.machine_open_kit = next(
            (kit for kit in kits if kit["machine"] == self.machine_var.get()), None
        )
        self._fill_carts_tree(kits)
        if self.machine_open_kit:
            kit = self.machine_open_kit
            needed = [c for c in kit["components"] if c["action"] != "keep"]
            done = sum(c["state"] != "missing" for c in needed)
            self.machine_banner_var.set(
                f"⚠ {kit['machine']} ya tiene un surtido {state_labels.get(kit['state'], kit['state'])} "
                f"en {kit['cart']} ({done}/{len(needed)}). Retómalo o cancélalo para hacer otro."
            )
            self.machine_banner.pack(fill=tk.X, pady=(0, 5), before=self.setups_frame)
        else:
            self.machine_banner.pack_forget()
        if not free and not self.machine_open_kit:
            self.workflow_status_var.set("No hay carros libres; termina o cancela algún surtido abierto.")
        self._update_start_button()

    def _update_start_button(self):
        ready = bool(
            getattr(self, "comparison_result", None)
            and self.machine_var.get()
            and self.cart_var.get()
            and not self.machine_open_kit
        )
        self.start_kit_button.configure(state=tk.NORMAL if ready else tk.DISABLED)

    def _resume_machine_open_kit(self):
        if self.machine_open_kit:
            self.cart_var.set(self.machine_open_kit["cart"])
            self._display_kit(self.machine_open_kit)

    def _ask_reason(self, title, prompt):
        reason = simpledialog.askstring(title, prompt, parent=self.window)
        if reason is None:
            return None
        if not reason.strip():
            messagebox.showwarning("Motivo requerido", "Escribe un motivo para continuar.", parent=self.window)
            return None
        return reason.strip()

    def _fill_carts_tree(self, kits):
        state_labels = {"preparing": "Surtiendo", "paused": "Pausado", "ready": "Listo p/ entregar"}
        by_cart = {kit["cart"]: kit for kit in kits}
        self.cart_rows = {}
        for row in self.carts_tree.get_children():
            self.carts_tree.delete(row)
        carts = sorted(
            item["codigo"] for item in self.locations if item["tipo"] == "cart" and item["activa"]
        )
        for cart in carts:
            kit = by_cart.get(cart)
            if not kit:
                self.carts_tree.insert("", tk.END, iid=cart, values=(cart, "Libre", "—", "—", ""), tags=("free",))
                continue
            needed = [c for c in kit["components"] if c["action"] != "keep"]
            missing = [c for c in needed if c["state"] == "missing"]
            if kit["state"] == "ready":
                note = "Nada. Falta escanear QR de máquina y de kit en Manufactura para entregar."
            else:
                names = ", ".join(
                    f"{c['tn']} {'punzón' if c['category'] == 'punzon' else 'matriz'}" for c in missing[:6]
                )
                note = f"{len(missing)} pendiente(s): {names}" + ("…" if len(missing) > 6 else "")
            if kit.get("reason"):
                note += f"  | Motivo: {kit['reason']}"
            self.carts_tree.insert(
                "", tk.END, iid=cart,
                values=(
                    cart, state_labels.get(kit["state"], kit["state"]), kit["machine"],
                    f"{len(needed) - len(missing)}/{len(needed)}", note,
                ),
                tags=("ready" if kit["state"] == "ready" else "busy",),
            )
            self.cart_rows[cart] = kit
        self.cart_resume_button.configure(state=tk.DISABLED)
        self.cart_release_button.configure(state=tk.DISABLED)

    def _cart_row_selected(self, _event=None):
        selection = self.carts_tree.selection()
        if not selection:
            return
        cart = selection[0]
        busy = cart in self.cart_rows
        self.cart_resume_button.configure(state=tk.NORMAL if busy else tk.DISABLED)
        self.cart_release_button.configure(state=tk.NORMAL if busy else tk.DISABLED)
        if not busy:
            self.cart_var.set(cart)
            self._update_start_button()

    def _selected_cart_kit(self):
        selection = self.carts_tree.selection()
        return self.cart_rows.get(selection[0]) if selection else None

    def _resume_selected_cart(self):
        kit = self._selected_cart_kit()
        if not kit:
            return
        self.machine_var.set(kit["machine"])
        self.cart_var.set(kit["cart"])
        self._display_kit(kit)

    def _release_selected_cart(self):
        kit = self._selected_cart_kit()
        if kit:
            self._cancel_kit(kit)

    def _cancel_machine_open_kit(self):
        if self.machine_open_kit:
            self._cancel_kit(self.machine_open_kit)

    def _cancel_kit(self, kit):
        if not messagebox.askyesno(
            "Cancelar surtido",
            f"Se cancelará el surtido de {kit['machine']} en {kit['cart']} y las herramientas "
            "escaneadas regresan a su rack. ¿Continuar?",
            parent=self.window,
        ):
            return
        reason = self._ask_reason("Motivo de cancelación", "¿Por qué se cancela este surtido?")
        if not reason:
            return
        result = self._api(
            "POST", f"/kits/{kit['id']}/cancel", {"realizado_por": self.actor, "motivo": reason}
        )
        if result is None:
            return
        if self.kit_id == kit["id"]:
            self._clear_active_kit()
        self._refresh_open_state()
        self.workflow_status_var.set("Surtido cancelado. Ya puedes hacer uno nuevo.")

    def _part_typed(self, side, event=None):
        if event is not None and event.keysym in {"Up", "Down", "Left", "Right", "Tab", "Shift_L", "Shift_R"}:
            return
        part_var = self.part_current_var if side == "current" else self.part_new_var
        text = part_var.get().strip().upper()
        loaded = self.setup_files[side]
        if loaded and str(loaded["path"]).startswith("Sistema:"):
            self.setup_files[side] = None
            self.setup_path_vars[side].set("Sin archivo cargado")
        self._invalidate_setup_flow()
        box = self.part_suggestions[side]
        if not text:
            box.grid_remove()
            return
        if not self.machine_var.get():
            self.setup_path_vars[side].set("Primero selecciona la máquina.")
            return
        if not self.stored_machine_type:
            return
        matches = [part for part in self.stored_parts if text in part.upper()]
        if event is not None and event.keysym == "Return" and len(matches) == 1:
            part_var.set(matches[0])
            self._select_stored_program(side)
            return
        if matches:
            box.delete(0, tk.END)
            for part in matches:
                box.insert(tk.END, part)
            box.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(3, 0))
            self.setup_path_vars[side].set(
                f"{len(matches)} coincidencia(s) para {self.stored_machine_type}. Elige una."
            )
            return
        box.grid_remove()
        others = self._api("GET", "/programs", params={"buscar": text}) or []
        types = sorted({item["tipo_maquina"] for item in others})
        if types:
            self.setup_path_vars[side].set(
                f"No hay programa {self.stored_machine_type} para ese número; "
                f"solo existe para: {', '.join(types)}. No se puede usar en esta máquina."
            )
        else:
            self.setup_path_vars[side].set("Ese número de parte no está en el sistema.")

    def _suggestion_chosen(self, side):
        box = self.part_suggestions[side]
        selection = box.curselection()
        if not selection:
            return
        part_var = self.part_current_var if side == "current" else self.part_new_var
        part_var.set(box.get(selection[0]))
        box.grid_remove()
        self._select_stored_program(side)

    def _select_stored_program(self, side):
        self.part_suggestions[side].grid_remove()
        part_var = self.part_current_var if side == "current" else self.part_new_var
        part_number = part_var.get().strip()
        machine_type = getattr(self, "stored_machine_type", "")
        if not part_number or not machine_type:
            return
        program = self._api("GET", f"/programs/{machine_type}/{part_number}")
        if not program:
            return
        self.setup_files[side] = {
            "path": f"Sistema: {part_number} ({machine_type}) rev {program['revision']}",
            "content": program["contenido"],
        }
        self.setup_path_vars[side].set(self.setup_files[side]["path"])
        self._invalidate_setup_flow()
        if self.setup_files["current"] and self.setup_files["new"]:
            self.compare_setup_files()

    def _setup_source_name(self, side):
        part_var = self.part_current_var if side == "current" else self.part_new_var
        part_number = part_var.get().strip()
        selected = self.setup_files[side]
        if part_number:
            return part_number if part_number.lower().endswith(".stp") else f"{part_number}.stp"
        return selected["path"].rsplit("\\", 1)[-1]

    def _invalidate_setup_flow(self, _event=None):
        self.comparison_result = None
        self._pick_list = []
        self.compare_button.configure(state=tk.NORMAL if (
            self.machine_var.get()
            and self.part_current_var.get().strip()
            and self.part_new_var.get().strip()
            and self.setup_files["current"]
            and self.setup_files["new"]
        ) else tk.DISABLED)
        self.start_kit_button.configure(state=tk.DISABLED)
        self.comparison_meta_var.set("Carga y compara los dos setups para ver qué surtir.")
        for counter in self.pick_counts.values():
            counter.set("—")
        for item in self.comparison_tree.get_children():
            self.comparison_tree.delete(item)
        if self.kit_id and getattr(self, "active_kit_machine", None) != self.machine_var.get():
            self._clear_active_kit("Selecciona la máquina correcta para retomar el surtido.")
        self.workflow_status_var.set(
            "Elige la máquina, busca los números de parte y compara."
        )

    def _cart_selected(self, _event=None):
        self._update_start_button()

    def compare_setup_files(self):
        current_file = self.setup_files["current"]
        new_file = self.setup_files["new"]
        if not current_file or not new_file:
            self._invalidate_setup_flow()
            return
        if not self.machine_var.get():
            messagebox.showwarning("Selecciona máquina", "Primero selecciona la máquina.", parent=self.window)
            return
        current_name = self._setup_source_name("current")
        new_name = self._setup_source_name("new")
        comparison = self._api("POST", "/setups/compare-files", {
            "setup_anterior_nombre": current_name,
            "setup_anterior_contenido": current_file["content"],
            "setup_nuevo_nombre": new_name,
            "setup_nuevo_contenido": new_file["content"],
        })
        if comparison is None:
            return
        self.comparison_result = comparison
        self._render_comparison(comparison)
        self.workflow_status_var.set(
            "Comparación lista. Revisa la lista y después inicia el surtido."
        )
        self._update_start_button()

    def _post_kit(self, payload):
        try:
            response = requests.post(f"{API_URL}/kits", json=payload, timeout=15)
        except requests.RequestException as error:
            messagebox.showerror("Error de conexión", str(error), parent=self.window)
            return None
        if response.ok:
            return response.json()
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        if response.status_code == 409 and str(detail).startswith("SETUP_NO_COINCIDE"):
            active = self._api("GET", f"/machines/{payload['maquina_codigo']}/active-setup") or {}
            registered = (active.get("nombre_archivo") or "?").replace("\\", "/").rsplit("/", 1)[-1]
            if not messagebox.askyesno(
                "El setup actual no coincide",
                f"El sistema tiene registrado en {payload['maquina_codigo']}:\n   {registered}\n\n"
                f"Tú elegiste como actual:\n   {self.part_current_var.get()}\n\n"
                "¿Es correcto lo que elegiste (lo que de verdad está montado)?\n"
                "Si dices Sí, se corrige el registro de la máquina y se pide un motivo.",
                parent=self.window,
            ):
                return None
            reason = self._ask_reason("Motivo de la corrección", "¿Por qué difiere el setup registrado?")
            if not reason:
                return None
            payload = dict(payload, corregir_actual=True, motivo_correccion=reason)
            return self._api("POST", "/kits", payload)
        messagebox.showerror(
            "Error de operación", f"{detail or response.reason} (HTTP {response.status_code})",
            parent=self.window,
        )
        return None

    def start_kit(self):
        if not getattr(self, "comparison_result", None):
            messagebox.showwarning(
                "Falta comparar",
                "Primero carga y compara el setup actual con el nuevo.",
                parent=self.window,
            )
            return
        if (
            self.kit_id
            and self.active_kit_machine == self.machine_var.get()
            and self.active_kit_state in {"preparing", "paused", "ready"}
        ):
            messagebox.showinfo(
                "Surtido ya iniciado",
                "Esta máquina ya tiene un surtido abierto. Continúa el surtido cargado o retómalo.",
                parent=self.window,
            )
            return
        if not self.machine_var.get() or not self.cart_var.get():
            messagebox.showwarning(
                "Falta ubicación",
                "Selecciona la máquina y el carro que llevará el surtido.",
                parent=self.window,
            )
            return
        open_kits = self._api("GET", "/kits")
        if open_kits is None:
            return
        existing_kit = find_open_kit_for_location(
            open_kits,
            self.machine_var.get(),
            self.cart_var.get(),
        )
        if existing_kit:
            reason = (
                f"La máquina {self.machine_var.get()}"
                if existing_kit["machine"] == self.machine_var.get()
                else f"El carro {self.cart_var.get()}"
            )
            if messagebox.askyesno(
                "Surtido ya iniciado",
                f"{reason} ya está asignado al surtido de la máquina "
                f"{existing_kit['machine']} en el carro {existing_kit['cart']}.\n\n"
                "¿Quieres retomar ese surtido? No se creará otro kit.",
                parent=self.window,
            ):
                self.machine_var.set(existing_kit["machine"])
                self.cart_var.set(existing_kit["cart"])
                self._display_kit(existing_kit)
                self.workflow_status_var.set(
                    f"Retomando surtido de {existing_kit['machine']} "
                    f"en el carro {existing_kit['cart']}."
                )
            return
        current_file = self.setup_files["current"]
        new_file = self.setup_files["new"]
        payload = {
            "maquina_codigo": self.machine_var.get(),
            "carro_codigo": self.cart_var.get(),
            "nombre_archivo": self._setup_source_name("new"),
            "contenido": new_file["content"],
            "creado_por": self.actor,
            "setup_anterior_nombre": self._setup_source_name("current"),
            "setup_anterior_contenido": current_file["content"],
        }
        kit = self._post_kit(payload)
        if kit is None:
            return
        self._display_kit(kit)
        self.start_kit_button.configure(state=tk.DISABLED)
        self._refresh_open_state()
        self.workflow_status_var.set(
            f"Surtido iniciado para {kit['machine']} en el carro {kit['cart']}."
        )
        if kit.get("warnings"):
            messagebox.showwarning(
                "Alertas de la máquina",
                f"El surtido se creó, pero revisa esto en {kit['machine']}:\n\n- "
                + "\n- ".join(kit["warnings"]),
                parent=self.window,
            )

    def _render_comparison(self, comparison):
        current = comparison["current_setup"]
        new = comparison["new_setup"]
        current_name = self.part_current_var.get().strip() or (
            current["drawing_name"] or current["source_name"]
        )
        new_name = self.part_new_var.get().strip() or (
            new["drawing_name"] or new["source_name"]
        )
        target_clearance = (
            f"{new['clearance_min']}–{new['clearance_max']} in"
            if new.get("clearance_min") is not None and new.get("clearance_max") is not None
            else "fuera de rango aprobado; requiere revisión manual"
        )
        self.comparison_meta_var.set(
            f"{current_name}  →  {new_name}    |    THICKNESS "
            f"{current['thickness']} → {new['thickness']} in    |    "
            f"Clearance objetivo de matriz: {target_clearance}"
        )
        self._pick_list = build_operator_pick_list(comparison)
        self.pick_counts["supply"].set(
            str(sum(item["kind"] == "supply" for item in self._pick_list))
        )
        self.pick_counts["verify"].set(
            str(sum(item["kind"] == "verify" for item in self._pick_list))
        )
        self._apply_pick_filter()

    def _apply_pick_filter(self, _event=None):
        if not hasattr(self, "comparison_tree"):
            return
        for child in self.comparison_tree.get_children():
            self.comparison_tree.delete(child)
        for item in self._pick_list:
            self.comparison_tree.insert(
                "",
                tk.END,
                values=(
                    1,
                    item["station"],
                    item["tn"],
                    item["angle"],
                    item["category"],
                    item["specification"],
                    item["action"],
                ),
                tags=(item["kind"],),
            )
        if not self._pick_list:
            self.comparison_tree.insert(
                "",
                tk.END,
                values=(
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "No se requieren herramientas nuevas para este cambio.",
                ),
                tags=("empty",),
            )

    def _display_kit(self, kit):
        self.kit_id = kit["id"]
        self.active_kit_state = kit["state"]
        self.active_kit_machine = kit["machine"]
        self.kit_components = kit["components"]
        self.setup_status_var.set(
            f"Surtido {kit['id'][:8]} | Máquina {kit['machine']} | "
            f"Carro {kit['cart']} | Estado: {kit['state']}"
        )
        pending_count = sum(
            component["action"] != "keep" and component["state"] == "missing"
            for component in kit["components"]
        )
        self.active_kit_summary_var.set(
            f"Máquina {kit['machine']}  ·  Carro {kit['cart']}  ·  "
            f"{pending_count} herramienta(s) pendiente(s)"
        )
        action_labels = {
            "keep": "Conservar",
            "new": "Agregar",
            "replace": "Reemplazar",
            "removed": "Retirar",
        }
        category_labels = {"punzon": "Punzón", "matriz": "Matriz"}
        state_labels = {"ready": "Listo", "missing": "Pendiente"}
        for child in self.components_tree.get_children():
            self.components_tree.delete(child)
        required_components = [
            component for component in kit["components"]
            if component["action"] != "keep"
        ]
        for component in required_components:
            self.components_tree.insert(
                "", tk.END,
                iid=str(component["component_id"]),
                values=(
                    component["station"],
                    component["tn"],
                    component["angle"] or "—",
                    category_labels.get(component["category"], component["category"]),
                    action_labels.get(component["action"], component["action"]),
                    component["previous_tool_id"] or "—",
                    component["tool_id"] or "—",
                    state_labels.get(component["state"], component["state"]),
                    component["component_id"],
                ),
                tags=(component["state"],),
            )
        if not required_components:
            self.components_tree.insert(
                "",
                tk.END,
                values=("", "", "", "", "", "", "", "No requiere herramientas nuevas", ""),
                tags=("empty",),
            )
        complete = all(
            component["state"] == "ready"
            and (component["action"] == "keep" or component["tool_id"])
            for component in kit["components"]
        )
        self._set_kit_controls_enabled(True, kit["state"], complete)
        self._show_kit_stage()
        if kit["state"] == "preparing":
            self.scan_status_var.set("Escanea el ID físico de la siguiente herramienta pendiente.")
        elif kit["state"] == "paused":
            self.scan_status_var.set("Surtido pausado. Presiona Reanudar para seguir escaneando.")
        elif kit["state"] == "ready":
            self.scan_status_var.set("Kit completo. Genera el QR final para continuar.")
        if kit["state"] == "preparing":
            self.physical_id_entry.focus_set()

    def resume_open_kit(self):
        kits = self._api("GET", "/kits")
        if kits is None:
            return
        if not kits:
            messagebox.showinfo("Sin surtidos", "No hay surtidos abiertos ni pausados.", parent=self.window)
            return
        state_labels = {"preparing": "En proceso", "paused": "Pausado", "ready": "Listo para entregar"}
        dialog = tk.Toplevel(self.window)
        dialog.title("Surtidos abiertos")
        dialog.transient(self.window)
        dialog.grab_set()
        ttk.Label(
            dialog,
            text="Selecciona el surtido que quieres continuar",
            font=("Segoe UI", 11, "bold"),
        ).pack(padx=12, pady=(12, 6), anchor="w")
        columns = ("machine", "cart", "state", "progress", "created")
        tree = ttk.Treeview(dialog, columns=columns, show="headings", height=8, selectmode="browse")
        for column, title, width in (
            ("machine", "Máquina", 90),
            ("cart", "Carro", 80),
            ("state", "Estado", 150),
            ("progress", "Avance", 90),
            ("created", "Creado", 150),
        ):
            tree.heading(column, text=title)
            tree.column(column, width=width, anchor="center")
        tree.pack(fill=tk.BOTH, expand=True, padx=12)
        by_item = {}
        for kit in kits:
            needed = [c for c in kit["components"] if c["action"] != "keep"]
            done = sum(c["state"] != "missing" for c in needed)
            item = tree.insert("", tk.END, values=(
                kit["machine"],
                kit["cart"],
                state_labels.get(kit["state"], kit["state"]),
                f"{done}/{len(needed)}",
                kit["created_at"][:16].replace("T", " "),
            ))
            by_item[item] = kit
        first = tree.get_children()
        if first:
            tree.selection_set(first[0])

        def open_selected(_event=None):
            selection = tree.selection()
            if not selection:
                return
            kit = by_item[selection[0]]
            dialog.destroy()
            self.machine_var.set(kit["machine"])
            self._display_kit(kit)

        tree.bind("<Double-1>", open_selected)
        buttons = ttk.Frame(dialog)
        buttons.pack(fill=tk.X, padx=12, pady=10)
        ttk.Button(buttons, text="Continuar surtido", command=open_selected).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="Cerrar", command=dialog.destroy).pack(side=tk.RIGHT, padx=6)

    def _clear_active_kit(self, status="Sin kit cargado."):
        self.kit_id = None
        self.active_kit_state = None
        self.active_kit_machine = None
        self.kit_components = []
        self.physical_id_var.set("")
        self.scan_status_var.set("Retoma o inicia un surtido para escanear un ID físico.")
        self.setup_status_var.set(status)
        for child in self.components_tree.get_children():
            self.components_tree.delete(child)
        self._set_kit_controls_enabled(False)
        self._show_setup_stage()

    def scan_kit_tool(self, _event=None):
        if not self.kit_id or self.active_kit_state not in {"preparing", "paused"}:
            self.scan_status_var.set("Primero inicia o retoma un surtido.")
            return "break"
        physical_id = self.physical_id_var.get().strip()
        if not physical_id:
            self.scan_status_var.set("Escanea o escribe el ID físico de la herramienta.")
            self.physical_id_entry.focus_set()
            return "break"
        result = self._api(
            "POST",
            f"/kits/{self.kit_id}/scan",
            {"herramienta_id": physical_id, "realizado_por": self.actor},
        )
        if result is not None:
            self.physical_id_var.set("")
            self._display_kit(result)
            assigned = next(
                (item for item in result["components"] if item["tool_id"] == physical_id),
                None,
            )
            if assigned:
                category = "Punzón" if assigned["category"] == "punzon" else "Matriz"
                self.scan_status_var.set(
                    f"ID {physical_id} aprobado: TN {assigned['tn']} · {category} · "
                    f"estación {assigned['station']}. Escanea la siguiente herramienta."
                )
            else:
                self.scan_status_var.set(
                    f"ID {physical_id} procesado. Escanea la siguiente herramienta."
                )
        else:
            self.scan_status_var.set(
                "No se asignó la herramienta. Corrige el ID o revisa el mensaje de error."
            )
            self.physical_id_entry.focus_set()
        return "break"

    def refresh_kit(self):
        if not self.kit_id:
            return
        kit = self._api("GET", f"/kits/{self.kit_id}")
        if kit is not None:
            self._display_kit(kit)

    def select_component_tool(self, _event=None):
        if not self.kit_id:
            return
        if getattr(self, "active_kit_state", None) not in {"preparing", "paused"}:
            return
        selected = self.components_tree.selection()
        if not selected:
            return
        values = self.components_tree.item(selected[0], "values")
        component_id = values[8]
        if not component_id or component_id == "—":
            return
        if values[4] == "Conservar":
            messagebox.showinfo(
                "Herramienta conservada",
                f"El ID {values[6]} permanece instalado; no se necesita seleccionar otra herramienta.",
                parent=self.window,
            )
            return
        candidates = self._api(
            "GET", f"/kits/{self.kit_id}/components/{component_id}/candidates"
        )
        if candidates is None:
            return
        if not candidates:
            messagebox.showinfo(
                "Sin candidatos",
                "No hay herramientas elegibles en un rack activo. Comprueba ID, TN, categoría y clearance.",
                parent=self.window,
            )
            return
        dialog = tk.Toplevel(self.window)
        dialog.title(f"Elegir herramienta física - TN {values[1]} {values[3]}")
        dialog.geometry("720x420")
        ttk.Label(
            dialog,
            text="Selecciona una fila o escanea/escribe el ID físico exacto.",
        ).pack(anchor="w", padx=10, pady=8)
        tree = ttk.Treeview(
            dialog,
            columns=("id", "type", "size", "clearance", "life", "location"),
            show="headings",
            height=12,
        )
        for column, label, width in (
            ("id", "ID físico", 130), ("type", "Tipo", 120), ("size", "Size", 100),
            ("clearance", "Clearance", 90), ("life", "Life", 90), ("location", "Rack", 120),
        ):
            tree.heading(column, text=label)
            tree.column(column, width=width, anchor="center")
        tree.pack(fill=tk.BOTH, expand=True, padx=10)
        for candidate in candidates:
            tree.insert(
                "", tk.END,
                values=(
                    candidate["id"], candidate["type"], candidate["size"],
                    candidate["clearance"], candidate["life"], candidate["location"],
                ),
            )
        id_var = tk.StringVar()
        entry_bar = ttk.Frame(dialog)
        entry_bar.pack(fill=tk.X, padx=10, pady=8)
        ttk.Label(entry_bar, text="ID físico").pack(side=tk.LEFT, padx=(0, 5))
        entry = ttk.Entry(entry_bar, textvariable=id_var)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        entry.focus_set()
        tree.bind(
            "<<TreeviewSelect>>",
            lambda _event: id_var.set(tree.item(tree.selection()[0], "values")[0])
            if tree.selection() else None,
        )

        def assign(event=None):
            physical_id = id_var.get().strip()
            if not physical_id:
                messagebox.showwarning("Falta ID", "Escanea o selecciona el ID físico.", parent=dialog)
                return
            result = self._api(
                "POST",
                f"/kits/{self.kit_id}/components/{component_id}/select",
                {"herramienta_id": physical_id, "realizado_por": self.actor},
            )
            if result is not None:
                self._display_kit(result)
                dialog.destroy()

        ttk.Button(entry_bar, text="Asignar al carro", command=assign).pack(side=tk.RIGHT, padx=5)
        entry.bind("<Return>", assign)
        ttk.Button(dialog, text="Cerrar", command=dialog.destroy).pack(anchor="e", padx=10, pady=(0, 8))

    def _kit_action(self, action, confirm=False, needs_reason=False):
        if not self.kit_id:
            messagebox.showwarning("Sin kit", "No hay un kit seleccionado.", parent=self.window)
            return None
        if confirm and not messagebox.askyesno(
            "Cancelar kit",
            "Las herramientas seleccionadas regresarán a su rack de origen. ¿Cancelar el kit?",
            parent=self.window,
        ):
            return None
        payload = {"realizado_por": self.actor}
        if needs_reason:
            reason = self._ask_reason(
                "Motivo", "¿Por qué se pausa / cancela este surtido?"
            )
            if not reason:
                return None
            payload["motivo"] = reason
        kit = self._api("POST", f"/kits/{self.kit_id}/{action}", payload)
        if kit is not None:
            self._display_kit(kit)
            self._refresh_open_state()
        return kit

    def pause_kit(self):
        self._kit_action("pause", needs_reason=True)

    def resume_kit(self):
        self._kit_action("resume")

    def cancel_kit(self):
        self._kit_action("cancel", confirm=True, needs_reason=True)

    def _on_close(self):
        """An in-progress kit can't be abandoned silently: pause or cancel it with a reason."""
        if self.kit_id and self.active_kit_state == "preparing":
            dialog = tk.Toplevel(self.window)
            dialog.title("Surtido en proceso")
            dialog.transient(self.window)
            dialog.grab_set()
            ttk.Label(
                dialog,
                text="Tienes un surtido en proceso. Para salir debes pausarlo\n"
                     "o cancelarlo (se pide un motivo).",
                padding=14,
            ).pack()
            buttons = ttk.Frame(dialog, padding=(14, 0, 14, 14))
            buttons.pack()

            def leave_after(action):
                dialog.destroy()
                kit = self._kit_action(
                    action, confirm=action == "cancel", needs_reason=True
                )
                if kit is not None:
                    self.window.destroy()

            ttk.Button(buttons, text="Pausar y salir", command=lambda: leave_after("pause")).pack(side=tk.LEFT, padx=4)
            ttk.Button(buttons, text="Cancelar surtido y salir", command=lambda: leave_after("cancel")).pack(side=tk.LEFT, padx=4)
            ttk.Button(buttons, text="Seguir aquí", command=dialog.destroy).pack(side=tk.LEFT, padx=4)
            return
        self.window.destroy()

    def show_machine_qr(self, machine_code=None):
        """QR to print and stick on a machine; scanned first when delivering a kit."""
        code = (machine_code or self.machine_var.get()).strip()
        if not code:
            messagebox.showwarning("Sin máquina", "Selecciona una máquina.", parent=self.window)
            return
        value = f"TOOLTRACK-MACHINE:{code}"
        try:
            import qrcode
            from PIL import ImageTk
            image = qrcode.make(value)
            photo = ImageTk.PhotoImage(image)
        except ImportError as error:
            messagebox.showerror("Falta dependencia QR", str(error), parent=self.window)
            return
        dialog = tk.Toplevel(self.window)
        dialog.title(f"QR de máquina {code}")
        dialog.transient(self.window)
        dialog.qr_image = photo
        ttk.Label(dialog, text=f"Máquina {code}", font=("Segoe UI", 14, "bold")).pack(padx=12, pady=(10, 4))
        ttk.Label(dialog, image=photo).pack(padx=12)
        ttk.Label(dialog, text=value).pack(padx=12, pady=6)

        def save_image():
            path = filedialog.asksaveasfilename(
                parent=dialog, title="Guardar QR de máquina", defaultextension=".png",
                filetypes=(("Imagen PNG", "*.png"),), initialfile=f"maquina_{code}.png",
            )
            if path:
                image.save(path)
                messagebox.showinfo("QR guardado", path, parent=dialog)

        ttk.Button(dialog, text="Guardar para imprimir...", command=save_image).pack(pady=(0, 10))

    def generate_qr(self):
        if not self.kit_id:
            messagebox.showwarning("Sin kit", "No hay un kit seleccionado.", parent=self.window)
            return
        result = self._api("POST", f"/kits/{self.kit_id}/qr")
        if result is None:
            return
        try:
            import qrcode
            from PIL import ImageTk
            image = qrcode.make(result["scan_value"])
            photo = ImageTk.PhotoImage(image)
        except ImportError as error:
            messagebox.showerror(
                "Falta dependencia QR",
                f"Instala las dependencias de requirements.txt para mostrar el QR:\n{error}",
                parent=self.window,
            )
            return
        dialog = tk.Toplevel(self.window)
        dialog.title("QR de entrega")
        dialog.transient(self.window)
        dialog.qr_image = photo
        ttk.Label(
            dialog,
            text=f"Kit listo para {result['machine']}. Escanea este QR en Manufactura.",
            wraplength=340,
        ).pack(padx=12, pady=10)
        ttk.Label(dialog, image=photo).pack(padx=12)
        ttk.Label(dialog, text=result["scan_value"], wraplength=360).pack(padx=12, pady=8)
        # Simula la lectura del QR para pruebas sin escáner.
        self.delivery_scan_var.set(result["scan_value"])

        def copy_value():
            self.window.clipboard_clear()
            self.window.clipboard_append(result["scan_value"])

        ttk.Button(dialog, text="Copiar valor del QR", command=copy_value).pack(pady=(0, 6))

        def save_image():
            path = filedialog.asksaveasfilename(
                parent=dialog,
                title="Guardar QR",
                defaultextension=".png",
                filetypes=(("Imagen PNG", "*.png"),),
                initialfile=f"kit_{result['kit_id'][:8]}.png",
            )
            if path:
                try:
                    image.save(path)
                except OSError as error:
                    messagebox.showerror(
                        "No se pudo guardar el QR",
                        str(error),
                        parent=dialog,
                    )
                    return
                messagebox.showinfo("QR guardado", path, parent=dialog)

        ttk.Button(dialog, text="Guardar QR...", command=save_image).pack(pady=(0, 10))

    def confirm_delivery(self):
        scan_value = self.delivery_scan_var.get().strip()
        machine_scan = self.delivery_machine_var.get().strip()
        if not machine_scan:
            messagebox.showwarning(
                "Escanea la máquina",
                "Primero escanea el QR pegado en la máquina donde se entrega el kit.",
                parent=self.window,
            )
            self.delivery_machine_entry.focus_set()
            return
        if not scan_value:
            messagebox.showwarning(
                "Escanea el QR",
                "Escanea o escribe el valor del QR del kit.",
                parent=self.window,
            )
            return
        result = self._api("POST", "/delivery/scan", {
            "scan_value": scan_value,
            "machine_scan": machine_scan,
            "realizado_por": self.actor,
        })
        if result is None:
            return
        self.delivery_scan_var.set("")
        self.delivery_machine_var.set("")
        self._clear_active_kit(
            f"Entrega confirmada en {result['machine']}. Setup activo: "
            f"{result['active_drawing'] or result['active_setup_id']}"
        )
        self.refresh_pending()
        self._machine_selected()
        messagebox.showinfo(
            f"Setup actualizado. Herramientas salientes pendientes de recoger: "
            f"{result['pending_pickups_created']}.",
            parent=self.window,
        )

    def refresh_pending(self):
        if not hasattr(self, "pending_tree"):
            return
        rows = self._api("GET", "/pending-pickups")
        if rows is None:
            return
        for child in self.pending_tree.get_children():
            self.pending_tree.delete(child)
        for row in rows:
            self.pending_tree.insert(
                "", tk.END,
                values=(
                    row["tool_id"], row["tn"], row["category"], row["machine"],
                    row["drawing_name"] or "—", row["age_hours"],
                    "VENCIDO" if row["overdue"] else "Pendiente", row["pending_id"],
                ),
                tags=("overdue",) if row["overdue"] else (),
            )

    def move_pending_pickup(self):
        selected = self.pending_tree.selection()
        if not selected:
            messagebox.showwarning(
                "Selecciona pendiente", "Selecciona la herramienta que ya fue recogida físicamente.",
                parent=self.window,
            )
            return
        values = self.pending_tree.item(selected[0], "values")
        destination = self.pending_destination_var.get()
        if not destination:
            messagebox.showwarning("Sin destino", "Registra y selecciona un destino.", parent=self.window)
            return
        result = self._api(
            "POST",
            f"/pending-pickups/{values[7]}/move",
            {"destino_codigo": destination, "realizado_por": self.actor},
        )
        if result is not None:
            self.refresh_pending()
            self.refresh_movable()

    def refresh_movable(self):
        if not hasattr(self, "movable_tree"):
            return
        rows = self._api("GET", "/tools/movable")
        if rows is None:
            return
        for child in self.movable_tree.get_children():
            self.movable_tree.delete(child)
        for row in rows:
            self.movable_tree.insert(
                "", tk.END,
                values=(
                    row["tool_id"], row["tn"], row["category"], row["clearance"],
                    row["location"], row["location_type"],
                ),
            )

    def move_available_tool(self):
        selected = self.movable_tree.selection()
        if not selected:
            messagebox.showwarning("Selecciona herramienta", "Selecciona una herramienta.", parent=self.window)
            return
        values = self.movable_tree.item(selected[0], "values")
        destination = self.movable_destination_var.get()
        if not destination:
            messagebox.showwarning("Sin destino", "Registra y selecciona un destino.", parent=self.window)
            return
        result = self._api(
            "POST",
            f"/tools/{values[0]}/move",
            {"destino_codigo": destination, "realizado_por": self.actor},
        )
        if result is not None:
            self.refresh_movable()
