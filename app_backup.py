
import os
import json
import re

import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel


import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from fastapi.responses import HTMLResponse, Response


from fastapi import FastAPI, Request, UploadFile, File, HTTPException


load_dotenv()

app = FastAPI(title="CareQueryPilot AI")

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# Persistent database stored in the project directory
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "carequerypilot.db"


def init_db():
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message TEXT NOT NULL,
                department TEXT,
                department_confidence REAL,
                urgency_score INTEGER,
                priority TEXT,
                refund_probability REAL,
                human_review_probability REAL,
                suggested_reply TEXT,
                provider TEXT,
                created_at TEXT NOT NULL
            )
        """)
        connection.commit()


def save_ticket(message, result):
    created_at = datetime.now(timezone.utc).isoformat()

    with sqlite3.connect(DB_PATH) as connection:
        cursor = connection.execute("""
            INSERT INTO tickets (
                message, department, department_confidence,
                urgency_score, priority, refund_probability,
                human_review_probability, suggested_reply,
                provider, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            message,
            result.get("department", "General Support"),
            result.get("department_confidence", 0),
            result.get("urgency_score", 1),
            result.get("priority", "Medium"),
            result.get("refund_probability", 0),
            result.get("human_review_probability", 0),
            result.get("suggested_reply", ""),
            result.get("provider", "Demo fallback"),
            created_at
        ))
        connection.commit()
        return cursor.lastrowid, created_at


init_db()


API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN", "")
ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
MODEL = os.getenv(
    "CLOUDFLARE_MODEL",
    "@cf/meta/llama-3.1-8b-instruct"
)


class Ticket(BaseModel):
    message: str


def demo_triage(message):
    text = message.lower()

    department = "General Support"
    priority = "Medium"
    urgency = 5
    refund_probability = 10
    human_review_probability = 20

    if any(word in text for word in [
        "refund", "payment", "charged", "billing", "invoice"
    ]):
        department = "Billing"
        refund_probability = 85

    if any(word in text for word in [
        "bug", "error", "crash", "not working", "technical"
    ]):
        department = "Technical Support"

    if any(word in text for word in [
        "login", "password", "account", "locked"
    ]):
        department = "Account Support"

    if any(word in text for word in [
        "urgent", "asap", "immediately", "emergency"
    ]):
        urgency = 9
        priority = "High"

    if any(word in text for word in [
        "lawsuit", "fraud", "security breach", "legal"
    ]):
        human_review_probability = 95
        priority = "High"

    return {
        "department": department,
        "department_confidence": 0.85,
        "urgency_score": urgency,
        "priority": priority,
        "refund_probability": refund_probability,
        "human_review_probability": human_review_probability,
        "suggested_reply": (
            "Thank you for contacting us. We have received your request "
            "and our support team will review it shortly."
        ),
        "provider": "Demo fallback"
    }


async def analyze_with_cloudflare(message):
    url = (
        "https://api.cloudflare.com/client/v4/accounts/"
        f"{ACCOUNT_ID}/ai/run/{MODEL}"
    )

    prompt = f"""
You are a customer support ticket triage assistant.

Analyze this ticket and return ONLY one valid JSON object.
Do not include markdown or additional text.

Ticket: {message}

Use exactly these fields:
{{
  "department": "Billing, Technical Support, Account Support, or General Support",
  "department_confidence": 0.0,
  "urgency_score": 1,
  "priority": "Low, Medium, or High",
  "refund_probability": 0,
  "human_review_probability": 0,
  "suggested_reply": "A short professional reply to the customer"
}}

Rules:
- Confidence and probabilities must be between 0 and 1 for confidence,
  and between 0 and 100 for probabilities.
- Urgency score must be an integer from 1 to 10.
- High priority means urgent or serious customer impact.
- Return one JSON object only.
"""

    headers = {
        "Authorization": f"Bearer {API_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "messages": [
            {
                "role": "system",
                "content": "Return only valid JSON."
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        "max_tokens": 400
    }

    async with httpx.AsyncClient(timeout=45) as client:
        response = await client.post(
            url, headers=headers, json=payload
        )
        response.raise_for_status()
        result = response.json()

    answer = result.get("result", {}).get("response", "")

    if isinstance(answer, dict):
        data = answer
    else:
        match = re.search(r"\{.*\}", answer, re.S)
        if not match:
            raise ValueError("AI did not return JSON")
        data = json.loads(match.group(0))

    required = [
        "department",
        "department_confidence",
        "urgency_score",
        "priority",
        "refund_probability",
        "human_review_probability",
        "suggested_reply"
    ]

    for field in required:
        if field not in data:
            raise ValueError(f"Missing AI field: {field}")

    data["provider"] = "Cloudflare Workers AI"
    return data


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(
    request=request,
    name="index.html",
    context={}
    )


@app.get("/health")
async def health():
    return {"status": "ok", "project": "CareQueryPilot AI"}



@app.post("/api/analyze")
async def analyze(ticket: Ticket):
    message = ticket.message.strip()

    if not message:
        return {"error": "Please enter a customer message."}

    try:
        if API_TOKEN and ACCOUNT_ID:
            try:
                result = await analyze_with_cloudflare(message)
            except Exception as error:
                print("Cloudflare AI error:", error)
                result = demo_triage(message)
                result["notice"] = (
                    "Live AI was unavailable. Demo rules were used."
                )
        else:
            result = demo_triage(message)
            result["notice"] = (
                "Demo mode: configure Cloudflare credentials "
                "to use live AI."
            )

        ticket_id, created_at = save_ticket(message, result)

        result["ticket_id"] = ticket_id
        result["created_at"] = created_at

        result["escalation_recommended"] = (
            float(result.get("human_review_probability", 0)) >= 60
            or str(result.get("priority", "")).lower() == "high"
        )

        return result

    except Exception as error:
        print("Ticket processing error:", error)
        return {"error": "Could not process this ticket. Please try again."}



@app.get("/api/history")
async def ticket_history(q: str = "", limit: int = 100):
    limit = max(1, min(limit, 500))
    search = f"%{q.strip()}%"

    with sqlite3.connect(DB_PATH) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute("""
            SELECT *
            FROM tickets
            WHERE message LIKE ?
               OR department LIKE ?
               OR priority LIKE ?
            ORDER BY id DESC
            LIMIT ?
        """, (search, search, search, limit)).fetchall()

    return {"tickets": [dict(row) for row in rows]}


@app.post("/api/import-csv")
async def import_csv(file: UploadFile = File(...)):
    import csv
    import io

    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(
            status_code=400,
            detail="Please upload a CSV file."
        )

    content = await file.read()

    # Limit uploads to 10 MB for this prototype.
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail="CSV file is too large. Maximum size is 10 MB."
        )

    try:
        text = content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))

        if not reader.fieldnames:
            raise ValueError("CSV file is empty.")

        headers = {
            name.strip().lower()
            for name in reader.fieldnames if name
        }

        if not (
            "message" in headers
            or "description" in headers
            or "subject" in headers
        ):
            raise ValueError(
                "CSV must contain message, description, or subject."
            )

        imported = 0
        skipped = 0

        for row in reader:
            normalized = {
                str(key).strip().lower(): str(value or "").strip()
                for key, value in row.items() if key
            }

            message = normalized.get("message", "")

            if not message:
                subject = normalized.get("subject", "")
                description = normalized.get("description", "")
                message = f"{subject}\n{description}".strip()

            if not message:
                skipped += 1
                continue

            # Keep this synchronous rule-based triage inexpensive.
            result = demo_triage(message)
            result["provider"] = "Bulk import rules"

            save_ticket(message[:10000], result)
            imported += 1

            if imported >= 5000:
                break

        return {
            "imported": imported,
            "skipped": skipped,
            "message": (
                f"Imported {imported} tickets. "
                "Bulk import used local rules, not live AI."
            )
        }

    except (UnicodeDecodeError, csv.Error, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error))
    finally:
        await file.close()



@app.get("/api/insights")
async def business_insights():
    with sqlite3.connect(DB_PATH) as connection:
        connection.row_factory = sqlite3.Row

        totals = connection.execute("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN LOWER(priority) = 'high'
                    THEN 1 ELSE 0 END) AS high_priority,
                SUM(CASE WHEN COALESCE(human_review_probability, 0) >= 60
                    THEN 1 ELSE 0 END) AS review_needed,
                ROUND(AVG(urgency_score), 2) AS average_urgency
            FROM tickets
        """).fetchone()

        departments = connection.execute("""
            SELECT department, COUNT(*) AS ticket_count
            FROM tickets
            GROUP BY department
            ORDER BY ticket_count DESC
        """).fetchall()

        priority_queue = connection.execute("""
            SELECT id, message, department, priority,
                   urgency_score, human_review_probability, created_at
            FROM tickets
            ORDER BY
                CASE LOWER(priority)
                    WHEN 'high' THEN 0
                    WHEN 'medium' THEN 1
                    ELSE 2
                END,
                urgency_score DESC,
                id DESC
            LIMIT 20
        """).fetchall()

    return {
        "summary": dict(totals),
        "departments": [dict(row) for row in departments],
        "priority_queue": [dict(row) for row in priority_queue]
    }


@app.get("/api/export.csv")
async def export_tickets():
    import csv
    import io

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "Ticket ID", "Created At", "Message", "Department",
        "Priority", "Urgency", "Refund Probability",
        "Human Review Probability", "Provider"
    ])

    with sqlite3.connect(DB_PATH) as connection:
        rows = connection.execute("""
            SELECT id, created_at, message, department,
                   priority, urgency_score, refund_probability,
                   human_review_probability, provider
            FROM tickets
            ORDER BY id DESC
        """).fetchall()

    writer.writerows(rows)

    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition":
                'attachment; filename="carequerypilot_tickets.csv"'
        }
    )


if __name__ == "__main__":
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
