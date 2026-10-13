import datetime
import csv
import os

AUDIT_FILE = "audit_log.csv"

def log_audit(action, user, tool_id, details):
    now = datetime.datetime.now().isoformat(sep=' ', timespec='seconds')
    exists = os.path.isfile(AUDIT_FILE)
    # Si el detalle es un dict, formatear para mostrar cambios de ubicacion explícitamente
    if isinstance(details, dict):
        ubicacion = details.get('ubicacion', None)
        if ubicacion is not None:
            details_str = f"{details} (Ubicacion: {ubicacion})"
        else:
            details_str = str(details)
    else:
        details_str = str(details)
    with open(AUDIT_FILE, mode='a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        if not exists:
            writer.writerow(["Timestamp", "User", "Action", "ToolID", "Details"])
        writer.writerow([now, user, action, tool_id, details_str])

def show_audit_log():
    if not os.path.isfile(AUDIT_FILE):
        print("No audit log found.")
        return
    with open(AUDIT_FILE, mode='r', encoding='utf-8') as f:
        for line in f:
            print(line.strip())
