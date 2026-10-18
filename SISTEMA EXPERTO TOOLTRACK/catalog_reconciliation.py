from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, Optional, Tuple


CATEGORY_ALIASES = {
    "punch": "punzon",
    "punzon": "punzon",
    "die": "matriz",
    "matriz": "matriz",
    "holder": "holder",
    "accesorio": "accesorio",
    "accessory": "accesorio",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_clearance(value: Any) -> Optional[str]:
    text = _text(value)
    if not text:
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    if not number.is_finite() or number < 0 or number.normalize().as_tuple().exponent < -3:
        return None
    return f"{number:.3f}"


def _legacy_identity(row: Dict[str, Any]) -> Tuple[Optional[Tuple[str, str, str, str, Optional[str]]], Optional[str]]:
    tool_id = _text(row.get("id"))
    tn = _text(row.get("tn"))
    size = _text(row.get("size")).upper()
    type_ = _text(row.get("type")).upper()
    category = CATEGORY_ALIASES.get(_text(row.get("categoria")).lower())
    clearance_value = _text(row.get("clearance"))

    if not tool_id:
        return None, "Missing physical tool ID."
    if not category:
        return None, "Category is not recognized."
    if category in {"holder", "accesorio"}:
        return None, "This category is handled outside the TN punch/die catalog."
    if not tn or not size or not type_:
        return None, "TN, SIZE and TYPE are required to identify a catalog configuration."
    if category == "punzon":
        if clearance_value:
            return None, "Legacy punch has CLEARANCE; category or source data must be reviewed."
        clearance = None
    else:
        clearance = _normalize_clearance(clearance_value)
        if clearance is None:
            return None, "Die CLEARANCE is missing or is not a valid non-negative value with at most three decimals."

    return (tn, size, type_, category, clearance), None


def preview_legacy_reconciliation(
    legacy_tools: Iterable[Dict[str, Any]],
    catalog_tns: Iterable[str],
    catalog_configurations: Iterable[Dict[str, Any]],
    catalog_variants: Iterable[Dict[str, Any]],
    catalog_links: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    legacy_tools = list(legacy_tools)
    tn_set = {_text(tn) for tn in catalog_tns}
    configs = {
        (_text(config.get("tn")), _text(config.get("size")).upper()): _text(config.get("type")).upper()
        for config in catalog_configurations
    }
    variants = {}
    variant_identity_by_id = {}
    for variant in catalog_variants:
        identity = (
            _text(variant.get("tn")),
            _text(variant.get("size")).upper(),
            _text(variant.get("categoria")).lower(),
            _normalize_clearance(variant.get("clearance")) if variant.get("clearance") is not None else None,
        )
        variants[identity] = variant.get("id")
        variant_identity_by_id[variant.get("id")] = identity
    linked_variants = {
        _text(link.get("herramienta_id")): link.get("variante_id")
        for link in catalog_links
    }

    types_by_configuration = defaultdict(set)
    normalized_rows = []
    for row in legacy_tools:
        identity, invalid_reason = _legacy_identity(row)
        normalized_rows.append((row, identity, invalid_reason))
        if identity:
            tn, size, type_, _category, _clearance = identity
            types_by_configuration[(tn, size)].add(type_)

    results = []
    for row, identity, invalid_reason in normalized_rows:
        tool_id = _text(row.get("id"))
        item = {
            "id": tool_id,
            "tn": _text(row.get("tn")),
            "size": _text(row.get("size")),
            "type": _text(row.get("type")),
            "categoria": _text(row.get("categoria")),
            "clearance": _text(row.get("clearance")),
            "status": "",
            "proposed_action": "",
            "detail": "",
        }
        if invalid_reason:
            if invalid_reason.startswith("This category is handled outside"):
                item.update(
                    status="out_of_scope",
                    proposed_action="none",
                    detail=invalid_reason,
                )
                results.append(item)
                continue
            item.update(
                status="requires_review",
                proposed_action="none",
                detail=invalid_reason,
            )
            results.append(item)
            continue

        tn, size, type_, category, clearance = identity
        config_key = (tn, size)
        variant_key = (tn, size, category, clearance)

        if len(types_by_configuration[config_key]) > 1:
            item.update(
                status="requires_review",
                proposed_action="none",
                detail="Legacy tools disagree on TYPE for the same TN and SIZE.",
            )
        elif config_key in configs and configs[config_key] != type_:
            item.update(
                status="requires_review",
                proposed_action="none",
                detail=f"Catalog TYPE is {configs[config_key]}, but legacy TYPE is {type_}.",
            )
        elif tool_id in linked_variants:
            linked_identity = variant_identity_by_id.get(linked_variants[tool_id])
            if linked_identity == variant_key and configs.get(config_key, type_) == type_:
                item.update(
                    status="already_linked",
                    proposed_action="none",
                    detail="Physical tool is already linked to the matching catalog variant.",
                )
            else:
                item.update(
                    status="requires_review",
                    proposed_action="none",
                    detail="Physical tool is already linked to a different or missing catalog variant.",
                )
        elif variant_key in variants and configs.get(config_key, type_) == type_:
            item.update(
                status="ready_to_link",
                proposed_action="link_to_existing_variant",
                detail=f"Would link physical tool to catalog variant {variants[variant_key]}.",
            )
        else:
            missing = []
            if tn not in tn_set:
                missing.append("TN")
            if config_key not in configs:
                missing.append("TN/SIZE configuration")
            if variant_key not in variants:
                missing.append("tool variant")
            item.update(
                status="catalog_entry_required",
                proposed_action="create_missing_catalog_entries_then_link",
                detail="Would require creating: " + ", ".join(missing) + ".",
            )
        results.append(item)

    counts = Counter(item["status"] for item in results)
    return {
        "read_only": True,
        "total": len(results),
        "summary": {
            "ready_to_link": counts["ready_to_link"],
            "catalog_entry_required": counts["catalog_entry_required"],
            "already_linked": counts["already_linked"],
            "requires_review": counts["requires_review"],
            "out_of_scope": counts["out_of_scope"],
        },
        "items": results,
    }
