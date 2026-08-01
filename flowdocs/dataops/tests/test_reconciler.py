import unittest

from flowdocs.dataops.reconciler import AutoHealBudget, choose_repairs


class AutoHealDecisionTests(unittest.TestCase):
    def test_safe_repairs_are_bounded_and_approval_is_not_auto_run(self):
        budget = AutoHealBudget(per_run=3, per_day=3)
        decisions = choose_repairs(
            [
                {"id": "stale-1", "condition": "refresh_observation", "count": 2},
                {"id": "missing", "condition": "reindex_documents", "count": 3},
                {"id": "candidate", "condition": "activate_staging", "count": 1},
            ],
            budget,
        )
        self.assertEqual([item.count for item in decisions[:2]], [2, 1])
        self.assertTrue(decisions[2].requires_approval)
        self.assertEqual(budget.used_day, 3)

    def test_unknown_condition_stops_without_mutation(self):
        decisions = choose_repairs([{"condition": "database_corruption"}], AutoHealBudget(5, 5))
        self.assertEqual(decisions[0].reason, "unknown_condition_stopped")
        self.assertEqual(decisions[0].count, 0)


if __name__ == "__main__":
    unittest.main()
