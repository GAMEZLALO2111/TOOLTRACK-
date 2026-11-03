"""Lista de herramientas a surtir a partir de la comparación de dos setups (fuente única, con códigos)."""


def build_pick_items(comparison):
    new_setup = comparison["new_setup"]
    clearance_min = new_setup.get("clearance_min")
    clearance_max = new_setup.get("clearance_max")
    clearance = (
        f"{clearance_min}–{clearance_max} in"
        if clearance_min is not None and clearance_max is not None
        else None
    )
    items = []

    for row in comparison["added"]:
        for category in ("punch", "die"):
            items.append({
                "kind": "supply",
                "station": row["station"] or "—",
                "tn": row["tn"],
                "angle": row["angle"] or "—",
                "category": category,
                "clearance": clearance if category == "die" else None,
                "action": "supply",
            })

    for row in comparison["unchanged"]:
        if row["die_action"] not in {"verify_clearance", "manual_die_selection"}:
            continue
        items.append({
            "kind": "verify",
            "station": row["station"] or "—",
            "tn": row["tn"],
            "angle": row["angle"] or "—",
            "category": "die",
            "clearance": clearance,
            "action": (
                "verify_clearance" if row["die_action"] == "verify_clearance" else "confirm_with_engineering"
            ),
        })

    items.sort(key=lambda item: (str(item["station"]), item["tn"], item["category"]))
    return items
