"""
main.py - Enterprise AI Sales CRM & Field Intelligence
Food Development Company (شركة تنمية الغذاء)
FastAPI Backend + High-Precision Printing Engine (OMR Currency & Corporate Identity)
"""

import os
import json
import uuid
import logging
from datetime import datetime
from typing import Optional, List
from fastapi import FastAPI, HTTPException, Response, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("SalesCRM")

app = FastAPI(title="FDC Sales CRM", version="3.3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------- شعار شركة تنمية الغذاء الرسمي (Pure Vector SVG) -----------------
OFFICIAL_COMPANY_LOGO = """
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 70" width="260" height="60" style="display: block;">
    <rect x="235" y="5" width="58" height="58" rx="14" fill="#3A056A" stroke="#C194FB" stroke-width="2.5"/>
    <text x="264" y="44" fill="#FFFFFF" font-family="'Cairo', sans-serif" font-size="28" font-weight="900" text-anchor="middle">ت</text>
    <text x="220" y="32" fill="#3A056A" font-family="'Cairo', sans-serif" font-size="19" font-weight="800" text-anchor="end">شركة تنمية الغذاء</text>
    <text x="220" y="50" fill="#7E22CE" font-family="'Cairo', sans-serif" font-size="9.5" font-weight="700" letter-spacing="1.5" text-anchor="end">FOOD DEVELOPMENT CO.</text>
</svg>
"""

# ----------------- CSS الطباعة الصارم والمعتمد (إخفاء ترويسات المتصفح الافتراضية) -----------------
PRINT_ENGINE_CSS = """
@page {
    size: A4 portrait;
    margin: 10mm 12mm 10mm 12mm;
}
@media print {
    html, body {
        width: 210mm;
        height: 297mm;
        margin: 0 !important;
        padding: 0 !important;
        background: #FFFFFF !important;
        color: #1A202C !important;
        -webkit-print-color-adjust: exact !important;
        print-color-adjust: exact !important;
    }
    .no-print {
        display: none !important;
    }
    .print-container {
        padding: 0 !important;
        box-shadow: none !important;
        border: none !important;
    }
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
    padding: 24px;
    font-size: 9.5pt;
    background: #F8FAFC;
}
.print-container {
    max-width: 200mm;
    margin: 0 auto;
    background: #FFFFFF;
    padding: 24px;
    border-radius: 8px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.05);
}
.kpi-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 12px;
    margin-bottom: 18px;
}
.kpi-card {
    background: #FAF7FD;
    border: 1px solid var(--line);
    border-radius: 8px;
    padding: 10px;
    text-align: center;
}
.kpi-lbl {
    font-size: 8.5pt;
    color: #64748B;
    font-weight: 600;
}
.kpi-val {
    font-size: 14pt;
    font-weight: 800;
    color: var(--brand);
    margin-top: 4px;
}
table.data-table {
    width: 100%;
    border-collapse: collapse;
    margin-bottom: 18px;
    border: 1px solid var(--line);
}
table.data-table th {
    background: var(--brand);
    color: #FFFFFF;
    text-align: right;
    padding: 8px 10px;
    font-size: 8.5pt;
    font-weight: 700;
}
table.data-table td {
    padding: 7px 10px;
    border-bottom: 1px solid var(--line);
    font-size: 8.5pt;
}
table.data-table tr:nth-child(even) {
    background: #FAF7FD;
}
.badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-weight: 700;
    font-size: 7.5pt;
}
.badge-ok { background: #EDF7F2; color: var(--ok); }
.badge-warn { background: #FDF6E7; color: var(--warn); }
.badge-bad { background: #FBEEEE; color: var(--bad); }
.editable-box {
    background: #FAF7FD;
    border: 1.5px dashed var(--accent);
    border-radius: 8px;
    padding: 12px 16px;
    margin-top: 14px;
    outline: none;
    line-height: 1.6;
}
.editable-box:focus {
    border-style: solid;
    background: #FFFFFF;
}
"""

# ----------------- بنك البيانات المعتمد بالريال العماني (OMR) -----------------
sales_executives_db = [
    {
        "id": 1,
        "name": "أحمد الشمري",
        "employee_code": "SE-101",
        "phone_number": "+96891112233",
        "region": "مسقط - الوسطى",
        "monthly_target": 25000.0,
        "achieved_sales": 27200.0,
        "fuel_allowance_liters": 400.0,
        "fuel_liters": 380.0,
        "total_expenses": 320.0,
        "status": "نشط"
    },
    {
        "id": 2,
        "name": "سالم الدوسري",
        "employee_code": "SE-102",
        "phone_number": "+96894445566",
        "region": "صحار - الباطنة",
        "monthly_target": 18000.0,
        "achieved_sales": 13500.0,
        "fuel_allowance_liters": 350.0,
        "fuel_liters": 395.0,
        "total_expenses": 410.0,
        "status": "نشط"
    },
    {
        "id": 3,
        "name": "تركي الغامدي",
        "employee_code": "SE-103",
        "phone_number": "+96897778899",
        "region": "صلالة - ظفار",
        "monthly_target": 22000.0,
        "achieved_sales": 21500.0,
        "fuel_allowance_liters": 380.0,
        "fuel_liters": 360.0,
        "total_expenses": 360.0,
        "status": "نشط"
    }
]

customer_accounts_db = [
    {
        "id": 1,
        "company_name": "سلسلة مطاعم الريف",
        "sector": "مطاعم وإعاشة",
        "contact_person": "م. فهد القرني",
        "phone": "+96899988771",
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
        "phone": "+96893322110",
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
        "phone": "+96898822334",
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
        "customer_name": "سلسلة مطاعم الريف",
        "rep_name": "أحمد الشمري",
        "product_name": "صدور دجاج متبلة (خلطة 4B)",
        "qty_free": 15,
        "delivery_date": "2026-08-25",
        "status": "APPROVED",
        "converted_po_id": "PO-2026-889",
        "po_value": 7800.0,
        "source": "WhatsApp Sentinel"
    },
    {
        "id": 2,
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
        "customer_name": "شركة الضيافة الفندقية العالمية",
        "rep_name": "تركي الغامدي",
        "product_name": "شاورما دجاج جاهزة للطهي",
        "qty_free": 25,
        "delivery_date": "2026-08-28",
        "status": "APPROVED",
        "converted_po_id": "PO-2026-904",
        "po_value": 11500.0,
        "source": "WhatsApp Sentinel"
    }
]

calendar_events_db = [
    {
        "id": 1,
        "customer_name": "سلسلة مطاعم الريف",
        "rep_name": "أحمد الشمري",
        "task_type": "توقيع عقد توريد سنوي",
        "scheduled_at": "2026-09-03 10:00",
        "location": "الإدارة العامة - مسقط",
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
        "location": "مستودعات صحار",
        "route_code": "R-14",
        "ack_status": False,
        "execution_status": "PENDING"
    }
]

whatsapp_logs_db = [
    {"created_at": "09:15", "sender_name": "م. فهد القرني", "is_external_call": False, "message_body": "السلام عليكم، نريد تجربة عينة صدور دجاج جديدة لفرع مسقط."},
    {"created_at": "09:22", "sender_name": "أحمد الشمري", "is_external_call": False, "message_body": "أهلاً بك، تم إرسال 15 كرتون عينة للتجربة الميدانية."},
    {"created_at": "11:45", "sender_name": "أحمد الشمري", "is_external_call": True, "message_body": "تم إجراء مكالمة مع مدير المشتريات وتأكيد استلام المواصفات القياسية."}
]

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

# ----------------- مسارات واجهة برمجة التطبيقات (APIs) -----------------
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
        "customer_name": payload.customer_name,
        "rep_name": payload.rep_name,
        "product_name": payload.product_name,
        "qty_free": payload.qty_free,
        "delivery_date": payload.delivery_date,
        "status": "PENDING",
        "converted_po_id": None,
        "po_value": 0.0,
        "source": "إدخال يدوي"
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

# ----------------- مسار معاينة وطباعة التقرير الرسمي -----------------
@app.post("/api/reports/preview")
def preview_report(req: dict):
    recipient = req.get("report_recipient", "سعادة رئيس مجلس الإدارة / المدير العام")
    recommendation = req.get("recommendation", "أظهر الفريق التزاماً استثنائياً في منطقة مسقط بنسبة إنجاز 108.8% مع كفاءة في استهلاك الوقود. يُوصى بمساندة مسار صحار لرفع معدل التحويل وتكثيف توريد العينات للقطاع الفندقي.")
    
    total_sales = sum(r["achieved_sales"] for r in sales_executives_db)
    total_exp = sum(r["total_expenses"] for r in sales_executives_db)
    
    html = f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="utf-8">
    <title>&nbsp;</title>
    <link href="https://fonts.googleapis.com/css2?family=Cairo:wght@400;600;700;800;900&display=swap" rel="stylesheet">
    <style>{PRINT_ENGINE_CSS}</style>
</head>
<body>
    <div class="no-print" style="max-width: 200mm; margin: 0 auto 16px auto; background: #3A056A; color: #FFFFFF; padding: 12px 20px; border-radius: 8px; display: flex; justify-content: space-between; align-items: center; box-shadow: 0 4px 10px rgba(0,0,0,0.1);">
        <div style="font-weight: 700; font-size: 10pt;">معاينة المستند الرسمي | شركة تنمية الغذاء</div>
        <div style="display: flex; gap: 10px;">
            <button onclick="window.print()" style="background: #C194FB; color: #3A056A; border: none; font-weight: 800; padding: 8px 20px; border-radius: 6px; cursor: pointer; font-family: Cairo; font-size: 9pt;">طباعة المستند الرسمي (A4) 🖨️</button>
        </div>
    </div>

    <div class="print-container">
        <!-- ترويسة التقرير الرسمية المعتمدة عبر جدول محاذاة صارم -->
        <table style="width: 100%; border-bottom: 2.5px solid #3A056A; padding-bottom: 12px; margin-bottom: 18px; border-collapse: collapse;">
            <tr>
                <td style="text-align: right; vertical-align: middle;">
                    <h1 style="margin: 0 0 6px 0; color: #3A056A; font-size: 17pt; font-weight: 800;">التقرير التنفيذي الشامل للمبيعات والعمليات</h1>
                    <div style="color: #64748B; font-size: 9pt;">الفترة: الربع الثالث 2026 &nbsp;|&nbsp; توجيه المستند: {recipient}</div>
                </td>
                <td style="text-align: left; vertical-align: middle; width: 270px;">
                    {OFFICIAL_COMPANY_LOGO}
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
                <div class="kpi-val">{(total_exp/total_sales*100):.2f}%</div>
            </div>
        </div>

        <div style="font-weight: 800; color: var(--brand); margin: 16px 0 8px 0; font-size: 10pt;">جدول إنجازات فريق المبيعات التنفيذي:</div>
        <table class="data-table">
            <thead>
                <tr>
                    <th>المسؤول</th>
                    <th>المنطقة</th>
                    <th>المستهدف الشهري</th>
                    <th>المبيعات المحققة</th>
                    <th>نسبة الإنجاز</th>
                    <th>استهلاك الوقود</th>
                    <th>المصاريف</th>
                    <th>تقييم الكفاءة</th>
                </tr>
            </thead>
            <tbody>
                {''.join([f'''
                <tr>
                    <td><strong>{r["name"]}</strong></td>
                    <td>{r["region"]}</td>
                    <td>{r["monthly_target"]:,.1f} ر.ع</td>
                    <td style="font-weight: 800; color: var(--ok);">{r["achieved_sales"]:,.1f} ر.ع</td>
                    <td>{(r["achieved_sales"]/r["monthly_target"]*100):.1f}%</td>
                    <td>{r["fuel_liters"]} / {r["fuel_allowance_liters"]} لتر</td>
                    <td>{r["total_expenses"]:,.1f} ر.ع</td>
                    <td><span class="badge badge-ok">عالي الكفاءة</span></td>
                </tr>
                ''' for r in sales_executives_db])}
            </tbody>
        </table>

        <div style="font-weight: 800; color: var(--brand); margin: 16px 0 8px 0; font-size: 10pt;">سجل حركة العينات وتحويلها لأوامر شراء (Sample ROI):</div>
        <table class="data-table">
            <thead>
                <tr>
                    <th>العميل</th>
                    <th>المنتج</th>
                    <th>الكمية</th>
                    <th>تاريخ التسليم</th>
                    <th>قرار الاعتماد</th>
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
                    <td style="font-weight: 800;">{s["po_value"]:,.1f} ر.ع</td>
                </tr>
                ''' for s in samples_db])}
            </tbody>
        </table>

        <div style="margin-top: 14px;">
            <div style="font-weight: 800; color: var(--brand); font-size: 9.5pt; margin-bottom: 4px;">التوصية الإدارية والتنفيذية (قابلة للتحرير قبل الطباعة):</div>
            <div class="editable-box" contenteditable="true" title="اضغط هنا لتعديل نص التوصية مباشرة قبل الطباعة">{recommendation}</div>
        </div>

        <div style="margin-top: 24px; padding-top: 12px; border-top: 1px solid var(--line); display: flex; justify-content: space-between; font-size: 8pt; color: #64748B;">
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
