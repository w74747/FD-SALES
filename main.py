"""
main.py - Enterprise AI Sales CRM & Field Intelligence
Food Development Company (شركة تنمية الغذاء)
FastAPI Backend + PostgreSQL Persistence + Hardened 2FA Security + Integrated Baileys Engine
"""

import os
import io
import json
import uuid
import base64
import logging
from datetime import datetime
from typing import Optional, List
from fastapi import FastAPI, HTTPException, Response, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel
import psycopg2
from psycopg2.extras import RealDictCursor
import pyotp
import qrcode
import httpx

# استيراد الشعار الأصلي المحفوظ كسلسلة Base64
try:
    from logo_data import LOGO_BASE64
except ImportError:
    LOGO_BASE64 = ""

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("SalesCRM")

app = FastAPI(title="FDC Sales CRM", version="6.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATABASE_URL = (
    os.getenv("DATABASE_URL") 
    or os.getenv("DATABASE_PUBLIC_URL") 
    or os.getenv("POSTGRES_URL") 
    or ""
)
FALLBACK_2FA_SECRET = "JBSWY3DPEHPK3PXP"

# ----------------- وظائف الاتصال والتهيئة لقاعدة البيانات -----------------
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

def init_database():
    conn = get_db_connection()
    if not conn:
        logger.warning("DATABASE_URL not found. Running in local state.")
        return

    # 1. إنشاء وتثبيت جدول الأمان
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
        conn.commit()
    except Exception as e:
        logger.error(f"Error initializing system_auth: {e}")
        conn.rollback()

    # 2. ترقية وإنشاء جداول النظام
    try:
        with conn.cursor() as cur:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS sales_executives (
                id SERIAL PRIMARY KEY,
                name VARCHAR(150) NOT NULL,
                employee_code VARCHAR(50) UNIQUE NOT NULL,
                phone_number VARCHAR(30) UNIQUE NOT NULL,
                region VARCHAR(100) NOT NULL,
                monthly_target NUMERIC(12, 2) DEFAULT 0.00,
                achieved_sales NUMERIC(12, 2) DEFAULT 0.00,
                fuel_allowance_liters NUMERIC(8, 2) DEFAULT 0.00,
                fuel_liters_used NUMERIC(8, 2) DEFAULT 0.00,
                total_expenses NUMERIC(12, 2) DEFAULT 0.00,
                status VARCHAR(20) DEFAULT 'نشط'
            );
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS customer_accounts (
                id SERIAL PRIMARY KEY,
                company_name VARCHAR(200) NOT NULL,
                sector VARCHAR(100) NOT NULL,
                contact_person VARCHAR(150) NOT NULL,
                phone VARCHAR(30) NOT NULL,
                assigned_rep_id INT REFERENCES sales_executives(id) ON DELETE SET NULL,
                assigned_rep_name VARCHAR(150),
                whatsapp_group_id VARCHAR(100) UNIQUE,
                tier VARCHAR(10) DEFAULT 'B',
                status VARCHAR(20) DEFAULT 'نشط'
            );
            """)
            cur.execute("""
            ALTER TABLE customer_accounts 
            ADD COLUMN IF NOT EXISTS assigned_rep_name VARCHAR(150);
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
        conn.commit()
    except Exception as e:
        logger.error(f"Error updating system tables: {e}")
        conn.rollback()

    # 3. إدخال البيانات التأسيسية بأمان ودون خرق المفتاح الأجنبي
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM sales_executives;")
            if cur.fetchone()["count"] == 0:
                cur.execute("""
                INSERT INTO sales_executives (name, employee_code, phone_number, region, monthly_target, achieved_sales, fuel_allowance_liters, fuel_liters_used, total_expenses, status)
                VALUES 
                ('أحمد الشمري', 'SE-101', '+96891112233', 'مسقط - الوسطى', 25000.0, 27200.0, 400.0, 380.0, 320.0, 'نشط'),
                ('سالم الدوسري', 'SE-102', '+96894445566', 'صحار - الباطنة', 18000.0, 13500.0, 350.0, 395.0, 410.0, 'نشط'),
                ('تركي الغامدي', 'SE-103', '+96897778899', 'صلالة - ظفار', 22000.0, 21500.0, 380.0, 360.0, 360.0, 'نشط')
                RETURNING id;
                """)
                rep_ids = [r["id"] for r in cur.fetchall()]
            else:
                cur.execute("SELECT id FROM sales_executives ORDER BY id ASC LIMIT 3;")
                rep_ids = [r["id"] for r in cur.fetchall()]

            cur.execute("SELECT COUNT(*) FROM customer_accounts;")
            if cur.fetchone()["count"] == 0 and rep_ids:
                r1 = rep_ids[0]
                r2 = rep_ids[1] if len(rep_ids) > 1 else r1
                r3 = rep_ids[2] if len(rep_ids) > 2 else r1

                cur.execute("""
                INSERT INTO customer_accounts (company_name, sector, contact_person, phone, assigned_rep_id, assigned_rep_name, whatsapp_group_id, tier, status)
                VALUES 
                ('سلسلة مطاعم الريف', 'مطاعم وإعاشة', 'م. فهد القرني', '+96899988771', %s, 'أحمد الشمري', '120363029182371@g.us', 'A', 'نشط'),
                ('مؤسسة التموين الحديث', 'تجارة جملة', 'أ/ طارق المنصور', '+96893322110', %s, 'سالم الدوسري', '120363088716253@g.us', 'B', 'راكد'),
                ('شركة الضيافة الفندقية العالمية', 'فنادق وخدمات', 'أ/ وائل الخالدي', '+96898822334', %s, 'تركي الغامدي', '120363077615243@g.us', 'A', 'نشط');
                """, (r1, r2, r3))

            cur.execute("SELECT COUNT(*) FROM sample_deliveries;")
            if cur.fetchone()["count"] == 0:
                cur.execute("""
                INSERT INTO sample_deliveries (customer_name, rep_name, product_name, qty_free, delivery_date, status, converted_po_id, po_value, source)
                VALUES 
                ('سلسلة مطاعم الريف', 'أحمد الشمري', 'صدور دجاج متبلة (خلطة 4B)', 15, '2026-08-25', 'APPROVED', 'PO-2026-889', 7800.0, 'WhatsApp Sentinel'),
                ('مؤسسة التموين الحديث', 'سالم الدوسري', 'دجاج مجمد فائق الجودة 1000g', 20, '2026-08-12', 'PENDING', NULL, 0.0, 'إدخال يدوي'),
                ('شركة الضيافة الفندقية العالمية', 'تركي الغامدي', 'شاورما دجاج جاهزة للطهي', 25, '2026-08-28', 'APPROVED', 'PO-2026-904', 11500.0, 'WhatsApp Sentinel');
                """)

            cur.execute("SELECT COUNT(*) FROM calendar_events;")
            if cur.fetchone()["count"] == 0:
                cur.execute("""
                INSERT INTO calendar_events (customer_name, rep_name, task_type, scheduled_at, location, route_code, execution_status)
                VALUES 
                ('سلسلة مطاعم الريف', 'أحمد الشمري', 'توقيع عقد توريد سنوي', '2026-09-03 10:00', 'الإدارة العامة - مسقط', 'R-10', 'DONE'),
                ('مؤسسة التموين الحديث', 'سالم الدوسري', 'زيارة تقصي واسترجاع عينات', '2026-09-04 13:00', 'مستودعات صحار', 'R-14', 'PENDING');
                """)

            cur.execute("SELECT COUNT(*) FROM whatsapp_logs;")
            if cur.fetchone()["count"] == 0:
                cur.execute("""
                INSERT INTO whatsapp_logs (created_at, sender_name, is_external_call, message_body)
                VALUES 
                ('09:15', 'م. فهد القرني', FALSE, 'السلام عليكم، نريد تجربة عينة صدور دجاج جديدة لفرع مسقط.'),
                ('09:22', 'أحمد الشمري', FALSE, 'أهلاً بك، تم إرسال 15 كرتون عينة للتجربة الميدانية.'),
                ('11:45', 'أحمد الشمري', TRUE, 'تم إجراء مكالمة مع مدير المشتريات وتأكيد استلام المواصفات القياسية.');
                """)
        conn.commit()
        logger.info("Database schema synchronized and default data verified.")
    except Exception as e:
        logger.error(f"Error seeding data: {e}")
        conn.rollback()
    finally:
        conn.close()

@app.on_event("startup")
def startup_event():
    init_database()

# ----------------- مسار تقديم الشعار المعتمد للشركة -----------------
@app.get("/logo.png")
def get_logo():
    try:
        from logo_data import LOGO_BASE64
        if LOGO_BASE64:
            clean_b64 = LOGO_BASE64.split(",")[-1].strip()
            image_bytes = base64.b64decode(clean_b64)
            return Response(
                content=image_bytes,
                media_type="image/png",
                headers={"Cache-Control": "public, max-age=86400"}
            )
    except Exception as e:
        logger.error(f"Error loading logo: {e}")
    raise HTTPException(status_code=404, detail="Logo not found")

# ----------------- مسارات المصادقة الثنائية 2FA -----------------
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
            cur.execute("SELECT totp_secret, is_2fa_enabled FROM system_auth WHERE username = 'admin';")
            row = cur.fetchone()

            if row and row["is_2fa_enabled"]:
                raise HTTPException(
                    status_code=403, 
                    detail="تم تفعيل التحقق الثنائي مسبقاً. تم قفل إعادة توليد الرمز لأسباب أمنية."
                )

            if not row:
                secret = pyotp.random_base32()
                cur.execute("INSERT INTO system_auth (username, totp_secret, is_2fa_enabled) VALUES ('admin', %s, FALSE);", (secret,))
                conn.commit()
            else:
                secret = row["totp_secret"]

        totp_uri = pyotp.totp.TOTP(secret).provisioning_uri(
            name="admin@fdc.om",
            issuer_name="Food Development Co - CRM"
        )

        qr = qrcode.QRCode(box_size=6, border=2)
        qr.add_data(totp_uri)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#3A056A", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return StreamingResponse(buf, media_type="image/png")
    except HTTPException:
        raise
    except Exception as e:
        fallback_url = f"https://api.qrserver.com/v1/create-qr-code/?size=200x200&data={totp_uri}"
        r = httpx.get(fallback_url)
        return Response(content=r.content, media_type="image/png")
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
            if not row:
                raise HTTPException(status_code=400, detail="المفتاح السري غير مهيأ")
            secret = row["totp_secret"]

        totp = pyotp.TOTP(secret)
        if totp.verify(payload.code, valid_window=1):
            with conn.cursor() as cur:
                cur.execute("UPDATE system_auth SET is_2fa_enabled = TRUE WHERE username = 'admin';")
            conn.commit()
            return {"status": "SUCCESS", "message": "تم التحقق الأمني بنجاح"}
        else:
            raise HTTPException(status_code=401, detail="الرمز غير صحيح أو انتهت صلاحيته")
    finally:
        conn.close()

# ----------------- نماذج Pydantic للبيانات -----------------
class NewRepPayload(BaseModel):
    name: str
    employee_code: str
    phone_number: str
    region: str
    monthly_target: float = 0.0
    fuel_allowance_liters: float = 0.0

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
    route_code: str = "R-01"

class NewSaleTransactionPayload(BaseModel):
    customer_id: int
    sale_amount: float
    primary_rep_id: int
    secondary_rep_id: Optional[int] = None
    split_percentage: Optional[float] = 50.0
    expense_fuel: Optional[float] = 0.0
    expense_other: Optional[float] = 0.0

class IncomingWhatsAppMessage(BaseModel):
    chat_id: str
    sender_phone: str
    sender_name: str
    message_text: str

# ----------------- مسارات البيانات والعمليات -----------------
@app.get("/health")
def health():
    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM sales_executives;")
                reps_count = cur.fetchone()["count"]
            conn.close()
            return {
                "status": "UP",
                "database": "CONNECTED_SUCCESSFULLY",
                "sales_executives_count": reps_count,
                "db_configured": True
            }
        except Exception as e:
            return {"status": "ERROR", "database": "QUERY_FAILED", "error": str(e), "db_configured": True}
    return {
        "status": "OFFLINE",
        "database": "FAILED_TO_CONNECT",
        "db_configured": bool(DATABASE_URL)
    }

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
                target = float(r["monthly_target"] or 0)
                sales = float(r["achieved_sales"] or 0)
                used_fuel = float(r["fuel_liters_used"] or 0)
                allow_fuel = float(r["fuel_allowance_liters"] or 0)
                rate = (sales / target * 100) if target > 0 else 0
                eff = "عالي الكفاءة" if rate >= 100 and used_fuel <= allow_fuel else ("مقبول" if rate >= 80 else "هدر موارد")
                enriched.append({
                    "id": r["id"],
                    "name": r["name"],
                    "employee_code": r["employee_code"],
                    "phone_number": r["phone_number"],
                    "region": r["region"],
                    "monthly_target": target,
                    "achieved_sales": sales,
                    "fuel_allowance_liters": allow_fuel,
                    "fuel_liters": used_fuel,
                    "total_expenses": float(r["total_expenses"] or 0),
                    "status": r["status"],
                    "achievement_rate": rate,
                    "efficiency": eff
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
            cur.execute("""
            INSERT INTO sales_executives (name, employee_code, phone_number, region, monthly_target, fuel_allowance_liters, achieved_sales, total_expenses, status)
            VALUES (%s, %s, %s, %s, %s, %s, 0.0, 0.0, 'نشط') RETURNING id;
            """, (payload.name, payload.employee_code, payload.phone_number, payload.region, payload.monthly_target, payload.fuel_allowance_liters))
            new_id = cur.fetchone()["id"]
            conn.commit()
            return {"status": "SUCCESS", "id": new_id, "message": "تمت إضافة المندوب واعتماد رقم هاتفه"}
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
            return cur.fetchall()
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
                r["po_value"] = float(r["po_value"] or 0)
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
            VALUES (%s, %s, %s, %s, %s, 'PENDING', 0.0, 'إدخال يدوي') RETURNING id;
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
            """, (payload.customer_name, payload.rep_name, payload.task_type, payload.scheduled_at, payload.location, payload.route_code))
            new_id = cur.fetchone()["id"]
            conn.commit()
            return {"status": "SUCCESS", "id": new_id}
    finally:
        conn.close()

@app.post("/api/transactions/sale")
def record_sale(payload: NewSaleTransactionPayload):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not reachable")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM sales_executives WHERE id = %s;", (payload.primary_rep_id,))
            p_rep = cur.fetchone()
            if not p_rep:
                raise HTTPException(status_code=404, detail="مسؤول المبيعات غير موجود")

            total_exp_added = (payload.expense_fuel or 0.0) + (payload.expense_other or 0.0)

            if payload.secondary_rep_id:
                cur.execute("SELECT * FROM sales_executives WHERE id = %s;", (payload.secondary_rep_id,))
                s_rep = cur.fetchone()
                if s_rep:
                    ratio = (payload.split_percentage or 50.0) / 100.0
                    p_add = payload.sale_amount * (1 - ratio)
                    s_add = payload.sale_amount * ratio
                    cur.execute("UPDATE sales_executives SET achieved_sales = achieved_sales + %s, total_expenses = total_expenses + %s WHERE id = %s;",
                                (p_add, total_exp_added, payload.primary_rep_id))
                    cur.execute("UPDATE sales_executives SET achieved_sales = achieved_sales + %s WHERE id = %s;",
                                (s_add, payload.secondary_rep_id))
            else:
                cur.execute("UPDATE sales_executives SET achieved_sales = achieved_sales + %s, total_expenses = total_expenses + %s WHERE id = %s;",
                            (payload.sale_amount, total_exp_added, payload.primary_rep_id))

            conn.commit()
            return {"status": "SUCCESS", "message": "تم حفظ العملية وتحديث رصيد الإنجاز في قاعدة البيانات"}
    finally:
        conn.close()

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

# ----------------- مسار توليد وجلب QR الواتساب الحقيقي المشفر -----------------
@app.get("/api/whatsapp/qr")
async def get_whatsapp_qr():
    """جلب رمز QR الحقيقي المولد بواسطة محرك Baileys الداخلي المشفر"""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get("http://127.0.0.1:3001/qr-status", timeout=2.5)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("connected"):
                    return Response(status_code=204)
                
                qr_base64 = data.get("qr")
                if qr_base64:
                    clean_b64 = qr_base64.split(",")[-1].strip()
                    image_bytes = base64.b64decode(clean_b64)
                    return Response(
                        content=image_bytes, 
                        media_type="image/png",
                        headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
                    )
    except Exception as e:
        logger.warning(f"Connecting to internal Baileys service: {e}")

    # صورة انتظار مؤقتة واضحة في حال كان محرك Baileys قيد الإقلاع
    qr = qrcode.QRCode(box_size=6, border=2)
    qr.add_data("WHATSAPP-ENGINE-STARTING-PLEASE-WAIT")
    qr.make(fit=True)
    img = qr.make_image(fill_color="#3A056A", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")

# ----------------- خطاف الويب الفعلي مع فلترة القائمة البيضاء الصارمة -----------------
@app.post("/api/whatsapp/webhook")
def handle_whatsapp_webhook(msg: IncomingWhatsAppMessage):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not reachable")
    try:
        with conn.cursor() as cur:
            # 1. التحقق: هل المحادثة تخص مجموعة عميل معتمدة في النظام؟
            cur.execute(
                "SELECT id, company_name FROM customer_accounts WHERE whatsapp_group_id = %s;",
                (msg.chat_id,)
            )
            customer = cur.fetchone()

            # 2. التحقق: هل المرسل مسؤول مبيعات معتمد لدينا؟
            cur.execute(
                "SELECT id, name FROM sales_executives WHERE phone_number = %s;",
                (msg.sender_phone,)
            )
            rep = cur.fetchone()

            # جدار الحماية: استبعاد وتجاهل أي رسالة خارج نطاق المجموعات أو أرقام المبيعات
            if not customer and not rep:
                return {"status": "IGNORED", "reason": "خارج نطاق المجموعات أو الأرقام المعتمدة"}

            cur.execute("""
            INSERT INTO whatsapp_logs (created_at, sender_name, is_external_call, message_body)
            VALUES (%s, %s, FALSE, %s);
            """, (
                datetime.now().strftime("%H:%M"),
                msg.sender_name,
                msg.message_text
            ))
            conn.commit()
            return {"status": "PROCESSED", "target": customer["company_name"] if customer else rep["name"]}
    finally:
        conn.close()

@app.post("/api/reports/preview")
def preview_report(req: dict):
    recipient = req.get("report_recipient", "سعادة رئيس مجلس الإدارة / المدير العام")
    recommendation = req.get("recommendation", "أظهر الفريق التزاماً استثنائياً في منطقة مسقط بنسبة إنجاز 108.8% مع كفاءة في استهلاك الوقود. يُوصى بمساندة مسار صحار لرفع معدل التحويل وتكثيف توريد العينات للقطاع الفندقي.")

    reps = get_reps()
    total_sales = sum(r["achieved_sales"] for r in reps)
    total_exp = sum(r["total_expenses"] for r in reps)

    html = f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="utf-8">
    <title>&nbsp;</title>
    <link href="https://fonts.googleapis.com/css2?family=Cairo:wght@400;600;700;800;900&display=swap" rel="stylesheet">
    <style>
        body {{ font-family: 'Cairo', sans-serif; direction: rtl; padding: 20px; font-size: 9pt; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
        th, td {{ border: 1px solid #E2E8F0; padding: 6px; text-align: right; }}
        th {{ background: #3A056A; color: white; }}
    </style>
</head>
<body>
    <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 2px solid #3A056A; padding-bottom: 10px;">
        <div>
            <h1 style="color: #3A056A; margin: 0; font-size: 16pt;">التقرير التنفيذي الشامل للمبيعات والعمليات</h1>
            <div style="color: #64748B; font-size: 8.5pt;">شركة تنمية الغذاء | توجيه: {recipient}</div>
        </div>
        <img src="/logo.png" style="max-height: 55px; width: auto;" />
    </div>
    <h3>إنجازات مسؤولي المبيعات:</h3>
    <table>
        <thead><tr><th>المسؤول</th><th>المنطقة</th><th>المستهدف</th><th>المحقق</th><th>المصاريف</th></tr></thead>
        <tbody>
            {''.join([f"<tr><td>{r['name']}</td><td>{r['region']}</td><td>{r['monthly_target']:,.1f} ر.ع</td><td>{r['achieved_sales']:,.1f} ر.ع</td><td>{r['total_expenses']:,.1f} ر.ع</td></tr>" for r in reps])}
        </tbody>
    </table>
    <div style="margin-top: 15px; padding: 10px; background: #F5F0FC; border: 1px dashed #C194FB; border-radius: 6px;">
        <strong>التوصية التنفيذية:</strong> {recommendation}
    </div>
</body>
</html>
"""
    return HTMLResponse(content=html)

@app.get("/", response_class=HTMLResponse)
def serve_dashboard():
    dashboard_path = os.path.join(os.path.dirname(__file__), "dashboard.html")
    if os.path.exists(dashboard_path):
        with open(dashboard_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>dashboard.html not found</h1>"

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
