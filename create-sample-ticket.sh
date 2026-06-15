#!/usr/bin/env bash
set -euo pipefail

TICKETING_URL="${TICKETING_URL:-$(oc get route ticketing-system -n coding-assistant \
  -o jsonpath='https://{.spec.host}')}"

echo "Creating sample ticket against: ${TICKETING_URL}"

curl -s -X POST "${TICKETING_URL}/api/incidents" \
  -H "Content-Type: application/json" \
  -d '{
    "short_description": "App is acting weird, please help",
    "description": "Hello, I am not very technical but I wanted to report that the application we use (I think it is called buggy-demo-app?) has been giving me trouble for the past couple of days. Sometimes when I click on things I get a white page with an error message, other times the page just takes forever to load and I end up refreshing. There was also one time where it said something about the service being unavailable. I am not sure if it is my computer or the app itself. A few of my colleagues said they are seeing the same thing so I thought I should report it. I do not know what logs or technical details to provide, sorry. Could someone from the IT team please take a look? It is really affecting our work. Thank you!",
    "impact": 2,
    "urgency": 2,
    "category": "Application",
    "caller_id": "jdoe"
  }' | python3 -m json.tool

echo ""
echo "Ticket created. Use the returned INC number to trigger the AI Ticketing Agent."
