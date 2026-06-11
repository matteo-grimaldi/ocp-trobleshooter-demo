#!/usr/bin/env bash
# build-and-deploy-all.sh — Build and deploy every component in the correct order.
#
# Usage:
#   ./build-and-deploy-all.sh                                  # deploy everything
#   ./build-and-deploy-all.sh --maas-hostname <hostname>       # override maas_hostname
#   ./build-and-deploy-all.sh <component>                      # deploy a single component
#   ./build-and-deploy-all.sh --maas-hostname <hostname> <component>
#   MAAS_HOSTNAME=<value> ./build-and-deploy-all.sh            # override via env var
#
# Components (in dependency order):
#   1. ticketing-system        — backend REST API (no deps)
#   2. quarkus-buggy-app       — demo app to troubleshoot (no deps)
#   3. ticketing-mcp-server    — MCP server wrapping ticketing-system
#   4. prometheus-mcp-server   — MCP server wrapping Thanos Querier
#   5. ai-agent                — Gradio UI + OGX (depends on MCP servers)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAMESPACE="coding-assistant"
MAAS_HOSTNAME="${MAAS_HOSTNAME:-}"
COMPONENT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --maas-hostname) MAAS_HOSTNAME="$2"; shift 2 ;;
    *) COMPONENT="$1"; shift ;;
  esac
done

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

banner() { echo -e "\n${BLUE}══════════════════════════════════════════════════${NC}"; echo -e "${BLUE}  $1${NC}"; echo -e "${BLUE}══════════════════════════════════════════════════${NC}\n"; }
ok()     { echo -e "${GREEN}✔ $1${NC}"; }
fail()   { echo -e "${RED}✘ $1${NC}"; }

deploy_component() {
  local component="$1"
  local dir="${SCRIPT_DIR}/${component}"

  if [ ! -d "${dir}" ]; then
    fail "Directory not found: ${dir}"
    return 1
  fi

  if [ ! -f "${dir}/build-and-deploy.sh" ]; then
    fail "No build-and-deploy.sh in ${dir}"
    return 1
  fi

  banner "Deploying: ${component}"
  (cd "${dir}" && MAAS_HOSTNAME="${MAAS_HOSTNAME}" bash build-and-deploy.sh)
  ok "${component} deployed"
}

# ── Pre-flight checks ──────────────────────────────────────────────────────

banner "Pre-flight checks"

if ! command -v oc &>/dev/null; then
  fail "'oc' CLI not found — install it first"
  exit 1
fi

if ! oc whoami &>/dev/null; then
  fail "Not logged into an OpenShift cluster — run 'oc login' first"
  exit 1
fi

echo "  Cluster:   $(oc whoami --show-server)"
echo "  User:      $(oc whoami)"
echo "  Namespace: ${NAMESPACE}"

if ! oc get namespace "${NAMESPACE}" &>/dev/null; then
  echo "  Creating namespace ${NAMESPACE}..."
  oc new-project "${NAMESPACE}" || oc create namespace "${NAMESPACE}"
fi

ok "Pre-flight passed"

# ── Apply shared cluster config ────────────────────────────────────────────

banner "Applying shared cluster config"
CONFIG_FILE="${SCRIPT_DIR}/ai-agent/k8s/cluster-config.yaml"
if [ -n "${MAAS_HOSTNAME}" ]; then
  echo "  Overriding maas_hostname: ${MAAS_HOSTNAME}"
  oc create configmap cluster-config \
    --from-literal="maas_hostname=${MAAS_HOSTNAME}" \
    -n "${NAMESPACE}" \
    --dry-run=client -o yaml | oc apply -f -
else
  MAAS_HOSTNAME=$(grep 'maas_hostname:' "${CONFIG_FILE}" | awk '{print $2}' | tr -d '"')
  echo "  Using maas_hostname from cluster-config.yaml: ${MAAS_HOSTNAME}"
  oc apply -f "${CONFIG_FILE}"
fi
ok "cluster-config applied"

# ── Single-component mode ──────────────────────────────────────────────────

if [ -n "${COMPONENT}" ]; then
  deploy_component "${COMPONENT}"
  echo ""
  ok "Done — deployed ${COMPONENT}"
  exit 0
fi

# ── Full deploy (dependency order) ─────────────────────────────────────────

COMPONENTS=(
  ticketing-system
  quarkus-buggy-app
  ticketing-mcp-server
  prometheus-mcp-server
  ai-agent
)

for component in "${COMPONENTS[@]}"; do
  deploy_component "${component}"
done

echo ""
banner "All components deployed"
echo "  Agent UI: $(oc get route ocp-troubleshooter -n ${NAMESPACE} -o jsonpath='https://{.spec.host}' 2>/dev/null || echo 'route not found')"
