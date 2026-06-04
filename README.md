# OCP AI Troubleshooter Demo

An AI agent that autonomously troubleshoots applications deployed on OpenShift.  
It combines **Nemotron 3 Nano 30B** (via the cluster's MaaS gateway), the **OpenShift MCP server**, and **Prometheus / Thanos** to diagnose application issues in real time.

---

## Architecture

```
User / Browser
     │  chat
     ▼
┌──────────────────────────────┐
│  AI Agent  (Gradio UI)       │   coding-assistant namespace
│  LangChain ReAct + LangGraph │
└──────┬───────────┬───────────┘
       │           │
       │ OpenAI    │ MCP tools      PromQL
       ▼           ▼                  ▼
  Nemotron     OpenShift MCP      Thanos Querier
  3 Nano 30B   server (k8s API)   (openshift-monitoring)
  (MaaS)
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

### 2. AI Troubleshooter Agent (`ai-agent/`)

A Python LangChain ReAct agent with a Gradio web UI.

**Tools available to the agent:**
- **OpenShift MCP tools** (via `langchain-mcp-adapters`) — pods, deployments, events, logs
- `query_prometheus(promql)` — instant PromQL query against Thanos Querier
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

### Step 2 — Deploy the AI Agent

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

### Step 3 — Run the Demo

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

---

## Local Development (Agent Only)

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
| `GRADIO_PORT` | `7860` | Gradio server port |
