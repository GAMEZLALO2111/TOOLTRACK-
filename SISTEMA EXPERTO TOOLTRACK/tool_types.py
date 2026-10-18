PUNCH_DIE_TYPES = (
    "ROUND",
    "SQUARE",
    "RECTANGULAR",
    "OVAL",
    "HEXAGONAL",
    "SPECIAL",
    "FORMED",
    "1 RADIUS",
    "2 RADII",
    "4 RADII",
)

HOLDER_TYPES = tuple(f"HOLDER {size}" for size in "ABCDE")

ACCESSORY_TYPES = (
    "FLAT DIE A",
    "FLAT DIE B",
    "FLAT DIE C",
    "FLAT DIE D",
    "FLAT DIE E",
    "FLAT PUNCH A",
    "FLAT PUNCH B",
    "FLAT PUNCH C",
    "FLAT PUNCH D",
    "FLAT PUNCH E",
    "SLIDER DIE A",
    "SLIDER DIE B",
    "ADAPTER A-B",
    "ADAPTER B-E",
    "ADAPTER C-E",
    "ADAPTER D-E",
    "STAMPING PUNCH B",
    "STAMPING PUNCH C",
    "STAMPING PUNCH D",
    "STAMPING DIE B",
    "STAMPING DIE C",
    "STAMPING DIE D",
)

INVENTORY_TYPES_BY_CATEGORY = {
    "punzon": PUNCH_DIE_TYPES,
    "matriz": PUNCH_DIE_TYPES,
    "holder": HOLDER_TYPES,
    "accesorio": ACCESSORY_TYPES,
}

ALL_INVENTORY_TYPES = frozenset(
    type_
    for category_types in INVENTORY_TYPES_BY_CATEGORY.values()
    for type_ in category_types
)
