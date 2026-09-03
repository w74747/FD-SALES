"""
sales_reports_engine.py
محرك التقارير التنفيذية والرسمية لشركة تنمية الغذاء (Food Development Company)
يعتمد Jinja2 و WeasyPrint لتوليد مستندات PDF فائقة الجودة.
"""

import os
from jinja2 import Environment, DictLoader
from weasyprint import HTML

# استيراد الشعار الفعلي الموحد لشركة تنمية الغذاء
try:
    from logo_data import LOGO_BASE64
    if LOGO_BASE64.startswith("data:image"):
        COMPANY_LOGO_SRC = LOGO_BASE64
    else:
        COMPANY_LOGO_SRC = f"data:image/png;base64,{LOGO_BASE64}"
except ImportError:
    COMPANY_LOGO_SRC = (
        "data:image/svg+xml;utf8,"
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 320 80' width='320' height='80'>"
        "<path d='M 40,12 A 28,28 0 1,1 12,40' fill='none' stroke='%233A056A' stroke-width='10' stroke-linecap='round'/>"
        "<text x='82' y='36' fill='%233A056A' font-family='Cairo, sans-serif' font-size='18' font-weight='900'>شركة تنمية الغذاء</text>"
        "<text x='82' y='56' fill='%237E22CE' font-family='Cairo, sans-serif' font-size='11' font-weight='700'>Food Development Company</text>"
        "</svg>"
    )

CSS_BASE = """
@page {
    size: A4 portrait;
    margin: 10mm 10mm 12mm 10mm;
    @bottom-left {
        content: "نظام المبيعات التنفيذي - شركة تنمية الغذاء";
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
    font-size: 8.5pt;
    line-height: 1.4;
}

.report-header {
    border-bottom: 2px solid var(--brand-primary);
    padding-bottom: 8px;
    margin-bottom: 12px;
    display: flex;
    justify-content: space-between;
    align-items: center;
}

.report-title-box h1 {
    margin: 0 0 4px 0;
    color: var(--brand-primary);
    font-size: 15pt;
    font-weight: 800;
}

.report-meta {
    font-size: 8pt;
    color: var(--text-muted);
}

.recipient-banner {
    background-color: var(--tint);
    border-right: 4px solid var(--brand-primary);
    padding: 6px 10px;
    margin-bottom: 12px;
    font-size: 8.5pt;
    display: flex;
    justify-content: space-between;
}

.kpi-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 8px;
    margin-bottom: 12px;
}

.kpi-card {
    background-color: var(--alt);
    border: 1px solid var(--line);
    border-radius: 6px;
    padding: 8px;
    text-align: center;
}

.kpi-card .val {
    font-size: 12pt;
    font-weight: bold;
    color: var(--brand-primary);
    margin-top: 3px;
}

.kpi-card .lbl {
    font-size: 7.5pt;
    color: var(--text-muted);
}

table.data-table {
    width: 100%;
    border-collapse: collapse;
    margin-bottom: 12px;
    font-size: 8pt;
    table-layout: fixed;
}

table.data-table th {
    background-color: var(--brand-primary);
    color: #FFFFFF;
    text-align: right;
    padding: 6px 8px;
    font-weight: 700;
    white-space: nowrap;
}

table.data-table td {
    padding: 5px 8px;
    border-bottom: 1px solid var(--line);
    word-break: break-word;
}

table.data-table tr:nth-child(even) {
    background-color: var(--alt);
}

.badge {
    display: inline-block;
    padding: 1.5px 6px;
    border-radius: 4px;
    font-size: 7pt;
    font-weight: bold;
}
.badge-ok { background: var(--ok-bg); color: var(--ok); border: 1px solid var(--ok-line); }
.badge-warn { background: var(--warn-bg); color: var(--warn); border: 1px solid var(--warn-line); }
.badge-bad { background: var(--bad-bg); color: var(--bad); border: 1px solid var(--bad-line); }

.ai-box {
    background-color: #FAF5FF;
    border: 1px dashed var(--brand-accent);
    border-radius: 6px;
    padding: 8px 12px;
    margin-bottom: 12px;
}
.ai-box-title {
    font-weight: bold;
    color: var(--brand-primary);
    font-size: 8.5pt;
    margin-bottom: 4px;
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
                <h1>بطاقة أداء مسؤول المبيعات وعائد التكلفة</h1>
                <div class="report-meta">الموظف: {{ rep.name }} | الكود: {{ rep.employee_code }} | المنطقة: {{ rep.region }}</div>
            </div>
            <img src="{{ logo }}" alt="Logo" style="max-height: 48px; max-width: 220px; object-fit: contain;">
        </div>

        <div class="recipient-banner">
            <div><strong>توجيه التقرير:</strong> {{ report_recipient }}</div>
            <div><strong>تاريخ التوليد:</strong> {{ generated_at }}</div>
        </div>

        <div class="kpi-grid">
            <div class="kpi-card">
                <div class="lbl">المستهدف الشهري</div>
                <div class="val">{{ "{:,.1f}".format(rep.monthly_target) }} ر.ع</div>
            </div>
            <div class="kpi-card">
                <div class="lbl">المبيعات المحققة</div>
                <div class="val">{{ "{:,.1f}".format(rep.achieved_sales) }} ر.ع</div>
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
                    <th style="width: 25%;">العميل</th>
                    <th style="width: 25%;">المنتج المسلّم</th>
                    <th style="width: 12%;">الكمية</th>
                    <th style="width: 13%;">حالة الاعتماد</th>
                    <th style="width: 12%;">أمر الشراء (PO)</th>
                    <th style="width: 13%;">القيمة المحققة</th>
                </tr>
            </thead>
            <tbody>
                {% for s in samples %}
                <tr>
                    <td><strong>{{ s.customer_name }}</strong></td>
                    <td>{{ s.product_name }}</td>
                    <td>{{ s.qty_free }} وحدة</td>
                    <td>
                        <span class="badge {% if s.status == 'APPROVED' %}badge-ok{% elif s.status == 'REJECTED' %}badge-bad{% else %}badge-warn{% endif %}">
                            {{ s.status }}
                        </span>
                    </td>
                    <td>{{ s.converted_po_id or '—' }}</td>
                    <td>{{ "{:,.1f}".format(s.po_value) if s.po_value else '0.0' }} ر.ع</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
        {% endif %}

        <div class="ai-box">
            <div class="ai-box-title">👔 التوصية التنفيذية والإدارية المعتمدة:</div>
            <div>{{ ai_recommendation }}</div>
        </div>
    </body>
    </html>
    """,

    # 02. التقرير التنفيذي العام لإدارة المبيعات
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
            <img src="{{ logo }}" alt="Logo" style="max-height: 48px; max-width: 220px; object-fit: contain;">
        </div>

        <div class="recipient-banner">
            <div><strong>توجيه المستند:</strong> {{ report_recipient }}</div>
            <div><strong>إجمالي المبيعات المحققة:</strong> {{ "{:,.1f}".format(total_revenue) }} ر.ع</div>
            <div><strong>نسبة كفاءة التكلفة:</strong> {{ cost_to_sales_ratio }}%</div>
        </div>

        <div style="font-weight: bold; color: var(--brand-primary); margin-bottom: 6px;">📊 كشف إنجازات فريق المبيعات التنفيذي الميداني</div>
        <table class="data-table">
            <thead>
                <tr>
                    <th>المسؤول</th>
                    <th>المنطقة</th>
                    <th>المستهدف</th>
                    <th>المحقق</th>
                    <th>نسبة الإنجاز</th>
                    <th>المصاريف</th>
                    <th>تصنيف الكفاءة</th>
                </tr>
            </thead>
            <tbody>
                {% for r in reps_performance %}
                <tr>
                    <td><strong>{{ r.name }}</strong></td>
                    <td>{{ r.region }}</td>
                    <td>{{ "{:,.1f}".format(r.monthly_target) }} ر.ع</td>
                    <td>{{ "{:,.1f}".format(r.achieved_sales) }} ر.ع</td>
                    <td><strong>{{ "%.1f"|format(r.achievement_rate) }}%</strong></td>
                    <td>{{ "{:,.1f}".format(r.total_expenses) }} ر.ع</td>
                    <td>
                        <span class="badge {% if r.efficiency == 'عالي الكفاءة' %}badge-ok{% elif r.efficiency == 'مقبول' %}badge-warn{% else %}badge-bad{% endif %}">
                            {{ r.efficiency }}
                        </span>
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>

        <div class="ai-box">
            <div class="ai-box-title">🧠 ملخص الرؤى الاستراتيجية والتحليل الميداني:</div>
            <div>{{ strategic_summary }}</div>
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
        "logo": COMPANY_LOGO_SRC,
        "report_recipient": context_data.get("report_recipient", "سعادة رئيس مجلس الإدارة / المدير العام"),
        "generated_at": context_data.get("generated_at", "2026-09-03")
    }
    return template.render(**payload)

def generate_report_pdf(template_name: str, context_data: dict) -> bytes:
    """يقوم بتوليد ملف PDF متوافق مع WeasyPrint بناءً على القالب المختار."""
    html_content = render_report_html(template_name, context_data)
    return HTML(string=html_content).write_pdf()
