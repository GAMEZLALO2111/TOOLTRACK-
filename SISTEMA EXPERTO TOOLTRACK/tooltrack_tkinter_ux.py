import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
import requests
from export_csv import export_inventory_to_csv
from import_csv import import_inventory_from_csv
from audit_log import log_audit
import os
import csv
import threading
import time
import uuid
import random
import re

API_URL = "http://127.0.0.1:8000/herramientas/"


class HerramientaApp:
    def __init__(self, root):
        self.root = root
        self.user = None
        self.auto_backup_csv(interval=3600)  # Backup cada hora
        self.show_login()

    def show_login(self):
        login_win = tk.Toplevel(self.root)
        login_win.title("Login - TOOLTRACK")
        login_win.geometry("320x220")
        login_win.grab_set()
        tk.Label(login_win, text="Usuario:", font=("Segoe UI", 12)).pack(pady=10)
        user_var = tk.StringVar()
        tk.Entry(login_win, textvariable=user_var, font=("Segoe UI", 12)).pack(pady=2)
        tk.Label(login_win, text="Contraseña:", font=("Segoe UI", 12)).pack(pady=10)
        pass_var = tk.StringVar()
        tk.Entry(login_win, textvariable=pass_var, font=("Segoe UI", 12), show="*").pack(pady=2)
        msg_var = tk.StringVar()
        tk.Label(login_win, textvariable=msg_var, fg="red").pack(pady=5)
        def do_login():
            username = user_var.get().strip()
            password = pass_var.get().strip()
            if not username or not password:
                msg_var.set("Ingresa usuario y contraseña.")
                return
            try:
                import requests
                r = requests.post("http://127.0.0.1:8000/login", json={"username": username, "password": password})
                if r.status_code == 200:
                    data = r.json()
                    self.user = {"username": data["username"], "is_admin": data["is_admin"]}
                    msg_var.set("")
                    login_win.destroy()
                    self.show_main_menu()
                else:
                    msg_var.set("Credenciales incorrectas.")
            except Exception as e:
                msg_var.set(f"Error de conexión: {e}")
        tk.Button(login_win, text="Iniciar sesión", command=do_login, font=("Segoe UI", 12)).pack(pady=15)

    def show_main_menu(self):
        # Encabezado llamativo
        header = tk.Label(self.root, text="TOOLTRACK", font=("Segoe UI", 24, "bold"), bg="#0078D7", fg="white", pady=18)
        header.pack(fill=tk.X)
        # Menú principal llamativo
        menu_frame = tk.Frame(self.root, bg="#f4f6fa")
        menu_frame.pack(pady=60)
        ttk.Button(menu_frame, text="Inventory", command=self.abrir_inventario, style='TButton', width=20).pack(pady=15)
        ttk.Button(menu_frame, text="Add Tool", command=self.menu_alta, style='TButton', width=20).pack(pady=15)
        ttk.Button(menu_frame, text="Tool Dispensing", command=self.tool_dispensing_flow, style='TButton', width=20).pack(pady=15)
        ttk.Button(menu_frame, text="View Audit Log", command=self.ver_auditoria, style='TButton', width=20).pack(pady=15)
        if self.user.get("is_admin", False):
            ttk.Button(menu_frame, text="Administrar Usuarios", command=self.admin_usuarios, style='TButton', width=20).pack(pady=15)
        else:
            ttk.Label(menu_frame, text="Operator mode: view only", font=("Segoe UI", 10), bg="#f4f6fa", fg="#666").pack(pady=10)

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
                    messagebox.showerror("Error", "Usuario y contraseña requeridos")
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
            tk.Label(top, text="Contraseña:").pack()
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
            if messagebox.askyesno("Confirmar", f"¿Eliminar usuario {username}?"):
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
                    messagebox.showerror("Error", "Contraseña requerida")
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
            tk.Label(top, text="Nueva Contraseña:").pack()
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
        # Paginación
        page_var = tk.IntVar(value=1)
        page_size = 15
        total_pages = [1]
        def update_pagination(total):
            total_pages[0] = max(1, (total + page_size - 1) // page_size)
            page_label.config(text=f"Página {page_var.get()} de {total_pages[0]}")
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
                    # Filtro por tipo/categoría
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
        page_label = tk.Label(btn_frame, text="Página 1 de 1", bg="#f4f6fa", font=("Segoe UI", 10))
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
                alerta = "¡End of life! LIFE < .3"
            elif valor_life > 0.6:
                alerta = "¡Warning! LIFE > .6 (recommended max .5)"
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
                alerta = "¡Warning! LIFE close to the minimum (1.1)"
            elif valor_life > 1.22:
                alerta = "¡Warning! LIFE close to the maximum (1.25)"
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
        # Busca el mayor ID numérico existente y suma 1, si no hay, inicia en 100000
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
            messagebox.showinfo("No aplica", "Solo puedes ajustar conteo de herramientas sin ID único.")
            return
        try:
            nuevo_conteo = int(simpledialog.askstring("Ajuste", "Nuevo conteo:"))
        except:
            messagebox.showerror("Error", "Cantidad inválida")
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
        if messagebox.askyesno("Eliminar", f"¿Seguro que deseas eliminar la herramienta ID {values[0]}?"):
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
        messagebox.showinfo("Delete", "Función eliminar aún no implementada.")

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
        tk.Label(filter_frame, text="Acción:", bg="#f4f6fa").pack(side=tk.LEFT)
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
                    messagebox.showerror("Auditoría", f"No se pudo obtener la bitácora: {r.text}")
            except Exception as e:
                messagebox.showerror("Auditoría", f"Error: {e}")
        ttk.Button(filter_frame, text="Buscar", command=buscar_auditoria, style='TButton').pack(side=tk.LEFT, padx=10)
        buscar_auditoria()

    def show_error(self, msg, field=None):
        messagebox.showerror("Error", msg)
        if field:
            field.config(bg="#ffe6e6")
            field.after(2000, lambda: field.config(bg="white"))
    def show_success(self, msg):
        messagebox.showinfo("Éxito", msg)

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
            # Aquí se puede continuar con el flujo de selección de set up
        ttk.Button(win, text="Confirm", command=confirm, style='TButton').pack(pady=10)

    def tool_dispensing_flow(self):
        # Step 1: Select machine
        win = tk.Toplevel(self.root)
        win.title("Tool Dispensing Flow")
        win.configure(bg="#f4f6fa")
        tk.Label(win, text="Select the machine:", font=("Segoe UI", 12, "bold"), bg="#f4f6fa").pack(pady=10)
        machine_options = [f"T-{i:03d}" for i in range(101, 118)]
        machine_var = tk.StringVar(value=machine_options[0])
        machine_combo = ttk.Combobox(win, textvariable=machine_var, values=machine_options, state="readonly", font=("Segoe UI", 11), width=15)
        machine_combo.pack(pady=10)
        def next_step():
            selected_machine = machine_var.get()
            win.destroy()
            self.select_setups_for_comparison(selected_machine)
        ttk.Button(win, text="Next", command=next_step, style='TButton').pack(pady=10)

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
            # Si no se encontró por ID, intentar por TN directo
            if not tn and code in to_dispense:
                tn = code
            if not tn:
                messagebox.showerror("Error", f"Tool ID or TN {code} is not in inventory or required for this setup.")
                return
            # Validar si el TN está en la lista a surtir
            if tn not in to_dispense:
                messagebox.showerror("Error", f"Tool TN {tn} is not required for this setup.")
                return
            for iid in tree.get_children():
                val = tree.item(iid)["values"][0]
                if val == tn and val not in scanned:
                    tree.set(iid, "Status", "Dispensed")
                    scanned.add(val)
                    progress_var.set(len(scanned))
                    # Registrar en bitácora/auditoría
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
            # Aquí se puede continuar con la verificación admin y generación de código de barras
        ttk.Button(win, text="Finish Dispensing", command=finish, style='TButton').pack(pady=10)

    def parse_stp_tool_ids(self, stp_file):
        # Parser ultra-tolerante: detecta cualquier encabezado tipo Tool No/TN y muestra primeras 40 líneas si falla
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
                # Extrae solo el último valor de cada fila de datos como Tool No. (TN)
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
            # Fallback: si no encontró nada, muestra primeras 40 líneas para depuración
            if not tool_numbers:
                print(f"[ERROR] No Tool Numbers found in file: {stp_file}")
                print("[INFO] Mostrando primeras 40 líneas del archivo para depuración:")
                for i, l in enumerate(lines[:40]):
                    print(f"{i+1:02d}: {l.strip()}")
            return tool_numbers
        except Exception as e:
            print(f"Parse error: {e}")
            return []


if __name__ == "__main__":
    root = tk.Tk()
    app = HerramientaApp(root)
    root.mainloop()
