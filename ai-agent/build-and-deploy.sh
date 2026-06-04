#!/usr/bin/env bash
# build-and-deploy.sh — Build and deploy the OCP Troubleshooter agent
set -euo pipefail

NAMESPACE="coding-assistant"
APP="ocp-troubleshooter"

echo "=== Applying RBAC ==="
oc apply -f k8s/rbac.yaml

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
# ConfigMap must be created from file to avoid YAML-within-YAML parsing issues
oc create configmap ogx-stack-config \
  --from-file=stack_run_config.yaml=ogx/stack_run_config.yaml \
  -n "${NAMESPACE}" \
  --dry-run=client -o yaml | oc apply -f -
oc create configmap agent-knowledge \
  --from-file=knowledge.md=knowledge.md \
  -n "${NAMESPACE}" \
  --dry-run=client -o yaml | oc apply -f -
oc apply -f k8s/deployment.yaml
oc apply -f k8s/route.yaml

echo "=== Waiting for rollout ==="
oc rollout status deployment/${APP} -n ${NAMESPACE} --timeout=180s

echo ""
echo "=== Agent is live at ==="
oc get route ${APP} -n ${NAMESPACE} -o jsonpath='https://{.spec.host}{"\n"}'
