"""
sales_reports_engine.py
محرك التقارير التنفيذية والرسمية لشركة تنمية الغذاء (Food Development Company)
يعتمد Jinja2 و WeasyPrint لتوليد مستندات PDF فائقة الجودة.
"""

import os
from jinja2 import Environment, DictLoader
from weasyprint import HTML

# الشعار الرسمي كـ SVG Base64 مرمز نقي لشركة تنمية الغذاء
COMPANY_LOGO_BASE64 = (
    "data:image/svg+xml;utf8,"
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 320 80' width='320' height='80'>"
    "<rect width='100%' height='100%' rx='10' fill='%233A056A'/>"
    "<circle cx='45' cy='40' r='24' fill='%23C194FB'/>"
    "<path d='M35 48 C 35 30, 55 30, 55 48 Z' fill='%23FFFFFF'/>"
    "<circle cx='45' cy='28' r='6' fill='%23FFFFFF'/>"
    "<text x='82' y='36' fill='%23FFFFFF' font-family='Cairo, sans-serif' font-size='18' font-weight='bold'>شركة تنمية الغذاء</text>"
    "<text x='82' y='55' fill='%23C194FB' font-family='Tajawal, sans-serif' font-size='11' letter-spacing='1'>FOOD DEVELOPMENT CO. | CRM &amp; FIELD INTEL</text>"
    "</svg>"
)

CSS_BASE = """
@page {
    size: A4 portrait;
    margin: 12mm 10mm 15mm 10mm;
    @top-center {
        content: "";
    }
    @bottom-left {
        content: "نظام المبيعات الذكي - شركة تنمية الغذاء";
        font-family: 'Cairo', sans-serif;
        font-size: 8pt;
        color: #718096;
    }
    @bottom-right {
        content: "صفحة " counter(page) " من " counter(pages);
        font-family: 'Cairo', sans-serif;
        font-size: 8pt;
        color: #718096;
    }
}

:root {
    --brand-primary: #3A056A;
    --brand-accent: #C194FB;
    --tint: #F5F0FC;
    --line: #E4D9F5;
    --alt: #FBF9FE;
    --text-main: #1A202C;
    --text-muted: #4A5568;
    --ok: #1E7A5A;
    --ok-bg: #EDF7F2;
    --ok-line: #B7DECD;
    --warn: #8A5D06;
    --warn-bg: #FDF6E7;
    --warn-line: #E8D4A0;
    --bad: #9E2222;
    --bad-bg: #FBEEEE;
    --bad-line: #E7C2C2;
}

body {
    direction: rtl;
    font-family: 'Cairo', 'Tajawal', sans-serif;
    color: var(--text-main);
    margin: 0;
    padding: 0;
    font-size: 9.5pt;
    line-height: 1.45;
}

.report-header {
    border-bottom: 2.5px solid var(--brand-primary);
    padding-bottom: 12px;
    margin-bottom: 16px;
    display: flex;
    justify-content: space-between;
    align-items: center;
}

.report-title-box h1 {
    margin: 0 0 4px 0;
    color: var(--brand-primary);
    font-size: 16pt;
    font-weight: 800;
}

.report-meta {
    font-size: 8.5pt;
    color: var(--text-muted);
}

.recipient-banner {
    background-color: var(--tint);
    border-right: 4px solid var(--brand-primary);
    padding: 8px 12px;
    margin-bottom: 16px;
    font-size: 9pt;
    display: flex;
    justify-content: space-between;
}

.kpi-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 10px;
    margin-bottom: 16px;
}

.kpi-card {
    background-color: var(--alt);
    border: 1px solid var(--line);
    border-radius: 6px;
    padding: 10px;
    text-align: center;
}

.kpi-card .val {
    font-size: 14pt;
    font-weight: bold;
    color: var(--brand-primary);
    margin-top: 4px;
}

.kpi-card .lbl {
    font-size: 8pt;
    color: var(--text-muted);
}

table.data-table {
    width: 100%;
    border-collapse: collapse;
    margin-bottom: 16px;
    font-size: 8.5pt;
}

table.data-table th {
    background-color: var(--brand-primary);
    color: #FFFFFF;
    text-align: right;
    padding: 7px 9px;
    font-weight: 600;
}

table.data-table td {
    padding: 6px 9px;
    border-bottom: 1px solid var(--line);
}

table.data-table tr:nth-child(even) {
    background-color: var(--alt);
}

.badge {
    display: inline-block;
    padding: 2px 7px;
    border-radius: 4px;
    font-size: 7.5pt;
    font-weight: bold;
}
.badge-ok { background: var(--ok-bg); color: var(--ok); border: 1px solid var(--ok-line); }
.badge-warn { background: var(--warn-bg); color: var(--warn); border: 1px solid var(--warn-line); }
.badge-bad { background: var(--bad-bg); color: var(--bad); border: 1px solid var(--bad-line); }

.ai-box {
    background-color: #FAF5FF;
    border: 1px dashed var(--brand-accent);
    border-radius: 6px;
    padding: 10px 14px;
    margin-bottom: 16px;
}
.ai-box-title {
    font-weight: bold;
    color: var(--brand-primary);
    font-size: 9pt;
    margin-bottom: 4px;
}
.page-break {
    page-break-before: always;
}
"""

REPORT_TEMPLATES = {
    # 01. بطاقة أداء المندوب الشامل وعائد التكلفة
    "01_rep_performance_scorecard.html": """
    <!DOCTYPE html>
    <html lang="ar" dir="rtl">
    <head><meta charset="utf-8"><style>{{ css }}</style></head>
    <body>
        <div class="report-header">
            <div class="report-title-box">
                <h1>بطاقة أداء المندوب وعائد التكلفة الميدانية</h1>
                <div class="report-meta">الموظف: {{ rep.name }} | الكود: {{ rep.employee_code }} | المنطقة: {{ rep.region }}</div>
            </div>
            <img src="{{ logo }}" alt="Logo" style="height: 48px;">
        </div>

        <div class="recipient-banner">
            <div><strong>توجيه التقرير:</strong> {{ report_recipient }}</div>
            <div><strong>تاريخ التوليد:</strong> {{ generated_at }}</div>
        </div>

        <div class="kpi-grid">
            <div class="kpi-card">
                <div class="lbl">المستهدف الشهري</div>
                <div class="val">{{ "{:,.0f}".format(rep.monthly_target) }} ر.س</div>
            </div>
            <div class="kpi-card">
                <div class="lbl">المبيعات المحققة</div>
                <div class="val">{{ "{:,.0f}".format(rep.achieved_sales) }} ر.س</div>
            </div>
            <div class="kpi-card">
                <div class="lbl">نسبة الإنجاز</div>
                <div class="val" style="color: {{ 'var(--ok)' if rep.achievement_rate >= 100 else 'var(--warn)' }}">{{ "%.1f"|format(rep.achievement_rate) }}%</div>
            </div>
            <div class="kpi-card">
                <div class="lbl">استهلاك الوقود الفعلي</div>
                <div class="val">{{ rep.fuel_liters }} / {{ rep.fuel_allowance_liters }} لتر</div>
            </div>
        </div>

        {% if include_samples %}
        <div style="font-weight: bold; color: var(--brand-primary); margin-bottom: 6px;">🧪 حركة العينات المجانية ومعدل التحويل لأوامر شراء</div>
        <table class="data-table">
            <thead>
                <tr>
                    <th>العميل</th>
                    <th>المنتج المسلّم</th>
                    <th>الكمية</th>
                    <th>حالة اعتماد الجودة</th>
                    <th>رقم أمر الشراء (PO)</th>
                    <th>القيمة المحققة</th>
                </tr>
            </thead>
            <tbody>
                {% for s in samples %}
                <tr>
                    <td>{{ s.customer_name }}</td>
                    <td>{{ s.product_name }}</td>
                    <td>{{ s.qty_free }} وحدة</td>
                    <td>
                        <span class="badge {% if s.status == 'APPROVED' %}badge-ok{% elif s.status == 'REJECTED' %}badge-bad{% else %}badge-warn{% endif %}">
                            {{ s.status }}
                        </span>
                    </td>
                    <td>{{ s.converted_po_id or '—' }}</td>
                    <td>{{ "{:,.2f}".format(s.po_value) if s.po_value else '0.00' }} ر.س</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
        {% endif %}

        {% if include_sla %}
        <div style="font-weight: bold; color: var(--brand-primary); margin-bottom: 6px;">⚡ التفاعل مع مجموعات الواتساب والمكالمات الخارجية الميدانية</div>
        <table class="data-table">
            <thead>
                <tr>
                    <th>المجموعة / العميل</th>
                    <th>متوسط زمن الرد (SLA)</th>
                    <th>مكالمات هاتفية خارج الواتساب</th>
                    <th>الالتزام ببروتوكول الرد</th>
                </tr>
            </thead>
            <tbody>
                {% for log in sla_summary %}
                <tr>
                    <td>{{ log.customer_name }}</td>
                    <td>{{ log.avg_response_minutes }} دقيقة</td>
                    <td>{{ log.phone_call_count }} مكالمة موثقة</td>
                    <td>
                        <span class="badge {% if log.avg_response_minutes <= 15 %}badge-ok{% else %}badge-warn{% endif %}">
                            {% if log.avg_response_minutes <= 15 %}ممتاز (أقل من 15 د){% else %}يحتاج تسريع{% endif %}
                        </span>
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
        {% endif %}

        <div class="ai-box">
            <div class="ai-box-title">👔 توصية المدير الذكي الميداني (Claude 3.7 Intelligence):</div>
            <div>{{ ai_recommendation }}</div>
        </div>
    </body>
    </html>
    """,

    # 02. التقرير التنفيذي العام لإدارة المبيعات والتسويق
    "02_executive_sales_report.html": """
    <!DOCTYPE html>
    <html lang="ar" dir="rtl">
    <head><meta charset="utf-8"><style>{{ css }}</style></head>
    <body>
        <div class="report-header">
            <div class="report-title-box">
                <h1>التقرير التنفيذي الشامل لإدارة المبيعات والعمليات</h1>
                <div class="report-meta">نطاق التقرير: الإدارة العامة وفروع التوزيع | الفترة الحالية</div>
            </div>
            <img src="{{ logo }}" alt="Logo" style="height: 48px;">
        </div>

        <div class="recipient-banner">
            <div><strong>توجيه:</strong> {{ report_recipient }}</div>
            <div><strong>إجمالي المبيعات المحققة:</strong> {{ "{:,.0f}".format(total_revenue) }} ر.س</div>
            <div><strong>كفاءة التكلفة الإجمالية:</strong> {{ cost_to_sales_ratio }}%</div>
        </div>

        <div style="font-weight: bold; color: var(--brand-primary); margin-bottom: 6px;">📊 كشف أداء مناديب المبيعات الميدانيين مقارنة بالمستهدف</div>
        <table class="data-table">
            <thead>
                <tr>
                    <th>المندوب</th>
                    <th>المنطقة</th>
                    <th>المستهدف</th>
                    <th>المحقق</th>
                    <th>نسبة الإنجاز</th>
                    <th>تكلفة الوقود والضيافة</th>
                    <th>تصنيف الكفاءة</th>
                </tr>
            </thead>
            <tbody>
                {% for r in reps_performance %}
                <tr>
                    <td><strong>{{ r.name }}</strong></td>
                    <td>{{ r.region }}</td>
                    <td>{{ "{:,.0f}".format(r.monthly_target) }} ر.س</td>
                    <td>{{ "{:,.0f}".format(r.achieved_sales) }} ر.س</td>
                    <td><strong>{{ "%.1f"|format(r.achievement_rate) }}%</strong></td>
                    <td>{{ "{:,.0f}".format(r.total_expenses) }} ر.س</td>
                    <td>
                        <span class="badge {% if r.efficiency == 'عالي الكفاءة' %}badge-ok{% elif r.efficiency == 'مقبول' %}badge-warn{% else %}badge-bad{% endif %}">
                            {{ r.efficiency }}
                        </span>
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>

        {% if include_stagnant %}
        <div style="font-weight: bold; color: var(--bad); margin-bottom: 6px;">⚠️ حسابات العملاء الراكدة (تتطلب تدخلاً عاجلاً)</div>
        <table class="data-table">
            <thead>
                <tr>
                    <th>اسم الحساب</th>
                    <th>المندوب المسؤول</th>
                    <th>القطاع</th>
                    <th>آخر تفاعل / زيارة</th>
                    <th>الإجراء التنفيذي المطلوب</th>
                </tr>
            </thead>
            <tbody>
                {% for act in stagnant_accounts %}
                <tr>
                    <td>{{ act.company_name }}</td>
                    <td>{{ act.rep_name }}</td>
                    <td>{{ act.sector }}</td>
                    <td>{{ act.last_activity }}</td>
                    <td style="color: var(--bad);">{{ act.action_required }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
        {% endif %}

        <div class="ai-box">
            <div class="ai-box-title">🧠 ملخص الرؤى الاستراتيجية وهدر الموارد (Sales Swarm Analytics):</div>
            <div>{{ strategic_summary }}</div>
        </div>
    </body>
    </html>
    """,

    # 03. ملف مراجعة ومتابعة حساب العميل الرئيسي
    "03_key_account_review.html": """
    <!DOCTYPE html>
    <html lang="ar" dir="rtl">
    <head><meta charset="utf-8"><style>{{ css }}</style></head>
    <body>
        <div class="report-header">
            <div class="report-title-box">
                <h1>ملف المراجعة الاستراتيجية لحساب رئيسي (Key Account)</h1>
                <div class="report-meta">الشركة: {{ account.company_name }} | التصنيف: Tier-{{ account.tier }} | القطاع: {{ account.sector }}</div>
            </div>
            <img src="{{ logo }}" alt="Logo" style="height: 48px;">
        </div>

        <div class="recipient-banner">
            <div><strong>المسؤول:</strong> {{ account.contact_person }} ({{ account.phone }})</div>
            <div><strong>المندوب المشرف:</strong> {{ account.rep_name }}</div>
            <div><strong>حالة الحساب:</strong> {{ account.status }}</div>
        </div>

        <div class="kpi-grid">
            <div class="kpi-card">
                <div class="lbl">مجموع العينات الموردة</div>
                <div class="val">{{ samples_summary.total_samples }} منتج</div>
            </div>
            <div class="kpi-card">
                <div class="lbl">العينات المعتمدة مخبرياً</div>
                <div class="val" style="color: var(--ok)">{{ samples_summary.approved }}</div>
            </div>
            <div class="kpi-card">
                <div class="lbl">طلبات الشراء المرتبطة</div>
                <div class="val">{{ samples_summary.po_count }}</div>
            </div>
            <div class="kpi-card">
                <div class="lbl">القيمة الإجمالية للتعاقد</div>
                <div class="val">{{ "{:,.0f}".format(samples_summary.total_po_value) }} ر.س</div>
            </div>
        </div>

        <div style="font-weight: bold; color: var(--brand-primary); margin-bottom: 6px;">💬 آخر المحادثات والمكالمات المرصودة عبر نظام Sentinel</div>
        <table class="data-table">
            <thead>
                <tr>
                    <th>الوقت</th>
                    <th>المرسل</th>
                    <th>نوع الرسالة</th>
                    <th>نص الرسالة / ملخص المكالمة</th>
                </tr>
            </thead>
            <tbody>
                {% for log in recent_logs %}
                <tr>
                    <td style="white-space: nowrap;">{{ log.created_at }}</td>
                    <td><strong>{{ log.sender_name }}</strong></td>
                    <td>
                        <span class="badge {% if log.is_external_call %}badge-warn{% else %}badge-ok{% endif %}">
                            {{ 'اتصال هاتفي' if log.is_external_call else 'واتساب' }}
                        </span>
                    </td>
                    <td>{{ log.message_body }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </body>
    </html>
    """,

    # 04. الجدول التشغيلي للمهام الميدانية والزيارات
    "04_field_operations_sheet.html": """
    <!DOCTYPE html>
    <html lang="ar" dir="rtl">
    <head><meta charset="utf-8"><style>{{ css }}</style></head>
    <body>
        <div class="report-header">
            <div class="report-title-box">
                <h1>جدول العمليات والتحركات الميدانية المعتمدة</h1>
                <div class="report-meta">النطاق الزمني: {{ schedule_period }} | خطوط السير اليومية</div>
            </div>
            <img src="{{ logo }}" alt="Logo" style="height: 48px;">
        </div>

        <div class="recipient-banner">
            <div><strong>توجيه النسخة:</strong> {{ report_recipient }}</div>
            <div><strong>إجمالي العمليات المجدولة:</strong> {{ events|length }} مهمة</div>
        </div>

        <table class="data-table">
            <thead>
                <tr>
                    <th>الموعد</th>
                    <th>المندوب</th>
                    <th>العميل</th>
                    <th>نوع المهمة</th>
                    <th>رمز المسار والموقع</th>
                    <th>تأكيد الموظف (Ack)</th>
                    <th>حالة التنفيذ</th>
                </tr>
            </thead>
            <tbody>
                {% for ev in events %}
                <tr>
                    <td>{{ ev.scheduled_at }}</td>
                    <td>{{ ev.rep_name }}</td>
                    <td><strong>{{ ev.customer_name }}</strong></td>
                    <td>{{ ev.task_type }}</td>
                    <td>{{ ev.route_code }} - {{ ev.location }}</td>
                    <td>
                        <span class="badge {% if ev.ack_status %}badge-ok{% else %}badge-warn{% endif %}">
                            {{ 'تم الاستلام' if ev.ack_status else 'بانتظار التأكيد' }}
                        </span>
                    </td>
                    <td>
                        <span class="badge {% if ev.execution_status == 'DONE' %}badge-ok{% elif ev.execution_status == 'MISSED' %}badge-bad{% else %}badge-warn{% endif %}">
                            {{ ev.execution_status }}
                        </span>
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </body>
    </html>
    """,

    # 05. مذكرة الإحالة الفورية وتسليم المهام (صفحة واحدة ملخصة)
    "05_task_handover_brief.html": """
    <!DOCTYPE html>
    <html lang="ar" dir="rtl">
    <head>
        <meta charset="utf-8">
        <style>
            {{ css }}
            @page { size: A4 portrait; margin: 10mm 10mm 10mm 10mm; }
            body { font-size: 9pt; }
        </style>
    </head>
    <body>
        <div class="report-header" style="margin-bottom: 10px; padding-bottom: 8px;">
            <div class="report-title-box">
                <h1 style="font-size: 14pt; margin: 0;">⚡ مذكرة إحالة فورية وتسليم ملف عميل (Handover Brief)</h1>
                <div class="report-meta">حالة الإحالة: طارئة / نافذة المفعول فوراً</div>
            </div>
            <img src="{{ logo }}" alt="Logo" style="height: 42px;">
        </div>

        <div class="recipient-banner" style="margin-bottom: 10px; padding: 6px 10px;">
            <div><strong>من الموظف:</strong> {{ handover.from_rep_name }}</div>
            <div><strong>إلى الموظف المستلم:</strong> {{ handover.to_rep_name }}</div>
            <div><strong>العميل المعني:</strong> {{ handover.customer_name }}</div>
            <div><strong>مستوى الأولوية:</strong> <span class="badge badge-bad">{{ handover.priority }}</span></div>
        </div>

        <div style="background-color: var(--alt); border: 1px solid var(--line); border-radius: 6px; padding: 10px; margin-bottom: 10px;">
            <div style="font-weight: bold; color: var(--brand-primary); margin-bottom: 4px;">🎯 ما تم الاتفاق عليه مؤخراً مع العميل:</div>
            <div>{{ handover.last_agreement_summary }}</div>
        </div>

        <div style="background-color: var(--tint); border: 1px solid var(--brand-accent); border-radius: 6px; padding: 10px; margin-bottom: 10px;">
            <div style="font-weight: bold; color: var(--brand-primary); margin-bottom: 4px;">🔍 ما يريده العميل الآن بالضبط ودواعي الإحالة:</div>
            <div><strong>السبب المباشر:</strong> {{ handover.reason }}</div>
            <div><strong>المتطلب الحرج:</strong> {{ handover.current_client_demand }}</div>
        </div>

        <div style="font-weight: bold; color: var(--brand-primary); margin-bottom: 4px;">⏱️ خطة العمل الإلزامية خلال (24 - 48 ساعة القادمة):</div>
        <table class="data-table" style="margin-bottom: 10px;">
            <thead>
                <tr>
                    <th style="width: 15%;">التوقيت</th>
                    <th style="width: 55%;">الإجراء المطلوب</th>
                    <th style="width: 30%;">المسؤول الميداني</th>
                </tr>
            </thead>
            <tbody>
                {% for act in handover.urgent_action_plan %}
                <tr>
                    <td>{{ act.timeframe }}</td>
                    <td><strong>{{ act.action }}</strong></td>
                    <td>{{ handover.to_rep_name }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>

        <div style="font-size: 8pt; color: var(--text-muted); border-top: 1px solid var(--line); padding-top: 6px; display: flex; justify-content: space-between;">
            <div>نظام إدارة المبيعات الذكي - توثيق آلي برعاية AI Swarm Handover Agent</div>
            <div>اعتماد الإدارة: ___________________</div>
        </div>
    </body>
    </html>
    """
}

jinja_env = Environment(loader=DictLoader(REPORT_TEMPLATES), autoescape=True)

def render_report_html(template_name: str, context_data: dict) -> str:
    """يدمج بيانات السياق داخل قالب Jinja2 مع حقن الهوية والـ CSS الموحد."""
    template = jinja_env.get_template(template_name)
    payload = {
        **context_data,
        "css": CSS_BASE,
        "logo": COMPANY_LOGO_BASE64,
        "report_recipient": context_data.get("report_recipient", "سعادة المدير العام / مدير المبيعات"),
        "generated_at": context_data.get("generated_at", "2026-09-03")
    }
    return template.render(**payload)

def generate_report_pdf(template_name: str, context_data: dict) -> bytes:
    """يقوم بتوليد ملف PDF عالي الجودة متوافق مع WeasyPrint بناءً على القالب المختار."""
    html_content = render_report_html(template_name, context_data)
    return HTML(string=html_content).write_pdf()
