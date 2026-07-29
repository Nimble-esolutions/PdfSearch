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
  run_recovery_certification.sh fresh validate
  run_recovery_certification.sh <fresh|accumulated> evidence
  run_recovery_certification.sh <fresh|accumulated> cleanup

Required environment:
  PDFSEARCH_IMAGE         immutable repository@sha256 image
  CERT_VAULT_NETWORK      existing Docker network that reaches only the Vault
  CERT_WEB_PORT           unused localhost port

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
  fresh:start|fresh:validate|fresh:evidence|fresh:cleanup|accumulated:start|accumulated:validate|accumulated:evidence|accumulated:cleanup) ;;
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
  python3 - "$CERT_DATA_VOLUME" "$CERT_CONTROL_VOLUME" \
    "$CERT_EMPTY_LEGACY_VOLUME" "$CERT_REDIS_VOLUME" \
    "$CERT_INTERNAL_NETWORK" "$CERT_VAULT_NETWORK" <<'PY'
import json, os, sys
expected = {
    "/app/data": "cert_data",
    "/app/data-control": "cert_control",
    "/mnt/legacy": "cert_empty_legacy",
}
model = json.loads(os.environ["RENDERED_CONFIG"])
expected_resources = {
    "cert_data": sys.argv[1],
    "cert_control": sys.argv[2],
    "cert_empty_legacy": sys.argv[3],
    "cert_redis": sys.argv[4],
}
for key, name in expected_resources.items():
    if model["volumes"][key].get("name") != name:
        raise SystemExit(f"{key}: unexpected external volume name")
expected_networks = {
    "cert_internal": sys.argv[5],
    "cert_vault": sys.argv[6],
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
  "${COMPOSE[@]}" exec -T web python manage.py shell -c '
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
  "${COMPOSE[@]}" exec -T web python - <<'PY' >"$EVIDENCE_DIR/database.txt"
from django.db import connection
with connection.cursor() as cursor:
    cursor.execute("PRAGMA integrity_check")
    print("integrity", cursor.fetchone()[0])
    cursor.execute("PRAGMA foreign_key_check")
    print("foreign_key_violations", len(cursor.fetchall()))
PY
  echo "Bounded evidence written to $EVIDENCE_DIR"
}

cleanup() {
  mkdir -p "$EVIDENCE_DIR"
  if [ ! -f "$EVIDENCE_DIR/certification-passed" ]; then
    echo "Certification has not passed; retaining project and volumes for review." >&2
    exit 1
  fi
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
  render_and_assert_model
  write_evidence
  "${COMPOSE[@]}" exec -T web python manage.py verify_activation_runtime \
    --intent-id "$CERT_ACTIVATION_INTENT_ID" \
    >"$EVIDENCE_DIR/activation-runtime.txt"
  "${COMPOSE[@]}" exec -T \
    -e CERT_EXPECTED_GENERATION_ID="$CERT_GENERATION_ID" \
    -e CERT_EXPECTED_MANIFEST_DIGEST="$CERT_MANIFEST_DIGEST" \
    web python manage.py shell -c '
import json, os
from django.conf import settings
from vaultops.runtime_control import read_runtime_pointer, runtime_control_paths
p = runtime_control_paths(settings.DATA_CONTROL_ROOT)
active = read_runtime_pointer(
    p["active"],
    deployment_id=settings.ENV_IDENTITY.deployment_id,
    signing_key=settings.ACTIVATION_INTENT_SIGNING_KEY,
    runtime_root=settings.RUNTIME_GENERATIONS_ROOT,
)
assert active.generation_id == os.environ["CERT_EXPECTED_GENERATION_ID"]
assert active.manifest_digest == os.environ["CERT_EXPECTED_MANIFEST_DIGEST"]
print(json.dumps({
    "generation_id": active.generation_id,
    "manifest_digest": active.manifest_digest,
    "pointer_digest": active.pointer_digest,
}, sort_keys=True))
' >"$EVIDENCE_DIR/active-pointer.json"
  printf 'generation_id=%s\nmanifest_digest=%s\nactivation_intent_id=%s\noperator_accepted=yes\n' \
    "$CERT_GENERATION_ID" "$CERT_MANIFEST_DIGEST" \
    "$CERT_ACTIVATION_INTENT_ID" \
    >"$EVIDENCE_DIR/operator-acceptance.txt"
  touch "$EVIDENCE_DIR/certification-passed"
  exit 0
fi

docker network inspect "$CERT_VAULT_NETWORK" >/dev/null
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
"${COMPOSE[@]}" up -d --wait redis maintenance web
write_evidence preflight
cat <<EOF
Certification target is isolated and running.
Project: $PROJECT
Evidence: $EVIDENCE_DIR

Complete the pinned-generation Workbench restore and signed activation checks.
Then run:
  CERT_RUN_ID=$CERT_RUN_ID CERT_WEB_PORT=$CERT_WEB_PORT \\
  CERT_VAULT_NETWORK=$CERT_VAULT_NETWORK PDFSEARCH_IMAGE=$PDFSEARCH_IMAGE \\
  $0 $MODE evidence
EOF
