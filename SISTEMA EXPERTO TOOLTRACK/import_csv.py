import csv
from tkinter import filedialog, messagebox
import requests
API_URL = "http://127.0.0.1:8000/herramientas/"

def import_inventory_from_csv():
    file_path = filedialog.askopenfilename(filetypes=[("CSV files", "*.csv")])
    if not file_path:
        return
    try:
        with open(file_path, mode='r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                data = {
                    "id": row.get("ID", ""),
                    "tn": row.get("TN", ""),
                    "size": row.get("SIZE", ""),
                    "type": row.get("TYPE", ""),
                    "life": row.get("LIFE", ""),
                    "clearance": row.get("CLEARANCE", ""),
                    "so": row.get("SO", ""),
                    "categoria": row.get("CATEGORY", ""),
                    "ubicacion": row.get("UBICACION", "")
                }
                if data["id"]:
                    requests.post(API_URL, json=data)
        messagebox.showinfo("Import", f"Inventory imported from {file_path}")
    except Exception as e:
        messagebox.showerror("Import", f"Could not import: {e}")
