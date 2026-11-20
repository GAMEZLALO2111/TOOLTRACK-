"""Standard turret designer.

Given the stations of a machine and the programs of the parts that will run on it, it proposes which tool
number goes in which station so the programmers edit as few .stp lines as possible when they build the
standard in Radan. It only proposes: nothing is applied to a machine or to a program.

- A tool is a TN. TNs of series 1-4 that are within the dimensional tolerance of each other can be served by one
  tool (352x252 for 350x250), so they share a station.
- A part that uses the same TN twice (different angles) needs two stations.
- A station holds one tool. Sizes A-E must match; a size A tool may use a size B station with an adapter.
- The best assignment keeps the most parts running the tool where their .stp already has it (fewest edits)
  after covering as many part uses as the stations allow. A station label keeps its `s` suffix.
"""
from collections import Counter, defaultdict
from decimal import Decimal

from tn_tolerance import DEFAULT_TOLERANCE, describe_difference, parse_tn, tn_compatible

USE_WEIGHT = 1000
VOTE_WEIGHT = 10
ADAPTER_PENALTY = 1.0
UNKNOWN_SIZE_PENALTY = 0.5
MAX_SUGGESTION_STEPS = 6
MAX_CANDIDATES = 6


def base_station(label):
    text = (label or "").strip()
    return text[:-1] if text.lower().endswith("s") else text


def _suffix(label):
    text = (label or "").strip()
    return text[-1] if text.lower().endswith("s") else ""


def _cluster_tns(usage, tolerance):
    """TN -> representative TN. The representative is within tolerance of each member it covers."""
    representative = {}
    ordered = sorted(usage, key=lambda tn: (-usage[tn], tn))
    for tn in ordered:
        if tn in representative:
            continue
        representative[tn] = tn
        if not parse_tn(tn):
            continue
        for other in ordered:
            if other not in representative and parse_tn(other) and tn_compatible(tn, other, tolerance):
                representative[other] = tn
    return representative


def _hungarian(cost):
    """Minimum cost assignment of every row to a different column (rows <= columns). Returns row -> column."""
    rows, cols = len(cost), len(cost[0]) if cost else 0
    inf = float("inf")
    u, v = [0.0] * (rows + 1), [0.0] * (cols + 1)
    match, way = [0] * (cols + 1), [0] * (cols + 1)
    for i in range(1, rows + 1):
        match[0] = i
        j0 = 0
        minimum = [inf] * (cols + 1)
        used = [False] * (cols + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = match[j0], inf, 0
            for j in range(1, cols + 1):
                if used[j]:
                    continue
                current = cost[i0 - 1][j - 1] - u[i0] - v[j]
                if current < minimum[j]:
                    minimum[j], way[j] = current, j0
                if minimum[j] < delta:
                    delta, j1 = minimum[j], j
            for j in range(cols + 1):
                if used[j]:
                    u[match[j]] += delta
                    v[j] -= delta
                else:
                    minimum[j] -= delta
            j0 = j1
            if match[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            match[j0] = match[j1]
            j0 = j1
    result = {}
    for j in range(1, cols + 1):
        if match[j]:
            result[match[j] - 1] = j - 1
    return result


def _compatibility(tool_size, station_size):
    """(allowed, penalty, adapter) of putting a tool of `tool_size` in a station of `station_size`."""
    if not tool_size:
        return True, UNKNOWN_SIZE_PENALTY, False
    if not station_size:
        return True, UNKNOWN_SIZE_PENALTY, False
    if tool_size == station_size:
        return True, 0.0, False
    if tool_size == "A" and station_size == "B":
        return True, ADAPTER_PENALTY, True
    return False, 0.0, False


def design(stations, parts, size_by_tn, tolerance=DEFAULT_TOLERANCE):
    """stations: [{number, size, autoindex}] available ones. parts: [{part, thickness, reqs:[{station, tn, angle}]}].
    size_by_tn: TN -> holder size letter or None."""
    stations = [dict(row, number=base_station(row["number"])) for row in stations]
    station_by_number = {row["number"]: row for row in stations}

    usage = Counter()
    for part in parts:
        for tn in {req["tn"] for req in part["reqs"]}:
            usage[tn] += 1
    representative = _cluster_tns(usage, tolerance)

    # Slots: one per tool that must exist at the same time. A part using a cluster twice needs two.
    multiplicity = Counter()
    part_slots = []
    for part in parts:
        by_cluster = defaultdict(list)
        for index, req in enumerate(part["reqs"]):
            by_cluster[representative[req["tn"]]].append(index)
        assignment = {}
        for cluster, indexes in by_cluster.items():
            ordered = sorted(indexes, key=lambda i: (part["reqs"][i].get("angle") or "", part["reqs"][i]["station"]))
            multiplicity[cluster] = max(multiplicity[cluster], len(ordered))
            for slot_number, i in enumerate(ordered):
                assignment[i] = (cluster, slot_number)
        part_slots.append(assignment)

    slots = [(cluster, k) for cluster in sorted(multiplicity) for k in range(multiplicity[cluster])]
    slot_index = {slot: i for i, slot in enumerate(slots)}
    uses = Counter()
    votes = defaultdict(Counter)
    parts_of_slot = defaultdict(set)
    for part, assignment in zip(parts, part_slots):
        for i, slot in assignment.items():
            uses[slot] += 1
            parts_of_slot[slot].add(part["part"])
            current = base_station(part["reqs"][i]["station"])
            if current in station_by_number:
                votes[slot][current] += 1

    def size_of(slot):
        members = [tn for tn, rep in representative.items() if rep == slot[0]]
        for tn in [slot[0]] + sorted(members):
            if size_by_tn.get(tn):
                return size_by_tn[tn].strip().upper()
        return None

    sizes = {slot: size_of(slot) for slot in slots}
    placement = {}
    if slots and stations:
        n, m = len(slots), len(stations)
        impossible = 1e9
        cost = []
        for slot in slots:
            row = []
            for station in stations:
                allowed, penalty, _adapter = _compatibility(sizes[slot], (station.get("size") or "").strip().upper() or None)
                if not allowed:
                    row.append(impossible)
                    continue
                profit = USE_WEIGHT * uses[slot] + VOTE_WEIGHT * votes[slot][station["number"]] - penalty
                row.append(-profit)
            row.extend([0.0] * n)  # a dummy column per slot: the tool stays out of the standard
            cost.append(row)
        for row_index, col in _hungarian(cost).items():
            if col < m and cost[row_index][col] < impossible / 2:
                placement[slots[row_index]] = stations[col]

    assignments, unplaced = [], []
    for slot in slots:
        cluster, k = slot
        members = sorted(tn for tn, rep in representative.items() if rep == cluster and tn != cluster)
        station = placement.get(slot)
        if station is None:
            unplaced.append({
                "tn": cluster, "also": members, "size": sizes[slot], "parts": sorted(parts_of_slot[slot]),
                "reason": "no_compatible_station" if not any(
                    _compatibility(sizes[slot], (s.get("size") or "").strip().upper() or None)[0] for s in stations
                ) else "no_free_station",
            })
            continue
        _allowed, _penalty, adapter = _compatibility(sizes[slot], (station.get("size") or "").strip().upper() or None)
        assignments.append({
            "station": station["number"], "station_size": station.get("size"), "autoindex": bool(station.get("autoindex")),
            "tn": cluster, "also": members, "tool_size": sizes[slot], "adapter": adapter,
            "size_known": sizes[slot] is not None, "parts": sorted(parts_of_slot[slot]),
            "kept_by": votes[slot][station["number"]], "of": uses[slot],
        })
    assignments.sort(key=lambda row: (not row["station"].isdigit(), int(row["station"]) if row["station"].isdigit() else 0, row["station"]))

    out_parts = []
    for part, assignment in zip(parts, part_slots):
        rows, changes = [], 0
        for i, req in enumerate(part["reqs"]):
            slot = assignment[i]
            station = placement.get(slot)
            current = req["station"]
            if station is None:
                rows.append({
                    "tn": req["tn"], "angle": req.get("angle"), "current_station": current, "new_station": None,
                    "changed": False, "off_standard": True, "substitute_tn": None, "difference": None,
                })
                continue
            new_label = station["number"] + _suffix(current)
            changed = base_station(current) != station["number"]
            changes += changed
            substitute = slot[0] if slot[0] != req["tn"] else None
            rows.append({
                "tn": req["tn"], "angle": req.get("angle"), "current_station": current, "new_station": new_label,
                "changed": changed, "off_standard": False, "substitute_tn": substitute,
                "difference": describe_difference(req["tn"], substitute) if substitute else None,
            })
        rows.sort(key=lambda row: (row["new_station"] is None, row["new_station"] or row["current_station"]))
        out_parts.append({
            "part": part["part"], "thickness": part.get("thickness"), "rows": rows, "changes": changes,
            "total": len(rows), "off_standard": sum(1 for row in rows if row["off_standard"]),
        })

    free = Counter(
        (s.get("size") or "?") for s in stations if s not in placement.values()
    )
    return {
        "stations_available": len(stations), "tools": len(slots), "placed": len(assignments),
        "unplaced": unplaced, "assignments": assignments, "parts": out_parts,
        "free_stations": dict(sorted(free.items())), "tool_merges": sum(
            1 for tn, rep in representative.items() if tn != rep
        ),
        "total_changes": sum(row["changes"] for row in out_parts),
        "total_rows": sum(row["total"] for row in out_parts),
    }


def suggest_removals(stations, parts, size_by_tn, tolerance=DEFAULT_TOLERANCE):
    """When tools do not fit: parts to leave out of the standard, picked one by one by how many unplaced tools
    each removal recovers (ties: the part with fewer tools)."""
    result = design(stations, parts, size_by_tn, tolerance)
    if not result["unplaced"]:
        return []
    remaining, steps = list(parts), []
    for _ in range(min(MAX_SUGGESTION_STEPS, len(parts) - 1)):
        # Only the parts with the most tools of their own can recover many stations; trying them all is slow.
        owners = Counter(req["tn"] for part in remaining for req in part["reqs"])
        ranked = sorted(
            remaining, key=lambda p: -sum(1 for req in p["reqs"] if owners[req["tn"]] == 1),
        )[:MAX_CANDIDATES]
        best = None
        for candidate in ranked:
            trial = [p for p in remaining if p is not candidate]
            outcome = design(stations, trial, size_by_tn, tolerance)
            score = (len(outcome["unplaced"]), len(candidate["reqs"]))
            if best is None or score < best[0]:
                best = (score, candidate, outcome)
        _score, candidate, outcome = best
        remaining.remove(candidate)
        steps.append({
            "remove": candidate["part"], "unplaced_after": len(outcome["unplaced"]),
            "parts_left": len(remaining),
        })
        if not outcome["unplaced"]:
            break
    return steps
