"""
ServiceNow-style Incident Management API.

A lightweight simulator that exposes REST endpoints compatible with
ServiceNow's Table API conventions for the ``incident`` table.
"""

import os
import sqlite3
import textwrap
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

DB_PATH = os.getenv("TICKETING_DB_PATH", "/tmp/ticketing/incidents.db")

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _ensure_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with _connect() as conn:
        conn.execute(textwrap.dedent("""\
            CREATE TABLE IF NOT EXISTS incidents (
                number         TEXT PRIMARY KEY,
                state          TEXT NOT NULL DEFAULT 'New',
                impact         INTEGER NOT NULL DEFAULT 3,
                urgency        INTEGER NOT NULL DEFAULT 3,
                priority       INTEGER NOT NULL DEFAULT 5,
                short_description TEXT NOT NULL,
                description    TEXT NOT NULL DEFAULT '',
                category       TEXT NOT NULL DEFAULT 'Application',
                subcategory    TEXT NOT NULL DEFAULT '',
                assignment_group TEXT NOT NULL DEFAULT '',
                assigned_to    TEXT NOT NULL DEFAULT '',
                caller_id      TEXT NOT NULL DEFAULT 'ocp-troubleshooter',
                opened_at      TEXT NOT NULL,
                updated_at     TEXT NOT NULL,
                resolved_at    TEXT,
                close_notes    TEXT NOT NULL DEFAULT ''
            )
        """))
        conn.execute(textwrap.dedent("""\
            CREATE TABLE IF NOT EXISTS work_notes (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                incident  TEXT NOT NULL REFERENCES incidents(number),
                note      TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """))


@contextmanager
def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _next_number() -> str:
    with _connect() as conn:
        row = conn.execute(
            "SELECT number FROM incidents ORDER BY number DESC LIMIT 1"
        ).fetchone()
    if row is None:
        return "INC0000001"
    seq = int(row["number"][3:]) + 1
    return f"INC{seq:07d}"


def _priority(impact: int, urgency: int) -> int:
    matrix = {
        (1, 1): 1, (1, 2): 2, (1, 3): 3,
        (2, 1): 2, (2, 2): 3, (2, 3): 4,
        (3, 1): 3, (3, 2): 4, (3, 3): 5,
    }
    return matrix.get((impact, urgency), 5)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class IncidentCreate(BaseModel):
    short_description: str
    description: str = ""
    impact: int = Field(default=3, ge=1, le=3)
    urgency: int = Field(default=3, ge=1, le=3)
    category: str = "Application"
    subcategory: str = ""
    assignment_group: str = ""
    assigned_to: str = ""
    caller_id: str = "ocp-troubleshooter"


class IncidentUpdate(BaseModel):
    state: Optional[str] = None
    impact: Optional[int] = Field(default=None, ge=1, le=3)
    urgency: Optional[int] = Field(default=None, ge=1, le=3)
    short_description: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None
    assignment_group: Optional[str] = None
    assigned_to: Optional[str] = None
    close_notes: Optional[str] = None


class WorkNoteCreate(BaseModel):
    note: str


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="ServiceNow Incident Simulator",
    version="1.0.0",
    description="A lightweight ServiceNow Table API simulator for the incident table.",
)

VALID_STATES = {"New", "In Progress", "On Hold", "Resolved", "Closed"}


@app.on_event("startup")
def startup():
    _ensure_db()


# ── Create ─────────────────────────────────────────────────────────────────

@app.post("/api/incidents", status_code=201)
def create_incident(body: IncidentCreate):
    now = _now()
    number = _next_number()
    priority = _priority(body.impact, body.urgency)

    with _connect() as conn:
        conn.execute(
            textwrap.dedent("""\
                INSERT INTO incidents
                    (number, impact, urgency, priority,
                     short_description, description, category, subcategory,
                     assignment_group, assigned_to, caller_id,
                     opened_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """),
            (
                number, body.impact, body.urgency, priority,
                body.short_description, body.description,
                body.category, body.subcategory,
                body.assignment_group, body.assigned_to, body.caller_id,
                now, now,
            ),
        )
    return _get_incident(number)


# ── List ───────────────────────────────────────────────────────────────────

@app.get("/api/incidents")
def list_incidents(
    state: Optional[str] = Query(default=None),
    category: Optional[str] = Query(default=None),
    priority: Optional[int] = Query(default=None, ge=1, le=5),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    clauses, params = [], []
    if state:
        clauses.append("state = ?")
        params.append(state)
    if category:
        clauses.append("category = ?")
        params.append(category)
    if priority:
        clauses.append("priority = ?")
        params.append(priority)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    query = f"SELECT * FROM incidents {where} ORDER BY opened_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
        total = conn.execute(
            f"SELECT COUNT(*) FROM incidents {where}", params[:-2]
        ).fetchone()[0]

    return {"result": [dict(r) for r in rows], "total": total}


# ── Get one ────────────────────────────────────────────────────────────────

@app.get("/api/incidents/{number}")
def get_incident(number: str):
    return _get_incident(number)


def _get_incident(number: str) -> dict:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM incidents WHERE number = ?", (number,)
        ).fetchone()
        if row is None:
            raise HTTPException(404, f"Incident {number} not found")
        notes = conn.execute(
            "SELECT * FROM work_notes WHERE incident = ? ORDER BY created_at",
            (number,),
        ).fetchall()
    incident = dict(row)
    incident["work_notes"] = [dict(n) for n in notes]
    return incident


# ── Update ─────────────────────────────────────────────────────────────────

@app.patch("/api/incidents/{number}")
def update_incident(number: str, body: IncidentUpdate):
    _get_incident(number)  # 404 if missing

    sets, params = [], []
    for field, value in body.model_dump(exclude_none=True).items():
        sets.append(f"{field} = ?")
        params.append(value)

    if not sets:
        return _get_incident(number)

    with _connect() as conn:
        row = conn.execute(
            "SELECT impact, urgency FROM incidents WHERE number = ?", (number,)
        ).fetchone()
        new_impact = body.impact if body.impact is not None else row["impact"]
        new_urgency = body.urgency if body.urgency is not None else row["urgency"]
        sets.append("priority = ?")
        params.append(_priority(new_impact, new_urgency))

        now = _now()
        sets.append("updated_at = ?")
        params.append(now)

        if body.state in ("Resolved", "Closed"):
            sets.append("resolved_at = ?")
            params.append(now)

        params.append(number)
        conn.execute(
            f"UPDATE incidents SET {', '.join(sets)} WHERE number = ?", params
        )

    return _get_incident(number)


# ── Work notes ─────────────────────────────────────────────────────────────

@app.post("/api/incidents/{number}/notes", status_code=201)
def add_work_note(number: str, body: WorkNoteCreate):
    _get_incident(number)  # 404 if missing
    now = _now()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO work_notes (incident, note, created_at) VALUES (?,?,?)",
            (number, body.note, now),
        )
        conn.execute(
            "UPDATE incidents SET updated_at = ? WHERE number = ?", (now, number)
        )
    return _get_incident(number)


# ── Dashboard (minimal HTML) ──────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def dashboard():
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM incidents ORDER BY opened_at DESC LIMIT 100"
        ).fetchall()

    STATE_BADGE = {
        "New": "#0d6efd",
        "In Progress": "#ffc107",
        "On Hold": "#6c757d",
        "Resolved": "#198754",
        "Closed": "#495057",
    }

    PRIORITY_LABEL = {1: "1-Critical", 2: "2-High", 3: "3-Moderate", 4: "4-Low", 5: "5-Planning"}

    table_rows = ""
    for r in rows:
        color = STATE_BADGE.get(r["state"], "#6c757d")
        table_rows += f"""
        <tr>
          <td><a href="/api/incidents/{r['number']}">{r['number']}</a></td>
          <td><span style="background:{color};color:#fff;padding:2px 8px;border-radius:4px;font-size:0.85em">{r['state']}</span></td>
          <td>{PRIORITY_LABEL.get(r['priority'], r['priority'])}</td>
          <td>{r['category']}</td>
          <td style="max-width:400px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{r['short_description']}</td>
          <td>{r['opened_at']}</td>
        </tr>"""

    return f"""\
<!DOCTYPE html>
<html>
<head>
  <title>Incident Dashboard</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 2rem; background: #f5f5f5; }}
    h1 {{ color: #1a1a2e; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff; box-shadow: 0 1px 3px rgba(0,0,0,.12); }}
    th, td {{ padding: 10px 14px; text-align: left; border-bottom: 1px solid #e0e0e0; }}
    th {{ background: #1a1a2e; color: #fff; }}
    tr:hover {{ background: #f0f4ff; }}
    a {{ color: #0d6efd; text-decoration: none; }}
    .stats {{ display: flex; gap: 1rem; margin-bottom: 1.5rem; }}
    .stat {{ background: #fff; padding: 1rem 1.5rem; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,.12); }}
    .stat .value {{ font-size: 1.8rem; font-weight: bold; color: #1a1a2e; }}
    .stat .label {{ color: #666; font-size: 0.9rem; }}
  </style>
</head>
<body>
  <h1>Incident Dashboard</h1>
  <div class="stats">
    <div class="stat"><div class="value">{len(rows)}</div><div class="label">Total Incidents</div></div>
    <div class="stat"><div class="value">{sum(1 for r in rows if r['state'] == 'New')}</div><div class="label">New</div></div>
    <div class="stat"><div class="value">{sum(1 for r in rows if r['state'] == 'In Progress')}</div><div class="label">In Progress</div></div>
    <div class="stat"><div class="value">{sum(1 for r in rows if r['state'] in ('Resolved','Closed'))}</div><div class="label">Resolved / Closed</div></div>
  </div>
  <table>
    <thead>
      <tr><th>Number</th><th>State</th><th>Priority</th><th>Category</th><th>Short Description</th><th>Opened</th></tr>
    </thead>
    <tbody>
      {table_rows if table_rows else '<tr><td colspan="6" style="text-align:center;color:#999">No incidents yet</td></tr>'}
    </tbody>
  </table>
</body>
</html>"""


# ── Health endpoint ────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}
