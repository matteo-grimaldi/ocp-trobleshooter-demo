#!/usr/bin/env bash
# build-and-deploy.sh — Build and deploy the ServiceNow Incident Simulator
set -euo pipefail

NAMESPACE="coding-assistant"
APP="ticketing-system"

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

echo "=== Deploying manifests ==="
oc apply -f k8s/deployment.yaml
oc apply -f k8s/route.yaml

echo "=== Restarting deployment ==="
oc rollout restart deployment/${APP} -n ${NAMESPACE}

echo "=== Waiting for rollout ==="
oc rollout status deployment/${APP} -n ${NAMESPACE} --timeout=120s

echo ""
echo "=== Ticketing system is live at ==="
oc get route ${APP} -n ${NAMESPACE} -o jsonpath='https://{.spec.host}{"\n"}'
