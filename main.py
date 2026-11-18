"""
main.py - Enterprise AI Sales CRM & Industrial Bakery Intelligence
Food Development Company (شركة تنمية الغذاء)
Features:
- Dynamic Semantic AI Order & Branch Matching (No hardcoded dictionaries)
- Intelligent Branch Auto-Provisioning & Interactive Location Prompting
- Universal Historical Orders Reconciliation & Backfill Endpoint (/api/analytics/reconcile-missed-orders)
- SQL Parameter Safeguards against string format crashes
- Direct Session Reset Endpoint (/api/whatsapp/disconnect) with Database Purge
- Sales Staff Whitelisting & Auto-Exclusion from Customer Bot Triggers
- Real-time Message Edit & Message Revocation (Deletion) Tracking for Logistics
- Two-Tier Intent Verification Pipeline (NEW_ORDER vs DISCUSSION)
- ASAP Flexible Delivery Scheduling (Subject to Logistics Schedule)
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
import math
import asyncio
import random
from datetime import datetime, timedelta
from typing import Optional, List, Dict
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Response, Request, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse, JSONResponse
from pydantic import BaseModel
import security
import db_pool
import ai_client
import order_engine
import knowledge
import catalog
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

dispatched_messages_cache: Dict[str, dict] = {}

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
    """اتصال من المجمّع (Connection Pool) - close() يعيده للمجمّع بدل إغلاقه"""
    return db_pool.get_pooled_connection(DATABASE_URL)

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

MAX_CATALOG_BYTES = 15 * 1024 * 1024


def restore_catalog_from_db():
    """
    قرص حاوية Railway مؤقت ويُمسح مع كل نشر، لذلك الكتالوج محفوظ في قاعدة البيانات
    ويُستعاد إلى القرص عند تشغيل الخادم.
    """
    if os.path.exists(CATALOG_FILE_PATH):
        return
    conn = get_db_connection()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT content FROM stored_files WHERE file_key = 'catalog_pdf';")
            row = cur.fetchone()
        if row and row["content"]:
            with open(CATALOG_FILE_PATH, "wb") as f:
                f.write(bytes(row["content"]))
            logger.info("Catalog PDF restored from database.")
    except Exception as e:
        logger.warning(f"Catalog restore skipped: {e}")
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

def calculate_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def normalize_unit_name(raw_unit: str) -> str:
    if not raw_unit:
        return "Carton"
    u = raw_unit.strip().lower()
    if any(k in u for k in ['كرتون', 'كراتين', 'كرتونين', 'carton', 'cartons', 'box', 'boxes', 'ctn']):
        return "Carton"
    elif any(k in u for k in ['حبة', 'حبات', 'قطعة', 'pc', 'pcs', 'piece']):
        return "Piece"
    elif any(k in u for k in ['كيس', 'أكياس', 'bag', 'bags', 'pack', 'packs']):
        return "Pack"
    return "Carton"

async def get_location_address(location_url: str) -> dict:
    if not location_url:
        return {}
    coord_match = re.search(r'q=([0-9\.\-]+),([0-9\.\-]+)', location_url)
    if not coord_match:
        return {}
    lat, lon = coord_match.group(1), coord_match.group(2)
    try:
        url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}&zoom=18&addressdetails=1"
        headers = {"User-Agent": "FDCBakeryLogistics/2.8"}
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=headers, timeout=3.5)
            if resp.status_code == 200:
                data = resp.json().get("address", {})
                return {
                    "road": data.get("road") or data.get("street") or "",
                    "suburb": data.get("suburb") or data.get("neighbourhood") or data.get("residential") or data.get("city_district") or "",
                    "city": data.get("city") or data.get("town") or data.get("county") or "Muscat"
                }
    except Exception as e:
        logger.warning(f"Error reverse geocoding: {e}")
    return {}

def auto_create_new_branch(customer_id: int, branch_name: str, branch_phone: str, location_url: str, city: str = "مسقط") -> dict:
    conn = get_db_connection()
    if not conn:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT * FROM customer_branches 
                WHERE customer_id = %s AND location_url = %s
                LIMIT 1;
            """, (customer_id, location_url))
            existing = cur.fetchone()
            if existing:
                return existing

            cur.execute("""
                INSERT INTO customer_branches (customer_id, branch_name, branch_phone, location_url, city)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING *;
            """, (customer_id, branch_name.strip(), branch_phone.strip(), location_url.strip(), city))
            new_b = cur.fetchone()
            conn.commit()
            logger.info(f"Auto-provisioned new branch: {branch_name} for customer ID {customer_id}")
            return new_b
    except Exception as e:
        logger.error(f"Error auto-creating branch: {e}")
        return None
    finally:
        conn.close()

def get_customer_habitual_items(customer_id: int) -> list:
    conn = get_db_connection()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT item_name, COUNT(*) as frequency 
                FROM dispatched_orders 
                WHERE customer_id = %s AND item_name NOT LIKE %s
                GROUP BY item_name 
                ORDER BY frequency DESC 
                LIMIT 3;
            """, (customer_id, "%في مجال%"))
            rows = cur.fetchall()
            return [r["item_name"] for r in rows if r["item_name"]]
    except Exception as e:
        logger.warning(f"Error getting habitual items: {e}")
        return []
    finally:
        conn.close()

async def extract_order_and_branch_with_ai(text: str, customer_brand: str, registered_branches: list, habitual_items: list) -> dict:
    """
    تحليل ذكي ديناميكي موحد:
    - يطابق الفرع دلالياً مع الفروع المسجلة (حتى لو كتب بالعربي أو بالعامية مثل 'بوشر' -> 'Bawshar Branch').
    - يكتشف أي فرع جديد غير مسجل ويترجمه فوراً للإنجليزية (مثل 'Amerat Branch').
    - يستخرج الأصناف والكميات ويترجمها للإنجليزية، ويفترض الصنف المعتاد في حال إغفال اسم الخبز (مثل '10 كراتين بوشر').
    """
    branches_context = [b.get("branch_name") for b in registered_branches] if registered_branches else []
    history_hint = ", ".join(habitual_items) if habitual_items else "Brioche Bun, Potato Bun"

    if PERPLEXITY_API_KEY:
        prompt = (
            f"You are an industrial bakery logistics dispatcher.\n"
            f"Customer '{customer_brand}' sent this order message in Arabic or English:\n"
            f"\"\"\"{text}\"\"\"\n\n"
            f"Client's registered delivery branches in our CRM database: {branches_context}\n"
            f"Client's recurring/habitual products from past orders: [{history_hint}]\n\n"
            f"Tasks:\n"
            f"1. Extract items & quantities formatted strictly in English lines like '- Potato Burger Bun: 10 Cartons'. If bread type is missing (e.g. '10 كراتين'), assume the habitual product.\n"
            f"2. Identify the target delivery branch:\n"
            f"   - Match it to one of the registered branches if mentioned (e.g., 'بوشر' matches 'Bawshar Branch', 'الخوض' matches 'Al Khoudh Branch').\n"
            f"   - If an unlisted/new branch area is specified (e.g., 'العامرات' or 'سلطنة' or 'صلالة'), provide its English name (e.g., 'Al Amerat Branch') and set 'matched_existing' to false and 'is_new_branch' to true.\n"
            f"   - If no branch is mentioned at all, set 'branch_name' to null.\n\n"
            f"Return STRICT JSON only without Markdown or backticks:\n"
            f"{{\n"
            f"  \"items\": [\"Potato Burger Bun: 10 Cartons\"],\n"
            f"  \"branch_name\": \"Bawshar Branch\",\n"
            f"  \"matched_existing\": true,\n"
            f"  \"is_new_branch\": false\n"
            f"}}"
        )
        try:
            url = "https://api.perplexity.ai/chat/completions"
            headers = {"Authorization": f"Bearer {PERPLEXITY_API_KEY}", "Content-Type": "application/json"}
            payload = {
                "model": "sonar",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 160
            }
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json=payload, headers=headers, timeout=5.5)
                if resp.status_code == 200:
                    raw = resp.json()["choices"][0]["message"]["content"].strip()
                    json_match = re.search(r'\{.*\}', raw, re.DOTALL)
                    if json_match:
                        return json.loads(json_match.group(0))
        except Exception as e:
            logger.warning(f"AI semantic branch & order extraction fallback: {e}")

    # Fallback في حال تعذر الذكاء الاصطناعي
    qty_match = re.search(r'(\d+)', text)
    qty = qty_match.group(1) if qty_match else "10"
    default_product = habitual_items[0] if habitual_items else "Burger Buns (Standard)"
    fallback_branch = registered_branches[0].get("branch_name") if registered_branches else None
    return {
        "items": [f"{default_product}: {qty} Cartons"],
        "branch_name": fallback_branch,
        "matched_existing": bool(fallback_branch),
        "is_new_branch": False
    }

async def classify_order_intent(text: str) -> bool:
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

def save_dispatched_order_items(customer_id: int, customer_name: str, branch_name: str, order_date_str: str, cleaned_items: list, message_id: Optional[str] = None):
    conn = get_db_connection()
    if not conn:
        return
    try:
        try:
            parsed_date = datetime.strptime(order_date_str.strip(), "%d/%m/%Y").date()
        except Exception:
            parsed_date = datetime.now().date()

        with conn.cursor() as cur:
            # عند تعديل العميل لرسالة الطلب (أو إعادة إرسال الـ webhook) تُستبدل أصناف الطلبية السابقة بالكامل
            # حتى تعكس الإحصائيات النسخة الأخيرة من الطلب فقط
            if message_id:
                cur.execute("DELETE FROM dispatched_orders WHERE message_id = %s;", (message_id,))

            for item in cleaned_items:
                if not item or item == "Items specified in customer communication":
                    continue

                item_name = ""
                qty = 1
                unit = "Carton"

                if ":" in item:
                    parts = item.split(":", 1)
                    item_name = parts[0].strip().lstrip("-").strip()
                    qty_part = parts[1].strip()
                    
                    q_match = re.search(r'(\d+)', qty_part)
                    qty = int(q_match.group(1)) if q_match else 1
                    
                    unit_match = re.search(r'[A-Za-z\u0600-\u06FF]+', qty_part)
                    unit = normalize_unit_name(unit_match.group(0) if unit_match else "Carton")
                else:
                    q_match = re.search(r'(\d+)', item)
                    qty = int(q_match.group(1)) if q_match else 1
                    
                    clean_item = re.sub(r'[:=\-\d]+', ' ', item)
                    clean_item = re.sub(r'\b(box|boxes|carton|cartons|ctn|كرتون|كراتين|في مجال|طلب|اليوم|محتاجين)\b', ' ', clean_item, flags=re.IGNORECASE)
                    item_name = ' '.join(clean_item.split()).strip()
                    unit = "Carton"

                if not item_name or any(noise in item_name.lower() for noise in ['في مجال', 'طلب اليوم', 'طلب']):
                    item_name = "Burger Buns (Standard)"

                cur.execute("""
                INSERT INTO dispatched_orders (customer_id, customer_name, branch_name, order_date, item_name, quantity, unit, message_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (message_id, item_name) WHERE message_id IS NOT NULL
                DO UPDATE SET quantity = dispatched_orders.quantity + EXCLUDED.quantity;
                """, (customer_id, customer_name, branch_name, parsed_date, item_name[:200], qty, unit, message_id))
            conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Error saving dispatched order items: {e}")
    finally:
        conn.close()

async def format_dispatch_order_en(
    text: str, 
    customer: dict, 
    sender_phone: str, 
    sender_name: str, 
    branches: list,
    shared_location: str = ""
) -> tuple:
    loc_match = re.search(r'(https?://[^\s]+)', text)
    location_url = shared_location if shared_location else (loc_match.group(1) if loc_match else "")

    contact_match = re.search(r'(?:contact|phone|tel|رقم|mobile)[:\s]*([0-9\+\s]{7,15})', text, re.IGNORECASE)
    branch_contact = contact_match.group(1).strip().replace(" ", "") if contact_match else ""

    delivery_date = "ASAP (Subject to Logistics Schedule)"
    coming_match = re.search(r'(?:coming|delivery|توصيل|وصول)[:\s]*([0-9]{1,2}[\.\/\-][0-9]{1,2}[\.\/\-][0-9]{2,4})', text, re.IGNORECASE)
    if coming_match:
        delivery_date = f"{coming_match.group(1).strip()} (Requested Date)"

    date_match = re.search(r'(?:date|تاريخ)[:\s]*([0-9]{1,2}[\.\/\-][0-9]{1,2}[\.\/\-][0-9]{2,4})', text, re.IGNORECASE)
    order_date = date_match.group(1).strip() if date_match else datetime.now().strftime("%d/%m/%Y")

    brand_name = customer.get("brand_name") or ""
    habitual_items = get_customer_habitual_items(customer.get("id", 0))

    # استخراج دلالي ذكي للأصناف والفرع
    ai_result = await extract_order_and_branch_with_ai(text, brand_name, branches, habitual_items)
    cleaned_items = ai_result.get("items", [])
    extracted_branch_name = ai_result.get("branch_name")
    is_new_branch = bool(ai_result.get("is_new_branch"))

    matched_branch = None

    # مطابقة الفرع المكتشف مع قاعدة البيانات
    if extracted_branch_name and branches:
        for b in branches:
            if b.get("branch_name", "").lower() in extracted_branch_name.lower() or extracted_branch_name.lower() in b.get("branch_name", "").lower():
                matched_branch = b
                extracted_branch_name = b.get("branch_name")
                is_new_branch = False
                break

    # معالجة اللوكيشن الجغرافي عند إرفاقه
    if location_url:
        coord_match = re.search(r'q=([0-9\.\-]+),([0-9\.\-]+)', location_url)
        curr_lat = float(coord_match.group(1)) if coord_match else None
        curr_lon = float(coord_match.group(2)) if coord_match else None

        extracted_addr = await get_location_address(location_url)
        road = extracted_addr.get("road", "")
        suburb = extracted_addr.get("suburb", "")
        city = extracted_addr.get("city", "Muscat")

        if curr_lat and curr_lon and branches:
            for b in branches:
                b_url = b.get("location_url", "")
                b_coords = re.search(r'q=([0-9\.\-]+),([0-9\.\-]+)', b_url)
                if b_coords:
                    dist = calculate_distance_km(curr_lat, curr_lon, float(b_coords.group(1)), float(b_coords.group(2)))
                    if dist <= 0.35:
                        matched_branch = b
                        is_new_branch = False
                        break

        # إذا كان الموقع بعيداً عن الفروع المسجلة، يتم تسجيله ذاتياً كفرع جديد
        if not matched_branch:
            is_new_branch = True
            auto_b_name = extracted_branch_name or (f"{suburb} Branch ({road})" if (suburb and road) else (f"{suburb} Branch" if suburb else "New Delivery Branch"))
            created_b = auto_create_new_branch(
                customer_id=customer["id"],
                branch_name=auto_b_name,
                branch_phone=sender_phone,
                location_url=location_url,
                city=city
            )
            matched_branch = created_b or {"branch_name": auto_b_name, "branch_phone": sender_phone}
            extracted_branch_name = auto_b_name

    # هل تم التعرف على الفرع بشكل قاطع؟
    branch_identified = False
    if matched_branch:
        branch_identified = True
        branch_name = matched_branch.get("branch_name", "Main Branch")
        if not location_url and matched_branch.get("location_url"):
            location_url = matched_branch["location_url"]
        if not branch_contact and matched_branch.get("branch_phone"):
            branch_contact = matched_branch["branch_phone"]
    elif extracted_branch_name and not is_new_branch:
        branch_identified = True
        branch_name = extracted_branch_name
    elif not extracted_branch_name and branches and len(branches) == 1:
        # إذا كان للعميل فرع واحد فقط مسجل ولم يذكر شيئاً
        branch_identified = True
        matched_branch = branches[0]
        branch_name = matched_branch.get("branch_name", "Main Branch")
        if not location_url and matched_branch.get("location_url"):
            location_url = matched_branch["location_url"]
        if not branch_contact and matched_branch.get("branch_phone"):
            branch_contact = matched_branch["branch_phone"]
    else:
        # فرع غير محدد أو جديد تماماً بدون لوكيشن
        branch_identified = False
        branch_name = extracted_branch_name or "Unspecified Branch"

    if not branch_contact:
        branch_contact = sender_phone if sender_phone else (customer.get("phone") or "N/A")

    items_formatted = "\n".join([f"- {it.lstrip('-').strip()}" for it in cleaned_items])

    header_title = "*DISPATCH & DELIVERY ORDER (NEW BRANCH ALERT 🚨)*" if is_new_branch else "*DISPATCH & DELIVERY ORDER*"
    branch_display = f"{branch_name} ⭐️ [NEW BRANCH - FIRST DELIVERY]" if is_new_branch else branch_name
    
    new_branch_notice = ""
    if is_new_branch:
        new_branch_notice = (
            f"----------------------------------------\n"
            f"⚠️ *Logistics Note:* This is a newly established branch location. Please verify location pin and contact receiver on arrival.\n"
        )

    formatted_msg = (
        f"{header_title}\n"
        f"----------------------------------------\n"
        f"*Company:* {customer.get('company_name', 'Customer')}\n"
        f"*Brand:* {brand_name if brand_name else customer.get('company_name', '')}\n"
        f"*Branch:* {branch_display}\n"
        f"*Order Received Date:* {order_date}\n"
        f"*Target Delivery Date:* {delivery_date}\n"
        f"----------------------------------------\n"
        f"*Ordered Items & Quantities:*\n"
        f"{items_formatted}\n"
        f"----------------------------------------\n"
        f"*Branch Contact:* {branch_contact}\n"
        f"*Delivery Location:*\n"
        f"{location_url if location_url else 'Registered Branch Location'}\n"
        f"{new_branch_notice}"
        f"----------------------------------------\n"
        f"Food Development Co. | Logistics & Operations"
    )

    return formatted_msg, branch_name, order_date, cleaned_items, branch_identified

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

            cur.execute("""
            CREATE TABLE IF NOT EXISTS dispatched_orders (
                id SERIAL PRIMARY KEY,
                customer_id INT REFERENCES customer_accounts(id) ON DELETE CASCADE,
                customer_name VARCHAR(200) NOT NULL,
                branch_name VARCHAR(150) NOT NULL,
                order_date DATE NOT NULL DEFAULT CURRENT_DATE,
                item_name VARCHAR(200) NOT NULL,
                quantity INT NOT NULL DEFAULT 1,
                unit VARCHAR(50) DEFAULT 'Carton',
                message_id VARCHAR(100),
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
            """)

            cur.execute("""
            UPDATE dispatched_orders 
            SET item_name = 'Burger Buns (Standard)' 
            WHERE item_name ILIKE '%في مجال%' OR item_name ILIKE '%طلب اليوم%';

            UPDATE dispatched_orders 
            SET unit = 'Carton' 
            WHERE unit IN ('كراتين', 'كرتون', 'box', 'boxes', 'ctn');
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
    run_isolated_ddl("ALTER TABLE dispatched_orders ADD COLUMN IF NOT EXISTS message_id VARCHAR(100);")

    # الطلبية الواحدة (رسالة واتساب واحدة) قد تحتوي عدة أصناف، لذلك يكون التفرد على (رقم الرسالة + الصنف)
    # بدلاً من رقم الرسالة وحده الذي كان يُسقط كل الأصناف بعد الأول ويُفشل الإدخال بالكامل.
    run_isolated_ddl("DROP INDEX IF EXISTS idx_dispatched_orders_msg_id;")
    run_isolated_ddl("ALTER TABLE dispatched_orders DROP CONSTRAINT IF EXISTS dispatched_orders_message_id_key;")
    run_isolated_ddl("""
        DELETE FROM dispatched_orders a USING dispatched_orders b
        WHERE a.message_id IS NOT NULL AND a.message_id = b.message_id
          AND a.item_name = b.item_name AND a.id > b.id;
    """)
    run_isolated_ddl("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_dispatched_orders_msg_item
        ON dispatched_orders (message_id, item_name) WHERE message_id IS NOT NULL;
    """)
    run_isolated_ddl("ALTER TABLE customer_accounts ALTER COLUMN contact_person SET DEFAULT '';")
    order_engine.init_schema(run_isolated_ddl)
    knowledge.init_schema(run_isolated_ddl)
    catalog.init_schema(run_isolated_ddl)
    run_isolated_ddl("""
        CREATE TABLE IF NOT EXISTS stored_files (
            file_key VARCHAR(100) PRIMARY KEY,
            file_name VARCHAR(255) NOT NULL,
            mime_type VARCHAR(100) NOT NULL,
            content BYTEA NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        );
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
        except Exception:
            pass

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_database()
    restore_catalog_from_db()
    start_whatsapp_service()
    scheduler_task = asyncio.create_task(order_scheduler_loop())
    yield
    scheduler_task.cancel()
    global whatsapp_process
    if whatsapp_process:
        whatsapp_process.terminate()
    db_pool.close_pool()

# توثيق الـ API التلقائي (/docs, /openapi.json) معطّل لعدم كشف خريطة المسارات للعموم
app = FastAPI(
    title="FDC Sales CRM", version="24.0.0", lifespan=lifespan,
    docs_url=None, redoc_url=None, openapi_url=None
)

# اللوحة تُخدم من نفس النطاق، لذلك CORS مغلق افتراضياً.
# لفتحه لنطاقات محددة: ALLOWED_ORIGINS=https://a.com,https://b.com
_allowed_origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]
if _allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type"],
    )


@app.middleware("http")
async def enforce_server_side_auth(request: Request, call_next):
    """
    بوابة الأمان المركزية:
    - المسارات الداخلية (جلسة الواتساب/Webhook) تتطلب التوكن الداخلي فقط.
    - بقية مسارات /api تتطلب جلسة دخول صالحة بعد التحقق الثنائي.
    """
    kind = security.classify_path(request.url.path)
    if request.method == "OPTIONS" or kind in ("public", "static"):
        return await call_next(request)

    if kind == "internal":
        if not security.is_valid_internal_token(request.headers.get(security.INTERNAL_TOKEN_HEADER)):
            return JSONResponse({"detail": "Forbidden"}, status_code=403)
        return await call_next(request)

    if not security.verify_session_token(request.cookies.get(security.SESSION_COOKIE_NAME)):
        return JSONResponse({"detail": "يجب تسجيل الدخول"}, status_code=401)
    return await call_next(request)

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

class Setup2FAPayload(BaseModel):
    setup_token: str

class LogisticsGroupPayload(BaseModel):
    group_id: str

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
    contact_person: Optional[str] = ""
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
    message_id: Optional[str] = None
    event_type: Optional[str] = "NORMAL"
    shared_location_url: Optional[str] = None
    # حقول المحرك الذكي: رسائل الفريق، الرد المقتبس، والصور/الملفات
    is_from_me: bool = False
    sender_is_staff_hint: bool = False
    quoted_message_id: Optional[str] = None
    quoted_text: Optional[str] = None
    media_base64: Optional[str] = None
    media_mimetype: Optional[str] = None
    media_filename: Optional[str] = None

class SessionSnapshotPayload(BaseModel):
    session_name: str
    snapshot: Dict[str, str]

# ----------------- مسار التحقق 2FA -----------------
def _set_session_cookie(response: Response, username: str):
    response.set_cookie(
        key=security.SESSION_COOKIE_NAME,
        value=security.create_session_token(username),
        max_age=security.SESSION_TTL_SECONDS,
        httponly=True,
        secure=security.COOKIE_SECURE,
        samesite="strict",
        path="/",
    )


@app.get("/api/auth/status")
def auth_status(request: Request):
    """حالة الدخول وحالة تفعيل 2FA لعرض الشاشة المناسبة في اللوحة"""
    authenticated = bool(security.verify_session_token(request.cookies.get(security.SESSION_COOKIE_NAME)))
    two_fa_enabled = False
    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT is_2fa_enabled FROM system_auth WHERE username = 'admin';")
                row = cur.fetchone()
                two_fa_enabled = bool(row and row["is_2fa_enabled"])
        finally:
            conn.close()
    return {
        "authenticated": authenticated,
        "two_fa_enabled": two_fa_enabled,
        "setup_available": bool(security.ADMIN_SETUP_TOKEN),
    }


@app.post("/api/auth/2fa/setup")
def setup_2fa(payload: Setup2FAPayload):
    """
    إعداد (أو إعادة ضبط) التحقق الثنائي للمدير.
    يتطلب ADMIN_SETUP_TOKEN المعرّف في Railway؛ يولّد مفتاحاً جديداً ويعرض رمز QR مرة واحدة.
    """
    if not security.ADMIN_SETUP_TOKEN:
        raise HTTPException(status_code=403, detail="إعداد 2FA معطّل: عرّف المتغير ADMIN_SETUP_TOKEN في Railway أولاً")
    if not security.is_valid_setup_token(payload.setup_token):
        security.otp_limiter.record_failure()
        raise HTTPException(status_code=403, detail="توكن الإعداد غير صحيح")
    if security.otp_limiter.is_blocked():
        raise HTTPException(status_code=429, detail="محاولات كثيرة، حاول بعد 5 دقائق")

    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="قاعدة البيانات غير متاحة")
    try:
        new_secret = pyotp.random_base32()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO system_auth (username, totp_secret, is_2fa_enabled) VALUES ('admin', %s, FALSE)
                ON CONFLICT (username) DO UPDATE SET totp_secret = EXCLUDED.totp_secret, is_2fa_enabled = FALSE;
            """, (new_secret,))
        conn.commit()
    finally:
        conn.close()

    otp_uri = pyotp.TOTP(new_secret).provisioning_uri(name="admin", issuer_name="FDC Sales CRM")
    from qrcode.image.pure import PyPNGImage
    buf = io.BytesIO()
    qrcode.make(otp_uri, image_factory=PyPNGImage).save(buf)
    return {
        "status": "PENDING_VERIFICATION",
        "otpauth_uri": otp_uri,
        "qr": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii"),
    }


@app.post("/api/auth/2fa/verify")
def verify_2fa(payload: Verify2FAPayload):
    if security.otp_limiter.is_blocked():
        return JSONResponse({"detail": "محاولات خاطئة كثيرة، حاول بعد 5 دقائق"}, status_code=429)
    conn = get_db_connection()
    if not conn:
        return JSONResponse({"detail": "قاعدة البيانات غير متاحة"}, status_code=500)
    try:
        clean_code = re.sub(r"\D", "", payload.code or "")
        with conn.cursor() as cur:
            cur.execute("SELECT totp_secret FROM system_auth WHERE username = 'admin';")
            row = cur.fetchone()
            secret = row["totp_secret"] if row else None

        if not secret:
            return JSONResponse({"detail": "لم يتم العثور على مفتاح التوثيق السري"}, status_code=400)

        if len(clean_code) == 6 and pyotp.TOTP(secret).verify(clean_code, valid_window=1):
            with conn.cursor() as cur:
                cur.execute("UPDATE system_auth SET is_2fa_enabled = TRUE WHERE username = 'admin';")
            conn.commit()
            security.otp_limiter.reset()
            response = JSONResponse({"status": "SUCCESS", "message": "تم التحقق بنجاح"})
            _set_session_cookie(response, "admin")
            return response

        security.otp_limiter.record_failure()
        return JSONResponse({"detail": "رمز التحقق غير صحيح أو انتهت صلاحيته"}, status_code=401)
    finally:
        conn.close()


@app.post("/api/auth/logout")
def logout():
    response = JSONResponse({"status": "LOGGED_OUT"})
    response.delete_cookie(security.SESSION_COOKIE_NAME, path="/")
    return response

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
    if len(content) > MAX_CATALOG_BYTES:
        raise HTTPException(status_code=400, detail="حجم الكتالوج يتجاوز 15 ميجابايت")
    if not content.startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="الملف ليس PDF صالحاً")
    # الحفظ في قاعدة البيانات أولاً (دائم)، ثم نسخة على القرص للإرسال السريع
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database unreachable")
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO stored_files (file_key, file_name, mime_type, content, updated_at)
                VALUES ('catalog_pdf', %s, 'application/pdf', %s, NOW())
                ON CONFLICT (file_key) DO UPDATE SET file_name = EXCLUDED.file_name,
                    content = EXCLUDED.content, updated_at = NOW();
            """, (file.filename, psycopg2.Binary(content)))
        conn.commit()
    finally:
        conn.close()
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

# ----------------- مسار المطابقة الشاملة واسترجاع الطلبيات الفائتة -----------------
@app.post("/api/analytics/reconcile-missed-orders")
async def reconcile_missed_orders():
    """
    محرك المطابقة الشامل: يمشط كافة الرسائل المخزنة في whatsapp_logs ويربطها بالعملاء
    حتى لو كانت مسجلة كـ 'محادثة مباشرة'، ويسجل أي طلبية فائتة بدقة تامة في الإحصائيات.
    """
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database unreachable")

    recovered_count = 0
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, company_name, brand_name, phone, whatsapp_group_id FROM customer_accounts;")
            customers = cur.fetchall()

            cur.execute("""
                SELECT id, created_at, sender_name, sender_phone, channel_name, message_body 
                FROM whatsapp_logs 
                WHERE message_body ~* '(box|boxes|carton|cartons|ctn|كرتون|كراتين|طلب|طلبية|potato|brioche|bun|buns)'
                ORDER BY id ASC;
            """)
            potential_logs = cur.fetchall()

            for log_entry in potential_logs:
                text = log_entry["message_body"].strip()
                fake_msg_id = f"reconciled-log-{log_entry['id']}"

                cur.execute("SELECT id FROM dispatched_orders WHERE message_id = %s;", (fake_msg_id,))
                if cur.fetchone():
                    continue

                matched_cust = None
                s_phone = re.sub(r'[^0-9]', '', log_entry.get("sender_phone") or "")
                ch_name = (log_entry.get("channel_name") or "").lower()

                for c in customers:
                    c_phone = re.sub(r'[^0-9]', '', c.get("phone") or "")
                    c_group = re.sub(r'[^0-9]', '', c.get("whatsapp_group_id") or "")
                    c_comp = (c.get("company_name") or "").lower()
                    c_brand = (c.get("brand_name") or "").lower()

                    if c_comp and c_comp in ch_name:
                        matched_cust = c
                        break
                    if c_brand and len(c_brand) > 2 and c_brand in ch_name:
                        matched_cust = c
                        break
                    if c_brand and len(c_brand) > 2 and c_brand in text.lower():
                        matched_cust = c
                        break
                    if s_phone and c_phone and (s_phone.endswith(c_phone[-8:]) or c_phone.endswith(s_phone[-8:])):
                        matched_cust = c
                        break
                    if c_group and c_group in ch_name:
                        matched_cust = c
                        break

                if not matched_cust:
                    continue

                is_order = await classify_order_intent(text)
                if not is_order:
                    continue

                cur.execute("SELECT * FROM customer_branches WHERE customer_id = %s;", (matched_cust["id"],))
                branches = cur.fetchall()

                _, branch_name, extracted_date, order_items, _ = await format_dispatch_order_en(
                    text, matched_cust, log_entry.get("sender_phone", ""), log_entry.get("sender_name", ""), branches
                )

                order_date_str = log_entry["created_at"].strftime("%d/%m/%Y") if log_entry.get("created_at") else extracted_date

                save_dispatched_order_items(
                    matched_cust["id"], matched_cust["company_name"], branch_name, order_date_str, order_items, message_id=fake_msg_id
                )
                recovered_count += 1

        conn.commit()
        return {
            "status": "SUCCESS",
            "recovered_count": recovered_count,
            "message": f"تمت مطابقة السجلات الشاملة بنجاح، واسترجاع وإدراج {recovered_count} طلبية فائتة إلى الإحصائيات."
        }
    finally:
        conn.close()

# ----------------- رادار الواتساب ومعالجة التعديل والحذف واستبعاد المندوبين -----------------
def resolve_logistics_target(cur, conf: dict) -> str:
    """
    تحديد مجموعة اللوجستيك التي تُوجَّه إليها أوامر التوريد:
    1. القيمة المحفوظة من شاشة الإعدادات (logistics_group_id).
    2. وإلا: قناة التوجيه لأول وكيل ذكي مفعّل لديه dispatch_channel.
    """
    target = (conf.get("logistics_group_id") or "").strip()
    if target:
        return target
    try:
        cur.execute("""
            SELECT dispatch_channel FROM ai_agents
            WHERE is_active = TRUE AND COALESCE(TRIM(dispatch_channel), '') <> ''
            ORDER BY id ASC LIMIT 1;
        """)
        row = cur.fetchone()
        return (row["dispatch_channel"] or "").strip() if row else ""
    except Exception as e:
        logger.warning(f"Could not resolve logistics target from agents: {e}")
        return ""


@app.get("/api/system/logistics-group")
def get_logistics_group():
    conn = get_db_connection()
    if not conn:
        return {"group_id": "", "effective_target": ""}
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT key_name, key_value FROM system_config;")
            conf = {r["key_name"]: r["key_value"] for r in cur.fetchall()}
            return {
                "group_id": conf.get("logistics_group_id", ""),
                "effective_target": resolve_logistics_target(cur, conf),
            }
    finally:
        conn.close()


@app.post("/api/system/logistics-group")
def save_logistics_group(payload: LogisticsGroupPayload):
    group_id = payload.group_id.strip()
    if group_id and not re.fullmatch(r"[0-9]+(-[0-9]+)?(@g\.us|@s\.whatsapp\.net)?", group_id):
        raise HTTPException(status_code=400, detail="صيغة معرّف المجموعة غير صحيحة (مثال: 120363000000000000@g.us)")
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database unreachable")
    try:
        with conn.cursor() as cur:
            cur.execute("""
            INSERT INTO system_config (key_name, key_value) VALUES ('logistics_group_id', %s)
            ON CONFLICT (key_name) DO UPDATE SET key_value = EXCLUDED.key_value;
            """, (group_id,))
            conn.commit()
            return {"status": "SUCCESS", "group_id": group_id}
    finally:
        conn.close()


@app.get("/api/whatsapp/groups")
async def list_whatsapp_groups():
    """قائمة مجموعات الواتساب المتصلة لاختيار مجموعة اللوجستيك من الواجهة"""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get("http://127.0.0.1:3001/groups", timeout=8.0)
            if resp.status_code == 200:
                return resp.json()
    except Exception as e:
        logger.warning(f"Could not fetch WhatsApp groups: {e}")
    return []


# ======================= الطلبيات الذكية =======================

class OrderProfilePayload(BaseModel):
    product_aliases: Optional[str] = ""
    default_branch: Optional[str] = ""
    default_unit: Optional[str] = "Cartons"
    ai_notes: Optional[str] = ""
    lead_days: Optional[int] = 1
    reminder_enabled: Optional[bool] = False
    reminder_days: Optional[str] = "0,1,2,3,4,5,6"
    reminder_time: Optional[str] = "09:00"
    reminder_text: Optional[str] = ""
    reminder_frequency: Optional[str] = "daily"
    products: Optional[List[dict]] = None
    auto_reply_enabled: Optional[bool] = True
    share_prices_with_bot: Optional[bool] = None


class AssistantSettingsPayload(BaseModel):
    admin_alert_phone: Optional[str] = ""
    auto_reply_enabled: Optional[bool] = False


class KnowledgeTextPayload(BaseModel):
    title: str
    content: str


class IssueStatusPayload(BaseModel):
    status: str


async def dispatch_actions(actions: list):
    """إرسال رسائل المحرك (للمجموعة أو للوجستيك أو للمندوب) عبر خدمة الواتساب"""
    for a in actions or []:
        if a.get("to") and a.get("text"):
            await send_whatsapp_direct(a["to"], a["text"])


def _load_customer(cur, customer_id: int) -> Optional[dict]:
    cur.execute("SELECT id, company_name, brand_name, phone, assigned_rep_id, whatsapp_group_id FROM customer_accounts WHERE id = %s;",
                (customer_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def _current_logistics_target(cur) -> str:
    cur.execute("SELECT key_name, key_value FROM system_config;")
    return resolve_logistics_target(cur, {r["key_name"]: r["key_value"] for r in cur.fetchall()})


async def _resolve_pending_by_id(pending_id: int, approve: bool, resolved_by: str) -> dict:
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database unreachable")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM pending_order_confirmations WHERE id = %s;", (pending_id,))
            pending = cur.fetchone()
            if not pending:
                raise HTTPException(status_code=404, detail="الطلب غير موجود")
            pending = dict(pending)
            if pending["status"] != "pending":
                return {"status": pending["status"], "already_resolved": True, "resolved_by": pending.get("resolved_by")}
            customer = _load_customer(cur, pending["customer_id"])
            profile = order_engine.load_profile(cur, pending["customer_id"])
            actions = order_engine.resolve_pending(
                cur, pending, approve, resolved_by, customer, profile, _current_logistics_target(cur),
                # العميل سُئل في المجموعة (أو طلب تأكيد التوفر): نغلق السؤال برسالة قصيرة عند الاعتماد
                announce_in_group=True)
        conn.commit()
    finally:
        conn.close()
    await dispatch_actions(actions)
    return {"status": "approved" if approve else "rejected"}


@app.get("/api/orders/pending")
def list_pending_orders():
    conn = get_db_connection()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            order_engine.expire_old_pending(cur)
            conn.commit()
            cur.execute("""
                SELECT p.id, p.customer_id, c.company_name, p.proposal, p.question_text, p.status, p.created_at
                FROM pending_order_confirmations p JOIN customer_accounts c ON c.id = p.customer_id
                WHERE p.status = 'pending' ORDER BY p.created_at DESC LIMIT 50;
            """)
            return cur.fetchall()
    finally:
        conn.close()


@app.post("/api/orders/pending/{pending_id}/approve")
async def approve_pending_order(pending_id: int):
    return await _resolve_pending_by_id(pending_id, True, "dashboard:admin")


@app.post("/api/orders/pending/{pending_id}/reject")
async def reject_pending_order(pending_id: int):
    return await _resolve_pending_by_id(pending_id, False, "dashboard:admin")


@app.get("/api/orders/batches")
def list_order_batches(days_back: int = 1):
    """الطلبيات المجمّعة من اليوم فصاعداً (طلبية واحدة لكل عميل وتاريخ تسليم)"""
    conn = get_db_connection()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT b.id, b.delivery_date, b.status, c.company_name, c.brand_name,
                       COALESCE(json_agg(json_build_object('branch', l.branch_name, 'product', l.product, 'sku', l.product_sku,
                                'quantity', l.quantity, 'unit', l.unit, 'value', l.line_value, 'unit_price', l.unit_price,
                                'price_unit', l.price_unit) ORDER BY l.branch_name, l.product)
                                FILTER (WHERE l.id IS NOT NULL), '[]') AS lines
                FROM order_batches b JOIN customer_accounts c ON c.id = b.customer_id
                LEFT JOIN order_lines l ON l.batch_id = b.id
                WHERE b.delivery_date >= %s
                GROUP BY b.id, c.company_name, c.brand_name
                ORDER BY b.delivery_date ASC, c.company_name ASC LIMIT 200;
            """, (order_engine.today_local() - timedelta(days=max(0, min(days_back, 60))),))
            batches = [dict(r) for r in cur.fetchall()]
            for b in batches:
                b["totals"] = order_engine.lines_totals(cur, b["lines"])
            return batches
    finally:
        conn.close()


@app.get("/api/customers/{customer_id}/order-profile")
def get_order_profile(customer_id: int):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database unreachable")
    try:
        with conn.cursor() as cur:
            profile = order_engine.load_profile(cur, customer_id)
            cur.execute("SELECT branch_name FROM customer_branches WHERE customer_id = %s ORDER BY id;", (customer_id,))
            profile["branches"] = [r["branch_name"] for r in cur.fetchall()]
            return profile
    finally:
        conn.close()


@app.post("/api/customers/{customer_id}/order-profile")
def save_order_profile(customer_id: int, payload: OrderProfilePayload):
    data = payload.dict()
    if data.get("reminder_time") and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", data["reminder_time"]):
        raise HTTPException(status_code=400, detail="وقت التذكير يجب أن يكون بصيغة HH:MM")
    data["reminder_days"] = ",".join(sorted(set(re.findall(r"[0-6]", data.get("reminder_days") or ""))))
    data["lead_days"] = max(0, min(int(data.get("lead_days") or 1), 14))
    data["reminder_frequency"] = "weekly" if data.get("reminder_frequency") == "weekly" else "daily"
    if data.get("products") is None:
        data.pop("products", None)
    else:
        cleaned = []
        for p in data["products"]:
            product = str(p.get("product") or "").strip()
            if not product:
                continue
            # القيمة المختارة من الكتالوج بصيغة "اسم المنتج [الكود]"
            m = re.search(r"\s*\[([A-Za-z0-9\-]+)\]\s*$", product)
            sku = (p.get("sku") or (m.group(1) if m else "")).strip()
            price = p.get("price")
            if price in (None, ""):
                price = None
            else:
                try:
                    price = round(float(price), 3)
                except (TypeError, ValueError):
                    raise HTTPException(status_code=400, detail=f"سعر غير صحيح للمنتج {product}")
                if not (0 <= price < 100000):
                    raise HTTPException(status_code=400, detail=f"سعر غير صحيح للمنتج {product}")
            unit = str(p.get("unit") or "").strip()[:30]
            cleaned.append({"alias": str(p.get("alias") or "").strip()[:120], "product": product[:m.start()].strip() if m else product[:200],
                            "sku": sku[:100], "unit": unit, "notes": str(p.get("notes") or "").strip()[:200],
                            "price": price, "price_unit": (str(p.get("price_unit") or "").strip() or unit)[:30]})
        data["products"] = cleaned[:100]
    if data.get("share_prices_with_bot") is None:
        data.pop("share_prices_with_bot", None)
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database unreachable")
    try:
        with conn.cursor() as cur:
            if "products" in data:
                # سجل تغيّر الأسعار: كل إضافة أو تعديل أو حذف لسعر منتج يُحفظ بتاريخه
                old = {(x.get("sku") or x.get("product")): x for x in order_engine.load_profile(cur, customer_id).get("products") or []}
                new = {(x.get("sku") or x.get("product")): x for x in data["products"]}
                for key in set(old) | set(new):
                    o, n = old.get(key) or {}, new.get(key) or {}
                    op, np_ = o.get("price"), n.get("price")
                    if (op is None) != (np_ is None) or (op is not None and abs(float(op) - float(np_)) > 0.0005) \
                            or (np_ is not None and (o.get("price_unit") or "") != (n.get("price_unit") or "")):
                        cur.execute("""INSERT INTO customer_price_history (customer_id, product_sku, product, old_price, new_price, price_unit)
                                       VALUES (%s, %s, %s, %s, %s, %s);""",
                                    (customer_id, (n or o).get("sku") or None, (n or o).get("product") or str(key),
                                     op, np_, (n or o).get("price_unit") or ""))
            order_engine.save_profile(cur, customer_id, data)
        conn.commit()
        return {"status": "SUCCESS"}
    finally:
        conn.close()


@app.get("/api/customers/{customer_id}/price-history")
def customer_price_history(customer_id: int):
    conn = get_db_connection()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT product_sku, product, old_price, new_price, price_unit, changed_at
                           FROM customer_price_history WHERE customer_id = %s ORDER BY changed_at DESC LIMIT 200;""",
                        (customer_id,))
            return cur.fetchall()
    finally:
        conn.close()


_APPROVAL_PAGE = """<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>اعتماد طلب</title><style>
body{{font-family:system-ui,Tahoma,sans-serif;background:#f5f0fa;margin:0;padding:16px;color:#222}}
.card{{max-width:440px;margin:24px auto;background:#fff;border-radius:16px;padding:20px;box-shadow:0 4px 18px #3a056a22}}
h1{{font-size:18px;color:#3A056A;margin:0 0 12px}} .muted{{color:#666;font-size:13px}}
table{{width:100%;border-collapse:collapse;margin:12px 0;font-size:14px}} td{{border-bottom:1px solid #eee;padding:6px 4px}}
button{{width:100%;padding:14px;border:0;border-radius:12px;font-size:16px;font-weight:700;margin-top:8px;cursor:pointer}}
.ok{{background:#3A056A;color:#fff}} .no{{background:#fde8e8;color:#b00020}} .done{{padding:12px;border-radius:12px;background:#e8f7ee;color:#106b34;font-weight:700}}
</style></head><body><div class="card">{body}</div></body></html>"""


def _render_approval(pending: dict, company: str, token: str, notice: str = "") -> str:
    from html import escape
    p = pending["proposal"]
    rows = "".join(
        f"<tr><td>{escape(str(l.get('branch','')))}</td><td>{escape(str(l.get('product','')))}</td>"
        f"<td><b>{escape(order_engine._qty(float(l.get('quantity') or 0)))}</b> {escape(str(l.get('unit','')))}</td></tr>"
        for l in (p.get("lines") or []))
    reason = "العميل طلب تأكيد التوفر" if p.get("ambiguity") == "availability" else "البوت غير متأكد وسأل العميل"
    body = (f"<h1>اعتماد طلب — {escape(company)}</h1>"
            f"<div class='muted'>{escape(reason)} · التسليم: {escape(str(p.get('delivery_date') or '-'))} · نوع العملية: {escape(str(p.get('kind')))}</div>"
            f"<table>{rows or '<tr><td>بدون أصناف</td></tr>'}</table>")
    if pending.get("question_text"):
        body += f"<div class='muted'>السؤال للعميل: {escape(pending['question_text'])}</div>"
    if notice or pending["status"] != "pending":
        status_ar = {"approved": "تم اعتماد الطلب ✅", "rejected": "تم رفض الطلب", "expired": "انتهت صلاحية الطلب",
                     "superseded": "استُبدل بطلب أحدث من العميل"}.get(pending["status"], pending["status"])
        who = f" ({escape(pending.get('resolved_by') or '')})" if pending.get("resolved_by") else ""
        body += f"<p class='done'>{escape(notice or status_ar)}{who}</p>"
    else:
        body += (f"<form method='post' action='/approve/{escape(token)}'>"
                 f"<button class='ok' name='action' value='approve'>✅ اعتماد وإرسال للوجستيك</button>"
                 f"<button class='no' name='action' value='reject'>✖ رفض</button></form>")
    return _APPROVAL_PAGE.format(body=body)


def _pending_for_token(token: str) -> tuple:
    pid = order_engine.verify_approval_token(token)
    if not pid:
        raise HTTPException(status_code=404, detail="الرابط غير صالح أو منتهي")
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database unreachable")
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT p.*, c.company_name FROM pending_order_confirmations p
                           JOIN customer_accounts c ON c.id = p.customer_id WHERE p.id = %s;""", (pid,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="الطلب غير موجود")
            return dict(row), row["company_name"]
    finally:
        conn.close()


@app.get("/approve/{token}", response_class=HTMLResponse)
def approval_page(token: str):
    """صفحة اعتماد المندوب بضغطة (الرابط موقّع ومؤقت، لا يحتاج تسجيل دخول)"""
    pending, company = _pending_for_token(token)
    return HTMLResponse(_render_approval(pending, company, token))


@app.post("/approve/{token}", response_class=HTMLResponse)
async def approval_submit(token: str, request: Request):
    pending, company = _pending_for_token(token)
    form = await request.form()
    approve = form.get("action") == "approve"
    await _resolve_pending_by_id(pending["id"], approve, "rep-link")
    pending, company = _pending_for_token(token)
    return HTMLResponse(_render_approval(pending, company, token))


# ======================= إعدادات المساعد، قاعدة المعرفة، الشكاوى =======================

@app.get("/api/system/assistant-settings")
def get_assistant_settings():
    conn = get_db_connection()
    if not conn:
        return {"admin_alert_phone": "", "auto_reply_enabled": False}
    try:
        with conn.cursor() as cur:
            return {
                "admin_alert_phone": order_engine.get_setting(cur, "admin_alert_phone"),
                "auto_reply_enabled": order_engine.get_setting(cur, "auto_reply_enabled", "false").lower() == "true",
                "ai_enabled": ai_client.is_enabled(),
                "voice_enabled": bool(os.getenv("TOGETHER_API_KEY", "").strip()),
            }
    finally:
        conn.close()


@app.post("/api/system/assistant-settings")
def save_assistant_settings(payload: AssistantSettingsPayload):
    phone = re.sub(r"[^0-9]", "", payload.admin_alert_phone or "")
    if phone and not (8 <= len(phone) <= 15):
        raise HTTPException(status_code=400, detail="رقم الهاتف غير صحيح (مثال: 96890000000)")
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database unreachable")
    try:
        with conn.cursor() as cur:
            for k, v in (("admin_alert_phone", phone), ("auto_reply_enabled", "true" if payload.auto_reply_enabled else "false")):
                cur.execute("""INSERT INTO system_config (key_name, key_value) VALUES (%s, %s)
                               ON CONFLICT (key_name) DO UPDATE SET key_value = EXCLUDED.key_value;""", (k, v))
        conn.commit()
        return {"status": "SUCCESS"}
    finally:
        conn.close()


MAX_KB_UPLOAD = 15 * 1024 * 1024


@app.get("/api/knowledge")
def list_knowledge():
    conn = get_db_connection()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT id, title, file_name, mime_type, is_active, created_at, updated_at,
                                  LENGTH(content_text) AS char_count, LEFT(content_text, 300) AS preview
                           FROM knowledge_documents ORDER BY id DESC;""")
            return cur.fetchall()
    finally:
        conn.close()


@app.get("/api/knowledge/{doc_id}")
def get_knowledge_doc(doc_id: int):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database unreachable")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM knowledge_documents WHERE id = %s;", (doc_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="غير موجود")
            return row
    finally:
        conn.close()


def _save_knowledge(title: str, file_name: str, mime: str, text: str) -> dict:
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database unreachable")
    try:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO knowledge_documents (title, file_name, mime_type, content_text)
                           VALUES (%s, %s, %s, %s) RETURNING id, LENGTH(content_text) AS char_count;""",
                        (title[:200], file_name[:255], mime[:120], text))
            row = cur.fetchone()
        conn.commit()
        return {"status": "SUCCESS", "id": row["id"], "char_count": row["char_count"]}
    finally:
        conn.close()


@app.post("/api/knowledge/upload")
async def upload_knowledge(file: UploadFile = File(...), title: str = Form("")):
    """رفع ملف معلومات للبوت: PDF، Word، Excel، CSV/نص، أو صورة"""
    raw = await file.read()
    if len(raw) > MAX_KB_UPLOAD:
        raise HTTPException(status_code=400, detail="الحد الأقصى لحجم الملف 15 ميجابايت")
    mime = file.content_type or "application/octet-stream"
    text = await knowledge.extract_document_text(raw, mime, file.filename or "")
    if len(text) < 20:
        raise HTTPException(status_code=400, detail="تعذر استخراج نص من الملف. جرّب PDF نصياً أو Word أو Excel، أو الصق المحتوى كنص.")
    return _save_knowledge(title.strip() or (file.filename or "ملف"), file.filename or "", mime, text)


@app.post("/api/knowledge/text")
def add_knowledge_text(payload: KnowledgeTextPayload):
    if len(payload.content.strip()) < 5:
        raise HTTPException(status_code=400, detail="المحتوى قصير جداً")
    return _save_knowledge(payload.title.strip() or "ملاحظة", "", "text/plain", payload.content.strip()[:knowledge.MAX_DOC_CHARS])


@app.post("/api/knowledge/{doc_id}/toggle")
def toggle_knowledge(doc_id: int):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database unreachable")
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE knowledge_documents SET is_active = NOT is_active, updated_at = NOW() WHERE id = %s RETURNING is_active;", (doc_id,))
            row = cur.fetchone()
        conn.commit()
        return {"status": "SUCCESS", "is_active": bool(row and row["is_active"])}
    finally:
        conn.close()


@app.delete("/api/knowledge/{doc_id}")
def delete_knowledge(doc_id: int):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database unreachable")
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM knowledge_documents WHERE id = %s;", (doc_id,))
        conn.commit()
        return {"status": "SUCCESS"}
    finally:
        conn.close()


@app.get("/api/issues")
def list_issues(status: str = "", issue_type: str = ""):
    conn = get_db_connection()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT i.*, c.company_name FROM customer_issues i JOIN customer_accounts c ON c.id = i.customer_id
                WHERE (%s = '' OR i.status = %s) AND (%s = '' OR i.issue_type = %s)
                ORDER BY i.created_at DESC LIMIT 200;
            """, (status, status, issue_type, issue_type))
            return cur.fetchall()
    finally:
        conn.close()


@app.post("/api/issues/{issue_id}/status")
def update_issue_status(issue_id: int, payload: IssueStatusPayload):
    if payload.status not in ("open", "escalated", "answered_by_bot", "resolved"):
        raise HTTPException(status_code=400, detail="حالة غير صحيحة")
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database unreachable")
    try:
        with conn.cursor() as cur:
            cur.execute("""UPDATE customer_issues SET status = %s,
                           resolved_at = CASE WHEN %s = 'resolved' THEN NOW() ELSE NULL END WHERE id = %s;""",
                        (payload.status, payload.status, issue_id))
        conn.commit()
        return {"status": "SUCCESS"}
    finally:
        conn.close()


@app.get("/api/issues/summary")
def issues_summary(days: int = 90):
    """ملخص الشكاوى حسب المنتج ونوع المشكلة (لتقارير الجودة والبحث والتطوير)"""
    conn = get_db_connection()
    if not conn:
        return {"by_product": [], "by_category": []}
    try:
        with conn.cursor() as cur:
            since = (" AND created_at > NOW() - (%s || ' days')::interval", (str(max(1, min(days, 365))),))
            cur.execute("SELECT COALESCE(NULLIF(product, ''), 'غير محدد') AS product, COUNT(*) AS n, "
                        "SUM(CASE WHEN severity = 'high' THEN 1 ELSE 0 END) AS high "
                        "FROM customer_issues WHERE issue_type = 'complaint'" + since[0] +
                        " GROUP BY 1 ORDER BY n DESC LIMIT 20;", since[1])
            by_product = cur.fetchall()
            cur.execute("SELECT category, issue_type, COUNT(*) AS n FROM customer_issues WHERE TRUE" + since[0] +
                        " GROUP BY 1, 2 ORDER BY n DESC;", since[1])
            return {"by_product": by_product, "by_category": cur.fetchall()}
    finally:
        conn.close()


async def order_scheduler_loop():
    """كل دقيقة: إرسال تذكيرات الطلب المستحقة وإنهاء طلبات التأكيد القديمة"""
    await asyncio.sleep(20)
    while True:
        try:
            conn = get_db_connection()
            if conn:
                try:
                    with conn.cursor() as cur:
                        order_engine.expire_old_pending(cur)
                        actions = order_engine.due_reminders(cur)
                    conn.commit()
                finally:
                    conn.close()
                await dispatch_actions(actions)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Order scheduler error: {e}")
        await asyncio.sleep(60)


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

        # استبعاد فوري لأي رسالة قادمة من فريق العمل الداخلي
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, name FROM sales_executives 
                WHERE regexp_replace(phone_number, '[^0-9]', '', 'g') LIKE %s 
                   OR %s LIKE concat('%%', regexp_replace(phone_number, '[^0-9]', '', 'g'), '%%')
                LIMIT 1;
            """, (f"%{clean_phone[-8:]}%", clean_phone))
            is_staff = cur.fetchone()
            # رسائل الفريق لا تُعامل كطلبات، لكنها تُحفظ كسياق للمحرك الذكي (مثل سؤال المندوب "بدنا اوردر صلالة")
            sender_is_staff = bool(is_staff) or "FDC" in (msg.sender_name or "").upper() \
                or msg.is_from_me or msg.sender_is_staff_hint

        with conn.cursor() as cur:
            cur.execute("SELECT key_name, key_value FROM system_config;")
            conf = {r["key_name"]: r["key_value"] for r in cur.fetchall()}
            logistics_group = resolve_logistics_target(cur, conf)
            auto_welcome_enabled = conf.get("exhibition_auto_reply_enabled") == "true"
            welcome_greeting = conf.get("exhibition_greeting_text", "")

            pure_group_num = re.sub(r'[^0-9]', '', chat_id)
            customer = None

            if pure_group_num:
                cur.execute("""
                SELECT id, company_name, brand_name, phone, assigned_rep_id
                FROM customer_accounts 
                WHERE regexp_replace(whatsapp_group_id, '[^0-9]', '', 'g') = %s 
                   OR whatsapp_group_id ILIKE %s 
                LIMIT 1;
                """, (pure_group_num, f"%{pure_group_num}%"))
                customer = cur.fetchone()

            # ---------- المحرك الذكي لفهم الطلبات (Claude) ----------
            if customer and ai_client.is_enabled() and chat_id.endswith("@g.us"):
                conn.commit()
                if sender_is_staff:
                    return order_engine.handle_staff_message(conn, dict(customer), msg, logistics_group)
                result = await order_engine.handle_customer_message(conn, dict(customer), msg, logistics_group)
                if not result.get("fallback"):
                    try:
                        with conn.cursor() as log_cur:
                            log_cur.execute("""
                            INSERT INTO whatsapp_logs (created_at, sender_name, sender_phone, channel_name, is_external_call, message_body)
                            VALUES (NOW(), %s, %s, %s, FALSE, %s);
                            """, (msg.sender_name, clean_phone, f"مجموعة: {customer['company_name']}",
                                  text or ("[صورة/ملف]" if msg.media_base64 else "")))
                        conn.commit()
                    except Exception:
                        conn.rollback()
                    return result
                logger.warning("Smart order engine unavailable, falling back to legacy parser.")

            if sender_is_staff:
                logger.info(f"Ignored message from internal sales representative: {msg.sender_name} ({clean_phone})")
                return {"status": "IGNORED_INTERNAL_STAFF"}
            if not text and not msg.shared_location_url and msg.event_type != 'MESSAGE_DELETED':
                return {"status": "IGNORED_EMPTY"}

            if msg.event_type == 'MESSAGE_DELETED':
                # الطلبية المحذوفة من الواتساب تُلغى من الإحصائيات ويُبلَّغ فريق اللوجستيك.
                # يُقرأ الطلب من قاعدة البيانات (وليس من الذاكرة) حتى يعمل الإلغاء بعد إعادة تشغيل الخادم.
                cached_order = dispatched_messages_cache.pop(msg.message_id, None) if msg.message_id else None
                if msg.message_id:
                    cur.execute("""
                        DELETE FROM dispatched_orders WHERE message_id = %s
                        RETURNING customer_name, branch_name;
                    """, (msg.message_id,))
                    deleted_rows = cur.fetchall()
                    if deleted_rows:
                        cached_order = {"company": deleted_rows[0]["customer_name"], "branch": deleted_rows[0]["branch_name"]}
                    conn.commit()
                if cached_order:
                    if logistics_group:
                        cancel_notice = (
                            f"🚫 *ORDER RETRACTED / CANCELLED BY SENDER*\n"
                            f"----------------------------------------\n"
                            f"*Company:* {cached_order.get('company')}\n"
                            f"*Branch:* {cached_order.get('branch')}\n"
                            f"----------------------------------------\n"
                            f"⚠️ *Notice:* The customer deleted the order message from WhatsApp. Please hold and do not dispatch."
                        )
                        forward_to_logistics = logistics_group
                        logistics_text = cancel_notice
                return {
                    "status": "PROCESSED_DELETE",
                    "forward_to_logistics": forward_to_logistics,
                    "logistics_text": logistics_text
                }

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
                
                if any(k in text.lower() for k in trigger_keywords) or bool(msg.shared_location_url):
                    is_actual_order = await classify_order_intent(text)

                    if is_actual_order:
                        cur.execute("SELECT * FROM customer_branches WHERE customer_id = %s;", (customer["id"],))
                        branches = cur.fetchall()
                        
                        logistics_msg, matched_branch_name, parsed_order_date, order_items, branch_identified = await format_dispatch_order_en(
                            text, customer, msg.sender_phone, msg.sender_name, branches, msg.shared_location_url or ""
                        )

                        # إذا كان الفرع غير محدد أو فرعاً جديداً تماماً دون موقع، يُرسل استفسار التحديد التفاعلي
                        if not branch_identified:
                            reply_text = (
                                "فضلاً، يرجى تحديد الفرع المطلوب للتوصيل (مثلاً: فرع الخوض، فرع بوشر) أو مشاركة موقع الفرع (Location) لتأكيد أمر التوريد لفريق اللوجستيك.\n\n"
                                "Kindly specify the target delivery branch (e.g., Al Khoudh Branch, Boshar Branch) or share the branch Location Pin to confirm the dispatch order for the logistics team."
                            )
                            logger.info(f"Ambiguous branch for customer {customer['id']}. Sent interactive bilingual query in group.")
                        else:
                            if msg.event_type == 'MESSAGE_EDITED':
                                logistics_msg = (
                                    f"✏️ *AMENDED ORDER (CUSTOMER UPDATED MESSAGE)*\n"
                                    f"----------------------------------------\n"
                                    f"{logistics_msg.split('----------------------------------------', 1)[-1]}"
                                )

                            if logistics_group:
                                forward_to_logistics = logistics_group
                                logistics_text = logistics_msg

                            if msg.message_id:
                                dispatched_messages_cache[msg.message_id] = {
                                    "company": customer["company_name"],
                                    "branch": matched_branch_name,
                                    "time": datetime.now().isoformat()
                                }

                            save_dispatched_order_items(
                                customer["id"], customer["company_name"], matched_branch_name, parsed_order_date, order_items, message_id=msg.message_id
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
                (payload.contact_person or "").strip(), payload.phone.strip(),
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
                f"*Target Delivery Date:* ASAP (Subject to Logistics Schedule)\n"
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
class ProductSpecsPayload(BaseModel):
    name_ar: Optional[str] = None
    piece_weight_g: Optional[float] = None
    pieces_per_pack: Optional[float] = None
    packs_per_box: Optional[float] = None


@app.post("/api/products/import")
async def import_products(file: UploadFile = File(...)):
    """
    استيراد ملف المنتجات (Assortment) كما هو: يُحدَّث المنتج الموجود بكوده ويُضاف الجديد.
    يعيد ملخصاً بالمنتجات ذات المواصفات الناقصة أو المتعارضة لاستكمالها.
    """
    raw = await file.read()
    if len(raw) > 20 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="الحد الأقصى لحجم الملف 20 ميجابايت")
    try:
        parsed = catalog.parse_assortment(raw)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Product import parse failed: {e}")
        raise HTTPException(status_code=400, detail="تعذر قراءة الملف. تأكد أنه Excel (.xlsx)")
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database unreachable")
    try:
        with conn.cursor() as cur:
            counts = catalog.upsert_products(cur, parsed["products"])
        conn.commit()
    finally:
        conn.close()
    return {"status": "SUCCESS", "sheet": parsed["sheet"], "total": len(parsed["products"]), **counts,
            "incomplete": parsed["incomplete"], "inconsistent": parsed["inconsistent"]}


@app.get("/api/products/export")
def export_products():
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database unreachable")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM products_catalog ORDER BY category NULLS LAST, sku;")
            rows = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
    data = catalog.export_xlsx(rows)
    return Response(content=data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="FDC_Products_{datetime.now():%Y-%m-%d}.xlsx"'})


@app.post("/api/products/{sku}/specs")
def update_product_specs(sku: str, payload: ProductSpecsPayload):
    """استكمال مواصفات التعبئة الناقصة يدوياً (مثل عدد الأكياس في الكرتون)"""
    data = {k: v for k, v in payload.dict().items() if v is not None}
    for k in ("piece_weight_g", "pieces_per_pack", "packs_per_box"):
        if k in data and not (0 < float(data[k]) < 100000):
            raise HTTPException(status_code=400, detail="قيمة غير صحيحة")
    if not data:
        return {"status": "NO_CHANGE"}
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database unreachable")
    try:
        with conn.cursor() as cur:
            sets = ", ".join(f"{k} = %s" for k in data)
            cur.execute(f"UPDATE products_catalog SET {sets}, updated_at = NOW() WHERE sku = %s RETURNING *;",
                        list(data.values()) + [sku])
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="المنتج غير موجود")
            # عدد الحبات في الكرتون يُعاد حسابه من المواصفات المحدّثة
            if row["pieces_per_pack"] and row["packs_per_box"]:
                cur.execute("UPDATE products_catalog SET pieces_per_box = pieces_per_pack * packs_per_box WHERE sku = %s;", (sku,))
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
            cur.execute("SELECT * FROM products_catalog ORDER BY category NULLS LAST, sku ASC;")
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

# ----------------- مسارات الواتساب وسجل الرادار وفصل الجلسة -----------------
@app.get("/api/whatsapp/status")
async def get_whatsapp_status():
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get("http://127.0.0.1:3001/qr-status", timeout=1.5)
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "connected": bool(data.get("connected")), 
                    "phone": data.get("user"),
                    "qr": data.get("qr"),
                    "state": data.get("status") or ("connected" if data.get("connected") else "connecting")
                }
    except Exception:
        pass
    return {"connected": False, "phone": None, "qr": None, "state": "service_down"}


@app.post("/api/whatsapp/request-qr")
async def request_whatsapp_qr():
    """استئناف الربط وتوليد رمز QR جديد بعد إيقاف المحاولات التلقائية"""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post("http://127.0.0.1:3001/start", timeout=4.0)
            return resp.json()
    except Exception:
        raise HTTPException(status_code=503, detail="خدمة الواتساب لا تستجيب")


@app.get("/healthz")
def healthz():
    """فحص صحة الخدمة لـ Railway: الخادم يعمل وقاعدة البيانات تستجيب"""
    conn = get_db_connection()
    if not conn:
        return JSONResponse({"status": "degraded", "database": False}, status_code=503)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1;")
        return {"status": "ok", "database": True}
    except Exception:
        return JSONResponse({"status": "degraded", "database": False}, status_code=503)
    finally:
        conn.close()

@app.post("/api/whatsapp/disconnect")
async def disconnect_whatsapp():
    """فصل الواتساب فوراً، مسح الجلسة من السيرفر، وتصفير قاعدة البيانات"""
    try:
        async with httpx.AsyncClient() as client:
            await client.post("http://127.0.0.1:3001/disconnect", timeout=4.0)
    except Exception:
        pass

    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM whatsapp_session_snapshots WHERE session_name = 'operations_main';")
            conn.commit()
        except Exception:
            pass
        finally:
            conn.close()

    return {"status": "DISCONNECTED"}

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
