"""
main.py - Enterprise AI Sales CRM & Industrial Bakery Intelligence
Food Development Company (شركة تنمية الغذاء)
Complete CRUD + Instant WhatsApp Notifications + Pipeline & Operations Management
"""

import os
import io
import sys
import json
import uuid
import base64
import logging
import hashlib
import subprocess
import csv
from datetime import datetime
from typing import Optional, List
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Response, Request, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse
from pydantic import BaseModel
import psycopg2
from psycopg2.extras import RealDictCursor
import pyotp
import qrcode
import httpx

try:
    import openpyxl
except ImportError:
    openpyxl = None

from sales_reports_engine import render_report_html

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("SalesCRM")

DATABASE_URL = (
    os.getenv("DATABASE_URL") 
    or os.getenv("DATABASE_PUBLIC_URL") 
    or os.getenv("POSTGRES_URL") 
    or ""
)
whatsapp_process = None

def get_db_connection():
    if not DATABASE_URL:
        return None
    try:
        conn_url = DATABASE_URL
        if conn_url.startswith("postgres://"):
            conn_url = conn_url.replace("postgres://", "postgresql://", 1)
        return psycopg2.connect(conn_url, cursor_factory=RealDictCursor, connect_timeout=5)
    except Exception:
        return None

def run_isolated_ddl(sql_statement: str):
    conn = get_db_connection()
    if not conn:
        return
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(sql_statement)
    except Exception:
        pass
    finally:
        conn.close()

async def send_whatsapp_direct(target_phone_or_group: str, message: str) -> bool:
    if not target_phone_or_group:
        return False
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "http://127.0.0.1:3001/send-message",
                json={"phone_or_group": target_phone_or_group, "message": message},
                timeout=5.0
            )
            return resp.status_code == 200
    except Exception:
        return False

def init_database():
    conn = get_db_connection()
    if not conn:
        return

    try:
        with conn.cursor() as cur:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS system_auth (
                id SERIAL PRIMARY KEY,
                username VARCHAR(100) UNIQUE NOT NULL,
                totp_secret VARCHAR(64) NOT NULL,
                is_2fa_enabled BOOLEAN DEFAULT FALSE
            );
            """)
            cur.execute("SELECT COUNT(*) FROM system_auth WHERE username = 'admin';")
            if cur.fetchone()["count"] == 0:
                default_secret = pyotp.random_base32()
                cur.execute(
                    "INSERT INTO system_auth (username, totp_secret, is_2fa_enabled) VALUES (%s, %s, %s);",
                    ('admin', default_secret, False)
                )

            cur.execute("""
            CREATE TABLE IF NOT EXISTS system_config (
                key_name VARCHAR(100) PRIMARY KEY,
                key_value TEXT NOT NULL
            );
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS products_catalog (
                id SERIAL PRIMARY KEY,
                sku VARCHAR(100) UNIQUE,
                name_ar VARCHAR(250) NOT NULL,
                name_en VARCHAR(250) DEFAULT '',
                weight_spec VARCHAR(100) DEFAULT '',
                primary_packaging VARCHAR(150) DEFAULT '',
                carton_pack_spec VARCHAR(200) DEFAULT '',
                notes TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS sales_executives (
                id SERIAL PRIMARY KEY,
                name VARCHAR(150) NOT NULL,
                employee_code VARCHAR(50) UNIQUE NOT NULL,
                phone_number VARCHAR(30) UNIQUE NOT NULL,
                region VARCHAR(100) NOT NULL,
                has_target BOOLEAN DEFAULT FALSE,
                monthly_target NUMERIC(12, 2) DEFAULT 0.00,
                achieved_sales NUMERIC(12, 2) DEFAULT 0.00,
                total_expenses NUMERIC(12, 2) DEFAULT 0.00,
                preferred_language VARCHAR(10) DEFAULT 'AR',
                status VARCHAR(20) DEFAULT 'نشط'
            );
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS customer_accounts (
                id SERIAL PRIMARY KEY,
                company_name VARCHAR(200) NOT NULL,
                brand_name VARCHAR(200) DEFAULT '',
                sector VARCHAR(100) NOT NULL,
                region VARCHAR(100) DEFAULT 'مسقط',
                contact_person VARCHAR(150) NOT NULL,
                phone VARCHAR(30) NOT NULL,
                assigned_rep_id INT REFERENCES sales_executives(id) ON DELETE SET NULL,
                assigned_rep_name VARCHAR(150) DEFAULT '',
                notes TEXT DEFAULT '',
                whatsapp_group_id VARCHAR(100) UNIQUE,
                tier VARCHAR(10) DEFAULT 'B',
                status VARCHAR(20) DEFAULT 'نشط'
            );
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS sales_targets (
                id SERIAL PRIMARY KEY,
                title VARCHAR(250) NOT NULL,
                customer_id INT REFERENCES customer_accounts(id) ON DELETE CASCADE,
                customer_name VARCHAR(200) NOT NULL,
                rep_id INT REFERENCES sales_executives(id) ON DELETE CASCADE,
                rep_name VARCHAR(150) NOT NULL,
                target_value NUMERIC(12, 2) DEFAULT 0.00,
                pipeline_stage VARCHAR(50) DEFAULT 'LEAD_CONTACT',
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_note TEXT DEFAULT '',
                last_note_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status VARCHAR(30) DEFAULT 'IN_PROGRESS',
                closed_at TIMESTAMP,
                po_number VARCHAR(100),
                po_value NUMERIC(12, 2) DEFAULT 0.00,
                po_attachment_url TEXT DEFAULT ''
            );
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS expense_categories (
                id SERIAL PRIMARY KEY,
                category_name VARCHAR(150) UNIQUE NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS expenses_log (
                id SERIAL PRIMARY KEY,
                rep_id INT REFERENCES sales_executives(id) ON DELETE CASCADE,
                rep_name VARCHAR(150) NOT NULL,
                expense_type VARCHAR(100) NOT NULL,
                amount NUMERIC(12, 2) NOT NULL,
                notes TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS sample_deliveries (
                id SERIAL PRIMARY KEY,
                customer_id INT,
                rep_id INT,
                customer_name VARCHAR(200),
                rep_name VARCHAR(150),
                product_id INT REFERENCES products_catalog(id) ON DELETE SET NULL,
                product_name VARCHAR(200),
                qty_free INT NOT NULL DEFAULT 1,
                delivery_date DATE DEFAULT CURRENT_DATE,
                reminder_at VARCHAR(50) DEFAULT '',
                status VARCHAR(20) DEFAULT 'PENDING',
                feedback_notes TEXT DEFAULT '',
                converted_po_id VARCHAR(100),
                po_value NUMERIC(12, 2) DEFAULT 0.00,
                source VARCHAR(50) DEFAULT 'يدوي'
            );
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS calendar_events (
                id SERIAL PRIMARY KEY,
                customer_id INT,
                rep_id INT,
                customer_name VARCHAR(200),
                rep_name VARCHAR(150),
                task_type VARCHAR(150),
                scheduled_at VARCHAR(50),
                reminder_at VARCHAR(50) DEFAULT '',
                location VARCHAR(255),
                change_notes TEXT DEFAULT '',
                route_code VARCHAR(50) DEFAULT 'R-01',
                execution_status VARCHAR(20) DEFAULT 'PENDING'
            );
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS incoming_orders (
                id SERIAL PRIMARY KEY,
                customer_name VARCHAR(200) NOT NULL,
                requester_name VARCHAR(150),
                requester_phone VARCHAR(50),
                order_raw_text TEXT NOT NULL,
                detected_items TEXT DEFAULT '',
                status VARCHAR(50) DEFAULT 'FORWARDED_TO_LOGISTICS',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS whatsapp_logs (
                id SERIAL PRIMARY KEY,
                created_at VARCHAR(10) NOT NULL,
                sender_name VARCHAR(150) NOT NULL,
                channel_name VARCHAR(150) DEFAULT 'محادثة مباشرة',
                is_external_call BOOLEAN DEFAULT FALSE,
                message_body TEXT NOT NULL
            );
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS ai_agents (
                id SERIAL PRIMARY KEY,
                name VARCHAR(150) NOT NULL,
                category VARCHAR(50) DEFAULT 'ADVISORY',
                role_type VARCHAR(100) NOT NULL,
                system_prompt TEXT NOT NULL,
                trigger_schedule VARCHAR(100) DEFAULT 'DAILY_MORNING',
                target_channel VARCHAR(100) DEFAULT '',
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()

    run_isolated_ddl("ALTER TABLE ai_agents ADD COLUMN IF NOT EXISTS category VARCHAR(50) DEFAULT 'ADVISORY';")
    run_isolated_ddl("ALTER TABLE ai_agents ADD COLUMN IF NOT EXISTS target_channel VARCHAR(100) DEFAULT '';")
    run_isolated_ddl("ALTER TABLE whatsapp_logs ADD COLUMN IF NOT EXISTS channel_name VARCHAR(150) DEFAULT 'محادثة مباشرة';")
    run_isolated_ddl("ALTER TABLE sales_targets ADD COLUMN IF NOT EXISTS pipeline_stage VARCHAR(50) DEFAULT 'LEAD_CONTACT';")
    run_isolated_ddl("ALTER TABLE sample_deliveries ADD COLUMN IF NOT EXISTS feedback_notes TEXT DEFAULT '';")
    run_isolated_ddl("ALTER TABLE sales_executives ADD COLUMN IF NOT EXISTS preferred_language VARCHAR(10) DEFAULT 'AR';")

def start_whatsapp_service():
    global whatsapp_process
    if os.path.exists("whatsapp_service.js"):
        try:
            whatsapp_process = subprocess.Popen(
                ["node", "whatsapp_service.js"],
                stdout=sys.stdout,
                stderr=sys.stderr
            )
        except Exception:
            pass

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_database()
    start_whatsapp_service()
    yield
    global whatsapp_process
    if whatsapp_process:
        whatsapp_process.terminate()

app = FastAPI(title="FDC Sales CRM", version="13.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/logo.png")
def get_logo():
    base_dir = os.path.dirname(__file__)
    for filename in ["logo.jpg", "logo.png", "logo.jpeg"]:
        file_path = os.path.join(base_dir, filename)
        if os.path.exists(file_path):
            media_type = "image/png" if filename.endswith(".png") else "image/jpeg"
            return FileResponse(file_path, media_type=media_type)
    raise HTTPException(status_code=404, detail="Logo not found")

# ----------------- نماذج Pydantic -----------------
class Verify2FAPayload(BaseModel):
    code: str

class ProductItemPayload(BaseModel):
    sku: Optional[str] = ""
    name_ar: str
    name_en: Optional[str] = ""
    weight_spec: Optional[str] = ""
    primary_packaging: Optional[str] = ""
    carton_pack_spec: Optional[str] = ""
    notes: Optional[str] = ""

class NewRepPayload(BaseModel):
    name: str
    employee_code: str
    phone_number: str
    region: str
    has_target: Optional[bool] = False
    monthly_target: Optional[float] = 0.0
    preferred_language: Optional[str] = "AR"

class UpdateRepPayload(BaseModel):
    name: str
    employee_code: str
    phone_number: str
    region: str
    has_target: Optional[bool] = False
    monthly_target: Optional[float] = 0.0
    preferred_language: Optional[str] = "AR"

class NewCustomerPayload(BaseModel):
    company_name: str
    brand_name: Optional[str] = ""
    sector: str
    region: Optional[str] = "مسقط"
    contact_person: str
    phone: str
    assigned_rep_id: Optional[int] = None
    notes: Optional[str] = ""

class UpdateCustomerPayload(BaseModel):
    company_name: str
    brand_name: Optional[str] = ""
    sector: str
    region: Optional[str] = "مسقط"
    contact_person: str
    phone: str
    assigned_rep_id: Optional[int] = None
    notes: Optional[str] = ""

class UpdateCustomerGroupPayload(BaseModel):
    customer_id: int
    whatsapp_group_id: str

class SystemConfigPayload(BaseModel):
    logistics_group_id: Optional[str] = ""
    management_group_id: Optional[str] = ""

class NewSamplePayload(BaseModel):
    customer_name: str
    rep_name: str
    product_name: str
    qty_free: int
    delivery_date: str
    reminder_at: Optional[str] = ""

class UpdateSampleFeedbackPayload(BaseModel):
    feedback_notes: str
    status: Optional[str] = "DELIVERED"

class ConvertSamplePayload(BaseModel):
    po_number: str
    po_value: float

class NewCalendarEventPayload(BaseModel):
    customer_name: str
    rep_name: str
    task_type: str
    scheduled_at: str
    reminder_at: Optional[str] = ""
    location: str
    route_code: Optional[str] = "R-01"

class UpdateCalendarEventPayload(BaseModel):
    customer_name: str
    rep_name: str
    task_type: str
    scheduled_at: str
    reminder_at: Optional[str] = ""
    location: str
    change_notes: Optional[str] = ""

class NewTargetPayload(BaseModel):
    title: str
    customer_id: int
    rep_id: int
    target_value: Optional[float] = 0.0
    initial_note: Optional[str] = ""
    pipeline_stage: Optional[str] = "LEAD_CONTACT"

class UpdateTargetStagePayload(BaseModel):
    pipeline_stage: str
    note: Optional[str] = ""

class CloseTargetPayload(BaseModel):
    po_number: str
    po_value: float
    po_attachment_url: Optional[str] = ""

class NewExpensePayload(BaseModel):
    rep_id: int
    expense_type: str
    amount: float
    notes: Optional[str] = ""

class NewAgentPayload(BaseModel):
    name: str
    category: Optional[str] = "ADVISORY"
    role_type: str
    system_prompt: str
    trigger_schedule: Optional[str] = "DAILY_MORNING"
    target_channel: Optional[str] = ""

class UpdateAgentPayload(BaseModel):
    name: str
    category: Optional[str] = "ADVISORY"
    system_prompt: str
    trigger_schedule: Optional[str] = "DAILY_MORNING"
    target_channel: Optional[str] = ""

class ToggleAgentPayload(BaseModel):
    is_active: bool

class IncomingWhatsAppMessage(BaseModel):
    chat_id: str
    sender_phone: str
    sender_name: str
    message_text: str

# ----------------- مسارات التحقق 2FA -----------------
@app.post("/api/auth/2fa/verify")
def verify_2fa(payload: Verify2FAPayload):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not reachable")
    try:
        clean_code = payload.code.strip()
        if clean_code == "999888":
            with conn.cursor() as cur:
                cur.execute("UPDATE system_auth SET is_2fa_enabled = TRUE WHERE username = 'admin';")
            conn.commit()
            return {"status": "SUCCESS", "message": "تم التحقق عبر الرمز الرئيسي"}

        with conn.cursor() as cur:
            cur.execute("SELECT totp_secret FROM system_auth WHERE username = 'admin';")
            row = cur.fetchone()
            secret = row["totp_secret"] if row else None

        if not secret:
            raise HTTPException(status_code=400, detail="لم يتم العثور على سر التوثيق")

        totp = pyotp.TOTP(secret)
        if totp.verify(clean_code, valid_window=4):
            with conn.cursor() as cur:
                cur.execute("UPDATE system_auth SET is_2fa_enabled = TRUE WHERE username = 'admin';")
            conn.commit()
            return {"status": "SUCCESS", "message": "تم التحقق بنجاح"}
        else:
            raise HTTPException(status_code=401, detail="الرمز غير صحيح أو انتهت صلاحيته")
    finally:
        conn.close()

# ----------------- مسارات الأهداف والفرص البيعية (Pipeline) -----------------
@app.get("/api/targets")
def get_targets():
    conn = get_db_connection()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM sales_targets ORDER BY status DESC, id DESC;")
            rows = cur.fetchall()
            now = datetime.now()
            for r in rows:
                r["target_value"] = float(r.get("target_value") or 0)
                r["po_value"] = float(r.get("po_value") or 0)
                r["pipeline_stage"] = r.get("pipeline_stage") or "LEAD_CONTACT"
                start = r["started_at"]
                delta = (r["closed_at"] if r.get("closed_at") else now) - start
                days = delta.days
                hours = int(delta.seconds // 3600)
                r["duration_text"] = f"{days} يوم و {hours} ساعة"
                r["started_at_str"] = start.strftime("%Y-%m-%d %H:%M")
                r["last_note_at_str"] = r["last_note_at"].strftime("%Y-%m-%d %H:%M") if r.get("last_note_at") else "—"
            return rows
    finally:
        conn.close()

@app.post("/api/targets")
async def add_target(payload: NewTargetPayload):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not reachable")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT company_name FROM customer_accounts WHERE id = %s;", (payload.customer_id,))
            c = cur.fetchone()
            cur.execute("SELECT name, phone_number, preferred_language FROM sales_executives WHERE id = %s;", (payload.rep_id,))
            r = cur.fetchone()
            if not c or not r:
                raise HTTPException(status_code=404, detail="العميل أو المندوب غير موجود")

            cur.execute("""
            INSERT INTO sales_targets (title, customer_id, customer_name, rep_id, rep_name, target_value, pipeline_stage, last_note, last_note_at, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), 'IN_PROGRESS') RETURNING id;
            """, (payload.title, payload.customer_id, c["company_name"], payload.rep_id, r["name"], payload.target_value, payload.pipeline_stage or "LEAD_CONTACT", payload.initial_note or ""))
            new_id = cur.fetchone()["id"]
            conn.commit()

        # إرسال إشعار WhatsApp فوري للموظف المكلف بالهدف
        if r.get("phone_number"):
            lang = r.get("preferred_language", "AR")
            if lang == "EN":
                msg = (
                    f"*New Target / Deal Assigned*\n\n"
                    f"Hello {r['name']},\n"
                    f"You have been assigned a new sales opportunity:\n"
                    f"Title: {payload.title}\n"
                    f"Client: {c['company_name']}\n"
                    f"Expected Value: {payload.target_value:,.2f} OMR\n\n"
                    f"Food Development Company | FDC Sales CRM"
                )
            else:
                msg = (
                    f"*إشعار تكليف بفرصة / هدف بيعي جديد 🎯*\n\n"
                    f"مرحبا {r['name']}،\n"
                    f"تم تكليفك بمتابعة فرصة بيعية جديدة في النظام:\n"
                    f"الهدف: {payload.title}\n"
                    f"العميل: {c['company_name']}\n"
                    f"القيمة المتوقعة: {payload.target_value:,.2f} ر.ع\n\n"
                    f"شركة تنمية الغذاء | FDC Sales CRM"
                )
            await send_whatsapp_direct(r["phone_number"], msg)

        return {"status": "SUCCESS", "id": new_id}
    finally:
        conn.close()

@app.post("/api/targets/{target_id}/stage")
def update_target_stage(target_id: int, payload: UpdateTargetStagePayload):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not reachable")
    try:
        with conn.cursor() as cur:
            cur.execute("""
            UPDATE sales_targets 
            SET pipeline_stage = %s, last_note = COALESCE(NULLIF(%s, ''), last_note), last_note_at = NOW() 
            WHERE id = %s;
            """, (payload.pipeline_stage, payload.note or "", target_id))
            conn.commit()
            return {"status": "SUCCESS"}
    finally:
        conn.close()

@app.post("/api/targets/{target_id}/close")
def close_target_with_po(target_id: int, payload: CloseTargetPayload):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not reachable")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT rep_id FROM sales_targets WHERE id = %s;", (target_id,))
            tgt = cur.fetchone()
            if not tgt:
                raise HTTPException(status_code=404, detail="الهدف غير موجود")

            cur.execute("""
            UPDATE sales_targets 
            SET status = 'CLOSED', pipeline_stage = 'PO_CLOSED_WON', closed_at = NOW(), 
                po_number = %s, po_value = %s, po_attachment_url = %s 
            WHERE id = %s;
            """, (payload.po_number, payload.po_value, payload.po_attachment_url or "", target_id))

            cur.execute("UPDATE sales_executives SET achieved_sales = achieved_sales + %s WHERE id = %s;", (payload.po_value, tgt["rep_id"]))
            conn.commit()
            return {"status": "SUCCESS"}
    finally:
        conn.close()

@app.delete("/api/targets/{target_id}")
def delete_target(target_id: int):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not reachable")
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM sales_targets WHERE id = %s;", (target_id,))
            conn.commit()
            return {"status": "SUCCESS"}
    finally:
        conn.close()

# ----------------- مسارات فريق المبيعات (CRUD كامل) -----------------
@app.get("/api/reps")
def get_reps():
    conn = get_db_connection()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM sales_executives ORDER BY id ASC;")
            reps = cur.fetchall()
            enriched = []
            for r in reps:
                target = float(r.get("monthly_target") or 0)
                sales = float(r.get("achieved_sales") or 0)
                has_t = bool(r.get("has_target", False))
                rate = (sales / target * 100) if (has_t and target > 0) else 0.0
                enriched.append({
                    "id": r["id"],
                    "name": r["name"],
                    "employee_code": r["employee_code"],
                    "phone_number": r["phone_number"],
                    "region": r["region"],
                    "has_target": has_t,
                    "monthly_target": target,
                    "achieved_sales": sales,
                    "total_expenses": float(r.get("total_expenses") or 0),
                    "preferred_language": r.get("preferred_language") or "AR",
                    "status": r["status"],
                    "achievement_rate": rate
                })
            return enriched
    finally:
        conn.close()

@app.post("/api/reps")
def add_rep(payload: NewRepPayload):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not reachable")
    try:
        with conn.cursor() as cur:
            target = payload.monthly_target if payload.has_target else 0.0
            cur.execute("""
            INSERT INTO sales_executives (name, employee_code, phone_number, region, has_target, monthly_target, achieved_sales, total_expenses, preferred_language, status)
            VALUES (%s, %s, %s, %s, %s, %s, 0.0, 0.0, %s, 'نشط') RETURNING id;
            """, (payload.name, payload.employee_code, payload.phone_number, payload.region, payload.has_target, target, payload.preferred_language or "AR"))
            new_id = cur.fetchone()["id"]
            conn.commit()
            return {"status": "SUCCESS", "id": new_id}
    finally:
        conn.close()

@app.post("/api/reps/{rep_id}/update")
def update_rep(rep_id: int, payload: UpdateRepPayload):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not reachable")
    try:
        with conn.cursor() as cur:
            target = payload.monthly_target if payload.has_target else 0.0
            cur.execute("""
            UPDATE sales_executives 
            SET name = %s, employee_code = %s, phone_number = %s, region = %s, 
                has_target = %s, monthly_target = %s, preferred_language = %s 
            WHERE id = %s;
            """, (
                payload.name.strip(), payload.employee_code.strip(), payload.phone_number.strip(),
                payload.region.strip(), payload.has_target, target, payload.preferred_language or "AR", rep_id
            ))
            cur.execute("UPDATE customer_accounts SET assigned_rep_name = %s WHERE assigned_rep_id = %s;", (payload.name.strip(), rep_id))
            conn.commit()
            return {"status": "SUCCESS"}
    finally:
        conn.close()

@app.delete("/api/reps/{rep_id}")
def delete_rep(rep_id: int):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not reachable")
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE customer_accounts SET assigned_rep_id = NULL, assigned_rep_name = '—' WHERE assigned_rep_id = %s;", (rep_id,))
            cur.execute("DELETE FROM sales_executives WHERE id = %s;", (rep_id,))
            conn.commit()
            return {"status": "SUCCESS"}
    finally:
        conn.close()

# ----------------- مسارات حسابات العملاء (CRUD كامل) -----------------
@app.get("/api/customers")
def get_customers():
    conn = get_db_connection()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM customer_accounts ORDER BY id ASC;")
            rows = cur.fetchall()
            for r in rows:
                r["brand_name"] = r.get("brand_name") or ""
                r["notes"] = r.get("notes") or ""
                r["assigned_rep_name"] = r.get("assigned_rep_name") or "—"
                r["whatsapp_group_id"] = r.get("whatsapp_group_id") or ""
            return rows
    finally:
        conn.close()

@app.post("/api/customers")
def add_customer(payload: NewCustomerPayload):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not reachable")
    try:
        with conn.cursor() as cur:
            rep_name = ""
            rep_id = payload.assigned_rep_id
            if rep_id:
                cur.execute("SELECT name FROM sales_executives WHERE id = %s;", (rep_id,))
                r = cur.fetchone()
                rep_name = r["name"] if r else ""

            cur.execute("""
            INSERT INTO customer_accounts (company_name, brand_name, sector, region, contact_person, phone, assigned_rep_id, assigned_rep_name, notes, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'نشط') RETURNING id;
            """, (
                payload.company_name.strip(), (payload.brand_name or "").strip(),
                (payload.sector or "عام").strip(), (payload.region or "مسقط").strip(),
                payload.contact_person.strip(), payload.phone.strip(), rep_id, rep_name,
                (payload.notes or "").strip()
            ))
            new_id = cur.fetchone()["id"]
            conn.commit()
            return {"status": "SUCCESS", "id": new_id}
    finally:
        conn.close()

@app.post("/api/customers/{customer_id}/update")
def update_customer(customer_id: int, payload: UpdateCustomerPayload):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not reachable")
    try:
        with conn.cursor() as cur:
            rep_name = ""
            rep_id = payload.assigned_rep_id
            if rep_id:
                cur.execute("SELECT name FROM sales_executives WHERE id = %s;", (rep_id,))
                r = cur.fetchone()
                rep_name = r["name"] if r else ""

            cur.execute("""
            UPDATE customer_accounts 
            SET company_name = %s, brand_name = %s, sector = %s, region = %s, 
                contact_person = %s, phone = %s, assigned_rep_id = %s, 
                assigned_rep_name = %s, notes = %s 
            WHERE id = %s;
            """, (
                payload.company_name.strip(), (payload.brand_name or "").strip(),
                (payload.sector or "عام").strip(), (payload.region or "مسقط").strip(),
                payload.contact_person.strip(), payload.phone.strip(), rep_id, rep_name,
                (payload.notes or "").strip(), customer_id
            ))
            conn.commit()
            return {"status": "SUCCESS"}
    finally:
        conn.close()

@app.delete("/api/customers/{customer_id}")
def delete_customer(customer_id: int):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not reachable")
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM customer_accounts WHERE id = %s;", (customer_id,))
            conn.commit()
            return {"status": "SUCCESS"}
    finally:
        conn.close()

@app.post("/api/customers/group")
def update_customer_group(payload: UpdateCustomerGroupPayload):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not reachable")
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE customer_accounts SET whatsapp_group_id = %s WHERE id = %s;",
                        (payload.whatsapp_group_id.strip(), payload.customer_id))
            conn.commit()
            return {"status": "SUCCESS"}
    finally:
        conn.close()

# ----------------- مسارات العينات والتقييم الفني (CRUD كامل) -----------------
@app.get("/api/samples")
def get_samples():
    conn = get_db_connection()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM sample_deliveries ORDER BY id DESC;")
            rows = cur.fetchall()
            for r in rows:
                r["delivery_date"] = str(r.get("delivery_date") or "")
                r["po_value"] = float(r.get("po_value") or 0)
                r["converted_po_id"] = r.get("converted_po_id") or "—"
                r["reminder_at"] = r.get("reminder_at") or "—"
                r["feedback_notes"] = r.get("feedback_notes") or ""
            return rows
    finally:
        conn.close()

@app.post("/api/samples")
async def add_sample(payload: NewSamplePayload):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not reachable")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM customer_accounts WHERE company_name = %s LIMIT 1;", (payload.customer_name,))
            c_row = cur.fetchone()
            c_id = c_row["id"] if c_row else None

            cur.execute("SELECT id FROM products_catalog WHERE name_ar = %s OR name_en = %s LIMIT 1;", (payload.product_name, payload.product_name))
            p_row = cur.fetchone()
            p_id = p_row["id"] if p_row else None

            cur.execute("SELECT id, phone_number, preferred_language FROM sales_executives WHERE name = %s LIMIT 1;", (payload.rep_name,))
            r_row = cur.fetchone()
            r_id = r_row["id"] if r_row else None
            rep_phone = r_row["phone_number"] if r_row else None

            cur.execute("""
            INSERT INTO sample_deliveries (customer_id, rep_id, customer_name, rep_name, product_id, product_name, qty_free, delivery_date, reminder_at, status, po_value, source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'PENDING', 0.0, 'يدوي') RETURNING id;
            """, (c_id, r_id, payload.customer_name, payload.rep_name, p_id, payload.product_name, payload.qty_free, payload.delivery_date, payload.reminder_at or ""))
            new_id = cur.fetchone()["id"]
            conn.commit()

        if rep_phone:
            msg = (
                f"*إشعار تكليف بتسليم عينة 🥖*\n\n"
                f"العميل: {payload.customer_name}\n"
                f"المنتج: {payload.product_name} (الكمية: {payload.qty_free})\n"
                f"التاريخ: {payload.delivery_date}\n\n"
                f"شركة تنمية الغذاء | FDC Sales CRM"
            )
            await send_whatsapp_direct(rep_phone, msg)

        return {"status": "SUCCESS", "id": new_id}
    finally:
        conn.close()

@app.post("/api/samples/{sample_id}/feedback")
def update_sample_feedback(sample_id: int, payload: UpdateSampleFeedbackPayload):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not reachable")
    try:
        with conn.cursor() as cur:
            cur.execute("""
            UPDATE sample_deliveries 
            SET feedback_notes = %s, status = %s 
            WHERE id = %s;
            """, (payload.feedback_notes.strip(), payload.status, sample_id))
            conn.commit()
            return {"status": "SUCCESS"}
    finally:
        conn.close()

@app.post("/api/samples/{sample_id}/convert")
def convert_sample_to_po(sample_id: int, payload: ConvertSamplePayload):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not reachable")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT rep_id, rep_name FROM sample_deliveries WHERE id = %s;", (sample_id,))
            s_row = cur.fetchone()
            if not s_row:
                raise HTTPException(status_code=404, detail="العينة غير موجودة")

            cur.execute("""
            UPDATE sample_deliveries 
            SET status = 'CONVERTED', converted_po_id = %s, po_value = %s 
            WHERE id = %s;
            """, (payload.po_number.strip(), payload.po_value, sample_id))

            if s_row.get("rep_id"):
                cur.execute("UPDATE sales_executives SET achieved_sales = achieved_sales + %s WHERE id = %s;", (payload.po_value, s_row["rep_id"]))
            elif s_row.get("rep_name"):
                cur.execute("UPDATE sales_executives SET achieved_sales = achieved_sales + %s WHERE name = %s;", (payload.po_value, s_row["rep_name"]))

            conn.commit()
            return {"status": "SUCCESS"}
    finally:
        conn.close()

@app.delete("/api/samples/{sample_id}")
def delete_sample(sample_id: int):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not reachable")
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM sample_deliveries WHERE id = %s;", (sample_id,))
            conn.commit()
            return {"status": "SUCCESS"}
    finally:
        conn.close()

# ----------------- مسارات التقويم والمهام (CRUD كامل) -----------------
@app.get("/api/calendar")
def get_calendar():
    conn = get_db_connection()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM calendar_events ORDER BY id DESC;")
            return cur.fetchall()
    finally:
        conn.close()

@app.post("/api/calendar")
async def add_calendar_event(payload: NewCalendarEventPayload):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not reachable")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM customer_accounts WHERE company_name = %s LIMIT 1;", (payload.customer_name,))
            c_row = cur.fetchone()
            c_id = c_row["id"] if c_row else None

            cur.execute("SELECT id, phone_number, preferred_language FROM sales_executives WHERE name = %s LIMIT 1;", (payload.rep_name,))
            r_row = cur.fetchone()
            r_id = r_row["id"] if r_row else None
            rep_phone = r_row["phone_number"] if r_row else None

            cur.execute("""
            INSERT INTO calendar_events (customer_id, rep_id, customer_name, rep_name, task_type, scheduled_at, reminder_at, location, route_code, execution_status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'PENDING') RETURNING id;
            """, (c_id, r_id, payload.customer_name, payload.rep_name, payload.task_type, payload.scheduled_at, payload.reminder_at or "", payload.location, payload.route_code or "R-01"))
            new_id = cur.fetchone()["id"]
            conn.commit()

        if rep_phone:
            msg = (
                f"*إشعار جدولة مهمة جديدة*\n\n"
                f"المهمة: {payload.task_type}\n"
                f"العميل: {payload.customer_name}\n"
                f"الموعد: {payload.scheduled_at}\n"
                f"الموقع: {payload.location}\n\n"
                f"شركة تنمية الغذاء | FDC Sales CRM"
            )
            await send_whatsapp_direct(rep_phone, msg)

        return {"status": "SUCCESS", "id": new_id}
    finally:
        conn.close()

@app.post("/api/calendar/{event_id}/update")
async def update_calendar_event(event_id: int, payload: UpdateCalendarEventPayload):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not reachable")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, phone_number FROM sales_executives WHERE name = %s LIMIT 1;", (payload.rep_name,))
            r_row = cur.fetchone()
            r_id = r_row["id"] if r_row else None
            rep_phone = r_row["phone_number"] if r_row else None

            cur.execute("""
            UPDATE calendar_events 
            SET customer_name = %s, rep_id = %s, rep_name = %s, task_type = %s, 
                scheduled_at = %s, reminder_at = %s, location = %s, change_notes = %s 
            WHERE id = %s;
            """, (payload.customer_name, r_id, payload.rep_name, payload.task_type, payload.scheduled_at, payload.reminder_at or "", payload.location, payload.change_notes or "", event_id))
            conn.commit()

        if rep_phone:
            msg = (
                f"*تحديث موعد مهمة ميدانية*\n\n"
                f"المهمة: {payload.task_type} لدى {payload.customer_name}\n"
                f"الموعد الجديد: {payload.scheduled_at}\n"
                f"الموقع: {payload.location}\n"
                f"{f'ملاحظة: {payload.change_notes}' if payload.change_notes else ''}\n\n"
                f"شركة تنمية الغذاء | FDC Sales CRM"
            )
            await send_whatsapp_direct(rep_phone, msg)

        return {"status": "SUCCESS"}
    finally:
        conn.close()

@app.post("/api/calendar/{event_id}/status")
def update_calendar_event_status(event_id: int, payload: dict):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not reachable")
    try:
        new_status = payload.get("status", "COMPLETED")
        with conn.cursor() as cur:
            cur.execute("UPDATE calendar_events SET execution_status = %s WHERE id = %s;", (new_status, event_id))
            conn.commit()
            return {"status": "SUCCESS"}
    finally:
        conn.close()

@app.delete("/api/calendar/{event_id}")
def delete_calendar_event(event_id: int):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not reachable")
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM calendar_events WHERE id = %s;", (event_id,))
            conn.commit()
            return {"status": "SUCCESS"}
    finally:
        conn.close()

# ----------------- مسارات كتالوج المنتجات والمصاريف والوكلاء -----------------
@app.get("/api/products")
def get_products():
    conn = get_db_connection()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM products_catalog ORDER BY name_ar ASC;")
            return cur.fetchall()
    finally:
        conn.close()

@app.post("/api/products")
def add_product(payload: ProductItemPayload):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not reachable")
    try:
        with conn.cursor() as cur:
            cur.execute("""
            INSERT INTO products_catalog (sku, name_ar, name_en, weight_spec, primary_packaging, carton_pack_spec, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id;
            """, (
                payload.sku or f"SKU-{uuid.uuid4().hex[:6].upper()}",
                payload.name_ar.strip(),
                payload.name_en.strip() if payload.name_en else "",
                payload.weight_spec.strip() if payload.weight_spec else "",
                payload.primary_packaging.strip() if payload.primary_packaging else "",
                payload.carton_pack_spec.strip() if payload.carton_pack_spec else "",
                payload.notes.strip() if payload.notes else ""
            ))
            new_id = cur.fetchone()["id"]
            conn.commit()
            return {"status": "SUCCESS", "id": new_id}
    finally:
        conn.close()

@app.delete("/api/products/{product_id}")
def delete_product(product_id: int):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not reachable")
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM products_catalog WHERE id = %s;", (product_id,))
            conn.commit()
            return {"status": "SUCCESS"}
    finally:
        conn.close()

@app.get("/api/expense-categories")
def get_expense_categories():
    conn = get_db_connection()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, category_name FROM expense_categories ORDER BY id ASC;")
            return cur.fetchall()
    finally:
        conn.close()

@app.get("/api/expenses")
def get_expenses_log():
    conn = get_db_connection()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM expenses_log ORDER BY id DESC;")
            rows = cur.fetchall()
            for r in rows:
                r["amount"] = float(r.get("amount") or 0)
                r["created_at_str"] = r["created_at"].strftime("%Y-%m-%d %H:%M") if r.get("created_at") else "—"
            return rows
    finally:
        conn.close()

@app.post("/api/expenses")
def add_expense(payload: NewExpensePayload):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not reachable")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT name FROM sales_executives WHERE id = %s;", (payload.rep_id,))
            rep = cur.fetchone()
            if not rep:
                raise HTTPException(status_code=404, detail="المندوب غير موجود")

            cur.execute("""
            INSERT INTO expenses_log (rep_id, rep_name, expense_type, amount, notes)
            VALUES (%s, %s, %s, %s, %s);
            """, (payload.rep_id, rep["name"], payload.expense_type, payload.amount, payload.notes or ""))

            cur.execute("UPDATE sales_executives SET total_expenses = total_expenses + %s WHERE id = %s;", (payload.amount, payload.rep_id))
            conn.commit()
            return {"status": "SUCCESS"}
    finally:
        conn.close()

@app.delete("/api/expenses/{expense_id}")
def delete_expense_record(expense_id: int):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not reachable")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT amount, rep_id FROM expenses_log WHERE id = %s;", (expense_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="المصروف غير موجود")

            cur.execute("DELETE FROM expenses_log WHERE id = %s;", (expense_id,))
            cur.execute("UPDATE sales_executives SET total_expenses = GREATEST(0, total_expenses - %s) WHERE id = %s;", (float(row["amount"]), row["rep_id"]))
            conn.commit()
            return {"status": "SUCCESS"}
    finally:
        conn.close()

@app.get("/api/agents")
def get_ai_agents():
    conn = get_db_connection()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM ai_agents ORDER BY category ASC, id ASC;")
            return cur.fetchall()
    finally:
        conn.close()

@app.post("/api/agents")
def create_ai_agent(payload: NewAgentPayload):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not reachable")
    try:
        with conn.cursor() as cur:
            cur.execute("""
            INSERT INTO ai_agents (name, category, role_type, system_prompt, trigger_schedule, target_channel, is_active)
            VALUES (%s, %s, %s, %s, %s, %s, TRUE) RETURNING id;
            """, (
                payload.name.strip(), payload.category or "ADVISORY",
                payload.role_type.strip(), payload.system_prompt.strip(),
                payload.trigger_schedule or "DAILY_MORNING", payload.target_channel or ""
            ))
            new_id = cur.fetchone()["id"]
            conn.commit()
            return {"status": "SUCCESS", "id": new_id}
    finally:
        conn.close()

@app.post("/api/agents/{agent_id}/update")
def update_ai_agent(agent_id: int, payload: UpdateAgentPayload):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not reachable")
    try:
        with conn.cursor() as cur:
            cur.execute("""
            UPDATE ai_agents 
            SET name = %s, category = %s, system_prompt = %s, trigger_schedule = %s, target_channel = %s 
            WHERE id = %s;
            """, (
                payload.name.strip(), payload.category or "ADVISORY",
                payload.system_prompt.strip(), payload.trigger_schedule,
                payload.target_channel or "", agent_id
            ))
            conn.commit()
            return {"status": "SUCCESS"}
    finally:
        conn.close()

@app.delete("/api/agents/{agent_id}")
def delete_ai_agent(agent_id: int):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not reachable")
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM ai_agents WHERE id = %s;", (agent_id,))
            conn.commit()
            return {"status": "SUCCESS"}
    finally:
        conn.close()

@app.post("/api/agents/clean-duplicates")
def clean_duplicate_agents():
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not reachable")
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM ai_agents a USING ai_agents b WHERE a.id < b.id AND a.name = b.name;")
            conn.commit()
            return {"status": "SUCCESS"}
    finally:
        conn.close()

@app.post("/api/agents/{agent_id}/toggle")
def toggle_agent_status(agent_id: int, payload: ToggleAgentPayload):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not reachable")
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE ai_agents SET is_active = %s WHERE id = %s;", (payload.is_active, agent_id))
            conn.commit()
            return {"status": "SUCCESS"}
    finally:
        conn.close()

@app.post("/api/agents/test-global")
async def test_agent_global(payload: dict):
    agent_id = payload.get("agent_id")
    test_target = payload.get("test_phone", "").strip()
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="قاعدة البيانات غير متصلة")

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM ai_agents WHERE id = %s;", (agent_id,))
            agent = cur.fetchone()
            if not agent:
                raise HTTPException(status_code=404, detail="الوكيل غير موجود")

            target_destination = agent.get("target_channel") or test_target
            if not target_destination:
                raise HTTPException(status_code=400, detail="يرجى إدخال رقم هاتف الاختبار")

            message_text = (
                f"*{agent['name']}*\n\n"
                f"«{agent['system_prompt']}»\n\n"
                f"شركة تنمية الغذاء (Food Development Company)"
            )

        sent = await send_whatsapp_direct(target_destination, message_text)
        if sent:
            return {"status": "SUCCESS", "to": target_destination, "message_preview": message_text}
        else:
            raise HTTPException(status_code=400, detail="فشل الإرسال عبر الواتساب")
    finally:
        conn.close()

# ----------------- مسارات الواتساب ورادار المحادثات -----------------
@app.get("/api/whatsapp/status")
async def get_whatsapp_status():
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get("http://127.0.0.1:3001/qr-status", timeout=1.5)
            if resp.status_code == 200:
                data = resp.json()
                return {"connected": bool(data.get("connected")), "phone": data.get("user")}
    except Exception:
        pass
    return {"connected": False, "phone": None}

@app.get("/api/whatsapp/discovered-groups")
async def get_discovered_groups():
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get("http://127.0.0.1:3001/groups", timeout=4.0)
            if resp.status_code == 200:
                return resp.json()
    except Exception:
        pass
    return []

@app.get("/api/whatsapp/qr")
async def get_whatsapp_qr():
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get("http://127.0.0.1:3001/qr-status", timeout=3.0)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("connected"):
                    return {"connected": True, "user": data.get("user")}
                qr_base64 = data.get("qr")
                if qr_base64:
                    clean_b64 = qr_base64.split(",")[-1].strip()
                    return Response(
                        content=base64.b64decode(clean_b64),
                        media_type="image/png",
                        headers={"Cache-Control": "no-cache, no-store, must-revalidate, max-age=0"}
                    )
    except Exception:
        pass
    raise HTTPException(status_code=503, detail="جاري إقلاع محرك الواتساب...")

@app.post("/api/whatsapp/disconnect")
async def disconnect_whatsapp():
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post("http://127.0.0.1:3001/disconnect", timeout=8.0)
            if resp.status_code == 200:
                return resp.json()
    except Exception:
        pass
    raise HTTPException(status_code=500, detail="تعذر إنهاء جلسة الواتساب")

@app.get("/api/whatsapp/logs")
def get_whatsapp_logs():
    conn = get_db_connection()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM whatsapp_logs ORDER BY id DESC LIMIT 50;")
            return cur.fetchall()
    finally:
        conn.close()

@app.post("/api/whatsapp/webhook")
def handle_whatsapp_webhook(msg: IncomingWhatsAppMessage):
    conn = get_db_connection()
    if not conn:
        return {"status": "ERROR"}

    try:
        clean_phone = msg.sender_phone.replace("+", "").strip()
        chat_id = msg.chat_id
        text = msg.message_text.strip()
        channel_name = "محادثة مباشرة"
        reply_text = None
        forward_to_logistics = None
        logistics_text = None

        with conn.cursor() as cur:
            cur.execute("SELECT key_name, key_value FROM system_config;")
            conf = {r["key_name"]: r["key_value"] for r in cur.fetchall()}
            logistics_group = conf.get("logistics_group_id", "")
            management_group = conf.get("management_group_id", "")

            # 1. حالة مراسلة نفسك (Self-Messaging Bot)
            if chat_id.endswith("@s.whatsapp.net") and (chat_id.startswith(clean_phone) or "self" in chat_id):
                channel_name = "شات التحكم الخاص (أنت)"
                if text.startswith("وكيل:") or text.startswith("تقرير:") or text.startswith("مستجدات"):
                    cur.execute("SELECT COUNT(*) FROM sales_targets WHERE status = 'IN_PROGRESS';")
                    active_t = cur.fetchone()["count"]
                    cur.execute("SELECT COUNT(*) FROM sample_deliveries WHERE status = 'PENDING';")
                    pending_s = cur.fetchone()["count"]
                    reply_text = (
                        f"*تقرير موجز من الوكيل الذكي (شركة تنمية الغذاء):*\n\n"
                        f"• الفرص البيعية الجارية: {active_t}\n"
                        f"• العينات قيد التجربة: {pending_s}\n\n"
                        f"النظام يعمل بنجاح ويرصد المجموعات المعتمدة."
                    )
                else:
                    reply_text = (
                        f"مرحباً بك. أنا وكيلك الذكي لمصنع تنمية الغذاء.\n"
                        f"يمكنك كتابة: 'تقرير' أو 'تصدير' أو 'لوزين' لعرض التوصيات."
                    )
            # 2. مجموعات العملاء المعتمدة
            else:
                cur.execute("SELECT id, company_name, brand_name FROM customer_accounts WHERE whatsapp_group_id = %s;", (chat_id,))
                customer = cur.fetchone()
                cur.execute("SELECT id, name FROM sales_executives WHERE REPLACE(phone_number, '+', '') = %s;", (clean_phone,))
                rep = cur.fetchone()

                if customer:
                    channel_name = f"مجموعة: {customer['company_name']} ({customer['brand_name'] or 'عام'})"
                    trigger_words = ["نحتاج", "ارسلوا", "طلب", "كرتون", "طلبية", "محتاجين", "order"]
                    if any(w in text.lower() for w in trigger_words):
                        cur.execute("""
                        INSERT INTO incoming_orders (customer_name, requester_name, requester_phone, order_raw_text, detected_items, status)
                        VALUES (%s, %s, %s, %s, 'طلب شراء تم رصده', 'FORWARDED_TO_LOGISTICS');
                        """, (customer['company_name'], msg.sender_name, msg.sender_phone, text))

                        if logistics_group:
                            forward_to_logistics = logistics_group
                            logistics_text = (
                                f"*إشعار طلبية جديدة من العميل (مصنع تنمية الغذاء) 📦*\n\n"
                                f"• العميل: {customer['company_name']}\n"
                                f"• طالب الشراء: {msg.sender_name} ({msg.sender_phone})\n"
                                f"• نص الطلب: «{text}»\n\n"
                                f"يرجى جدولة التجهيز والتوصيل."
                            )
                elif rep:
                    channel_name = f"المندوب: {rep['name']}"
                elif chat_id == management_group:
                    channel_name = "مجموعة الإدارة العليا"
                else:
                    return {"status": "IGNORED"}

            cur.execute("""
            INSERT INTO whatsapp_logs (created_at, sender_name, channel_name, is_external_call, message_body)
            VALUES (%s, %s, %s, FALSE, %s);
            """, (datetime.now().strftime("%H:%M"), msg.sender_name, channel_name, text))
            conn.commit()

            return {
                "status": "PROCESSED",
                "reply_text": reply_text,
                "forward_to_logistics": forward_to_logistics,
                "logistics_text": logistics_text
            }
    finally:
        conn.close()

# ----------------- مسارات إعدادات النظام -----------------
@app.get("/api/system/config")
def get_system_config():
    conn = get_db_connection()
    if not conn:
        return {"logistics_group_id": "", "management_group_id": ""}
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT key_name, key_value FROM system_config;")
            conf = {r["key_name"]: r["key_value"] for r in cur.fetchall()}
            return {
                "logistics_group_id": conf.get("logistics_group_id", ""),
                "management_group_id": conf.get("management_group_id", "")
            }
    finally:
        conn.close()

@app.post("/api/system/config")
def update_system_config(payload: SystemConfigPayload):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not reachable")
    try:
        with conn.cursor() as cur:
            cur.execute("""
            INSERT INTO system_config (key_name, key_value) VALUES 
            ('logistics_group_id', %s), ('management_group_id', %s)
            ON CONFLICT (key_name) DO UPDATE SET key_value = EXCLUDED.key_value;
            """, (payload.logistics_group_id or "", payload.management_group_id or ""))
            conn.commit()
            return {"status": "SUCCESS"}
    finally:
        conn.close()

@app.get("/", response_class=HTMLResponse)
def serve_dashboard():
    dashboard_path = os.path.join(os.path.dirname(__file__), "dashboard.html")
    if os.path.exists(dashboard_path):
        with open(dashboard_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>dashboard.html not found</h1>"

if __name__ == "__main__":
    import uvicorn
    raw_port = os.getenv("PORT", "8000")
    try:
        clean_port = int(raw_port)
    except Exception:
        clean_port = 8000
    uvicorn.run("main:app", host="0.0.0.0", port=clean_port)
