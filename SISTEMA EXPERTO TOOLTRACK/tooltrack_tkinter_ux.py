import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
import requests
from export_csv import export_inventory_to_csv
from import_csv import import_inventory_from_csv
from audit_log import log_audit
from tool_types import ACCESSORY_TYPES, HOLDER_TYPES, PUNCH_DIE_TYPES
import os
import csv
import threading
import time
import uuid
import random
import re
from workflow_ui import SetupWorkflowWindow, fetch_rack_codes
from tool_locations_ui import ToolLocationWindow
from machines_ui import MachineWindow
from programs_ui import ProgramWindow
from ui_theme import BG, CARD, NAVY, NAVY_LIGHT, ACCENT, ACCENT_DARK, TEXT, MUTED, BORDER, apply_theme, center_window

API_URL = "http://127.0.0.1:8000/herramientas/"


class HerramientaApp:
    def __init__(self, root):
        self.root = root
        self.user = None
        self.auto_backup_csv(interval=3600)  # Backup cada hora
        self.show_login()

    def show_login(self):
        self.root.withdraw()
        login_win = tk.Toplevel(self.root)
        login_win.title("TOOLTRACK - Inicio de sesión")
        login_win.configure(bg=BG)
        login_win.resizable(False, False)
        login_win.protocol("WM_DELETE_WINDOW", self.root.destroy)
        center_window(login_win, 440, 500)

        header = tk.Frame(login_win, bg=NAVY)
        header.pack(fill=tk.X)
        tk.Label(header, text="TOOLTRACK", font=("Segoe UI", 26, "bold"), bg=NAVY, fg="white").pack(pady=(26, 0))
        tk.Label(
            header, text="CONTROL DE HERRAMENTAL", font=("Segoe UI", 9, "bold"), bg=NAVY, fg=ACCENT
        ).pack(pady=(0, 20))
        tk.Frame(login_win, bg=ACCENT, height=4).pack(fill=tk.X)

        body = tk.Frame(login_win, bg=BG)
        body.pack(fill=tk.BOTH, expand=True, padx=40, pady=22)
        tk.Label(body, text="Usuario", font=("Segoe UI", 10, "bold"), bg=BG, fg=MUTED).pack(anchor="w")
        user_var = tk.StringVar()
        user_entry = tk.Entry(body, textvariable=user_var, font=("Segoe UI", 13), relief="solid", bd=1)
        user_entry.pack(fill=tk.X, ipady=6, pady=(2, 14))
        tk.Label(body, text="Contraseña", font=("Segoe UI", 10, "bold"), bg=BG, fg=MUTED).pack(anchor="w")
        pass_var = tk.StringVar()
        pass_entry = tk.Entry(body, textvariable=pass_var, font=("Segoe UI", 13), show="•", relief="solid", bd=1)
        pass_entry.pack(fill=tk.X, ipady=6, pady=(2, 8))
        msg_var = tk.StringVar()
        tk.Label(body, textvariable=msg_var, fg="#c62828", bg=BG, font=("Segoe UI", 10, "bold")).pack(pady=4)

        def do_login(_event=None):
            username = user_var.get().strip()
            password = pass_var.get().strip()
            if not username or not password:
                msg_var.set("Ingresa usuario y contraseña.")
                (user_entry if not username else pass_entry).focus_set()
                return
            try:
                r = requests.post(
                    "http://127.0.0.1:8000/login",
                    json={"username": username, "password": password},
                    timeout=10,
                )
                if r.status_code == 200:
                    data = r.json()
                    self.user = {"username": data["username"], "is_admin": data["is_admin"]}
                    login_win.destroy()
                    self.show_main_menu()
                else:
                    msg_var.set("Credenciales incorrectas.")
                    pass_var.set("")
                    pass_entry.focus_set()
            except Exception as e:
                msg_var.set(f"No se pudo conectar con el servidor: {e}")

        login_button = tk.Button(
            body, text="INICIAR SESIÓN", command=do_login, font=("Segoe UI", 12, "bold"),
            bg=ACCENT, fg="white", activebackground=ACCENT_DARK, activeforeground="white",
            relief="flat", cursor="hand2", pady=8,
        )
        login_button.pack(fill=tk.X, pady=(8, 0))
        user_entry.bind("<Return>", lambda _e: pass_entry.focus_set())
        pass_entry.bind("<Return>", do_login)
        login_win.bind("<Return>", do_login)
        login_win.after(150, lambda: (login_win.lift(), login_win.focus_force(), user_entry.focus_set()))

    def show_main_menu(self):
        root = self.root
        for child in root.winfo_children():
            child.destroy()
        root.title("TOOLTRACK")
        root.configure(bg=BG)
        root.deiconify()
        center_window(root, 1000, 680)
        root.minsize(900, 620)

        header = tk.Frame(root, bg=NAVY)
        header.pack(fill=tk.X)
        tk.Label(header, text="TOOLTRACK", font=("Segoe UI", 24, "bold"), bg=NAVY, fg="white").pack(
            side=tk.LEFT, padx=28, pady=18
        )
        role = "Administrador" if self.user.get("is_admin", False) else "Operador"
        tk.Label(
            header, text=f"{self.user['username']}  ·  {role}", font=("Segoe UI", 11),
            bg=NAVY, fg="#c9d3df",
        ).pack(side=tk.RIGHT, padx=28)
        tk.Frame(root, bg=ACCENT, height=4).pack(fill=tk.X)

        tk.Label(
            root, text="¿Qué quieres hacer?", font=("Segoe UI", 18, "bold"), bg=BG, fg=TEXT
        ).pack(pady=(34, 4))
        tk.Label(
            root, text="Selecciona un módulo para continuar", font=("Segoe UI", 10), bg=BG, fg=MUTED
        ).pack(pady=(0, 22))

        grid = tk.Frame(root, bg=BG)
        grid.pack()
        cards = [
            ("📦", "Inventory", "Registro y consulta de herramientas", self.gestion_catalogo),
            ("🛒", "Tool Dispensing", "Comparar setups y surtir al carro", self.tool_dispensing_flow),
                        ("📍", "Tool Tracking", "Localización, retornos y pendientes", self.tool_location_flow),
                        ("🏭", "Máquinas", "Fichas, estaciones y capacidad", self.machines_flow),
                        ("🧾", "Números de parte", "Programas 4I, 2I y 45 STA", self.programs_flow),
                        ("📋", "View Audit Log", "Historial de acciones del sistema", self.ver_auditoria),
        ]
        if self.user.get("is_admin", False):
            cards.append(("👤", "Administrar Usuarios", "Altas y bajas de usuarios", self.admin_usuarios))
        for index, (icon, title, subtitle, command) in enumerate(cards):
            self._menu_card(grid, icon, title, subtitle, command).grid(
                row=index // 2, column=index % 2, padx=14, pady=14
            )

    def _menu_card(self, parent, icon, title, subtitle, command):
        card = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1, cursor="hand2")
        card.configure(width=380, height=130)
        card.pack_propagate(False)
        stripe = tk.Frame(card, bg=ACCENT, width=6)
        stripe.pack(side=tk.LEFT, fill=tk.Y)
        icon_label = tk.Label(card, text=icon, font=("Segoe UI Emoji", 30), bg=CARD)
        icon_label.pack(side=tk.LEFT, padx=18)
        text = tk.Frame(card, bg=CARD)
        text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, pady=26)
        title_label = tk.Label(text, text=title, font=("Segoe UI", 14, "bold"), bg=CARD, fg=NAVY, anchor="w")
        title_label.pack(fill=tk.X)
        sub_label = tk.Label(text, text=subtitle, font=("Segoe UI", 9), bg=CARD, fg=MUTED, anchor="w", wraplength=220, justify="left")
        sub_label.pack(fill=tk.X, pady=(4, 0))
        widgets = (card, icon_label, text, title_label, sub_label)

        def set_bg(color):
            for widget in widgets:
                widget.configure(bg=color)

        for widget in widgets:
            widget.bind("<Button-1>", lambda _e: command())
            widget.bind("<Enter>", lambda _e: set_bg("#fff4e0"))
            widget.bind("<Leave>", lambda _e: set_bg(CARD))
        return card
    def admin_usuarios(self):
        win = tk.Toplevel(self.root)
        win.title("Administrar Usuarios")
        win.configure(bg="#f4f6fa")
        tree = ttk.Treeview(win, columns=("Username", "Rol"), show="headings", height=10)
        tree.heading("Username", text="Username")
        tree.heading("Rol", text="Rol")
        tree.pack(pady=10)
        def cargar_usuarios():
            for row in tree.get_children():
                tree.delete(row)
            try:
                import requests
                r = requests.get("http://127.0.0.1:8000/users")
                if r.status_code == 200:
                    for u in r.json():
                        rol = "Admin" if u["is_admin"] else "Operador"
                        tree.insert('', tk.END, values=(u["username"], rol))
            except Exception as e:
                messagebox.showerror("Error", f"No se pudo cargar usuarios: {e}")
        cargar_usuarios()
        def crear_usuario():
            def guardar():
                username = user_var.get().strip()
                password = pass_var.get().strip()
                is_admin = rol_var.get() == "Admin"
                if not username or not password:
                    messagebox.showerror("Error", "Usuario y contraseÃ±a requeridos")
                    return
                try:
                    import requests
                    r = requests.post("http://127.0.0.1:8000/users", json={"username": username, "password": password, "is_admin": is_admin})
                    if r.status_code == 200:
                        messagebox.showinfo("OK", "Usuario creado")
                        top.destroy()
                        cargar_usuarios()
                    else:
                        messagebox.showerror("Error", r.json().get("detail", "No se pudo crear usuario"))
                except Exception as e:
                    messagebox.showerror("Error", f"No se pudo crear usuario: {e}")
            top = tk.Toplevel(win)
            top.title("Crear Usuario")
            tk.Label(top, text="Usuario:").pack()
            user_var = tk.StringVar()
            tk.Entry(top, textvariable=user_var).pack()
            tk.Label(top, text="ContraseÃ±a:").pack()
            pass_var = tk.StringVar()
            tk.Entry(top, textvariable=pass_var, show="*").pack()
            tk.Label(top, text="Rol:").pack()
            rol_var = tk.StringVar(value="Operador")
            ttk.Combobox(top, textvariable=rol_var, values=["Operador", "Admin"], state="readonly").pack()
            tk.Button(top, text="Guardar", command=guardar).pack(pady=10)
        def eliminar_usuario():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning("Selecciona", "Selecciona un usuario para eliminar")
                return
            username = tree.item(sel[0])["values"][0]
            if username == self.user["username"]:
                messagebox.showwarning("No permitido", "No puedes eliminarte a ti mismo")
                return
            if messagebox.askyesno("Confirmar", f"Â¿Eliminar usuario {username}?"):
                try:
                    import requests
                    r = requests.delete(f"http://127.0.0.1:8000/users/{username}")
                    if r.status_code == 200:
                        cargar_usuarios()
                    else:
                        messagebox.showerror("Error", r.json().get("detail", "No se pudo eliminar usuario"))
                except Exception as e:
                    messagebox.showerror("Error", f"No se pudo eliminar usuario: {e}")
        def editar_usuario():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning("Selecciona", "Selecciona un usuario para editar")
                return
            username = tree.item(sel[0])["values"][0]
            def guardar_edicion():
                password = pass_var.get().strip()
                is_admin = rol_var.get() == "Admin"
                if not password:
                    messagebox.showerror("Error", "ContraseÃ±a requerida")
                    return
                try:
                    import requests
                    r = requests.patch(f"http://127.0.0.1:8000/users/{username}", json={"username": username, "password": password, "is_admin": is_admin})
                    if r.status_code == 200:
                        messagebox.showinfo("OK", "Usuario actualizado")
                        top.destroy()
                        cargar_usuarios()
                    else:
                        messagebox.showerror("Error", r.json().get("detail", "No se pudo actualizar usuario"))
                except Exception as e:
                    messagebox.showerror("Error", f"No se pudo actualizar usuario: {e}")
            top = tk.Toplevel(win)
            top.title(f"Editar Usuario: {username}")
            tk.Label(top, text=f"Usuario: {username}").pack()
            tk.Label(top, text="Nueva ContraseÃ±a:").pack()
            pass_var = tk.StringVar()
            tk.Entry(top, textvariable=pass_var, show="*").pack()
            tk.Label(top, text="Rol:").pack()
            rol_var = tk.StringVar(value="Operador")
            # Detectar rol actual
            for row in tree.get_children():
                if tree.item(row)["values"][0] == username:
                    rol_actual = tree.item(row)["values"][1]
                    rol_var.set(rol_actual)
            ttk.Combobox(top, textvariable=rol_var, values=["Operador", "Admin"], state="readonly").pack()
            tk.Button(top, text="Guardar", command=guardar_edicion).pack(pady=10)
        btns = tk.Frame(win, bg="#f4f6fa")
        btns.pack(pady=10)
        tk.Button(btns, text="Crear Usuario", command=crear_usuario).pack(side=tk.LEFT, padx=5)
        tk.Button(btns, text="Editar Usuario", command=editar_usuario).pack(side=tk.LEFT, padx=5)
        tk.Button(btns, text="Eliminar Usuario", command=eliminar_usuario).pack(side=tk.LEFT, padx=5)

    def abrir_inventario(self):
        win = tk.Toplevel(self.root)
        win.title("Inventory Validation")
        win.configure(bg="#f4f6fa")
        search_frame = tk.Frame(win, bg="#f4f6fa")
        search_frame.pack(pady=5)
        tk.Label(search_frame, text="Search:", bg="#f4f6fa").pack(side=tk.LEFT)
        search_var = tk.StringVar()
        search_entry = tk.Entry(search_frame, textvariable=search_var, font=("Segoe UI", 11))
        search_entry.pack(side=tk.LEFT, padx=5)
        filter_var = tk.StringVar(value="all")
        tk.Label(search_frame, text="  Filter by type:", bg="#f4f6fa").pack(side=tk.LEFT)
        filter_options = [
            "all", "punch", "die", "holder", "accessory"
        ]
        ttk.Combobox(search_frame, textvariable=filter_var, values=filter_options, state="readonly", width=12).pack(side=tk.LEFT, padx=5)
        result_frame = tk.Frame(win, bg="#f4f6fa")
        result_frame.pack(pady=10)
        tree = ttk.Treeview(result_frame, columns=("ID", "TN", "SIZE", "TYPE", "LIFE", "CLEARANCE", "SO", "CATEGORY", "LOCATION"), show="headings", height=12)
        for col in tree["columns"]:
            tree.heading(col, text=col)
            tree.column(col, width=110, anchor=tk.CENTER)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar = ttk.Scrollbar(result_frame, orient="vertical", command=tree.yview)
        tree.configure(yscroll=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        btn_frame = tk.Frame(win, bg="#f4f6fa")
        btn_frame.pack(pady=5)
        # PaginaciÃ³n
        page_var = tk.IntVar(value=1)
        page_size = 15
        total_pages = [1]
        def update_pagination(total):
            total_pages[0] = max(1, (total + page_size - 1) // page_size)
            page_label.config(text=f"PÃ¡gina {page_var.get()} de {total_pages[0]}")
            prev_btn.config(state=tk.NORMAL if page_var.get() > 1 else tk.DISABLED)
            next_btn.config(state=tk.NORMAL if page_var.get() < total_pages[0] else tk.DISABLED)
        def buscar():
            for row in tree.get_children():
                tree.delete(row)
            tipo_filtro = filter_var.get()
            search_text = search_var.get().strip().lower()
            try:
                r = requests.get(API_URL)
                if r.status_code != 200:
                    raise Exception(f"Backend error: {r.status_code}")
                herramientas = r.json()
                for h in herramientas:
                    cat = h.get('categoria','')
                    cat_map = {'punzon':'punch','matriz':'die','holder':'holder','accesorio':'accessory'}
                    cat_eng = cat_map.get(cat,cat)
                    # Filtro por tipo/categorÃ­a
                    if tipo_filtro != "all" and cat_eng != tipo_filtro:
                        continue
                    # Filtro parcial por cualquier campo visible
                    values = [str(h.get(col.lower(), '')).lower() for col in tree["columns"]]
                    if search_text and not any(search_text in v for v in values):
                        continue
                    tree.insert('', tk.END, values=(
                        h.get('id',''),
                        h.get('tn',''),
                        h.get('size',''),
                        h.get('type',''),
                        h.get('life',''),
                        h.get('clearance',''),
                        h.get('so',''),
                        cat_eng.capitalize(),
                        h.get('ubicacion','')
                    ))
            except Exception as e:
                messagebox.showerror("Error", f"Could not get inventory: {e}")
        def prev_page():
            if page_var.get() > 1:
                page_var.set(page_var.get()-1)
                buscar()
        def next_page():
            if page_var.get() < total_pages[0]:
                page_var.set(page_var.get()+1)
                buscar()
        page_label = tk.Label(btn_frame, text="PÃ¡gina 1 de 1", bg="#f4f6fa", font=("Segoe UI", 10))
        page_label.pack(side=tk.LEFT, padx=10)
        prev_btn = ttk.Button(btn_frame, text="Anterior", command=prev_page, style='TButton')
        prev_btn.pack(side=tk.LEFT, padx=5)
        next_btn = ttk.Button(btn_frame, text="Siguiente", command=next_page, style='TButton')
        next_btn.pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Delete", command=self.eliminar, style='TButton').pack(side=tk.LEFT, padx=10)
        ttk.Button(btn_frame, text="Edit", command=self.editar, style='TButton').pack(side=tk.LEFT, padx=10)
        def exportar_csv():
            herramientas_actual = []
            try:
                r = requests.get(API_URL)
                if r.status_code == 200:
                    herramientas_actual = r.json()
            except Exception:
                pass
            export_inventory_to_csv(herramientas_actual)
        ttk.Button(btn_frame, text="Export CSV", command=exportar_csv, style='TButton').pack(side=tk.LEFT, padx=10)
        ttk.Button(btn_frame, text="Import CSV", command=import_inventory_from_csv, style='TButton').pack(side=tk.LEFT, padx=10)
        ttk.Button(win, text="Search", command=buscar, style='TButton').pack(pady=5)
        search_entry.bind('<KeyRelease>', lambda event: buscar())
        buscar()  # Ejecutar siempre al abrir la ventana
        win.focus()

    def gestion_catalogo_legacy(self):
        win = tk.Toplevel(self.root)
        win.title("TOOLTRACK Inventory")
        win.geometry("1320x650")
        win.configure(bg="#f4f6fa")

        notebook = ttk.Notebook(win)
        catalog_tab = tk.Frame(notebook, bg="#f4f6fa")
        tools_tab = tk.Frame(notebook, bg="#f4f6fa")
        notebook.add(tools_tab, text="Inventory")
        notebook.add(catalog_tab, text="Tool Number Catalog")
        notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        catalog_tree = ttk.Treeview(
            catalog_tab,
            columns=("TN", "SIZE", "TYPE", "VARIANTS"),
            show="headings",
            height=16,
        )
        for column, width in (("TN", 180), ("SIZE", 100), ("TYPE", 180), ("VARIANTS", 450)):
            catalog_tree.heading(column, text=column)
            catalog_tree.column(column, width=width, anchor=tk.CENTER)
        catalog_tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        inventory_summary = tk.StringVar()
        tk.Label(
            tools_tab,
            textvariable=inventory_summary,
            bg="#f4f6fa",
            anchor=tk.W,
        ).pack(fill=tk.X, padx=8, pady=(6, 0))
        tk.Label(
            tools_tab,
            text="All registered physical tools are shown. Catalog Link identifies records not yet associated with the normalized TN catalog.",
            bg="#f4f6fa",
            fg="#555",
            anchor=tk.W,
        ).pack(fill=tk.X, padx=8, pady=(2, 4))
        inventory_frame = tk.Frame(tools_tab, bg="#f4f6fa")
        inventory_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        tool_tree = ttk.Treeview(
            inventory_frame,
            columns=("ID", "TN", "SIZE", "TYPE", "CATEGORY", "CLEARANCE", "LIFE", "SO", "LOCATION", "CATALOG LINK"),
            show="headings",
            height=16,
        )
        for column, width in (
            ("ID", 150), ("TN", 120), ("SIZE", 65), ("TYPE", 140),
            ("CATEGORY", 110), ("CLEARANCE", 95), ("LIFE", 100),
            ("SO", 120), ("LOCATION", 160), ("CATALOG LINK", 150),
        ):
            tool_tree.heading(column, text=column)
            tool_tree.column(
                column,
                width=width,
                anchor=tk.W if column in ("ID", "TYPE", "SO", "LOCATION", "CATALOG LINK") else tk.CENTER,
            )
        inventory_y_scroll = ttk.Scrollbar(inventory_frame, orient="vertical", command=tool_tree.yview)
        inventory_x_scroll = ttk.Scrollbar(inventory_frame, orient="horizontal", command=tool_tree.xview)
        tool_tree.configure(
            yscrollcommand=inventory_y_scroll.set,
            xscrollcommand=inventory_x_scroll.set,
        )
        tool_tree.grid(row=0, column=0, sticky="nsew")
        inventory_y_scroll.grid(row=0, column=1, sticky="ns")
        inventory_x_scroll.grid(row=1, column=0, sticky="ew")
        inventory_frame.grid_rowconfigure(0, weight=1)
        inventory_frame.grid_columnconfigure(0, weight=1)

        is_admin = self.user.get("is_admin", False)
        configs = []
        variants = []

        def request_error(response):
            try:
                detail = response.json().get("detail")
            except (ValueError, AttributeError):
                detail = None
            return detail or f"API error {response.status_code}: {response.text}"

        def refresh_catalog():
            nonlocal configs, variants
            try:
                config_response = requests.get("http://127.0.0.1:8000/catalogo/configuraciones", timeout=10)
                if config_response.status_code != 200:
                    raise RuntimeError(request_error(config_response))
                tn_response = requests.get("http://127.0.0.1:8000/catalogo/tn", timeout=10)
                if tn_response.status_code != 200:
                    raise RuntimeError(request_error(tn_response))
                variant_response = requests.get("http://127.0.0.1:8000/catalogo/variantes", timeout=10)
                if variant_response.status_code != 200:
                    raise RuntimeError(request_error(variant_response))
                configs = config_response.json()
                variants = variant_response.json()
                tool_numbers = [item["tn"] for item in tn_response.json()]
                for row in catalog_tree.get_children():
                    catalog_tree.delete(row)
                for tn in tool_numbers:
                    tn_configs = [config for config in configs if config["tn"] == tn]
                    if not tn_configs:
                        catalog_tree.insert(
                            "",
                            tk.END,
                            values=(tn, "-", "-", "No configurations"),
                        )
                        continue
                    for config in tn_configs:
                        variant_text = ", ".join(
                            "Punch" if variant["categoria"] == "punzon"
                            else f"Die clearance {variant['clearance']}"
                            for variant in config["variantes"]
                        )
                        catalog_tree.insert(
                            "",
                            tk.END,
                            values=(config["tn"], config["size"], config["type"], variant_text),
                        )
            except (requests.RequestException, RuntimeError, ValueError) as error:
                messagebox.showerror("Catalog Error", f"Could not load catalog: {error}", parent=win)

        def refresh_tools():
            try:
                inventory_response = requests.get("http://127.0.0.1:8000/herramientas/", timeout=10)
                if inventory_response.status_code != 200:
                    raise RuntimeError(request_error(inventory_response))
                catalog_response = requests.get("http://127.0.0.1:8000/catalogo/herramientas", timeout=10)
                if catalog_response.status_code != 200:
                    raise RuntimeError(request_error(catalog_response))
                cataloged_by_id = {
                    str(tool.get("id")): tool
                    for tool in catalog_response.json()
                }
                for row in tool_tree.get_children():
                    tool_tree.delete(row)
                inventory = inventory_response.json()
                linked_count = 0
                for tool in inventory:
                    catalog_record = cataloged_by_id.get(str(tool.get("id")))
                    catalog_link = "Linked" if catalog_record else "Not linked"
                    if catalog_record:
                        linked_count += 1
                    tool_tree.insert(
                        "",
                        tk.END,
                        values=(
                            tool.get("id", ""),
                            tool.get("tn", ""),
                            tool.get("size", ""),
                            tool.get("type", ""),
                            tool.get("categoria", ""),
                            tool.get("clearance", "") or "",
                            tool.get("life", "") or "",
                            tool.get("so", "") or "",
                            tool.get("ubicacion", "") or "",
                            catalog_link,
                        ),
                    )
                inventory_summary.set(
                    f"Physical tools: {len(inventory)} | Linked to catalog: {linked_count} | Not linked: {len(inventory) - linked_count}"
                )
            except (requests.RequestException, RuntimeError, ValueError) as error:
                messagebox.showerror("Inventory Error", f"Could not load inventory: {error}", parent=win)

        def submit_catalog_request(response, action, record_id, details, form):
            if response.status_code not in (200, 201):
                messagebox.showerror("Catalog Error", request_error(response), parent=form)
                return False
            log_audit(action, self.user["username"], record_id, details)
            form.destroy()
            refresh_catalog()
            refresh_tools()
            return True

        def add_tn():
            tn = simpledialog.askstring("New Tool Number", "Tool Number:", parent=win)
            if tn is None:
                return
            tn = tn.strip()
            if not tn:
                messagebox.showerror("Validation", "Tool Number is required.", parent=win)
                return
            try:
                response = requests.post(
                    "http://127.0.0.1:8000/catalogo/tn",
                    json={"tn": tn},
                    timeout=10,
                )
                if response.status_code not in (200, 201):
                    messagebox.showerror("Catalog Error", request_error(response), parent=win)
                    return
                log_audit("catalog_tn_create", self.user["username"], tn, response.json())
                refresh_catalog()
            except requests.RequestException as error:
                messagebox.showerror("Catalog Error", f"Could not create Tool Number: {error}", parent=win)

        def add_configuration():
            tn_values = load_tn_options()
            if not tn_values:
                return
            form = tk.Toplevel(win)
            form.title("Add TN Configuration")
            form.configure(bg="#f4f6fa")
            tn_var = tk.StringVar(value=tn_values[0] if tn_values else "")
            size_var = tk.StringVar()
            type_var = tk.StringVar()
            fields = (
                ("Tool Number", ttk.Combobox(form, textvariable=tn_var, values=tn_values, state="readonly")),
                ("SIZE", ttk.Combobox(form, textvariable=size_var, values=["A", "B", "C", "D", "E"], state="readonly")),
                ("TYPE", ttk.Combobox(
                    form,
                    textvariable=type_var,
                    values=["ROUND", "SQUARE", "RECTANGULAR", "OVAL", "HEXAGONAL", "SPECIAL", "FORMED", "1 RADIUS", "2 RADII", "4 RADII"],
                    state="readonly",
                )),
            )
            for row, (label, widget) in enumerate(fields):
                tk.Label(form, text=label, bg="#f4f6fa").grid(row=row, column=0, sticky=tk.W, padx=8, pady=6)
                widget.grid(row=row, column=1, padx=8, pady=6)

            def save():
                payload = {"tn": tn_var.get(), "size": size_var.get(), "type": type_var.get()}
                if not all(payload.values()):
                    messagebox.showerror("Validation", "Tool Number, SIZE and TYPE are required.", parent=form)
                    return
                try:
                    response = requests.post(
                        "http://127.0.0.1:8000/catalogo/configuraciones",
                        json=payload,
                        timeout=10,
                    )
                    submit_catalog_request(response, "catalog_configuration_create", payload["tn"], payload, form)
                except requests.RequestException as error:
                    messagebox.showerror("Catalog Error", f"Could not create configuration: {error}", parent=form)

            ttk.Button(form, text="Save", command=save).grid(row=len(fields), column=0, columnspan=2, pady=10)

        def load_tn_options():
            try:
                response = requests.get("http://127.0.0.1:8000/catalogo/tn", timeout=10)
                if response.status_code != 200:
                    raise RuntimeError(request_error(response))
                return [item["tn"] for item in response.json()]
            except (requests.RequestException, RuntimeError, ValueError, KeyError) as error:
                messagebox.showerror("Catalog Error", f"Could not load Tool Numbers: {error}", parent=win)
                return []

        def add_variant():
            if not configs:
                messagebox.showwarning("Catalog", "Create a TN configuration before adding variants.", parent=win)
                return
            form = tk.Toplevel(win)
            form.title("Add Tool Variant")
            form.configure(bg="#f4f6fa")
            config_options = [
                f"{config['tn']} | {config['size']} | {config['type']}"
                for config in configs
            ]
            config_lookup = dict(zip(config_options, configs))
            config_var = tk.StringVar(value=config_options[0])
            category_var = tk.StringVar(value="punzon")
            clearance_var = tk.StringVar()
            tk.Label(form, text="TN / SIZE / TYPE", bg="#f4f6fa").grid(row=0, column=0, sticky=tk.W, padx=8, pady=6)
            ttk.Combobox(form, textvariable=config_var, values=config_options, state="readonly", width=35).grid(row=0, column=1, padx=8, pady=6)
            tk.Label(form, text="Category", bg="#f4f6fa").grid(row=1, column=0, sticky=tk.W, padx=8, pady=6)
            category_widget = ttk.Combobox(form, textvariable=category_var, values=["punzon", "matriz"], state="readonly")
            category_widget.grid(row=1, column=1, padx=8, pady=6)
            tk.Label(form, text="CLEARANCE (dies only)", bg="#f4f6fa").grid(row=2, column=0, sticky=tk.W, padx=8, pady=6)
            clearance_entry = tk.Entry(form, textvariable=clearance_var)
            clearance_entry.grid(row=2, column=1, padx=8, pady=6)

            def toggle_clearance(_event=None):
                clearance_entry.config(state=tk.NORMAL if category_var.get() == "matriz" else tk.DISABLED)
                if category_var.get() == "punzon":
                    clearance_var.set("")

            category_widget.bind("<<ComboboxSelected>>", toggle_clearance)
            toggle_clearance()

            def save():
                config = config_lookup[config_var.get()]
                payload = {"categoria": category_var.get()}
                if category_var.get() == "matriz":
                    payload["clearance"] = clearance_var.get().strip()
                if category_var.get() == "matriz" and not payload["clearance"]:
                    messagebox.showerror("Validation", "CLEARANCE is required for a die.", parent=form)
                    return
                try:
                    from urllib.parse import quote
                    path = f"{quote(config['tn'], safe='')}/{quote(config['size'], safe='')}"
                    response = requests.post(
                        f"http://127.0.0.1:8000/catalogo/configuraciones/{path}/variantes",
                        json=payload,
                        timeout=10,
                    )
                    record_id = f"{config['tn']}/{config['size']}/{payload['categoria']}/{payload.get('clearance', '')}"
                    submit_catalog_request(response, "catalog_variant_create", record_id, payload, form)
                except requests.RequestException as error:
                    messagebox.showerror("Catalog Error", f"Could not create variant: {error}", parent=form)

            ttk.Button(form, text="Save", command=save).grid(row=3, column=0, columnspan=2, pady=10)

        def add_physical_tool():
            if not variants:
                messagebox.showwarning("Catalog", "Create a tool variant before registering a physical tool.", parent=win)
                return
            form = tk.Toplevel(win)
            form.title("Register Physical Tool")
            form.configure(bg="#f4f6fa")
            variant_options = [
                f"{variant['tn']} | {variant['size']} | {variant['categoria']} | "
                f"{variant['clearance'] or '-'} | {variant['type']}"
                for variant in variants
            ]
            variant_lookup = dict(zip(variant_options, variants))
            variant_var = tk.StringVar(value=variant_options[0])
            fields = {}
            tk.Label(form, text="Catalog variant", bg="#f4f6fa").grid(row=0, column=0, sticky=tk.W, padx=8, pady=6)
            ttk.Combobox(
                form,
                textvariable=variant_var,
                values=variant_options,
                state="readonly",
                width=48,
            ).grid(row=0, column=1, padx=8, pady=6)
            for row, label in enumerate(("Physical ID", "LIFE", "SO", "Location"), start=1):
                tk.Label(form, text=label, bg="#f4f6fa").grid(row=row, column=0, sticky=tk.W, padx=8, pady=6)
                if label == "Location":
                    entry = ttk.Combobox(form, values=["", *fetch_rack_codes(form)], state="readonly", width=17)
                else:
                    entry = tk.Entry(form)
                entry.grid(row=row, column=1, padx=8, pady=6)
                fields[label] = entry

            def save():
                physical_id = fields["Physical ID"].get().strip()
                life = fields["LIFE"].get().strip()
                if not physical_id or not life:
                    messagebox.showerror("Validation", "Physical ID and LIFE are required.", parent=form)
                    return
                variant = variant_lookup[variant_var.get()]
                payload = {
                    "id": physical_id,
                    "variante_id": variant["id"],
                    "life": life,
                    "so": fields["SO"].get().strip() or None,
                    "ubicacion": fields["Location"].get().strip() or None,
                }
                try:
                    response = requests.post(
                        "http://127.0.0.1:8000/catalogo/herramientas",
                        json=payload,
                        timeout=10,
                    )
                    submit_catalog_request(response, "catalog_physical_tool_create", physical_id, payload, form)
                except requests.RequestException as error:
                    messagebox.showerror("Catalog Error", f"Could not register physical tool: {error}", parent=form)

            ttk.Button(form, text="Save", command=save).grid(row=5, column=0, columnspan=2, pady=10)

        catalog_buttons = tk.Frame(catalog_tab, bg="#f4f6fa")
        catalog_buttons.pack(pady=6)
        ttk.Button(catalog_buttons, text="Refresh", command=refresh_catalog).pack(side=tk.LEFT, padx=4)
        tool_buttons = tk.Frame(tools_tab, bg="#f4f6fa")
        tool_buttons.pack(pady=6)
        ttk.Button(tool_buttons, text="Refresh", command=refresh_tools).pack(side=tk.LEFT, padx=4)
        if is_admin:
            ttk.Button(catalog_buttons, text="Add Tool Number", command=add_tn).pack(side=tk.LEFT, padx=4)
            ttk.Button(catalog_buttons, text="Add Configuration", command=add_configuration).pack(side=tk.LEFT, padx=4)
            ttk.Button(catalog_buttons, text="Add Variant", command=add_variant).pack(side=tk.LEFT, padx=4)
            ttk.Button(tool_buttons, text="Register Physical Tool", command=add_physical_tool).pack(side=tk.LEFT, padx=4)
        else:
            tk.Label(
                win,
                text="View only. Catalog and tool registration require administrator access.",
                bg="#f4f6fa",
                fg="#666",
            ).pack(pady=(0, 8))

        refresh_catalog()
        refresh_tools()
        win.focus()

    def gestion_catalogo(self):
        win = tk.Toplevel(self.root)
        win.title("TOOLTRACK - Inventory")
        win.geometry("1320x680")
        win.configure(bg="#f4f6fa")

        tk.Label(
            win,
            text="Physical Tool Inventory",
            font=("Segoe UI", 16, "bold"),
            bg="#f4f6fa",
        ).pack(anchor=tk.W, padx=14, pady=(12, 2))
        tk.Label(
            win,
            text="Register and view every tool from this screen. Catalog records are created automatically during registration.",
            bg="#f4f6fa",
            fg="#555",
        ).pack(anchor=tk.W, padx=14, pady=(0, 8))

        inventory_summary = tk.StringVar()
        tk.Label(
            win,
            textvariable=inventory_summary,
            bg="#f4f6fa",
            anchor=tk.W,
        ).pack(fill=tk.X, padx=14, pady=(2, 6))

        columns = (
            "ID", "TN", "SIZE", "TYPE", "CATEGORY", "CLEARANCE",
            "LIFE", "SO", "LOCATION", "CATALOG",
        )
        table_frame = tk.Frame(win, bg="#f4f6fa")
        table_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=5)
        tree = ttk.Treeview(table_frame, columns=columns, show="headings")
        widths = (150, 120, 65, 150, 110, 95, 100, 120, 170, 130)
        for column, width in zip(columns, widths):
            tree.heading(column, text=column)
            tree.column(
                column,
                width=width,
                anchor=tk.W if column in {"ID", "TYPE", "SO", "LOCATION", "CATALOG"} else tk.CENTER,
            )
        y_scroll = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=tree.yview)
        x_scroll = ttk.Scrollbar(table_frame, orient=tk.HORIZONTAL, command=tree.xview)
        tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        tree.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)

        def api_error(response):
            try:
                detail = response.json().get("detail")
            except (ValueError, AttributeError):
                detail = None
            return detail or f"API error {response.status_code}: {response.text}"

        def refresh_inventory():
            try:
                inventory_response = requests.get(API_URL, timeout=10)
                if inventory_response.status_code != 200:
                    raise RuntimeError(api_error(inventory_response))
                catalog_response = requests.get(
                    "http://127.0.0.1:8000/catalogo/herramientas",
                    timeout=10,
                )
                if catalog_response.status_code != 200:
                    raise RuntimeError(api_error(catalog_response))
                catalog_ids = {
                    str(tool.get("id"))
                    for tool in catalog_response.json()
                }
                inventory = inventory_response.json()
                for row in tree.get_children():
                    tree.delete(row)
                linked_count = 0
                for tool in inventory:
                    linked = str(tool.get("id")) in catalog_ids
                    linked_count += int(linked)
                    tree.insert(
                        "",
                        tk.END,
                        values=(
                            tool.get("id") or "",
                            tool.get("tn") or "",
                            tool.get("size") or "",
                            tool.get("type") or "",
                            tool.get("categoria") or "",
                            tool.get("clearance") or "",
                            tool.get("life") or "",
                            tool.get("so") or "",
                            tool.get("ubicacion") or "",
                            "Linked" if linked else "Not linked",
                        ),
                    )
                inventory_summary.set(
                    f"Tools: {len(inventory)}  |  Catalog linked: {linked_count}  |  Legacy/unlinked: {len(inventory) - linked_count}"
                )
            except (requests.RequestException, RuntimeError, ValueError) as error:
                messagebox.showerror(
                    "Inventory Error",
                    f"Could not load inventory: {error}",
                    parent=win,
                )

        def register_tool(existing=None):
            if not self.user.get("is_admin", False):
                messagebox.showwarning(
                    "Permission denied",
                    "Only administrators can register tools.",
                    parent=win,
                )
                return

            form = tk.Toplevel(win)
            form.title("Edit Physical Tool" if existing else "Register Physical Tool")
            form.configure(bg="#f4f6fa")
            form.transient(win)
            form.grab_set()

            fields = {}
            current_category = (existing.get("categoria") or "punzon").lower() if existing else "punzon"
            current_category = {"punch": "punzon", "die": "matriz", "accessory": "accesorio"}.get(
                current_category,
                current_category,
            )
            category_var = tk.StringVar(value=current_category)
            labels = (
                "Physical ID", "Category", "TN", "SIZE", "TYPE",
                "CLEARANCE", "LIFE", "SO", "Location",
            )
            label_widgets = {}
            for row, label in enumerate(labels):
                label_widget = tk.Label(form, text=label, bg="#f4f6fa")
                label_widget.grid(
                    row=row,
                    column=0,
                    sticky=tk.W,
                    padx=10,
                    pady=5,
                )
                label_widgets[label] = label_widget
                if label == "Category":
                    widget = ttk.Combobox(
                        form,
                        textvariable=category_var,
                        values=["punzon", "matriz", "holder", "accesorio"],
                        state="readonly",
                        width=30,
                    )
                elif label == "SIZE":
                    widget = ttk.Combobox(
                        form,
                        values=["A", "B", "C", "D", "E"],
                        state="readonly",
                        width=30,
                    )
                elif label == "TYPE":
                    widget = ttk.Combobox(
                        form,
                        values=PUNCH_DIE_TYPES,
                        state="readonly",
                        width=30,
                    )
                elif label == "Location":
                    widget = ttk.Combobox(
                        form,
                        values=["", *fetch_rack_codes(form)],
                        state="readonly",
                        width=30,
                    )
                else:
                    widget = tk.Entry(form, width=33)
                widget.grid(row=row, column=1, sticky=tk.W, padx=10, pady=5)
                fields[label] = widget

            if existing:
                fields["Physical ID"].insert(0, existing.get("id") or "")
                fields["Physical ID"].configure(state=tk.DISABLED)
                fields["TN"].insert(0, existing.get("tn") or "")
                fields["SIZE"].set(existing.get("size") or "")
                fields["CLEARANCE"].insert(0, existing.get("clearance") or "")
                fields["LIFE"].insert(0, existing.get("life") or "")
                fields["SO"].insert(0, existing.get("so") or "")
                fields["Location"].set(existing.get("ubicacion") or "")

            def type_values_for_category(category):
                if category in {"punzon", "matriz"}:
                    allowed = PUNCH_DIE_TYPES
                elif category == "holder":
                    allowed = HOLDER_TYPES
                else:
                    allowed = ACCESSORY_TYPES
                current_type = fields["TYPE"].get().strip().upper()
                all_standard_types = set(PUNCH_DIE_TYPES + HOLDER_TYPES + ACCESSORY_TYPES)
                if (
                    category == current_category
                    and current_type
                    and current_type not in allowed
                    and current_type not in all_standard_types
                ):
                    return [*allowed, current_type]
                return allowed

            if existing:
                current_type = (existing.get("type") or "").upper()
                category_types = {
                    "punzon": PUNCH_DIE_TYPES,
                    "matriz": PUNCH_DIE_TYPES,
                    "holder": HOLDER_TYPES,
                    "accesorio": ACCESSORY_TYPES,
                }
                valid_types = category_types.get(current_category, [])
                all_standard_types = set(PUNCH_DIE_TYPES + HOLDER_TYPES + ACCESSORY_TYPES)
                fields["TYPE"].set(
                    current_type
                    if current_type in valid_types or current_type not in all_standard_types
                    else ""
                )
                values = type_values_for_category(current_category)
                fields["TYPE"]["values"] = values

            def toggle_tn_fields(_event=None):
                is_tn_tool = category_var.get() in {"punzon", "matriz"}
                for label in ("TN", "SIZE", "CLEARANCE", "LIFE"):
                    visible = is_tn_tool
                    widget = fields[label]
                    if visible:
                        widget.grid()
                        label_widgets[label].grid()
                    else:
                        widget.grid_remove()
                        label_widgets[label].grid_remove()
                fields["CLEARANCE"].configure(state=tk.NORMAL if category_var.get() == "matriz" else tk.DISABLED)
                if category_var.get() != "matriz":
                    fields["CLEARANCE"].delete(0, tk.END)
                if not is_tn_tool:
                    for label in ("TN", "SIZE", "LIFE"):
                        fields[label].delete(0, tk.END)
                fields["TYPE"]["values"] = type_values_for_category(category_var.get())

            previous_category = [current_category]

            def category_changed(*_args):
                toggle_tn_fields()
                new_category = category_var.get()
                if new_category != previous_category[0]:
                    values = type_values_for_category(new_category)
                    if values:
                        fields["TYPE"].set(values[0])
                    previous_category[0] = new_category

            category_var.trace_add("write", category_changed)
            toggle_tn_fields()

            def save():
                category = category_var.get()
                physical_id = fields["Physical ID"].get().strip()
                type_ = fields["TYPE"].get().strip()
                if not physical_id or not type_:
                    messagebox.showerror(
                        "Validation",
                        "Physical ID and TYPE are required.",
                        parent=form,
                    )
                    return
                payload = {
                    "id": physical_id,
                    "categoria": category,
                    "type": type_,
                    "so": fields["SO"].get().strip() or None,
                    "ubicacion": fields["Location"].get().strip() or None,
                }
                if category in {"punzon", "matriz"}:
                    payload.update({
                        "tn": fields["TN"].get().strip(),
                        "size": fields["SIZE"].get().strip(),
                        "life": fields["LIFE"].get().strip(),
                    })
                    if category == "matriz":
                        payload["clearance"] = fields["CLEARANCE"].get().strip()
                try:
                    if existing:
                        response = requests.put(
                            f"http://127.0.0.1:8000/inventario/herramientas/{physical_id}",
                            json=payload,
                            timeout=15,
                        )
                    else:
                        response = requests.post(
                            "http://127.0.0.1:8000/inventario/herramientas",
                            json=payload,
                            timeout=15,
                        )
                    if response.status_code not in (200, 201):
                        messagebox.showerror("Save Error", api_error(response), parent=form)
                        return
                    log_audit(
                        "inventory_tool_updated" if existing else "inventory_tool_registered",
                        self.user["username"],
                        physical_id,
                        response.json(),
                    )
                    form.destroy()
                    refresh_inventory()
                    messagebox.showinfo(
                        "Registered",
                        f"Tool {physical_id} was {'updated' if existing else 'registered'} in Inventory.",
                        parent=win,
                    )
                except requests.RequestException as error:
                    messagebox.showerror(
                        "Save Error",
                        f"Could not save tool: {error}",
                        parent=form,
                    )

            ttk.Button(form, text="Save Changes" if existing else "Register Tool", command=save).grid(
                row=len(labels),
                column=0,
                columnspan=2,
                pady=12,
            )

        def selected_inventory_record():
            selection = tree.selection()
            if not selection:
                messagebox.showwarning("Inventory", "Select a tool first.", parent=win)
                return None
            values = tree.item(selection[0], "values")
            return {
                "id": values[0],
                "tn": values[1],
                "size": values[2],
                "type": values[3],
                "categoria": values[4],
                "clearance": values[5],
                "life": values[6],
                "so": values[7],
                "ubicacion": values[8],
            }

        def edit_tool():
            record = selected_inventory_record()
            if record:
                register_tool(existing=record)

        def delete_tool():
            record = selected_inventory_record()
            if not record:
                return
            if not messagebox.askyesno(
                "Delete Tool",
                f"Delete physical tool {record['id']} from inventory? This cannot be undone.",
                parent=win,
            ):
                return
            try:
                from urllib.parse import quote
                response = requests.delete(
                    f"{API_URL}{quote(record['id'], safe='')}",
                    timeout=15,
                )
                if response.status_code != 200:
                    messagebox.showerror("Delete Error", api_error(response), parent=win)
                    return
                log_audit(
                    "inventory_tool_deleted",
                    self.user["username"],
                    record["id"],
                    record,
                )
                refresh_inventory()
            except requests.RequestException as error:
                messagebox.showerror("Delete Error", f"Could not delete tool: {error}", parent=win)

        controls = tk.Frame(win, bg="#f4f6fa")
        controls.pack(pady=8)
        ttk.Button(controls, text="Refresh Inventory", command=refresh_inventory).pack(side=tk.LEFT, padx=5)
        if self.user.get("is_admin", False):
            ttk.Button(controls, text="Register Tool", command=register_tool).pack(side=tk.LEFT, padx=5)
            ttk.Button(controls, text="Edit Selected", command=edit_tool).pack(side=tk.LEFT, padx=5)
            ttk.Button(controls, text="Delete Selected", command=delete_tool).pack(side=tk.LEFT, padx=5)
        refresh_inventory()
        win.focus()

    def menu_alta(self):
        if not self.user.get("is_admin", False):
            messagebox.showwarning("Permission denied", "Only admin can add tools.")
            return
        win = tk.Toplevel(self.root)
        win.title("Add Tool")
        win.configure(bg="#f4f6fa")
        tk.Label(win, text="What do you want to add?", font=("Segoe UI", 12, "bold"), bg="#f4f6fa").pack(pady=10)
        ttk.Button(win, text="Punch", command=lambda:[win.destroy(), self.alta_punzon()]).pack(pady=5)
        ttk.Button(win, text="Die", command=lambda:[win.destroy(), self.alta_matriz()]).pack(pady=5)
        ttk.Button(win, text="Holder", command=lambda:[win.destroy(), self.alta_holder()]).pack(pady=5)
        ttk.Button(win, text="Accessory", command=lambda:[win.destroy(), self.alta_accesorio()]).pack(pady=5)

    def alta_punzon(self):
        if not self.user.get("is_admin", False):
            messagebox.showwarning("Permission denied", "Only admin can add tools.")
            return
        win = tk.Toplevel(self.root)
        win.title("Add Punch")
        win.configure(bg="#f4f6fa")
        fields = {}
        size_options = ["A", "B", "C", "D", "E"]
        type_options = [
            "ROUND", "SQUARE", "RECTANGULAR", "OVAL", "HEXAGONAL", "SPECIAL", "FORMED", "1 RADIUS", "2 RADII", "4 RADII"
        ]
        labels = [
            ("ID", True),
            ("TN", True),
            ("SIZE", True),
            ("TYPE", True),
            ("LIFE", True),
            ("SO", False),
            ("UBICACION", False)
        ]
        for i, (label, required) in enumerate(labels):
            tk.Label(win, text=label+(' *' if required else ''), bg="#f4f6fa").grid(row=i, column=0, sticky=tk.W, pady=2)
            if label == "SIZE":
                combo = ttk.Combobox(win, values=size_options, state="readonly", font=("Segoe UI", 11))
                combo.grid(row=i, column=1, pady=2, padx=5)
                fields[label] = combo
            elif label == "TYPE":
                combo = ttk.Combobox(win, values=type_options, state="readonly", font=("Segoe UI", 11))
                combo.grid(row=i, column=1, pady=2, padx=5)
                fields[label] = combo
            else:
                entry = tk.Entry(win, font=("Segoe UI", 11))
                entry.grid(row=i, column=1, pady=2, padx=5)
                fields[label] = entry
        def submit():
            codigo_unico = fields["ID"].get().strip()
            tn = fields["TN"].get().strip()
            size = fields["SIZE"].get().strip().upper()
            type_ = fields["TYPE"].get().strip().upper()
            life = fields["LIFE"].get().strip()
            so = fields["SO"].get().strip()
            ubicacion = fields["UBICACION"].get().strip()
            # Validar campos obligatorios
            if not tn:
                self.show_error("The field TN is required.", fields["TN"])
                return
            if not codigo_unico:
                self.show_error("The field ID is required.", fields["ID"])
                return
            if not size:
                self.show_error("The field SIZE is required.", fields["SIZE"])
                return
            if not type_:
                self.show_error("The field TYPE is required.", fields["TYPE"])
                return
            if not life:
                self.show_error("The field LIFE is required.", fields["LIFE"])
                return
            # Validar duplicados
            try:
                r = requests.get(API_URL)
                if r.status_code == 200:
                    herramientas = r.json()
                    for h in herramientas:
                        if h['id'] == codigo_unico:
                            self.show_error(f"Already exists a tool with ID '{codigo_unico}'.", fields["ID"])
                            return
            except Exception:
                pass
            # Validar y formatear LIFE
            if not (life.startswith(".") or life.startswith("0.")):
                self.show_error("LIFE must start with . or 0.", fields["LIFE"])
                return
            try:
                valor_life = float(life)
                life_fmt = f"{valor_life:.3f}"
            except:
                self.show_error("LIFE must be a valid number.", fields["LIFE"])
                return
            alerta = None
            if valor_life < 0.3:
                alerta = "Â¡End of life! LIFE < .3"
            elif valor_life > 0.6:
                alerta = "Â¡Warning! LIFE > .6 (recommended max .5)"
            data = {
                "id": codigo_unico,
                "tn": tn,
                "size": size,
                "type": type_,
                "life": life_fmt,
                "so": so,
                "categoria": "punch",
                "ubicacion": ubicacion
            }
            try:
                resp = requests.post(API_URL, json=data)
                if resp.status_code == 200 or resp.status_code == 201:
                    log_audit("alta", self.user["username"], codigo_unico, str(data))
                    win.destroy()
                    if alerta:
                        messagebox.showwarning("LIFE Alert", alerta)
                    self.show_success("Tool registered correctly.")
                else:
                    self.show_error(f"No se pudo agregar: {resp.text}")
            except Exception as e:
                self.show_error(f"No se pudo agregar: {e}")
        ttk.Button(win, text="Save", command=submit, style='TButton').grid(row=len(labels), column=0, columnspan=2, pady=10)

    def alta_matriz(self):
        if not self.user.get("is_admin", False):
            messagebox.showwarning("Permission denied", "Only admin can add tools.")
            return
        win = tk.Toplevel(self.root)
        win.title("Add Die")
        win.configure(bg="#f4f6fa")
        fields = {}
        size_options = ["A", "B", "C", "D", "E"]
        type_options = [
            "ROUND", "SQUARE", "RECTANGULAR", "OVAL", "HEXAGONAL", "SPECIAL", "FORMED", "1 RADIUS", "2 RADII", "4 RADII"
        ]
        labels = [
            ("ID", True),
            ("TN", True),
            ("SIZE", True),
            ("TYPE", True),
            ("LIFE", True),
            ("CLEARANCE", True),
            ("SO", False),
            ("UBICACION", False)
        ]
        for i, (label, required) in enumerate(labels):
            tk.Label(win, text=label+(' *' if required else ''), bg="#f4f6fa").grid(row=i, column=0, sticky=tk.W, pady=2)
            if label == "SIZE":
                combo = ttk.Combobox(win, values=size_options, state="readonly", font=("Segoe UI", 11))
                combo.grid(row=i, column=1, pady=2, padx=5)
                fields[label] = combo
            elif label == "TYPE":
                combo = ttk.Combobox(win, values=type_options, state="readonly", font=("Segoe UI", 11))
                combo.grid(row=i, column=1, pady=2, padx=5)
                fields[label] = combo
            else:
                entry = tk.Entry(win, font=("Segoe UI", 11))
                entry.grid(row=i, column=1, pady=2, padx=5)
                fields[label] = entry
        def submit():
            codigo_unico = fields["ID"].get().strip()
            tn = fields["TN"].get().strip()
            size = fields["SIZE"].get().strip().upper()
            type_ = fields["TYPE"].get().strip().upper()
            life = fields["LIFE"].get().strip()
            clearance = fields["CLEARANCE"].get().strip()
            so = fields["SO"].get().strip()
            ubicacion = fields["UBICACION"].get().strip()
            # Validar campos obligatorios
            if not codigo_unico:
                self.show_error("The field ID is required.", fields["ID"])
                return
            if not tn:
                self.show_error("The field TN is required.", fields["TN"])
                return
            if not size:
                self.show_error("The field SIZE is required.", fields["SIZE"])
                return
            if not type_:
                self.show_error("The field TYPE is required.", fields["TYPE"])
                return
            if not life:
                self.show_error("The field LIFE is required.", fields["LIFE"])
                return
            if not clearance:
                self.show_error("The field CLEARANCE is required.", fields["CLEARANCE"])
                return
            # Validar y formatear LIFE
            try:
                valor_life = float(life)
            except:
                self.show_error("LIFE must be a valid number.", fields["LIFE"])
                return
            if valor_life < 1.1:
                self.show_error("The minimum height allowed is 1.1", fields["LIFE"])
                return
            if valor_life > 1.25:
                self.show_error("The maximum height allowed is 1.25", fields["LIFE"])
                return
            life_fmt = f"{valor_life:.3f}"
            # Validar y formatear CLEARANCE
            try:
                valor_clearance = float(clearance)
            except:
                self.show_error("CLEARANCE must be a valid number.", fields["CLEARANCE"])
                return
            if not (clearance.startswith(".") or clearance.startswith("0.")):
                self.show_error("CLEARANCE must start with . or 0.", fields["CLEARANCE"])
                return
            clearance_fmt = f"{valor_clearance:.3f}"
            alerta = None
            if valor_life < 1.15:
                alerta = "Â¡Warning! LIFE close to the minimum (1.1)"
            elif valor_life > 1.22:
                alerta = "Â¡Warning! LIFE close to the maximum (1.25)"
            if not tn or not codigo_unico or not size or not type_ or not life or not clearance:
                self.show_error("All required fields must be filled.")
                return
            data = {
                "id": codigo_unico,
                "tn": tn,
                "size": size,
                "type": type_,
                "life": life_fmt,
                "clearance": clearance_fmt,
                "so": so,
                "categoria": "die",
                "ubicacion": ubicacion
            }
            try:
                resp = requests.post(API_URL, json=data)
                if resp.status_code == 200 or resp.status_code == 201:
                    log_audit("alta", self.user["username"], codigo_unico, str(data))
                    win.destroy()
                    if alerta:
                        messagebox.showwarning("LIFE Alert", alerta)
                    self.show_success("Tool registered correctly.")
                else:
                    self.show_error(f"No se pudo agregar: {resp.text}")
            except Exception as e:
                self.show_error(f"No se pudo agregar: {e}")
        ttk.Button(win, text="Save", command=submit, style='TButton').grid(row=len(labels), column=0, columnspan=2, pady=10)

    def generar_id_interno(self):
        # Busca el mayor ID numÃ©rico existente y suma 1, si no hay, inicia en 100000
        try:
            r = requests.get(API_URL)
            if r.status_code == 200:
                herramientas = r.json()
                ids = [int(h['id']) for h in herramientas if h.get('id','').isdigit()]
                nuevo_id = str(max(ids)+1) if ids else "100000"
                return nuevo_id
        except Exception:
            pass
        return str(random.randint(100000,999999))

    def alta_holder(self):
        if not self.user.get("is_admin", False):
            messagebox.showwarning("Permission denied", "Only admin can add holders.")
            return
        win = tk.Toplevel(self.root)
        win.title("Add Holder")
        win.configure(bg="#f4f6fa")
        fields = {}
        tipo_options = ["A", "B", "C", "D", "E"]
        tk.Label(win, text="TYPE *", bg="#f4f6fa").grid(row=0, column=0, sticky=tk.W, pady=2)
        tipo_combo = ttk.Combobox(win, values=tipo_options, state="readonly", font=("Segoe UI", 11))
        tipo_combo.grid(row=0, column=1, pady=2, padx=5)
        fields["TYPE"] = tipo_combo
        tk.Label(win, text="LOCATION", bg="#f4f6fa").grid(row=1, column=0, sticky=tk.W, pady=2)
        ubicacion_entry = tk.Entry(win, font=("Segoe UI", 11))
        ubicacion_entry.grid(row=1, column=1, pady=2, padx=5)
        fields["LOCATION"] = ubicacion_entry
        def submit():
            tipo = fields["TYPE"].get().strip().upper()
            ubicacion = fields["LOCATION"].get().strip()
            if not tipo:
                self.show_error("TYPE is required.", fields["TYPE"])
                return
            tipo_final = f"HOLDER {tipo}"
            data = {
                "id": self.generar_id_interno(),
                "type": tipo_final,
                "categoria": "holder",
                "ubicacion": ubicacion
            }
            try:
                resp = requests.post(API_URL, json=data)
                if resp.status_code == 200 or resp.status_code == 201:
                    log_audit("add", self.user["username"], data["id"], str(data))
                    win.destroy()
                    self.show_success("Holder added successfully.")
                else:
                    self.show_error(f"Could not add: {resp.text}")
            except Exception as e:
                self.show_error(f"Could not add: {e}")
        ttk.Button(win, text="Save", command=submit, style='TButton').grid(row=2, column=0, columnspan=2, pady=10)

    def alta_accesorio(self):
        if not self.user.get("is_admin", False):
            messagebox.showwarning("Permission denied", "Only admin can add accessories.")
            return
        win = tk.Toplevel(self.root)
        win.title("Add Accessory")
        win.configure(bg="#f4f6fa")
        fields = {}
        tipo_options = [
            "FLAT DIE A", "FLAT DIE B", "FLAT DIE C", "FLAT DIE D", "FLAT DIE E",
            "FLAT PUNCH A", "FLAT PUNCH B", "FLAT PUNCH C", "FLAT PUNCH D", "FLAT PUNCH E",
            "SLIDER DIE A", "SLIDER DIE B",
            "ADAPTER A-B", "ADAPTER B-E", "ADAPTER C-E", "ADAPTER D-E",
            "STAMPING PUNCH B", "STAMPING PUNCH C", "STAMPING PUNCH D",
            "STAMPING DIE B", "STAMPING DIE C", "STAMPING DIE D"
        ]
        tk.Label(win, text="TYPE *", bg="#f4f6fa").grid(row=0, column=0, sticky=tk.W, pady=2)
        tipo_combo = ttk.Combobox(win, values=tipo_options, state="readonly", font=("Segoe UI", 11))
        tipo_combo.grid(row=0, column=1, pady=2, padx=5)
        fields["TYPE"] = tipo_combo
        tk.Label(win, text="LOCATION", bg="#f4f6fa").grid(row=1, column=0, sticky=tk.W, pady=2)
        ubicacion_entry = tk.Entry(win, font=("Segoe UI", 11))
        ubicacion_entry.grid(row=1, column=1, pady=2, padx=5)
        fields["LOCATION"] = ubicacion_entry
        def submit():
            tipo = fields["TYPE"].get().strip().upper()
            ubicacion = fields["LOCATION"].get().strip()
            if not tipo:
                self.show_error("TYPE is required.", fields["TYPE"])
                return
            data = {
                "id": self.generar_id_interno(),
                "type": tipo,
                "categoria": "accesorio",
                "ubicacion": ubicacion
            }
            try:
                resp = requests.post(API_URL, json=data)
                if resp.status_code == 200 or resp.status_code == 201:
                    log_audit("add", self.user["username"], data["id"], str(data))
                    win.destroy()
                    self.show_success("Accessory added successfully.")
                else:
                    self.show_error(f"Could not add: {resp.text}")
            except Exception as e:
                self.show_error(f"Could not add: {e}")
        ttk.Button(win, text="Save", command=submit, style='TButton').grid(row=2, column=0, columnspan=2, pady=10)

    def ajustar_conteo(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("Selecciona", "Selecciona una herramienta de la tabla.")
            return
        item = self.tree.item(selected[0])
        values = item['values']
        if values[4] == '':
            messagebox.showinfo("No aplica", "Solo puedes ajustar conteo de herramientas sin ID Ãºnico.")
            return
        try:
            nuevo_conteo = int(simpledialog.askstring("Ajuste", "Nuevo conteo:"))
        except:
            messagebox.showerror("Error", "Cantidad invÃ¡lida")
            return
        try:
            requests.patch(f"{API_URL}{values[0]}/ajustar_conteo?conteo={nuevo_conteo}")
            self.refresh()
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo ajustar: {e}")

    def editar_herramienta(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("Selecciona", "Selecciona una herramienta para editar.")
            return
        item = self.tree.item(selected[0])
        values = item['values']
        win = tk.Toplevel(self.root)
        win.title("Editar herramienta")
        win.configure(bg="#f4f6fa")
        fields = {}
        type_options = ["A", "B", "C", "D", "E"]
        labels = [
            ("TN", True),
            ("ID", True),
            ("TYPE", True),
            ("POSTRUSION", True),
            ("altura_matriz", False),
            ("SO", False)
        ]
        for i, (label, required) in enumerate(labels):
            tk.Label(win, text=label+(' *' if required else ''), bg="#f4f6fa").grid(row=i, column=0, sticky=tk.W, pady=2)
            if label == "TYPE":
                combo = ttk.Combobox(win, values=type_options, state="readonly", font=("Segoe UI", 11))
                combo.set(values[2])
                combo.grid(row=i, column=1, pady=2, padx=5)
                fields[label] = combo
            else:
                entry = tk.Entry(win, font=("Segoe UI", 11))
                entry.insert(0, values[i+1])
                entry.grid(row=i, column=1, pady=2, padx=5)
                fields[label] = entry
        def submit():
            tn = fields["TN"].get().strip()
            codigo_unico = fields["ID"].get().strip()
            tipo = fields["TYPE"].get().strip().upper()
            postrusion = fields["POSTRUSION"].get().strip()
            altura_matriz = fields["altura_matriz"].get().strip()
            so = fields["SO"].get().strip()
            if not (postrusion.startswith(".") or postrusion.startswith("0.")):
                self.show_error("POSTRUSION must start with . or 0.", fields["POSTRUSION"])
                return
            try:
                valor_postrusion = float(postrusion)
            except:
                self.show_error("POSTRUSION must be a valid number.", fields["POSTRUSION"])
                return
            alerta = None
            if valor_postrusion < 0.3:
                alerta = "End of life! The postrusion is less than .3"
            elif valor_postrusion > 0.6:
                alerta = "Warning! The postrusion is greater than .6 (recommended max .5)"
            if not tn or not codigo_unico or not tipo or not postrusion:
                self.show_error("All required fields must be filled")
                return
            data = {
                "tn": tn,
                "type": tipo,
                "codigo_unico": codigo_unico,
                "altura_punta": postrusion,
                "altura_matriz": altura_matriz,
                "so": so
            }
            try:
                requests.patch(f"{API_URL}{values[0]}", json=data)
                win.destroy()
                if alerta:
                    messagebox.showwarning("Alerta de postrusion", alerta)
            except Exception as e:
                self.show_error(f"No se pudo editar: {e}")
        ttk.Button(win, text="Guardar cambios", command=submit, style='TButton').grid(row=len(labels), column=0, columnspan=2, pady=10)

    def eliminar_herramienta(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("Selecciona", "Selecciona una herramienta para eliminar.")
            return
        item = self.tree.item(selected[0])
        values = item['values']
        if messagebox.askyesno("Eliminar", f"Â¿Seguro que deseas eliminar la herramienta ID {values[0]}?"):
            if not self.user.get("is_admin", False):
                messagebox.showwarning("Permission denied", "Only admin can delete tools.")
                return
            try:
                r = requests.delete(f"{API_URL}{values[0]}")
                if r.status_code == 200:
                    log_audit("eliminacion", self.user["username"], values[0], "Deleted tool")
                    self.refresh()
                else:
                    messagebox.showerror("Delete", f"Could not delete: {r.text}")
            except Exception as e:
                messagebox.showerror("Error", f"No se pudo eliminar: {e}")

    def eliminar(self):
        messagebox.showinfo("Delete", "FunciÃ³n eliminar aÃºn no implementada.")

    def editar(self):
        win = tk.Toplevel(self.root)
        win.title("Editar herramienta")
        win.configure(bg="#f4f6fa")
        selected = self.root.focus_get().master.focus_get().selection()
        if not selected:
            messagebox.showwarning("Edit", "Selecciona una herramienta para editar.")
            win.destroy()
            return
        item = self.root.focus_get().master.focus_get().item(selected[0])
        vals = item['values']
        fields = {}
        labels = ["ID", "TN", "SIZE", "TYPE", "LIFE", "CLEARANCE", "SO", "CATEGORY", "UBICACION"]
        for i, label in enumerate(labels):
            tk.Label(win, text=label, bg="#f4f6fa").grid(row=i, column=0, sticky=tk.W, pady=2)
            entry = tk.Entry(win, font=("Segoe UI", 11))
            entry.insert(0, vals[i])
            fields[label] = entry
        def save_edit():
            data = {
                "id": fields["ID"].get().strip(),
                "tn": fields["TN"].get().strip(),
                "size": fields["SIZE"].get().strip(),
                "type": fields["TYPE"].get().strip(),
                "life": fields["LIFE"].get().strip(),
                "clearance": fields["CLEARANCE"].get().strip(),
                "so": fields["SO"].get().strip(),
                "categoria": fields["CATEGORY"].get().strip().lower(),
                "ubicacion": fields["UBICACION"].get().strip()
            }
            try:
                r = requests.put(f"http://127.0.0.1:8000/herramientas/{vals[0]}", json=data)
                if r.status_code == 200:
                    messagebox.showinfo("Edit", "Herramienta actualizada correctamente.")
                    win.destroy()
                else:
                    messagebox.showerror("Edit", f"No se pudo actualizar: {r.text}")
            except Exception as e:
                messagebox.showerror("Edit", f"Error: {e}")
        ttk.Button(win, text="Guardar cambios", command=save_edit, style='TButton').grid(row=len(labels), column=0, columnspan=2, pady=10)

    def ver_auditoria(self):
        win = tk.Toplevel(self.root)
        win.title("Audit Log")
        win.configure(bg="#f4f6fa")
        filter_frame = tk.Frame(win, bg="#f4f6fa")
        filter_frame.pack(pady=5)
        tk.Label(filter_frame, text="Usuario:", bg="#f4f6fa").pack(side=tk.LEFT)
        user_var = tk.StringVar()
        user_entry = tk.Entry(filter_frame, textvariable=user_var, font=("Segoe UI", 10), width=12)
        user_entry.pack(side=tk.LEFT, padx=5)
        tk.Label(filter_frame, text="AcciÃ³n:", bg="#f4f6fa").pack(side=tk.LEFT)
        action_var = tk.StringVar()
        action_entry = tk.Entry(filter_frame, textvariable=action_var, font=("Segoe UI", 10), width=10)
        action_entry.pack(side=tk.LEFT, padx=5)
        tk.Label(filter_frame, text="Fecha (YYYY-MM-DD):", bg="#f4f6fa").pack(side=tk.LEFT)
        date_var = tk.StringVar()
        date_entry = tk.Entry(filter_frame, textvariable=date_var, font=("Segoe UI", 10), width=12)
        date_entry.pack(side=tk.LEFT, padx=5)
        tree_audit = ttk.Treeview(win, columns=("Timestamp", "User", "Action", "ToolID", "Details"), show="headings", height=20)
        for col in tree_audit["columns"]:
            tree_audit.heading(col, text=col)
            tree_audit.column(col, width=120, anchor=tk.CENTER)
        tree_audit.pack(fill=tk.BOTH, expand=True)
        def buscar_auditoria():
            for row in tree_audit.get_children():
                tree_audit.delete(row)
            params = {}
            if user_var.get().strip():
                params['user'] = user_var.get().strip()
            if action_var.get().strip():
                params['action'] = action_var.get().strip()
            if date_var.get().strip():
                params['fecha'] = date_var.get().strip()
            try:
                r = requests.get("http://127.0.0.1:8000/audit/filter", params=params)
                if r.status_code == 200:
                    for row in r.json():
                        tree_audit.insert('', tk.END, values=(row["Timestamp"], row["User"], row["Action"], row["ToolID"], row["Details"]))
                else:
                    messagebox.showerror("AuditorÃ­a", f"No se pudo obtener la bitÃ¡cora: {r.text}")
            except Exception as e:
                messagebox.showerror("AuditorÃ­a", f"Error: {e}")
        ttk.Button(filter_frame, text="Buscar", command=buscar_auditoria, style='TButton').pack(side=tk.LEFT, padx=10)
        buscar_auditoria()

    def show_error(self, msg, field=None):
        messagebox.showerror("Error", msg)
        if field:
            field.config(bg="#ffe6e6")
            field.after(2000, lambda: field.config(bg="white"))
    def show_success(self, msg):
        messagebox.showinfo("Ã‰xito", msg)

    def auto_backup_csv(self, interval=3600):
        # Solo guarda UN backup, sobreescribiendo el anterior
        def backup():
            while True:
                try:
                    r = requests.get(API_URL)
                    if r.status_code == 200:
                        herramientas = r.json()
                        with open("backup_inventario.csv", "w", encoding="utf-8", newline='') as f:
                            writer = csv.writer(f)
                            writer.writerow(["ID", "TN", "SIZE", "TYPE", "LIFE", "CLEARANCE", "SO", "CATEGORY", "UBICACION"])
                            for h in herramientas:
                                writer.writerow([
                                    h.get('id',''), h.get('tn',''), h.get('size',''), h.get('type',''),
                                    h.get('life',''), h.get('clearance',''), h.get('so',''), h.get('categoria',''), h.get('ubicacion','')
                                ])
                except Exception:
                    pass
                time.sleep(interval)
        t = threading.Thread(target=backup, daemon=True)
        t.start()

    def select_machine(self):
        win = tk.Toplevel(self.root)
        win.title("Select Machine")
        win.configure(bg="#f4f6fa")
        tk.Label(win, text="Select the machine for tool dispensing:", font=("Segoe UI", 12, "bold"), bg="#f4f6fa").pack(pady=10)
        machine_options = [f"T-{i:03d}" for i in range(101, 118)]
        machine_var = tk.StringVar(value=machine_options[0])
        machine_combo = ttk.Combobox(win, textvariable=machine_var, values=machine_options, state="readonly", font=("Segoe UI", 11), width=15)
        machine_combo.pack(pady=10)
        def confirm():
            selected_machine = machine_var.get()
            messagebox.showinfo("Machine Selected", f"You selected: {selected_machine}")
            win.destroy()
            # AquÃ­ se puede continuar con el flujo de selecciÃ³n de set up
        ttk.Button(win, text="Confirm", command=confirm, style='TButton').pack(pady=10)

    def tool_dispensing_flow(self):
        SetupWorkflowWindow(self.root, self.user).open()

    def tool_location_flow(self):
        ToolLocationWindow(self.root, self.user).open()

    def machines_flow(self):
        MachineWindow(self.root, self.user).open()

    def programs_flow(self):
        ProgramWindow(self.root, self.user).open()

    def select_setups_for_comparison(self, machine):
        win = tk.Toplevel(self.root)
        win.title("Select Setups for Comparison")
        win.configure(bg="#f4f6fa")
        tk.Label(win, text=f"Machine: {machine}", font=("Segoe UI", 11, "bold"), bg="#f4f6fa").pack(pady=5)
        tk.Label(win, text="Select the CURRENT setup file (.stp):", bg="#f4f6fa").pack(pady=5)
        current_var = tk.StringVar()
        def browse_current():
            from tkinter import filedialog
            file_path = filedialog.askopenfilename(filetypes=[("Setup files", "*.stp")])
            if file_path:
                current_var.set(file_path)
        ttk.Button(win, text="Browse Current", command=browse_current, style='TButton').pack(pady=2)
        current_entry = tk.Entry(win, textvariable=current_var, font=("Segoe UI", 10), width=50)
        current_entry.pack(pady=2)
        tk.Label(win, text="Select the NEW setup file (.stp):", bg="#f4f6fa").pack(pady=5)
        new_var = tk.StringVar()
        def browse_new():
            from tkinter import filedialog
            file_path = filedialog.askopenfilename(filetypes=[("Setup files", "*.stp")])
            if file_path:
                new_var.set(file_path)
        ttk.Button(win, text="Browse New", command=browse_new, style='TButton').pack(pady=2)
        new_entry = tk.Entry(win, textvariable=new_var, font=("Segoe UI", 10), width=50)
        new_entry.pack(pady=2)
        def next_step():
            current_file = current_var.get()
            new_file = new_var.get()
            if not current_file or not new_file:
                messagebox.showwarning("Select files", "Please select both setup files.")
                return
            win.destroy()
            self.compare_and_dispense(machine, current_file, new_file)
        ttk.Button(win, text="Next", command=next_step, style='TButton').pack(pady=10)

    def compare_and_dispense(self, machine, current_file, new_file):
        # Parse both .stp files
        current_tools = [str(t).strip() for t in self.parse_stp_tool_ids(current_file)]
        new_tools = [str(t).strip() for t in self.parse_stp_tool_ids(new_file)]
        # Tools to dispense = in new_tools but not in current_tools
        to_dispense = [t for t in new_tools if t and t not in current_tools]
        print(f"[DEBUG] current_tools: {current_tools}")
        print(f"[DEBUG] new_tools: {new_tools}")
        print(f"[DEBUG] to_dispense (final): {to_dispense}")
        win = tk.Toplevel(self.root)
        win.title("Digital Comparative Sheet & Dispensing")
        win.configure(bg="#f4f6fa")
        tk.Label(win, text=f"Machine: {machine}", font=("Segoe UI", 11, "bold"), bg="#f4f6fa").pack(pady=5)
        tk.Label(win, text=f"Current setup: {current_file}", font=("Segoe UI", 9), bg="#f4f6fa").pack(pady=2)
        tk.Label(win, text=f"New setup: {new_file}", font=("Segoe UI", 9), bg="#f4f6fa").pack(pady=2)
        tk.Label(win, text="Comparative Sheet (Tools to Dispense):", bg="#f4f6fa").pack(pady=5)
        tree = ttk.Treeview(win, columns=("Tool No.", "Status"), show="headings", height=10)
        tree.heading("Tool No.", text="Tool No.")
        tree.heading("Status", text="Status")
        for tool in to_dispense:
            tree.insert('', tk.END, values=(tool, "Pending"))
        tree.pack(pady=5)
        progress_var = tk.IntVar(value=0)
        progress_bar = ttk.Progressbar(win, maximum=len(to_dispense), variable=progress_var, length=300)
        progress_bar.pack(pady=5)
        scanned = set()
        def scan_tool():
            code = simpledialog.askstring("Scan Tool", "Enter or scan tool ID (or TN):")
            if not code:
                return
            # Buscar el TN correspondiente al ID en inventario
            try:
                import requests
                r = requests.get("http://127.0.0.1:8000/herramientas/")
                if r.status_code == 200:
                    inventario = r.json()
                else:
                    inventario = []
            except Exception:
                inventario = []
            tn = None
            tool_id = None
            # Buscar por ID exacto
            for h in inventario:
                if str(h.get('id','')) == code:
                    tn = str(h.get('tn','')).strip()
                    tool_id = str(h.get('id',''))
                    break
            # Si no se encontrÃ³ por ID, intentar por TN directo
            if not tn and code in to_dispense:
                tn = code
            if not tn:
                messagebox.showerror("Error", f"Tool ID or TN {code} is not in inventory or required for this setup.")
                return
            # Validar si el TN estÃ¡ en la lista a surtir
            if tn not in to_dispense:
                messagebox.showerror("Error", f"Tool TN {tn} is not required for this setup.")
                return
            for iid in tree.get_children():
                val = tree.item(iid)["values"][0]
                if val == tn and val not in scanned:
                    tree.set(iid, "Status", "Dispensed")
                    scanned.add(val)
                    progress_var.set(len(scanned))
                    # Registrar en bitÃ¡cora/auditorÃ­a
                    from audit_log import log_audit
                    log_audit(
                        action="surtido",
                        user=self.user["username"],
                        tool_id=tool_id if tool_id else tn,
                        details={
                            "tn": tn,
                            "machine": machine,
                            "setup_current": current_file,
                            "setup_new": new_file
                        }
                    )
                    break
            else:
                messagebox.showwarning("Already scanned", f"Tool {tn} already dispensed.")
        ttk.Button(win, text="Scan Tool", command=scan_tool, style='TButton').pack(pady=5)
        def finish():
            if len(scanned) < len(to_dispense):
                messagebox.showwarning("Incomplete", "Not all tools have been dispensed.")
                return
            messagebox.showinfo("Complete", "All tools dispensed! Awaiting admin verification.")
            win.destroy()
            # AquÃ­ se puede continuar con la verificaciÃ³n admin y generaciÃ³n de cÃ³digo de barras
        ttk.Button(win, text="Finish Dispensing", command=finish, style='TButton').pack(pady=10)

    def parse_stp_tool_ids(self, stp_file):
        # Parser ultra-tolerante: detecta cualquier encabezado tipo Tool No/TN y muestra primeras 40 lÃ­neas si falla
        import re
        try:
            with open(stp_file, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            tool_numbers = []
            in_tool_list = False
            header_found = False
            for idx, line in enumerate(lines):
                l = line.strip().lower()
                if 'tool list' in l:
                    in_tool_list = True
                    continue
                # Detecta encabezado de tabla
                if in_tool_list and not header_found and 'tool no' in l:
                    header_found = True
                    continue
                # Extrae solo el Ãºltimo valor de cada fila de datos como Tool No. (TN)
                if in_tool_list and header_found:
                    if 'extra notes' in l:
                        break
                    if l == '':
                        continue
                    row = line.strip().split()
                    if len(row) < 2:
                        continue
                    val = row[-1].strip().replace('\u200b','').replace('\ufeff','')
                    tool_numbers.append(val)
                    print(f"[DEBUG] Tool No. (TN) encontrado: {val}")
            # Fallback: si no encontrÃ³ nada, muestra primeras 40 lÃ­neas para depuraciÃ³n
            if not tool_numbers:
                print(f"[ERROR] No Tool Numbers found in file: {stp_file}")
                print("[INFO] Mostrando primeras 40 lÃ­neas del archivo para depuraciÃ³n:")
                for i, l in enumerate(lines[:40]):
                    print(f"{i+1:02d}: {l.strip()}")
            return tool_numbers
        except Exception as e:
            print(f"Parse error: {e}")
            return []


if __name__ == "__main__":
    root = tk.Tk()
    apply_theme(root)
    app = HerramientaApp(root)
    root.mainloop()
