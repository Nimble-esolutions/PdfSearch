"""Fast, dependency-free contract tests for the Data Operations cutover.

These tests intentionally exercise policy vectors rather than Django models so
they remain useful while the new control plane is built in parallel.
"""

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[3]
VECTORS = Path(__file__).with_name("contracts.json")


def resolve(env, db, default):
    """Return (value, source) using the ENV → DB → default policy."""
    if env not in (None, ""):
        return env, "env"
    if db not in (None, ""):
        return db, "db"
    return default, "default"


def consume_budget(requested, *, per_run, per_day, used_run, used_day):
    """Consume the bounded amount and report whether a request is accepted."""
    remaining = min(per_run - used_run, per_day - used_day)
    accepted = max(0, min(requested, remaining))
    return accepted, accepted == requested


class DataOpsEnvironmentContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vectors = json.loads(VECTORS.read_text())

    def test_environment_always_wins_over_database_fallback(self):
        for vector in self.vectors["env_precedence"]:
            self.assertEqual(
                resolve(vector["env"], vector["db"], vector["default"]),
                (vector["effective"], vector["source"]),
            )

    def test_legacy_vault_names_are_rejected_by_hard_cutover_contract(self):
        for prefix in self.vectors["legacy_prefixes"]:
            self.assertNotEqual(prefix, self.vectors["required_env_prefix"])
            self.assertTrue(prefix.endswith("_"))

    def test_contract_docs_are_present_and_do_not_publish_secret_values(self):
        env_doc = ROOT / "docs" / "dataops" / "ENV_CONTRACT.md"
        heal_doc = ROOT / "docs" / "dataops" / "AUTO_HEAL_CONTRACT.md"
        self.assertTrue(env_doc.is_file())
        self.assertTrue(heal_doc.is_file())
        text = env_doc.read_text() + heal_doc.read_text()
        self.assertIn("ENV → DB → default", text)
        self.assertIn("never log", text.lower())
        self.assertNotIn("AKIA", text)


class BoundedAutoHealContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.budget = json.loads(VECTORS.read_text())["auto_heal_budget"]

    def test_reindex_budget_is_hard_per_run_and_per_day_cap(self):
        used_day = 0
        accepted = []
        denied = []
        for requested in self.budget["requests"]:
            amount, complete = consume_budget(
                requested,
                per_run=self.budget["per_run"],
                per_day=self.budget["per_day"],
                # The per-run counter is reset for each independent reconciler
                # invocation; the daily counter is shared across invocations.
                used_run=0,
                used_day=used_day,
            )
            accepted.append(amount)
            denied.append(not complete)
            used_day += amount
        self.assertEqual(accepted, self.budget["accepted"])
        self.assertEqual(denied, self.budget["denied"])
        self.assertLessEqual(used_day, self.budget["per_day"])
