#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-coding-assistant}"
DB_PATH="${TICKETING_DB_PATH:-/tmp/ticketing/incidents.db}"

POD=$(oc get pods -n "${NAMESPACE}" -l app=ticketing-system \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null) || {
  echo "Error: could not find a ticketing-system pod in namespace '${NAMESPACE}'"
  exit 1
}

echo "Found ticketing-system pod: ${POD}"

COUNT=$(oc exec -n "${NAMESPACE}" "${POD}" -- \
  sqlite3 "${DB_PATH}" "SELECT COUNT(*) FROM incidents;" 2>/dev/null) || {
  echo "Error: could not query the database on pod '${POD}'"
  exit 1
}

if [ "${COUNT}" -eq 0 ]; then
  echo "No tickets to delete."
  exit 0
fi

echo "Deleting ${COUNT} ticket(s)..."

oc exec -n "${NAMESPACE}" "${POD}" -- \
  sqlite3 "${DB_PATH}" "DELETE FROM work_notes; DELETE FROM incidents;"

echo "Done. All tickets have been deleted."
