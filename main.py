"""
main.py - Enterprise AI Sales CRM & Field Intelligence
Food Development Company (شركة تنمية الغذاء)
FastAPI Backend + PostgreSQL Persistence + 2FA Google Authenticator + Official Branding
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

app = FastAPI(title="FDC Sales CRM", version="4.5.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATABASE_URL = os.getenv("DATABASE_URL", "")
FALLBACK_2FA_SECRET = "JBSWY3DPEHPK3PXP"

# ----------------- وظائف الاتصال والتهيئة لقاعدة البيانات -----------------
def init_database():
    conn = get_db_connection()
    if not conn:
        logger.warning("DATABASE_URL not found. Running in local state.")
        return

    try:
        # 1. إنشاء جدول الأمان والمصادقة الثنائية بشكل مستقل وتثبيته
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

        # 2. إنشاء وتحديث جداول النظام وإضافة الأعمدة الناقصة إن وجدت
        with conn.cursor() as cur:
            # جدول مسؤولي المبيعات
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

            # جدول حسابات العملاء
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
            # ترقية جدول customer_accounts في حال كان العمود مفقوداً
            cur.execute("""
            ALTER TABLE customer_accounts 
            ADD COLUMN IF NOT EXISTS assigned_rep_name VARCHAR(150);
            """)

            # جدول العينات
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

            # جدول التقويم الميداني
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

            # جدول سجلات الواتساب
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

        # 3. إدخال البيانات الافتراضية إذا كانت الجداول فارغة
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM sales_executives;")
            if cur.fetchone()["count"] == 0:
                cur.execute("""
                INSERT INTO sales_executives (name, employee_code, phone_number, region, monthly_target, achieved_sales, fuel_allowance_liters, fuel_liters_used, total_expenses, status)
                VALUES 
                ('أحمد الشمري', 'SE-101', '+96891112233', 'مسقط - الوسطى', 25000.0, 27200.0, 400.0, 380.0, 320.0, 'نشط'),
                ('سالم الدوسري', 'SE-102', '+96894445566', 'صحار - الباطنة', 18000.0, 13500.0, 350.0, 395.0, 410.0, 'نشط'),
                ('تركي الغامدي', 'SE-103', '+96897778899', 'صلالة - ظفار', 22000.0, 21500.0, 380.0, 360.0, 360.0, 'نشط');
                """)

            cur.execute("SELECT COUNT(*) FROM customer_accounts;")
            if cur.fetchone()["count"] == 0:
                cur.execute("""
                INSERT INTO customer_accounts (company_name, sector, contact_person, phone, assigned_rep_id, assigned_rep_name, whatsapp_group_id, tier, status)
                VALUES 
                ('سلسلة مطاعم الريف', 'مطاعم وإعاشة', 'م. فهد القرني', '+96899988771', 1, 'أحمد الشمري', '120363029182371@g.us', 'A', 'نشط'),
                ('مؤسسة التموين الحديث', 'تجارة جملة', 'أ/ طارق المنصور', '+96893322110', 2, 'سالم الدوسري', '120363088716253@g.us', 'B', 'راكد'),
                ('شركة الضيافة الفندقية العالمية', 'فنادق وخدمات', 'أ/ وائل الخالدي', '+96898822334', 3, 'تركي الغامدي', '120363077615243@g.us', 'A', 'نشط');
                """)

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
        logger.info("PostgreSQL Database synchronized successfully.")
    except Exception as e:
        logger.error(f"Error during schema migration: {e}")
        conn.rollback()
    finally:
        conn.close()
# ----------------- مسار تقديم الشعار الحقيقي من ملف logo_data.py -----------------
@app.get("/logo.png")
def get_logo():
    """فك تشفير الشعار الفعلي المعتمد للشركة من logo_data.py وتقديمه بصيغة PNG"""
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
        logger.error(f"Error loading logo from logo_data: {e}")

    raise HTTPException(status_code=404, detail="ملف الشعار logo_data.py غير موجود أو البيانات غير صالحة")

# ----------------- مسارات المصادقة الثنائية (Google Authenticator) -----------------
class Verify2FAPayload(BaseModel):
    code: str

@app.get("/api/auth/2fa/qr")
def get_2fa_qr():
    secret = None
    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT totp_secret FROM system_auth WHERE username = 'admin';")
                row = cur.fetchone()
                if not row:
                    secret = pyotp.random_base32()
                    cur.execute("INSERT INTO system_auth (username, totp_secret, is_2fa_enabled) VALUES ('admin', %s, FALSE);", (secret,))
                    conn.commit()
                else:
                    secret = row["totp_secret"]
        except Exception as e:
            logger.error(f"Error fetching 2FA secret: {e}")
        finally:
            conn.close()

    if not secret:
        secret = FALLBACK_2FA_SECRET

    totp_uri = pyotp.totp.TOTP(secret).provisioning_uri(
        name="admin@fdc.om",
        issuer_name="Food Development Co - CRM"
    )

    try:
        qr = qrcode.QRCode(box_size=6, border=2)
        qr.add_data(totp_uri)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#3A056A", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return StreamingResponse(buf, media_type="image/png")
    except Exception as e:
        logger.warning(f"Local QR generation failed ({e}), using fallback API...")
        fallback_url = f"https://api.qrserver.com/v1/create-qr-code/?size=200x200&data={totp_uri}"
        r = httpx.get(fallback_url)
        return Response(content=r.content, media_type="image/png")

@app.post("/api/auth/2fa/verify")
def verify_2fa(payload: Verify2FAPayload):
    secret = None
    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT totp_secret FROM system_auth WHERE username = 'admin';")
                row = cur.fetchone()
                if row:
                    secret = row["totp_secret"]
        except Exception as e:
            logger.error(f"Error reading 2FA secret: {e}")
        finally:
            conn.close()

    if not secret:
        secret = FALLBACK_2FA_SECRET

    totp = pyotp.TOTP(secret)
    if totp.verify(payload.code, valid_window=1):
        if conn:
            try:
                conn_up = get_db_connection()
                if conn_up:
                    with conn_up.cursor() as cur:
                        cur.execute("UPDATE system_auth SET is_2fa_enabled = TRUE WHERE username = 'admin';")
                        conn_up.commit()
                    conn_up.close()
            except Exception as e:
                logger.error(f"Error updating 2FA flag: {e}")

        return {"status": "SUCCESS", "message": "تم التحقق الأمني بنجاح"}
    else:
        raise HTTPException(status_code=401, detail="الرمز غير صحيح أو انتهت صلاحيته")

# ----------------- CSS الطباعة الصارم لتقارير A4 -----------------
PRINT_ENGINE_CSS = """
@page {
    size: A4 portrait;
    margin: 8mm 8mm 8mm 8mm;
}
@media print {
    html, body {
        width: 100% !important;
        margin: 0 !important;
        padding: 0 !important;
        background: #FFFFFF !important;
        color: #0F172A !important;
        -webkit-print-color-adjust: exact !important;
        print-color-adjust: exact !important;
    }
    .no-print { display: none !important; }
    .print-container {
        width: 100% !important;
        max-width: 100% !important;
        padding: 0 !important;
        margin: 0 !important;
        box-shadow: none !important;
        border: none !important;
    }
}
* { box-sizing: border-box; }
:root {
    --brand: #3A056A;
    --accent: #C194FB;
    --tint: #F5F0FC;
    --line: #E2E8F0;
    --text: #0F172A;
    --ok: #166534;
    --warn: #854D0E;
    --bad: #991B1B;
}
body {
    direction: rtl;
    font-family: 'Cairo', 'Tajawal', sans-serif;
    color: var(--text);
    margin: 0;
    padding: 16px;
    font-size: 8.5pt;
    background: #F8FAFC;
    line-height: 1.35;
}
.print-container {
    width: 100%;
    max-width: 194mm;
    margin: 0 auto;
    background: #FFFFFF;
    padding: 16px;
    border-radius: 8px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.06);
}
.kpi-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 8px;
    margin-bottom: 12px;
}
.kpi-card {
    background: #FAF7FD;
    border: 1px solid var(--line);
    border-radius: 6px;
    padding: 8px 6px;
    text-align: center;
}
.kpi-lbl { font-size: 7.5pt; color: #64748B; font-weight: 700; }
.kpi-val { font-size: 11.5pt; font-weight: 900; color: var(--brand); margin-top: 3px; }
table.data-table {
    width: 100%;
    border-collapse: collapse;
    margin-bottom: 12px;
    border: 1px solid var(--line);
    table-layout: fixed;
}
table.data-table th {
    background: var(--brand);
    color: #FFFFFF;
    text-align: right;
    padding: 6px 8px;
    font-size: 8pt;
    font-weight: 800;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
table.data-table td {
    padding: 5px 8px;
    border-bottom: 1px solid var(--line);
    font-size: 8pt;
    word-break: break-word;
}
table.data-table tr:nth-child(even) { background: #FAFAFC; }
.badge {
    display: inline-block;
    padding: 1.5px 6px;
    border-radius: 4px;
    font-weight: 800;
    font-size: 7pt;
    white-space: nowrap;
}
.badge-ok { background: #DCFCE7; color: var(--ok); }
.badge-warn { background: #FEF9C3; color: var(--warn); }
.badge-bad { background: #FEE2E2; color: var(--bad); }
.editable-box {
    background: #FAF7FD;
    border: 1px dashed var(--accent);
    border-radius: 6px;
    padding: 8px 12px;
    margin-top: 8px;
    outline: none;
    line-height: 1.5;
    font-size: 8.5pt;
}
.editable-box:focus { border-style: solid; background: #FFFFFF; }
"""

# ----------------- نماذج Pydantic للطلبات -----------------
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

# ----------------- مسارات البيانات والعمليات -----------------
@app.get("/health")
def health():
    return {"status": "UP", "timestamp": datetime.now().isoformat(), "db_configured": bool(DATABASE_URL)}

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

@app.get("/api/whatsapp/status")
def get_whatsapp_status():
    return {"status": "QR_READY", "phone_connected": None, "last_sync": datetime.now().strftime("%Y-%m-%d %H:%M")}

@app.get("/api/whatsapp/qr")
def get_whatsapp_qr():
    session_id = str(uuid.uuid4())[:8]
    return {
        "qr_image_url": f"https://api.qrserver.com/v1/create-qr-code/?size=250x250&data=FDC-WHATSAPP-SESSION-{session_id}",
        "session_id": session_id,
        "status": "QR_READY"
    }

# ----------------- مسار معاينة وطباعة التقرير بالريال العماني -----------------
@app.post("/api/reports/preview")
def preview_report(req: dict):
    recipient = req.get("report_recipient", "سعادة رئيس مجلس الإدارة / المدير العام")
    recommendation = req.get("recommendation", "أظهر الفريق التزاماً استثنائياً في منطقة مسقط بنسبة إنجاز 108.8% مع كفاءة في استهلاك الوقود. يُوصى بمساندة مسار صحار لرفع معدل التحويل وتكثيف توريد العينات للقطاع الفندقي.")

    reps = get_reps()
    samples = get_samples()

    total_sales = sum(r["achieved_sales"] for r in reps)
    total_exp = sum(r["total_expenses"] for r in reps)

    html = f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="utf-8">
    <title>&nbsp;</title>
    <link href="https://fonts.googleapis.com/css2?family=Cairo:wght@400;600;700;800;900&display=swap" rel="stylesheet">
    <style>{PRINT_ENGINE_CSS}</style>
</head>
<body>
    <div class="no-print" style="width: 100%; max-width: 194mm; margin: 0 auto 12px auto; background: #3A056A; color: #FFFFFF; padding: 10px 16px; border-radius: 6px; display: flex; justify-content: space-between; align-items: center; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">
        <div style="font-weight: 700; font-size: 9.5pt;">معاينة المستند الرسمي | شركة تنمية الغذاء</div>
        <button onclick="window.print()" style="background: #C194FB; color: #3A056A; border: none; font-weight: 800; padding: 6px 16px; border-radius: 5px; cursor: pointer; font-family: Cairo; font-size: 8.5pt;">طباعة المستند الرسمي (A4) 🖨️</button>
    </div>

    <div class="print-container">
        <table style="width: 100%; border-bottom: 2px solid #3A056A; padding-bottom: 8px; margin-bottom: 12px; border-collapse: collapse;">
            <tr>
                <td style="text-align: right; vertical-align: middle;">
                    <h1 style="margin: 0 0 3px 0; color: #3A056A; font-size: 15pt; font-weight: 900; line-height: 1.2;">التقرير التنفيذي الشامل للمبيعات والعمليات</h1>
                    <div style="color: #64748B; font-size: 8pt; font-weight: 600;">الفترة: الربع الثالث 2026 &nbsp;|&nbsp; توجيه المستند: {recipient}</div>
                </td>
                <td style="text-align: left; vertical-align: middle; width: 220px; height: 60px;">
                    <img src="/logo.png" alt="شركة تنمية الغذاء" style="max-width: 100%; max-height: 55px; width: auto; height: auto; object-fit: contain; display: block; border: none;" />
                </td>
            </tr>
        </table>

        <div class="kpi-grid">
            <div class="kpi-card">
                <div class="kpi-lbl">إجمالي المبيعات المحققة</div>
                <div class="kpi-val">{total_sales:,.1f} ر.ع</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-lbl">العينات المعتمدة تجارياً</div>
                <div class="kpi-val" style="color: var(--ok);">66.7%</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-lbl">المصاريف التشغيلية الكلية</div>
                <div class="kpi-val" style="color: #7E22CE;">{total_exp:,.1f} ر.ع</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-lbl">نسبة كفاءة التكلفة للبيع</div>
                <div class="kpi-val">{(total_exp/total_sales*100 if total_sales > 0 else 0):.2f}%</div>
            </div>
        </div>

        <div style="font-weight: 800; color: var(--brand); margin: 10px 0 6px 0; font-size: 9pt;">جدول إنجازات فريق المبيعات التنفيذي:</div>
        <table class="data-table">
            <thead>
                <tr>
                    <th style="width: 16%;">المسؤول</th>
                    <th style="width: 14%;">المنطقة</th>
                    <th style="width: 13%;">المستهدف</th>
                    <th style="width: 13%;">المحقق</th>
                    <th style="width: 10%;">الإنجاز</th>
                    <th style="width: 14%;">الوقود (فعلي/متاح)</th>
                    <th style="width: 10%;">المصاريف</th>
                    <th style="width: 10%;">الكفاءة</th>
                </tr>
            </thead>
            <tbody>
                {''.join([f'''
                <tr>
                    <td><strong>{r["name"]}</strong></td>
                    <td>{r["region"]}</td>
                    <td>{r["monthly_target"]:,.1f} ر.ع</td>
                    <td style="font-weight: 800; color: var(--ok);">{r["achieved_sales"]:,.1f} ر.ع</td>
                    <td style="font-weight: 800;">{(r["achievement_rate"]):.1f}%</td>
                    <td>{r["fuel_liters"]} / {r["fuel_allowance_liters"]} لتر</td>
                    <td>{r["total_expenses"]:,.1f} ر.ع</td>
                    <td><span class="badge badge-ok">{r["efficiency"]}</span></td>
                </tr>
                ''' for r in reps])}
            </tbody>
        </table>

        <div style="font-weight: 800; color: var(--brand); margin: 10px 0 6px 0; font-size: 9pt;">سجل حركة العينات وتحويلها لأوامر شراء (Sample ROI):</div>
        <table class="data-table">
            <thead>
                <tr>
                    <th style="width: 25%;">العميل</th>
                    <th style="width: 25%;">المنتج</th>
                    <th style="width: 10%;">الكمية</th>
                    <th style="width: 13%;">تاريخ التسليم</th>
                    <th style="width: 12%;">القرار</th>
                    <th style="width: 15%;">أمر الشراء (PO)</th>
                </tr>
            </thead>
            <tbody>
                {''.join([f'''
                <tr>
                    <td><strong>{s["customer_name"]}</strong></td>
                    <td>{s["product_name"]}</td>
                    <td>{s["qty_free"]} وحدة</td>
                    <td>{s["delivery_date"]}</td>
                    <td><span class="badge {'badge-ok' if s['status']=='APPROVED' else 'badge-warn'}">{s['status']}</span></td>
                    <td style="font-family: monospace; font-weight: 800;">{s["converted_po_id"] or '—'}</td>
                </tr>
                ''' for s in samples])}
            </tbody>
        </table>

        <div style="margin-top: 10px;">
            <div style="font-weight: 800; color: var(--brand); font-size: 8.5pt; margin-bottom: 2px;">التوصية الإدارية والتنفيذية (قابلة للتحرير قبل الطباعة):</div>
            <div class="editable-box" contenteditable="true" title="اضغط هنا لتعديل نص التوصية مباشرة">{recommendation}</div>
        </div>

        <div style="margin-top: 14px; padding-top: 8px; border-top: 1px solid var(--line); display: flex; justify-content: space-between; font-size: 7.5pt; color: #64748B;">
            <div>وثيقة رسمية صادرة عن: نظام إدارة المبيعات الميدانية والتنفيذية الذكي</div>
            <div>اعتماد الإدارة العامة: ___________________</div>
        </div>
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
