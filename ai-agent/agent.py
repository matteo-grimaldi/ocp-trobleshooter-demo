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

import asyncio
import logging
import os
from typing import AsyncIterator

import httpx
from ogx_client import AsyncOgxClient

logger = logging.getLogger(__name__)

OGX_BASE_URL = os.getenv("OGX_BASE_URL", "http://localhost:8321")
OCP_MCP_URL = os.getenv("OCP_MCP_URL", "http://openshift-mcp.coding-assistant.svc:8000/mcp")
PROMETHEUS_MCP_URL = os.getenv("PROMETHEUS_MCP_URL", "http://prometheus-mcp-server.coding-assistant.svc:8080/mcp")
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
- Do NOT use list_incidents — always create a new incident when issues are found.

**Troubleshooting workflow — always follow this order:**
1. List pods in the target namespace (default: demo-app) — note STATUS and RESTARTS.
2. For any pod not in Running state, get pod details and events.
3. Query Prometheus for HTTP error rates and latency across all endpoints.
4. Retrieve logs from the affected pods (last 50 lines — do NOT request more).
5. Synthesize your findings into a structured diagnosis.
6. If any issues were found, open an incident in the ticketing system:
   - Set short_description to a concise summary of the issue (e.g. "High 5xx error rate on /api/products in demo-app").
   - IMPORTANT: Set description to the COMPLETE diagnosis — do NOT summarize or shorten it.
     The ticket description MUST contain the full Diagnosis Summary (all sections: Observed Symptoms,
     Root Cause Analysis, Affected Endpoints table, Recommended Fix, and Severity).
     Copy the entire diagnosis into the description field verbatim — never abbreviate.
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

Be concise but thorough. Use data from tools to back every claim.
If a tool call fails, note the failure and continue with available information.

**Token budget — important:**
- Do NOT call the same tool twice with the same or similar parameters.
- Avoid retrieving logs for pods that are running normally with zero restarts.
- For Prometheus range queries, prefer short intervals (5m) over long ones.
- Once you have enough evidence to diagnose the issue, stop collecting data and write your diagnosis.
"""


AGENT_TIMEOUT_SECONDS = int(os.getenv("AGENT_TIMEOUT_SECONDS", "300"))
MAX_INFER_ITERS = int(os.getenv("MAX_INFER_ITERS", "15"))


async def run_agent(user_message: str) -> AsyncIterator[str]:
    """
    Stream the agent's response for a given user message.

    The OGX server handles the full ReAct loop server-side:
    it calls the MCP tools, feeds results back to Nemotron, and
    streams the final answer.

    Yields:
        Text chunks (and tool-call status lines) from the agent.
    """
    client = AsyncOgxClient(
        base_url=OGX_BASE_URL,
        api_key="local",
        timeout=httpx.Timeout(connect=30.0, read=AGENT_TIMEOUT_SECONDS, write=30.0, pool=30.0),
    )

    knowledge = _load_knowledge()
    instructions = (
        f"{SYSTEM_PROMPT}\n\n---\n## Application Knowledge Base\n\n{knowledge}"
        if knowledge
        else SYSTEM_PROMPT
    )

    logger.info("Sending request to OGX at %s", OGX_BASE_URL)

    try:
        stream = await asyncio.wait_for(
            client.responses.create(
                model=NEMOTRON_MODEL,
                input=user_message,
                instructions=instructions,
                tools=[
                    {
                        "type": "mcp",
                        "server_label": "OpenShift MCP",
                        "server_url": OCP_MCP_URL,
                        "require_approval": "never",
                    },
                    {
                        "type": "mcp",
                        "server_label": "Prometheus MCP",
                        "server_url": PROMETHEUS_MCP_URL,
                        "require_approval": "never",
                    },
                    {
                        "type": "mcp",
                        "server_label": "Ticketing System MCP",
                        "server_url": TICKETING_MCP_URL,
                        "require_approval": "never",
                    },
                ],
                stream=True,
                extra_body={"max_infer_iters": MAX_INFER_ITERS},
            ),
            timeout=60,
        )
    except asyncio.TimeoutError:
        logger.error("Timed out waiting for OGX to start streaming")
        yield "\n\n⚠️ **Error:** Timed out connecting to the agent backend. Please try again.\n"
        return
    except Exception as exc:
        logger.exception("Failed to create OGX stream")
        yield f"\n\n⚠️ **Error:** Could not reach the agent backend: {exc}\n"
        return

    try:
        async with asyncio.timeout(AGENT_TIMEOUT_SECONDS):
            async for event in stream:
                event_type = getattr(event, "type", None)

                if event_type == "response.output_text.delta":
                    yield event.delta

                elif event_type == "response.output_item.added":
                    item = getattr(event, "item", None)
                    if item and getattr(item, "type", None) == "mcp_call":
                        server = getattr(item, "server_label", "unknown")
                        tool = getattr(item, "name", "unknown")
                        logger.info("Tool call started: %s → %s", server, tool)
                        yield f"\n\n> 🔧 Calling **{server}** → `{tool}`…\n\n"

                elif event_type == "response.output_item.done":
                    item = getattr(event, "item", None)
                    if item and getattr(item, "type", None) == "mcp_call":
                        server = getattr(item, "server_label", "unknown")
                        tool = getattr(item, "name", "unknown")
                        error = getattr(item, "error", None)
                        if error:
                            logger.warning("Tool call failed: %s → %s: %s", server, tool, error)
                            yield f"\n\n> ⚠️ **{server}** → `{tool}` failed: {error}\n\n"
                        else:
                            logger.info("Tool call completed: %s → %s", server, tool)

    except TimeoutError:
        logger.error("Agent exceeded %ds timeout", AGENT_TIMEOUT_SECONDS)
        yield (
            f"\n\n⚠️ **Error:** The agent took longer than {AGENT_TIMEOUT_SECONDS}s and was stopped. "
            "This usually means the model got stuck in a tool-calling loop. Please try again "
            "with a more specific question.\n"
        )
    except Exception as exc:
        logger.exception("Error during agent streaming")
        yield f"\n\n⚠️ **Error:** Agent encountered an error: {exc}\n"
