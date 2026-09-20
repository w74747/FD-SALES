"""
main.py - Enterprise AI Sales CRM & Industrial Bakery Intelligence
Food Development Company (شركة تنمية الغذاء)
Inbound Auto-Welcome & Catalog Bot, Safe Anti-Ban Bulk Messaging, and Full CRUD Suite
"""

import os
import io
import sys
import json
import uuid
import base64
import logging
import subprocess
import csv
import re
import asyncio
import random
from datetime import datetime
from typing import Optional, List, Dict
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Response, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse
from pydantic import BaseModel
import psycopg2
from psycopg2.extras import RealDictCursor
import pyotp
import qrcode
import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("SalesCRM")

DATABASE_URL = os.getenv("DATABASE_URL") or os.getenv("DATABASE_PUBLIC_URL") or os.getenv("POSTGRES_URL") or ""
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY", "").strip()

CATALOG_FOLDER = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(CATALOG_FOLDER, exist_ok=True)
CATALOG_FILE_PATH = os.path.join(CATALOG_FOLDER, "fdc_catalog.pdf")

whatsapp_process = None

LOGO_SVG_RAW = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 420 90" width="420" height="90">
  <rect width="100%" height="100%" fill="transparent"/>
  <g transform="translate(10, 10)">
    <circle cx="35" cy="35" r="32" fill="#F5F0FC" stroke="#E4D9F5" stroke-width="2"/>
    <path d="M 35 15 C 23.95 15 15 23.95 15 35 C 15 46.05 23.95 55 35 55 C 43.5 55 50.8 49.7 53.6 42 L 44.5 42 C 42.4 46.3 38.9 48.5 35 48.5 C 27.5 48.5 21.5 42.5 21.5 35 C 21.5 27.5 27.5 21.5 35 21.5 C 40.2 21.5 44.6 25.2 46.5 29.5 L 54.2 29.5 C 51.5 20.9 44 15 35 15 Z" fill="#3A056A"/>
    <circle cx="35" cy="35" r="5.5" fill="#7E22CE"/>
    <path d="M 48 20 C 49 23 48.5 27 46 29 C 44 26 44.5 22 48 20 Z" fill="#C194FB"/>
    <text x="85" y="34" font-family="'Cairo', sans-serif" font-size="21" font-weight="900" fill="#3A056A">شركة تنمية الغذاء</text>
    <text x="86" y="54" font-family="'Cairo', sans-serif" font-size="11" font-weight="700" fill="#7E22CE" letter-spacing="1.5">FOOD DEVELOPMENT CO.</text>
  </g>
</svg>"""

def get_db_connection():
    if not DATABASE_URL:
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
        logger.warning(f"DDL notice ({sql_statement[:35]}...): {e}")
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

async def send_whatsapp_document(target_phone_or_group: str, caption: str, file_bytes: bytes, filename: str) -> bool:
    try:
        b64_data = base64.b64encode(file_bytes).decode("utf-8")
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "http://127.0.0.1:3001/send-document",
                json={
                    "phone_or_group": target_phone_or_group,
                    "caption": caption,
                    "file_base64": b64_data,
                    "file_name": filename
                },
                timeout=20.0
            )
            return resp.status_code == 200
    except Exception as e:
        logger.error(f"Error sending WhatsApp document: {e}")
        return False

def format_dispatch_order_en(text: str, customer: dict, sender_phone: str, sender_name: str, branches: list) -> str:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    loc_match = re.search(r'(https?://[^\s]+)', text)
    location_url = loc_match.group(1) if loc_match else ""

    contact_match = re.search(r'(?:contact|phone|tel|رقم)[:\s]*([0-9\+\s]{7,15})', text, re.IGNORECASE)
    branch_contact = contact_match.group(1).strip().replace(" ", "") if contact_match else ""

    delivery_date = "Next Scheduled Delivery"
    coming_match = re.search(r'(?:coming|delivery|توصيل|وصول)[:\s]*([0-9]{1,2}[\.\/\-][0-9]{1,2}[\.\/\-][0-9]{2,4})', text, re.IGNORECASE)
    if coming_match:
        delivery_date = coming_match.group(1).strip()

    date_match = re.search(r'(?:date|تاريخ)[:\s]*([0-9]{1,2}[\.\/\-][0-9]{1,2}[\.\/\-][0-9]{2,4})', text, re.IGNORECASE)
    order_date = date_match.group(1).strip() if date_match else datetime.now().strftime("%d/%m/%Y")

    brand_name = customer.get("brand_name") or ""
    branch_name = "Main Branch"
    for l in lines:
        if 'branch' in l.lower():
            m = re.search(r'([A-Za-z\u0600-\u06FF\s\-]+branch)', l, re.IGNORECASE)
            if m:
                clean_b = m.group(1).strip()
                if brand_name and brand_name.lower() in clean_b.lower():
                    clean_b = re.sub(brand_name, '', clean_b, flags=re.IGNORECASE).strip()
                branch_name = clean_b.title() if clean_b else "Main Branch"
                break

    for b in branches:
        b_name = b.get("branch_name", "")
        if b_name.lower() in text.lower() or b_name.lower() in branch_name.lower():
            branch_name = b_name
            if not location_url and b.get("location_url"):
                location_url = b["location_url"]
            if not branch_contact and b.get("branch_phone"):
                branch_contact = b["branch_phone"]
            break

    raw_items = []
    for l in lines:
        if re.search(r'^(date|coming|location|contact|tel|phone|odare|order)', l, re.IGNORECASE):
            continue
        if 'http' in l.lower() or 'branch' in l.lower() or (brand_name and l.lower() == brand_name.lower()):
            continue
        if re.search(r'(box|cartoon|carton|ctn|كرتون|حبة|pc|pcs|bag|كيس|bread|buns|bun|brioche|potato)', l, re.IGNORECASE):
            raw_items.append(l)

    cleaned_items = []
    i = 0
    while i < len(raw_items):
        item_text = raw_items[i]
        if i + 1 < len(raw_items) and re.search(r'^\d+\s*(box|cartoon|carton|ctn|كرتون)', raw_items[i+1], re.IGNORECASE):
            item_text = f"{raw_items[i]}: {raw_items[i+1]}"
            i += 1
        cleaned_items.append(item_text)
        i += 1

    if not cleaned_items:
        cleaned_items = ["Items specified in customer communication"]

    items_formatted = "\n".join([f"- {it}" for it in cleaned_items])

    return (
        f"*DISPATCH & DELIVERY ORDER*\n"
        f"----------------------------------------\n"
        f"*Company:* {customer.get('company_name', 'Customer')}\n"
        f"*Brand:* {brand_name if brand_name else customer.get('company_name', '')}\n"
        f"*Branch:* {branch_name}\n"
        f"*Order Received Date:* {order_date}\n"
        f"*Target Delivery Date:* {delivery_date}\n"
        f"----------------------------------------\n"
        f"*Ordered Items & Quantities:*\n"
        f"{items_formatted}\n"
        f"----------------------------------------\n"
        f"*Branch Contact:* {branch_contact if branch_contact else 'N/A'}\n"
        f"*Delivery Location:*\n"
        f"{location_url if location_url else 'Registered Branch Location'}\n"
        f"----------------------------------------\n"
        f"Food Development Co. | Logistics & Operations"
    )

def init_database():
    conn = get_db_connection()
    if not conn:
        return

    try:
        with conn.cursor() as cur:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS whatsapp_session_snapshots (
                session_name VARCHAR(50) PRIMARY KEY,
                snapshot_data JSONB NOT NULL,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
            """)

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
            CREATE TABLE IF NOT EXISTS sales_executives (
                id SERIAL PRIMARY KEY,
                name VARCHAR(150) NOT NULL,
                employee_code VARCHAR(50) UNIQUE NOT NULL,
                phone_number VARCHAR(30) UNIQUE NOT NULL,
                region VARCHAR(100) NOT NULL,
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
                whatsapp_group_id VARCHAR(100),
                tier VARCHAR(10) DEFAULT 'B',
                status VARCHAR(20) DEFAULT 'نشط'
            );
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS sales_targets (
                id SERIAL PRIMARY KEY,
                title VARCHAR(250) NOT NULL,
                customer_id INT,
                customer_name VARCHAR(200) NOT NULL,
                rep_id INT,
                rep_name VARCHAR(150) NOT NULL,
                target_value NUMERIC(12, 2) DEFAULT 0.00,
                pipeline_stage VARCHAR(50) DEFAULT 'LEAD_CONTACT',
                status VARCHAR(30) DEFAULT 'IN_PROGRESS'
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
                status VARCHAR(50) DEFAULT 'قيد التجربة',
                feedback_notes TEXT DEFAULT '',
                converted_po_id VARCHAR(100) DEFAULT '—',
                po_value NUMERIC(12, 2) DEFAULT 0.00
            );
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS calendar_events (
                id SERIAL PRIMARY KEY,
                customer_name VARCHAR(200),
                rep_name VARCHAR(255),
                task_type VARCHAR(150),
                scheduled_at VARCHAR(50),
                location VARCHAR(255),
                change_notes TEXT DEFAULT '',
                route_code VARCHAR(50) DEFAULT 'R-01',
                execution_status VARCHAR(50) DEFAULT 'PENDING'
            );
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS expenses_log (
                id SERIAL PRIMARY KEY,
                rep_id INT,
                rep_name VARCHAR(150) NOT NULL,
                expense_type VARCHAR(100) NOT NULL,
                amount NUMERIC(12, 2) NOT NULL,
                notes TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS whatsapp_logs (
                id SERIAL PRIMARY KEY,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                sender_name VARCHAR(150) NOT NULL,
                sender_phone VARCHAR(50) DEFAULT '',
                channel_name VARCHAR(150) DEFAULT 'محادثة مباشرة',
                is_external_call BOOLEAN DEFAULT FALSE,
                message_body TEXT NOT NULL
            );
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS ai_agents (
                id SERIAL PRIMARY KEY,
                name VARCHAR(150) NOT NULL,
                listen_scope VARCHAR(50) DEFAULT 'ALL_GROUPS',
                dispatch_channel VARCHAR(100) DEFAULT '',
                system_prompt TEXT NOT NULL,
                is_active BOOLEAN DEFAULT TRUE
            );
            """)
        conn.commit()
    except Exception as e:
        logger.error(f"Error initializing DB: {e}")
        conn.rollback()
    finally:
        conn.close()

    run_isolated_ddl("ALTER TABLE whatsapp_logs ADD COLUMN IF NOT EXISTS sender_phone VARCHAR(50) DEFAULT '';")

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

app = FastAPI(title="FDC Sales CRM", version="21.3.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/logo.png")
def get_logo():
    return Response(
        content=LOGO_SVG_RAW.strip().encode("utf-8"),
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400", "Access-Control-Allow-Origin": "*"}
    )

# ----------------- نماذج Pydantic -----------------
class Verify2FAPayload(BaseModel):
    code: str

class InboundWelcomeConfigPayload(BaseModel):
    is_enabled: bool
    greeting_text: str

class BulkCampaignItem(BaseModel):
    name: str
    phone: str

class BulkCampaignPayload(BaseModel):
    contacts: List[BulkCampaignItem]
    message_template: str
    include_catalog: bool = False

class CreateCustomerPayload(BaseModel):
    company_name: str
    brand_name: Optional[str] = ""
    sector: Optional[str] = "مطاعم"
    region: Optional[str] = "مسقط"
    phone: str
    assigned_rep_id: Optional[int] = None
    assigned_rep_name: Optional[str] = ""
    whatsapp_group_id: Optional[str] = ""

class UpdateCustomerPayload(BaseModel):
    company_name: str
    brand_name: Optional[str] = ""
    region: Optional[str] = "مسقط"
    phone: str
    assigned_rep_id: Optional[int] = None
    assigned_rep_name: Optional[str] = ""
    whatsapp_group_id: Optional[str] = ""

class CreateTargetPayload(BaseModel):
    title: str
    customer_id: Optional[int] = None
    customer_name: str
    rep_id: Optional[int] = None
    rep_name: str
    target_value: float
    pipeline_stage: Optional[str] = "LEAD_CONTACT"

class UpdateTargetPayload(BaseModel):
    title: str
    customer_name: str
    rep_name: str
    target_value: float
    pipeline_stage: str

class UpdateRepPayload(BaseModel):
    name: str
    region: str
    phone_number: str
    monthly_target: Optional[float] = 0.0
    status: Optional[str] = "نشط"

class UpdateAgentPayload(BaseModel):
    name: str
    listen_scope: Optional[str] = "ALL_GROUPS"
    dispatch_channel: Optional[str] = ""
    system_prompt: str

class CalendarEventPayload(BaseModel):
    customer_id: Optional[int] = None
    customer_name: str
    rep_id: Optional[int] = None
    rep_name: str
    task_type: str
    scheduled_at: str
    reminder_at: Optional[str] = ""
    location: Optional[str] = ""
    change_notes: Optional[str] = ""
    route_code: Optional[str] = "R-01"

class CalendarUpdatePayload(BaseModel):
    task_type: str
    route_code: Optional[str] = "R-01"
    scheduled_at: str
    location: Optional[str] = ""
    change_notes: Optional[str] = ""
    execution_status: Optional[str] = "PENDING"

class IncomingWhatsAppMessage(BaseModel):
    chat_id: str
    sender_phone: str
    sender_name: str
    message_text: str

class SessionSnapshotPayload(BaseModel):
    session_name: str
    snapshot: Dict[str, str]

# ----------------- مسار التحقق 2FA -----------------
@app.post("/api/auth/2fa/verify")
def verify_2fa(payload: Verify2FAPayload):
    conn = get_db_connection()
    if not conn:
        return Response(content=json.dumps({"detail": "قاعدة البيانات غير متاحة"}), status_code=500, media_type="application/json")
    try:
        clean_code = payload.code.strip()
        with conn.cursor() as cur:
            cur.execute("SELECT totp_secret FROM system_auth WHERE username = 'admin';")
            row = cur.fetchone()
            secret = row["totp_secret"] if row else None

        if not secret:
            return Response(content=json.dumps({"detail": "لم يتم العثور على مفتاح التوثيق السري"}), status_code=400, media_type="application/json")

        totp = pyotp.TOTP(secret)
        if totp.verify(clean_code, valid_window=1):
            with conn.cursor() as cur:
                cur.execute("UPDATE system_auth SET is_2fa_enabled = TRUE WHERE username = 'admin';")
            conn.commit()
            return {"status": "SUCCESS", "message": "تم التحقق بنجاح"}
        else:
            return Response(content=json.dumps({"detail": "رمز التحقق غير صحيح أو انتهت صلاحيته"}), status_code=401, media_type="application/json")
    finally:
        conn.close()

# ----------------- محرك الرد الترحيبي التلقائي بالكتالوج -----------------
@app.get("/api/exhibition/config")
def get_inbound_welcome_config():
    conn = get_db_connection()
    if not conn:
        return {"is_enabled": False, "greeting_text": "", "has_catalog": False}
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT key_name, key_value FROM system_config WHERE key_name IN ('exhibition_auto_reply_enabled', 'exhibition_greeting_text');")
            rows = {r["key_name"]: r["key_value"] for r in cur.fetchall()}
            return {
                "is_enabled": rows.get("exhibition_auto_reply_enabled") == "true",
                "greeting_text": rows.get("exhibition_greeting_text", "أهلاً وسهلاً بك في شركة تنمية الغذاء. يسعدنا تواصلك معنا ونرفق لك كتالوج وقائمة منتجات المخبوزات الصناعية المعتمدة لدينا. كيف يمكننا خدمتك اليوم؟"),
                "has_catalog": os.path.exists(CATALOG_FILE_PATH)
            }
    finally:
        conn.close()

@app.post("/api/exhibition/config")
def save_inbound_welcome_config(payload: InboundWelcomeConfigPayload):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database unreachable")
    try:
        with conn.cursor() as cur:
            cur.execute("""
            INSERT INTO system_config (key_name, key_value) VALUES 
            ('exhibition_auto_reply_enabled', %s),
            ('exhibition_greeting_text', %s)
            ON CONFLICT (key_name) DO UPDATE SET key_value = EXCLUDED.key_value;
            """, (str(payload.is_enabled).lower(), payload.greeting_text.strip()))
            conn.commit()
            return {"status": "SUCCESS"}
    finally:
        conn.close()

@app.post("/api/exhibition/upload-catalog")
async def upload_catalog_file(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="يرجى رفع ملف بصيغة PDF حصراً")
    content = await file.read()
    with open(CATALOG_FILE_PATH, "wb") as f:
        f.write(content)
    return {"status": "SUCCESS", "filename": file.filename, "size_kb": len(content) // 1024}

# ----------------- محرك الإرسال الجماعي الآمن ضد الحظر -----------------
@app.post("/api/campaigns/send-bulk")
async def send_bulk_campaign(payload: BulkCampaignPayload):
    if not payload.contacts:
        raise HTTPException(status_code=400, detail="قائمة الأرقام فارغة")

    async def run_safe_campaign():
        for contact in payload.contacts:
            name = contact.name.strip()
            phone = contact.phone.strip()
            personalized_text = payload.message_template.replace("{name}", name)

            if payload.include_catalog and os.path.exists(CATALOG_FILE_PATH):
                with open(CATALOG_FILE_PATH, "rb") as f:
                    pdf_bytes = f.read()
                await send_whatsapp_document(phone, personalized_text, pdf_bytes, "Food_Development_Catalog.pdf")
            else:
                await send_whatsapp_direct(phone, personalized_text)

            # تأخير عشوائي ذكي بين 18 إلى 32 ثانية لحماية الرقم من الحظر
            jitter_delay = random.uniform(18.0, 32.0)
            await asyncio.sleep(jitter_delay)

    asyncio.create_task(run_safe_campaign())
    return {
        "status": "QUEUED",
        "total_contacts": len(payload.contacts),
        "message": f"تمت جدولة إرسال {len(payload.contacts)} رسالة بتأخير أمني ذكي ضد الحظر."
    }

# ----------------- رادار الواتساب + التفاعل التلقائي -----------------
@app.post("/api/whatsapp/webhook")
def handle_whatsapp_webhook(msg: IncomingWhatsAppMessage):
    chat_id = msg.chat_id.strip()
    if "@newsletter" in chat_id or "status@broadcast" in chat_id:
        return {"status": "IGNORED_BROADCAST"}

    conn = get_db_connection()
    if not conn:
        return {"status": "ERROR"}

    try:
        text = msg.message_text.strip()
        channel_name = "محادثة مباشرة"
        reply_text = None
        forward_to_logistics = None
        logistics_text = None
        clean_phone = msg.sender_phone.replace("+", "").strip()
        send_catalog = False

        with conn.cursor() as cur:
            cur.execute("SELECT key_name, key_value FROM system_config;")
            conf = {r["key_name"]: r["key_value"] for r in cur.fetchall()}
            logistics_group = conf.get("logistics_group_id", "").strip()
            auto_welcome_enabled = conf.get("exhibition_auto_reply_enabled") == "true"
            welcome_greeting = conf.get("exhibition_greeting_text", "")

            cur.execute("SELECT id, company_name, brand_name FROM customer_accounts WHERE whatsapp_group_id = %s;", (chat_id,))
            customer = cur.fetchone()

            if not customer and "@g.us" in chat_id:
                clean_gid = chat_id.split("@")[0]
                cur.execute("SELECT id, company_name, brand_name FROM customer_accounts WHERE whatsapp_group_id LIKE %s;", (f"%{clean_gid}%",))
                customer = cur.fetchone()

            if customer:
                channel_name = f"مجموعة: {customer['company_name']} ({customer['brand_name'] or 'عام'})"
                trigger_keywords = ["box", "boxes", "cartoon", "carton", "ctn", "odare", "order", "potato", "buns", "bun", "bread", "brioche", "طلب", "طلبية", "كرتون", "حبة"]
                if any(k in text.lower() for k in trigger_keywords):
                    cur.execute("SELECT * FROM customer_branches WHERE customer_id = %s;", (customer["id"],))
                    branches = cur.fetchall()
                    logistics_msg = format_dispatch_order_en(text, customer, msg.sender_phone, msg.sender_name, branches)
                    if logistics_group:
                        forward_to_logistics = logistics_group
                        logistics_text = logistics_msg
            else:
                if auto_welcome_enabled and not chat_id.endswith("@g.us"):
                    reply_text = welcome_greeting
                    send_catalog = os.path.exists(CATALOG_FILE_PATH)

            try:
                cur.execute("""
                INSERT INTO whatsapp_logs (created_at, sender_name, sender_phone, channel_name, is_external_call, message_body)
                VALUES (NOW(), %s, %s, %s, FALSE, %s);
                """, (msg.sender_name, clean_phone, channel_name, text))
            except Exception:
                pass

            conn.commit()
            return {
                "status": "PROCESSED",
                "reply_text": reply_text,
                "send_catalog": send_catalog,
                "catalog_path": CATALOG_FILE_PATH if send_catalog else None,
                "forward_to_logistics": forward_to_logistics,
                "logistics_text": logistics_text
            }
    finally:
        conn.close()

# ----------------- مسارات العملاء -----------------
@app.get("/api/customers")
def get_customers():
    conn = get_db_connection()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM customer_accounts ORDER BY id ASC;")
            return cur.fetchall()
    finally:
        conn.close()

@app.post("/api/customers")
def create_customer(payload: CreateCustomerPayload):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database unreachable")
    try:
        with conn.cursor() as cur:
            cur.execute("""
            INSERT INTO customer_accounts (company_name, brand_name, sector, region, contact_person, phone, assigned_rep_id, assigned_rep_name, whatsapp_group_id, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'نشط') RETURNING id;
            """, (
                payload.company_name.strip(), payload.brand_name.strip() if payload.brand_name else "",
                payload.sector or "مطاعم", payload.region or "مسقط",
                payload.company_name.strip(), payload.phone.strip(),
                payload.assigned_rep_id, payload.assigned_rep_name or "",
                payload.whatsapp_group_id.strip() if payload.whatsapp_group_id else ""
            ))
            new_id = cur.fetchone()["id"]
            conn.commit()
            return {"status": "SUCCESS", "id": new_id}
    finally:
        conn.close()

@app.post("/api/customers/{cust_id}/update")
def update_customer(cust_id: int, payload: UpdateCustomerPayload):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database unreachable")
    try:
        with conn.cursor() as cur:
            cur.execute("""
            UPDATE customer_accounts
            SET company_name = %s, brand_name = %s, region = %s, phone = %s, 
                assigned_rep_id = %s, assigned_rep_name = %s, whatsapp_group_id = %s
            WHERE id = %s;
            """, (
                payload.company_name.strip(), payload.brand_name.strip() if payload.brand_name else "",
                payload.region or "مسقط", payload.phone.strip(),
                payload.assigned_rep_id, payload.assigned_rep_name or "",
                payload.whatsapp_group_id.strip() if payload.whatsapp_group_id else "",
                cust_id
            ))
            conn.commit()
            return {"status": "SUCCESS"}
    finally:
        conn.close()

@app.delete("/api/customers/{cust_id}")
def delete_customer(cust_id: int):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database error")
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM customer_accounts WHERE id = %s;", (cust_id,))
            conn.commit()
            return {"status": "SUCCESS"}
    finally:
        conn.close()

# ----------------- مسارات الأهداف والفرص -----------------
@app.get("/api/targets")
def get_targets():
    conn = get_db_connection()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM sales_targets ORDER BY status DESC, id DESC;")
            rows = cur.fetchall()
            for r in rows:
                r["target_value"] = float(r.get("target_value") or 0)
            return rows
    finally:
        conn.close()

@app.post("/api/targets")
def create_target(payload: CreateTargetPayload):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database error")
    try:
        with conn.cursor() as cur:
            cur.execute("""
            INSERT INTO sales_targets (title, customer_id, customer_name, rep_id, rep_name, target_value, pipeline_stage, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'IN_PROGRESS') RETURNING id;
            """, (
                payload.title.strip(), payload.customer_id, payload.customer_name,
                payload.rep_id, payload.rep_name, payload.target_value or 0.0,
                payload.pipeline_stage or "LEAD_CONTACT"
            ))
            new_id = cur.fetchone()["id"]
            conn.commit()
            return {"status": "SUCCESS", "id": new_id}
    finally:
        conn.close()

@app.post("/api/targets/{target_id}/update")
def update_sales_target(target_id: int, payload: UpdateTargetPayload):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database error")
    try:
        with conn.cursor() as cur:
            cur.execute("""
            UPDATE sales_targets
            SET title = %s, customer_name = %s, rep_name = %s, target_value = %s, pipeline_stage = %s
            WHERE id = %s;
            """, (
                payload.title.strip(), payload.customer_name, payload.rep_name,
                payload.target_value or 0.0, payload.pipeline_stage, target_id
            ))
            conn.commit()
            return {"status": "SUCCESS"}
    finally:
        conn.close()

@app.delete("/api/targets/{target_id}")
def delete_target(target_id: int):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database error")
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM sales_targets WHERE id = %s;", (target_id,))
            conn.commit()
            return {"status": "SUCCESS"}
    finally:
        conn.close()

# ----------------- مسارات الوكلاء الموحدين -----------------
@app.get("/api/agents")
def get_unified_agents():
    conn = get_db_connection()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM ai_agents ORDER BY id ASC;")
            return cur.fetchall()
    finally:
        conn.close()

@app.post("/api/agents/{agent_id}/update")
def update_agent(agent_id: int, payload: UpdateAgentPayload):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database error")
    try:
        with conn.cursor() as cur:
            cur.execute("""
            UPDATE ai_agents
            SET name = %s, listen_scope = %s, dispatch_channel = %s, system_prompt = %s
            WHERE id = %s;
            """, (
                payload.name.strip(), payload.listen_scope or "ALL_GROUPS",
                payload.dispatch_channel.strip() if payload.dispatch_channel else "",
                payload.system_prompt.strip(), agent_id
            ))
            conn.commit()
            return {"status": "SUCCESS"}
    finally:
        conn.close()

@app.delete("/api/agents/{agent_id}")
def delete_unified_agent(agent_id: int):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database error")
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM ai_agents WHERE id = %s;", (agent_id,))
            conn.commit()
            return {"status": "SUCCESS"}
    finally:
        conn.close()

# ----------------- مسارات فريق المبيعات -----------------
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
                enriched.append({
                    "id": r["id"], "name": r["name"], "employee_code": r["employee_code"],
                    "phone_number": r["phone_number"], "region": r["region"],
                    "monthly_target": float(r.get("monthly_target") or 0),
                    "achieved_sales": float(r.get("achieved_sales") or 0),
                    "total_expenses": float(r.get("total_expenses") or 0),
                    "status": r["status"] or "نشط"
                })
            return enriched
    finally:
        conn.close()

@app.post("/api/reps/{rep_id}/update")
def update_sales_rep(rep_id: int, payload: UpdateRepPayload):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database error")
    try:
        with conn.cursor() as cur:
            cur.execute("""
            UPDATE sales_executives 
            SET name = %s, region = %s, phone_number = %s, monthly_target = %s, status = %s
            WHERE id = %s;
            """, (
                payload.name.strip(), payload.region.strip(), 
                payload.phone_number.strip(), payload.monthly_target or 0.0, 
                payload.status or "نشط", rep_id
            ))
            conn.commit()
            return {"status": "SUCCESS"}
    finally:
        conn.close()

@app.delete("/api/reps/{rep_id}")
def delete_sales_rep(rep_id: int):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database error")
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM sales_executives WHERE id = %s;", (rep_id,))
            conn.commit()
            return {"status": "SUCCESS"}
    finally:
        conn.close()

# ----------------- مسارات التقويم والعينات والمصاريف -----------------
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
def create_calendar_event(payload: CalendarEventPayload):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database unreachable")
    try:
        with conn.cursor() as cur:
            cur.execute("""
            INSERT INTO calendar_events (customer_id, customer_name, rep_id, rep_name, task_type, scheduled_at, reminder_at, location, change_notes, route_code, execution_status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'PENDING') RETURNING id;
            """, (
                payload.customer_id, payload.customer_name, payload.rep_id, payload.rep_name,
                payload.task_type, payload.scheduled_at, payload.reminder_at or "",
                payload.location or "", payload.change_notes or "", payload.route_code or "R-01"
            ))
            new_id = cur.fetchone()["id"]
            conn.commit()
            return {"status": "SUCCESS", "id": new_id}
    finally:
        conn.close()

@app.post("/api/calendar/{cal_id}/update")
def update_calendar_event(cal_id: int, payload: CalendarUpdatePayload):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database unreachable")
    try:
        with conn.cursor() as cur:
            cur.execute("""
            UPDATE calendar_events
            SET task_type = %s, route_code = %s, scheduled_at = %s, 
                location = %s, change_notes = %s, execution_status = %s
            WHERE id = %s;
            """, (
                payload.task_type.strip(), payload.route_code or "R-01",
                payload.scheduled_at, payload.location or "",
                payload.change_notes or "", payload.execution_status or "PENDING",
                cal_id
            ))
            conn.commit()
            return {"status": "SUCCESS"}
    finally:
        conn.close()

@app.delete("/api/calendar/{cal_id}")
def delete_calendar_event(cal_id: int):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database unreachable")
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM calendar_events WHERE id = %s;", (cal_id,))
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
            return rows
    finally:
        conn.close()

@app.delete("/api/samples/{sample_id}")
def delete_sample(sample_id: int):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database unreachable")
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM sample_deliveries WHERE id = %s;", (sample_id,))
            conn.commit()
            return {"status": "SUCCESS"}
    finally:
        conn.close()

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

@app.get("/api/expenses")
def get_expenses():
    conn = get_db_connection()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM expenses_log ORDER BY id DESC;")
            rows = cur.fetchall()
            for r in rows:
                r["amount"] = float(r.get("amount") or 0)
                r["created_at_str"] = r["created_at"].strftime("%Y-%m-%d %H:%M") if r.get("created_at") and hasattr(r["created_at"], "strftime") else "—"
            return rows
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

@app.get("/api/whatsapp/logs")
def get_whatsapp_logs():
    conn = get_db_connection()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM whatsapp_logs ORDER BY id DESC LIMIT 50;")
            rows = cur.fetchall()
            for r in rows:
                if r.get("created_at") and hasattr(r["created_at"], "strftime"):
                    r["created_at"] = r["created_at"].strftime("%H:%M")
                r["sender_phone"] = r.get("sender_phone") or ""
            return rows
    finally:
        conn.close()

# ----------------- مسارات استرجاع وحفظ الجلسات -----------------
@app.get("/api/internal/session-snapshot/{session_name}")
def get_session_snapshot(session_name: str):
    conn = get_db_connection()
    if not conn:
        return Response(status_code=500)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT snapshot_data FROM whatsapp_session_snapshots WHERE session_name = %s;", (session_name,))
            row = cur.fetchone()
            if row:
                return {"snapshot": row["snapshot_data"]}
            return Response(status_code=404)
    finally:
        conn.close()

@app.post("/api/internal/session-snapshot")
def save_session_snapshot(payload: SessionSnapshotPayload):
    conn = get_db_connection()
    if not conn:
        return Response(status_code=500)
    try:
        with conn.cursor() as cur:
            cur.execute("""
            INSERT INTO whatsapp_session_snapshots (session_name, snapshot_data, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (session_name) DO UPDATE 
            SET snapshot_data = EXCLUDED.snapshot_data, updated_at = NOW();
            """, (payload.session_name, json.dumps(payload.snapshot)))
            conn.commit()
            return {"status": "SUCCESS"}
    finally:
        conn.close()

@app.delete("/api/internal/session-snapshot/{session_name}")
def delete_session_snapshot(session_name: str):
    conn = get_db_connection()
    if not conn:
        return Response(status_code=500)
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM whatsapp_session_snapshots WHERE session_name = %s;", (session_name,))
            conn.commit()
            return {"status": "CLEARED"}
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
