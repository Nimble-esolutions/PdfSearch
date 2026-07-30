#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MODE="${1:-}"
ACTION="${2:-start}"

usage() {
  cat <<'EOF'
Usage:
  run_recovery_certification.sh fresh start
  run_recovery_certification.sh accumulated start
  run_recovery_certification.sh <fresh|accumulated> activate
  run_recovery_certification.sh fresh validate
  run_recovery_certification.sh <fresh|accumulated> evidence
  run_recovery_certification.sh <fresh|accumulated> cleanup

Required environment:
  PDFSEARCH_IMAGE         immutable repository@sha256 image
  CERT_VAULT_NETWORK      existing Docker network that reaches only the Vault
  CERT_WEB_PORT           unused localhost port
  CERT_ACTIVATION_INTENT_SIGNING_KEY
                          ephemeral, at least 32 characters
  CERT_ACTIVATION_RECOVERY_SUPERADMIN_USERNAME
  CERT_ACTIVATION_RECOVERY_SUPERADMIN_PASSWORD
                          ephemeral credentials for this disposable target

For accumulated mode:
  CERT_SOURCE_DATA_VOLUME and CERT_SOURCE_CONTROL_VOLUME must identify a
  quiesced, independently backed-up source pair. The wrapper refuses sources
  mounted by any running container.

Optional:
  CERT_RUN_ID             lowercase letters, digits, and hyphens
  CERT_EVIDENCE_DIR       defaults outside the repository
  CERT_RETAIN_SUCCESS=1   make cleanup retain a successful target

Evidence acceptance additionally requires:
  CERT_OPERATOR_ACCEPTED=1
  CERT_GENERATION_ID, CERT_MANIFEST_DIGEST, and CERT_ACTIVATION_INTENT_ID
EOF
}

case "$MODE:$ACTION" in
  fresh:start|fresh:activate|fresh:validate|fresh:evidence|fresh:cleanup|accumulated:start|accumulated:activate|accumulated:validate|accumulated:evidence|accumulated:cleanup) ;;
  *) usage >&2; exit 2 ;;
esac

python3 - "${PDFSEARCH_IMAGE:-}" <<'PY'
import re, sys
if not re.fullmatch(r"[^@\s]+@sha256:[0-9a-f]{64}", sys.argv[1]):
    raise SystemExit("PDFSEARCH_IMAGE must be an immutable repository@sha256 reference")
PY

CERT_RUN_ID="${CERT_RUN_ID:-$(date -u +%Y%m%d-%H%M%S)}"
case "$CERT_RUN_ID" in
  *[!a-z0-9-]*|"") echo "CERT_RUN_ID contains unsupported characters" >&2; exit 2 ;;
esac
case "${CERT_WEB_PORT:-}" in
  ''|*[!0-9]*) echo "CERT_WEB_PORT must be numeric" >&2; exit 2 ;;
esac
case "${CERT_VAULT_NETWORK:-}" in
  ''|*[!A-Za-z0-9_.-]*) echo "CERT_VAULT_NETWORK is invalid" >&2; exit 2 ;;
esac

if [ "$ACTION" != "cleanup" ]; then
  : "${CERT_ACTIVATION_INTENT_SIGNING_KEY:?CERT_ACTIVATION_INTENT_SIGNING_KEY is required}"
  : "${CERT_ACTIVATION_RECOVERY_SUPERADMIN_USERNAME:?CERT_ACTIVATION_RECOVERY_SUPERADMIN_USERNAME is required}"
  : "${CERT_ACTIVATION_RECOVERY_SUPERADMIN_PASSWORD:?CERT_ACTIVATION_RECOVERY_SUPERADMIN_PASSWORD is required}"
  [ "${#CERT_ACTIVATION_INTENT_SIGNING_KEY}" -ge 32 ] || {
    echo "CERT_ACTIVATION_INTENT_SIGNING_KEY must contain at least 32 characters" >&2
    exit 2
  }
fi
if [ "$ACTION" = "activate" ] || [ "$ACTION" = "evidence" ]; then
  export CERT_ACTIVATION_ENABLED=1
  if [ "$MODE" = "fresh" ]; then
    export CERT_INITIAL_ACTIVATION_ENABLED=1
  else
    export CERT_INITIAL_ACTIVATION_ENABLED=0
  fi
else
  # Never inherit an accidentally enabled activation posture into preflight.
  export CERT_ACTIVATION_ENABLED=0
  export CERT_INITIAL_ACTIVATION_ENABLED=0
fi

export CERT_DEPLOYMENT_ID="recovery-cert-$CERT_RUN_ID"
export CERT_DATA_VOLUME="recovery-cert-$CERT_RUN_ID-data"
export CERT_CONTROL_VOLUME="recovery-cert-$CERT_RUN_ID-control"
export CERT_REDIS_VOLUME="recovery-cert-$CERT_RUN_ID-redis"
export CERT_EMPTY_LEGACY_VOLUME="recovery-cert-$CERT_RUN_ID-empty-legacy"
export CERT_INTERNAL_NETWORK="recovery-cert-$CERT_RUN_ID-internal"
PROJECT="recovery-cert-$CERT_RUN_ID"
EVIDENCE_DIR="${CERT_EVIDENCE_DIR:-/tmp/$PROJECT-evidence}"
COMPOSE=(
  docker compose
  -p "$PROJECT"
  -f "$ROOT/docker-compose.yml"
  -f "$ROOT/docker-compose.recovery-cert.yml"
)
LABEL="net.ai-sahakar.recovery-cert=$CERT_RUN_ID"

app_exec() {
  local service="$1"
  shift
  "${COMPOSE[@]}" exec -T --user appuser "$service" "$@"
}

assert_appuser_runtime() {
  app_exec web sh -c '
    set -eu
    test "$(id -u)" = "1000"
    test -r /app/data
    test -w /app/data
    test -r /app/data-control
    test -w /app/data-control
  ' || {
    echo "Certification management commands cannot run as appuser" >&2
    exit 1
  }
}

resource_is_owned() {
  kind="$1"
  name="$2"
  [ "$(docker "$kind" inspect -f '{{index .Labels "net.ai-sahakar.recovery-cert"}}' "$name" 2>/dev/null || true)" = "$CERT_RUN_ID" ]
}

assert_not_mounted() {
  volume="$1"
  mounted=""
  while IFS= read -r container; do
    [ -n "$container" ] || continue
    match="$(
      docker inspect \
        --format '{{.Name}} {{range .Mounts}}{{if eq .Name "'"$volume"'"}}{{.Destination}}{{end}}{{end}}' \
        "$container"
    )"
    case "$match" in
      *" /"*) mounted="${mounted}${match}"$'\n' ;;
    esac
  done < <(docker ps -q)
  if [ -n "$mounted" ]; then
    echo "Volume is mounted by a running container: $volume" >&2
    return 1
  fi
}

assert_empty_volume() {
  volume="$1"
  docker run --rm --user 0:0 --entrypoint sh \
    --mount "type=volume,src=$volume,dst=/target,readonly" \
    "$PDFSEARCH_IMAGE" \
    -c 'test -z "$(find /target -mindepth 1 -print -quit)"' || {
      echo "Certification volume is not empty: $volume" >&2
      exit 1
    }
}

create_volume() {
  volume="$1"
  if docker volume inspect "$volume" >/dev/null 2>&1; then
    resource_is_owned volume "$volume" || {
      echo "Refusing unowned existing volume: $volume" >&2
      exit 1
    }
  else
    docker volume create --label "$LABEL" "$volume" >/dev/null
  fi
}

copy_quiesced_volume() {
  source="$1"
  target="$2"
  [ "$source" != "$target" ] || {
    echo "Source and target volume must differ" >&2
    exit 1
  }
  docker volume inspect "$source" >/dev/null
  assert_not_mounted "$source"
  docker run --rm --user 0:0 --entrypoint sh \
    --mount "type=volume,src=$source,dst=/source,readonly" \
    --mount "type=volume,src=$target,dst=/target" \
    "$PDFSEARCH_IMAGE" \
    -c 'set -eu; test -z "$(find /target -mindepth 1 -print -quit)"; cd /source; tar cf - . | tar xpf - -C /target'
}

render_and_assert_model() {
  rendered="$("${COMPOSE[@]}" config --format json)"
  RENDERED_CONFIG="$rendered" \
  python3 - "$PROJECT" "$CERT_DATA_VOLUME" "$CERT_CONTROL_VOLUME" \
    "$CERT_EMPTY_LEGACY_VOLUME" "$CERT_REDIS_VOLUME" \
    "$CERT_INTERNAL_NETWORK" "$CERT_VAULT_NETWORK" <<'PY'
import json, os, sys
expected = {
    "/app/data": "cert_data",
    "/app/data-control": "cert_control",
    "/mnt/legacy": "cert_empty_legacy",
}
model = json.loads(os.environ["RENDERED_CONFIG"])
if model.get("name") != sys.argv[1]:
    raise SystemExit("certification Compose project identity is invalid")
expected_resources = {
    "cert_data": sys.argv[2],
    "cert_control": sys.argv[3],
    "cert_empty_legacy": sys.argv[4],
    "cert_redis": sys.argv[5],
}
for key, name in expected_resources.items():
    if model["volumes"][key].get("name") != name:
        raise SystemExit(f"{key}: unexpected external volume name")
expected_networks = {
    "cert_internal": sys.argv[6],
    "cert_vault": sys.argv[7],
}
for key, name in expected_networks.items():
    if model["networks"][key].get("name") != name:
        raise SystemExit(f"{key}: unexpected network name")
services = model["services"]
for service_name in ("web", "maintenance"):
    service = services[service_name]
    mounts = {item["target"]: item["source"] for item in service["volumes"]}
    if mounts != expected:
        raise SystemExit(f"{service_name}: unexpected mounts: {mounts}")
    networks = set(service["networks"])
    if networks != {"cert_internal", "cert_vault"}:
        raise SystemExit(f"{service_name}: unexpected networks: {networks}")
    environment = service["environment"]
    if environment.get("CREATE_SUPERUSER") != "0":
        raise SystemExit(f"{service_name}: startup superuser creation must be disabled")
    expected_activation = os.environ["CERT_ACTIVATION_ENABLED"]
    expected_initial = os.environ["CERT_INITIAL_ACTIVATION_ENABLED"]
    if environment.get("STAGING_RUNTIME_ACTIVATION_ENABLED") != expected_activation:
        raise SystemExit(f"{service_name}: unexpected activation phase")
    if environment.get("STAGING_INITIAL_ACTIVATION_ENABLED") != expected_initial:
        raise SystemExit(f"{service_name}: unexpected initial-activation phase")
ports = services["web"].get("ports", [])
if len(ports) != 1 or ports[0].get("host_ip") != "127.0.0.1":
    raise SystemExit("web: certification ingress is not localhost-only")
redis_mounts = {item["target"]: item["source"] for item in services["redis"]["volumes"]}
if redis_mounts != {"/data": "cert_redis"}:
    raise SystemExit(f"redis: unexpected mounts: {redis_mounts}")
PY
}

write_evidence() {
  phase="${1:-final}"
  mkdir -p "$EVIDENCE_DIR"
  umask 077
  python3 - "$PROJECT" "$MODE" "$PDFSEARCH_IMAGE" \
    "$CERT_DATA_VOLUME" "$CERT_CONTROL_VOLUME" "$CERT_EMPTY_LEGACY_VOLUME" \
    "$CERT_REDIS_VOLUME" "$CERT_INTERNAL_NETWORK" "$CERT_VAULT_NETWORK" \
    "$("${COMPOSE[@]}" ps --format json 2>/dev/null || printf '[]')" \
    >"$EVIDENCE_DIR/runtime.json" <<'PY'
import json, sys
try:
    services = json.loads(sys.argv[10])
except json.JSONDecodeError:
    services = [
        json.loads(line) for line in sys.argv[10].splitlines() if line.strip()
    ]
if isinstance(services, dict):
    services = [services]
bounded = [{
    key: row.get(key) for key in
    ("Name", "Service", "State", "Health", "Image")
} for row in services]
print(json.dumps({
    "schema_version": 1,
    "project": sys.argv[1],
    "mode": sys.argv[2],
    "image": sys.argv[3],
    "volumes": {
        "data": sys.argv[4], "control": sys.argv[5],
        "empty_legacy": sys.argv[6], "redis": sys.argv[7],
    },
    "networks": {"internal": sys.argv[8], "vault": sys.argv[9]},
    "services": bounded,
}, indent=2, sort_keys=True))
PY
  curl -fsS "http://127.0.0.1:$CERT_WEB_PORT/livez" >"$EVIDENCE_DIR/livez.txt"
  if [ "$phase" = "final" ]; then
    curl -fsS "http://127.0.0.1:$CERT_WEB_PORT/readyz" >"$EVIDENCE_DIR/readyz.txt"
    printf '200\n' >"$EVIDENCE_DIR/readyz-status.txt"
  else
    curl -sS -o "$EVIDENCE_DIR/readyz.txt" \
      -w '%{http_code}\n' \
      "http://127.0.0.1:$CERT_WEB_PORT/readyz" \
      >"$EVIDENCE_DIR/readyz-status.txt"
  fi
  app_exec web python manage.py shell -c '
import json
from django.conf import settings
from core.management.commands.inventory_artifacts import build_manifest
m = build_manifest(settings.DATA_ROOT)
faiss = [item.get("faiss", {}) for item in m["faiss"]["files"]]
print(json.dumps({
    "schema_version": 1,
    "database_sha256": m["database"]["sha256"],
    "database_size_bytes": m["database"]["size_bytes"],
    "migration_count": m["database"]["migrations"]["count"],
    "latest_core_migration": m["database"]["migrations"]["latest"],
    "counts": m["counts"],
    "faiss_loadable": sum(bool(item.get("loadable")) for item in faiss),
    "faiss_vectors": sum(int(item.get("vector_count", 0)) for item in faiss),
    "faiss_dimensions": sorted({
        int(item["dimensions"]) for item in faiss if item.get("dimensions") is not None
    }),
}, sort_keys=True))
' >"$EVIDENCE_DIR/inventory.json"
  app_exec web python manage.py shell -c '
from django.db import connection
with connection.cursor() as cursor:
    cursor.execute("PRAGMA integrity_check")
    print("integrity", cursor.fetchone()[0])
    cursor.execute("PRAGMA foreign_key_check")
    print("foreign_key_violations", len(cursor.fetchall()))
' >"$EVIDENCE_DIR/database.txt"
  echo "Bounded evidence written to $EVIDENCE_DIR"
}

cleanup() {
  mkdir -p "$EVIDENCE_DIR"
  marker="$EVIDENCE_DIR/certification-passed.json"
  if [ ! -f "$marker" ]; then
    echo "Certification has not passed; retaining project and volumes for review." >&2
    exit 1
  fi
  python3 - "$marker" "$EVIDENCE_DIR" "$PROJECT" "$MODE" "$PDFSEARCH_IMAGE" <<'PY'
import hashlib, json, pathlib, sys
marker_path = pathlib.Path(sys.argv[1])
evidence_dir = pathlib.Path(sys.argv[2]).resolve()
if marker_path.is_symlink():
    raise SystemExit("Certification marker path is unsafe")
document = json.loads(marker_path.read_text(encoding="utf-8"))
if (
    document.get("schema_version") != 1
    or document.get("status") != "passed"
    or document.get("project") != sys.argv[3]
    or document.get("mode") != sys.argv[4]
    or document.get("image") != sys.argv[5]
):
    raise SystemExit("Certification marker identity is invalid")
files = document.get("evidence_sha256")
if not isinstance(files, dict) or not files:
    raise SystemExit("Certification marker has no bounded evidence")
for name, expected in files.items():
    path = (evidence_dir / name).resolve()
    try:
        path.relative_to(evidence_dir)
    except ValueError as exc:
        raise SystemExit("Certification marker path is unsafe") from exc
    if (
        path.is_symlink()
        or not path.is_file()
        or hashlib.sha256(path.read_bytes()).hexdigest() != expected
    ):
        raise SystemExit(f"Certification evidence changed: {name}")
PY
  if [ "${CERT_RETAIN_SUCCESS:-0}" = "1" ]; then
    echo "CERT_RETAIN_SUCCESS=1; retaining successful resources."
    exit 0
  fi
  "${COMPOSE[@]}" down --remove-orphans
  for volume in "$CERT_DATA_VOLUME" "$CERT_CONTROL_VOLUME" \
    "$CERT_REDIS_VOLUME" "$CERT_EMPTY_LEGACY_VOLUME"; do
    assert_not_mounted "$volume"
    resource_is_owned volume "$volume" || {
      echo "Refusing to remove unowned volume: $volume" >&2
      exit 1
    }
    docker volume rm "$volume" >/dev/null
  done
  resource_is_owned network "$CERT_INTERNAL_NETWORK" &&
    docker network rm "$CERT_INTERNAL_NETWORK" >/dev/null || true
}

if [ "$ACTION" = "cleanup" ]; then
  cleanup
  exit 0
fi
if [ "$ACTION" = "validate" ]; then
  render_and_assert_model
  exit 0
fi
if [ "$ACTION" = "activate" ]; then
  render_and_assert_model
  "${COMPOSE[@]}" stop maintenance web
  # Recreate one process role at a time. Both roles share SQLite and must never
  # race their startup migration/restore entrypoints.
  "${COMPOSE[@]}" up -d --wait --no-deps web
  "${COMPOSE[@]}" up -d --wait --no-deps maintenance
  echo "Runtime activation is enabled for the isolated certification target."
  echo "Complete the signed activation journey, then run the evidence phase."
  exit 0
fi
if [ "$ACTION" = "evidence" ]; then
  [ "${CERT_OPERATOR_ACCEPTED:-0}" = "1" ] || {
    echo "Set CERT_OPERATOR_ACCEPTED=1 only after the complete operator checklist passes" >&2
    exit 2
  }
  : "${CERT_GENERATION_ID:?CERT_GENERATION_ID is required}"
  : "${CERT_MANIFEST_DIGEST:?CERT_MANIFEST_DIGEST is required}"
  : "${CERT_ACTIVATION_INTENT_ID:?CERT_ACTIVATION_INTENT_ID is required}"
  python3 - "$CERT_GENERATION_ID" "$CERT_MANIFEST_DIGEST" \
    "$CERT_ACTIVATION_INTENT_ID" <<'PY'
import re, sys, uuid
if not re.fullmatch(r"[A-Za-z0-9._:-]+", sys.argv[1]):
    raise SystemExit("CERT_GENERATION_ID is invalid")
if not re.fullmatch(r"[0-9a-f]{64}", sys.argv[2]):
    raise SystemExit("CERT_MANIFEST_DIGEST is invalid")
try:
    valid_intent = str(uuid.UUID(sys.argv[3])) == sys.argv[3]
except ValueError:
    valid_intent = False
if not valid_intent:
    raise SystemExit("CERT_ACTIVATION_INTENT_ID is invalid")
PY
  mkdir -p "$EVIDENCE_DIR"
  rm -f \
    "$EVIDENCE_DIR/certification-passed" \
    "$EVIDENCE_DIR/certification-passed.json"
  render_and_assert_model
  assert_appuser_runtime
  write_evidence
  python3 - "$EVIDENCE_DIR/livez.txt" "$EVIDENCE_DIR/readyz.txt" \
    "$CERT_GENERATION_ID" "$CERT_MANIFEST_DIGEST" \
    >"$EVIDENCE_DIR/endpoint-identity.json" <<'PY'
import json, pathlib, sys
live = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
ready = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))
if live.get("status") != "ok":
    raise SystemExit("livez evidence is not healthy")
if (
    ready.get("status") != "ready"
    or ready.get("runtime_generation_id") != sys.argv[3]
    or ready.get("runtime_manifest_digest") != sys.argv[4]
):
    raise SystemExit("readyz runtime identity does not match acceptance")
print(json.dumps({
    "livez": live["status"],
    "readyz": ready["status"],
    "runtime_generation_id": ready["runtime_generation_id"],
    "runtime_manifest_digest": ready["runtime_manifest_digest"],
}, sort_keys=True))
PY
  initial_args=()
  if [ "$MODE" = "fresh" ]; then
    initial_args=(--require-initial)
  fi
  app_exec web python manage.py verify_recovery_certification \
    --intent-id "$CERT_ACTIVATION_INTENT_ID" \
    --generation-id "$CERT_GENERATION_ID" \
    --manifest-digest "$CERT_MANIFEST_DIGEST" \
    "${initial_args[@]}" \
    >"$EVIDENCE_DIR/certification.json"
  printf 'generation_id=%s\nmanifest_digest=%s\nactivation_intent_id=%s\noperator_accepted=yes\n' \
    "$CERT_GENERATION_ID" "$CERT_MANIFEST_DIGEST" \
    "$CERT_ACTIVATION_INTENT_ID" \
    >"$EVIDENCE_DIR/operator-acceptance.txt"
  python3 - "$EVIDENCE_DIR" "$PROJECT" "$MODE" "$PDFSEARCH_IMAGE" \
    "$CERT_GENERATION_ID" "$CERT_MANIFEST_DIGEST" \
    "$CERT_ACTIVATION_INTENT_ID" <<'PY'
import hashlib, json, os, pathlib, sys, tempfile
evidence_dir = pathlib.Path(sys.argv[1])
names = [
    "runtime.json",
    "livez.txt",
    "readyz.txt",
    "readyz-status.txt",
    "endpoint-identity.json",
    "inventory.json",
    "database.txt",
    "certification.json",
    "operator-acceptance.txt",
]
if sys.argv[3] == "fresh":
    names.append("initial-runtime-authority.json")
digests = {}
for name in names:
    path = evidence_dir / name
    if not path.is_file():
        raise SystemExit(f"Required certification evidence is absent: {name}")
    digests[name] = hashlib.sha256(path.read_bytes()).hexdigest()
document = {
    "schema_version": 1,
    "status": "passed",
    "project": sys.argv[2],
    "mode": sys.argv[3],
    "image": sys.argv[4],
    "generation_id": sys.argv[5],
    "manifest_digest": sys.argv[6],
    "activation_intent_id": sys.argv[7],
    "evidence_sha256": digests,
}
descriptor, temporary = tempfile.mkstemp(
    prefix=".certification-passed.",
    suffix=".partial",
    dir=evidence_dir,
)
try:
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(document, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, evidence_dir / "certification-passed.json")
    directory = os.open(evidence_dir, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
finally:
    pathlib.Path(temporary).unlink(missing_ok=True)
PY
  exit 0
fi

docker network inspect "$CERT_VAULT_NETWORK" >/dev/null
if [ "$(docker network inspect -f '{{.Internal}}' "$CERT_VAULT_NETWORK")" != "false" ]; then
  echo "Vault network must be dedicated but non-internal so localhost publication works" >&2
  exit 1
fi
for volume in "$CERT_DATA_VOLUME" "$CERT_CONTROL_VOLUME" \
  "$CERT_REDIS_VOLUME" "$CERT_EMPTY_LEGACY_VOLUME"; do
  create_volume "$volume"
  assert_not_mounted "$volume"
done
assert_empty_volume "$CERT_EMPTY_LEGACY_VOLUME"
if [ "$MODE" = "accumulated" ]; then
  : "${CERT_SOURCE_DATA_VOLUME:?CERT_SOURCE_DATA_VOLUME is required}"
  : "${CERT_SOURCE_CONTROL_VOLUME:?CERT_SOURCE_CONTROL_VOLUME is required}"
  [ "$CERT_SOURCE_DATA_VOLUME" != "$CERT_SOURCE_CONTROL_VOLUME" ] || {
    echo "Source data and control volumes must be distinct" >&2
    exit 1
  }
  copy_quiesced_volume "$CERT_SOURCE_DATA_VOLUME" "$CERT_DATA_VOLUME"
  copy_quiesced_volume "$CERT_SOURCE_CONTROL_VOLUME" "$CERT_CONTROL_VOLUME"
else
  assert_empty_volume "$CERT_DATA_VOLUME"
  assert_empty_volume "$CERT_CONTROL_VOLUME"
fi
render_and_assert_model
# Redis must be ready before either application role starts. Start web and
# maintenance serially because both share the disposable SQLite volumes and
# execute startup migration/restore checks.
"${COMPOSE[@]}" up -d --wait redis
"${COMPOSE[@]}" up -d --wait --no-deps web
"${COMPOSE[@]}" up -d --wait --no-deps maintenance
assert_appuser_runtime
if [ "$MODE" = "fresh" ]; then
  mkdir -p "$EVIDENCE_DIR"
  umask 077
  app_exec web python manage.py shell -c '
import json
from django.conf import settings
from vaultops.models import ArtifactGeneration
from vaultops.runtime_control import runtime_control_paths
p = runtime_control_paths(settings.DATA_CONTROL_ROOT)
assert not p["active"].exists()
assert not p["previous"].exists()
assert not ArtifactGeneration.objects.using("control").filter(
    deployment_id=settings.ENV_IDENTITY.deployment_id,
    runtime_state=ArtifactGeneration.RuntimeState.ACTIVE,
).exists()
print(json.dumps({
    "active_pointer": "absent",
    "previous_pointer": "absent",
    "active_generation_projection": "absent",
}, sort_keys=True))
' >"$EVIDENCE_DIR/initial-runtime-authority.json"
fi
write_evidence preflight
cat <<EOF
Certification target is isolated and running.
Project: $PROJECT
Evidence: $EVIDENCE_DIR

Complete the pinned-generation Workbench restore and signed activation checks.
After restore preparation succeeds, enable activation in a separate phase:
  CERT_RUN_ID=$CERT_RUN_ID CERT_WEB_PORT=$CERT_WEB_PORT \\
  CERT_VAULT_NETWORK=$CERT_VAULT_NETWORK PDFSEARCH_IMAGE=$PDFSEARCH_IMAGE \\
  $0 $MODE activate

Then run:
  CERT_RUN_ID=$CERT_RUN_ID CERT_WEB_PORT=$CERT_WEB_PORT \\
  CERT_VAULT_NETWORK=$CERT_VAULT_NETWORK PDFSEARCH_IMAGE=$PDFSEARCH_IMAGE \\
  $0 $MODE evidence
EOF
