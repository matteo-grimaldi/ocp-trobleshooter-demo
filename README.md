# OCP AI Troubleshooter Demo

An AI agent that autonomously troubleshoots applications deployed on OpenShift.  
It combines **Nemotron 3 Nano 30B** (via the cluster's MaaS gateway), the **OpenShift MCP server**, and **Prometheus / Thanos** to diagnose application issues in real time.

---

## Architecture

```
User / Browser
     │  chat                          │  dashboard
     ▼                                ▼
┌──────────────────────────────┐  ┌──────────────────────────┐
│  AI Agent  (Gradio UI)       │  │  Ticketing System        │
│  LangChain ReAct + LangGraph │  │  (ServiceNow Simulator)  │
└──────┬───────────┬───────────┘  └──────────────────────────┘
       │           │                  ▲
       │ OpenAI    │ MCP tools        │ REST API
       ▼           ▼                  │
  Nemotron     OpenShift MCP   ┌──────────────────────┐
  3 Nano 30B   server          │ Ticketing MCP Server │  ← OpenShift AI
  (MaaS)       (k8s API)       │ (MCP catalog)        │    MCP catalog
               │               └──────────────────────┘
               │
           Thanos Querier
           (openshift-monitoring)
                    │ scrapes
                    ▼
           ┌──────────────────┐
           │ Quarkus Buggy App │  demo-app namespace
           │ /api/products  500│
           │ /api/orders  delay│
           │ /api/inventory 503│
           └──────────────────┘
```

---

## Components

### 1. Quarkus Buggy App (`quarkus-buggy-app/`)

A Quarkus 3 REST microservice with intentional, randomly-triggered failures:

| Endpoint | Failure | Rate | HTTP Code |
|---|---|---|---|
| `GET /api/products` | NullPointerException | 30% | 500 |
| `GET /api/orders` | 3-second sleep | 20% | 200 (slow) |
| `GET /api/inventory` | ServiceUnavailable | 40% | 503 |

- Exposes `/q/metrics` (Micrometer + Prometheus format)
- Exposes `/q/health` (SmallRye Health liveness + readiness)
- Includes a `TrafficGenerator` that calls all endpoints every 5 seconds to produce continuous metrics

### 2. Ticketing System (`ticketing-system/`)

A lightweight **ServiceNow Table API simulator** for incident management. Provides a REST API that mimics ServiceNow's `incident` table, backed by SQLite.

**API endpoints:**

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/incidents` | Create a new incident |
| `GET` | `/api/incidents` | List incidents (filter by `state`, `category`, `priority`) |
| `GET` | `/api/incidents/{number}` | Get a single incident with work notes |
| `PATCH` | `/api/incidents/{number}` | Update incident fields (state, priority, assignment, etc.) |
| `POST` | `/api/incidents/{number}/notes` | Add a work note to an incident |
| `GET` | `/` | HTML dashboard showing all incidents |
| `GET` | `/health` | Health check |

**Incident fields** (ServiceNow-compatible):

| Field | Example | Description |
|---|---|---|
| `number` | `INC0000001` | Auto-generated incident ID |
| `state` | `New` | New / In Progress / On Hold / Resolved / Closed |
| `impact` | `1` | 1 (High) / 2 (Medium) / 3 (Low) |
| `urgency` | `2` | 1 (High) / 2 (Medium) / 3 (Low) |
| `priority` | `2` | Auto-calculated from impact + urgency (1-5) |
| `short_description` | `High error rate on /api/products` | Brief summary |
| `description` | *(full diagnosis)* | Detailed report body |
| `category` | `Application` | Application / Infrastructure / Network / Database |
| `caller_id` | `ocp-troubleshooter` | Who reported the incident |
| `opened_at` | `2025-01-15 07:00:00` | Creation timestamp (UTC) |

### 3. Ticketing MCP Server (`ticketing-mcp-server/`)

A standalone **MCP server** that wraps the ticketing system REST API as MCP tools. Deployed as its own service and registered in the **OpenShift AI MCP catalog** so any agent on the platform can discover and use it.

**MCP tools exposed:**

| Tool | Description |
|---|---|
| `create_incident` | Create a new incident with auto-generated INC number and priority |
| `list_incidents` | List/filter incidents by state, category, priority |
| `get_incident` | Get full incident details including work notes |
| `update_incident` | Update state, assignment, severity, close notes |
| `add_work_note` | Append a timestamped work note to an incident |

- **Transport:** `streamable-http` at `/mcp`
- **In-cluster URL:** `http://ticketing-mcp-server.coding-assistant.svc:8080/mcp`

**OpenShift AI integration (two options):**

| Mechanism | File | RHOAI version | What it does |
|---|---|---|---|
| `MCPServer` CRD | `k8s/mcpserver.yaml` | 3.4+ | The MCP lifecycle operator creates the Deployment, Service, and probes; the server appears in the MCP catalog automatically |
| `gen-ai-aa-mcp-servers` ConfigMap | `k8s/mcp-catalog-entry.yaml` | 3.0+ | Registers an already-deployed server in the GenAI Playground UI |

`build-and-deploy.sh` detects which mechanism is available and applies the right one.

### 4. AI Troubleshooter Agent (`ai-agent/`)

A Python LangChain ReAct agent with a Gradio web UI.

**Tools available to the agent:**
- **OpenShift MCP tools** (via `langchain-mcp-adapters`) — pods, deployments, events, logs
- `query_prometheus(promql)` — instant PromQL query against Thanos Querier
- **Ticketing MCP tools** — `create_incident`, `list_incidents`, `get_incident`, `update_incident`, `add_work_note`
- `query_prometheus_range(promql, duration_minutes)` — range query with min/avg/max summary

**LLM:** Nemotron 3 Nano 30B via `https://<your-maas-endpoint>`

---

## Prerequisites

- OpenShift cluster with:
  - User workload monitoring enabled (already done: `enableUserWorkload: true`)
  - `openshift-mcp` server running in `coding-assistant` namespace
  - Nemotron model deployed via MaaS
- `oc` CLI logged in as cluster-admin
- `podman` (or Docker) for building images
- Java 17+ and Maven (for Quarkus build)

---

## Deploy: Step by Step

### Step 1 — Deploy the Quarkus Buggy App

```bash
cd quarkus-buggy-app

# Create the demo-app namespace and ServiceMonitor
oc apply -f k8s/namespace.yaml
oc apply -f k8s/service-monitor.yaml

# Build and push the image to the OpenShift internal registry
oc registry login --skip-check
./mvnw package \
  -Dquarkus.container-image.build=true \
  -Dquarkus.container-image.push=true \
  -Dquarkus.container-image.insecure=true \
  -DskipTests

# Deploy
oc apply -f k8s/deployment.yaml

# Verify
oc get pods -n demo-app
oc get route buggy-demo-app -n demo-app
```

Wait ~30 seconds for the app to start generating errors.  
Visit the Route URL to hit the endpoints manually.

### Step 2 — Deploy the Ticketing System

```bash
cd ticketing-system

# Build and push the image to the OpenShift internal registry
REGISTRY_HOST=$(oc get route default-route -n openshift-image-registry \
  -o jsonpath='{.spec.host}')
podman build --platform linux/amd64 \
  -t "${REGISTRY_HOST}/coding-assistant/ticketing-system:latest" .
podman push --tls-verify=false \
  "${REGISTRY_HOST}/coding-assistant/ticketing-system:latest"

# Deploy
oc apply -f k8s/deployment.yaml
oc apply -f k8s/route.yaml

# Verify
oc get pods -n coding-assistant | grep ticketing
oc get route ticketing-system -n coding-assistant
```

Or use the all-in-one script:

```bash
cd ticketing-system
./build-and-deploy.sh
```

Open the Route URL in a browser to see the incident dashboard.

### Step 3 — Deploy the Ticketing MCP Server

```bash
cd ticketing-mcp-server
./build-and-deploy.sh
```

The script auto-detects whether the MCP lifecycle operator is installed:
- **RHOAI 3.4+** (operator present) — applies `k8s/mcpserver.yaml`; the operator creates the Deployment and Service and the server appears in the MCP catalog
- **RHOAI 3.0–3.3** (no operator) — applies `k8s/deployment.yaml` + `k8s/route.yaml` as a manual Deployment

In both cases the script also applies `k8s/mcp-catalog-entry.yaml` to register the server in the GenAI Playground.

```bash
# Verify
oc get pods -n coding-assistant | grep ticketing-mcp
# If using the MCPServer CRD:
oc get mcpserver ticketing-mcp-server -n coding-assistant
```

### Step 4 — Deploy the AI Agent

```bash
cd ai-agent

# Apply RBAC (ServiceAccount + cluster-monitoring-view binding)
oc apply -f k8s/rbac.yaml

# Build and push the agent image
# Option A — if the internal registry has an external route:
REGISTRY_HOST=$(oc get route default-route -n openshift-image-registry \
  -o jsonpath='{.spec.host}')
podman build -t "${REGISTRY_HOST}/coding-assistant/ocp-troubleshooter:latest" .
podman push --tls-verify=false \
  "${REGISTRY_HOST}/coding-assistant/ocp-troubleshooter:latest"

# Deploy
oc apply -f k8s/deployment.yaml
oc apply -f k8s/route.yaml

# Verify
oc get pods -n coding-assistant | grep troubleshooter
oc get route ocp-troubleshooter -n coding-assistant
```

### Step 5 — Run the Demo

1. Open the agent Route URL in a browser.
2. Use one of the quick-start example prompts, e.g.:

   > *"Troubleshoot the application in the demo-app namespace. Check pod health, look at Prometheus metrics for error rates and latency, retrieve logs, and give me a full diagnosis."*

3. Watch the agent:
   - Call `list_pods` or equivalent MCP tool → sees the running pod
   - Call `query_prometheus` → sees 30% error rate on `/api/products`, 40% on `/api/inventory`
   - Call `get_pod_logs` → sees NPE stack traces and "stock sync" error messages
   - Output a structured diagnosis with root cause and recommended fix

---

## Demo Script (Suggested Walkthrough)

### Scene 1 — Full Health Check

Prompt:
```
Troubleshoot the application in the demo-app namespace. Check pod health, 
look at Prometheus metrics for error rates and latency, retrieve logs, 
and give me a full diagnosis.
```

Expected diagnosis:
- Pod is Running but generating errors
- `/api/products`: ~30% HTTP 500 (NullPointerException in logs)
- `/api/inventory`: ~40% HTTP 503 (stock sync errors)
- `/api/orders`: p99 latency spike to ~3s (20% slow queries)

### Scene 2 — Targeted Metric Query

Prompt:
```
What HTTP endpoints in demo-app are returning 5xx errors right now?
Show me the error rates from Prometheus for the last 30 minutes.
```

### Scene 3 — Log Investigation

Prompt:
```
Get the last 50 lines of logs from the buggy-demo-app pod in demo-app 
and explain what errors you see.
```

### Scene 4 — Ticketing System API

Create an incident manually via the REST API:

```bash
TICKETING_URL=$(oc get route ticketing-system -n coding-assistant \
  -o jsonpath='https://{.spec.host}')

# Create an incident
curl -s -X POST "${TICKETING_URL}/api/incidents" \
  -H "Content-Type: application/json" \
  -d '{
    "short_description": "High 5xx error rate on /api/products",
    "description": "30% of requests to /api/products return HTTP 500 due to NullPointerException in ProductResource.java",
    "impact": 1,
    "urgency": 2,
    "category": "Application",
    "caller_id": "ocp-troubleshooter"
  }' | python3 -m json.tool

# List all incidents
curl -s "${TICKETING_URL}/api/incidents" | python3 -m json.tool

# Open the dashboard in a browser
open "${TICKETING_URL}"
```

---

## Local Development

### Agent

```bash
cd ai-agent
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Point to the real cluster services (requires oc port-forward or kubeconfig)
export OCP_MCP_URL=http://localhost:8000/mcp
export THANOS_URL=https://thanos-querier.openshift-monitoring.svc:9091
export PROMETHEUS_TOKEN=$(oc whoami --show-token)
export NEMOTRON_BASE_URL=https://<your-maas-endpoint>/maas/nemotron-3-nano-30b-a3b/v1

python app.py
# Open http://localhost:7860
```

### Ticketing System

```bash
cd ticketing-system
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

uvicorn app:app --host 0.0.0.0 --port 8080 --reload
# Dashboard: http://localhost:8080
# API docs:  http://localhost:8080/docs
```

---

## Project Structure

```
ocp-troubleshooter-demo/
├── quarkus-buggy-app/
│   ├── pom.xml
│   ├── build-and-deploy.sh
│   ├── src/main/java/com/demo/
│   │   ├── ProductResource.java     ← 30% NPE → 500
│   │   ├── OrderResource.java       ← 20% sleep → latency
│   │   ├── InventoryResource.java   ← 40% → 503
│   │   └── TrafficGenerator.java    ← background load
│   ├── src/main/resources/
│   │   └── application.properties
│   └── k8s/
│       ├── namespace.yaml
│       ├── deployment.yaml          ← Deployment + Service + Route
│       └── service-monitor.yaml     ← Prometheus scraping
├── ticketing-system/
│   ├── app.py                       ← FastAPI ServiceNow simulator
│   ├── requirements.txt
│   ├── Dockerfile
│   ├── build-and-deploy.sh
│   └── k8s/
│       ├── deployment.yaml          ← Deployment + PVC + Service
│       └── route.yaml               ← External route (dashboard)
├── ticketing-mcp-server/
│   ├── server.py                    ← FastMCP server (MCP catalog)
│   ├── requirements.txt
│   ├── Dockerfile
│   ├── build-and-deploy.sh
│   └── k8s/
│       ├── mcpserver.yaml           ← MCPServer CR (RHOAI 3.4+, operator-managed)
│       ├── mcp-catalog-entry.yaml   ← GenAI Playground registration (RHOAI 3.0+)
│       ├── deployment.yaml          ← Manual fallback Deployment + Service
│       └── route.yaml               ← Optional external route
└── ai-agent/
    ├── app.py                       ← Gradio web UI
    ├── agent.py                     ← LangChain ReAct agent
    ├── tools/
    │   ├── openshift_mcp.py         ← MCP client (langchain-mcp-adapters)
    │   └── prometheus.py            ← PromQL tools (Thanos)
    ├── requirements.txt
    ├── Dockerfile
    ├── build-and-deploy.sh
    └── k8s/
        ├── rbac.yaml                ← ServiceAccount + monitoring RBAC
        ├── deployment.yaml          ← Deployment + Service
        └── route.yaml               ← External route
```

---

## Key Configuration (Environment Variables)

| Variable | Default | Description |
|---|---|---|
| `OCP_MCP_URL` | `http://openshift-mcp.coding-assistant.svc:8000/mcp` | OpenShift MCP server URL |
| `THANOS_URL` | `https://thanos-querier.openshift-monitoring.svc:9091` | Thanos Querier base URL |
| `NEMOTRON_BASE_URL` | `https://maas.apps.../maas/nemotron-3-nano-30b-a3b/v1` | Nemotron API base URL |
| `NEMOTRON_MODEL` | `nemotron-3-nano-30b-a3b` | Model name |
| `NEMOTRON_API_KEY` | `fake` | API key (MaaS uses bearer token auth internally) |
| `PROMETHEUS_TOKEN` | *(SA token from mount)* | Override bearer token for Prometheus |
| `TICKETING_MCP_URL` | `http://ticketing-mcp-server.coding-assistant.svc:8080/mcp` | Ticketing MCP server URL |
| `GRADIO_PORT` | `7860` | Gradio server port |

### Ticketing System

| Variable | Default | Description |
|---|---|---|
| `TICKETING_DB_PATH` | `/tmp/ticketing/incidents.db` | SQLite database file path |

### Ticketing MCP Server

| Variable | Default | Description |
|---|---|---|
| `TICKETING_API_URL` | `http://ticketing-system.coding-assistant.svc:8080` | Ticketing system REST API base URL |
| `MCP_PORT` | `8080` | Port for the MCP streamable-http transport |
