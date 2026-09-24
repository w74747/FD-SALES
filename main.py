"""
main.py - Enterprise AI Sales CRM & Industrial Bakery Intelligence
Food Development Company (شركة تنمية الغذاء)
Includes: 
- Two-Tier Intent Verification Pipeline (NEW_ORDER vs DISCUSSION)
- Dispatched Orders Analytics & Monthly Reporting (By Customer & Branch)
- Fuzzy Phonetic Branch Matching (Bawshar vs Boshar & Al Khoudh)
- Branch CRUD with Live Update Endpoints
- Dynamic Trigger Keywords & JID Matching
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

UPLOADS_FOLDER = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOADS_FOLDER, exist_ok=True)
CATALOG_FILE_PATH = os.path.join(UPLOADS_FOLDER, "fdc_catalog.pdf")

whatsapp_process = None

LOGO_SVG_RAW = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 420 90" width="420" height="90">
  <rect width="100%" fill="transparent"/>
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
                timeout=25.0
            )
            return resp.status_code == 200
    except Exception as e:
        logger.error(f"Error sending WhatsApp document: {e}")
        return False

async def classify_order_intent(text: str) -> bool:
    """
    تحليل النية السياقية بالذكاء الاصطناعي:
    - NEW_ORDER (طلب شراء وتوريد جديد محدد) -> True
    - DISCUSSION (نقاش، استفسار عن موعد أو فاتورة، رد على كلام سابق، شكوى، أو تعديل شفهي) -> False
    """
    clean = text.strip().lower()

    inquiry_indicators = [
        "متى", "وين", "وصل", "تأخر", "فاتورة", "حساب", "غيرو", "ليش", "كنسل", 
        "عدل", "بدون فاتورة", "خليهم", "معاكم", "بكم", "السعر", "سلام", "شكرا", "thank"
    ]
    if any(q in clean for q in inquiry_indicators):
        if not (("branch" in clean or "فرع" in clean) and re.search(r'\d+\s*(box|boxes|carton|ctn|كرتون|كراتين)', clean)):
            return False

    if PERPLEXITY_API_KEY:
        prompt = (
            "أنت مصنف ذكي متخصص في فرز رسائل مجموعات مبيعات المخابز الصناعية.\n"
            "حلل الرسالة التالية بدقة وحدد هل هي (طلب توريد جديد محدد) أم (نقاش، استفسار، متابعة موعد، تعديل على كمية، سؤال عن فاتورة، أو حديث عام).\n\n"
            f"نص الرسالة: \"\"\"{text}\"\"\"\n\n"
            "شروط التصنيف الصارمة:\n"
            "1. أجب بكلمة واحدة فقط: NEW_ORDER إذا كانت الرسالة تتضمن أمر شراء جديداً ومحدداً بالأصناف والكميات المطلوبة للتسليم.\n"
            "2. أجب بكلمة واحدة فقط: DISCUSSION إذا كانت الرسالة سؤالاً، استفساراً، متابعة وصول، تعديلاً شفهياً لطلب سابق، أو رداً عادياً.\n"
            "جوابك (كلمة واحدة فقط):"
        )

        try:
            url = "https://api.perplexity.ai/chat/completions"
            headers = {"Authorization": f"Bearer {PERPLEXITY_API_KEY}", "Content-Type": "application/json"}
            payload = {
                "model": "sonar",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.0,
                "max_tokens": 10
            }
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json=payload, headers=headers, timeout=4.5)
                if resp.status_code == 200:
                    verdict = resp.json()["choices"][0]["message"]["content"].strip().upper()
                    return "NEW_ORDER" in verdict
        except Exception as e:
            logger.warning(f"Intent classifier call failed, falling back to rule engine: {e}")

    has_quantity = bool(re.search(r'\d+\s*(box|boxes|cartoon|carton|cartons|ctn|كرتون|كراتين|حبة|حبات|كيس|درزن)', clean))
    is_not_question = not any(q in clean for q in ["متى", "وين", "وصل", "تأخر", "؟", "?"])
    return has_quantity and is_not_question

def normalize_branch_text(s: str) -> str:
    """توحيد الحروف المتقاربة بالإنجليزية والعربية لحل فروق التهجئة مثل Bawshar و Boshar"""
    if not s:
        return ""
    clean = s.lower()
    clean = re.sub(r'b[ao]+w?sh[ae]?r', 'boshar', clean)        # bawshar / bousher / boshar -> boshar
    clean = re.sub(r'kh[ou]+[wd]+h?', 'khoudh', clean)           # khoud / khodh / khoudh -> khoudh
    clean = re.sub(r'[^a-zA-Z0-9\u0600-\u06FF\s]', ' ', clean)
    clean = re.sub(r'\b(branch|main|street|st|al|فرع|شارع)\b', ' ', clean)
    return ' '.join(clean.split())

def save_dispatched_order_items(customer_id: int, customer_name: str, branch_name: str, order_date_str: str, cleaned_items: list):
    """تخزين بنود الطلبية والكميات المستخرجة رقمياً في قاعدة البيانات للإحصائيات الشهرية"""
    conn = get_db_connection()
    if not conn:
        return
    try:
        try:
            parsed_date = datetime.strptime(order_date_str.strip(), "%d/%m/%Y").date()
        except Exception:
            parsed_date = datetime.now().date()

        with conn.cursor() as cur:
            for item in cleaned_items:
                if item == "Items specified in customer communication":
                    continue
                qty_match = re.search(r'(\d+)\s*(box|boxes|carton|cartons|ctn|كرتون|كراتين)?', item, re.IGNORECASE)
                qty = int(qty_match.group(1)) if qty_match else 1
                unit = qty_match.group(2) if qty_match and qty_match.group(2) else 'box'

                clean_item = re.sub(r'[:=\-\d]+', ' ', item)
                clean_item = re.sub(r'\b(box|boxes|carton|cartons|ctn|كرتون|كراتين)\b', ' ', clean_item, flags=re.IGNORECASE)
                clean_item = ' '.join(clean_item.split()).strip()
                if not clean_item:
                    clean_item = "Potato Buns / Bakery Item"

                cur.execute("""
                INSERT INTO dispatched_orders (customer_id, customer_name, branch_name, order_date, item_name, quantity, unit)
                VALUES (%s, %s, %s, %s, %s, %s, %s);
                """, (customer_id, customer_name, branch_name, parsed_date, clean_item, qty, unit))
            conn.commit()
    except Exception as e:
        logger.error(f"Error saving dispatched order items: {e}")
    finally:
        conn.close()

def format_dispatch_order_en(text: str, customer: dict, sender_phone: str, sender_name: str, branches: list) -> tuple:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    
    loc_match = re.search(r'(https?://[^\s]+)', text)
    location_url = loc_match.group(1) if loc_match else ""

    contact_match = re.search(r'(?:contact|phone|tel|رقم|mobile)[:\s]*([0-9\+\s]{7,15})', text, re.IGNORECASE)
    branch_contact = contact_match.group(1).strip().replace(" ", "") if contact_match else ""

    delivery_date = "Next Scheduled Delivery"
    coming_match = re.search(r'(?:coming|delivery|توصيل|وصول)[:\s]*([0-9]{1,2}[\.\/\-][0-9]{1,2}[\.\/\-][0-9]{2,4})', text, re.IGNORECASE)
    if coming_match:
        delivery_date = coming_match.group(1).strip()

    date_match = re.search(r'(?:date|تاريخ)[:\s]*([0-9]{1,2}[\.\/\-][0-9]{1,2}[\.\/\-][0-9]{2,4})', text, re.IGNORECASE)
    order_date = date_match.group(1).strip() if date_match else datetime.now().strftime("%d/%m/%Y")

    brand_name = customer.get("brand_name") or ""
    
    branch_name = ""
    for l in lines:
        if 'branch' in l.lower() or 'فرع' in l.lower():
            m = re.search(r'([A-Za-z\u0600-\u06FF\s\-]+(?:branch|فرع[A-Za-z\u0600-\u06FF\s\-]*))', l, re.IGNORECASE)
            if m:
                clean_b = m.group(1).strip()
                if brand_name and brand_name.lower() in clean_b.lower():
                    clean_b = re.sub(brand_name, '', clean_b, flags=re.IGNORECASE).strip()
                branch_name = clean_b.title() if clean_b else ""
                break

    matched_branch = None

    if branches:
        for b in branches:
            b_reg = b.get("branch_name", "").strip()
            b_norm = normalize_branch_text(b_reg)
            text_norm = normalize_branch_text(text)
            branch_norm = normalize_branch_text(branch_name)

            if (b_reg.lower() in text.lower()) or (branch_name and branch_name.lower() in b_reg.lower()):
                matched_branch = b
                break

            if b_norm and (branch_norm or text_norm):
                b_words = set(b_norm.split())
                target_words = set(branch_norm.split()) if branch_norm else set(text_norm.split())
                if b_words.intersection(target_words):
                    matched_branch = b
                    break

        if not matched_branch and sender_phone:
            clean_sender = re.sub(r'[^0-9]', '', sender_phone)
            for b in branches:
                clean_b_phone = re.sub(r'[^0-9]', '', b.get("branch_phone") or "")
                if clean_b_phone and (clean_b_phone.endswith(clean_sender[-8:]) or clean_sender.endswith(clean_b_phone[-8:])):
                    matched_branch = b
                    break

    if matched_branch:
        if not branch_name:
            branch_name = matched_branch.get("branch_name", "Main Branch")
        if not location_url and matched_branch.get("location_url"):
            location_url = matched_branch["location_url"]
        if not branch_contact and matched_branch.get("branch_phone"):
            branch_contact = matched_branch["branch_phone"]

    if not branch_name:
        branch_name = "Main Branch"

    if not branch_contact:
        branch_contact = sender_phone if sender_phone else (customer.get("phone") or "N/A")

    raw_items = []
    for l in lines:
        if re.search(r'^(date|coming|location|contact|tel|phone|odare|order|تاريخ|توصيل|شكرا|thank|good|because|which|can|forwarded)', l, re.IGNORECASE):
            continue
        if 'http' in l.lower() or 'branch' in l.lower() or (brand_name and l.lower() == brand_name.lower()):
            continue
        if re.search(r'(box|boxes|cartoon|carton|cartons|ctn|كرتون|كراتين|كرتونين|حبة|حبات|pc|pcs|bag|كيس|bread|buns|bun|brioche|potato|خبز|صمون|برجر)', l, re.IGNORECASE):
            raw_items.append(l)

    cleaned_items = []
    i = 0
    while i < len(raw_items):
        item_text = raw_items[i]
        if i + 1 < len(raw_items) and re.search(r'^\d+\s*(box|boxes|cartoon|carton|cartons|ctn|كرتون|كراتين)', raw_items[i+1], re.IGNORECASE):
            item_text = f"{raw_items[i]}: {raw_items[i+1]}"
            i += 1
        cleaned_items.append(item_text)
        i += 1

    if not cleaned_items:
        cleaned_items = ["Items specified in customer communication"]

    items_formatted = "\n".join([f"- {it}" for it in cleaned_items])

    formatted_msg = (
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
        f"*Branch Contact:* {branch_contact}\n"
        f"*Delivery Location:*\n"
        f"{location_url if location_url else 'Registered Branch Location'}\n"
        f"----------------------------------------\n"
        f"Food Development Co. | Logistics & Operations"
    )

    return formatted_msg, branch_name, order_date, cleaned_items

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

            # جدول بنود الطلبيات الرقمي للإحصائيات الشهرية
            cur.execute("""
            CREATE TABLE IF NOT EXISTS dispatched_orders (
                id SERIAL PRIMARY KEY,
                customer_id INT REFERENCES customer_accounts(id) ON DELETE CASCADE,
                customer_name VARCHAR(200) NOT NULL,
                branch_name VARCHAR(150) NOT NULL,
                order_date DATE NOT NULL DEFAULT CURRENT_DATE,
                item_name VARCHAR(200) NOT NULL,
                quantity INT NOT NULL DEFAULT 1,
                unit VARCHAR(50) DEFAULT 'box',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
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

app = FastAPI(title="FDC Sales CRM", version="22.4.0", lifespan=lifespan)

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

class KeywordsPayload(BaseModel):
    keywords: str

class BulkCampaignItem(BaseModel):
    name: str
    phone: str

class BulkCampaignPayload(BaseModel):
    contacts: List[BulkCampaignItem]
    message_template: str
    include_catalog: bool = False
    custom_attachment_b64: Optional[str] = None
    custom_attachment_name: Optional[str] = None

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

class CustomerBranchPayload(BaseModel):
    customer_id: int
    branch_name: str
    branch_phone: Optional[str] = ""
    location_url: Optional[str] = ""
    city: Optional[str] = "مسقط"

class UpdateCustomerBranchPayload(BaseModel):
    branch_name: str
    branch_phone: Optional[str] = ""
    location_url: Optional[str] = ""
    city: Optional[str] = "مسقط"

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

class UnifiedAgentPayload(BaseModel):
    name: str
    listen_scope: Optional[str] = "ALL_GROUPS"
    dispatch_channel: Optional[str] = ""
    system_prompt: str

class SampleFeedbackPayload(BaseModel):
    status: str
    feedback_notes: Optional[str] = ""

class SampleConvertPOPayload(BaseModel):
    po_number: str
    po_value: Optional[float] = 0.0

class CalendarEventPayload(BaseModel):
    customer_name: str
    rep_name: str
    task_type: str
    scheduled_at: str
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

# ----------------- الكلمات المفتاحية الحية -----------------
@app.get("/api/system/trigger-keywords")
def get_trigger_keywords():
    conn = get_db_connection()
    if not conn:
        return {"keywords": ""}
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT key_value FROM system_config WHERE key_name = 'order_trigger_keywords';")
            row = cur.fetchone()
            default_kw = "box, boxes, cartoon, carton, cartons, ctn, odare, order, orders, potato, buns, bun, bread, brioche, طلب, طلبية, طلبيات, كرتون, كراتين, كرتونين, حبة, حبات, اوردر, أوردر, صلالة, مسقط"
            return {"keywords": row["key_value"] if row and row["key_value"] else default_kw}
    finally:
        conn.close()

@app.post("/api/system/trigger-keywords")
def save_trigger_keywords(payload: KeywordsPayload):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database unreachable")
    try:
        with conn.cursor() as cur:
            cur.execute("""
            INSERT INTO system_config (key_name, key_value) VALUES ('order_trigger_keywords', %s)
            ON CONFLICT (key_name) DO UPDATE SET key_value = EXCLUDED.key_value;
            """, (payload.keywords.strip(),))
            conn.commit()
            return {"status": "SUCCESS"}
    finally:
        conn.close()

# ----------------- إعدادات الرد الترحيبي والكتالوج -----------------
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
                "greeting_text": rows.get("exhibition_greeting_text", "أهلاً وسهلاً بك في شركة تنمية الغذاء. يسعدنا تواصلك معنا ونرفق لك كتالوج وقائمة منتجات المخبوزات الصناعية المعتمدة لدينا."),
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

# ----------------- الإرسال الجماعي الآمن ضد الحظر -----------------
@app.post("/api/campaigns/send-bulk")
async def send_bulk_campaign(payload: BulkCampaignPayload):
    if not payload.contacts:
        raise HTTPException(status_code=400, detail="قائمة الأرقام فارغة")

    async def run_safe_campaign():
        for contact in payload.contacts:
            name = contact.name.strip()
            phone = contact.phone.strip()
            personalized_text = payload.message_template.replace("{name}", name)

            if payload.custom_attachment_b64:
                file_bytes = base64.b64decode(payload.custom_attachment_b64)
                await send_whatsapp_document(phone, personalized_text, file_bytes, payload.custom_attachment_name or "Offer.pdf")
            elif payload.include_catalog and os.path.exists(CATALOG_FILE_PATH):
                with open(CATALOG_FILE_PATH, "rb") as f:
                    pdf_bytes = f.read()
                await send_whatsapp_document(phone, personalized_text, pdf_bytes, "Food_Development_Catalog.pdf")
            else:
                await send_whatsapp_direct(phone, personalized_text)

            jitter_delay = random.uniform(18.0, 32.0)
            await asyncio.sleep(jitter_delay)

    asyncio.create_task(run_safe_campaign())
    return {
        "status": "QUEUED",
        "total_contacts": len(payload.contacts),
        "message": f"تمت جدولة إرسال {len(payload.contacts)} رسالة بتأخير أمني ذكي ضد الحظر."
    }

# ----------------- رادار الواتساب وبوابة الفرز الذكي وحفظ الإحصائيات -----------------
@app.post("/api/whatsapp/webhook")
async def handle_whatsapp_webhook(msg: IncomingWhatsAppMessage):
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

            pure_group_num = re.sub(r'[^0-9]', '', chat_id)
            customer = None

            if pure_group_num:
                cur.execute("""
                SELECT id, company_name, brand_name, phone 
                FROM customer_accounts 
                WHERE regexp_replace(whatsapp_group_id, '[^0-9]', '', 'g') = %s 
                   OR whatsapp_group_id ILIKE %s 
                LIMIT 1;
                """, (pure_group_num, f"%{pure_group_num}%"))
                customer = cur.fetchone()

            if customer:
                channel_name = f"مجموعة: {customer['company_name']} ({customer['brand_name'] or 'عام'})"
                
                kw_val = conf.get("order_trigger_keywords", "")
                if kw_val:
                    trigger_keywords = [k.strip().lower() for k in kw_val.split(",") if k.strip()]
                else:
                    trigger_keywords = [
                        "box", "boxes", "cartoon", "carton", "cartons", "ctn", "odare", "order", "orders",
                        "potato", "buns", "bun", "bread", "brioche", "طلب", "طلبية", "طلبيات", "كرتون", 
                        "كراتين", "كرتونين", "حبة", "حبات", "اوردر", "أوردر", "صلالة", "مسقط"
                    ]
                
                if any(k in text.lower() for k in trigger_keywords):
                    is_actual_order = await classify_order_intent(text)

                    if is_actual_order:
                        cur.execute("SELECT * FROM customer_branches WHERE customer_id = %s;", (customer["id"],))
                        branches = cur.fetchall()
                        logistics_msg, matched_branch_name, parsed_order_date, order_items = format_dispatch_order_en(
                            text, customer, msg.sender_phone, msg.sender_name, branches
                        )
                        if logistics_group:
                            forward_to_logistics = logistics_group
                            logistics_text = logistics_msg

                        # تسجيل بنود الطلبية رقمياً للإحصائيات الشهرية
                        save_dispatched_order_items(
                            customer["id"], customer["company_name"], matched_branch_name, parsed_order_date, order_items
                        )
                    else:
                        logger.info(f"Classified as DISCUSSION. Withheld from logistics: {text[:45]}")

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

# ----------------- مسارات إحصائيات الطلبيات الشهرية -----------------
@app.get("/api/analytics/monthly-orders")
def get_monthly_orders_analytics(month: Optional[str] = None, customer_id: Optional[int] = None):
    conn = get_db_connection()
    if not conn:
        return {"records": [], "totals_by_item": [], "total_boxes": 0}
    try:
        with conn.cursor() as cur:
            query = """
                SELECT 
                    TO_CHAR(order_date, 'YYYY-MM') AS order_month,
                    customer_name,
                    branch_name,
                    item_name,
                    SUM(quantity) AS total_qty,
                    unit
                FROM dispatched_orders
                WHERE 1=1
            """
            params = []
            if month:
                query += " AND TO_CHAR(order_date, 'YYYY-MM') = %s"
                params.append(month)
            if customer_id:
                query += " AND customer_id = %s"
                params.append(customer_id)

            query += " GROUP BY order_month, customer_name, branch_name, item_name, unit ORDER BY order_month DESC, customer_name ASC;"
            cur.execute(query, tuple(params))
            rows = cur.fetchall()

            total_boxes = sum(r["total_qty"] for r in rows)

            item_query = """
                SELECT item_name, SUM(quantity) as item_total 
                FROM dispatched_orders 
                WHERE 1=1
            """
            item_params = []
            if month:
                item_query += " AND TO_CHAR(order_date, 'YYYY-MM') = %s"
                item_params.append(month)
            if customer_id:
                item_query += " AND customer_id = %s"
                item_params.append(customer_id)
            item_query += " GROUP BY item_name ORDER BY item_total DESC;"

            cur.execute(item_query, tuple(item_params))
            item_totals = cur.fetchall()

            return {
                "records": rows,
                "totals_by_item": item_totals,
                "total_boxes": total_boxes
            }
    finally:
        conn.close()

# ----------------- مسارات العملاء وفروعهم -----------------
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

@app.post("/api/customers/branches/{branch_id}/update")
def update_customer_branch(branch_id: int, payload: UpdateCustomerBranchPayload):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not reachable")
    try:
        with conn.cursor() as cur:
            cur.execute("""
            UPDATE customer_branches 
            SET branch_name = %s, branch_phone = %s, location_url = %s, city = %s
            WHERE id = %s;
            """, (payload.branch_name.strip(), payload.branch_phone or "", payload.location_url or "", payload.city or "مسقط", branch_id))
            conn.commit()
            return {"status": "SUCCESS"}
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

@app.post("/api/agents")
def create_unified_agent(payload: UnifiedAgentPayload):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not reachable")
    try:
        with conn.cursor() as cur:
            cur.execute("""
            INSERT INTO ai_agents (name, listen_scope, dispatch_channel, system_prompt, is_active)
            VALUES (%s, %s, %s, %s, TRUE) RETURNING id;
            """, (payload.name.strip(), payload.listen_scope or "ALL_GROUPS", payload.dispatch_channel or "", payload.system_prompt.strip()))
            new_id = cur.fetchone()["id"]
            conn.commit()
            return {"status": "SUCCESS", "id": new_id}
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
                raise HTTPException(status_code=400, detail="يرجى إدخال رقم هاتف الاختبار")

            message_text = (
                f"*DISPATCH & DELIVERY ORDER (TEST SIMULATION)*\n"
                f"----------------------------------------\n"
                f"*Company:* Yummies Burgers & Bakery\n"
                f"*Brand:* Yummies\n"
                f"*Branch:* Boshar Branch\n"
                f"*Order Received Date:* {datetime.now().strftime('%d/%m/%Y')}\n"
                f"*Target Delivery Date:* Tomorrow 08:00 AM\n"
                f"----------------------------------------\n"
                f"*Ordered Items & Quantities:*\n"
                f"- Brioche Burger Bun 75g: 10 Cartons\n"
                f"- Potato Roll Bread 65g: 8 Cartons\n"
                f"----------------------------------------\n"
                f"*Branch Contact:* +96894987936\n"
                f"*Delivery Location:*\n"
                f"https://maps.google.com/?q=23.5880,58.3829\n"
                f"----------------------------------------\n"
                f"Food Development Co. | Logistics & Operations"
            )

        sent = await send_whatsapp_direct(destination, message_text)
        if sent:
            return {"status": "SUCCESS", "to": destination, "message_preview": message_text}
        else:
            raise HTTPException(status_code=400, detail="فشل الإرسال عبر محرك الواتساب")
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

# ----------------- مسارات التقويم -----------------
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
            INSERT INTO calendar_events (customer_name, rep_name, task_type, scheduled_at, location, change_notes, route_code, execution_status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'PENDING') RETURNING id;
            """, (
                payload.customer_name, payload.rep_name, payload.task_type,
                payload.scheduled_at, payload.location or "", payload.change_notes or "", payload.route_code or "R-01"
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

# ----------------- مسارات العينات -----------------
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

@app.post("/api/samples/{sample_id}/feedback")
def submit_sample_feedback(sample_id: int, payload: SampleFeedbackPayload):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database unreachable")
    try:
        with conn.cursor() as cur:
            cur.execute("""
            UPDATE sample_deliveries 
            SET status = %s, feedback_notes = %s 
            WHERE id = %s;
            """, (payload.status, payload.feedback_notes or "", sample_id))
            conn.commit()
            return {"status": "SUCCESS"}
    finally:
        conn.close()

@app.post("/api/samples/{sample_id}/convert-po")
def convert_sample_to_po(sample_id: int, payload: SampleConvertPOPayload):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database unreachable")
    try:
        with conn.cursor() as cur:
            cur.execute("""
            UPDATE sample_deliveries 
            SET status = 'CONVERTED_PO', converted_po_id = %s, po_value = %s 
            WHERE id = %s RETURNING rep_name;
            """, (payload.po_number.strip(), payload.po_value or 0.0, sample_id))
            row = cur.fetchone()
            if row and row.get("rep_name") and (payload.po_value or 0) > 0:
                cur.execute("""
                UPDATE sales_executives 
                SET achieved_sales = achieved_sales + %s 
                WHERE name = %s;
                """, (payload.po_value, row["rep_name"]))
            conn.commit()
            return {"status": "SUCCESS"}
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

# ----------------- مسارات المنتجات والمصاريف -----------------
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

# ----------------- مسارات الواتساب وسجل الرادار -----------------
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
