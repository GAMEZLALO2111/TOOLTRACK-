import csv
from tkinter import filedialog, messagebox

def export_inventory_to_csv(herramientas):
    file_path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV files", "*.csv")])
    if not file_path:
        return
    try:
        with open(file_path, mode='w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(["ID", "TN", "SIZE", "TYPE", "LIFE", "CLEARANCE", "SO", "CATEGORY", "UBICACION"])
            for h in herramientas:
                writer.writerow([
                    h.get('id',''), h.get('tn',''), h.get('size',''), h.get('type',''),
                    h.get('life',''), h.get('clearance',''), h.get('so',''), h.get('categoria',''), h.get('ubicacion','')
                ])
        messagebox.showinfo("Export", f"Inventory exported to {file_path}")
    except Exception as e:
        messagebox.showerror("Export", f"Could not export: {e}")
