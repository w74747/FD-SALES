"""
لغة رسائل البوت لكل جهة: العميل، عضو فريق المبيعات، مسؤول النظام، إدخال البيانات، اللوجستيك.
التواصل في الغالب بالإنجليزية (الافتراضي)، والعربية متاحة لأي جهة من لوحة التحكم.
للعميل خيار إضافي «auto»: الرد بنفس لغة رسالة العميل.
"""
import re
from datetime import date

LANGS = ("en", "ar")
DEFAULT = "en"
WEEKDAYS_AR = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]
_ARABIC = re.compile(r"[؀-ۿ]")


def norm(lang, default: str = DEFAULT) -> str:
    lang = (lang or "").strip().lower()
    return lang if lang in LANGS else default


def has_arabic(text: str) -> bool:
    return bool(_ARABIC.search(text or ""))


def customer_lang(customer: dict, detected: str = "") -> str:
    """لغة العميل المحددة في ملفه، أو (تلقائي) لغة رسالته الأخيرة"""
    forced = (customer or {}).get("language")
    if forced in LANGS:
        return forced
    return norm(detected, DEFAULT)


def fmt_date(d: date, lang: str) -> str:
    return f"{WEEKDAYS_AR[d.weekday()]} {d.strftime('%d/%m/%Y')}" if norm(lang) == "ar" else d.strftime("%a %d/%m/%Y")


M = {
    # ---------- رسالة الطلب (اللوجستيك وإدخال البيانات) ----------
    "new_order": {"en": "📦 *NEW ORDER*", "ar": "📦 *طلب جديد*"},
    "order_update": {"en": "🔄 *ORDER UPDATE*", "ar": "🔄 *تحديث طلب*"},
    "order_cancelled": {"en": "🚫 *ORDER CANCELLED*", "ar": "🚫 *إلغاء طلب*"},
    "delivery": {"en": "🗓 *Delivery:*", "ar": "🗓 *التسليم:*"},
    "total": {"en": "*Total:*", "ar": "*الإجمالي:*"},
    "changes": {"en": "*Changes:*", "ar": "*التغييرات:*"},
    "full_now": {"en": "*Full order now:*", "ar": "*الطلب كاملاً الآن:*"},
    "removed": {"en": "removed", "ar": "حُذف"},
    "ref": {"en": "Ref:", "ar": "المرجع:"},
    "pcs": {"en": "pcs", "ar": "حبة"},
    "kg": {"en": "kg", "ar": "كجم"},
    "cancel_note_logistics": {"en": "⚠️ The customer cancelled/removed this order. Please hold and do not dispatch.",
                              "ar": "⚠️ ألغى العميل هذا الطلب. يرجى إيقاف التجهيز وعدم الإرسال."},
    "cancel_note_entry": {"en": "⚠️ The customer cancelled this order. Please cancel it in the system.",
                          "ar": "⚠️ ألغى العميل هذا الطلب. يرجى إلغاؤه في النظام."},
    "order_no": {"en": "🧾 *Order No:* {no}", "ar": "🧾 *رقم الطلب:* {no}"},
    "approved_tag": {"en": "✅ *APPROVED*", "ar": "✅ *معتمد*"},
    "cancel_confirmed_tag": {"en": "✅ *CANCELLATION CONFIRMED*", "ar": "✅ *تم تأكيد الإلغاء*"},
    "for_entry": {"en": "📝 *For system entry* — {kind}", "ar": "📝 *للإدخال في النظام* — {kind}"},
    "kind_new": {"en": "New order", "ar": "طلب جديد"},
    "kind_update": {"en": "Order update", "ar": "تحديث طلب"},
    "kind_cancel": {"en": "Cancellation", "ar": "إلغاء طلب"},
    "instr_new": {"en": "↩️ After entering the order, *reply to this message* with the order number. "
                        "After finance approval, reply \"approved\" (or both together: 12345 approved).",
                  "ar": "↩️ بعد إدخال الطلب *ردّ على هذه الرسالة* برقم الطلب، وبعد اعتماد المالية ردّ بكلمة «معتمد» "
                        "(أو الاثنين معاً: 12345 معتمد)."},
    "instr_update": {"en": "↩️ Update the order in the system, then after finance approval *reply to this message* with \"approved\".",
                     "ar": "↩️ عدّل الطلب في النظام، وبعد اعتماد المالية *ردّ على هذه الرسالة* بكلمة «معتمد»."},
    "instr_cancel": {"en": "↩️ Cancel the order in the system, then *reply to this message* with \"done\".",
                     "ar": "↩️ ألغِ الطلب في النظام ثم *ردّ على هذه الرسالة* بكلمة «تم»."},

    # ---------- ردود البوت على موظف إدخال البيانات ----------
    "st_awaiting": {"en": "awaiting entry", "ar": "بانتظار الإدخال"},
    "st_entered": {"en": "entered — awaiting finance", "ar": "مُدخل — بانتظار المالية"},
    "st_approved": {"en": "approved and sent to logistics", "ar": "معتمد ومرسل للوجستيك"},
    "st_rejected": {"en": "not approved", "ar": "مرفوض"},
    "st_superseded": {"en": "replaced by a newer update", "ar": "استُبدل بتحديث أحدث"},
    "de_which": {"en": "⚠️ I couldn't tell which order you mean. Please *reply to the order message itself*.\nOpen orders:\n{lst}",
                 "ar": "⚠️ لم أعرف أي طلب تقصد. يرجى *الرد على رسالة الطلب نفسها*.\nالطلبات المفتوحة:\n{lst}"},
    "de_already": {"en": "ℹ️ {tag}: already approved and sent to logistics (No. {no}).",
                   "ar": "ℹ️ {tag}: هذا الطلب معتمد ومرسل للوجستيك مسبقاً (رقم {no})."},
    "de_state": {"en": "ℹ️ {tag}: this order is \"{state}\" — no action needed.",
                 "ar": "ℹ️ {tag}: حالة هذا الطلب «{state}»، لا حاجة لإجراء."},
    "de_need_number": {"en": "⚠️ {tag}: please send the *order number* from the system with the approval (e.g. 12345 approved), replying to the order message.",
                       "ar": "⚠️ {tag}: يرجى إرسال *رقم الطلب* في النظام مع الاعتماد (مثال: 12345 معتمد) بالرد على رسالة الطلب."},
    "de_entered": {"en": "📝 {tag}: order number *{no}* recorded — awaiting finance approval.\nWhen approved, reply to the order message with \"approved\".",
                   "ar": "📝 {tag}: سُجّل رقم الطلب *{no}* — بانتظار اعتماد المالية.\nعند الاعتماد ردّ على رسالة الطلب بكلمة «معتمد»."},
    "de_unclear": {"en": "ℹ️ {tag}: I didn't understand. Send the order number, \"approved\" after finance approval, or \"rejected\".",
                   "ar": "ℹ️ {tag}: لم أفهم الرد. أرسل رقم الطلب، أو «معتمد» بعد اعتماد المالية، أو «مرفوض»."},
    "de_newer": {"en": "ℹ️ {tag}: a newer update of this order (FD-{newer}) was already approved and sent to logistics, so this version isn't needed.",
                 "ar": "ℹ️ {tag}: تم اعتماد تحديث أحدث لهذا الطلب (FD-{newer}) وأُرسل للوجستيك، فلا حاجة لإرسال هذه النسخة."},
    "de_cancel_ok": {"en": "✅ {tag}: cancellation confirmed.", "ar": "✅ {tag}: تم تأكيد الإلغاء."},
    "de_cancel_ok_log": {"en": "✅ {tag}: cancellation confirmed and logistics informed.", "ar": "✅ {tag}: تم تأكيد الإلغاء وإبلاغ اللوجستيك."},
    "de_sent": {"en": "✅ {tag}: order *{no}* approved and sent to logistics.", "ar": "✅ {tag}: الطلب رقم *{no}* معتمد وأُرسل للوجستيك."},
    "de_no_logistics": {"en": "⚠️ {tag}: order *{no}* approved, but no logistics destination is set in settings.",
                        "ar": "⚠️ {tag}: اعتُمد الطلب رقم *{no}* لكن وجهة اللوجستيك غير محددة في الإعدادات."},
    "de_nochange": {"en": "✅ {tag}: order *{no}* approved (nothing new for logistics).",
                    "ar": "✅ {tag}: الطلب رقم *{no}* معتمد (لا تغيير جديد على ما لدى اللوجستيك)."},
    "de_rejected": {"en": "❌ {tag}: recorded as not approved — it will not be sent to logistics.",
                    "ar": "❌ {tag}: سُجّل أن الطلب غير معتمد ولن يُرسل للوجستيك."},

    # ---------- تنبيهات المندوب / مسؤول النظام ----------
    "al_rejected": {"en": "❌ *Order not approved by finance* — {company}\nRef: FD-{sid}{no}{note}\nPlease follow up with the customer or finance.",
                    "ar": "❌ *طلب لم تعتمده المالية* — {company}\nالمرجع: FD-{sid}{no}{note}\nيرجى التواصل مع العميل أو المالية."},
    "al_rejected_no": {"en": " | Order No: {no}", "ar": " | رقم الطلب: {no}"},
    "al_note": {"en": "\nNote: {text}", "ar": "\nالملاحظة: {text}"},
    "al_pending": {"en": "⚠️ *Order needs your approval* — {company}\nReason: {reason}\nDelivery: {delivery}\nOrder as understood by the bot: {summary}\n{action}",
                   "ar": "⚠️ *طلب يحتاج اعتمادك* — {company}\nالسبب: {reason}\nالتسليم: {delivery}\nالطلب كما فهمه البوت: {summary}\n{action}"},
    "al_reason_availability": {"en": "the customer asked to confirm availability", "ar": "العميل يطلب تأكيد التوفر"},
    "al_reason_unsure": {"en": "the bot wasn't sure it understood the order and asked the customer",
                         "ar": "البوت غير متأكد من فهم الطلب وسأل العميل"},
    "al_default_delivery": {"en": "default delivery day", "ar": "الموعد الافتراضي"},
    "al_no_items": {"en": "(no items)", "ar": "(بدون أصناف)"},
    "al_link": {"en": "✅ Approve or reject with one tap:\n{link}", "ar": "✅ للاعتماد أو الرفض بضغطة:\n{link}"},
    "al_dashboard": {"en": "Open the dashboard → Smart Orders to approve.", "ar": "افتح لوحة التحكم ← الطلبيات الذكية للاعتماد."},
    "al_complaint": {"en": "🚨 *Customer complaint* — {company}\nType: {category} · Product: {product} · Severity: {severity}\nSummary: {summary}\nCustomer text ({sender}): {text}",
                     "ar": "🚨 *شكوى من عميل* — {company}\nالنوع: {category} · المنتج: {product} · الخطورة: {severity}\nالملخص: {summary}\nنص العميل ({sender}): {text}"},
    "al_inquiry": {"en": "❓ *Inquiry needs your reply* — {company}\nTopic: {category} · Product: {product}\nQuestion ({sender}): {text}{reason}",
                   "ar": "❓ *استفسار يحتاج ردكم* — {company}\nالموضوع: {category} · المنتج: {product}\nالسؤال ({sender}): {text}{reason}"},
    "al_escalation": {"en": "\nEscalation reason: {reason}", "ar": "\nسبب التحويل: {reason}"},
    "al_autoreply_off": {"en": "auto-reply is off", "ar": "الرد الآلي غير مفعّل"},

    # ---------- رسائل البوت في مجموعة العميل ----------
    "cu_confirm_q": {"en": "Just to confirm, do you mean: {summary}?", "ar": "هل تقصدون: {summary}؟"},
    "cu_order_confirmed": {"en": "✅ Your order is confirmed, thank you.", "ar": "✅ تم اعتماد طلبكم، شكراً لكم."},
    "cu_complaint": {"en": "We're sorry about this 🙏 Your feedback has been passed to the responsible team and they will contact you shortly.",
                     "ar": "نعتذر عن ذلك 🙏 تم تحويل ملاحظتكم للفريق المختص وسيتواصل معكم في أقرب وقت."},
    "cu_inquiry_hold": {"en": "Thank you for reaching out 🌷 A member of our team will get back to you shortly.",
                        "ar": "شكراً لتواصلكم 🌷 سيقوم أحد أعضاء فريقنا بالرد على استفساركم قريباً."},
    "cu_reminder": {"en": "Good morning 🌞 A kind reminder for {day_en}'s order ({date}).",
                    "ar": "صباح الخير 🌞 نذكركم بطلب {day} ({date})."},

    # ---------- ملخص تقرير الإدارة ----------
    "rp_value": {"en": "Value", "ar": "القيمة"},
    "rp_pieces": {"en": "Quantity", "ar": "الكمية"},
    "rp_pieces_unit": {"en": "pcs", "ar": "حبة"},
    "rp_orders": {"en": "Orders", "ar": "الطلبيات"},
    "rp_customers": {"en": "Active customers", "ar": "العملاء النشطون"},
    "rp_full": {"en": "Full report:", "ar": "التقرير الكامل:"},
    "rp_title_month": {"en": "Monthly Sales & Operations Report — {label}", "ar": ""},
    "rp_title_quarter": {"en": "Quarterly Sales & Operations Report — {label}", "ar": ""},
    "rp_title_year": {"en": "Annual Sales & Operations Report — {label}", "ar": ""},
}

CATEGORY = {
    "quality": ("Quality", "الجودة"), "freshness": ("Freshness", "الطزاجة"), "size_weight": ("Size/weight", "الحجم/الوزن"),
    "packaging": ("Packaging", "التغليف"), "delivery": ("Delivery", "التوصيل"), "quantity": ("Short quantity", "نقص الكمية"),
    "wrong_item": ("Wrong item", "صنف خاطئ"), "price": ("Prices", "الأسعار"), "product_info": ("Product info", "معلومات منتج"),
    "availability": ("Availability", "التوفر"), "account_invoice": ("Account/invoices", "الحساب/الفواتير"),
    "company_info": ("About the company", "عن الشركة"), "other": ("Other", "أخرى"),
}
SEVERITY = {"low": ("Low", "منخفضة"), "medium": ("Medium", "متوسطة"), "high": ("🔴 High", "🔴 عالية")}


def t(key: str, lang: str, **kw) -> str:
    entry = M[key]
    text = entry[norm(lang)] or entry["en"]
    return text.format(**kw) if kw else text


def category(code: str, lang: str) -> str:
    pair = CATEGORY.get(code or "other", CATEGORY["other"])
    return pair[1] if norm(lang) == "ar" else pair[0]


def severity(code: str, lang: str) -> str:
    pair = SEVERITY.get(code or "medium", SEVERITY["medium"])
    return pair[1] if norm(lang) == "ar" else pair[0]
