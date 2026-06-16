#!/usr/bin/env bash
# build-and-deploy.sh — Build Quarkus app and deploy to OpenShift
set -euo pipefail

NAMESPACE="demo-app"
APP="working-demo-app"

echo "=== Creating namespace ==="
oc apply -f k8s/namespace.yaml

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

echo "=== Building Quarkus app and pushing to OpenShift registry ==="
./mvnw package \
  -Dquarkus.container-image.build=true \
  -Dquarkus.container-image.push=true \
  -Dquarkus.container-image.registry="${REGISTRY_HOST}" \
  -Dquarkus.container-image.group="${NAMESPACE}" \
  -Dquarkus.container-image.name="${APP}" \
  -Dquarkus.container-image.tag=latest \
  -Dquarkus.container-image.insecure=true \
  -DskipTests

echo "=== Deploying manifests ==="
oc apply -f k8s/deployment.yaml
oc apply -f k8s/service-monitor.yaml

echo "=== Restarting deployment ==="
oc rollout restart deployment/${APP} -n ${NAMESPACE}

echo "=== Waiting for rollout ==="
oc rollout status deployment/${APP} -n ${NAMESPACE} --timeout=180s

echo ""
echo "=== App is live at ==="
oc get route ${APP} -n ${NAMESPACE} -o jsonpath='https://{.spec.host}{"\n"}'