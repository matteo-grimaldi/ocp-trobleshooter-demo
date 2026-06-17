# AI-Driven Incident Management on OpenShift

A demo of **L1-style incident management** powered by AI agents on **OpenShift AI** and **OGX**.  
Two autonomous agents — one proactive, one reactive — collaborate with a shared ticketing system and the same MCP tool servers to detect, diagnose, and document application issues on an OpenShift cluster.

| Agent | Role | Trigger |
|---|---|---|
| **AI Troubleshooter** (`ai-agent/`) | Proactively scans a namespace, diagnoses issues, and **creates** new incidents | User asks "troubleshoot this app" |
| **AI Ticketing Agent** (`ai-ticketing-agent/`) | Picks up a **human-created** incident, investigates the reported problem, and **updates** the ticket with findings and resolution | User provides an incident ID (e.g. `INC0000001`) |

Both agents use the same LLM (**Nemotron 3 Nano 30B** via MaaS), the same MCP tool servers, and the same knowledge base — only the system prompt and workflow differ.

---

## Demo Scenario

The purpose of this demo is to demonstrate how to use AI agents and MCP Servers deployed on OpenShift AI to troubleshoot applications issues.
In this simplified scenario there will be the following components: 
1. **The buggy app** generates continuous errors (HTTP 500s, 503s, high latency).
2. **The working app** runs the same endpoints without any faults, serving as a healthy baseline.
3. **AI Troubleshooter** proactively detects and diagnoses these issues, opening incidents automatically.
3. **AI Ticketing Agent** picks up the human-created incident, investigates using real cluster data, and enriches the ticket with a full diagnosis and 
recommended fix.
4. **Ticketing System** persists opened tickets and allows creating new tickets or updating existing ones
5. **MCP Servers** allows agents to investigate issues and to interact with the ticketing system


---

## Agent Architectures

Both agents share the same runtime pattern: a **Gradio web UI** container paired with an **OGX sidecar** container in the same pod. The OGX sidecar handles the server-side agentic loop (tool calling, inference) via the Responses API.

### AI Troubleshooter Agent

Proactive agent — scans a namespace, finds problems, creates tickets.

```
User / Browser
     │ "Troubleshoot demo-app"
     ▼
┌─────────────────────────────────┐
│  AI Troubleshooter (Gradio UI)  │
│  + OGX sidecar (Responses API) │
└──────┬──────────┬──────────┬────┘
       │          │          │
       ▼          ▼          ▼
  OpenShift   Prometheus  Ticketing
  MCP Server  MCP Server  MCP Server
  (k8s API)   (Thanos)    (incidents)
       │          │          │
       ▼          ▼          ▼
  Pods, logs,  Error rates  create_incident
  events       & latency    add_work_note
```

**Workflow:** List pods → query Prometheus → retrieve logs → synthesize diagnosis → **create** incident.

### AI Ticketing Agent

Reactive agent — investigates an existing incident, enriches it with data.

```
User / Browser
     │ "Investigate INC0000031"
     ▼
┌──────────────────────────────────┐
│  AI Ticketing Agent (Gradio UI)  │
│  + OGX sidecar (Responses API)  │
└──────┬──────────┬──────────┬─────┘
       │          │          │
       ▼          ▼          ▼
  OpenShift   Prometheus  Ticketing
  MCP Server  MCP Server  MCP Server
  (k8s API)   (Thanos)    (incidents)
       │          │          │
       ▼          ▼          ▼
  Pods, logs,  Error rates  get_incident
  events       & latency    update_incident
                             add_work_note
```

**Workflow:** Read incident → add work note "investigation started" → identify app/namespace → check pods → query Prometheus → retrieve logs → correlate with incident description → **update** incident with findings → set state to "In Progress".

---

## Shared Components

These services are deployed once and consumed by both agents.

### Quarkus Buggy App (`quarkus-buggy-app/`)

A Quarkus 3 REST microservice with intentional, randomly-triggered failures:

| Endpoint | Failure | Rate | HTTP Code |
|---|---|---|---|
| `GET /api/products` | NullPointerException | 30% | 500 |
| `GET /api/orders` | 3-second sleep | 20% | 200 (slow) |
| `GET /api/inventory` | ServiceUnavailable | 40% | 503 |

- Exposes `/q/metrics` (Micrometer + Prometheus format)
- Exposes `/q/health` (SmallRye Health liveness + readiness)
- Includes a `TrafficGenerator` that calls all endpoints every 5 seconds to produce continuous metrics

### Quarkus Working App (`quarkus-working-app/`)

A healthy version of the buggy app with the same three REST endpoints, but **no injected faults**. All requests return HTTP 200 with normal latency. Logs contain only INFO-level messages. Useful as a baseline to contrast with the buggy app — the AI agents will find nothing to investigate here.

| Endpoint | Behaviour | HTTP Code |
|---|---|---|
| `GET /api/products` | Returns product catalog | 200 |
| `GET /api/orders` | Returns orders (no delay) | 200 |
| `GET /api/inventory` | Returns stock levels | 200 |

- Same Micrometer metrics, SmallRye Health, and `TrafficGenerator` as the buggy app
- Deployed as `working-demo-app` in the `demo-app` namespace

### Ticketing System (`ticketing-system/`)

A lightweight **ServiceNow Table API simulator** for incident management. Provides a REST API that mimics ServiceNow's `incident` table, backed by SQLite. Includes an HTML dashboard at the root URL.

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

### Ticketing MCP Server (`ticketing-mcp-server/`)

A standalone **MCP server** that wraps the ticketing system REST API as MCP tools. Deployed as its own service and registered in the **OpenShift AI MCP catalog** so any agent on the platform can discover and use it.

**MCP tools exposed:**

| Tool | Used by Troubleshooter | Used by Ticketing Agent |
|---|---|---|
| `create_incident` | **Yes** — creates new tickets | No |
| `list_incidents` | No | Yes — browse open incidents |
| `get_incident` | No | **Yes** — reads the incident to investigate |
| `update_incident` | No | **Yes** — enriches ticket with findings |
| `add_work_note` | **Yes** — appends investigation notes | **Yes** — appends investigation notes |

- **Transport:** `streamable-http` at `/mcp`
- **In-cluster URL:** `http://ticketing-mcp-server.coding-assistant.svc:8080/mcp`

**OpenShift AI integration (two options):**

| Mechanism | File | RHOAI version | What it does |
|---|---|---|---|
| `MCPServer` CRD | `k8s/mcpserver.yaml` | 3.4+ | The MCP lifecycle operator creates the Deployment, Service, and probes; the server appears in the MCP catalog automatically |
| `gen-ai-aa-mcp-servers` ConfigMap | `k8s/mcp-catalog-entry.yaml` | 3.0+ | Registers an already-deployed server in the GenAI Playground UI |

`build-and-deploy.sh` detects which mechanism is available and applies the right one.

### Prometheus MCP Server (`prometheus-mcp-server/`)

A standalone **MCP server** that exposes PromQL queries against Thanos Querier as MCP tools.

**MCP tools exposed:**

| Tool | Description |
|---|---|
| `query_prometheus` | Instant PromQL query against Thanos Querier |
| `query_prometheus_range` | Range query (default 30 min) with min/avg/max summary |

- **Transport:** `streamable-http` at `/mcp`
- **In-cluster URL:** `http://prometheus-mcp-server.coding-assistant.svc:8080/mcp`

### OpenShift MCP Server (external)

The **kubernetes-mcp-server** (`openshift-mcp`) provides Kubernetes API access as MCP tools: listing pods, reading logs, describing deployments, viewing events, etc.

- **In-cluster URL:** `http://openshift-mcp.coding-assistant.svc:8000/mcp`
- Deployed separately (not part of this repository)

### Shared RBAC (`ai-agent/k8s/rbac.yaml`)

Both agents share a single `ServiceAccount` and `ClusterRoleBinding`:

- **ServiceAccount:** `ocp-troubleshooter-sa` in `coding-assistant` namespace
- **ClusterRoleBinding:** binds to `cluster-monitoring-view` (system ClusterRole) — allows querying Prometheus / Thanos metrics across all namespaces

---

## Prerequisites

- OpenShift cluster with:
  - User workload monitoring enabled (`enableUserWorkload: true`)
  - `openshift-mcp` server running in `coding-assistant` namespace
  - Nemotron model deployed via MaaS
- `oc` CLI logged in as cluster-admin
- `podman` (or Docker) for building images
- Java 17+ and Maven (for Quarkus build)

---

## Deploy

### Quick Deploy (all components)

Use the top-level `build-and-deploy-all.sh` script to build and deploy every component in the correct dependency order with a single command:

```bash
# Deploy everything (reads maas_hostname from ai-agent/k8s/cluster-config.yaml)
./build-and-deploy-all.sh

# Override the MaaS gateway hostname
./build-and-deploy-all.sh --maas-hostname <hostname>
# or via environment variable
MAAS_HOSTNAME=<hostname> ./build-and-deploy-all.sh

# Deploy a single component
./build-and-deploy-all.sh ticketing-system
./build-and-deploy-all.sh --maas-hostname <hostname> ai-agent
```

The script runs pre-flight checks (`oc` CLI installed, cluster login, namespace exists), applies the shared `cluster-config` ConfigMap, then deploys each component in order:

1. `ticketing-system`
2. `quarkus-buggy-app`
3. `quarkus-working-app`
4. `ticketing-mcp-server`
5. `prometheus-mcp-server`
6. `ai-agent`
7. `ai-ticketing-agent`

Each component's own `build-and-deploy.sh` is called under the hood, so the individual steps below are only needed if you want to deploy components manually.

---

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

### Step 2 — Deploy the Quarkus Working App

```bash
cd quarkus-working-app

# Build and push the image to the OpenShift internal registry
oc registry login --skip-check
./mvnw package \
  -Dquarkus.container-image.build=true \
  -Dquarkus.container-image.push=true \
  -Dquarkus.container-image.insecure=true \
  -DskipTests

# Deploy
oc apply -f k8s/deployment.yaml
oc apply -f k8s/service-monitor.yaml

# Verify
oc get pods -n demo-app
oc get route working-demo-app -n demo-app
```

This app produces only healthy metrics — no errors, no latency spikes.

### Step 3 — Deploy the Ticketing System

```bash
cd ticketing-system
./build-and-deploy.sh

# Verify
oc get pods -n coding-assistant | grep ticketing
oc get route ticketing-system -n coding-assistant
```

Open the Route URL in a browser to see the incident dashboard.

### Step 4 — Deploy the MCP Servers

```bash
# Ticketing MCP Server
cd ticketing-mcp-server
./build-and-deploy.sh

# Prometheus MCP Server
cd ../prometheus-mcp-server
./build-and-deploy.sh
```

The ticketing MCP server script auto-detects whether the MCP lifecycle operator is installed:
- **RHOAI 3.4+** (operator present) — applies `k8s/mcpserver.yaml`; the operator creates the Deployment and Service and the server appears in the MCP catalog
- **RHOAI 3.0–3.3** (no operator) — applies `k8s/deployment.yaml` + `k8s/route.yaml` as a manual Deployment

### Step 5 — Deploy the Agents

```bash
# AI Troubleshooter Agent
cd ai-agent
./build-and-deploy.sh
# or: ./build-and-deploy.sh <maas_hostname>

# AI Ticketing Agent
cd ../ai-ticketing-agent
./build-and-deploy.sh
```

```bash
# Verify both agents
oc get pods -n coding-assistant | grep -E 'troubleshooter|ticketing-agent'
oc get route ocp-troubleshooter ai-ticketing-agent -n coding-assistant
```

---

## Demo Script (Suggested Walkthrough)

### Act 1 — Proactive Detection (AI Troubleshooter)

Open the **AI Troubleshooter** Route URL in a browser.

**Prompt:**
```
Troubleshoot the application in the demo-app namespace. Check pod health,
look at Prometheus metrics for error rates and latency, retrieve logs,
and give me a full diagnosis.
```

Watch the agent:
- Call MCP tools to list pods → sees the running pod
- Query Prometheus → sees 30% error rate on `/api/products`, 40% on `/api/inventory`
- Retrieve logs → sees NPE stack traces
- **Create an incident** in the ticketing system with the full diagnosis

Open the ticketing system dashboard to see the newly created incident.

### Act 2 — Human Reports an Incident

A user (non-technical) notices something is wrong and opens a ticket manually:

```bash
TICKETING_URL=$(oc get route ticketing-system -n coding-assistant \
  -o jsonpath='https://{.spec.host}')

curl -s -X POST "${TICKETING_URL}/api/incidents" \
  -H "Content-Type: application/json" \
  -d '{
    "short_description": "buggy-demo-app is broken, nothing works",
    "description": "Hi, I am not sure what is going on but the buggy-demo-app in the demo-app namespace seems to have problems. I tried to access it a few times and sometimes I get errors, sometimes it is super slow, and sometimes it just says service unavailable. I do not really know what to check or where to look. Can someone please have a look? It has been like this for a while and users are complaining. Thanks.",
    "impact": 2,
    "urgency": 2,
    "category": "Application",
    "caller_id": "jsmith"
  }' | python3 -m json.tool
```

Note the `INC` number returned (e.g. `INC0000031`).

### Act 3 — AI-Assisted Triage (AI Ticketing Agent)

Open the **AI Ticketing Agent** Route URL in a browser.

**Prompt:**
```
Investigate incident INC0000031. Read the incident, troubleshoot the reported
issue on OpenShift, and update the ticket with your findings.
```

Watch the agent:
- **Read** the incident via `get_incident` → sees the vague human description
- **Add a work note** — "Starting automated investigation"
- **List pods** in `demo-app` → finds the running pod
- **Query Prometheus** → identifies specific error rates per endpoint
- **Retrieve logs** → finds NullPointerException stack traces
- **Update the incident** description with a full investigation report (root cause, affected endpoints, recommended fix)
- **Set state** to "In Progress"

Go back to the ticketing system dashboard — the incident now has a detailed AI-generated diagnosis appended to the original human description, plus timestamped work notes showing the investigation trail.

### Act 4 — Targeted Queries (either agent)

```
What HTTP endpoints in demo-app are returning 5xx errors right now?
Show me the error rates from Prometheus for the last 30 minutes.
```

```
Get the last 50 lines of logs from the buggy-demo-app pod in demo-app
and explain what errors you see.
```

---

## Local Development

### Agents

```bash
cd ai-agent  # or cd ai-ticketing-agent
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
├── build-and-deploy-all.sh              ← Master deploy script (all components)
│
│── Shared Components ──────────────────────────────────────────────────
│
├── quarkus-buggy-app/
│   ├── pom.xml
│   ├── build-and-deploy.sh
│   ├── src/main/java/com/demo/
│   │   ├── ProductResource.java          ← 30% NPE → 500
│   │   ├── OrderResource.java            ← 20% sleep → latency
│   │   ├── InventoryResource.java        ← 40% → 503
│   │   └── TrafficGenerator.java         ← background load
│   └── k8s/
│       ├── namespace.yaml
│       ├── deployment.yaml               ← Deployment + Service + Route
│       └── service-monitor.yaml          ← Prometheus scraping
│
├── quarkus-working-app/
│   ├── pom.xml
│   ├── build-and-deploy.sh
│   ├── src/main/java/com/demo/
│   │   ├── ProductResource.java          ← Always 200
│   │   ├── OrderResource.java            ← Always 200, no delay
│   │   ├── InventoryResource.java        ← Always 200
│   │   └── TrafficGenerator.java         ← background load
│   └── k8s/
│       ├── namespace.yaml
│       ├── deployment.yaml               ← Deployment + Service + Route
│       └── service-monitor.yaml          ← Prometheus scraping
│
├── ticketing-system/
│   ├── app.py                            ← FastAPI ServiceNow simulator
│   ├── Dockerfile
│   ├── build-and-deploy.sh
│   └── k8s/
│       ├── deployment.yaml               ← Deployment + PVC + Service
│       └── route.yaml                    ← External route (dashboard)
│
├── ticketing-mcp-server/
│   ├── server.py                         ← FastMCP server (MCP catalog)
│   ├── Dockerfile
│   ├── build-and-deploy.sh
│   └── k8s/
│       ├── mcpserver.yaml                ← MCPServer CR (RHOAI 3.4+)
│       ├── mcp-catalog-entry.yaml        ← GenAI Playground registration
│       ├── deployment.yaml               ← Manual fallback Deployment
│       └── route.yaml                    ← Optional external route
│
├── prometheus-mcp-server/
│   ├── server.py                         ← FastMCP server (PromQL tools)
│   ├── Dockerfile
│   ├── build-and-deploy.sh
│   └── k8s/
│       ├── mcpserver.yaml                ← MCPServer CR (RHOAI 3.4+)
│       └── deployment.yaml               ← Manual fallback Deployment
│
│── Agents ─────────────────────────────────────────────────────────────
│
├── ai-agent/                              ← AI Troubleshooter (proactive)
│   ├── agent.py                           ← OGX Responses API agent
│   ├── app.py                             ← Gradio web UI
│   ├── knowledge.md                       ← App knowledge base
│   ├── Dockerfile
│   ├── start.sh                           ← OGX sidecar wait + launch
│   ├── build-and-deploy.sh
│   ├── ogx/
│   │   └── stack_run_config.yaml          ← OGX + Nemotron config
│   └── k8s/
│       ├── rbac.yaml                      ← ServiceAccount + monitoring RBAC (shared)
│       ├── cluster-config.yaml            ← MaaS hostname ConfigMap
│       ├── deployment.yaml                ← Deployment + Service
│       └── route.yaml                     ← External route
│
└── ai-ticketing-agent/                    ← AI Ticketing Agent (reactive)
    ├── agent.py                           ← OGX Responses API agent
    ├── app.py                             ← Gradio web UI
    ├── knowledge.md                       ← App knowledge base
    ├── Dockerfile
    ├── start.sh                           ← OGX sidecar wait + launch
    ├── build-and-deploy.sh
    ├── ogx/
    │   └── stack_run_config.yaml          ← OGX + Nemotron config
    └── k8s/
        ├── deployment.yaml                ← Deployment + Service
        └── route.yaml                     ← External route
```

---

## Key Configuration

### Environment Variables (both agents)

| Variable | Default | Description |
|---|---|---|
| `OGX_BASE_URL` | `http://localhost:8321` | OGX sidecar endpoint (within the pod) |
| `OCP_MCP_URL` | `http://openshift-mcp.coding-assistant.svc:8000/mcp` | OpenShift MCP server URL |
| `PROMETHEUS_MCP_URL` | `http://prometheus-mcp-server.coding-assistant.svc:8080/mcp` | Prometheus MCP server URL |
| `TICKETING_MCP_URL` | `http://ticketing-mcp-server.coding-assistant.svc:8080/mcp` | Ticketing MCP server URL |
| `NEMOTRON_MODEL` | `nemotron/nemotron-3-nano-30b-a3b` | Model identifier for the OGX Responses API |
| `KNOWLEDGE_FILE` | `/etc/agent-knowledge/knowledge.md` | Mounted knowledge base |
| `GRADIO_PORT` | `7860` | Gradio server port |

### Token Budget and Context Window Tuning

> **Note:** The settings below have been tuned for this demo, which uses **Nemotron 3 Nano 30B** with a **131 072-token context window**. In a production deployment — especially with a model that has a larger context — these values should be reviewed and adjusted to match your model, cluster size, and operational requirements.

The agent's agentic loop accumulates every tool result (pod listings, Prometheus time-series, container logs) in the model's context. On clusters with many pods or verbose logs, this can exhaust the context window before the agent finishes its diagnosis. The following parameters control how much data the agent collects and how many iterations it runs:

| Parameter | Location | Default | Purpose |
|---|---|---|---|
| `MAX_INFER_ITERS` | `agent.py` (env var) | `18` | Maximum number of ReAct iterations (tool-call rounds) the agent can perform. Each iteration adds tool input + output to the context. Lower values reduce the risk of hitting the context limit but may prevent the agent from completing complex diagnoses. |
| `VLLM_MAX_TOKENS` | `ogx/stack_run_config.yaml` (env var) | `4096` | Maximum output tokens per LLM inference call. Limits how long each individual model response can be. A lower value reserves more of the context window for tool results. |
| `AGENT_TIMEOUT_SECONDS` | `agent.py` (env var) | `300` | Hard timeout (seconds) for the entire agent run. Acts as a safety net — if the agent is stuck in a loop, it will be stopped after this duration. |
| Log line cap | `agent.py` (system prompt) | `50 lines` | The system prompt instructs the agent to retrieve only the last 50 lines of logs per pod. Larger values produce more context for diagnosis but consume more tokens. |
| Prometheus query intervals | `agent.py` (system prompt) | `5m` preferred | The system prompt instructs the agent to prefer short intervals (`[5m]`) for range queries instead of longer windows, reducing the volume of time-series data returned. |
| Redundant tool call prevention | `agent.py` (system prompt) | Enabled | The system prompt includes a "Token budget" section that tells the agent to avoid calling the same tool twice, skip healthy pods, and stop collecting data once it has enough evidence. |

---

## Known Issues

### OpenShift MCP `pods_log` returns opaque errors for terminated containers

When the agent calls `pods_log` on a pod whose container has terminated, the OpenShift MCP server returns `Error (code 1): None` instead of the actual Kubernetes error message (`container "X" in pod "Y" not found`). The agent receives no actionable context about the failure. This happens because the `kubernetes-mcp-server` does not propagate the upstream Kubernetes API error in the MCP response.

The issue is intermittent — it only affects pods with terminated containers (e.g., exit code 143 / SIGTERM). Calls to running pods succeed normally.

Both agents mitigate this with explicit tool-name lists in the system prompt and instructions to only call tools by their exact registered names.

### OGX silently exits inference loop on unrecognized tool calls

When the model calls a tool name that is not registered in any connected MCP server (either hallucinated by the model or a legitimate tool not in the registry), OGX classifies it as a "client-side function call" and exits the inference loop without returning an error to the model or the caller. The agent run terminates silently — from the user's perspective the agent simply stops responding mid-investigation.

This can happen when the model invents plausible tool names (e.g. `services_list`) or when the system prompt lists a tool name that doesn't match the MCP server's actual registry. Both agents mitigate this with explicit tool-name lists in the system prompt and a "do not invent tool names" instruction.

### MaaS endpoint cold starts cause agent timeouts

The Nemotron MaaS endpoint (`maas.apps.cluster-*.opentlc.com`) exhibits cold-start behavior: the first inference request after an idle period hangs for >60 seconds while the model is loaded onto the GPU, then times out against OGX's default 60-second HTTP read timeout. OGX retries immediately and the retry succeeds in 5-7 seconds, but each timeout-retry cycle burns ~60 seconds of the agent's 300-second budget. With 3 retries in a single run, 180 seconds (60% of the budget) are wasted on timeouts, leaving insufficient time for the agent to produce its final response — even though all tool calls and the incident ticket complete successfully.

**TODO:**

- [ ] **Increase `AGENT_TIMEOUT_SECONDS`** — bump from 300 to 600 in the deployment env vars. Quickest mitigation; gives the agent enough headroom to absorb retry delays.
- [ ] **Configure OGX vLLM provider timeout** — add a `request_timeout` to the `remote::vllm` provider in `ogx/stack_run_config.yaml` (e.g., 120-180s) so the first request can survive a cold start without triggering a retry.
- [ ] **Keep the model warm** — add a lightweight periodic inference request (sidecar CronJob or init probe) so the model stays loaded between agent runs. The current `GET /v1/models` health check only hits the vLLM API, not the model itself.
- [ ] **Check MaaS autoscaling policy** — if the nemotron endpoint supports `minReplicas: 1` or an idle-timeout configuration, prevent scale-to-zero entirely to eliminate cold starts.

---

### Ticketing System

| Variable | Default | Description |
|---|---|---|
| `TICKETING_DB_PATH` | `/tmp/ticketing/incidents.db` | SQLite database file path |

### Ticketing MCP Server

| Variable | Default | Description |
|---|---|---|
| `TICKETING_API_URL` | `http://ticketing-system.coding-assistant.svc:8080` | Ticketing system REST API base URL |
| `MCP_PORT` | `8080` | Port for the MCP streamable-http transport |
