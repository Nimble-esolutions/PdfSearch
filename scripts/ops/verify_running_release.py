#!/usr/bin/env python3
"""Verify the release identity of an actual running application container."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Callable, Iterable, Sequence

try:
    from .release_integrity import IntegrityError, _differences, build_manifest
except ImportError:  # Direct script execution places scripts/ops on sys.path.
    from release_integrity import IntegrityError, _differences, build_manifest


RunCommand = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def _run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, capture_output=True, text=True)


def _checked(run: RunCommand, command: Sequence[str]) -> str:
    result = run(command)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise IntegrityError(
            f"command failed ({result.returncode}): {' '.join(command)}: {detail}"
        )
    return result.stdout


def _single_inspect(payload: str, subject: str) -> dict[str, object]:
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise IntegrityError(f"Docker returned invalid JSON for {subject}: {exc}") from exc
    if not isinstance(decoded, list) or len(decoded) != 1 or not isinstance(decoded[0], dict):
        raise IntegrityError(f"Docker returned an unexpected inspect result for {subject}")
    return decoded[0]


def _container_manifest(
    docker: str, container: str, run: RunCommand
) -> dict[str, object]:
    verifier = "/app/scripts/ops/release_integrity.py"
    _checked(
        run,
        [
            docker,
            "exec",
            container,
            "python",
            verifier,
            "verify",
            "--root",
            "/app",
            "--manifest",
            "/app/release-integrity.json",
        ],
    )
    output = _checked(
        run,
        [
            docker,
            "exec",
            container,
            "python",
            verifier,
            "manifest",
            "--root",
            "/app",
        ],
    )
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        raise IntegrityError(f"container returned an invalid sentinel manifest: {exc}") from exc
    if not isinstance(payload, dict):
        raise IntegrityError("container sentinel manifest is not a JSON object")
    return payload


def verify_running_release(
    *,
    container: str,
    expected_image: str,
    expected_revision: str,
    checkout_root: Path,
    docker: str = "docker",
    run: RunCommand = _run,
) -> dict[str, object]:
    container_info = _single_inspect(
        _checked(run, [docker, "inspect", "--type", "container", container]),
        container,
    )
    config = container_info.get("Config")
    if not isinstance(config, dict):
        raise IntegrityError("container inspect is missing Config")
    actual_config_image = config.get("Image")
    actual_image_id = container_info.get("Image")
    if not isinstance(actual_config_image, str) or not actual_config_image:
        raise IntegrityError("container inspect is missing .Config.Image")
    if not isinstance(actual_image_id, str) or not actual_image_id:
        raise IntegrityError("container inspect is missing .Image")

    image_info = _single_inspect(
        _checked(run, [docker, "image", "inspect", actual_image_id]), actual_image_id
    )
    inspected_image_id = image_info.get("Id")
    if inspected_image_id != actual_image_id:
        raise IntegrityError(
            f"container image ID mismatch: container={actual_image_id} inspected={inspected_image_id}"
        )
    image_config = image_info.get("Config")
    if not isinstance(image_config, dict):
        raise IntegrityError("image inspect is missing Config")
    labels = image_config.get("Labels") or {}
    if not isinstance(labels, dict):
        raise IntegrityError("image OCI labels have an unexpected shape")
    actual_revision = labels.get("org.opencontainers.image.revision")
    repo_digests = image_info.get("RepoDigests") or []
    if not isinstance(repo_digests, list):
        raise IntegrityError("image RepoDigests have an unexpected shape")

    if actual_config_image != expected_image:
        raise IntegrityError(
            "running container image reference drift: "
            f"expected={expected_image} actual_config_image={actual_config_image}"
        )
    if "@sha256:" not in expected_image:
        raise IntegrityError("expected image must be an immutable repo@sha256 digest reference")
    if expected_image not in repo_digests:
        raise IntegrityError(
            "running image content does not advertise the expected digest: "
            f"expected={expected_image} repo_digests={','.join(map(str, repo_digests))}"
        )
    if actual_revision != expected_revision:
        raise IntegrityError(
            "running image OCI revision mismatch: "
            f"expected={expected_revision} actual={actual_revision}"
        )

    expected_manifest = build_manifest(checkout_root)
    actual_manifest = _container_manifest(docker, container, run)
    differences = _differences(expected_manifest, actual_manifest)
    if differences:
        raise IntegrityError(
            "running container sentinel mismatch fields=" + ",".join(differences)
        )

    return {
        "container": container,
        "config_image": actual_config_image,
        "image_id": actual_image_id,
        "oci_revision": actual_revision,
        "repo_digest": expected_image,
        "sentinel_count": len(expected_manifest["sentinels"]),
        "status": "verified",
    }


def _dry_run_commands(docker: str, container: str) -> list[list[str]]:
    verifier = "/app/scripts/ops/release_integrity.py"
    return [
        [docker, "inspect", "--type", "container", container],
        [docker, "image", "inspect", "<actual-container-.Image>"],
        [
            docker,
            "exec",
            container,
            "python",
            verifier,
            "verify",
            "--root",
            "/app",
            "--manifest",
            "/app/release-integrity.json",
        ],
        [docker, "exec", container, "python", verifier, "manifest", "--root", "/app"],
    ]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", required=True)
    parser.add_argument("--expected-image", required=True)
    parser.add_argument("--expected-revision", required=True)
    parser.add_argument("--checkout-root", type=Path, default=Path.cwd())
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.dry_run:
        for command in _dry_run_commands(args.docker, args.container):
            print(" ".join(command))
        print("dry_run_only: no Docker or container state was read or changed")
        return 0
    try:
        evidence = verify_running_release(
            container=args.container,
            expected_image=args.expected_image,
            expected_revision=args.expected_revision,
            checkout_root=args.checkout_root,
            docker=args.docker,
        )
    except IntegrityError as exc:
        print(f"running_release_verification_error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(evidence, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
