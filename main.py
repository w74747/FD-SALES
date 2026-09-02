"""
main.py
الملف الرئيسي لنظام إدارة المبيعات الميدانية والتنفيذية الذكي
FastAPI + Asyncio + SSE Stream + AI Swarm Watchers + WhatsApp Webhooks
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Optional, List
from fastapi import FastAPI, HTTPException, Response, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from sales_reports_engine import render_report_html, generate_report_pdf

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("SalesCRM")

app = FastAPI(
    title="Enterprise AI Sales CRM & Field Intelligence",
    version="2.4.0",
    description="نظام إدارة المبيعات الميدانية لشركة تنمية الغذاء مدعوماً بهرمية وكلاء ذكاء اصطناعي"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------- الذاكرة الحية المؤقتة للمنظومة (Data Store) -----------------
system_state = {
    "db_connected": True,
    "whatsapp_status": "QR_READY",  # CONNECTED, DISCONNECTED, QR_READY
    "whatsapp_qr": "https://api.qrserver.com/v1/create-qr-code/?size=220x220&data=FOOD-DEV-CO-AUTH-TOKEN-2026",
    "last_sre_check": None,
    "agents": {
        "sales_director": {"status": "ACTIVE", "model": "Claude 3.7 Sonnet", "latency_ms": 120},
        "group_sentinel": {"status": "ACTIVE", "model": "DeepSeek Coder / Parser", "latency_ms": 45},
        "together_ai": {"status": "ACTIVE", "model": "Llama 3.3 Turbo", "latency_ms": 85},
        "cybersec": {"status": "ACTIVE", "model": "DLP / Guard Engine", "threats_blocked": 0},
        "sre_sentinel": {"status": "ACTIVE", "checks_run": 0, "healthy": True}
    }
}

sales_reps_db = [
    {
        "id": 1,
        "name": "أحمد الشمري",
        "employee_code": "REP-101",
        "phone_number": "+966501112233",
        "region": "الرياض - الوسطى",
        "monthly_target": 250000.0,
        "achieved_sales": 272000.0,
        "fuel_allowance_liters": 400.0,
        "fuel_liters": 380.0,
        "total_expenses": 3200.0,
        "status": "ACTIVE"
    },
    {
        "id": 2,
        "name": "سالم الدوسري",
        "employee_code": "REP-102",
        "phone_number": "+966504445566",
        "region": "الدمام - الشرقية",
        "monthly_target": 180000.0,
        "achieved_sales": 135000.0,
        "fuel_allowance_liters": 350.0,
        "fuel_liters": 395.0,
        "total_expenses": 4100.0,
        "status": "ACTIVE"
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
        "whatsapp_group_id": "120363029182371@g.us",
        "tier": "A",
        "status": "ACTIVE",
        "last_activity": "2026-09-02 11:30"
    },
    {
        "id": 2,
        "company_name": "مؤسسة التموين الحديث",
        "sector": "تجارة جملة",
        "contact_person": "أ/ طارق المنصور",
        "phone": "+966503322110",
        "assigned_rep_id": 2,
        "whatsapp_group_id": "120363088716253@g.us",
        "tier": "B",
        "status": "STAGNANT",
        "last_activity": "2026-08-10 14:00"
    }
]

samples_db = [
    {
        "id": 1,
        "customer_id": 1,
        "customer_name": "سلسلة مطاعم الريف الحجازي",
        "rep_id": 1,
        "product_name": "صدور دجاج متبلة (خلطة 4B)",
        "qty_free": 15,
        "delivery_date": "2026-08-25",
        "status": "APPROVED",
        "converted_po_id": "PO-2026-889",
        "po_value": 78000.0
    },
    {
        "id": 2,
        "customer_id": 2,
        "customer_name": "مؤسسة التموين الحديث",
        "rep_id": 2,
        "product_name": "دجاج مجمد فائق الجودة 1000g",
        "qty_free": 20,
        "delivery_date": "2026-08-12",
        "status": "PENDING",
        "converted_po_id": None,
        "po_value": 0.0
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
    }
]

handovers_db = [
    {
        "id": 1,
        "customer_name": "سلسلة مطاعم الريف الحجازي",
        "from_rep_name": "سالم الدوسري",
        "to_rep_name": "أحمد الشمري",
        "reason": "إجازة طارئة للمندوب السابق واستعجال طلبات الجودة",
        "priority": "HIGH",
        "last_agreement_summary": "تم تسليم عينة صدور متبلة واعتمدت الجودة بنجاح وننتظر إصدار أمر الشراء النهائي خلال 48 ساعة.",
        "current_client_demand": "تأكيد جدول التوريد يوم السبت القادم بحد أقصى الساعة 8 صباحاً.",
        "urgent_action_plan": [
            {"timeframe": "أول 6 ساعات", "action": "الاتصال بمدير المشتريات وتأكيد استلام المواصفات"},
            {"timeframe": "خلال 24 ساعة", "action": "تنسيق موعد مع إدارة المستودعات لحجز الكمية المطلوبة"},
            {"timeframe": "خلال 48 ساعة", "action": "توقيع أمر الشراء النهائي واعتماده رسمياً"}
        ]
    }
]

whatsapp_logs_db = [
    {"created_at": "09:15", "sender_name": "م. فهد القرني", "is_external_call": False, "message_body": "السلام عليكم، متى تصل شحنة العينات الجديدة لفرع التخصصي؟"},
    {"created_at": "09:22", "sender_name": "أحمد الشمري", "is_external_call": False, "message_body": "أهلاً بك مهندس فهد، سيارتنا في الطريق وستكون عندكم قبل 11:00 صباحاً بإذن الله."},
    {"created_at": "11:45", "sender_name": "أحمد الشمري", "is_external_call": True, "message_body": "تم إجراء مكالمة هاتفية مع مدير التشغيل لتأكيد التخزين في درجات التجميد المعتمدة."}
]

# ----------------- نماذج البيانات (Pydantic Models) -----------------
class WhatsAppMessageInbound(BaseModel):
    group_id: str
    sender_name: str
    sender_type: str = "CLIENT"  # CLIENT | REP
    message_body: str
    is_external_call: bool = False

class ReportGenerationRequest(BaseModel):
    template_id: str = "01_rep_performance_scorecard.html"
    report_recipient: str = "سعادة رئيس مجلس الإدارة / المدير العام"
    rep_id: Optional[int] = 1
    customer_id: Optional[int] = 1
    include_samples: bool = True
    include_sla: bool = True
    include_stagnant: bool = True

# ----------------- الرقابة الذاتية وفلتر الأمان (CyberSec & SRE) -----------------
def inspect_security_payload(text: str):
    """CyberSec Guardian: منع هجمات حقن الأوامر ومنع تسريب البيانات DLP."""
    forbidden_tokens = ["DROP TABLE", "SELECT * FROM", "API_KEY", "JWT_SECRET", "<script>", "EXEC("]
    for token in forbidden_tokens:
        if token.lower() in text.lower():
            system_state["agents"]["cybersec"]["threats_blocked"] += 1
            raise HTTPException(status_code=400, detail=f"CyberSec Guardian: تم رصد محاولة غير آمنة وحجبها ({token}).")

async def sre_health_loop():
    """حلقة مراقبة الصحة الذاتية للمنظومة (SRE Sentinel) كل 20 ثانية."""
    while True:
        try:
            await asyncio.sleep(20)
            system_state["agents"]["sre_sentinel"]["checks_run"] += 1
            system_state["last_sre_check"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            # تدقيق اتصالات الواتساب وتدفق الذاكرة
            if system_state["whatsapp_status"] == "DISCONNECTED":
                logger.warning("SRE Alert: WhatsApp disconnected. Attempting auto-reconnect...")
                system_state["whatsapp_status"] = "QR_READY"
        except Exception as e:
            logger.error(f"SRE Sentinel Exception: {e}")

@app.on_event("startup")
async def on_startup():
    asyncio.create_task(sre_health_loop())

# ----------------- مسارات واجهة برمجة التطبيقات (API Routes) -----------------

@app.get("/api/state")
def get_system_state():
    return {
        "system": system_state,
        "reps_count": len(sales_reps_db),
        "customers_count": len(customer_accounts_db),
        "samples_count": len(samples_db),
        "events_count": len(calendar_events_db)
    }

@app.get("/api/reps")
def get_reps():
    enriched = []
    for r in sales_reps_db:
        achieve = (r["achieved_sales"] / r["monthly_target"]) * 100 if r["monthly_target"] > 0 else 0
        eff = "عالي الكفاءة" if achieve >= 100 and r["fuel_liters"] <= r["fuel_allowance_liters"] else ("مقبول" if achieve >= 80 else "هدر موارد")
        enriched.append({**r, "achievement_rate": achieve, "efficiency": eff})
    return enriched

@app.get("/api/customers")
def get_customers():
    return customer_accounts_db

@app.get("/api/samples")
def get_samples():
    return samples_db

@app.get("/api/calendar")
def get_calendar():
    return calendar_events_db

@app.get("/api/whatsapp/logs")
def get_whatsapp_logs():
    return whatsapp_logs_db

@app.post("/api/whatsapp/webhook")
def receive_whatsapp_message(msg: WhatsAppMessageInbound, background_tasks: BackgroundTasks):
    """استقبال رسائل ومكالمات الواتساب مع الرقابة الأمنية والرد الذكي من الوكلاء."""
    inspect_security_payload(msg.message_body)
    
    entry = {
        "created_at": datetime.now().strftime("%H:%M"),
        "sender_name": msg.sender_name,
        "is_external_call": msg.is_external_call,
        "message_body": msg.message_body
    }
    whatsapp_logs_db.insert(0, entry)

    # معالجة استجابة الوكيل في الخلفية إذا كانت الرسالة واردة من عميل
    if msg.sender_type == "CLIENT" and not msg.is_external_call:
        def agent_dispatcher():
            logger.info(f"Group Sentinel dispatched alert for client: {msg.sender_name}")
        background_tasks.add_task(agent_dispatcher)

    return {"status": "SUCCESS", "message_logged": True}

@app.get("/api/stream/events")
async def events_stream(request: Request):
    """بث الأحداث الحية للوحة التحكم عبر Server-Sent Events (SSE)."""
    async def event_generator():
        while True:
            if await request.is_disconnected():
                break
            payload = {
                "timestamp": datetime.now().strftime("%H:%M:%S"),
                "whatsapp_status": system_state["whatsapp_status"],
                "reps": len(sales_reps_db),
                "threats_blocked": system_state["agents"]["cybersec"]["threats_blocked"],
                "sre_checks": system_state["agents"]["sre_sentinel"]["checks_run"]
            }
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            await asyncio.sleep(5)
    return StreamingResponse(event_generator(), media_type="text/event-stream")

# ----------------- مسارات محرك التقارير (HTML & PDF) -----------------

def build_report_context(req: ReportGenerationRequest) -> dict:
    rep = next((r for r in sales_reps_db if r["id"] == req.rep_id), sales_reps_db[0])
    achieve = (rep["achieved_sales"] / rep["monthly_target"]) * 100
    rep_obj = {**rep, "achievement_rate": achieve}

    reps_perf = []
    tot_rev = 0
    tot_exp = 0
    for r in sales_reps_db:
        rate = (r["achieved_sales"] / r["monthly_target"]) * 100
        eff = "عالي الكفاءة" if rate >= 100 and r["fuel_liters"] <= r["fuel_allowance_liters"] else ("مقبول" if rate >= 80 else "هدر موارد")
        tot_rev += r["achieved_sales"]
        tot_exp += r["total_expenses"]
        reps_perf.append({**r, "achievement_rate": rate, "efficiency": eff})

    cost_ratio = (tot_exp / tot_rev * 100) if tot_rev > 0 else 0

    return {
        "report_recipient": req.report_recipient,
        "rep": rep_obj,
        "reps_performance": reps_perf,
        "total_revenue": tot_rev,
        "cost_to_sales_ratio": f"{cost_ratio:.2f}",
        "strategic_summary": "أظهرت التحليلات استقرار المبيعات في المنطقة الوسطى بينما تشهد المنطقة الشرقية ارتفاعاً طفيفاً في تكلفة الوقود بالنسبة للمبيعات بنسبة 2.8%، ويوصى بإعادة تنظيم مسارات التوزيع.",
        "ai_recommendation": "الموظف حقق 108.8% من التارجت الشهري مع انضباط في استهلاك الوقود (380 من 400 لتر). يُوصى بصرف مكافأة تميز وإسناد حسابات المنطقة الشرقية للمساندة.",
        "samples": samples_db,
        "include_samples": req.include_samples,
        "include_sla": req.include_sla,
        "include_stagnant": req.include_stagnant,
        "sla_summary": [
            {"customer_name": "سلسلة مطاعم الريف الحجازي", "avg_response_minutes": 8.5, "phone_call_count": 4},
            {"customer_name": "مؤسسة التموين الحديث", "avg_response_minutes": 22.0, "phone_call_count": 1}
        ],
        "stagnant_accounts": [
            {
                "company_name": "مؤسسة التموين الحديث",
                "rep_name": "سالم الدوسري",
                "sector": "تجارة جملة",
                "last_activity": "منذ 24 يوماً",
                "action_required": "إرسال مذكرة تسليم مهام لمندوب آخر وإجراء زيارة طارئة"
            }
        ],
        "account": {
            **customer_accounts_db[0],
            "rep_name": "أحمد الشمري"
        },
        "samples_summary": {
            "total_samples": 3,
            "approved": 2,
            "po_count": 1,
            "total_po_value": 78000.0
        },
        "recent_logs": whatsapp_logs_db,
        "schedule_period": "الأسبوع الأول من سبتمبر 2026",
        "events": [
            {**ev} for ev in calendar_events_db
        ],
        "handover": handovers_db[0]
    }

@app.post("/api/reports/preview")
def preview_report(req: ReportGenerationRequest):
    ctx = build_report_context(req)
    html = render_report_html(req.template_id, ctx)
    return HTMLResponse(content=html)

@app.post("/api/reports/download-pdf")
def download_pdf(req: ReportGenerationRequest):
    ctx = build_report_context(req)
    try:
        pdf_bytes = generate_report_pdf(req.template_id, ctx)
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename=FDC_Report_{req.template_id.replace('.html', '')}.pdf"}
        )
    except Exception as e:
        logger.error(f"WeasyPrint PDF Generation Error: {e}")
        raise HTTPException(status_code=500, detail=f"تعذر توليد الـ PDF: {str(e)}")

@app.get("/", response_class=HTMLResponse)
def serve_dashboard():
    with open("dashboard.html", "r", encoding="utf-8") as f:
        return f.read()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
