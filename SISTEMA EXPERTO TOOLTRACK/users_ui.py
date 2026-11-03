import tkinter as tk
from tkinter import messagebox, ttk

import requests

import api_client

USERS_URL = f"{api_client.API_BASE}/users"
ROLE_INFO = {
    "SYSTEM_ADMIN": "Users, machines, locations, catalogs, audit",
    "ENGINEERING": "Programs and part numbers, read-only elsewhere",
    "PLANNING": "Creates and edits the kitting demand",
    "TOOLCRIB_SUPERVISOR": "Toolcrib operations + inventory adjustments and audit",
    "TOOLCRIB_OPERATOR": "Kitting, deliveries, returns, run progress",
    "MAINTENANCE_TECHNICIAN": "Receives, sharpens and releases tools",
}
ROLES = list(ROLE_INFO)


class UsersWindow:
    """Administración de usuarios: rol único por usuario, desactivar en lugar de borrar."""

    def __init__(self, parent, current_username):
        self.parent = parent
        self.current_username = current_username
        self.window = None
        self.tree = None

    def open(self):
        if self.window is not None and self.window.winfo_exists():
            self.window.lift()
            return self.window
        win = self.window = tk.Toplevel(self.parent)
        win.title("User Administration")
        win.geometry("760x460")
        win.minsize(620, 380)
        win.configure(bg="#f4f6fa")

        frame = ttk.Frame(win, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)
        self.tree = ttk.Treeview(frame, columns=("username", "role", "status", "scope"), show="headings",
                                 selectmode="browse")
        for column, title, width in (("username", "Username", 140), ("role", "Role", 190),
                                     ("status", "Status", 90), ("scope", "Can do", 300)):
            self.tree.heading(column, text=title)
            self.tree.column(column, width=width, anchor="w")
        self.tree.tag_configure("inactive", foreground="#999999")
        self.tree.pack(fill=tk.BOTH, expand=True)

        buttons = ttk.Frame(win, padding=(12, 0, 12, 12))
        buttons.pack(fill=tk.X)
        ttk.Button(buttons, text="New user…", command=self.new_user).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Change role…", command=self.change_role).pack(side=tk.LEFT, padx=6)
        ttk.Button(buttons, text="Reset password…", command=self.reset_password).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Deactivate / Reactivate", command=self.toggle_active).pack(side=tk.LEFT, padx=6)
        ttk.Button(buttons, text="Refresh", command=self.refresh).pack(side=tk.RIGHT)
        self.refresh()
        return win

    def _call(self, method, url, payload=None):
        try:
            response = requests.request(method, url, json=payload)
        except requests.RequestException:
            messagebox.showerror("No connection", "Could not reach the TOOLTRACK server. Check that it is running.",
                                 parent=self.window)
            return None
        if not response.ok:
            messagebox.showerror("Not saved", api_client.error_detail(response), parent=self.window)
            return None
        return response.json() if response.content else {}

    def refresh(self):
        users = self._call("GET", USERS_URL)
        if users is None:
            return
        self.tree.delete(*self.tree.get_children())
        for user in users:
            self.tree.insert(
                "", tk.END, iid=user["username"],
                values=(user["username"], user["role"], "Active" if user["activo"] else "Inactive",
                        ROLE_INFO.get(user["role"], "")),
                tags=() if user["activo"] else ("inactive",),
            )

    def _selected(self):
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("Select a user", "Select a user in the list first.", parent=self.window)
            return None
        return selection[0]

    def _form(self, title, with_username=False, with_password=False, with_role=False, current_role=None):
        dialog = tk.Toplevel(self.window)
        dialog.title(title)
        dialog.transient(self.window)
        dialog.grab_set()
        form = ttk.Frame(dialog, padding=14)
        form.pack(fill=tk.BOTH, expand=True)
        values = {}
        row = 0
        if with_username:
            ttk.Label(form, text="Username").grid(row=row, column=0, sticky="w", pady=4)
            values["username"] = tk.StringVar()
            ttk.Entry(form, textvariable=values["username"], width=32).grid(row=row, column=1, pady=4)
            row += 1
        if with_password:
            ttk.Label(form, text="Password (min. 6)").grid(row=row, column=0, sticky="w", pady=4)
            values["password"] = tk.StringVar()
            ttk.Entry(form, textvariable=values["password"], show="•", width=32).grid(row=row, column=1, pady=4)
            row += 1
        if with_role:
            ttk.Label(form, text="Role").grid(row=row, column=0, sticky="w", pady=4)
            values["role"] = tk.StringVar(value=current_role or "TOOLCRIB_OPERATOR")
            ttk.Combobox(form, textvariable=values["role"], values=ROLES, state="readonly", width=30).grid(
                row=row, column=1, pady=4)
            row += 1
            hint = tk.StringVar()
            ttk.Label(form, textvariable=hint, foreground="#555", wraplength=300).grid(
                row=row, column=0, columnspan=2, sticky="w")
            values["role"].trace_add("write", lambda *_: hint.set(ROLE_INFO.get(values["role"].get(), "")))
            hint.set(ROLE_INFO.get(values["role"].get(), ""))
            row += 1
        result = {}

        def accept():
            result.update({key: var.get().strip() for key, var in values.items()})
            dialog.destroy()

        buttons = ttk.Frame(form)
        buttons.grid(row=row, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="Save", command=accept).pack(side=tk.RIGHT, padx=6)
        dialog.wait_window()
        return result or None

    def new_user(self):
        data = self._form("New user", with_username=True, with_password=True, with_role=True)
        if not data:
            return
        if self._call("POST", USERS_URL, data) is not None:
            self.refresh()

    def change_role(self):
        username = self._selected()
        if not username:
            return
        current = self.tree.set(username, "role")
        data = self._form(f"Change role: {username}", with_role=True, current_role=current)
        if data and data["role"] != current:
            if self._call("PATCH", f"{USERS_URL}/{username}", {"role": data["role"]}) is not None:
                self.refresh()

    def reset_password(self):
        username = self._selected()
        if not username:
            return
        data = self._form(f"Reset password: {username}", with_password=True)
        if data and self._call("PATCH", f"{USERS_URL}/{username}", {"password": data["password"]}) is not None:
            messagebox.showinfo("Password updated", f"The password of {username} was changed.", parent=self.window)

    def toggle_active(self):
        username = self._selected()
        if not username:
            return
        active = self.tree.set(username, "status") == "Active"
        if username == self.current_username and active:
            messagebox.showinfo("Not allowed", "You cannot deactivate your own account.", parent=self.window)
            return
        action = "Deactivate" if active else "Reactivate"
        if not messagebox.askyesno(action, f"{action} user {username}?\nTheir history is kept.", parent=self.window):
            return
        result = self._call("PATCH", f"{USERS_URL}/{username}", {"activo": not active})
        if result is not None:
            self.refresh()
