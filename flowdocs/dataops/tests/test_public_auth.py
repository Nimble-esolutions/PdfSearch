import unittest
from types import SimpleNamespace

from dataops.public_auth import public_auth_gate


class PublicAuthGateTests(unittest.TestCase):
    def test_private_stage_is_the_default_for_production_derived_auth_data(self):
        result = public_auth_gate(
            SimpleNamespace(
                STAGE_PUBLIC_AUTH_EXCEPTION_REQUIRED=True,
                PUBLIC_SEARCH_ENABLED=False,
                STAGE_PUBLIC_AUTH_EXCEPTION_APPROVED=False,
            )
        )
        self.assertEqual(result["status"], "private_only")
        self.assertFalse(result["requested"])

    def test_public_stage_requires_recorded_security_controls(self):
        result = public_auth_gate(
            SimpleNamespace(
                STAGE_PUBLIC_AUTH_EXCEPTION_REQUIRED=True,
                PUBLIC_SEARCH_ENABLED=True,
                STAGE_PUBLIC_AUTH_EXCEPTION_APPROVED=True,
                STAGE_PUBLIC_AUTH_EXCEPTION_OWNER="owner",
                STAGE_PUBLIC_AUTH_EXCEPTION_MONITORING="monitoring",
                STAGE_PUBLIC_AUTH_EXCEPTION_INCIDENT_RESPONSE="incident-response",
                STAGE_PUBLIC_AUTH_EXCEPTION_ROLLBACK_AUTHORITY="rollback-authority",
            )
        )
        self.assertEqual(result["status"], "approved")
        self.assertTrue(result["approved"])

    def test_approval_switch_without_complete_record_stays_blocked(self):
        result = public_auth_gate(
            SimpleNamespace(
                STAGE_PUBLIC_AUTH_EXCEPTION_REQUIRED=True,
                PUBLIC_SEARCH_ENABLED=True,
                STAGE_PUBLIC_AUTH_EXCEPTION_APPROVED=True,
                STAGE_PUBLIC_AUTH_EXCEPTION_OWNER="owner",
            )
        )
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason_code"], "security_owner_approval_required")


if __name__ == "__main__":
    unittest.main()
