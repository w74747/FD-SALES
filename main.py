"""
main.py - Enterprise AI Sales CRM & Field Intelligence
Food Development Company (شركة تنمية الغذاء)
FastAPI Backend + PostgreSQL Persistence + Hardened 2FA Security + Multi-Agent Automation
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
from datetime import datetime
from typing import Optional, List
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Response, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse
from pydantic import BaseModel
import psycopg2
from psycopg2.extras import RealDictCursor
import pyotp
import qrcode
import httpx

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
        logger.error("DATABASE_URL is empty.")
        return None
    try:
        conn_url = DATABASE_URL
        if conn_url.startswith("postgres://"):
            conn_url = conn_url.replace("postgres://", "postgresql://", 1)
        return psycopg2.connect(conn_url, cursor_factory=RealDictCursor, connect_timeout=5)
    except Exception as e:
        logger.error(f"Database connection error: {e}")
        return None

def run_isolated_ddl(sql_statement: str):
    conn = get_db_connection()
    if not conn:
        return
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(sql_statement)
    except Exception as e:
        logger.warning(f"DDL statement bypassed ({e}): {sql_statement[:50]}")
    finally:
        conn.close()

def init_database():
    conn = get_db_connection()
    if not conn:
        logger.warning("DATABASE_URL not found. Running in local state.")
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
                customer_name VARCHAR(200),
                rep_name VARCHAR(150),
                product_name VARCHAR(200),
                qty_free INT NOT NULL DEFAULT 1,
                delivery_date DATE DEFAULT CURRENT_DATE,
                status VARCHAR(20) DEFAULT 'PENDING',
                converted_po_id VARCHAR(100),
                po_value NUMERIC(12, 2) DEFAULT 0.00,
                source VARCHAR(50) DEFAULT 'يدوي'
            );
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS calendar_events (
                id SERIAL PRIMARY KEY,
                customer_name VARCHAR(200),
                rep_name VARCHAR(150),
                task_type VARCHAR(150),
                scheduled_at VARCHAR(50),
                location VARCHAR(255),
                route_code VARCHAR(50) DEFAULT 'R-01',
                execution_status VARCHAR(20) DEFAULT 'PENDING'
            );
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS whatsapp_logs (
                id SERIAL PRIMARY KEY,
                created_at VARCHAR(10) NOT NULL,
                sender_name VARCHAR(150) NOT NULL,
                is_external_call BOOLEAN DEFAULT FALSE,
                message_body TEXT NOT NULL
            );
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS ai_agents (
                id SERIAL PRIMARY KEY,
                name VARCHAR(150) NOT NULL,
                role_type VARCHAR(100) NOT NULL,
                system_prompt TEXT NOT NULL,
                trigger_schedule VARCHAR(100) DEFAULT 'DAILY_MORNING',
                test_phone VARCHAR(30) DEFAULT '',
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)
        conn.commit()
    except Exception as e:
        logger.error(f"Error creating tables: {e}")
        conn.rollback()
    finally:
        conn.close()

    # ترقية الجداول القديمة وإسقاط قيود NOT NULL المسببة للخطأ
    run_isolated_ddl("ALTER TABLE sales_executives ADD COLUMN IF NOT EXISTS preferred_language VARCHAR(10) DEFAULT 'AR';")
    run_isolated_ddl("ALTER TABLE sales_executives ADD COLUMN IF NOT EXISTS has_target BOOLEAN DEFAULT FALSE;")
    run_isolated_ddl("ALTER TABLE sales_executives ADD COLUMN IF NOT EXISTS monthly_target NUMERIC(12, 2) DEFAULT 0.00;")
    run_isolated_ddl("ALTER TABLE sales_executives ADD COLUMN IF NOT EXISTS achieved_sales NUMERIC(12, 2) DEFAULT 0.00;")
    run_isolated_ddl("ALTER TABLE sales_executives ADD COLUMN IF NOT EXISTS total_expenses NUMERIC(12, 2) DEFAULT 0.00;")
    
    # حل قيد NOT NULL في جدول العينات والتقويم
    run_isolated_ddl("ALTER TABLE sample_deliveries ALTER COLUMN customer_id DROP NOT NULL;")
    run_isolated_ddl("ALTER TABLE sample_deliveries ALTER COLUMN rep_id DROP NOT NULL;")
    run_isolated_ddl("ALTER TABLE sample_deliveries ADD COLUMN IF NOT EXISTS customer_name VARCHAR(200) DEFAULT '';")
    run_isolated_ddl("ALTER TABLE sample_deliveries ADD COLUMN IF NOT EXISTS rep_name VARCHAR(150) DEFAULT '';")
    run_isolated_ddl("ALTER TABLE sample_deliveries ADD COLUMN IF NOT EXISTS product_name VARCHAR(200) DEFAULT '';")
    
    run_isolated_ddl("ALTER TABLE calendar_events ALTER COLUMN customer_id DROP NOT NULL;")
    run_isolated_ddl("ALTER TABLE calendar_events ALTER COLUMN rep_id DROP NOT NULL;")
    run_isolated_ddl("ALTER TABLE calendar_events ADD COLUMN IF NOT EXISTS customer_name VARCHAR(200) DEFAULT '';")
    run_isolated_ddl("ALTER TABLE calendar_events ADD COLUMN IF NOT EXISTS rep_name VARCHAR(150) DEFAULT '';")
    run_isolated_ddl("ALTER TABLE calendar_events ADD COLUMN IF NOT EXISTS task_type VARCHAR(150) DEFAULT '';")

def start_whatsapp_service():
    global whatsapp_process
    if os.path.exists("whatsapp_service.js"):
        try:
            whatsapp_process = subprocess.Popen(
                ["node", "whatsapp_service.js"],
                stdout=sys.stdout,
                stderr=sys.stderr
            )
        except Exception as e:
            logger.error(f"Error launching whatsapp_service.js: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_database()
    start_whatsapp_service()
    yield
    global whatsapp_process
    if whatsapp_process:
        whatsapp_process.terminate()

app = FastAPI(title="FDC Sales CRM", version="10.5.0", lifespan=lifespan)

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
    raise HTTPException(status_code=404, detail="Logo file not found on disk")

class Verify2FAPayload(BaseModel):
    code: str

@app.get("/api/auth/2fa/status")
def get_2fa_status():
    conn = get_db_connection()
    if not conn:
        return {"is_enabled": False}
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT is_2fa_enabled FROM system_auth WHERE username = 'admin';")
            row = cur.fetchone()
            return {"is_enabled": bool(row["is_2fa_enabled"]) if row else False}
    finally:
        conn.close()

@app.get("/api/auth/2fa/qr")
def get_2fa_qr():
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not reachable")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT totp_secret FROM system_auth WHERE username = 'admin';")
            row = cur.fetchone()
            secret = row["totp_secret"] if row else pyotp.random_base32()
            if not row:
                cur.execute("INSERT INTO system_auth (username, totp_secret, is_2fa_enabled) VALUES ('admin', %s, FALSE);", (secret,))
                conn.commit()

        totp_uri = pyotp.totp.TOTP(secret).provisioning_uri(name="admin@fdc.om", issuer_name="Food Development Co - CRM")
        qr = qrcode.QRCode(box_size=6, border=2)
        qr.add_data(totp_uri)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#3A056A", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return StreamingResponse(buf, media_type="image/png")
    finally:
        conn.close()

@app.post("/api/auth/2fa/verify")
def verify_2fa(payload: Verify2FAPayload):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not reachable")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT totp_secret FROM system_auth WHERE username = 'admin';")
            row = cur.fetchone()
            secret = row["totp_secret"]

        totp = pyotp.TOTP(secret)
        if totp.verify(payload.code, valid_window=1):
            with conn.cursor() as cur:
                cur.execute("UPDATE system_auth SET is_2fa_enabled = TRUE WHERE username = 'admin';")
            conn.commit()
            return {"status": "SUCCESS", "message": "تم التحقق بنجاح"}
        else:
            raise HTTPException(status_code=401, detail="الرمز غير صحيح أو انتهت صلاحيته")
    finally:
        conn.close()

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

class NewSamplePayload(BaseModel):
    customer_name: str
    rep_name: str
    product_name: str
    qty_free: int
    delivery_date: str

class NewCalendarEventPayload(BaseModel):
    customer_name: str
    rep_name: str
    task_type: str
    scheduled_at: str
    location: str
    route_code: Optional[str] = "R-01"

class NewTargetPayload(BaseModel):
    title: str
    customer_id: int
    rep_id: int
    target_value: Optional[float] = 0.0
    initial_note: Optional[str] = ""

class UpdateTargetNotePayload(BaseModel):
    note: str

class CloseTargetPayload(BaseModel):
    po_number: str
    po_value: float
    po_attachment_url: Optional[str] = ""

class NewExpenseCategoryPayload(BaseModel):
    category_name: str

class NewExpensePayload(BaseModel):
    rep_id: int
    expense_type: str
    amount: float
    notes: Optional[str] = ""

class NewAgentPayload(BaseModel):
    name: str
    role_type: str
    system_prompt: str
    trigger_schedule: Optional[str] = "DAILY_MORNING"
    test_phone: Optional[str] = ""

class ToggleAgentPayload(BaseModel):
    is_active: bool

class IncomingWhatsAppMessage(BaseModel):
    chat_id: str
    sender_phone: str
    sender_name: str
    message_text: str

class ReportPreviewPayload(BaseModel):
    report_recipient: Optional[str] = "سعادة رئيس مجلس الإدارة / المدير العام"
    recommendation: Optional[str] = ""

@app.post("/api/reports/preview", response_class=HTMLResponse)
def preview_sales_report(payload: ReportPreviewPayload):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not reachable")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM sales_executives ORDER BY id ASC;")
            reps = cur.fetchall()

        total_sales = sum(float(r.get("achieved_sales") or 0) for r in reps)
        total_exp = sum(float(r.get("total_expenses") or 0) for r in reps)
        cost_ratio = round((total_exp / total_sales * 100), 1) if total_sales > 0 else 0.0

        reps_perf = []
        for r in reps:
            target = float(r.get("monthly_target") or 0)
            sales = float(r.get("achieved_sales") or 0)
            rate = (sales / target * 100) if (r.get("has_target") and target > 0) else 0.0
            reps_perf.append({
                "name": r["name"],
                "region": r["region"],
                "monthly_target": target,
                "achieved_sales": sales,
                "achievement_rate": rate,
                "total_expenses": float(r.get("total_expenses") or 0),
                "efficiency": "عالي الكفاءة" if rate >= 100 else ("مقبول" if rate >= 70 else "يحتاج متابعة")
            })

        context = {
            "report_recipient": payload.report_recipient,
            "total_revenue": total_sales,
            "cost_to_sales_ratio": cost_ratio,
            "reps_performance": reps_perf,
            "strategic_summary": payload.recommendation or "أظهر الفريق التزاماً متميزاً في تغطية المسارات وزيادة معدل تحويل العينات لأوامر شراء.",
            "generated_at": datetime.now().strftime("%Y-%m-%d"),
            "logo": "/logo.png?v=2"
        }
        return render_report_html("02_executive_sales_report.html", context)
    finally:
        conn.close()

@app.get("/api/agents")
def get_ai_agents():
    conn = get_db_connection()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM ai_agents ORDER BY id ASC;")
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
            INSERT INTO ai_agents (name, role_type, system_prompt, trigger_schedule, test_phone, is_active)
            VALUES (%s, %s, %s, %s, %s, TRUE) RETURNING id;
            """, (payload.name.strip(), payload.role_type, payload.system_prompt.strip(), payload.trigger_schedule, payload.test_phone.strip()))
            new_id = cur.fetchone()["id"]
            conn.commit()
            return {"status": "SUCCESS", "id": new_id}
    finally:
        conn.close()

@app.post("/api/agents/{agent_id}/update")
def update_ai_agent(agent_id: int, payload: dict):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not reachable")
    try:
        with conn.cursor() as cur:
            cur.execute("""
            UPDATE ai_agents 
            SET name = %s, system_prompt = %s, trigger_schedule = %s 
            WHERE id = %s;
            """, (payload.get("name"), payload.get("system_prompt"), payload.get("trigger_schedule"), agent_id))
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

    if not test_target:
        raise HTTPException(status_code=400, detail="يرجى إدخال رقم هاتف الاختبار الموحد")

    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="قاعدة البيانات غير متصلة")

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM ai_agents WHERE id = %s;", (agent_id,))
            agent = cur.fetchone()
            if not agent:
                raise HTTPException(status_code=404, detail="الوكيل غير موجود")

            # التحقق من اللغة المفضلة للمندوب صاحب الرقم إن وجد
            clean_search_phone = test_target.replace("+", "").strip()
            cur.execute("SELECT preferred_language FROM sales_executives WHERE REPLACE(phone_number, '+', '') = %s;", (clean_search_phone,))
            rep_lang_row = cur.fetchone()
            pref_lang = rep_lang_row["preferred_language"] if rep_lang_row else "AR"

            cur.execute("SELECT * FROM sample_deliveries ORDER BY id DESC LIMIT 1;")
            sample = cur.fetchone()

            clean_title = agent["name"].replace("وكيل ", "").replace("وكيل", "").strip()

            if pref_lang == "EN":
                # الصياغة باللغة الإنجليزية بدون خطوط
                sample_info_en = f"Client: {sample['customer_name']} | Product: {sample['product_name']}" if (sample and sample.get('customer_name')) else "Client: Al Reef Restaurants | Product: Chicken Breast 4B"
                if agent["role_type"] == "SAMPLES_CONVERSION":
                    message_text = (
                        f"*Sample Follow-up & PO Request*\n\n"
                        f"Hello, please update us on the sample trial result with:\n"
                        f"{sample_info_en}\n\n"
                        f"Kindly register the Purchase Order (PO) number once approved."
                    )
                elif agent["role_type"] == "CALENDAR_DISPATCH":
                    message_text = (
                        f"*Daily Schedule Reminder*\n\n"
                        f"Hello, reminder of your scheduled field visits for today.\n\n"
                        f"Food Development Company | FDC Sales CRM"
                    )
                elif agent["role_type"] == "STAGNANT_TARGETS":
                    message_text = (
                        f"*Pending Sales Pipeline Follow-up*\n\n"
                        f"Hello, please follow up on pending deals to close POs.\n\n"
                        f"Food Development Company | FDC Sales CRM"
                    )
                else:
                    message_text = (
                        f"*{clean_title}*\n\n"
                        f"Hello,\n«{agent['system_prompt']}»\n\n"
                        f"Food Development Company | FDC Sales CRM"
                    )
            else:
                # الصياغة باللغة العربية الرسمية بدون خطوط
                sample_info_ar = f"العميل: {sample['customer_name']} | المنتج: {sample['product_name']}" if (sample and sample.get('customer_name')) else "العميل: مطاعم الريف | المنتج: صدور دجاج 4B"
                if agent["role_type"] == "SAMPLES_CONVERSION":
                    message_text = (
                        f"*{clean_title}*\n\n"
                        f"مرحبا، يرجى موافاتنا بنتيجة تجربة العينات الميدانية لدى:\n"
                        f"{sample_info_ar}\n\n"
                        f"عند الاعتماد نرجو تسجيل رقم أمر الشراء (PO) في النظام."
                    )
                elif agent["role_type"] == "CALENDAR_DISPATCH":
                    message_text = (
                        f"*{clean_title}*\n\n"
                        f"مرحبا، تذكير بالزيارات الميدانية المجدولة في جدول العمليات اليوم.\n\n"
                        f"شركة تنمية الغذاء | FDC Sales CRM"
                    )
                elif agent["role_type"] == "STAGNANT_TARGETS":
                    message_text = (
                        f"*{clean_title}*\n\n"
                        f"مرحبا، يرجى متابعة الفرص البيعية غير المحدثة لإغلاق الصفقات وإصدار أوامر الشراء.\n\n"
                        f"شركة تنمية الغذاء | FDC Sales CRM"
                    )
                else:
                    message_text = (
                        f"*{clean_title}*\n\n"
                        f"مرحبا،\n«{agent['system_prompt']}»\n\n"
                        f"شركة تنمية الغذاء | FDC Sales CRM"
                    )

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "http://127.0.0.1:3001/send-message",
                json={"phone_or_group": test_target, "message": message_text},
                timeout=6.0
            )
            if resp.status_code == 200:
                return {"status": "SUCCESS", "to": test_target, "message_preview": message_text}
            else:
                err = resp.json()
                raise HTTPException(status_code=400, detail=err.get("error", "فشل الإرسال عبر الواتساب"))
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

@app.post("/api/expense-categories")
def add_expense_category(payload: NewExpenseCategoryPayload):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not reachable")
    try:
        with conn.cursor() as cur:
            name = payload.category_name.strip()
            if not name:
                raise HTTPException(status_code=400, detail="يرجى كتابة اسم البند")
            cur.execute("INSERT INTO expense_categories (category_name) VALUES (%s) RETURNING id;", (name,))
            new_id = cur.fetchone()["id"]
            conn.commit()
            return {"status": "SUCCESS", "id": new_id, "category_name": name}
    except psycopg2.IntegrityError:
        conn.rollback()
        raise HTTPException(status_code=400, detail="هذا البند مضاف مسبقاً")
    finally:
        conn.close()

@app.delete("/api/expense-categories/{cat_id}")
def delete_expense_category(cat_id: int):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not reachable")
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM expense_categories WHERE id = %s;", (cat_id,))
            conn.commit()
            return {"status": "SUCCESS"}
    finally:
        conn.close()

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
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=f"فشل الحفظ: {str(e)}")
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
                payload.name.strip(),
                payload.employee_code.strip(),
                payload.phone_number.strip(),
                payload.region.strip(),
                payload.has_target,
                target,
                payload.preferred_language or "AR",
                rep_id
            ))
            cur.execute("UPDATE customer_accounts SET assigned_rep_name = %s WHERE assigned_rep_id = %s;", (payload.name.strip(), rep_id))
            conn.commit()
            return {"status": "SUCCESS"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=f"فشل تحديث بيانات المندوب: {str(e)}")
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
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=f"تعذر حذف المندوب: {str(e)}")
    finally:
        conn.close()

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
                if r:
                    rep_name = r["name"]
                else:
                    rep_id = None

            cur.execute("""
            INSERT INTO customer_accounts (company_name, brand_name, sector, region, contact_person, phone, assigned_rep_id, assigned_rep_name, notes, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'نشط') RETURNING id;
            """, (
                payload.company_name.strip(),
                (payload.brand_name or "").strip(),
                (payload.sector or "عام").strip(),
                (payload.region or "مسقط").strip(),
                payload.contact_person.strip(),
                payload.phone.strip(),
                rep_id,
                rep_name,
                (payload.notes or "").strip()
            ))
            new_id = cur.fetchone()["id"]
            conn.commit()
            return {"status": "SUCCESS", "id": new_id}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=f"فشل حفظ العميل: {str(e)}")
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
                if r:
                    rep_name = r["name"]
                else:
                    rep_id = None

            cur.execute("""
            UPDATE customer_accounts 
            SET company_name = %s, brand_name = %s, sector = %s, region = %s, 
                contact_person = %s, phone = %s, assigned_rep_id = %s, 
                assigned_rep_name = %s, notes = %s 
            WHERE id = %s;
            """, (
                payload.company_name.strip(),
                (payload.brand_name or "").strip(),
                (payload.sector or "عام").strip(),
                (payload.region or "مسقط").strip(),
                payload.contact_person.strip(),
                payload.phone.strip(),
                rep_id,
                rep_name,
                (payload.notes or "").strip(),
                customer_id
            ))
            conn.commit()
            return {"status": "SUCCESS"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=f"فشل تحديث العميل: {str(e)}")
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
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=f"فشل حذف العميل: {str(e)}")
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
            return rows
    finally:
        conn.close()

@app.post("/api/samples")
def add_sample(payload: NewSamplePayload):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not reachable")
    try:
        with conn.cursor() as cur:
            # إيجاد customer_id و rep_id إن تواجدا
            cur.execute("SELECT id FROM customer_accounts WHERE company_name = %s LIMIT 1;", (payload.customer_name,))
            c_row = cur.fetchone()
            c_id = c_row["id"] if c_row else None

            cur.execute("SELECT id FROM sales_executives WHERE name = %s LIMIT 1;", (payload.rep_name,))
            r_row = cur.fetchone()
            r_id = r_row["id"] if r_row else None

            cur.execute("""
            INSERT INTO sample_deliveries (customer_id, rep_id, customer_name, rep_name, product_name, qty_free, delivery_date, status, po_value, source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'PENDING', 0.0, 'يدوي') RETURNING id;
            """, (c_id, r_id, payload.customer_name, payload.rep_name, payload.product_name, payload.qty_free, payload.delivery_date))
            new_id = cur.fetchone()["id"]
            conn.commit()
            return {"status": "SUCCESS", "id": new_id}
    except Exception as e:
        conn.rollback()
        logger.error(f"Error saving sample: {e}")
        raise HTTPException(status_code=400, detail=f"فشل حفظ تسليم العينة: {str(e)}")
    finally:
        conn.close()

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
def add_calendar_event(payload: NewCalendarEventPayload):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not reachable")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM customer_accounts WHERE company_name = %s LIMIT 1;", (payload.customer_name,))
            c_row = cur.fetchone()
            c_id = c_row["id"] if c_row else None

            cur.execute("SELECT id FROM sales_executives WHERE name = %s LIMIT 1;", (payload.rep_name,))
            r_row = cur.fetchone()
            r_id = r_row["id"] if r_row else None

            cur.execute("""
            INSERT INTO calendar_events (customer_id, rep_id, customer_name, rep_name, task_type, scheduled_at, location, route_code, execution_status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'PENDING') RETURNING id;
            """, (c_id, r_id, payload.customer_name, payload.rep_name, payload.task_type, payload.scheduled_at, payload.location, payload.route_code or "R-01"))
            new_id = cur.fetchone()["id"]
            conn.commit()
            return {"status": "SUCCESS", "id": new_id}
    except Exception as e:
        conn.rollback()
        logger.error(f"Error saving calendar event: {e}")
        raise HTTPException(status_code=400, detail=f"فشل جدولة المهمة: {str(e)}")
    finally:
        conn.close()

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
def add_target(payload: NewTargetPayload):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not reachable")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT company_name FROM customer_accounts WHERE id = %s;", (payload.customer_id,))
            c = cur.fetchone()
            cur.execute("SELECT name FROM sales_executives WHERE id = %s;", (payload.rep_id,))
            r = cur.fetchone()
            if not c or not r:
                raise HTTPException(status_code=404, detail="العميل أو المندوب غير موجود")

            cur.execute("""
            INSERT INTO sales_targets (title, customer_id, customer_name, rep_id, rep_name, target_value, last_note, last_note_at, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), 'IN_PROGRESS') RETURNING id;
            """, (payload.title, payload.customer_id, c["company_name"], payload.rep_id, r["name"], payload.target_value, payload.initial_note or ""))
            new_id = cur.fetchone()["id"]
            conn.commit()
            return {"status": "SUCCESS", "id": new_id}
    finally:
        conn.close()

@app.post("/api/targets/{target_id}/note")
def update_target_note(target_id: int, payload: UpdateTargetNotePayload):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not reachable")
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE sales_targets SET last_note = %s, last_note_at = NOW() WHERE id = %s;", (payload.note, target_id))
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
            SET status = 'CLOSED', closed_at = NOW(), po_number = %s, po_value = %s, po_attachment_url = %s 
            WHERE id = %s;
            """, (payload.po_number, payload.po_value, payload.po_attachment_url or "", target_id))

            cur.execute("UPDATE sales_executives SET achieved_sales = achieved_sales + %s WHERE id = %s;", (payload.po_value, tgt["rep_id"]))
            conn.commit()
            return {"status": "SUCCESS"}
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
            resp = await client.get("http://127.0.0.1:3001/qr-status", timeout=2.5)
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
                        headers={
                            "Cache-Control": "no-cache, no-store, must-revalidate, max-age=0",
                            "Pragma": "no-cache",
                            "Expires": "0"
                        }
                    )
    except Exception as e:
        logger.warning(f"Waiting for Baileys: {e}")
    raise HTTPException(status_code=503, detail="جاري إقلاع محرك الواتساب...")

@app.post("/api/whatsapp/disconnect")
async def disconnect_whatsapp():
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post("http://127.0.0.1:3001/disconnect", timeout=8.0)
            if resp.status_code == 200:
                return resp.json()
    except Exception as e:
        logger.error(f"Error disconnecting whatsapp: {e}")
    raise HTTPException(status_code=500, detail="تعذر إنهاء جلسة الواتساب حالياً")

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
        raise HTTPException(status_code=500, detail="Database not reachable")
    try:
        clean_phone = msg.sender_phone.replace("+", "").strip()
        with conn.cursor() as cur:
            cur.execute("SELECT id, company_name FROM customer_accounts WHERE whatsapp_group_id = %s;", (msg.chat_id,))
            customer = cur.fetchone()

            cur.execute(
                "SELECT id, name FROM sales_executives WHERE REPLACE(phone_number, '+', '') = %s;", 
                (clean_phone,)
            )
            rep = cur.fetchone()

            if not customer and not rep:
                return {"status": "IGNORED", "reason": "خارج نطاق المجموعات أو الأرقام المعتمدة"}

            cur.execute("""
            INSERT INTO whatsapp_logs (created_at, sender_name, is_external_call, message_body)
            VALUES (%s, %s, FALSE, %s);
            """, (datetime.now().strftime("%H:%M"), msg.sender_name, msg.message_text))
            conn.commit()
            return {"status": "PROCESSED", "target": customer["company_name"] if customer else rep["name"]}
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
    except (ValueError, TypeError):
        clean_port = 8000
    uvicorn.run("main:app", host="0.0.0.0", port=clean_port)
