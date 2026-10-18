import re
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from tool_locations_ui import ToolLocationWindow

TABS = (("4I", "4 Index (4I)"), ("2I", "2 Index (2I)"), ("45STA", "45 Estaciones"))


class ProgramWindow(ToolLocationWindow):
    """Part-number programs per machine type, with a change log."""

    def open(self):
        self.window = tk.Toplevel(self.parent)
        self.window.title("Números de parte - Programas")
        self.window.geometry("1180x760")
        self.window.minsize(980, 620)

        self.part_var = tk.StringVar()
        self.family_var = tk.StringVar()
        self.client_var = tk.StringVar()
        self.kind_var = tk.StringVar(value="SUSTAINING")
        self.search_var = tk.StringVar()
        self.log_search_var = tk.StringVar()
        self.trees = {}
        self.programs = {}

        body = ttk.Frame(self.window, padding=10)
        body.pack(fill=tk.BOTH, expand=True)
        body.rowconfigure(1, weight=1)
        body.columnconfigure(0, weight=1)

        search = ttk.Frame(body)
        search.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(search, text="Buscar número de parte").pack(side=tk.LEFT)
        entry = ttk.Entry(search, textvariable=self.search_var, width=30)
        entry.pack(side=tk.LEFT, padx=6)
        entry.bind("<Return>", lambda _e: self.refresh_programs())
        ttk.Button(search, text="Buscar", command=self.refresh_programs).pack(side=tk.LEFT)
        ttk.Button(search, text="Actualizar", command=self.refresh_programs).pack(side=tk.LEFT, padx=6)

        self.notebook = ttk.Notebook(body)
        self.notebook.grid(row=1, column=0, sticky="nsew")
        columns = [
            ("numero", "Número de parte", 190), ("cliente", "Cliente", 100), ("familia", "Familia / FG", 120),
            ("tipo", "Tipo", 90),
            ("rev", "Rev.", 50), ("espesor", "Espesor", 70), ("est", "Estaciones", 80),
            ("por", "Actualizó", 110), ("fecha", "Fecha", 140),
        ]
        for key, title in TABS:
            tab = ttk.Frame(self.notebook, padding=6)
            self.notebook.add(tab, text=title)
            frame, tree = self._tree(tab, columns, 14)
            frame.pack(fill=tk.BOTH, expand=True)
            tree.bind("<<TreeviewSelect>>", lambda _e: self.on_selected())
            self.trees[key] = tree
        self.log_tab = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(self.log_tab, text="Bitácora de cambios")
        self._build_log_tab()
        self.notebook.bind("<<NotebookTabChanged>>", lambda _e: self.on_tab_changed())

        form = ttk.LabelFrame(body, text="Cargar o actualizar programa", padding=8)
        form.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(form, text="Cliente").grid(row=0, column=0, sticky="w")
        self.client_box = ttk.Combobox(form, textvariable=self.client_var, state="readonly", width=16)
        self.client_box.grid(row=0, column=1, padx=6, sticky="w")
        self.client_box.bind("<<ComboboxSelected>>", lambda _e: self.on_client_changed())
        ttk.Button(form, text="+ Cliente", command=self.new_client).grid(row=0, column=2, sticky="w")
        ttk.Label(form, text="Familia / FG").grid(row=0, column=3, sticky="w", padx=(12, 0))
        self.family_box = ttk.Combobox(form, textvariable=self.family_var, state="readonly", width=18)
        self.family_box.grid(row=0, column=4, padx=6)
        ttk.Button(form, text="+ Familia", command=self.new_family).grid(row=0, column=5)
        ttk.Label(form, text="Tipo").grid(row=0, column=6, sticky="w", padx=(12, 0))
        ttk.Combobox(
            form, textvariable=self.kind_var, values=["NPI", "SUSTAINING"], state="readonly", width=12
        ).grid(row=0, column=7, padx=6)
        ttk.Label(form, text="Número de parte").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(form, textvariable=self.part_var, width=28).grid(row=1, column=1, columnspan=2, padx=6, pady=(8, 0), sticky="w")
        ttk.Button(form, text="Cargar .stp...", style="Accent.TButton", command=self.load_program).grid(
            row=1, column=3, sticky="w", pady=(8, 0), padx=(12, 0)
        )
        ttk.Button(form, text="Guardar copia del .stp...", command=self.export_program).grid(
            row=1, column=4, columnspan=2, sticky="w", pady=(8, 0)
        )
        ttk.Label(
            form,
            text="Formato: TYEH-1193217_01-SW. Si el número ya existe se reemplaza y pide el motivo.",
            foreground="#666",
        ).grid(row=2, column=0, columnspan=8, sticky="w", pady=(6, 0))

        self.refresh_clients()
        self.refresh_programs()
        return self.window

    def _build_log_tab(self):
        top = ttk.Frame(self.log_tab)
        top.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(top, text="Número de parte").pack(side=tk.LEFT)
        entry = ttk.Entry(top, textvariable=self.log_search_var, width=26)
        entry.pack(side=tk.LEFT, padx=6)
        entry.bind("<Return>", lambda _e: self.refresh_log())
        ttk.Button(top, text="Buscar", command=self.refresh_log).pack(side=tk.LEFT)
        frame, self.log_tree = self._tree(
            self.log_tab,
            [
                ("fecha", "Fecha", 130), ("numero", "Número de parte", 170), ("tipo", "Máquina", 70),
                ("accion", "Acción", 85), ("rev", "Rev.", 45), ("usuario", "Usuario", 90),
                ("motivo", "Motivo", 250), ("detalle", "Detalle", 300),
            ],
            14,
        )
        frame.pack(fill=tk.BOTH, expand=True)

    def _current_type(self):
        index = self.notebook.index(self.notebook.select())
        return TABS[index][0] if index < len(TABS) else None

    def on_tab_changed(self):
        if self._current_type() is None:
            self.refresh_log()

    def refresh_clients(self):
        clients = self._api("GET", "/programs/clients")
        if clients is None:
            return
        self.client_box["values"] = clients
        self.on_client_changed(keep_family=True)

    def on_client_changed(self, keep_family=False):
        client = self.client_var.get()
        families = self._api("GET", "/programs/families", params={"cliente": client}) if client else []
        if families is None:
            return
        self.family_box["values"] = [row["nombre"] for row in families]
        if not keep_family or self.family_var.get() not in self.family_box["values"]:
            self.family_var.set("")

    def new_client(self):
        name = simpledialog.askstring("Nuevo cliente", "Nombre del cliente (ej. CIENA):", parent=self.window)
        if not name or not name.strip():
            return
        created = self._api("POST", "/programs/clients", {"nombre": name})
        if created is None:
            return
        self.refresh_clients()
        self.client_var.set(created["nombre"])
        self.on_client_changed()

    def new_family(self):
        client = self.client_var.get()
        if not client:
            messagebox.showinfo("Programas", "Elige primero el cliente de la familia.", parent=self.window)
            return
        name = simpledialog.askstring("Nueva familia", f"Familia / FG de {client}:", parent=self.window)
        if not name or not name.strip():
            return
        created = self._api("POST", "/programs/families", {"nombre": name, "cliente": client})
        if created is None:
            return
        self.on_client_changed()
        self.family_var.set(created["nombre"])

    def refresh_programs(self):
        search = self.search_var.get().strip()
        for key, tree in self.trees.items():
            rows = self._api("GET", "/programs", params={"tipo_maquina": key, "buscar": search})
            if rows is None:
                return
            tree.delete(*tree.get_children())
            for row in rows:
                tree.insert(
                    "", tk.END, iid=row["numero_parte"],
                    values=(
                        row["numero_parte"], row["cliente"] or "—", row["familia"] or "—",
                        row["tipo_programa"], row["revision"],
                        row["thickness"], len(row["requisitos"]), row["actualizado_por"],
                        row["actualizado_en"][:16].replace("T", " "),
                    ),
                )
                self.programs[(key, row["numero_parte"])] = row
        if self._current_type() is None:
            self.refresh_log()

    def refresh_log(self):
        rows = self._api("GET", "/programs/log", params={"numero_parte": self.log_search_var.get().strip()})
        if rows is None:
            return
        self.log_tree.delete(*self.log_tree.get_children())
        for row in rows:
            self.log_tree.insert(
                "", tk.END,
                values=(
                    row["fecha"][:16].replace("T", " "), row["numero_parte"], row["tipo_maquina"],
                    row["accion"], row["revision"], row["usuario"], row["motivo"],
                    (row["detalle"] or "").replace("\n", " | "),
                ),
            )

    def on_selected(self):
        key = self._current_type()
        if not key:
            return
        selection = self.trees[key].selection()
        if not selection:
            return
        row = self.programs.get((key, selection[0]))
        if row:
            self.part_var.set(row["numero_parte"])
            self.client_var.set(row["cliente"] or "")
            self.on_client_changed()
            self.family_var.set(row["familia"] or "")
            self.kind_var.set(row["tipo_programa"])

    def load_program(self):
        key = self._current_type()
        if not key:
            messagebox.showinfo("Programas", "Elige primero la pestaña 4I, 2I o 45 Estaciones.", parent=self.window)
            return
        part = self.part_var.get().strip().upper()
        path = filedialog.askopenfilename(
            parent=self.window, title="Archivo .stp del programa",
            filetypes=(("Archivos de setup", "*.stp"), ("Todos los archivos", "*.*")),
        )
        if not path:
            return
        if not part:
            match = re.match(r"^([A-Z]{2,6}-\d{5,9}(?:-\d{2})?_\d{2}-[A-Z]{1,4})", path.rsplit("\\", 1)[-1].upper())
            part = match.group(1) if match else ""
            self.part_var.set(part)
        if not part:
            messagebox.showwarning(
                "Programas", "No pude sacar el número de parte del nombre del archivo. Escríbelo en el campo.",
                parent=self.window,
            )
            return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as source:
                content = source.read()
        except OSError as error:
            messagebox.showerror("No se pudo leer el archivo", str(error), parent=self.window)
            return
        reason = None
        if (key, part) in self.programs:
            reason = simpledialog.askstring(
                "Motivo del cambio",
                f"{part} ya existe en {key}. Se va a reemplazar.\n\n"
                "Motivo (cambio de secuencia, de herramienta, de notas...):",
                parent=self.window,
            )
            if not reason or not reason.strip():
                messagebox.showwarning("Programas", "El motivo es obligatorio. No se guardó nada.", parent=self.window)
                return
        result = self._api("PUT", f"/programs/{key}/{part}", {
            "familia": self.family_var.get() or None,
            "tipo_programa": self.kind_var.get(),
            "nombre_archivo": path.rsplit("\\", 1)[-1],
            "contenido": content,
            "usuario": self.actor,
            "motivo": reason,
        })
        if result is None:
            return
        self.refresh_programs()
        messagebox.showinfo(
            "Programas", f"{part} ({key}) guardado. Revisión {result['revision']}.", parent=self.window
        )

    def export_program(self):
        key = self._current_type()
        part = self.part_var.get().strip().upper()
        if not key or not part or (key, part) not in self.programs:
            messagebox.showinfo("Programas", "Selecciona un número de parte de la lista.", parent=self.window)
            return
        program = self._api("GET", f"/programs/{key}/{part}")
        if program is None:
            return
        target = filedialog.asksaveasfilename(
            parent=self.window, defaultextension=".stp", initialfile=f"{part}.stp",
            filetypes=(("Archivos de setup", "*.stp"),),
        )
        if not target:
            return
        with open(target, "w", encoding="utf-8", newline="") as output:
            output.write(program["contenido"])
        messagebox.showinfo("Programas", f"Archivo guardado en:\n{target}", parent=self.window)
