"""Weekly capacity check: do the machines cover the demand?

Pure calculation (no database) so it can be tested on its own.

Method, week by week:
1. What each make item still needs this week is its net demand plus what was left late from earlier weeks.
2. It is placed on compatible machines (type with a program, thickness inside the machine range when both
   are known), fastest machine type first, filling the machine with most free hours. Each machine used for a
   part pays one fixed setup.
3. What does not fit stays late and moves to the next week.
4. Free hours left in the week pull demand forward from the next `ahead` weeks.
A week fails when something is still late at its end, it is tight above 90% use.
"""
from collections import defaultdict

TIGHT = 0.9
EPS = 1e-9


def _hours_per_piece(machine, info):
    hpp = info["hpp"].get(machine["type"])
    if not hpp:
        return None
    thickness = info.get("thickness")
    low, high = machine.get("thickness_min"), machine.get("thickness_max")
    if thickness is not None and low is not None and high is not None and not (low - EPS <= thickness <= high + EPS):
        return None
    return hpp


def _place(part, pieces, remaining, setups, machines, parts, setup_h, loads, log=None, week=None, prebuilt=False):
    """Puts up to `pieces` of a part on the machines with free hours. Returns (pieces placed, hours used)."""
    options = []
    for machine in machines:
        hpp = _hours_per_piece(machine, parts[part])
        if hpp:
            options.append((hpp, -remaining[machine["code"]], machine["code"]))
    options.sort()
    left, hours = pieces, 0.0
    for hpp, _free, code in options:
        if left <= EPS:
            break
        extra = 0.0 if (code, part) in setups else setup_h
        free = remaining[code] - extra
        if free <= EPS:
            continue
        take = min(left, free / hpp)
        if take < 1 and left >= 1:
            continue
        spent = extra + take * hpp
        remaining[code] -= spent
        setups.add((code, part))
        loads[code] += spent
        if log is not None:
            log.append({"week": week, "machine": code, "part": part, "pieces": take, "hours": take * hpp,
                        "setup_h": extra, "prebuilt": prebuilt})
        left -= take
        hours += spent
    return pieces - left, hours


def simulate(weeks, machines, parts, need, setup_h, ahead):
    """`machines`: code, type, cap (free hours per week), thickness_min/max.
    `parts`: part -> {hpp: {machine type: hours per piece}, thickness}.
    `need`: part -> net pieces per week (a list of `weeks` numbers). Returns per-week and per-part results."""
    need = {part: list(values) for part, values in need.items()}
    late = defaultdict(float)
    week_rows, part_rows = [], {part: [] for part in need}
    order_key = {
        part: sum(1 for m in machines if _hours_per_piece(m, parts[part])) for part in need
    }
    best_hpp = {part: min(parts[part]["hpp"].values()) if parts[part]["hpp"] else None for part in need}
    machine_loads = {m["code"]: [] for m in machines}
    placements = []
    for w in range(weeks):
        remaining = {m["code"]: max(m["cap"][w], 0.0) for m in machines}
        gross = sum(remaining.values())
        setups, loads = set(), defaultdict(float)
        worked = sorted(need, key=lambda p: (order_key[p], -(late[p] + need[p][w])))
        used = 0.0
        entries = {}
        for part in worked:
            wanted = late[part] + need[part][w]
            if wanted <= EPS:
                entries[part] = {"need": 0.0, "assigned": 0.0}
                continue
            if not parts[part]["hpp"]:
                late[part] = wanted
                entries[part] = {"need": wanted, "assigned": 0.0}
                continue
            placed, hours = _place(part, wanted, remaining, setups, machines, parts, setup_h, loads, placements, w, False)
            used += hours
            late[part] = wanted - placed
            entries[part] = {"need": wanted, "assigned": placed}
        prebuilt = 0.0
        for k in range(1, ahead + 1):
            if w + k >= weeks or sum(remaining.values()) <= EPS:
                break
            for part in worked:
                future = need[part][w + k]
                if future <= EPS or not parts[part]["hpp"]:
                    continue
                placed, hours = _place(part, future, remaining, setups, machines, parts, setup_h, loads, placements, w, True)
                need[part][w + k] = future - placed
                prebuilt += hours
                used += hours
        late_hours = sum(late[p] * best_hpp[p] for p in need if best_hpp[p] and late[p] > EPS)
        unplanned = sum(late[p] for p in need if not parts[p]["hpp"])
        status = "no_cumple" if late_hours > EPS else ("justo" if gross > EPS and used / gross >= TIGHT else "cumple")
        week_rows.append({
            "capacity_h": round(gross, 1), "load_h": round(used - prebuilt, 1), "prebuilt_h": round(prebuilt, 1),
            "utilization": round(used / gross, 3) if gross > EPS else None,
            "late_h": round(late_hours, 1), "late_pieces": round(sum(late[p] for p in need if best_hpp[p])),
            "unplanned_pieces": round(unplanned), "status": status,
            "machines_used": sum(1 for value in loads.values() if value > EPS),
        })
        for part in need:
            entry = entries.get(part, {"need": 0.0, "assigned": 0.0})
            part_rows[part].append({
                "need": round(entry["need"]), "assigned": round(entry["assigned"]), "late": round(late[part]),
            })
        for machine in machines:
            machine_loads[machine["code"]].append(round(loads.get(machine["code"], 0.0), 1))
    return {"weeks": week_rows, "parts": part_rows, "machine_loads": machine_loads, "placements": placements,
            "late": {part: round(value) for part, value in late.items() if value > EPS}}


def net_need(weekly_demand, offset):
    """Net weekly demand of one make item.

    `weekly_demand`: gross pieces per week; `offset`: pieces already covered (produced this week and before
    the cut-off, and still pending on the Board). The cover is used against the earliest weeks first, so an
    excess carries into the next weeks."""
    result, total, previous = [], 0.0, 0.0
    for value in weekly_demand:
        total += value
        net_total = max(0.0, total - offset)
        result.append(net_total - previous)
        previous = net_total
    return result
