"""PID-1 child-process supervisor for staging runtime activation.

The supervisor deliberately uses only the Python standard library and the
pure-stdlib runtime-control protocol. It never imports Django in the parent
process, so every child restart resolves the shared runtime pointer afresh.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from vaultops.runtime_control import (
    RuntimeControlError,
    atomic_write_json,
    build_runtime_pointer,
    read_runtime_pointer,
    read_signed_document,
    resolve_runtime_from_env,
    runtime_control_paths,
    set_runtime_workspace_writable,
    sign_document,
    validate_runtime_workspace,
)


class SupervisorError(RuntimeError):
    reason_code = "runtime_supervisor_failed"

    def __init__(self, reason_code=None):
        self.reason_code = reason_code or self.reason_code
        super().__init__(self.reason_code)


def _bool_env(environment, name, default=False):
    raw = environment.get(name, "1" if default else "0")
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


class RuntimeSupervisor:
    def __init__(
        self,
        role,
        *,
        environment=None,
        popen_factory=None,
        run_command=None,
        urlopen=None,
        sleep=None,
        monotonic=None,
    ):
        if role not in {"web", "maintenance"}:
            raise SupervisorError("runtime_supervisor_role_invalid")
        self.role = role
        self.environment = dict(os.environ if environment is None else environment)
        self.control_root = Path(
            self.environment.get("DATA_CONTROL_ROOT", "/app/data-control")
        )
        self.runtime_root = Path(
            self.environment.get(
                "RUNTIME_GENERATIONS_ROOT",
                "/app/data/runtime-generations",
            )
        )
        self.deployment_id = self.environment.get("DEPLOYMENT_ID", "").strip()
        self.signing_key = self.environment.get(
            "ACTIVATION_INTENT_SIGNING_KEY", ""
        )
        self.app_env = self.environment.get("APP_ENV", "").strip().lower()
        self.activation_enabled = _bool_env(
            self.environment,
            "STAGING_RUNTIME_ACTIVATION_ENABLED",
        )
        self.initial_activation_enabled = _bool_env(
            self.environment,
            "STAGING_INITIAL_ACTIVATION_ENABLED",
        )
        self.apply_mode = self.environment.get(
            "STAGING_ACTIVATION_APPLY_MODE", "auto"
        ).strip().lower()
        self.poll_seconds = max(
            float(
                self.environment.get(
                    "ACTIVATION_SUPERVISOR_POLL_SECONDS", "2"
                )
            ),
            0.1,
        )
        self.readiness_timeout = max(
            int(
                self.environment.get(
                    "ACTIVATION_READINESS_TIMEOUT_SECONDS", "120"
                )
            ),
            1,
        )
        self.paths = runtime_control_paths(self.control_root)
        self.popen_factory = popen_factory or subprocess.Popen
        self.run_command = run_command or subprocess.run
        self.urlopen = urlopen or urllib.request.urlopen
        self.sleep = sleep or time.sleep
        self.monotonic = monotonic or time.monotonic
        self.child = None
        self.shutdown_requested = False
        self.paused_for_intent = ""

    def child_command(self):
        if self.role == "web":
            return [
                "gunicorn",
                "--bind",
                "0.0.0.0:8000",
                "--workers",
                self.environment.get("GUNICORN_WORKERS", "4"),
                "--max-requests",
                self.environment.get("GUNICORN_MAX_REQUESTS", "1000"),
                "--max-requests-jitter",
                self.environment.get(
                    "GUNICORN_MAX_REQUESTS_JITTER", "50"
                ),
                "--timeout",
                self.environment.get("GUNICORN_TIMEOUT", "300"),
                "--access-logfile",
                "-",
                "--error-logfile",
                "-",
                "flowdocs.wsgi:application",
            ]
        return [
            sys.executable,
            "manage.py",
            "run_maintenance_jobs",
        ]

    def _resolved_process_environment(self):
        """Bind a new process to the currently signed runtime authority."""
        child_environment = dict(self.environment)
        runtime = resolve_runtime_from_env(child_environment)
        if runtime is None:
            for name in (
                "RUNTIME_GENERATION_ID",
                "RUNTIME_MANIFEST_DIGEST",
            ):
                child_environment.pop(name, None)
            return child_environment
        child_environment.update(
            {
                "SQLITE_DB_PATH": str(runtime.database_path),
                "MEDIA_ROOT": str(runtime.media_root),
                "PDF_CACHE_DIR": str(runtime.pdf_cache_dir),
                "FAISS_INDEX_DIR": str(runtime.faiss_index_dir),
                "CHROMA_DIR": str(runtime.chroma_dir),
                "RUNTIME_GENERATION_ID": runtime.generation_id,
                "RUNTIME_MANIFEST_DIGEST": runtime.manifest_digest,
            }
        )
        return child_environment

    def start_child(self):
        if self.child is not None and self.child.poll() is None:
            return self.child
        child_environment = self._resolved_process_environment()
        child_environment["FLOWDOCS_SUPERVISOR_CHILD"] = "1"
        self.child = self.popen_factory(
            self.child_command(),
            cwd=Path(__file__).resolve().parent,
            env=child_environment,
            start_new_session=True,
        )
        return self.child

    def stop_child(self, timeout=30):
        if self.child is None or self.child.poll() is not None:
            return
        try:
            os.killpg(self.child.pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            self.child.terminate()
        try:
            self.child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(self.child.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                self.child.kill()
            self.child.wait(timeout=10)

    def _signal(self, signum, frame):
        self.shutdown_requested = True
        self.stop_child()

    def install_signal_handlers(self):
        signal.signal(signal.SIGTERM, self._signal)
        signal.signal(signal.SIGINT, self._signal)

    def _intent_files(self):
        directory = self.paths["intents"]
        if not directory.is_dir():
            return []
        return sorted(directory.glob("*.json"), key=lambda path: path.name)

    def _result_path(self, intent_id):
        return self.paths["results"] / f"{intent_id}.json"

    def _load_intent(self, path):
        try:
            document = read_signed_document(
                path,
                signing_key=self.signing_key,
                expected_kind="activation_intent",
                deployment_id=self.deployment_id,
            )
        except RuntimeControlError as exc:
            raise SupervisorError(exc.reason_code) from exc
        intent_id = document.get("intent_id", "")
        if not intent_id or path.stem != intent_id:
            raise SupervisorError("activation_intent_identity_mismatch")
        if int(document.get("expires_at_unix", 0)) <= int(time.time()):
            raise SupervisorError("activation_intent_expired")
        return document

    def _write_ack(self, intent, state):
        payload = {
            "schema_version": 1,
            "kind": "activation_ack",
            "deployment_id": self.deployment_id,
            "intent_id": intent["intent_id"],
            "intent_digest": intent["document_digest"],
            "role": self.role,
            "state": state,
            "process_id": os.getpid(),
            "observed_at_unix": int(time.time()),
        }
        document = sign_document(payload, self.signing_key)
        path = self.paths["acks"] / (
            f"{intent['intent_id']}.{self.role}.json"
        )
        atomic_write_json(path, document)

    def _read_ack(self, intent, role):
        path = self.paths["acks"] / f"{intent['intent_id']}.{role}.json"
        try:
            document = read_signed_document(
                path,
                signing_key=self.signing_key,
                expected_kind="activation_ack",
                deployment_id=self.deployment_id,
            )
        except RuntimeControlError:
            return None
        if not (
            document.get("intent_id") == intent["intent_id"]
            and document.get("intent_digest")
            == intent["document_digest"]
            and document.get("role") == role
        ):
            return None
        return document

    def _maintenance_acknowledged(self, intent):
        document = self._read_ack(intent, "maintenance")
        return bool(document and document.get("state") == "quiesced")

    def _wait_maintenance_state(self, intent, expected_state):
        deadline = self.monotonic() + self.readiness_timeout
        while self.monotonic() < deadline:
            document = self._read_ack(intent, "maintenance")
            if document and document.get("state") == expected_state:
                return
            self.sleep(0.25)
        raise SupervisorError("activation_maintenance_ack_timeout")

    def _result_exists(self, intent):
        return self._result_path(intent["intent_id"]).is_file()

    def maintenance_tick(self):
        for path in self._intent_files():
            try:
                intent = self._load_intent(path)
            except SupervisorError:
                continue
            if self._result_exists(intent):
                if self.paused_for_intent == intent["intent_id"]:
                    self.start_child()
                    self.paused_for_intent = ""
                continue
            if self.app_env == "production":
                continue
            if self.paused_for_intent and (
                self.paused_for_intent != intent["intent_id"]
            ):
                continue
            if self.paused_for_intent == intent["intent_id"]:
                web_ack = self._read_ack(intent, "web")
                web_state = (
                    web_ack.get("state") if web_ack is not None else ""
                )
                if web_state == "pointer_switched":
                    self.start_child()
                    self._write_ack(intent, "target_started")
                elif web_state == "rollback_pending":
                    self.stop_child()
                    self._write_ack(intent, "rollback_quiesced")
                elif web_state == "rollback_pointer_switched":
                    self.start_child()
                    self._write_ack(intent, "rollback_started")
                return
            self.stop_child()
            self._write_ack(intent, "quiesced")
            self.paused_for_intent = intent["intent_id"]
            return

    def _current_pointer(self):
        return read_runtime_pointer(
            self.paths["active"],
            deployment_id=self.deployment_id,
            signing_key=self.signing_key,
            runtime_root=self.runtime_root,
        )

    def _previous_pointer(self):
        return read_runtime_pointer(
            self.paths["previous"],
            deployment_id=self.deployment_id,
            signing_key=self.signing_key,
            runtime_root=self.runtime_root,
        )

    def _current_pointer_document(self):
        return read_signed_document(
            self.paths["active"],
            signing_key=self.signing_key,
            expected_kind="runtime_pointer",
            deployment_id=self.deployment_id,
        )

    def _acquire_lock(self, intent):
        path = self.paths["lock"]
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags, 0o600)
        except FileExistsError as exc:
            raise SupervisorError("runtime_activation_in_progress") from exc
        content = json.dumps(
            {
                "intent_id": intent["intent_id"],
                "process_id": os.getpid(),
            },
            sort_keys=True,
        ).encode()
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())

    def _release_lock(self, intent):
        path = self.paths["lock"]
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if value.get("intent_id") == intent["intent_id"]:
            path.unlink(missing_ok=True)

    def _claim_recovery_lock(self, intent):
        """Fence restart reconciliation to the interrupted intent only."""
        path = self.paths["lock"]
        if not path.exists():
            self._acquire_lock(intent)
            return
        if path.is_symlink() or not path.is_file():
            raise SupervisorError("runtime_activation_lock_invalid")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SupervisorError(
                "runtime_activation_lock_invalid"
            ) from exc
        if value.get("intent_id") != intent["intent_id"]:
            raise SupervisorError("runtime_activation_in_progress")
        atomic_write_json(
            path,
            {
                "intent_id": intent["intent_id"],
                "process_id": os.getpid(),
                "recovery": True,
            },
        )

    def _acquire_or_resume_pre_cutover_lock(self, intent):
        """Resume only this intent's signed pre-cutover checkpoint."""
        if not self.paths["lock"].exists():
            self._acquire_lock(intent)
            return False
        web_ack = self._read_ack(intent, "web")
        if not web_ack or web_ack.get("state") != "applying":
            raise SupervisorError("runtime_activation_in_progress")
        self._claim_recovery_lock(intent)
        return True

    def _has_same_intent_applying_lock(self, intent):
        web_ack = self._read_ack(intent, "web")
        if not web_ack or web_ack.get("state") != "applying":
            return False
        path = self.paths["lock"]
        if path.is_symlink() or not path.is_file():
            return False
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return value.get("intent_id") == intent["intent_id"]

    @staticmethod
    def _pointer_matches(pointer, *, generation_id, manifest_digest, pointer_digest):
        return (
            pointer.generation_id == generation_id
            and pointer.manifest_digest == manifest_digest
            and pointer.pointer_digest == pointer_digest
        )

    def _rollback_previous_matches(self, intent, previous):
        return self._pointer_matches(
            previous,
            generation_id=intent.get(
                "rollback_previous_generation_id", ""
            ),
            manifest_digest=intent.get(
                "rollback_previous_manifest_digest", ""
            ),
            pointer_digest=intent.get(
                "rollback_previous_pointer_digest", ""
            ),
        ) and (
            previous.generation_id == intent.get("target_generation_id")
            and previous.manifest_digest
            == intent.get("target_manifest_digest")
        )

    def _assert_cutover_authority(self, intent, *, resumed):
        """Recheck signed authority under the acquired same-intent lock."""
        active = self._current_pointer()
        if not (
            active.generation_id
            == intent.get("previous_generation_id", "")
            and active.pointer_digest
            == intent.get("previous_pointer_digest", "")
        ):
            raise SupervisorError("activation_previous_pointer_changed")
        if intent.get("activation_mode") != "rollback":
            return active
        previous = self._previous_pointer()
        if self._rollback_previous_matches(intent, previous):
            return active
        if resumed and (
            previous.generation_id == active.generation_id
            and previous.manifest_digest == active.manifest_digest
            and previous.pointer_digest == active.pointer_digest
        ):
            # Bounded crash state: this same signed intent wrote previous.json
            # but did not yet switch active.json.
            return active
        raise SupervisorError("rollback_previous_pointer_changed")

    def _run_manage(self, arguments, *, timeout):
        process_environment = self._resolved_process_environment()
        result = self.run_command(
            [sys.executable, "manage.py", *arguments],
            cwd=Path(__file__).resolve().parent,
            env=process_environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0:
            raise SupervisorError("activation_runtime_command_failed")

    def _reconcile_result_best_effort(self, intent):
        try:
            self._run_manage(
                [
                    "reconcile_activation_result",
                    "--intent-id",
                    intent["intent_id"],
                ],
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError, SupervisorError):
            # The signed result is durable authority for a later reconciliation.
            return False
        return True

    def _http_json(self, path):
        request = urllib.request.Request(
            f"http://127.0.0.1:8000{path}",
            headers={"Accept": "application/json"},
            method="GET",
        )
        with self.urlopen(request, timeout=5) as response:
            if int(getattr(response, "status", 200)) != 200:
                raise SupervisorError("activation_readiness_failed")
            data = response.read(128 * 1024 + 1)
        if len(data) > 128 * 1024:
            raise SupervisorError("activation_readiness_response_too_large")
        try:
            value = json.loads(data)
        except (ValueError, UnicodeDecodeError) as exc:
            raise SupervisorError("activation_readiness_invalid") from exc
        if not isinstance(value, dict):
            raise SupervisorError("activation_readiness_invalid")
        return value

    def _wait_ready(self, generation_id, manifest_digest):
        deadline = self.monotonic() + self.readiness_timeout
        last_code = "activation_readiness_timeout"
        while self.monotonic() < deadline:
            if self.child is None or self.child.poll() is not None:
                raise SupervisorError("activation_child_exited")
            try:
                live = self._http_json("/livez")
                ready = self._http_json("/readyz")
                if (
                    live.get("status") == "ok"
                    and ready.get("status") == "ready"
                    and ready.get("runtime_generation_id")
                    == generation_id
                    and ready.get("runtime_manifest_digest")
                    == manifest_digest
                ):
                    return {
                        "livez": "ok",
                        "readyz": "ready",
                        "runtime_generation_id": generation_id,
                        "runtime_manifest_digest": manifest_digest,
                    }
                last_code = "activation_readiness_mismatch"
            except (
                OSError,
                urllib.error.URLError,
                SupervisorError,
            ) as exc:
                last_code = getattr(
                    exc, "reason_code", "activation_readiness_unreachable"
                )
            self.sleep(1)
        raise SupervisorError(last_code)

    def _write_result(
        self,
        intent,
        *,
        status,
        active_pointer,
        previous_generation_id,
        readiness_evidence,
        safe_error_code="",
    ):
        payload = {
            "schema_version": 1,
            "kind": "activation_result",
            "deployment_id": self.deployment_id,
            "intent_id": intent["intent_id"],
            "intent_digest": intent["document_digest"],
            "status": status,
            "active_generation_id": active_pointer.generation_id,
            "active_manifest_digest": active_pointer.manifest_digest,
            "previous_generation_id": previous_generation_id,
            "active_pointer_digest": active_pointer.pointer_digest,
            "readiness_evidence": readiness_evidence,
            "process_identity": {
                "supervisor_pid": os.getpid(),
                "child_pid": getattr(self.child, "pid", 0),
                "role": self.role,
            },
            "safe_error_code": safe_error_code,
            "observed_at_unix": int(time.time()),
        }
        atomic_write_json(
            self._result_path(intent["intent_id"]),
            sign_document(payload, self.signing_key),
        )

    def _write_failed_without_cutover(self, intent, reason_code):
        try:
            active = self._current_pointer()
        except RuntimeControlError:
            return
        self._write_result(
            intent,
            status="failed",
            active_pointer=active,
            previous_generation_id=active.generation_id,
            readiness_evidence={},
            safe_error_code=reason_code,
        )

    def _write_initial_failure(self, intent, reason_code):
        payload = {
            "schema_version": 1,
            "kind": "activation_result",
            "deployment_id": self.deployment_id,
            "intent_id": intent["intent_id"],
            "intent_digest": intent["document_digest"],
            "status": "failed",
            "active_generation_id": "",
            "active_manifest_digest": "",
            "previous_generation_id": "",
            "active_pointer_digest": "",
            "readiness_evidence": {},
            "process_identity": {
                "supervisor_pid": os.getpid(),
                "child_pid": getattr(self.child, "pid", 0),
                "role": self.role,
            },
            "safe_error_code": reason_code,
            "observed_at_unix": int(time.time()),
        }
        atomic_write_json(
            self._result_path(intent["intent_id"]),
            sign_document(payload, self.signing_key),
        )

    def _fail_initial_without_pointer(self, intent, reason_code):
        """Release a maintenance quiesce when bootstrap validation fails."""
        self._write_ack(intent, "rollback_pointer_switched")
        self._wait_maintenance_state(intent, "rollback_started")
        self._write_initial_failure(intent, reason_code)
        self._write_ack(intent, "rolled_back")
        if self._reconcile_result_best_effort(intent):
            self._write_ack(intent, "reconciled")

    def _apply_initial_intent(self, intent):
        if not self.initial_activation_enabled:
            return
        if intent.get("previous_generation_id") or intent.get(
            "previous_pointer_digest"
        ):
            return
        if self.paths["previous"].exists():
            return
        try:
            current = self._current_pointer()
        except RuntimeControlError:
            current = None
        if current is not None and (
            current.generation_id != intent["target_generation_id"]
            or current.manifest_digest != intent["target_manifest_digest"]
            or current.intent_digest != intent["document_digest"]
        ):
            return
        if not self._maintenance_acknowledged(intent):
            return
        try:
            validate_runtime_workspace(
                intent["target_runtime_path"],
                runtime_root=self.runtime_root,
                generation_id=intent["target_generation_id"],
                manifest_digest=intent["target_manifest_digest"],
            )
        except Exception as exc:
            self._fail_initial_without_pointer(
                intent,
                getattr(
                    exc,
                    "reason_code",
                    "activation_verification_failed",
                ),
            )
            return
        web_ack = self._read_ack(intent, "web")
        if web_ack and web_ack.get("state") != "applying":
            return
        if web_ack is None:
            self._write_ack(intent, "applying")
        try:
            resumed = self._acquire_or_resume_pre_cutover_lock(intent)
        except SupervisorError:
            return
        try:
            if current is None:
                # Recheck the absence of both rollback authorities under the
                # same-intent lock immediately before the first pointer write.
                if self.paths["active"].exists() or self.paths["previous"].exists():
                    raise SupervisorError(
                        "activation_previous_pointer_changed"
                    )
                target_document = build_runtime_pointer(
                    deployment_id=self.deployment_id,
                    generation_id=intent["target_generation_id"],
                    manifest_digest=intent["target_manifest_digest"],
                    runtime_path=intent["target_runtime_path"],
                    intent_digest=intent["document_digest"],
                    state_version=int(intent.get("state_version", 1)),
                    signing_key=self.signing_key,
                )
                atomic_write_json(self.paths["active"], target_document)
            # The bootstrap web child was intentionally started without a
            # runtime pointer so the operator could prepare this intent.
            # Stop it before any target-bound verification or restart.
            self.stop_child()
            set_runtime_workspace_writable(
                intent["target_runtime_path"],
                runtime_root=self.runtime_root,
                generation_id=intent["target_generation_id"],
                manifest_digest=intent["target_manifest_digest"],
                writable=True,
            )
            self._run_manage(
                ["verify_activation_runtime", "--intent-id", intent["intent_id"]],
                timeout=self.readiness_timeout,
            )
            self.start_child()
            self._write_ack(intent, "pointer_switched")
            self._wait_maintenance_state(intent, "target_started")
            self._write_ack(intent, "verifying")
            readiness = self._wait_ready(
                intent["target_generation_id"],
                intent["target_manifest_digest"],
            )
            self._run_manage(
                ["verify_activation_runtime", "--intent-id", intent["intent_id"]],
                timeout=self.readiness_timeout,
            )
            active = self._current_pointer()
            self._write_result(
                intent,
                status="committed",
                active_pointer=active,
                previous_generation_id="",
                readiness_evidence={
                    **readiness,
                    "runtime_smoke": "passed",
                    "initial_activation": True,
                },
            )
            self._write_ack(intent, "committed")
            if self._reconcile_result_best_effort(intent):
                self._write_ack(intent, "reconciled")
        except Exception as exc:
            reason_code = getattr(
                exc, "reason_code", "activation_verification_failed"
            )
            try:
                self.stop_child()
                active = self._current_pointer()
                if (
                    active.generation_id == intent["target_generation_id"]
                    and active.intent_digest == intent["document_digest"]
                ):
                    self._write_ack(intent, "rollback_pending")
                    self._wait_maintenance_state(
                        intent, "rollback_quiesced"
                    )
                    set_runtime_workspace_writable(
                        intent["target_runtime_path"],
                        runtime_root=self.runtime_root,
                        generation_id=intent["target_generation_id"],
                        manifest_digest=intent["target_manifest_digest"],
                        writable=False,
                    )
                    self.paths["active"].unlink(missing_ok=True)
                    self.start_child()
                    self._write_ack(intent, "rollback_pointer_switched")
                    self._wait_maintenance_state(
                        intent, "rollback_started"
                    )
                self._write_initial_failure(intent, reason_code)
                self._write_ack(intent, "rolled_back")
                if self._reconcile_result_best_effort(intent):
                    self._write_ack(intent, "reconciled")
            except Exception:
                pass
        finally:
            self._release_lock(intent)

    def _recover_incomplete_cutover(self, intent, current):
        if current.generation_id != intent.get("target_generation_id"):
            return False
        try:
            previous_document = read_signed_document(
                self.paths["previous"],
                signing_key=self.signing_key,
                expected_kind="runtime_pointer",
                deployment_id=self.deployment_id,
            )
            if (
                previous_document.get("document_digest")
                != intent.get("previous_pointer_digest")
                or previous_document.get("generation_id")
                != intent.get("previous_generation_id")
            ):
                return False
            self.stop_child()
            self._write_ack(intent, "rollback_pending")
            self._wait_maintenance_state(intent, "rollback_quiesced")
            set_runtime_workspace_writable(
                current.runtime_path,
                runtime_root=self.runtime_root,
                generation_id=current.generation_id,
                manifest_digest=current.manifest_digest,
                writable=False,
            )
            atomic_write_json(self.paths["active"], previous_document)
            set_runtime_workspace_writable(
                previous_document["runtime_path"],
                runtime_root=self.runtime_root,
                generation_id=previous_document["generation_id"],
                manifest_digest=previous_document["manifest_digest"],
                writable=True,
            )
            self.start_child()
            self._write_ack(intent, "rollback_pointer_switched")
            self._wait_maintenance_state(intent, "rollback_started")
            readiness = self._wait_ready(
                intent["previous_generation_id"],
                previous_document["manifest_digest"],
            )
            restored = self._current_pointer()
            self._write_result(
                intent,
                status="rolled_back",
                active_pointer=restored,
                previous_generation_id=intent[
                    "previous_generation_id"
                ],
                readiness_evidence={
                    **readiness,
                    "restart_reconciliation": "rolled_back",
                },
                safe_error_code="activation_incomplete_recovered",
            )
            self._write_ack(intent, "rolled_back")
            if self._reconcile_result_best_effort(intent):
                self._write_ack(intent, "reconciled")
            return True
        except Exception:
            return False

    def apply_intent(self, intent):
        if self.app_env == "production":
            self._write_failed_without_cutover(
                intent, "production_activation_disabled"
            )
            return
        if self.app_env != "staging" or not self.activation_enabled:
            self._write_failed_without_cutover(
                intent, "staging_activation_disabled"
            )
            return
        if intent.get("activation_mode") == "initial":
            self._apply_initial_intent(intent)
            return
        current = self._current_pointer()
        if (
            current.pointer_digest
            != intent.get("previous_pointer_digest")
            or current.generation_id
            != intent.get("previous_generation_id")
        ):
            try:
                self._claim_recovery_lock(intent)
            except SupervisorError:
                return
            try:
                if self._recover_incomplete_cutover(intent, current):
                    return
            finally:
                self._release_lock(intent)
            self._write_failed_without_cutover(
                intent, "activation_previous_pointer_changed"
            )
            return
        if intent.get("activation_mode") == "rollback":
            try:
                previous = self._previous_pointer()
            except RuntimeControlError:
                self._write_failed_without_cutover(
                    intent, "rollback_previous_pointer_changed"
                )
                return
            resumable_intermediate = (
                self._has_same_intent_applying_lock(intent)
                and previous.generation_id == current.generation_id
                and previous.manifest_digest == current.manifest_digest
                and previous.pointer_digest == current.pointer_digest
            )
            if not (
                self._rollback_previous_matches(intent, previous)
                or resumable_intermediate
            ):
                self._write_failed_without_cutover(
                    intent, "rollback_previous_pointer_changed"
                )
                return
        if not self._maintenance_acknowledged(intent):
            return
        validate_runtime_workspace(
            intent["target_runtime_path"],
            runtime_root=self.runtime_root,
            generation_id=intent["target_generation_id"],
            manifest_digest=intent["target_manifest_digest"],
        )
        # Persist a signed same-intent checkpoint before the lock. A process
        # crash on either side can then acquire or resume deterministically.
        web_ack = self._read_ack(intent, "web")
        if web_ack and web_ack.get("state") != "applying":
            return
        if web_ack is None:
            self._write_ack(intent, "applying")
        try:
            resumed = self._acquire_or_resume_pre_cutover_lock(intent)
        except SupervisorError:
            return
        try:
            current = self._assert_cutover_authority(
                intent, resumed=resumed
            )
            previous_document = self._current_pointer_document()
        except SupervisorError as exc:
            self._write_failed_without_cutover(intent, exc.reason_code)
            self._release_lock(intent)
            return
        readiness = {}
        try:
            self.stop_child()
            set_runtime_workspace_writable(
                current.runtime_path,
                runtime_root=self.runtime_root,
                generation_id=current.generation_id,
                manifest_digest=current.manifest_digest,
                writable=False,
            )
            atomic_write_json(self.paths["previous"], previous_document)
            target_document = build_runtime_pointer(
                deployment_id=self.deployment_id,
                generation_id=intent["target_generation_id"],
                manifest_digest=intent["target_manifest_digest"],
                runtime_path=intent["target_runtime_path"],
                intent_digest=intent["document_digest"],
                state_version=int(intent.get("state_version", 1)),
                signing_key=self.signing_key,
            )
            atomic_write_json(self.paths["active"], target_document)
            set_runtime_workspace_writable(
                intent["target_runtime_path"],
                runtime_root=self.runtime_root,
                generation_id=intent["target_generation_id"],
                manifest_digest=intent["target_manifest_digest"],
                writable=True,
            )
            self._run_manage(
                [
                    "verify_activation_runtime",
                    "--intent-id",
                    intent["intent_id"],
                ],
                timeout=self.readiness_timeout,
            )
            self.start_child()
            self._write_ack(intent, "pointer_switched")
            self._wait_maintenance_state(intent, "target_started")
            self._write_ack(intent, "verifying")
            readiness = self._wait_ready(
                intent["target_generation_id"],
                intent["target_manifest_digest"],
            )
            self._run_manage(
                [
                    "verify_activation_runtime",
                    "--intent-id",
                    intent["intent_id"],
                ],
                timeout=self.readiness_timeout,
            )
            active = self._current_pointer()
            self._write_result(
                intent,
                status="committed",
                active_pointer=active,
                previous_generation_id=current.generation_id,
                readiness_evidence={
                    **readiness,
                    "runtime_smoke": "passed",
                },
            )
            self._write_ack(intent, "committed")
            if self._reconcile_result_best_effort(intent):
                self._write_ack(intent, "reconciled")
        except Exception as exc:
            reason_code = getattr(
                exc, "reason_code", "activation_verification_failed"
            )
            try:
                self.stop_child()
                self._write_ack(intent, "rollback_pending")
                self._wait_maintenance_state(
                    intent, "rollback_quiesced"
                )
                set_runtime_workspace_writable(
                    intent["target_runtime_path"],
                    runtime_root=self.runtime_root,
                    generation_id=intent["target_generation_id"],
                    manifest_digest=intent["target_manifest_digest"],
                    writable=False,
                )
                atomic_write_json(self.paths["active"], previous_document)
                set_runtime_workspace_writable(
                    current.runtime_path,
                    runtime_root=self.runtime_root,
                    generation_id=current.generation_id,
                    manifest_digest=current.manifest_digest,
                    writable=True,
                )
                self.start_child()
                self._write_ack(intent, "rollback_pointer_switched")
                self._wait_maintenance_state(
                    intent, "rollback_started"
                )
                rollback_readiness = self._wait_ready(
                    current.generation_id,
                    current.manifest_digest,
                )
                restored = self._current_pointer()
                self._write_result(
                    intent,
                    status="rolled_back",
                    active_pointer=restored,
                    previous_generation_id=current.generation_id,
                    readiness_evidence={
                        **rollback_readiness,
                        "rollback_verification": "passed",
                    },
                    safe_error_code=reason_code,
                )
                self._write_ack(intent, "rolled_back")
                if self._reconcile_result_best_effort(intent):
                    self._write_ack(intent, "reconciled")
            except Exception:
                try:
                    restored = self._current_pointer()
                    self._write_result(
                        intent,
                        status="rollback_failed",
                        active_pointer=restored,
                        previous_generation_id=current.generation_id,
                        readiness_evidence={},
                        safe_error_code="activation_rollback_failed",
                    )
                except Exception:
                    pass
        finally:
            self._release_lock(intent)

    def web_tick(self):
        if self.apply_mode != "auto":
            return
        for path in self._intent_files():
            try:
                intent = self._load_intent(path)
            except SupervisorError:
                continue
            if self._result_exists(intent):
                web_ack = self._read_ack(intent, "web")
                if (
                    not web_ack
                    or web_ack.get("state") != "reconciled"
                ) and self._reconcile_result_best_effort(intent):
                    self._write_ack(intent, "reconciled")
                continue
            self.apply_intent(intent)
            return

    def run(self):
        self.install_signal_handlers()
        if self.activation_enabled and self.app_env == "staging":
            try:
                active = self._current_pointer()
            except RuntimeControlError:
                if (
                    not self.initial_activation_enabled
                    or self.paths["active"].exists()
                    or self.paths["previous"].exists()
                ):
                    raise
            else:
                set_runtime_workspace_writable(
                    active.runtime_path,
                    runtime_root=self.runtime_root,
                    generation_id=active.generation_id,
                    manifest_digest=active.manifest_digest,
                    writable=True,
                )
        self.start_child()
        while not self.shutdown_requested:
            if self.activation_enabled:
                if self.role == "maintenance":
                    self.maintenance_tick()
                else:
                    self.web_tick()
            if (
                self.child is not None
                and self.child.poll() is not None
                and not self.paused_for_intent
            ):
                return int(self.child.returncode or 0)
            self.sleep(self.poll_seconds)
        return 0


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--role", required=True, choices=["web", "maintenance"]
    )
    options = parser.parse_args(argv)
    supervisor = RuntimeSupervisor(options.role)
    return supervisor.run()


if __name__ == "__main__":
    raise SystemExit(main())
