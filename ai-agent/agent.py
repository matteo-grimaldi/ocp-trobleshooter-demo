"""
OCP Troubleshooter Agent — OGX edition.

Uses the OGX Responses API (server-side agentic loop) instead of
LangChain/LangGraph.  The OGX server runs as a sidecar container in the
same pod and handles:
  - Tool calls to the OpenShift MCP server
  - Tool calls to the Prometheus MCP server
  - Inference via Nemotron (vLLM / MaaS)
"""

from __future__ import annotations

import logging
import os
from typing import AsyncIterator

from ogx_client import AsyncOgxClient

logger = logging.getLogger(__name__)

OGX_BASE_URL = os.getenv("OGX_BASE_URL", "http://localhost:8321")
OCP_MCP_URL = os.getenv("OCP_MCP_URL", "http://openshift-mcp.coding-assistant.svc:8000/mcp")
PROMETHEUS_MCP_URL = os.getenv("PROMETHEUS_MCP_URL", "http://localhost:8765/mcp")
TICKETING_MCP_URL = os.getenv("TICKETING_MCP_URL", "http://ticketing-mcp-server.coding-assistant.svc:8080/mcp")
NEMOTRON_MODEL = os.getenv("NEMOTRON_MODEL", "nemotron/nemotron-3-nano-30b-a3b")
KNOWLEDGE_FILE = os.getenv("KNOWLEDGE_FILE", "/etc/agent-knowledge/knowledge.md")


def _load_knowledge() -> str:
    """Load the application knowledge base from the mounted ConfigMap file."""
    try:
        with open(KNOWLEDGE_FILE) as f:
            content = f.read().strip()
        logger.info("Loaded knowledge base from %s (%d chars)", KNOWLEDGE_FILE, len(content))
        return content
    except FileNotFoundError:
        logger.warning("Knowledge file not found at %s — running without app context", KNOWLEDGE_FILE)
        return ""

SYSTEM_PROMPT = """You are an expert OpenShift Site Reliability Engineer (SRE) and AI troubleshooting assistant.

Your job is to autonomously diagnose problems with applications deployed on OpenShift clusters.
You have access to the following MCP tool servers:

**openshift** (kubernetes-mcp-server):
- Use these to inspect pod status, deployment conditions, Kubernetes events, and container logs.
- Always start by listing pods in the affected namespace to understand the current state.
- Check deployment conditions and pod events for CrashLoopBackOff, OOMKilled, ImagePullBackOff, etc.
- Retrieve recent logs for pods that are failing.

**prometheus** (Thanos Querier):
- Use query_prometheus for instant metrics.
- Use query_prometheus_range for trends over the last 30 minutes.
- Key metrics for the Quarkus demo app:
  * HTTP 5xx errors: rate(http_server_requests_seconds_count{namespace="demo-app",outcome="SERVER_ERROR"}[5m])
  * HTTP 503 errors: rate(http_server_requests_seconds_count{namespace="demo-app",status="503"}[5m])
  * p99 latency:     histogram_quantile(0.99, rate(http_server_requests_seconds_bucket{namespace="demo-app"}[5m]))
  * Pod restarts:    kube_pod_container_status_restarts_total{namespace="demo-app"}

**ticketing** (Ticketing System):
- Use create_incident to open a ticket when you detect issues.
- Use add_work_note to append investigation details to an existing ticket.
- Use list_incidents to check for recent open incidents before creating duplicates.

**Troubleshooting workflow — always follow this order:**
1. List pods in the target namespace (default: demo-app) — note STATUS and RESTARTS.
2. For any pod not in Running state, get pod details and events.
3. Query Prometheus for HTTP error rates and latency across all endpoints.
4. Retrieve logs from the affected pods (last 100 lines).
5. Synthesize your findings into a structured diagnosis.
6. If any issues were found, open an incident in the ticketing system:
   - Set short_description to a concise summary of the issue (e.g. "High 5xx error rate on /api/products in demo-app").
   - Set description to the full diagnosis including observed symptoms, root cause analysis, affected endpoints, and recommended fix.
   - Set impact based on severity: 1 (High) for CRITICAL, 2 (Medium) for HIGH, 3 (Low) for MEDIUM/LOW.
   - Set urgency based on how quickly the issue needs attention: 1 if user-facing, 2 if degraded, 3 if low-impact.
   - Set category to "Application" for app-level issues, "Infrastructure" for pod/node issues.
   - If multiple distinct issues are found, create separate incidents for each.

**Output format — always end with this structure:**
---
## Diagnosis Summary

**Application:** <name>
**Namespace:** <namespace>

### Observed Symptoms
- <bullet list of what you found>

### Root Cause Analysis
<detailed explanation of why this is happening>

### Affected Endpoints
| Endpoint | Error Rate | Issue |
|----------|-----------|-------|
| ...      | ...       | ...   |

### Recommended Fix
<specific, actionable steps to resolve the issue>

### Severity
<CRITICAL / HIGH / MEDIUM / LOW>

### Incidents Created
| Ticket | Summary | Priority |
|--------|---------|----------|
| INCxxxxxxx | ... | ... |
---

Be concise but thorough. Use data from tools to back every claim.
If a tool call fails, note the failure and continue with available information.
"""


async def run_agent(user_message: str) -> AsyncIterator[str]:
    """
    Stream the agent's response for a given user message.

    The OGX server handles the full ReAct loop server-side:
    it calls the MCP tools, feeds results back to Nemotron, and
    streams the final answer.

    Yields:
        Text chunks from the assistant's final response.
    """
    client = AsyncOgxClient(base_url=OGX_BASE_URL, api_key="local")

    knowledge = _load_knowledge()
    instructions = (
        f"{SYSTEM_PROMPT}\n\n---\n## Application Knowledge Base\n\n{knowledge}"
        if knowledge
        else SYSTEM_PROMPT
    )

    logger.info("Sending request to OGX at %s", OGX_BASE_URL)

    stream = await client.responses.create(
        model=NEMOTRON_MODEL,
        input=user_message,
        instructions=instructions,
        tools=[
            {
                "type": "mcp",
                "server_label": "openshift",
                "server_url": OCP_MCP_URL,
                "require_approval": "never",
            },
            {
                "type": "mcp",
                "server_label": "prometheus",
                "server_url": PROMETHEUS_MCP_URL,
                "require_approval": "never",
            },
            {
                "type": "mcp",
                "server_label": "ticketing",
                "server_url": TICKETING_MCP_URL,
                "require_approval": "never",
            },
        ],
        stream=True,
        extra_body={"max_infer_iters": 30},
    )

    async for event in stream:
        if getattr(event, "type", None) == "response.output_text.delta":
            yield event.delta
