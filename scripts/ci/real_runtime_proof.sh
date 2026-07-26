#!/usr/bin/env bash
# Compatibility entrypoint for the isolated lifecycle/process-death proof.
#
# This script intentionally never targets an existing application container.
# Callers must provide the test application image and digest-pinned MinIO and
# Redis images; run_vault_integration.sh owns a uniquely named disposable stack.
set -euo pipefail

exec bash "$(dirname "$0")/run_vault_integration.sh"
