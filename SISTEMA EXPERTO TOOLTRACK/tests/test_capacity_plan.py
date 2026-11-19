import unittest

from capacity_plan import net_need, simulate

WEEKS = 4


def machine(code="M1", kind="4I", hours=100.0, **extra):
    return {"code": code, "type": kind, "cap": [hours] * WEEKS, "thickness_min": None, "thickness_max": None, **extra}


def part(hpp=0.01, kind="4I", thickness=None):
    return {"hpp": {kind: hpp} if hpp else {}, "thickness": thickness}


class NetNeedTests(unittest.TestCase):
    def test_what_is_already_covered_is_used_against_the_earliest_weeks(self):
        self.assertEqual(net_need([100, 100, 100], 150), [0, 50, 100])
        self.assertEqual(net_need([100, 100, 100], 250), [0, 0, 50])
        self.assertEqual(net_need([100, 100], 0), [100, 100])
        self.assertEqual(net_need([100, 100], 999), [0, 0])


class SimulateTests(unittest.TestCase):
    def run_plan(self, machines, parts, need, setup_h=1.0, ahead=0):
        return simulate(WEEKS, machines, parts, need, setup_h, ahead)

    def test_a_demand_that_fits_is_met(self):
        result = self.run_plan([machine()], {"A": part()}, {"A": [5000, 0, 0, 0]})
        week = result["weeks"][0]
        self.assertEqual((week["status"], week["late_pieces"], week["load_h"]), ("cumple", 0, 51.0))
        self.assertEqual(result["parts"]["A"][0], {"need": 5000, "assigned": 5000, "late": 0})

    def test_what_does_not_fit_stays_late_and_moves_to_the_next_week(self):
        result = self.run_plan([machine()], {"A": part()}, {"A": [20000, 0, 0, 0]})
        statuses = [week["status"] for week in result["weeks"]]
        self.assertEqual(statuses, ["no_cumple", "no_cumple", "cumple", "cumple"])
        self.assertEqual([row["late"] for row in result["parts"]["A"]], [10100, 200, 0, 0])

    def test_free_hours_pull_demand_forward_up_to_the_limit(self):
        result = self.run_plan([machine()], {"A": part()}, {"A": [0, 8000, 0, 0]}, ahead=1)
        self.assertEqual(result["weeks"][0]["prebuilt_h"], 81.0)
        self.assertEqual(result["parts"]["A"][1]["need"], 0)
        none = self.run_plan([machine()], {"A": part()}, {"A": [0, 8000, 0, 0]}, ahead=0)
        self.assertEqual(none["weeks"][0]["prebuilt_h"], 0)
        self.assertEqual(none["parts"]["A"][1]["assigned"], 8000)

    def test_a_tight_week_is_flagged_and_the_setup_counts(self):
        result = self.run_plan([machine()], {"A": part(), "B": part()}, {"A": [4500, 0, 0, 0], "B": [4500, 0, 0, 0]})
        week = result["weeks"][0]
        self.assertEqual(week["load_h"], 92.0)
        self.assertEqual(week["status"], "justo")

    def test_a_machine_whose_thickness_range_excludes_the_part_cannot_take_it(self):
        narrow = machine(thickness_min=0.036, thickness_max=0.150)
        result = self.run_plan([narrow], {"A": part(thickness=0.2)}, {"A": [100, 0, 0, 0]})
        self.assertEqual(result["weeks"][0]["status"], "no_cumple")
        unknown_range = self.run_plan([machine()], {"A": part(thickness=0.2)}, {"A": [100, 0, 0, 0]})
        self.assertEqual(unknown_range["weeks"][0]["status"], "cumple")

    def test_the_same_part_runs_faster_on_the_machine_type_that_suits_it_and_splits_when_full(self):
        machines = [machine("F1", "4I", 10.0), machine("S1", "2I", 100.0)]
        info = {"A": {"hpp": {"4I": 0.01, "2I": 0.02}, "thickness": None}}
        result = self.run_plan(machines, info, {"A": [2000, 0, 0, 0]}, setup_h=0.0)
        self.assertEqual(result["parts"]["A"][0]["assigned"], 2000)
        self.assertEqual(result["machine_loads"]["F1"][0], 10.0)
        self.assertEqual(result["machine_loads"]["S1"][0], 20.0)

    def test_a_part_without_program_is_reported_as_unplanned_and_not_as_late_hours(self):
        result = self.run_plan([machine()], {"A": part(hpp=None)}, {"A": [300, 0, 0, 0]})
        week = result["weeks"][0]
        self.assertEqual((week["unplanned_pieces"], week["late_h"], week["status"]), (300, 0, "cumple"))


if __name__ == "__main__":
    unittest.main()
