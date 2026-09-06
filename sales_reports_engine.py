"""
sales_reports_engine.py
محرك التقارير التنفيذية والرسمية لشركة تنمية الغذاء (Food Development Company)
يعتمد Jinja2 لتوليد مستندات رسمية متوافقة مع معيار الطباعة A4 بالريال العماني دون استخدام رموز تعبيرية.
"""

from jinja2 import Environment, DictLoader

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
    --text-main: #1E293B;
    --text-muted: #64748B;
    --ok: #059669;
    --ok-bg: #ECFDF5;
    --ok-line: #A7F3D0;
    --warn: #D97706;
    --warn-bg: #FFFBEB;
    --warn-line: #FDE68A;
    --bad: #DC2626;
    --bad-bg: #FEF2F2;
    --bad-line: #FECACA;
}

body {
    direction: rtl;
    font-family: 'Cairo', sans-serif;
    color: var(--text-main);
    margin: 0;
    padding: 15mm;
    font-size: 9pt;
    line-height: 1.5;
    background: #FFFFFF;
}

.report-header {
    border-bottom: 2px solid var(--brand-primary);
    padding-bottom: 12px;
    margin-bottom: 16px;
    display: flex;
    justify-content: space-between;
    align-items: center;
}

.report-title-box h1 {
    margin: 0 0 6px 0;
    color: var(--brand-primary);
    font-size: 16pt;
    font-weight: 900;
}

.report-meta {
    font-size: 8.5pt;
    color: var(--text-muted);
    font-weight: 500;
}

.recipient-banner {
    background-color: var(--tint);
    border-right: 4px solid var(--brand-primary);
    padding: 8px 12px;
    margin-bottom: 16px;
    font-size: 9pt;
    display: flex;
    justify-content: space-between;
    border-radius: 4px;
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
    border-radius: 8px;
    padding: 10px;
    text-align: center;
}

.kpi-card .val {
    font-size: 13pt;
    font-weight: 800;
    color: var(--brand-primary);
    margin-top: 4px;
}

.kpi-card .lbl {
    font-size: 8pt;
    color: var(--text-muted);
    font-weight: 600;
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
    padding: 8px 10px;
    font-weight: 700;
    white-space: nowrap;
}

table.data-table td {
    padding: 8px 10px;
    border-bottom: 1px solid var(--line);
    word-break: break-word;
}

table.data-table tr:nth-child(even) {
    background-color: var(--alt);
}

.badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 7.5pt;
    font-weight: 700;
}
.badge-ok { background: var(--ok-bg); color: var(--ok); border: 1px solid var(--ok-line); }
.badge-warn { background: var(--warn-bg); color: var(--warn); border: 1px solid var(--warn-line); }
.badge-bad { background: var(--bad-bg); color: var(--bad); border: 1px solid var(--bad-line); }

.ai-box {
    background-color: #FAF5FF;
    border: 1px dashed var(--brand-accent);
    border-radius: 8px;
    padding: 12px 16px;
    margin-bottom: 16px;
}
.ai-box-title {
    font-weight: 800;
    color: var(--brand-primary);
    font-size: 9.5pt;
    margin-bottom: 6px;
}

.print-btn-bar {
    text-align: center;
    margin-bottom: 20px;
}
.print-btn {
    background-color: #3A056A;
    color: #FFFFFF;
    border: none;
    padding: 8px 20px;
    border-radius: 6px;
    font-family: 'Cairo', sans-serif;
    font-weight: 700;
    cursor: pointer;
}

@media print {
    .print-btn-bar { display: none !important; }
    body { padding: 0 !important; }
}
"""

REPORT_TEMPLATES = {
    "02_executive_sales_report.html": """
    <!DOCTYPE html>
    <html lang="ar" dir="rtl">
    <head>
        <meta charset="utf-8">
        <title>تقرير المبيعات التنفيذي المعتمد</title>
        <link href="https://fonts.googleapis.com/css2?family=Cairo:wght@400;600;700;800;900&display=swap" rel="stylesheet">
        <style>{{ css }}</style>
    </head>
    <body>
        <div class="print-btn-bar">
            <button onclick="window.print()" class="print-btn">طباعة المستند الرسمي (A4)</button>
        </div>

        <div class="report-header">
            <div class="report-title-box">
                <h1>التقرير التنفيذي الشامل لإدارة المبيعات والعمليات</h1>
                <div class="report-meta">نطاق التقرير: الإدارة العامة وفروع التوزيع الميدانية | شركة تنمية الغذاء</div>
            </div>
            
            <div style="display: flex; align-items: center; gap: 10px;">
                <img src="/logo.png" alt="شركة تنمية الغذاء" style="max-height: 52px; width: auto; display: block;" onerror="this.style.display='none'">
                <div style="text-align: right;">
                    <div style="font-size: 16pt; font-weight: 900; color: #3A056A; line-height: 1.1;">شركة تنمية الغذاء</div>
                    <div style="font-size: 8pt; font-weight: 700; color: #7E22CE; letter-spacing: 1px;">FOOD DEVELOPMENT COMPANY</div>
                </div>
            </div>
        </div>

        <div class="recipient-banner">
            <div><strong>توجيه المستند:</strong> {{ report_recipient }}</div>
            <div><strong>إجمالي المبيعات المحققة:</strong> {{ "{:,.1f}".format(total_revenue) }} ر.ع</div>
            <div><strong>نسبة المصروف للمبيعات:</strong> {{ cost_to_sales_ratio }}%</div>
        </div>

        <div style="font-weight: bold; color: var(--brand-primary); margin-bottom: 8px; font-size: 10pt;">
            كشف إنجازات فريق المبيعات التنفيذي الميداني
        </div>
        <table class="data-table">
            <thead>
                <tr>
                    <th>المسؤول الميداني</th>
                    <th>المنطقة البيعية</th>
                    <th>المستهدف الشهري</th>
                    <th>المبيعات المحققة</th>
                    <th>نسبة الإنجاز</th>
                    <th>إجمالي المصاريف</th>
                    <th>كفاءة الأداء</th>
                </tr>
            </thead>
            <tbody>
                {% for r in reps_performance %}
                <tr>
                    <td><strong>{{ r.name }}</strong></td>
                    <td>{{ r.region }}</td>
                    <td>{{ "{:,.1f}".format(r.monthly_target) }} ر.ع</td>
                    <td style="color: var(--ok); font-weight: bold;">{{ "{:,.1f}".format(r.achieved_sales) }} ر.ع</td>
                    <td><strong>{{ "%.1f"|format(r.achievement_rate) }}%</strong></td>
                    <td style="color: var(--warn); font-weight: bold;">{{ "{:,.1f}".format(r.total_expenses) }} ر.ع</td>
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
            <div class="ai-box-title">التوصية الإدارية والتحليل الميداني المعتمد:</div>
            <div>{{ strategic_summary }}</div>
        </div>
    </body>
    </html>
    """
}

jinja_env = Environment(loader=DictLoader(REPORT_TEMPLATES), autoescape=True)

def render_report_html(template_name: str, context_data: dict) -> str:
    template = jinja_env.get_template(template_name)
    payload = {
        **context_data,
        "css": CSS_BASE,
        "report_recipient": context_data.get("report_recipient", "سعادة رئيس مجلس الإدارة / المدير العام"),
        "generated_at": context_data.get("generated_at", "2026-09-07")
    }
    return template.render(**payload)
