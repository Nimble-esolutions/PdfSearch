import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from core.maintenance_plans import capability_reasons
from core.worker_readiness import (
    maintenance_worker_capability,
    write_worker_heartbeat,
)


class MaintenanceWorkerCapabilityTests(SimpleTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "runtime" / "worker.heartbeat"
        self.settings = override_settings(
            MAINTENANCE_WORKER_HEARTBEAT_PATH=self.path,
            MAINTENANCE_WORKER_HEARTBEAT_MAX_AGE_SECONDS=30,
            MAINTENANCE_WORKER_READINESS_REQUIRED=True,
            LOCAL_INDEX_MAINTENANCE_ENABLED=True,
            FORCE_REINDEX_ENABLED=True,
            EXTERNAL_EMBEDDINGS_ENABLED=True,
            ACTIVE_RUNTIME=None,
        )
        self.settings.enable()
        self.addCleanup(self.settings.disable)

    def test_missing_heartbeat_disables_every_local_operation(self):
        capability = maintenance_worker_capability(observed_at=100)

        self.assertEqual(capability["state"], "worker_offline")
        self.assertEqual(
            capability["reason_code"], "maintenance_worker_unavailable"
        )
        self.assertEqual(
            set(capability_reasons().values()),
            {"maintenance_worker_unavailable"},
        )

    def test_fresh_shared_heartbeat_enables_local_operations(self):
        write_worker_heartbeat(observed_at=time.time())

        capability = maintenance_worker_capability()

        self.assertEqual(capability["state"], "available")
        self.assertTrue(capability["available"])
        self.assertEqual(set(capability_reasons().values()), {""})

    def test_stale_heartbeat_is_not_accepted_as_worker_availability(self):
        write_worker_heartbeat(observed_at=50)
        os.utime(self.path, (50, 50))

        capability = maintenance_worker_capability(observed_at=100)

        self.assertEqual(capability["state"], "worker_offline")
        self.assertEqual(capability["age_seconds"], 50)

    @patch(
        "core.maintenance_plans.maintenance_worker_capability",
        return_value={
            "state": "worker_offline",
            "available": False,
            "reason_code": "maintenance_worker_unavailable",
        },
    )
    def test_capability_gate_uses_server_evidence_not_client_state(self, worker):
        reasons = capability_reasons()

        self.assertEqual(
            reasons["validate"], "maintenance_worker_unavailable"
        )
        worker.assert_called_once_with()
