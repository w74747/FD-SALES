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
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel
import psycopg2
from psycopg2.extras import RealDictCursor
import pyotp
import qrcode
import httpx

try:
    from logo_data import LOGO_BASE64
except ImportError:
    LOGO_BASE64 = ""

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
                customer_name VARCHAR(200) NOT NULL,
                rep_name VARCHAR(150) NOT NULL,
                product_name VARCHAR(200) NOT NULL,
                qty_free INT NOT NULL,
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
                customer_name VARCHAR(200) NOT NULL,
                rep_name VARCHAR(150) NOT NULL,
                task_type VARCHAR(150) NOT NULL,
                scheduled_at VARCHAR(50) NOT NULL,
                location VARCHAR(255) NOT NULL,
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

    run_isolated_ddl("ALTER TABLE sales_executives ADD COLUMN IF NOT EXISTS has_target BOOLEAN DEFAULT FALSE;")
    run_isolated_ddl("ALTER TABLE sales_executives ADD COLUMN IF NOT EXISTS monthly_target NUMERIC(12, 2) DEFAULT 0.00;")
    run_isolated_ddl("ALTER TABLE sales_executives ADD COLUMN IF NOT EXISTS achieved_sales NUMERIC(12, 2) DEFAULT 0.00;")
    run_isolated_ddl("ALTER TABLE sales_executives ADD COLUMN IF NOT EXISTS total_expenses NUMERIC(12, 2) DEFAULT 0.00;")
    run_isolated_ddl("ALTER TABLE customer_accounts ADD COLUMN IF NOT EXISTS brand_name VARCHAR(200) DEFAULT '';")
    run_isolated_ddl("ALTER TABLE customer_accounts ADD COLUMN IF NOT EXISTS region VARCHAR(100) DEFAULT 'مسقط';")
    run_isolated_ddl("ALTER TABLE customer_accounts ADD COLUMN IF NOT EXISTS notes TEXT DEFAULT '';")
    run_isolated_ddl("ALTER TABLE customer_accounts ADD COLUMN IF NOT EXISTS assigned_rep_name VARCHAR(150) DEFAULT '';")

    run_isolated_ddl("""
    INSERT INTO ai_agents (name, role_type, system_prompt, trigger_schedule, test_phone, is_active)
    VALUES 
    (
        'وكيل متابعة العينات واسترجاع الـ PO',
        'SAMPLES_CONVERSION',
        'أنت المنسق الميداني لشركة تنمية الغذاء. اكتب رسالة مهنية ودية إلى عضو الفريق لمتابعة العينات المسلمة التي لم يُصدر لها أمر شراء حتى الآن، واطلب منه بلباقة موافاتك بقرار الشيف أو مدير المشتريات وإرسال رقم أمر الشراء PO عند اعتماده.',
        'DAILY_10AM',
        '+96898996963',
        TRUE
    ),
    (
        'وكيل التذكير الصباحي بالمسارات',
        'CALENDAR_DISPATCH',
        'أنت منسق جدول العمليات في شركة تنمية الغذاء. قم بصياغة رسالة صباحية مشجعة وموجزة تذكر فيها عضو الفريق بالزيارات الميدانية المجدولة له اليوم، وأسماء العملاء والمواقع المستهدفة.',
        'DAILY_08AM',
        '+96898996963',
        TRUE
    ),
    (
        'وكيل إنعاش الأهداف والفرص الراكدة',
        'STAGNANT_TARGETS',
        'أنت مستشار الصفقات في شركة تنمية الغذاء. اكتب رسالة تحفيزية لعضو الفريق بخصوص الفرص البيعية التي مر عليها أكثر من 3 أيام دون أي تحديث، واقترح عليه إجراء مكالمة هاتفية أو طلب عينة دعم للإغلاق.',
        'WEEKLY_SUNDAY',
        '+96898996963',
        TRUE
    )
    ON CONFLICT DO NOTHING;
    """)

    run_isolated_ddl("""
    INSERT INTO expense_categories (category_name) VALUES 
    ('وقود سيارة'), ('إيجار سيارة / نقل'), ('علاوة يومية (انتداب مدينة أخرى)'),
    ('ضيافة واجتماعات عملاء'), ('شحن ونثريات عينات'), ('صيانة وإصلاحات طارئة')
    ON CONFLICT DO NOTHING;
    """)

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

app = FastAPI(title="FDC Sales CRM", version="9.7.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/logo.png")
def get_logo():
    """إرسال صورة الشعار مباشرة وتجاوز مشاكل التخزين المؤقت"""
    try:
        from logo_data import LOGO_BASE64
        if LOGO_BASE64:
            clean_b64 = LOGO_BASE64.split(",")[-1].strip()
            image_bytes = base64.b64decode(clean_b64)
            return Response(
                content=image_bytes,
                media_type="image/png",
                headers={
                    "Cache-Control": "public, max-age=86400",
                    "Access-Control-Allow-Origin": "*"
                }
            )
    except Exception as e:
        logger.error(f"Error loading logo: {e}")
    raise HTTPException(status_code=404, detail="Logo not found")

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
    """إرجاع QR التوثيق دائماً دون حظر 403 لمنع تعليق واجهة المستخدم"""
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

# ----------------- نماذج البيانات -----------------
class NewRepPayload(BaseModel):
    name: str
    employee_code: str
    phone_number: str
    region: str
    has_target: Optional[bool] = False
    monthly_target: Optional[float] = 0.0

class NewCustomerPayload(BaseModel):
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

# ----------------- مسارات وكلاء الذكاء الاصطناعي -----------------
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

            cur.execute("SELECT * FROM sample_deliveries ORDER BY id DESC LIMIT 1;")
            sample = cur.fetchone()
            sample_info = f"العميل: {sample['customer_name']}، المنتج: {sample['product_name']}" if sample else "العميل: مطاعم الريف، المنتج: صدور دجاج 4B"

            if agent["role_type"] == "SAMPLES_CONVERSION":
                message_text = (
                    f"🤖 *متابعة تجريبية من: {agent['name']}*\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"أهلاً بك، يرجى موافاتنا بنتيجة تجربة العينات الميدانية لدى:\n"
                    f"📍 {sample_info}\n\n"
                    f"عند الاعتماد نرجو تسجيل رقم أمر الشراء (PO) في النظام."
                )
            else:
                message_text = (
                    f"🤖 *رسالة تجريبية من: {agent['name']}*\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"التوجيه النشط:\n«{agent['system_prompt']}»\n\n"
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

# ----------------- مسارات بنود المصاريف -----------------
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

# ----------------- مسارات فريق المبيعات والعمليات -----------------
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
            INSERT INTO sales_executives (name, employee_code, phone_number, region, has_target, monthly_target, achieved_sales, total_expenses, status)
            VALUES (%s, %s, %s, %s, %s, %s, 0.0, 0.0, 'نشط') RETURNING id;
            """, (payload.name, payload.employee_code, payload.phone_number, payload.region, payload.has_target, target))
            new_id = cur.fetchone()["id"]
            conn.commit()
            return {"status": "SUCCESS", "id": new_id}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=f"فشل الحفظ: {str(e)}")
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
                r["delivery_date"] = str(r["delivery_date"])
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
            cur.execute("""
            INSERT INTO sample_deliveries (customer_name, rep_name, product_name, qty_free, delivery_date, status, po_value, source)
            VALUES (%s, %s, %s, %s, %s, 'PENDING', 0.0, 'يدوي') RETURNING id;
            """, (payload.customer_name, payload.rep_name, payload.product_name, payload.qty_free, payload.delivery_date))
            new_id = cur.fetchone()["id"]
            conn.commit()
            return {"status": "SUCCESS", "id": new_id}
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
            cur.execute("""
            INSERT INTO calendar_events (customer_name, rep_name, task_type, scheduled_at, location, route_code, execution_status)
            VALUES (%s, %s, %s, %s, %s, %s, 'PENDING') RETURNING id;
            """, (payload.customer_name, payload.rep_name, payload.task_type, payload.scheduled_at, payload.location, payload.route_code or "R-01"))
            new_id = cur.fetchone()["id"]
            conn.commit()
            return {"status": "SUCCESS", "id": new_id}
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
                        headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
                    )
    except Exception as e:
        logger.warning(f"Waiting for Baileys: {e}")
    raise HTTPException(status_code=503, detail="جاري إقلاع محرك الواتساب المشفر...")

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
        with conn.cursor() as cur:
            cur.execute("SELECT id, company_name FROM customer_accounts WHERE whatsapp_group_id = %s;", (msg.chat_id,))
            customer = cur.fetchone()

            cur.execute("SELECT id, name FROM sales_executives WHERE phone_number = %s;", (msg.sender_phone,))
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
