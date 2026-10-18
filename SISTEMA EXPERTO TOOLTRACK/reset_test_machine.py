"""Test helper: undo the last delivered kit of a machine.

Usage:  python reset_test_machine.py T-107 [--db tooltrack.db]

Returns the delivered tools to the racks they came from, restores the previous
setup as the machine's active setup, and records the moves in the history.
A backup copy of the database is saved next to it before any change.
"""
import argparse
import shutil
import sqlite3
from datetime import datetime


def reset_machine(db_path, machine):
    conn = sqlite3.connect(db_path)
    try:
        kit = conn.execute(
            "SELECT id, setup_anterior_id, setup_nuevo_id FROM surtidos "
            "WHERE maquina_codigo=? AND estado='delivered' ORDER BY entregado_en DESC LIMIT 1",
            (machine,),
        ).fetchone()
        if not kit:
            raise SystemExit(f"{machine} no tiene surtidos entregados que deshacer.")
        kit_id, old_setup, new_setup = kit
        active = conn.execute(
            "SELECT setup_activo_id FROM estados_maquina WHERE maquina_codigo=?", (machine,)
        ).fetchone()
        if not active or active[0] != new_setup:
            raise SystemExit("El último surtido ya no es el setup activo de la máquina.")
        pending = conn.execute(
            "SELECT COUNT(*) FROM pendientes_recoger WHERE surtido_id=? AND estado='pending'",
            (kit_id,),
        ).fetchone()[0]
        if pending:
            raise SystemExit("Hay herramientas pendientes de recoger; resuélvelas primero.")

        now = datetime.utcnow().isoformat(sep=" ")
        moved = 0
        origins = conn.execute(
            "SELECT herramienta_id, origen_codigo FROM movimientos_herramienta "
            "WHERE surtido_id=? AND tipo='rack_to_cart_for_kit'",
            (kit_id,),
        ).fetchall()
        for tool_id, rack in origins:
            current = conn.execute(
                "SELECT ubicacion FROM herramientas WHERE id=?", (tool_id,)
            ).fetchone()
            if not current or current[0] != machine:
                continue
            conn.execute("UPDATE herramientas SET ubicacion=? WHERE id=?", (rack, tool_id))
            conn.execute(
                "INSERT INTO movimientos_herramienta (herramienta_id, origen_codigo, destino_codigo, "
                "tipo, surtido_id, setup_id, realizado_por, creado_en, detalle) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (tool_id, machine, rack, "test_reset_machine_to_rack", kit_id, new_setup,
                 "reset_test", now, "Test reset: tool returned to rack"),
            )
            moved += 1
        conn.execute(
            "UPDATE requisitos_setup SET punch_tool_id=NULL, die_tool_id=NULL WHERE setup_id=?",
            (new_setup,),
        )
        conn.execute("UPDATE setups SET estado='draft' WHERE id=?", (new_setup,))
        conn.execute("UPDATE setups SET estado='active' WHERE id=?", (old_setup,))
        conn.execute(
            "UPDATE estados_maquina SET setup_activo_id=? WHERE maquina_codigo=?",
            (old_setup, machine),
        )
        conn.execute("UPDATE surtidos SET estado='cancelled', actualizado_en=? WHERE id=?", (now, kit_id))
        conn.commit()
        return moved
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("machine")
    parser.add_argument("--db", default="tooltrack.db")
    args = parser.parse_args()
    machine = args.machine.strip().upper()
    shutil.copy2(args.db, f"{args.db}.bak-{datetime.now():%Y%m%d-%H%M%S}")
    print(f"{reset_machine(args.db, machine)} herramienta(s) regresadas a su rack en {machine}.")
