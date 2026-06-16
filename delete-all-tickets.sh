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
  python3 -c "import sqlite3; print(sqlite3.connect('${DB_PATH}').execute('SELECT COUNT(*) FROM incidents').fetchone()[0])") || {
  echo "Error: could not query the database on pod '${POD}'"
  exit 1
}

if [ "${COUNT}" -eq 0 ]; then
  echo "No tickets to delete."
  exit 0
fi

echo "Deleting ${COUNT} ticket(s)..."

oc exec -n "${NAMESPACE}" "${POD}" -- \
  python3 -c "
import sqlite3
conn = sqlite3.connect('${DB_PATH}')
conn.execute('DELETE FROM work_notes')
conn.execute('DELETE FROM incidents')
conn.commit()
conn.close()
"

echo "Done. All tickets have been deleted."
