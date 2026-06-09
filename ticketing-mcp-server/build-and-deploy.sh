#!/usr/bin/env bash
# build-and-deploy.sh — Build and deploy the Ticketing MCP Server
set -euo pipefail

NAMESPACE="coding-assistant"
APP="ticketing-mcp-server"

echo "=== Getting external registry route ==="
REGISTRY_HOST=$(oc get route default-route -n openshift-image-registry \
  -o jsonpath='{.spec.host}' 2>/dev/null || true)

if [ -z "${REGISTRY_HOST}" ]; then
  echo "  Enabling external registry route..."
  oc patch configs.imageregistry.operator.openshift.io/cluster \
    --patch='{"spec":{"defaultRoute":true}}' --type=merge
  sleep 15
  REGISTRY_HOST=$(oc get route default-route -n openshift-image-registry \
    -o jsonpath='{.spec.host}')
fi
echo "  Registry: ${REGISTRY_HOST}"

echo "=== Logging into external registry ==="
oc registry login --skip-check --registry="${REGISTRY_HOST}"

BUILD_IMAGE="${REGISTRY_HOST}/${NAMESPACE}/${APP}:latest"

echo "=== Building container image ==="
podman build --platform linux/amd64 -t "${BUILD_IMAGE}" .

echo "=== Pushing image ==="
podman push --tls-verify=false "${BUILD_IMAGE}"

echo "=== Deploying MCP server ==="
# Try the MCPServer CRD first (requires MCP lifecycle operator, RHOAI 3.4+).
# Falls back to manual Deployment + Service if the CRD is not installed.
if oc api-resources --api-group=mcp.x-k8s.io 2>/dev/null | grep -q mcpservers; then
  echo "  MCPServer CRD detected — using operator-managed deployment"
  oc apply -f k8s/mcpserver.yaml
else
  echo "  MCPServer CRD not found — using manual deployment"
  oc apply -f k8s/deployment.yaml
  oc apply -f k8s/route.yaml

  echo "=== Waiting for rollout ==="
  oc rollout status deployment/${APP} -n ${NAMESPACE} --timeout=120s
fi

echo "=== Registering in GenAI Playground MCP catalog ==="
CATALOG_NS="redhat-ods-applications"
CATALOG_CM="gen-ai-aa-mcp-servers"
ENTRY_VALUE='{"url":"http://ticketing-mcp-server.coding-assistant.svc:8080/mcp","description":"ServiceNow-style incident management. Create, query, update, and close incidents with work notes."}'

if oc get configmap "${CATALOG_CM}" -n "${CATALOG_NS}" &>/dev/null; then
  echo "  Patching existing ${CATALOG_CM} (preserving other entries)"
  ESCAPED=$(printf '%s' "${ENTRY_VALUE}" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')
  oc patch configmap "${CATALOG_CM}" -n "${CATALOG_NS}" --type=merge \
    -p "{\"data\":{\"Ticketing-System\":${ESCAPED}}}"
else
  echo "  Creating ${CATALOG_CM}"
  oc create configmap "${CATALOG_CM}" -n "${CATALOG_NS}" \
    --from-literal="Ticketing-System=${ENTRY_VALUE}"
fi

echo ""
echo "=== Ticketing MCP server is live ==="
echo "  In-cluster: http://${APP}.${NAMESPACE}.svc:8080/mcp"
if oc get route ${APP} -n ${NAMESPACE} &>/dev/null; then
  echo "  External:   $(oc get route ${APP} -n ${NAMESPACE} -o jsonpath='https://{.spec.host}/mcp')"
fi
