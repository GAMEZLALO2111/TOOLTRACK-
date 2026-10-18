import tkinter as tk
from tkinter import ttk

BG = "#eef1f5"
CARD = "#ffffff"
NAVY = "#16222f"
NAVY_LIGHT = "#223245"
ACCENT = "#f28c00"
ACCENT_DARK = "#c97200"
TEXT = "#1d2733"
MUTED = "#6b7785"
BORDER = "#cfd6df"
FONT = "Segoe UI"


def center_window(win, width=None, height=None, parent=None):
    """Center a window on the screen, clamped to the visible area."""
    win.update_idletasks()
    screen_w = win.winfo_screenwidth()
    screen_h = win.winfo_screenheight()
    width = width or win.winfo_width()
    height = height or win.winfo_height()
    if width <= 1 or height <= 1:
        width, height = win.winfo_reqwidth(), win.winfo_reqheight()
    width = min(width, screen_w - 80)
    height = min(height, screen_h - 120)
    x = (screen_w - width) // 2
    y = max((screen_h - height) // 2 - 20, 0)
    win.geometry(f"{width}x{height}+{x}+{y}")


def _install_auto_center():
    if getattr(tk.Toplevel, "_tooltrack_centered", False):
        return
    original_init = tk.Toplevel.__init__
    original_geometry = tk.Wm.wm_geometry

    def remembering_geometry(self, newGeometry=None):
        if newGeometry and "x" in newGeometry:
            size = newGeometry.split("+")[0]
            try:
                self._requested_size = tuple(int(v) for v in size.split("x"))
            except ValueError:
                pass
        return original_geometry(self, newGeometry)

    tk.Wm.wm_geometry = remembering_geometry
    tk.Wm.geometry = remembering_geometry

    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.after_idle(lambda: _center_new_window(self))

    tk.Toplevel.__init__ = patched_init
    tk.Toplevel._tooltrack_centered = True


def _center_new_window(win):
    try:
        if not win.winfo_exists():
            return
        win.update_idletasks()
        width, height = getattr(win, "_requested_size", (1, 1))
        if width <= 1 or height <= 1:
            width, height = win.winfo_reqwidth(), win.winfo_reqheight()
        center_window(win, max(width, win.winfo_reqwidth()), max(height, win.winfo_reqheight()))
    except tk.TclError:
        pass


def apply_theme(root):
    _install_auto_center()
    root.configure(bg=BG)
    root.option_add("*Toplevel.background", BG)
    root.option_add("*Frame.background", BG)
    root.option_add("*Label.background", BG)
    root.option_add("*Label.foreground", TEXT)
    root.option_add("*Label.font", (FONT, 10))
    root.option_add("*Entry.font", (FONT, 10))
    root.option_add("*Entry.relief", "solid")
    root.option_add("*Entry.borderWidth", 1)
    root.option_add("*Button.background", NAVY_LIGHT)
    root.option_add("*Button.foreground", "white")
    root.option_add("*Button.activeBackground", ACCENT)
    root.option_add("*Button.activeForeground", "white")
    root.option_add("*Button.relief", "flat")
    root.option_add("*Button.font", (FONT, 10, "bold"))
    root.option_add("*Button.padX", 14)
    root.option_add("*Button.padY", 6)
    root.option_add("*Button.cursor", "hand2")

    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=BG, foreground=TEXT, font=(FONT, 10))
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("TLabelframe", background=BG, bordercolor=BORDER, relief="solid")
    style.configure("TLabelframe.Label", background=BG, foreground=NAVY, font=(FONT, 10, "bold"))
    style.configure(
        "TButton", background=NAVY_LIGHT, foreground="white", padding=(14, 7),
        borderwidth=0, focusthickness=0, font=(FONT, 10, "bold"),
    )
    style.map(
        "TButton",
        background=[("disabled", "#b9c1cc"), ("active", ACCENT), ("pressed", ACCENT_DARK)],
        foreground=[("disabled", "#eef1f5")],
    )
    style.configure("Accent.TButton", background=ACCENT, foreground="white", font=(FONT, 11, "bold"))
    style.map("Accent.TButton", background=[("disabled", "#e5c79a"), ("active", ACCENT_DARK)])
    style.configure("TEntry", padding=5, fieldbackground="white", bordercolor=BORDER)
    style.configure("TCombobox", padding=5, fieldbackground="white", bordercolor=BORDER)
    style.configure("TNotebook", background=BG, borderwidth=0)
    style.configure("TNotebook.Tab", padding=(18, 8), font=(FONT, 10, "bold"), background="#dde3ea")
    style.map(
        "TNotebook.Tab",
        background=[("selected", NAVY), ("active", "#c4ccd6")],
        foreground=[("selected", "white")],
    )
    style.configure(
        "Treeview", background="white", fieldbackground="white", rowheight=28,
        bordercolor=BORDER, font=(FONT, 10),
    )
    style.configure(
        "Treeview.Heading", background=NAVY, foreground="white",
        font=(FONT, 10, "bold"), padding=(6, 7), relief="flat",
    )
    style.map("Treeview.Heading", background=[("active", NAVY_LIGHT)])
    style.map("Treeview", background=[("selected", ACCENT)], foreground=[("selected", "white")])
    style.configure("Comparison.Treeview", rowheight=30, font=(FONT, 11))
    style.configure("Comparison.Treeview.Heading", font=(FONT, 11, "bold"))
