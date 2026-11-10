"""Importador controlado del inventario B1 a TOOLTRACK.

Uso recomendado:
1) Cerrar clientes y backend.
2) Copiar este script a la carpeta donde están tooltrack.db y el Excel original.
3) Ejecutar primero: python importar_inventario_b1_tooltrack.py --dry-run
4) Revisar el reporte generado.
5) Ejecutar: python importar_inventario_b1_tooltrack.py --commit

El script crea un respaldo automático antes de escribir y nunca sobrescribe IDs existentes.
"""
from __future__ import annotations
import argparse
import csv
import re
import shutil
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from openpyxl import load_workbook

DEFAULT_EXCEL = "F-BM-ING-16-02 REV A INVENTARIO DE HERRAMIENTA.xlsx"
DEFAULT_DB = "tooltrack.db"
SHEETS = {
    "INVENTARIO B1": 4,
    "PERLA MONICA": 1,
    "MICHEL B1": 1,
    "JAIME": 1,
}

NULLS = {"", "N/A", "NA", "N/P", "S/N", "NONE", "NULL", "XXX", "XXXX", "*", "-"}


def clean(value):
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).strip()
    return None if text.upper() in NULLS else text


def clean_id(value):
    text = clean(value)
    if not text:
        return None
    # Excel puede convertir códigos numéricos a 123456.0.
    text = re.sub(r"\.0$", "", text)
    return text.upper()


def normalize_category(value):
    text = (clean(value) or "").upper()
    if text in {"P", "PUNCH", "PUNZON", "PUNZÓN"}:
        return "punzon"
    if text in {"D", "DIE", "MATRIZ"}:
        return "matriz"
    return text.lower() or None


def normalize_type(value):
    text = (clean(value) or "").upper()
    aliases = {
        "ROUND": "REDONDO", "RD": "REDONDO",
        "SQUARE": "CUADRADO", "SQ": "CUADRADO",
        "RECTANGULAR": "RECTANGULAR", "RECTANGLE": "RECTANGULAR", "RECT": "RECTANGULAR",
        "OBROUND": "OBLONGO", "OBLONG": "OBLONGO",
    }
    return aliases.get(text, text) or None


def normalize_number(value):
    text = clean(value)
    if not text:
        return None
    return text.replace(",", ".")


def row_is_tool(values):
    loc = clean(values[0] if len(values) > 0 else None)
    physical_id = clean_id(values[1] if len(values) > 1 else None)
    tn = clean(values[8] if len(values) > 8 else None)
    return bool(loc or physical_id or tn)


def read_rows(excel_path: Path):
    wb = load_workbook(excel_path, read_only=True, data_only=True)
    records = []
    for sheet, header_row in SHEETS.items():
        if sheet not in wb.sheetnames:
            continue
        ws = wb[sheet]
        for source_row, raw in enumerate(
            ws.iter_rows(min_row=header_row + 1, max_col=20, values_only=True),
            start=header_row + 1,
        ):
            values = list(raw)
            if not row_is_tool(values):
                continue
            first = " ".join(str(v).upper() for v in values[:4] if v not in (None, ""))
            if "LOCALIDAD" in first or first.strip() == "INVENTARIO":
                continue
            physical_id = clean_id(values[1])
            records.append({
                "id": physical_id,
                "tn": clean(values[8]),
                "size": (clean(values[3]) or "").upper() or None,
                "type": normalize_type(values[5]),
                "life": normalize_number(values[13]),
                "clearance": normalize_number(values[10]),
                "so": clean(values[9]),
                "proveedor": (clean(values[4]) or "").upper() or None,
                "categoria": normalize_category(values[2]),
                "ubicacion": (clean(values[0]) or "").upper() or None,
                "estado": "disponible",
                "source_sheet": sheet,
                "source_row": source_row,
            })
    return records


def database_columns(con):
    return [r[1] for r in con.execute("PRAGMA table_info(herramientas)")]


def main():
    ap = argparse.ArgumentParser(description="Importa el inventario B1 a TOOLTRACK")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Analiza sin modificar la base")
    mode.add_argument("--commit", action="store_true", help="Crea respaldo e importa")
    ap.add_argument("--excel", default=DEFAULT_EXCEL)
    ap.add_argument("--db", default=DEFAULT_DB)
    args = ap.parse_args()

    excel = Path(args.excel).resolve()
    db = Path(args.db).resolve()
    if not excel.exists():
        sys.exit(f"No se encontró el Excel: {excel}")
    if not db.exists():
        sys.exit(f"No se encontró la base: {db}")

    records = read_rows(excel)
    id_counts = Counter(r["id"] for r in records if r["id"])
    con = sqlite3.connect(db)
    cols = database_columns(con)
    required = {"id", "tn", "size", "type", "life", "clearance", "so", "categoria", "ubicacion", "estado"}
    missing_cols = required - set(cols)
    if missing_cols:
        con.close()
        sys.exit("Faltan columnas en herramientas: " + ", ".join(sorted(missing_cols)))

    existing = {str(r[0]).strip().upper() for r in con.execute("SELECT id FROM herramientas")}
    accepted, rejected = [], []
    for r in records:
        reason = None
        if not r["id"]:
            reason = "SIN 6 DIGIT"
        elif id_counts[r["id"]] > 1:
            reason = "6 DIGIT DUPLICADO EN EXCEL"
        elif r["id"] in existing:
            reason = "YA EXISTE EN TOOLTRACK"
        elif not r["tn"]:
            reason = "SIN TOOL NUMBER"
        elif not r["categoria"]:
            reason = "SIN PUNZON/MATRIZ"
        if reason:
            rejected.append((r, reason))
        else:
            accepted.append(r)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    report = Path(f"reporte_importacion_b1_{stamp}.csv")
    with report.open("w", newline="", encoding="utf-8-sig") as f:
        fields = ["resultado", "motivo", "id", "tn", "size", "type", "life", "clearance", "so", "proveedor", "categoria", "ubicacion", "source_sheet", "source_row"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in accepted:
            w.writerow({"resultado": "LISTO", "motivo": "", **{k: r.get(k) for k in fields if k not in {"resultado", "motivo"}}})
        for r, reason in rejected:
            w.writerow({"resultado": "OMITIDO", "motivo": reason, **{k: r.get(k) for k in fields if k not in {"resultado", "motivo"}}})

    print(f"Registros leídos: {len(records)}")
    print(f"Listos para importar: {len(accepted)}")
    print(f"Omitidos para revisión: {len(rejected)}")
    print(f"Reporte: {report.resolve()}")

    if args.dry_run:
        con.close()
        print("DRY RUN: no se modificó tooltrack.db")
        return

    backup = db.with_name(f"tooltrack.backup-antes-importacion-b1-{stamp}.db")
    shutil.copy2(db, backup)
    insert_cols = [c for c in ["id", "tn", "size", "type", "life", "clearance", "so", "proveedor", "categoria", "ubicacion", "estado"] if c in cols]
    sql = f"INSERT INTO herramientas ({','.join(insert_cols)}) VALUES ({','.join('?' for _ in insert_cols)})"
    try:
        con.execute("BEGIN")
        con.executemany(sql, [[r[c] for c in insert_cols] for r in accepted])
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    print(f"Respaldo: {backup.resolve()}")
    print(f"IMPORTACIÓN COMPLETADA: {len(accepted)} herramientas")


if __name__ == "__main__":
    main()
