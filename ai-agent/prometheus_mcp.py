"""
Prometheus MCP Server.

A lightweight FastMCP server that exposes PromQL query tools against
the in-cluster Thanos Querier.  Runs on port 8765 as a sidecar to the
OGX server so it can be referenced as an MCP tool source in Responses API calls.

The service-account bearer token is read from the standard Kubernetes
mount path (or from the PROMETHEUS_TOKEN env var for local dev).
"""

from __future__ import annotations

import logging
import os
import time

import requests
import urllib3
from mcp.server.fastmcp import FastMCP

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_SA_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
_THANOS_URL = os.getenv(
    "THANOS_URL",
    "https://thanos-querier.openshift-monitoring.svc:9091",
)

mcp = FastMCP("prometheus-mcp", host="0.0.0.0", port=int(os.getenv("PROMETHEUS_MCP_PORT", "8765")))


def _bearer_token() -> str:
    env_token = os.getenv("PROMETHEUS_TOKEN", "")
    if env_token:
        return env_token
    try:
        with open(_SA_TOKEN_PATH) as f:
            return f.read().strip()
    except FileNotFoundError:
        logger.warning("SA token not found at %s", _SA_TOKEN_PATH)
        return ""


def _query(params: dict) -> str:
    token = _bearer_token()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        resp = requests.get(
            f"{_THANOS_URL}/api/v1/query",
            params=params,
            headers=headers,
            verify=False,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        return f"Prometheus query failed: {exc}"

    if data.get("status") != "success":
        return f"Prometheus returned non-success: {data}"

    results = data.get("data", {}).get("result", [])
    if not results:
        return f"No data for query: {params.get('query')}"

    lines = [f"PromQL: {params['query']}", f"Results ({len(results)} series):"]
    for s in results:
        labels = ", ".join(f'{k}="{v}"' for k, v in s.get("metric", {}).items())
        value = s.get("value", [None, "N/A"])[1]
        lines.append(f"  {{{labels}}} → {value}")
    return "\n".join(lines)


@mcp.tool()
def query_prometheus(promql: str) -> str:
    """
    Execute an instant PromQL query against the in-cluster Thanos Querier.

    Useful queries for the Quarkus demo app:
      - HTTP 5xx rate:  rate(http_server_requests_seconds_count{namespace="demo-app",outcome="SERVER_ERROR"}[5m])
      - HTTP 503 rate:  rate(http_server_requests_seconds_count{namespace="demo-app",status="503"}[5m])
      - p99 latency:    histogram_quantile(0.99, rate(http_server_requests_seconds_bucket{namespace="demo-app"}[5m]))
      - Pod restarts:   kube_pod_container_status_restarts_total{namespace="demo-app"}

    Args:
        promql: A valid PromQL expression.
    Returns:
        Formatted string with metric series and their current values.
    """
    return _query({"query": promql})


@mcp.tool()
def query_prometheus_range(promql: str, duration_minutes: int = 30) -> str:
    """
    Execute a PromQL range query over the last N minutes and return
    min/avg/max summary for each series.

    Args:
        promql:           A valid PromQL expression.
        duration_minutes: How many minutes back to look (default: 30).
    Returns:
        Human-readable summary with min, avg, max per series.
    """
    end = int(time.time())
    start = end - duration_minutes * 60
    token = _bearer_token()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        resp = requests.get(
            f"{_THANOS_URL}/api/v1/query_range",
            params={"query": promql, "start": start, "end": end, "step": "60"},
            headers=headers,
            verify=False,
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        return f"Prometheus range query failed: {exc}"

    if data.get("status") != "success":
        return f"Prometheus error: {data}"

    results = data.get("data", {}).get("result", [])
    if not results:
        return f"No data for range query: {promql}"

    lines = [f"PromQL (last {duration_minutes}m): {promql}", f"Summaries ({len(results)} series):"]
    for s in results:
        labels = ", ".join(f'{k}="{v}"' for k, v in s.get("metric", {}).items())
        values = [float(v[1]) for v in s.get("values", []) if v[1] not in ("NaN", "+Inf", "-Inf")]
        summary = (
            f"min={min(values):.4f} avg={sum(values)/len(values):.4f} max={max(values):.4f}"
            if values
            else "no numeric values"
        )
        lines.append(f"  {{{labels}}} → {summary}")
    return "\n".join(lines)


if __name__ == "__main__":
    port = int(os.getenv("PROMETHEUS_MCP_PORT", "8765"))
    logger.info("Starting Prometheus MCP server on port %d", port)
    mcp.run(transport="streamable-http")
