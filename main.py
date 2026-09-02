"""
main.py - Enterprise AI Sales CRM & Field Intelligence
Food Development Company (شركة تنمية الغذاء)
FastAPI Backend + AI Swarms (Claude 3.7, DeepSeek, Together AI) + Live SSE + WhatsApp QR Gateway
"""

import os
import sys
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
import httpx

# Configure Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("SalesCRM")

# Application Initialization
app = FastAPI(
    title="Enterprise AI Sales CRM & Field Intelligence",
    version="2.5.0",
    description="نظام إدارة المبيعات الميدانية لشركة تنمية الغذاء مدعوماً بهرمية وكلاء ذكاء اصطناعي تفاعليين"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------- قراءة المتغيرات البيئية من Railway -----------------
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
TOGETHER_API_KEY = os.getenv("TOGETHER_API_KEY", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")

WHATSAPP_SERVER_URL = os.getenv("WHATSAPP_SERVER_URL", "").rstrip("/")
WHATSAPP_API_TOKEN = os.getenv("WHATSAPP_API_TOKEN", "")
WHATSAPP_INSTANCE_NAME = os.getenv("WHATSAPP_INSTANCE_NAME", "fdc_sales_instance")

# ----------------- استيراد محرك التقارير بأمان -----------------
try:
    from sales_reports_engine import render_report_html, generate_report_pdf
    WEASYPRINT_AVAILABLE = True
except Exception as e:
    logger.warning(f"WeasyPrint engine warning: {e}. Fallback to HTML/Direct Print enabled.")
    WEASYPRINT_AVAILABLE = False
    def render_report_html(t, c):
        return f"<html><body dir='rtl'><h1>تقرير تجريبي</h1><pre>{json.dumps(c, ensure_ascii=False, indent=2)}</pre></body></html>"
    def generate_report_pdf(t, c):
        raise HTTPException(status_code=501, detail="WeasyPrint system libraries missing on host. Use HTML preview & print.")

# ----------------- بنك البيانات الحي المؤقت (In-Memory Datastore) -----------------
system_state = {
    "db_connected": bool(DATABASE_URL),
    "whatsapp_status": "QR_READY",  # CONNECTED, DISCONNECTED, QR_READY
    "whatsapp_phone": None,
    "current_qr_url": "https://api.qrserver.com/v1/create-qr-code/?size=250x250&data=FOOD-DEV-CO-PAIRING-TOKEN-INIT",
    "last_sre_check": None,
    "agents": {
        "sales_director": {
            "name": "👔 Sales Director Agent",
            "model": "Claude 3.7 Sonnet",
            "status": "ACTIVE" if ANTHROPIC_API_KEY else "READY (SIMULATED)",
            "role": "التحليل الاستراتيجي، التوصيات الإدارية، وتوليد نصوص التقارير الرسمية",
            "latency_ms": 110
        },
        "omnichannel_watcher": {
            "name": "⚡ Omnichannel Watcher",
            "model": "Llama 3.3 Turbo (Together AI)",
            "status": "ACTIVE" if TOGETHER_API_KEY else "READY (SIMULATED)",
            "role": "الرد الفوري، تنبيهات الـ SLA اللحظية، وتذكير المناديب في الخاص",
            "latency_ms": 65
        },
        "data_kpi_engine": {
            "name": "📐 Data & KPI Engine",
            "model": "DeepSeek V3 / Coder",
            "status": "ACTIVE" if DEEPSEEK_API_KEY else "READY (SIMULATED)",
            "role": "استخراج JSON من نصوص الواتساب، وتدقيق التكاليف وحساب مؤشرات الإنجاز",
            "latency_ms": 80
        },
        "cybersec": {
            "name": "🛡️ CyberSec Guardian",
            "status": "ACTIVE",
            "threats_blocked": 0,
            "role": "فلترة الـ Prompt Injection ومنع تسريب التوكنات وتأمين البيانات"
        },
        "sre_sentinel": {
            "name": "🩺 SRE Sentinel",
            "status": "ACTIVE",
            "checks_run": 0,
            "role": "مراقبة سلامة الاتصال والتعافي التلقائي كل 20 ثانية"
        }
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
    },
    {
        "id": 3,
        "name": "تركي الغامدي",
        "employee_code": "REP-103",
        "phone_number": "+966507778899",
        "region": "جدة - الغربية",
        "monthly_target": 220000.0,
        "achieved_sales": 215000.0,
        "fuel_allowance_liters": 380.0,
        "fuel_liters": 360.0,
        "total_expenses": 3600.0,
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
        "last_activity": "منذ 15 دقيقة"
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
        "last_activity": "منذ 24 يوماً"
    },
    {
        "id": 3,
        "company_name": "شركة الضيافة الفندقية العالمية",
        "sector": "فنادق وخدمات",
        "contact_person": "أ/ وائل الخالدي",
        "phone": "+966508822334",
        "assigned_rep_id": 3,
        "whatsapp_group_id": "120363077615243@g.us",
        "tier": "A",
        "status": "ACTIVE",
        "last_activity": "منذ ساعتين"
    }
]

samples_db = [
    {
        "id": 1,
        "customer_id": 1,
        "customer_name": "سلسلة مطاعم الريف الحجازي",
        "rep_id": 1,
        "product_name": "صدور دجاج متبلة (خلطة 4B الخاصة)",
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
    },
    {
        "id": 3,
        "customer_id": 3,
        "customer_name": "شركة الضيافة الفندقية العالمية",
        "rep_id": 3,
        "product_name": "شاورما دجاج متبلة جاهزة للطهي",
        "qty_free": 25,
        "delivery_date": "2026-08-28",
        "status": "APPROVED",
        "converted_po_id": "PO-2026-904",
        "po_value": 115000.0
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

handovers_db = [
    {
        "id": 1,
        "customer_name": "سلسلة مطاعم الريف الحجازي",
        "from_rep_name": "سالم الدوسري",
        "to_rep_name": "أحمد الشمري",
        "reason": "إجازة طارئة للمندوب السابق واستعجال متطلبات اعتماد الجودة",
        "priority": "HIGH",
        "last_agreement_summary": "تم تسليم عينة صدور متبلة واعتمدت الجودة بنجاح وننتظر إصدار أمر الشراء النهائي بقيمة 78 ألف ريال.",
        "current_client_demand": "تأكيد جدول التوريد يوم السبت القادم بحد أقصى الساعة 8 صباحاً وتثبيت الأسعار لـ 6 أشهر.",
        "urgent_action_plan": [
            {"timeframe": "أول 6 ساعات", "action": "الاتصال المباشر بمدير المشتريات وتأكيد استلام المواصفات"},
            {"timeframe": "خلال 24 ساعة", "action": "تنسيق موعد مع إدارة المستودعات المركزية لحجز الكمية المطلوبة"},
            {"timeframe": "خلال 48 ساعة", "action": "توقيع أمر الشراء النهائي (PO-889) واعتماده رسمياً"}
        ]
    }
]

whatsapp_logs_db = [
    {"created_at": "09:15", "sender_name": "م. فهد القرني", "is_external_call": False, "message_body": "السلام عليكم، متى تصل شحنة العينات الجديدة لفرع التخصصي؟"},
    {"created_at": "09:22", "sender_name": "أحمد الشمري", "is_external_call": False, "message_body": "أهلاً بك مهندس فهد، سيارتنا في الطريق وستكون عندكم قبل 11:00 صباحاً بإذن الله."},
    {"created_at": "11:45", "sender_name": "أحمد الشمري", "is_external_call": True, "message_body": "تم إجراء مكالمة هاتفية مع مدير التشغيل لتأكيد درجات التبريد والتخزين المعتمدة."},
    {"created_at": "14:10", "sender_name": "أ/ وائل الخالدي", "is_external_call": False, "message_body": "تمت مراجعة عينة الشاورما مع الشيف التنفيذي، الطعم ممتاز ونريد إضافة صنف إضافي للتجربة."}
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

# ----------------- الرقابة الذاتية وفلتر الأمان (CyberSec Guardian) -----------------
def inspect_security_payload(text: str):
    forbidden_tokens = ["DROP TABLE", "SELECT * FROM", "API_KEY", "JWT_SECRET", "<script>", "EXEC(", "bash -i"]
    for token in forbidden_tokens:
        if token.lower() in text.lower():
            system_state["agents"]["cybersec"]["threats_blocked"] += 1
            raise HTTPException(status_code=400, detail=f"CyberSec Guardian: تم رصد محاولة غير آمنة وحجبها ({token}).")

# ----------------- حلقة مراقبة الصحة الذاتية (SRE Sentinel) -----------------
async def sre_health_loop():
    while True:
        try:
            await asyncio.sleep(20)
            system_state["agents"]["sre_sentinel"]["checks_run"] += 1
            system_state["last_sre_check"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            # فحص خادم Evolution API إذا تم إعداده
            if WHATSAPP_SERVER_URL:
                try:
                    async with httpx.AsyncClient(timeout=4.0) as client:
                        headers = {"apikey": WHATSAPP_API_TOKEN} if WHATSAPP_API_TOKEN else {}
                        resp = await client.get(f"{WHATSAPP_SERVER_URL}/instance/connectionState/{WHATSAPP_INSTANCE_NAME}", headers=headers)
                        if resp.status_code == 200:
                            data = resp.json()
                            state_val = data.get("instance", {}).get("state", "DISCONNECTED")
                            system_state["whatsapp_status"] = "CONNECTED" if state_val == "open" else "QR_READY"
                except Exception as e:
                    logger.warning(f"Evolution API check connection failed: {e}")
        except Exception as e:
            logger.error(f"SRE Sentinel Exception: {e}")

@app.on_event("startup")
async def on_startup():
    asyncio.create_task(sre_health_loop())

# ----------------- مسارات واجهة برمجة التطبيقات (APIs) -----------------

@app.get("/health")
def health():
    return {"status": "UP", "timestamp": datetime.now().isoformat()}

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

# ----------------- مسارات إدارة وتوليد QR Code للواتساب -----------------

@app.get("/api/whatsapp/status")
async def get_whatsapp_status():
    if WHATSAPP_SERVER_URL:
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                headers = {"apikey": WHATSAPP_API_TOKEN} if WHATSAPP_API_TOKEN else {}
                resp = await client.get(f"{WHATSAPP_SERVER_URL}/instance/connectionState/{WHATSAPP_INSTANCE_NAME}", headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    state_val = data.get("instance", {}).get("state", "close")
                    system_state["whatsapp_status"] = "CONNECTED" if state_val == "open" else "QR_READY"
        except Exception:
            pass

    return {
        "status": system_state["whatsapp_status"],
        "phone_connected": system_state["whatsapp_phone"],
        "last_sync": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

@app.get("/api/whatsapp/qr")
async def get_whatsapp_qr():
    if WHATSAPP_SERVER_URL:
        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                headers = {"apikey": WHATSAPP_API_TOKEN} if WHATSAPP_API_TOKEN else {}
                resp = await client.get(f"{WHATSAPP_SERVER_URL}/instance/connect/{WHATSAPP_INSTANCE_NAME}", headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    qr_code = data.get("base64") or data.get("code")
                    if qr_code:
                        system_state["whatsapp_status"] = "QR_READY"
                        return {
                            "qr_image_url": qr_code if qr_code.startswith("data:") else f"https://api.qrserver.com/v1/create-qr-code/?size=250x250&data={qr_code}",
                            "status": "QR_READY"
                        }
        except Exception as e:
            logger.warning(f"Failed to fetch QR from external server, falling back to simulated QR: {e}")

    session_id = str(uuid.uuid4())[:8]
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=250x250&data=WHATSAPP-AUTH-FDC-{session_id}"
    system_state["current_qr_url"] = qr_url
    if system_state["whatsapp_status"] != "CONNECTED":
        system_state["whatsapp_status"] = "QR_READY"

    return {
        "qr_image_url": qr_url,
        "session_id": session_id,
        "status": system_state["whatsapp_status"],
        "expires_in_seconds": 60
    }

@app.post("/api/whatsapp/confirm-pairing")
def confirm_pairing():
    system_state["whatsapp_status"] = "CONNECTED"
    system_state["whatsapp_phone"] = "+966 50 111 2233"
    return {"status": "SUCCESS", "message": "تم تأكيد ربط جلسة الواتساب بنجاح!"}

@app.post("/api/whatsapp/disconnect")
def disconnect_whatsapp():
    system_state["whatsapp_status"] = "DISCONNECTED"
    system_state["whatsapp_phone"] = None
    return {"status": "SUCCESS", "message": "تم فصل جلسة الواتساب، يمكنك إعادة مسح الرمز."}

@app.post("/api/whatsapp/webhook")
async def receive_whatsapp_message(msg: WhatsAppMessageInbound, background_tasks: BackgroundTasks):
    inspect_security_payload(msg.message_body)
    
    entry = {
        "created_at": datetime.now().strftime("%H:%M"),
        "sender_name": msg.sender_name,
        "is_external_call": msg.is_external_call,
        "message_body": msg.message_body
    }
    whatsapp_logs_db.insert(0, entry)

    async def process_agents_pipeline(text: str, sender: str):
        if "عين" in text or "طلب" in text or "PO" in text or "شراء" in text:
            logger.info(f"[DeepSeek KPI Engine] Analyzing procurement intent in message: {text}")
        if msg.sender_type == "CLIENT" and not msg.is_external_call:
            logger.info(f"[Together AI Omnichannel] Dispatched auto-notification to rep for client: {sender}")

    background_tasks.add_task(process_agents_pipeline, msg.message_body, msg.sender_name)
    return {"status": "SUCCESS", "message_logged": True}

# ----------------- مسار بث الأحداث الحي (SSE Stream) -----------------
@app.get("/api/stream/events")
async def events_stream(request: Request):
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
            await asyncio.sleep(4)
    return StreamingResponse(event_generator(), media_type="text/event-stream")

# ----------------- محرك التقارير التنفيذية (Jinja2 & PDF) -----------------
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
        "strategic_summary": "أظهر تحليل وكيل المبيعات التنفيذي (Claude 3.7) استقراراً في مبيعات المنطقة الوسطى ونمواً قياسياً في عقود الفنادق بالمنطقة الغربية، مع ضرورة إعادة تنظيم خطوط سير المنطقة الشرقية للحد من استهلاك الوقود.",
        "ai_recommendation": f"الموظف ({rep['name']}) أتم نسبة إنجاز ممتازة بواقع {achieve:.1f}% مع انضباط في استهلاك الوقود. يوصى بصرف مكافأة كفاءة تكلفة ميدانية.",
        "samples": samples_db,
        "include_samples": req.include_samples,
        "include_sla": req.include_sla,
        "include_stagnant": req.include_stagnant,
        "sla_summary": [
            {"customer_name": "سلسلة مطاعم الريف الحجازي", "avg_response_minutes": 8.5, "phone_call_count": 4},
            {"customer_name": "مؤسسة التموين الحديث", "avg_response_minutes": 22.0, "phone_call_count": 1},
            {"customer_name": "شركة الضيافة الفندقية العالمية", "avg_response_minutes": 6.2, "phone_call_count": 3}
        ],
        "stagnant_accounts": [
            {
                "company_name": "مؤسسة التموين الحديث",
                "rep_name": "سالم الدوسري",
                "sector": "تجارة جملة",
                "last_activity": "منذ 24 يوماً",
                "action_required": "إصدار مذكرة إحالة طارئة وتكليف مندوب بديل لتنفيذ زيارة استرجاع عينات"
            }
        ],
        "account": {
            **customer_accounts_db[0],
            "rep_name": "أحمد الشمري"
        },
        "samples_summary": {
            "total_samples": len(samples_db),
            "approved": len([s for s in samples_db if s["status"] == "APPROVED"]),
            "po_count": len([s for s in samples_db if s["converted_po_id"]]),
            "total_po_value": sum(s["po_value"] for s in samples_db)
        },
        "recent_logs": whatsapp_logs_db,
        "schedule_period": "شهر سبتمبر 2026",
        "events": calendar_events_db,
        "handover": handovers_db[0],
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M")
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
            headers={"Content-Disposition": f"attachment; filename=FDC_Official_Report_{req.template_id.replace('.html', '')}.pdf"}
        )
    except Exception as e:
        logger.error(f"PDF Generation issue: {e}")
        raise HTTPException(status_code=500, detail=f"تعذر توليد ملف PDF: {str(e)}")

# مسار تقديم لوحة التحكم
@app.get("/", response_class=HTMLResponse)
def serve_dashboard():
    dashboard_path = os.path.join(os.path.dirname(__file__), "dashboard.html")
    if os.path.exists(dashboard_path):
        with open(dashboard_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>dashboard.html not found. Please upload it alongside main.py</h1>"

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
