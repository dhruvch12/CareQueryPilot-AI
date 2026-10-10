
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

from fastapi import HTTPException
from starlette.middleware.sessions import SessionMiddleware
from pwdlib import PasswordHash
from pydantic import BaseModel, EmailStr, Field


from fastapi import FastAPI, Request, UploadFile, File, HTTPException


load_dotenv()

app = FastAPI(title="CareQueryPilot AI")

SESSION_SECRET = os.getenv("SESSION_SECRET", "")

if not SESSION_SECRET:
    raise RuntimeError(
        "Add SESSION_SECRET to your .env file before starting the app."
    )

app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    same_site="lax",
    https_only=os.getenv("APP_ENV") == "production",
    max_age=60 * 60 * 8,
)

password_hasher = PasswordHash.recommended()

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
        
        # SaaS company accounts
        connection.execute("""
            CREATE TABLE IF NOT EXISTS companies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        # Each user belongs to one company
        connection.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'admin',
                created_at TEXT NOT NULL,
                FOREIGN KEY (company_id) REFERENCES companies(id)
            )
        """)

        # Safely add company ownership to existing tickets
        columns = {
            row[1] for row in
            connection.execute("PRAGMA table_info(tickets)").fetchall()
        }

        if "company_id" not in columns:
            connection.execute(
                "ALTER TABLE tickets ADD COLUMN company_id INTEGER"
            )

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_tickets_company_created
            ON tickets(company_id, created_at)
        """)

        connection.commit()


def save_ticket(message, result, company_id):
    created_at = datetime.now(timezone.utc).isoformat()

    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        cursor = connection.execute("""
            INSERT INTO tickets (
                company_id, message, department, department_confidence,
                urgency_score, priority, refund_probability,
                human_review_probability, suggested_reply, provider, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            company_id,
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
    try:
        require_company_id(request)
    except HTTPException:
        return RedirectResponse(url="/login", status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={}
    )


@app.get("/health")
async def health():
    return {"status": "ok", "project": "CareQueryPilot AI"}



def require_company_id(request: Request) -> int:
    user_id = request.session.get("user_id")
    session_company_id = request.session.get("company_id")

    if not user_id or not session_company_id:
        raise HTTPException(status_code=401, detail="Please log in.")

    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        row = conn.execute(
            "SELECT company_id FROM users WHERE id = ?",
            (user_id,)
        ).fetchone()
    finally:
        conn.close()

    if not row or row[0] != session_company_id:
        request.session.clear()
        raise HTTPException(status_code=401, detail="Invalid session.")

    return row[0]


@app.post("/api/analyze")
async def analyze(ticket: Ticket, request: Request):
    company_id = require_company_id(request)
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

        ticket_id, created_at = save_ticket(message, result, company_id)

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
async def ticket_history(request: Request, q: str = "", limit: int = 100):
    company_id = require_company_id(request)
    limit = max(1, min(limit, 500))
    search = f"%{q.strip()}%"

    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute("""
            SELECT *
            FROM tickets
            WHERE company_id = ?
              AND (
                  message LIKE ?
                  OR department LIKE ?
                  OR priority LIKE ?
              )
            ORDER BY id DESC
            LIMIT ?
        """, (company_id, search, search, search, limit)).fetchall()

    return {"tickets": [dict(row) for row in rows]}

@app.post("/api/import-csv")
async def import_csv(request: Request, file: UploadFile = File(...)):
    company_id = require_company_id(request)
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

            save_ticket(message[:10000], result, company_id)
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
async def business_insights(request: Request):
    company_id = require_company_id(request)

    with sqlite3.connect(DB_PATH, timeout=10) as connection:
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
            WHERE company_id = ?
        """, (company_id,)).fetchone()

        departments = connection.execute("""
            SELECT department, COUNT(*) AS ticket_count
            FROM tickets
            WHERE company_id = ?
            GROUP BY department
            ORDER BY ticket_count DESC
        """, (company_id,)).fetchall()

        priority_queue = connection.execute("""
            SELECT id, message, department, priority,
                   urgency_score, human_review_probability, created_at
            FROM tickets
            WHERE company_id = ?
            ORDER BY
                CASE LOWER(priority)
                    WHEN 'high' THEN 0
                    WHEN 'medium' THEN 1
                    ELSE 2
                END,
                urgency_score DESC,
                id DESC
            LIMIT 20
        """, (company_id,)).fetchall()

    return {
        "summary": dict(totals),
        "departments": [dict(row) for row in departments],
        "priority_queue": [dict(row) for row in priority_queue]
    }


@app.get("/api/export.csv")
async def export_tickets(request: Request):
    import csv
    import io

    company_id = require_company_id(request)
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "Ticket ID", "Created At", "Message", "Department",
        "Priority", "Urgency", "Refund Probability",
        "Human Review Probability", "Provider"
    ])

    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        rows = connection.execute("""
            SELECT id, created_at, message, department,
                   priority, urgency_score, refund_probability,
                   human_review_probability, provider
            FROM tickets
            WHERE company_id = ?
            ORDER BY id DESC
        """, (company_id,)).fetchall()

    writer.writerows(rows)

    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition":
                'attachment; filename="carequerypilot_tickets.csv"'
        }
    )


class RegisterRequest(BaseModel):
    company_name: str = Field(min_length=2, max_length=100)
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


@app.post("/api/auth/register")
def register(data: RegisterRequest, request: Request):
    email = str(data.email).strip().lower()
    company_name = data.company_name.strip()

    if len(company_name) < 2:
        raise HTTPException(400, "Enter a valid company name.")

    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        conn.execute("PRAGMA foreign_keys = ON")

        existing = conn.execute(
            "SELECT id FROM users WHERE email = ?", (email,)
        ).fetchone()

        if existing:
            raise HTTPException(
                status_code=409,
                detail="An account with this email already exists."
            )

        created_at = datetime.now(timezone.utc).isoformat()
        password_hash = password_hasher.hash(data.password)

        with conn:
            cursor = conn.execute(
                "INSERT INTO companies (name, created_at) VALUES (?, ?)",
                (company_name, created_at)
            )
            company_id = cursor.lastrowid

            cursor = conn.execute(
                """INSERT INTO users
                   (company_id, email, password_hash, role, created_at)
                   VALUES (?, ?, ?, 'admin', ?)""",
                (company_id, email, password_hash, created_at)
            )
            user_id = cursor.lastrowid

        request.session.clear()
        request.session["user_id"] = user_id
        request.session["company_id"] = company_id

        return {
            "message": "Company account created.",
            "company_id": company_id,
            "email": email
        }
    except sqlite3.IntegrityError:
        raise HTTPException(409, "This email is already registered.")
    finally:
        conn.close()


@app.post("/api/auth/login")
def login(data: LoginRequest, request: Request):
    email = str(data.email).strip().lower()

    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        row = conn.execute(
            """SELECT id, company_id, password_hash
               FROM users WHERE email = ?""",
            (email,)
        ).fetchone()
    finally:
        conn.close()

    if not row or not password_hasher.verify(data.password, row[2]):
        raise HTTPException(401, "Invalid email or password.")

    request.session.clear()
    request.session["user_id"] = row[0]
    request.session["company_id"] = row[1]

    return {"message": "Login successful.", "email": email}


@app.get("/api/auth/me")
def current_user(request: Request):
    user_id = request.session.get("user_id")

    if not user_id:
        raise HTTPException(401, "Please log in.")

    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        row = conn.execute(
            """SELECT users.email, companies.name
               FROM users
               JOIN companies ON companies.id = users.company_id
               WHERE users.id = ?""",
            (user_id,)
        ).fetchone()
    finally:
        conn.close()

    if not row:
        request.session.clear()
        raise HTTPException(401, "Account not found.")

    return {"email": row[0], "company_name": row[1]}


@app.post("/api/auth/logout")
def logout(request: Request):
    request.session.clear()
    return {"message": "Logged out successfully."}


from fastapi.responses import RedirectResponse

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={}
    )


if __name__ == "__main__":
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
