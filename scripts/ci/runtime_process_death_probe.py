#!/usr/bin/env python3
"""Crash/recovery harness for the signed staging runtime supervisor."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, "/app/flowdocs")

from runtime_supervisor import RuntimeSupervisor
from vaultops.runtime_control import (
    atomic_write_json,
    build_runtime_pointer,
    read_runtime_pointer,
    read_signed_document,
    runtime_control_paths,
    sign_document,
)


CURRENT_GENERATION = "process-death-current"
TARGET_GENERATION = "process-death-target"
CURRENT_DIGEST = "a" * 64
TARGET_DIGEST = "b" * 64


class FakeChild:
    pid = 4242

    def poll(self):
        return None


class ProbeSupervisor(RuntimeSupervisor):
    def __init__(self, role, *, block_after_switch=False, maintenance=None):
        super().__init__(
            role,
            environment=os.environ,
            sleep=(
                (lambda _seconds: maintenance.maintenance_tick())
                if maintenance is not None
                else (lambda _seconds: None)
            ),
        )
        self.child = FakeChild()
        self.block_after_switch = block_after_switch

    def stop_child(self, timeout=30):
        return None

    def start_child(self):
        return self.child

    def _run_manage(self, arguments, *, timeout):
        if self.block_after_switch:
            marker = self.control_root / "process-death" / "cutover-reached"
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("target pointer durable\n", encoding="utf-8")
            while True:
                time.sleep(60)

    def _wait_ready(self, generation_id):
        return {
            "livez": "ok",
            "readyz": "ready",
            "runtime_generation_id": generation_id,
            "probe": "container-restart",
        }

    def _reconcile_result_best_effort(self, intent):
        return True


def _create_runtime(root, generation_id, manifest_digest):
    runtime = root / f"{generation_id}-{manifest_digest[:12]}"
    for relative in ("media", "pdf_cache", "faiss_indexes", "chroma_db"):
        (runtime / relative).mkdir(parents=True, exist_ok=True)
    database = runtime / "db.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE django_migrations ("
        "id INTEGER PRIMARY KEY, app TEXT, name TEXT)"
    )
    connection.commit()
    connection.close()
    (runtime / "runtime-evidence.json").write_text(
        json.dumps(
            {
                "generation_id": generation_id,
                "manifest_digest": manifest_digest,
                "profile_fingerprint": "f" * 64,
                "sanitized": True,
                "static_assets_posture": "custody_only",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    for directory, directories, files in os.walk(runtime, topdown=False):
        for name in files:
            os.chmod(Path(directory) / name, 0o440)
        for name in directories:
            os.chmod(Path(directory) / name, 0o550)
    os.chmod(runtime, 0o550)
    return runtime


def _setup():
    control_root = Path(os.environ["DATA_CONTROL_ROOT"])
    runtime_root = Path(os.environ["RUNTIME_GENERATIONS_ROOT"])
    deployment_id = os.environ["DEPLOYMENT_ID"]
    signing_key = os.environ["ACTIVATION_INTENT_SIGNING_KEY"]
    paths = runtime_control_paths(control_root)
    current = _create_runtime(
        runtime_root, CURRENT_GENERATION, CURRENT_DIGEST
    )
    target = _create_runtime(runtime_root, TARGET_GENERATION, TARGET_DIGEST)
    current_pointer = build_runtime_pointer(
        deployment_id=deployment_id,
        generation_id=CURRENT_GENERATION,
        manifest_digest=CURRENT_DIGEST,
        runtime_path=current,
        intent_digest="bootstrap-intent",
        state_version=1,
        signing_key=signing_key,
    )
    atomic_write_json(paths["active"], current_pointer)
    intent_id = str(uuid.uuid4())
    intent = sign_document(
        {
            "schema_version": 1,
            "kind": "activation_intent",
            "intent_id": intent_id,
            "deployment_id": deployment_id,
            "target_generation_id": TARGET_GENERATION,
            "target_manifest_digest": TARGET_DIGEST,
            "target_runtime_path": str(target),
            "previous_generation_id": CURRENT_GENERATION,
            "previous_pointer_digest": current_pointer["document_digest"],
            "smoke_queries_digest": "c" * 64,
            "expires_at_unix": int(time.time()) + 900,
            "state_version": 1,
        },
        signing_key,
    )
    atomic_write_json(paths["intents"] / f"{intent_id}.json", intent)
    maintenance_ack = sign_document(
        {
            "schema_version": 1,
            "kind": "activation_ack",
            "deployment_id": deployment_id,
            "intent_id": intent_id,
            "intent_digest": intent["document_digest"],
            "role": "maintenance",
            "state": "quiesced",
            "process_id": os.getpid(),
            "observed_at_unix": int(time.time()),
        },
        signing_key,
    )
    atomic_write_json(
        paths["acks"] / f"{intent_id}.maintenance.json",
        maintenance_ack,
    )
    return intent


def _intent():
    supervisor = ProbeSupervisor("web")
    intent_path = supervisor._intent_files()[0]
    return supervisor._load_intent(intent_path)


def cutover():
    intent = _setup()
    ProbeSupervisor("web", block_after_switch=True).apply_intent(intent)
    raise AssertionError("cutover probe unexpectedly returned")


def recover():
    intent = _intent()
    maintenance = ProbeSupervisor("maintenance")
    maintenance.maintenance_tick()
    web = ProbeSupervisor("web", maintenance=maintenance)
    web.apply_intent(intent)
    print("process-death recovery completed", flush=True)


def verify():
    control_root = Path(os.environ["DATA_CONTROL_ROOT"])
    runtime_root = Path(os.environ["RUNTIME_GENERATIONS_ROOT"])
    deployment_id = os.environ["DEPLOYMENT_ID"]
    signing_key = os.environ["ACTIVATION_INTENT_SIGNING_KEY"]
    paths = runtime_control_paths(control_root)
    active = read_runtime_pointer(
        paths["active"],
        deployment_id=deployment_id,
        signing_key=signing_key,
        runtime_root=runtime_root,
    )
    if active.generation_id != CURRENT_GENERATION:
        raise AssertionError(
            f"expected rollback to {CURRENT_GENERATION}, got "
            f"{active.generation_id}"
        )
    intent = _intent()
    result = read_signed_document(
        paths["results"] / f"{intent['intent_id']}.json",
        signing_key=signing_key,
        expected_kind="activation_result",
        deployment_id=deployment_id,
    )
    if result.get("status") != "rolled_back":
        raise AssertionError(f"unexpected result: {result}")
    if result.get("safe_error_code") != "activation_incomplete_recovered":
        raise AssertionError(f"unexpected recovery code: {result}")
    if paths["lock"].exists():
        raise AssertionError("stale activation lock survived reconciliation")
    marker = control_root / "process-death" / "cutover-reached"
    if not marker.is_file():
        raise AssertionError("container-death marker did not survive")
    print("signed pointer, result, rollback, and lock recovery verified")


if __name__ == "__main__":
    modes = {"cutover": cutover, "recover": recover, "verify": verify}
    try:
        mode = sys.argv[1]
        action = modes[mode]
    except (IndexError, KeyError):
        raise SystemExit("usage: runtime_process_death_probe.py cutover|recover|verify")
    action()
