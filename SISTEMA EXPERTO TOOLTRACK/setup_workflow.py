import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional


THICKNESS_CLEARANCE_BANDS = (
    (Decimal("0.020"), Decimal("0.036"), Decimal("0.004"), Decimal("0.006")),
    (Decimal("0.036"), Decimal("0.048"), Decimal("0.006"), Decimal("0.008")),
    (Decimal("0.048"), Decimal("0.059"), Decimal("0.008"), Decimal("0.012")),
    (Decimal("0.059"), Decimal("0.074"), Decimal("0.012"), Decimal("0.014")),
    (Decimal("0.074"), Decimal("0.104"), Decimal("0.014"), Decimal("0.018")),
    (Decimal("0.104"), Decimal("0.120"), Decimal("0.018"), Decimal("0.022")),
)

STATION_PATTERN = re.compile(r"^\d+s?$", re.IGNORECASE)
NUMBER_PATTERN = re.compile(r"^\d+(?:\.\d+)?$")
FIELD_PATTERN = re.compile(r"^([A-Z][A-Z /]+?)\s*:\s*(.*?)\s*$", re.IGNORECASE)
THICKNESS_PATTERN = re.compile(
    r"^([0-9]+(?:\.[0-9]+)?)\s*in(?:ches?)?$",
    re.IGNORECASE,
)


class SetupParseError(ValueError):
    pass


class SetupReviewRequired(ValueError):
    pass


@dataclass(frozen=True)
class SetupRequirement:
    station: str
    tn: str
    angle: Optional[str]

    @property
    def key(self):
        return self.tn, self.angle


@dataclass(frozen=True)
class ParsedSetup:
    source_name: str
    drawing_name: Optional[str]
    material: Optional[str]
    thickness: Decimal
    requirements: tuple[SetupRequirement, ...]


def _normalize_decimal(value: str) -> str:
    number = Decimal(value)
    normalized = number.normalize()
    return format(normalized, "f")


def parse_setup_text(text: str, source_name: str = "") -> ParsedSetup:
    lines = text.splitlines()
    tool_list_start = next(
        (index for index, line in enumerate(lines) if line.strip().upper() == "TOOL LIST"),
        None,
    )
    if tool_list_start is None:
        raise SetupParseError("The setup file does not contain a TOOL LIST section.")

    extra_notes_start = next(
        (
            index
            for index in range(tool_list_start + 1, len(lines))
            if lines[index].strip().upper() == "EXTRA NOTES"
        ),
        None,
    )
    if extra_notes_start is None:
        raise SetupParseError("The setup file does not contain an EXTRA NOTES section.")

    fields = {}
    for line in lines[:tool_list_start]:
        match = FIELD_PATTERN.match(line.strip())
        if match:
            fields[match.group(1).strip().upper()] = match.group(2).strip()

    thickness_text = fields.get("THICKNESS", "")
    thickness_match = THICKNESS_PATTERN.match(thickness_text)
    if not thickness_match:
        raise SetupParseError(
            "THICKNESS must be provided in inches, for example '0.075 in'."
        )
    try:
        thickness = Decimal(thickness_match.group(1))
    except InvalidOperation as error:
        raise SetupParseError("THICKNESS is not a valid decimal value.") from error
    if not thickness.is_finite() or thickness <= 0:
        raise SetupParseError("THICKNESS must be a positive finite value.")

    requirements = []
    malformed_rows = []
    for line_number, line in enumerate(
        lines[tool_list_start + 1:extra_notes_start],
        start=tool_list_start + 2,
    ):
        parts = line.split()
        if not parts or not STATION_PATTERN.fullmatch(parts[0]):
            continue
        if len(parts) < 7:
            malformed_rows.append(line_number)
            continue
        tn = parts[-1]
        angle_text = parts[-4]
        if not NUMBER_PATTERN.fullmatch(tn):
            malformed_rows.append(line_number)
            continue
        if angle_text == "-":
            angle = None
        elif NUMBER_PATTERN.fullmatch(angle_text):
            angle = _normalize_decimal(angle_text)
        else:
            malformed_rows.append(line_number)
            continue
        requirements.append(SetupRequirement(
            station=parts[0],
            tn=tn,
            angle=angle,
        ))

    if malformed_rows:
        lines_text = ", ".join(str(number) for number in malformed_rows)
        raise SetupParseError(
            f"Could not parse tool row(s) on line(s): {lines_text}."
        )
    if not requirements:
        raise SetupParseError("No tool rows were found in the TOOL LIST section.")

    drawing_name = fields.get("DRAWING NAME") or None
    material = fields.get("MATERIAL") or None
    return ParsedSetup(
        source_name=source_name,
        drawing_name=drawing_name,
        material=material,
        thickness=thickness,
        requirements=tuple(requirements),
    )


def parse_setup_file(path: str) -> ParsedSetup:
    setup_path = Path(path)
    try:
        text = setup_path.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        raise SetupParseError(f"Could not read setup file '{setup_path.name}': {error}") from error
    return parse_setup_text(text, source_name=setup_path.name)


def validate_setup_requirements(setup: ParsedSetup) -> None:
    by_tn = {}
    for requirement in setup.requirements:
        by_tn.setdefault(requirement.tn, []).append(requirement)

    duplicates = []
    review_tns = []
    for tn, requirements in by_tn.items():
        if len(requirements) < 2:
            continue
        if any(requirement.angle is None for requirement in requirements):
            review_tns.append(tn)
            continue
        seen_angles = set()
        for requirement in requirements:
            if requirement.angle in seen_angles:
                duplicates.append((tn, requirement.angle))
            seen_angles.add(requirement.angle)

    if duplicates:
        summary = ", ".join(f"TN {tn} at {angle}°" for tn, angle in duplicates)
        raise SetupParseError(
            f"Duplicate TN at the same angle; correct the setup before continuing: {summary}."
        )
    if review_tns:
        summary = ", ".join(review_tns)
        raise SetupReviewRequired(
            f"Repeated TN without enough angle information requires manual review: {summary}."
        )


def clearance_range_for_thickness(thickness: Decimal):
    for index, (lower, upper, clearance_min, clearance_max) in enumerate(
        THICKNESS_CLEARANCE_BANDS
    ):
        is_last_band = index == len(THICKNESS_CLEARANCE_BANDS) - 1
        if lower <= thickness and (thickness < upper or (is_last_band and thickness == upper)):
            return clearance_min, clearance_max
    return None


def compare_setup_requirements(current: ParsedSetup, new: ParsedSetup):
    current_by_key = {requirement.key: requirement for requirement in current.requirements}
    new_by_key = {requirement.key: requirement for requirement in new.requirements}
    current_keys = set(current_by_key)
    new_keys = set(new_by_key)
    return {
        "removed": tuple(current_by_key[key] for key in sorted(current_keys - new_keys)),
        "added": tuple(new_by_key[key] for key in sorted(new_keys - current_keys)),
        "unchanged": tuple(new_by_key[key] for key in sorted(current_keys & new_keys)),
        "thickness_changed": current.thickness != new.thickness,
    }
