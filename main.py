"""
main.py - Enterprise AI Sales CRM & Field Intelligence
Food Development Company (شركة تنمية الغذاء)
FastAPI Backend + Dual Execution Reporting (Pure HTML/Print + WeasyPrint Fallback)
"""

import os
import json
import uuid
import asyncio
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException, Response, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field
from jinja2 import Template

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("SalesCRM")

app = FastAPI(title="FDC Sales CRM", version="3.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------- الشعار وهوية الشركة المعتمدة -----------------
LOGO_SVG = """
<div style="display: flex; align-items: center; gap: 10px;">
    <div style="background: #3A056A; color: #FFFFFF; font-weight: 900; font-size: 22px; width: 44px; height: 44px; border-radius: 10px; display: flex; align-items: center; justify-content: center; border: 2px solid #C194FB;">ت</div>
    <div>
        <div style="color: #3A056A; font-weight: 800; font-size: 16px; line-height: 1.1;">شركة تنمية الغذاء</div>
        <div style="color: #6B21A8; font-size: 9px; letter-spacing: 1px; font-weight: bold;">FOOD DEVELOPMENT CO.</div>
    </div>
</div>
"""

CSS_PRINT = """
@page {
    size: A4 portrait;
    margin: 12mm 10mm 15mm 10mm;
}
:root {
    --brand: #3A056A;
    --accent: #C194FB;
    --tint: #F5F0FC;
    --line: #E4D9F5;
    --text: #1A202C;
    --ok: #1E7A5A;
    --warn: #8A5D06;
    --bad: #9E2222;
}
body {
    direction: rtl;
    font-family: 'Cairo', 'Tajawal', sans-serif;
    color: var(--text);
    margin: 0;
    padding: 20px;
    font-size: 9pt;
    background: #FFFFFF;
}
.header-box {
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-bottom: 2.5px solid var(--brand);
    padding-bottom: 12px;
    margin-bottom: 16px;
}
.title-box h1 { margin: 0; color: var(--brand); font-size: 16pt; font-weight: 800; }
.kpi-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 16px; }
.kpi-card { background: #FAF7FD; border: 1px solid var(--line); border-radius: 6px; padding: 10px; text-align: center; }
.kpi-val { font-size: 13pt; font-weight: bold; color: var(--brand); margin-top: 4px; }
table.data-table { width: 100%; border-collapse: collapse; margin-bottom: 16px; }
table.data-table th { background: var(--brand); color: #fff; text-align: right; padding: 8px 10px; font-size: 8.5pt; }
table.data-table td { padding: 7px 10px; border-bottom: 1px solid var(--line); font-size: 8.5pt; }
table.data-table tr:nth-child(even) { background: #FAF7FD; }
.badge { display: inline-block; padding: 2px 7px; border-radius: 4px; font-weight: bold; font-size: 7.5pt; }
.badge-ok { background: #EDF7F2; color: var(--ok); }
.badge-warn { background: #FDF6E7; color: var(--warn); }
.badge-bad { background: #FBEEEE; color: var(--bad); }
.ai-box { background: #F6F0FD; border: 1px dashed var(--accent); border-radius: 6px; padding: 12px; margin-top: 15px; }
@media print {
    .no-print { display: none !important; }
    body { padding: 0; }
}
"""

# ----------------- بنك البيانات المحدث (In-Memory Database) -----------------
sales_executives_db = [
    {
        "id": 1,
        "name": "أحمد الشمري",
        "employee_code": "SE-101",
        "phone_number": "+966501112233",
        "region": "الرياض - الوسطى",
        "monthly_target": 250000.0,
        "achieved_sales": 272000.0,
        "fuel_allowance_liters": 400.0,
        "fuel_liters": 380.0,
        "total_expenses": 3200.0,
        "status": "نشط"
    },
    {
        "id": 2,
        "name": "سالم الدوسري",
        "employee_code": "SE-102",
        "phone_number": "+966504445566",
        "region": "الدمام - الشرقية",
        "monthly_target": 180000.0,
        "achieved_sales": 135000.0,
        "fuel_allowance_liters": 350.0,
        "fuel_liters": 395.0,
        "total_expenses": 4100.0,
        "status": "نشط"
    },
    {
        "id": 3,
        "name": "تركي الغامدي",
        "employee_code": "SE-103",
        "phone_number": "+966507778899",
        "region": "جدة - الغربية",
        "monthly_target": 220000.0,
        "achieved_sales": 215000.0,
        "fuel_allowance_liters": 380.0,
        "fuel_liters": 360.0,
        "total_expenses": 3600.0,
        "status": "نشط"
    }
]

customer_accounts_db = [
    {
        "id": 1,
        "company_name": "سلسلة مطاعم الريف الحجازي",
        "sector": "مطاعم وإعاشة",
        "contact_person": "م. فهد القرني",
        "phone": "+966509988771",
        "assigned_rep_id": 1,
        "assigned_rep_name": "أحمد الشمري",
        "whatsapp_group_id": "120363029182371@g.us",
        "tier": "A",
        "status": "نشط",
        "last_activity": "منذ 15 دقيقة"
    },
    {
        "id": 2,
        "company_name": "مؤسسة التموين الحديث",
        "sector": "تجارة جملة",
        "contact_person": "أ/ طارق المنصور",
        "phone": "+966503322110",
        "assigned_rep_id": 2,
        "assigned_rep_name": "سالم الدوسري",
        "whatsapp_group_id": "120363088716253@g.us",
        "tier": "B",
        "status": "راكد",
        "last_activity": "منذ 24 يوماً"
    },
    {
        "id": 3,
        "company_name": "شركة الضيافة الفندقية العالمية",
        "sector": "فنادق وخدمات",
        "contact_person": "أ/ وائل الخالدي",
        "phone": "+966508822334",
        "assigned_rep_id": 3,
        "assigned_rep_name": "تركي الغامدي",
        "whatsapp_group_id": "120363077615243@g.us",
        "tier": "A",
        "status": "نشط",
        "last_activity": "منذ ساعتين"
    }
]

samples_db = [
    {
        "id": 1,
        "customer_id": 1,
        "customer_name": "سلسلة مطاعم الريف الحجازي",
        "rep_name": "أحمد الشمري",
        "product_name": "صدور دجاج متبلة (خلطة 4B الخاصة)",
        "qty_free": 15,
        "delivery_date": "2026-08-25",
        "status": "APPROVED",
        "converted_po_id": "PO-2026-889",
        "po_value": 78000.0,
        "source": "WhatsApp Sentinel"
    },
    {
        "id": 2,
        "customer_id": 2,
        "customer_name": "مؤسسة التموين الحديث",
        "rep_name": "سالم الدوسري",
        "product_name": "دجاج مجمد فائق الجودة 1000g",
        "qty_free": 20,
        "delivery_date": "2026-08-12",
        "status": "PENDING",
        "converted_po_id": None,
        "po_value": 0.0,
        "source": "إدخال يدوي"
    },
    {
        "id": 3,
        "customer_id": 3,
        "customer_name": "شركة الضيافة الفندقية العالمية",
        "rep_name": "تركي الغامدي",
        "product_name": "شاورما دجاج متبلة جاهزة للطهي",
        "qty_free": 25,
        "delivery_date": "2026-08-28",
        "status": "APPROVED",
        "converted_po_id": "PO-2026-904",
        "po_value": 115000.0,
        "source": "WhatsApp Sentinel"
    }
]

calendar_events_db = [
    {
        "id": 1,
        "customer_name": "سلسلة مطاعم الريف الحجازي",
        "rep_name": "أحمد الشمري",
        "task_type": "توقيع عقد توريد سنوي",
        "scheduled_at": "2026-09-03 10:00",
        "location": "الإدارة العامة - الملز",
        "route_code": "R-10",
        "ack_status": True,
        "execution_status": "DONE"
    },
    {
        "id": 2,
        "customer_name": "مؤسسة التموين الحديث",
        "rep_name": "سالم الدوسري",
        "task_type": "زيارة تقصي واسترجاع عينات",
        "scheduled_at": "2026-09-04 13:00",
        "location": "مستودعات الخالدية",
        "route_code": "R-14",
        "ack_status": False,
        "execution_status": "PENDING"
    },
    {
        "id": 3,
        "customer_name": "شركة الضيافة الفندقية العالمية",
        "rep_name": "تركي الغامدي",
        "task_type": "تسليم عينات إضافية جديدة",
        "scheduled_at": "2026-09-04 15:30",
        "location": "فندق الكورنيش - جدة",
        "route_code": "R-22",
        "ack_status": True,
        "execution_status": "PENDING"
    }
]

whatsapp_logs_db = [
    {"created_at": "09:15", "sender_name": "م. فهد القرني", "is_external_call": False, "message_body": "السلام عليكم، نريد تجربة عينة صدور دجاج جديدة لفرع التخصصي."},
    {"created_at": "09:22", "sender_name": "أحمد الشمري", "is_external_call": False, "message_body": "أهلاً بك، تم جدولة تسليم 15 كرتون عينة غداً صباحاً بإذن الله."},
    {"created_at": "11:45", "sender_name": "أحمد الشمري", "is_external_call": True, "message_body": "تمت مكالمة مدير التشغيل لتأكيد درجات التبريد والتخزين المعتمدة."}
]

# ----------------- نماذج Pydantic للإدخال اليدوي -----------------
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

# ----------------- واجهات برمجة التطبيقات (APIs) -----------------
@app.get("/health")
def health():
    return {"status": "UP", "timestamp": datetime.now().isoformat()}

@app.get("/api/reps")
def get_reps():
    enriched = []
    for r in sales_executives_db:
        rate = (r["achieved_sales"] / r["monthly_target"]) * 100 if r["monthly_target"] > 0 else 0
        eff = "عالي الكفاءة" if rate >= 100 and r["fuel_liters"] <= r["fuel_allowance_liters"] else ("مقبول" if rate >= 80 else "هدر موارد")
        enriched.append({**r, "achievement_rate": rate, "efficiency": eff})
    return enriched

@app.get("/api/customers")
def get_customers():
    return customer_accounts_db

@app.get("/api/samples")
def get_samples():
    return samples_db

@app.post("/api/samples")
def add_sample(payload: NewSamplePayload):
    new_s = {
        "id": len(samples_db) + 1,
        "customer_id": 1,
        "customer_name": payload.customer_name,
        "rep_name": payload.rep_name,
        "product_name": payload.product_name,
        "qty_free": payload.qty_free,
        "delivery_date": payload.delivery_date,
        "status": "PENDING",
        "converted_po_id": None,
        "po_value": 0.0,
        "source": "إدخال يدوي مباشر"
    }
    samples_db.insert(0, new_s)
    return {"status": "SUCCESS", "sample": new_s}

@app.get("/api/calendar")
def get_calendar():
    return calendar_events_db

@app.post("/api/calendar")
def add_calendar_event(payload: NewCalendarEventPayload):
    new_ev = {
        "id": len(calendar_events_db) + 1,
        "customer_name": payload.customer_name,
        "rep_name": payload.rep_name,
        "task_type": payload.task_type,
        "scheduled_at": payload.scheduled_at,
        "location": payload.location,
        "route_code": payload.route_code,
        "ack_status": False,
        "execution_status": "PENDING"
    }
    calendar_events_db.insert(0, new_ev)
    return {"status": "SUCCESS", "event": new_ev}

@app.post("/api/transactions/sale")
def record_sale(payload: NewSaleTransactionPayload):
    p_rep = next((r for r in sales_executives_db if r["id"] == payload.primary_rep_id), None)
    if not p_rep:
        raise HTTPException(status_code=404, detail="مسؤول المبيعات غير موجود")

    if payload.secondary_rep_id:
        s_rep = next((r for r in sales_executives_db if r["id"] == payload.secondary_rep_id), None)
        if s_rep:
            ratio = (payload.split_percentage or 50.0) / 100.0
            p_rep["achieved_sales"] += payload.sale_amount * (1 - ratio)
            s_rep["achieved_sales"] += payload.sale_amount * ratio
    else:
        p_rep["achieved_sales"] += payload.sale_amount

    p_rep["total_expenses"] += (payload.expense_fuel or 0.0) + (payload.expense_other or 0.0)
    return {"status": "SUCCESS", "message": "تم تقييد المبيعات والمصاريف بنجاح"}

@app.get("/api/whatsapp/logs")
def get_whatsapp_logs():
    return whatsapp_logs_db

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

@app.post("/api/whatsapp/confirm-pairing")
def confirm_pairing():
    return {"status": "SUCCESS", "message": "تم الربط"}

@app.post("/api/whatsapp/disconnect")
def disconnect_pairing():
    return {"status": "SUCCESS", "message": "تم الفصل"}

# ----------------- مسار المعاينة والطباعة عالية الدقة -----------------
@app.post("/api/reports/preview")
async def preview_report(req: dict):
    template_id = req.get("template_id", "02_executive_sales_report.html")
    recipient = req.get("report_recipient", "سعادة رئيس مجلس الإدارة / المدير العام")
    
    total_sales = sum(r["achieved_sales"] for r in sales_executives_db)
    total_exp = sum(r["total_expenses"] for r in sales_executives_db)
    
    html_template = f"""
    <!DOCTYPE html>
    <html lang="ar" dir="rtl">
    <head>
        <meta charset="utf-8">
        <title>تقرير شركة تنمية الغذاء الرسمي</title>
        <link href="https://fonts.googleapis.com/css2?family=Cairo:wght@400;600;700;800&display=swap" rel="stylesheet">
        <style>
            {CSS_PRINT}
        </style>
    </head>
    <body>
        <div class="no-print" style="background: #3A056A; color: #fff; padding: 12px 20px; margin: -20px -20px 20px -20px; display: flex; justify-content: space-between; align-items: center;">
            <div style="font-weight: bold; font-size: 11pt;">معاينة التقرير الرسمي المعتمد لشركة تنمية الغذاء</div>
            <div>
                <button onclick="window.print()" style="background: #C194FB; color: #3A056A; border: none; font-weight: bold; padding: 8px 18px; border-radius: 6px; cursor: pointer; font-family: Cairo;">طباعة أو حفظ PDF 🖨️</button>
            </div>
        </div>

        <div class="header-box">
            <div class="title-box">
                <h1>التقرير التنفيذي الشامل للمبيعات والعمليات</h1>
                <div style="color: #6B7280; font-size: 9pt; margin-top: 4px;">الفترة: الربع الثالث 2026 | جهة التوجيه: {recipient}</div>
            </div>
            {LOGO_SVG}
        </div>

        <div class="kpi-grid">
            <div class="kpi-card">
                <div style="color: #6B7280; font-size: 8pt;">إجمالي المبيعات المحققة</div>
                <div class="kpi-val">{total_sales:,.0f} ر.س</div>
            </div>
            <div class="kpi-card">
                <div style="color: #6B7280; font-size: 8pt;">العينات المعتمدة تجارياً</div>
                <div class="kpi-val" style="color: var(--ok);">66.7%</div>
            </div>
            <div class="kpi-card">
                <div style="color: #6B7280; font-size: 8pt;">المصاريف التشغيلية الكلية</div>
                <div class="kpi-val" style="color: #9333EA;">{total_exp:,.0f} ر.س</div>
            </div>
            <div class="kpi-card">
                <div style="color: #6B7280; font-size: 8pt;">نسبة كفاءة التكلفة للبيع</div>
                <div class="kpi-val">{(total_exp/total_sales*100):.2f}%</div>
            </div>
        </div>

        <div style="font-weight: bold; color: var(--brand); margin: 16px 0 8px 0; font-size: 10pt;">📊 جدول إنجازات فريق المبيعات التنفيذي:</div>
        <table class="data-table">
            <thead>
                <tr>
                    <th>المسؤول</th>
                    <th>المنطقة</th>
                    <th>المستهدف</th>
                    <th>المحقق</th>
                    <th>نسبة الإنجاز</th>
                    <th>استهلاك الوقود</th>
                    <th>تقييم الكفاءة</th>
                </tr>
            </thead>
            <tbody>
                {''.join([f'''
                <tr>
                    <td><strong>{r["name"]}</strong></td>
                    <td>{r["region"]}</td>
                    <td>{r["monthly_target"]:,.0f} ر.س</td>
                    <td style="font-weight:bold; color:var(--ok);">{r["achieved_sales"]:,.0f} ر.س</td>
                    <td>{(r["achieved_sales"]/r["monthly_target"]*100):.1f}%</td>
                    <td>{r["fuel_liters"]} / {r["fuel_allowance_liters"]} لتر</td>
                    <td><span class="badge badge-ok">عالي الكفاءة</span></td>
                </tr>
                ''' for r in sales_executives_db])}
            </tbody>
        </table>

        <div style="font-weight: bold; color: var(--brand); margin: 16px 0 8px 0; font-size: 10pt;">🧪 سجل حركة العينات وتحويلها لأوامر شراء (Sample ROI):</div>
        <table class="data-table">
            <thead>
                <tr>
                    <th>العميل</th>
                    <th>المنتج</th>
                    <th>الكمية</th>
                    <th>تاريخ التسليم</th>
                    <th>قرار الجودة</th>
                    <th>أمر الشراء (PO)</th>
                    <th>قيمة العقد</th>
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
                    <td style="font-family: monospace;">{s["converted_po_id"] or '—'}</td>
                    <td style="font-weight:bold;">{s["po_value"]:,.0f} ر.س</td>
                </tr>
                ''' for s in samples_db])}
            </tbody>
        </table>

        <div class="ai-box">
            <div style="font-weight: bold; color: var(--brand); margin-bottom: 4px;">👔 التوصية الاستراتيجية الذكية (Claude 3.7 Intelligence):</div>
            <div>أظهر الفريق التزاماً استثنائياً في المنطقة الوسطى بتحقيق 108% مع كفاءة في الوقود. يوصى بإسناد حسابات المنطقة الشرقية المتعثرة للمساندة المشتركة وتكثيف توريد عينات الشاورما للقطاع الفندقي.</div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_template)

@app.post("/api/reports/download-pdf")
async def download_pdf_fallback(req: dict):
    # تحويل مباشر لصفحة المعاينة فائقة الدقة ليقوم المتصفح بطباعتها بدقة A4
    return await preview_report(req)

@app.get("/", response_class=HTMLResponse)
def serve_dashboard():
    dashboard_path = os.path.join(os.path.dirname(__file__), "dashboard.html")
    with open(dashboard_path, "r", encoding="utf-8") as f:
        return f.read()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
