"""
main.py - Enterprise AI Sales CRM & Industrial Bakery Intelligence
Food Development Company (شركة تنمية الغذاء)
Unified Agent Model & Instant PostgreSQL Session Snapshot Engine
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

try:
    import openpyxl
except ImportError:
    openpyxl = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("SalesCRM")

DATABASE_URL = (
    os.getenv("DATABASE_URL") 
    or os.getenv("DATABASE_PUBLIC_URL") 
    or os.getenv("POSTGRES_URL") 
    or ""
)
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY", "").strip()
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

async def send_whatsapp_direct(target_phone_or_group: str, message: str, session_type: str = "operations") -> bool:
    if not target_phone_or_group:
        return False
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "http://127.0.0.1:3001/send-message",
                json={"phone_or_group": target_phone_or_group, "message": message, "session_type": session_type},
                timeout=5.0
            )
            return resp.status_code == 200
    except Exception:
        return False

async def query_perplexity_intelligence(system_prompt: str, search_query: str) -> str:
    if not PERPLEXITY_API_KEY:
        return "تنبيه: لم يتم العثور على PERPLEXITY_API_KEY في متغيرات البيئة بـ Railway."

    url = "https://api.perplexity.ai/chat/completions"
    headers = {
        "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
        "Content-Type": "application/json"
    }

    user_content = (
        f"المطلوب: إجراء بحث واستقصاء حي عبر الإنترنت ومصادر الأعمال والأخبار حول:\n"
        f"الموضوع / الشركات المستهدفة: {search_query}\n\n"
        f"قم بصياغة تقرير تنفيذي رسمي وموجز باللغة العربية يوضح:\n"
        f"1. أحدث الأخبار والتحركات خلال الأيام الأخيرة.\n"
        f"2. المنتجات الجديدة أو التغييرات التسعيرية وحملات الترويج المرصودة.\n"
        f"3. توصية استراتيجية واضحة لشركة تنمية الغذاء لاقتناص الفرصة التنافسية.\n"
        f"اجعل التقرير مهنياً تماماً وخالياً من أي رموز تعبيرية."
    )

    payload = {
        "model": "sonar",
        "messages": [
            {"role": "system", "content": system_prompt or "أنت مستشار استخبارات الأعمال وتطوير المبيعات لشركة تنمية الغذاء."},
            {"role": "user", "content": user_content}
        ],
        "temperature": 0.2
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, headers=headers, timeout=35.0)
            if resp.status_code == 200:
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            else:
                return f"تعذر استدعاء البحث الذكي (خطأ {resp.status_code}): {resp.text[:150]}"
    except Exception as e:
        return f"خطأ في الاتصال بمحرك Perplexity: {str(e)}"

def match_rep_by_region(conn, region_term: str):
    with conn.cursor() as cur:
        cur.execute("SELECT id, name, phone_number, region FROM sales_executives WHERE region ILIKE %s AND status = 'نشط' LIMIT 1;", (f"%{region_term}%",))
        rep = cur.fetchone()
        if not rep:
            cur.execute("SELECT id, name, phone_number, region FROM sales_executives WHERE region ILIKE '%مسقط%' AND status = 'نشط' LIMIT 1;")
            rep = cur.fetchone()
        return rep

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
    sender_clean = f"{sender_phone} ({sender_name})"

    msg_output = (
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
        f"*Sender Contact:* {sender_clean}\n"
        f"*Delivery Location:*\n"
        f"{location_url if location_url else 'Registered Branch Location'}\n"
        f"----------------------------------------\n"
        f"Food Development Co. | Logistics & Operations"
    )
    return msg_output

def init_database():
    conn = get_db_connection()
    if not conn:
        return

    try:
        with conn.cursor() as cur:
            # جدول حفظ النسخ الاحتياطية المجمعة للجلسات Snapshots
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
            CREATE TABLE IF NOT EXISTS customer_bot_sessions (
                phone_number VARCHAR(50) PRIMARY KEY,
                customer_name VARCHAR(150),
                conversation_history JSONB DEFAULT '[]'::jsonb,
                last_interaction TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
                whatsapp_group_id VARCHAR(100),
                tier VARCHAR(10) DEFAULT 'B',
                status VARCHAR(20) DEFAULT 'نشط'
            );
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS customer_branches (
                id SERIAL PRIMARY KEY,
                customer_id INT REFERENCES customer_accounts(id) ON DELETE CASCADE,
                branch_name VARCHAR(150) NOT NULL,
                branch_phone VARCHAR(50) DEFAULT '',
                location_url TEXT DEFAULT '',
                city VARCHAR(100) DEFAULT 'مسقط',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
                rep_id INT,
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
                product_id INT,
                product_name VARCHAR(200),
                qty_free INT NOT NULL DEFAULT 1,
                delivery_date DATE DEFAULT CURRENT_DATE,
                reminder_at VARCHAR(50) DEFAULT '',
                status VARCHAR(50) DEFAULT 'PENDING',
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
                rep_name VARCHAR(255),
                task_type VARCHAR(150),
                scheduled_at VARCHAR(50),
                reminder_at VARCHAR(50) DEFAULT '',
                location VARCHAR(255),
                change_notes TEXT DEFAULT '',
                route_code VARCHAR(50) DEFAULT 'R-01',
                execution_status VARCHAR(50) DEFAULT 'PENDING'
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
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
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
                category VARCHAR(50) DEFAULT 'UNIFIED',
                role_type VARCHAR(100) DEFAULT 'UNIFIED',
                listen_scope VARCHAR(50) DEFAULT 'ALL_GROUPS',
                dispatch_channel VARCHAR(100) DEFAULT '',
                enable_web_search BOOLEAN DEFAULT FALSE,
                search_keywords TEXT DEFAULT '',
                system_prompt TEXT NOT NULL,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)
        conn.commit()
    except Exception as e:
        logger.error(f"Error initializing DB: {e}")
        conn.rollback()
    finally:
        conn.close()

    run_isolated_ddl("ALTER TABLE ai_agents ALTER COLUMN role_type DROP NOT NULL;")
    run_isolated_ddl("ALTER TABLE ai_agents ALTER COLUMN role_type SET DEFAULT 'UNIFIED';")
    run_isolated_ddl("ALTER TABLE ai_agents ALTER COLUMN category SET DEFAULT 'UNIFIED';")
    run_isolated_ddl("ALTER TABLE ai_agents ADD COLUMN IF NOT EXISTS listen_scope VARCHAR(50) DEFAULT 'ALL_GROUPS';")
    run_isolated_ddl("ALTER TABLE ai_agents ADD COLUMN IF NOT EXISTS dispatch_channel VARCHAR(100) DEFAULT '';")
    run_isolated_ddl("ALTER TABLE ai_agents ADD COLUMN IF NOT EXISTS enable_web_search BOOLEAN DEFAULT FALSE;")
    run_isolated_ddl("ALTER TABLE ai_agents ADD COLUMN IF NOT EXISTS search_keywords TEXT DEFAULT '';")
    run_isolated_ddl("ALTER TABLE customer_bot_sessions ADD COLUMN IF NOT EXISTS conversation_history JSONB DEFAULT '[]'::jsonb;")

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

app = FastAPI(title="FDC Sales CRM", version="20.6.0", lifespan=lifespan)

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

class UnifiedAgentPayload(BaseModel):
    name: str
    listen_scope: Optional[str] = "ALL_GROUPS"
    dispatch_channel: Optional[str] = ""
    enable_web_search: Optional[bool] = False
    search_keywords: Optional[str] = ""
    system_prompt: str

class UpdateUnifiedAgentPayload(BaseModel):
    name: str
    listen_scope: Optional[str] = "ALL_GROUPS"
    dispatch_channel: Optional[str] = ""
    enable_web_search: Optional[bool] = False
    search_keywords: Optional[str] = ""
    system_prompt: str

class ToggleAgentPayload(BaseModel):
    is_active: bool

class IncomingWhatsAppMessage(BaseModel):
    chat_id: str
    sender_phone: str
    sender_name: str
    message_text: str

class InboundBotMessage(BaseModel):
    sender_phone: str
    sender_name: str
    message_text: str

class CustomerBranchPayload(BaseModel):
    customer_id: int
    branch_name: str
    branch_phone: Optional[str] = ""
    location_url: Optional[str] = ""
    city: Optional[str] = "مسقط"

class SystemConfigPayload(BaseModel):
    logistics_group_id: Optional[str] = ""
    management_group_id: Optional[str] = ""

class SessionSnapshotPayload(BaseModel):
    session_name: str
    snapshot: Dict[str, str]

# ----------------- مسارات مزامنة واسترجاع الـ Snapshot المجمعة -----------------
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
    except Exception as e:
        logger.error(f"Error saving session snapshot: {e}")
        conn.rollback()
        return Response(status_code=500)
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

# ----------------- التحقق الأمني الصارم 2FA النظيف -----------------
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

# ----------------- مسارات الوكلاء الموحدين -----------------
@app.get("/api/agents")
def get_unified_agents():
    conn = get_db_connection()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM ai_agents ORDER BY id ASC;")
            rows = cur.fetchall()
            for r in rows:
                r["listen_scope"] = r.get("listen_scope") or "ALL_GROUPS"
                r["dispatch_channel"] = r.get("dispatch_channel") or ""
                r["enable_web_search"] = bool(r.get("enable_web_search", False))
                r["search_keywords"] = r.get("search_keywords") or ""
            return rows
    finally:
        conn.close()

@app.post("/api/agents")
def create_unified_agent(payload: UnifiedAgentPayload):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not reachable")
    try:
        with conn.cursor() as cur:
            cur.execute("""
            INSERT INTO ai_agents (name, category, role_type, listen_scope, dispatch_channel, enable_web_search, search_keywords, system_prompt, is_active)
            VALUES (%s, 'UNIFIED', 'UNIFIED', %s, %s, %s, %s, %s, TRUE) RETURNING id;
            """, (
                payload.name.strip(), payload.listen_scope or "ALL_GROUPS", 
                payload.dispatch_channel or "", payload.enable_web_search or False, 
                payload.search_keywords or "", payload.system_prompt.strip()
            ))
            new_id = cur.fetchone()["id"]
            conn.commit()
            return {"status": "SUCCESS", "id": new_id}
    except Exception as e:
        logger.error(f"Error creating agent: {e}")
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.post("/api/agents/{agent_id}/update")
def update_unified_agent(agent_id: int, payload: UpdateUnifiedAgentPayload):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not reachable")
    try:
        with conn.cursor() as cur:
            cur.execute("""
            UPDATE ai_agents 
            SET name = %s, listen_scope = %s, dispatch_channel = %s, 
                enable_web_search = %s, search_keywords = %s, system_prompt = %s 
            WHERE id = %s;
            """, (
                payload.name.strip(), payload.listen_scope or "ALL_GROUPS", 
                payload.dispatch_channel or "", payload.enable_web_search or False, 
                payload.search_keywords or "", payload.system_prompt.strip(), agent_id
            ))
            conn.commit()
            return {"status": "SUCCESS"}
    except Exception as e:
        logger.error(f"Error updating agent: {e}")
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.delete("/api/agents/{agent_id}")
def delete_unified_agent(agent_id: int):
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

@app.post("/api/agents/{agent_id}/toggle")
def toggle_unified_agent_status(agent_id: int, payload: ToggleAgentPayload):
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
async def test_unified_agent(payload: dict):
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

            destination = agent.get("dispatch_channel") or test_target
            if not destination:
                raise HTTPException(status_code=400, detail="يرجى إدخال رقم هاتف الاختبار أو تحديد قناة الإرسال")

            if agent.get("enable_web_search"):
                keywords = agent.get("search_keywords") or agent["name"]
                intelligence_summary = await query_perplexity_intelligence(agent["system_prompt"], keywords)
                message_text = (
                    f"*{agent['name']} | تقرير استخبارات الويب الحي*\n"
                    f"----------------------------------------\n"
                    f"{intelligence_summary}\n"
                    f"----------------------------------------\n"
                    f"نظام المبيعات الذكي | شركة تنمية الغذاء"
                )
            else:
                message_text = f"*{agent['name']}*\n\n«{agent['system_prompt']}»\n\nشركة تنمية الغذاء (Food Development Company)"

        sent = await send_whatsapp_direct(destination, message_text)
        if sent:
            return {"status": "SUCCESS", "to": destination, "message_preview": message_text}
        else:
            raise HTTPException(status_code=400, detail="فشل الإرسال عبر الواتساب")
    finally:
        conn.close()

# ----------------- مسار بوت مبيعات العملاء الجدد التفاعلي -----------------
@app.post("/api/bot/inbound-sales")
async def handle_inbound_sales_bot(msg: InboundBotMessage):
    conn = get_db_connection()
    if not conn:
        return {"reply_text": "أهلاً بك في شركة تنمية الغذاء. سنعاود التواصل معك قريباً."}

    phone = msg.sender_phone
    text = msg.message_text.strip()

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT name_ar, weight_spec, carton_pack_spec FROM products_catalog ORDER BY name_ar ASC LIMIT 30;")
            prods = cur.fetchall()
            products_summary = "\n".join([f"- {p['name_ar']} (الوزن: {p['weight_spec'] or 'معتمد'}, التعبئة: {p['carton_pack_spec'] or 'كرتون'})" for p in prods])

            cur.execute("SELECT * FROM customer_bot_sessions WHERE phone_number = %s;", (phone,))
            session = cur.fetchone()

            history = []
            if session and session.get("conversation_history"):
                history = session["conversation_history"]
                if not isinstance(history, list):
                    history = []

            history.append({"role": "user", "content": text})
            history = history[-6:]

            system_instruction = (
                "أنت مسؤول المبيعات والاستشارات في شركة تنمية الغذاء (Food Development Company). "
                "تتحدث بأسلوب بشري مهني، ترحيبي، واثق، وسلس، دون إظهار أي طابع آلي وبدون خيارات رقمية. "
                "هدفنا توريد المخبوزات وعجائن المجمدات للمطاعم والفنادق وسلاسل الهايبرماركت. "
                f"أبرز المنتجات المتاحة لدينا:\n{products_summary}\n\n"
                "إرشادات الحوار:\n"
                "- إذا طلب عينات: رحب به وأكد أننا نقدم عينات تجريبية مجانية للشيف واسأله عن اسم المطعم وموقع الفرع.\n"
                "- إذا سأل عن الأسعار: وضح أن الأسعار تعتمد على حجم التوريد وعدد الفروع لتقديم أفضل سعر، واسأله بلطف عن عدد فروعه واستهلاكه التقريبي."
            )

            messages_payload = [{"role": "system", "content": system_instruction}]
            for h in history:
                messages_payload.append({"role": h["role"], "content": h["content"]})

            reply_text = ""
            if PERPLEXITY_API_KEY:
                url = "https://api.perplexity.ai/chat/completions"
                headers = {"Authorization": f"Bearer {PERPLEXITY_API_KEY}", "Content-Type": "application/json"}
                payload = {"model": "sonar", "messages": messages_payload, "temperature": 0.3}
                try:
                    async with httpx.AsyncClient() as client:
                        resp = await client.post(url, json=payload, headers=headers, timeout=15.0)
                        if resp.status_code == 200:
                            reply_text = resp.json()["choices"][0]["message"]["content"]
                except Exception:
                    pass

            if not reply_text:
                reply_text = f"أهلاً وسهلاً بك أخي العزيز في شركة تنمية الغذاء. كيف يمكننا خدمتك اليوم في توريد المخبوزات لمطعمكم الموقر؟"

            history.append({"role": "assistant", "content": reply_text})

            if any(w in text.lower() for w in ["عينة", "عينات", "تجربة", "تذوق", "sample"]):
                rep = match_rep_by_region(conn, text)
                cur.execute("""
                INSERT INTO sample_deliveries (customer_name, rep_name, rep_id, product_name, qty_free, delivery_date, status, source)
                VALUES (%s, %s, %s, %s, 10, CURRENT_DATE, 'PENDING', 'واتساب مبيعات العملاء الجدد');
                """, (msg.sender_name, rep["name"] if rep else "فريق المبيعات", rep["id"] if rep else None, f"مخبوزات متنوعة (طلب عميل: {text[:35]})"))

                if rep and rep.get("phone_number"):
                    lead_msg = (
                        f"*طلب عينة تجريبية من عميل وارد 🥖*\n\n"
                        f"• الاسم: {msg.sender_name}\n"
                        f"• الهاتف: {phone}\n"
                        f"• تفاصيل الطلب: {text}\n"
                        f"• المندوب الميداني: {rep['name']} ({rep['region']})\n\n"
                        f"يرجى التواصل لترتيب تسليم العينة."
                    )
                    await send_whatsapp_direct(rep["phone_number"], lead_msg)

            cur.execute("""
            INSERT INTO customer_bot_sessions (phone_number, customer_name, conversation_history, last_interaction)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (phone_number) DO UPDATE SET 
                conversation_history = EXCLUDED.conversation_history,
                last_interaction = NOW();
            """, (phone, msg.sender_name, json.dumps(history, ensure_ascii=False)))
            conn.commit()

            return {"reply_text": reply_text}
    finally:
        conn.close()

# ----------------- مسار الواتساب العام وتوجيه طلبيات المجموعات المحمي -----------------
@app.post("/api/whatsapp/webhook")
def handle_whatsapp_webhook(msg: IncomingWhatsAppMessage):
    conn = get_db_connection()
    if not conn:
        return {"status": "ERROR"}

    try:
        clean_phone = msg.sender_phone.replace("+", "").strip()
        chat_id = msg.chat_id.strip()
        text = msg.message_text.strip()
        channel_name = "محادثة مباشرة"
        reply_text = None
        forward_to_logistics = None
        logistics_text = None

        with conn.cursor() as cur:
            cur.execute("SELECT key_name, key_value FROM system_config;")
            conf = {r["key_name"]: r["key_value"] for r in cur.fetchall()}
            logistics_group = conf.get("logistics_group_id", "").strip()
            management_group = conf.get("management_group_id", "").strip()

            if chat_id.endswith("@s.whatsapp.net") and (chat_id.startswith(clean_phone) or "self" in chat_id):
                channel_name = "شات التحكم الخاص"
                if text.startswith("تقرير") or text.startswith("مستجدات"):
                    cur.execute("SELECT COUNT(*) FROM sales_targets WHERE status = 'IN_PROGRESS';")
                    active_t = cur.fetchone()["count"]
                    cur.execute("SELECT COUNT(*) FROM sample_deliveries WHERE status = 'PENDING';")
                    pending_s = cur.fetchone()["count"]
                    reply_text = (
                        f"*تقرير موجز من نظام تنمية الغذاء:*\n\n"
                        f"• الفرص البيعية الجارية: {active_t}\n"
                        f"• العينات قيد التجربة: {pending_s}\n\n"
                        f"النظام يعمل بنجاح ويرصد المجموعات المعتمدة."
                    )
            else:
                cur.execute("SELECT id, company_name, brand_name FROM customer_accounts WHERE whatsapp_group_id = %s;", (chat_id,))
                customer = cur.fetchone()

                if not customer and "@g.us" in chat_id:
                    clean_gid = chat_id.split("@")[0]
                    cur.execute("SELECT id, company_name, brand_name FROM customer_accounts WHERE whatsapp_group_id LIKE %s;", (f"%{clean_gid}%",))
                    customer = cur.fetchone()

                if customer:
                    channel_name = f"مجموعة: {customer['company_name']} ({customer['brand_name'] or 'عام'})"
                    
                    trigger_keywords = [
                        "box", "boxes", "cartoon", "carton", "cartoons", "ctn", "odare", "order", 
                        "potato", "buns", "bun", "bread", "brioche", "طلب", "طلبية", "كرتون", 
                        "حبة", "نحتاج", "ارسلوا", "محتاجين", "branch", "توصيل"
                    ]
                    
                    is_order_detected = any(k in text.lower() for k in trigger_keywords)

                    if is_order_detected:
                        cur.execute("SELECT * FROM customer_branches WHERE customer_id = %s;", (customer["id"],))
                        branches = cur.fetchall()

                        logistics_msg = format_dispatch_order_en(
                            text=text,
                            customer=customer,
                            sender_phone=msg.sender_phone,
                            sender_name=msg.sender_name,
                            branches=branches
                        )

                        cur.execute("""
                        INSERT INTO incoming_orders (customer_name, requester_name, requester_phone, order_raw_text, detected_items, status)
                        VALUES (%s, %s, %s, %s, %s, 'FORWARDED_TO_LOGISTICS');
                        """, (customer['company_name'], msg.sender_name, msg.sender_phone, text, logistics_msg))

                        if logistics_group:
                            forward_to_logistics = logistics_group
                            logistics_text = logistics_msg
                            print(f"[SUCCESS] Order automatically routed to Logistics Group: {logistics_group}")

                elif chat_id == management_group:
                    channel_name = "مجموعة الإدارة العليا"
                else:
                    channel_name = f"مجموعة ({chat_id[:15]}...)"

            try:
                cur.execute("""
                INSERT INTO whatsapp_logs (created_at, sender_name, channel_name, is_external_call, message_body)
                VALUES (NOW(), %s, %s, FALSE, %s);
                """, (msg.sender_name, channel_name, text))
            except Exception as log_err:
                logger.warning(f"Notice logging message: {log_err}")

            conn.commit()

            return {
                "status": "PROCESSED",
                "reply_text": reply_text,
                "forward_to_logistics": forward_to_logistics,
                "logistics_text": logistics_text
            }
    finally:
        conn.close()

# ----------------- باقي مسارات الـ API الأساسية -----------------
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
                    "id": r["id"], "name": r["name"], "employee_code": r["employee_code"],
                    "phone_number": r["phone_number"], "region": r["region"], "has_target": has_t,
                    "monthly_target": target, "achieved_sales": sales, "total_expenses": float(r.get("total_expenses") or 0),
                    "preferred_language": r.get("preferred_language") or "AR", "status": r["status"] or "نشط", "achievement_rate": rate
                })
            return enriched
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

@app.get("/api/customers/{customer_id}/branches")
def get_customer_branches(customer_id: int):
    conn = get_db_connection()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM customer_branches WHERE customer_id = %s ORDER BY id ASC;", (customer_id,))
            return cur.fetchall()
    finally:
        conn.close()

@app.post("/api/customers/branches")
def add_customer_branch(payload: CustomerBranchPayload):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not reachable")
    try:
        with conn.cursor() as cur:
            cur.execute("""
            INSERT INTO customer_branches (customer_id, branch_name, branch_phone, location_url, city)
            VALUES (%s, %s, %s, %s, %s) RETURNING id;
            """, (payload.customer_id, payload.branch_name.strip(), payload.branch_phone or "", payload.location_url or "", payload.city or "مسقط"))
            new_id = cur.fetchone()["id"]
            conn.commit()
            return {"status": "SUCCESS", "id": new_id}
    finally:
        conn.close()

@app.delete("/api/customers/branches/{branch_id}")
def delete_customer_branch(branch_id: int):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not reachable")
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM customer_branches WHERE id = %s;", (branch_id,))
            conn.commit()
            return {"status": "SUCCESS"}
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
                r["pipeline_stage"] = r.get("pipeline_stage") or "LEAD_CONTACT"
                start = r.get("started_at") or now
                delta = (r["closed_at"] if r.get("closed_at") else now) - start
                r["duration_text"] = f"{delta.days} يوم و {int(delta.seconds // 3600)} ساعة"
                r["started_at_str"] = start.strftime("%Y-%m-%d %H:%M") if hasattr(start, "strftime") else str(start)
                r["last_note_at_str"] = r["last_note_at"].strftime("%Y-%m-%d %H:%M") if r.get("last_note_at") and hasattr(r["last_note_at"], "strftime") else "—"
            return rows
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
                r["feedback_notes"] = r.get("feedback_notes") or ""
            return rows
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

@app.get("/api/whatsapp/sales-status")
async def get_whatsapp_sales_status():
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get("http://127.0.0.1:3001/sales/qr-status", timeout=1.5)
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

@app.get("/api/whatsapp/sales-qr")
async def get_whatsapp_sales_qr():
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get("http://127.0.0.1:3001/sales/qr-status", timeout=3.0)
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
    raise HTTPException(status_code=503, detail="جاري إقلاع محرك واتساب المبيعات...")

@app.post("/api/whatsapp/disconnect")
async def disconnect_whatsapp():
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post("http://127.0.0.1:3001/disconnect", timeout=8.0)
            if resp.status_code == 200:
                return resp.json()
    except Exception:
        pass
    raise HTTPException(status_code=500, detail="تعذر إنهاء الجلسة")

@app.post("/api/whatsapp/sales-disconnect")
async def disconnect_whatsapp_sales():
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post("http://127.0.0.1:3001/sales/disconnect", timeout=8.0)
            if resp.status_code == 200:
                return resp.json()
    except Exception:
        pass
    raise HTTPException(status_code=500, detail="تعذر إنهاء جلسة المبيعات")

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
            return rows
    finally:
        conn.close()

@app.get("/api/system/config")
def get_system_config():
    conn = get_db_connection()
    if not conn:
        return {"logistics_group_id": "", "management_group_id": ""}
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT key_name, key_value FROM system_config;")
            conf = {r["key_name"]: r["key_value"] for r in cur.fetchall()}
            return {"logistics_group_id": conf.get("logistics_group_id", ""), "management_group_id": conf.get("management_group_id", "")}
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
