"""
security.py - طبقة الأمان على مستوى الخادم لنظام FDC Sales CRM

- جلسة دخول موقّعة (HMAC-SHA256) في Cookie من نوع HttpOnly بدلاً من localStorage.
- حماية كل مسارات /api/* بجلسة صالحة.
- المسارات الداخلية (جلسة الواتساب و Webhook) لا تُقبل إلا بتوكن داخلي سري
  يُمرَّر تلقائياً لخدمة Node عبر متغير البيئة INTERNAL_API_TOKEN.
- تحديد عدد محاولات إدخال رمز 2FA الخاطئة.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
from typing import Optional

logger = logging.getLogger("SalesCRM.security")

SESSION_COOKIE_NAME = "fdc_session"
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_HOURS", "12")) * 3600
INTERNAL_TOKEN_HEADER = "X-Internal-Token"

# مفتاح توقيع الجلسات: يُقرأ من APP_SECRET_KEY في Railway.
# في حال غيابه يُولَّد مفتاح مؤقت (تنتهي كل الجلسات عند إعادة التشغيل).
_APP_SECRET = os.getenv("APP_SECRET_KEY", "").strip()
if len(_APP_SECRET) < 16:
    logger.warning("APP_SECRET_KEY غير معرّف أو قصير جداً - سيُستخدم مفتاح مؤقت تنتهي الجلسات معه عند إعادة التشغيل.")
    _APP_SECRET = secrets.token_urlsafe(48)
_SIGNING_KEY = hashlib.sha256(("fdc-session:" + _APP_SECRET).encode("utf-8")).digest()

# التوكن الداخلي بين FastAPI وخدمة الواتساب (Node).
# يُكتب في os.environ ليرثه الـ subprocess الخاص بالواتساب تلقائياً.
INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "").strip() or secrets.token_urlsafe(48)
os.environ["INTERNAL_API_TOKEN"] = INTERNAL_API_TOKEN

# توكن إعداد/إعادة ضبط 2FA: يجب تعريفه يدوياً في Railway (ADMIN_SETUP_TOKEN).
# بدونه يكون مسار إعداد 2FA معطّلاً تماماً.
ADMIN_SETUP_TOKEN = os.getenv("ADMIN_SETUP_TOKEN", "").strip()

COOKIE_SECURE = os.getenv("COOKIE_SECURE", "true").strip().lower() != "false"

# مسارات عامة لا تحتاج جلسة
PUBLIC_PATHS = {
    "/",
    "/logo.png",
    "/favicon.png",
    "/healthz",
    "/api/auth/status",
    "/api/auth/2fa/verify",
    "/api/auth/2fa/setup",
    "/api/auth/logout",
}

# مسارات داخلية: تُستدعى من خدمة الواتساب فقط
INTERNAL_PREFIXES = ("/api/internal/", "/api/whatsapp/webhook")


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def create_session_token(username: str) -> str:
    payload = {"u": username, "exp": int(time.time()) + SESSION_TTL_SECONDS, "n": secrets.token_hex(8)}
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = _b64e(hmac.new(_SIGNING_KEY, body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_session_token(token: Optional[str]) -> Optional[str]:
    """يعيد اسم المستخدم إذا كانت الجلسة صالحة وغير منتهية، وإلا None."""
    if not token or "." not in token:
        return None
    body, sig = token.rsplit(".", 1)
    expected = _b64e(hmac.new(_SIGNING_KEY, body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        payload = json.loads(_b64d(body))
    except Exception:
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    return payload.get("u")


def is_valid_internal_token(value: Optional[str]) -> bool:
    return bool(value) and hmac.compare_digest(value, INTERNAL_API_TOKEN)


def is_valid_setup_token(value: Optional[str]) -> bool:
    return bool(ADMIN_SETUP_TOKEN) and bool(value) and hmac.compare_digest(value.strip(), ADMIN_SETUP_TOKEN)


def classify_path(path: str) -> str:
    """public | internal | protected | static"""
    if path in PUBLIC_PATHS:
        return "public"
    if any(path.startswith(p) for p in INTERNAL_PREFIXES):
        return "internal"
    if path.startswith("/approve/"):
        return "public"  # محمي برابط موقّع ومؤقت (HMAC) بدل الجلسة
    if path.startswith("/api/"):
        return "protected"
    return "static"


class FailedAttemptLimiter:
    """
    حد للمحاولات الخاطئة لرمز 2FA. خلف بروكسي Railway يظهر كل الزوار بنفس عنوان IP،
    لذلك الحد عام: عدد محدود من المحاولات الخاطئة خلال نافذة زمنية.
    """

    def __init__(self, max_failures: int = 10, window_seconds: int = 300):
        self.max_failures = max_failures
        self.window = window_seconds
        self._failures: list = []
        self._lock = threading.Lock()

    def _prune(self, now: float):
        self._failures = [t for t in self._failures if now - t < self.window]

    def is_blocked(self) -> bool:
        with self._lock:
            self._prune(time.time())
            return len(self._failures) >= self.max_failures

    def record_failure(self):
        with self._lock:
            now = time.time()
            self._prune(now)
            self._failures.append(now)

    def reset(self):
        with self._lock:
            self._failures.clear()


otp_limiter = FailedAttemptLimiter()
