#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
ci_project="pt-reimbursement-ci-$(date +%s)-$$"
ci_compose=(docker compose --env-file /dev/null --project-name "$ci_project" --file compose.ci.yaml)
trap '"${ci_compose[@]}" down --volumes --remove-orphans' EXIT

"${ci_compose[@]}" build checks
"${ci_compose[@]}" run --rm checks
docker build --target runtime --tag pt-reimbursement:local .
