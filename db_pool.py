"""
db_pool.py - مجمّع اتصالات PostgreSQL (Connection Pool)

بدلاً من فتح اتصال جديد مع كل طلب (ما سبّب أخطاء timeout مع postgres.railway.internal)
يُعاد استخدام عدد محدود من الاتصالات المفتوحة. الحد الأقصى من DB_MAX_CONNECTIONS (افتراضياً 10).

الاستخدام لا يتغير في بقية الكود:
    conn = get_db_connection()
    ...
    conn.close()   # يعيد الاتصال للمجمّع بدلاً من إغلاقه فعلياً
"""

import logging
import os
import threading
import time
from typing import Optional

import psycopg2
import psycopg2.extensions
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool

logger = logging.getLogger("SalesCRM.db")

_pool: Optional[ThreadedConnectionPool] = None
_pool_lock = threading.Lock()
_slots: Optional[threading.BoundedSemaphore] = None

_last_used: dict = {}
IDLE_PING_SECONDS = 5

MAX_CONNECTIONS = max(2, int(os.getenv("DB_MAX_CONNECTIONS", "10") or 10))
ACQUIRE_TIMEOUT_SECONDS = float(os.getenv("DB_ACQUIRE_TIMEOUT", "10"))


def _normalize_url(url: str) -> str:
    return url.replace("postgres://", "postgresql://", 1) if url.startswith("postgres://") else url


def _ensure_pool(database_url: str) -> Optional[ThreadedConnectionPool]:
    global _pool, _slots
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is None:
            # ملاحظة: psycopg2 يغلق أي اتصال مُعاد يتجاوز minconn، لذلك minconn = الحد الأقصى
            # حتى تبقى الاتصالات مفتوحة وجاهزة لإعادة الاستخدام تحت الضغط
            _pool = ThreadedConnectionPool(
                MAX_CONNECTIONS, MAX_CONNECTIONS, _normalize_url(database_url),
                cursor_factory=RealDictCursor,
                connect_timeout=5,
                # TCP keepalive: اكتشاف الاتصالات الميتة بعد إعادة تشغيل قاعدة البيانات
                keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=3,
                application_name="fdc-sales-crm",
            )
            _slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
            logger.info(f"PostgreSQL pool ready (max {MAX_CONNECTIONS} connections)")
    return _pool


def _is_broken(raw) -> bool:
    if raw.closed:
        return True
    return raw.info.transaction_status == psycopg2.extensions.TRANSACTION_STATUS_UNKNOWN


def _is_alive(raw) -> bool:
    """فحص سريع (SELECT 1) فقط للاتصالات الخاملة لأكثر من 5 ثوانٍ، لتجنّب اتصال ميت بعد إعادة تشغيل القاعدة."""
    if _is_broken(raw):
        return False
    if time.time() - _last_used.get(id(raw), 0) < IDLE_PING_SECONDS:
        return True
    try:
        with raw.cursor() as cur:
            cur.execute("SELECT 1")
        raw.rollback()
        return True
    except Exception:
        return False


class PooledConnection:
    """غلاف يعيد الاتصال للمجمّع عند close() ويمرّر بقية الخصائص للاتصال الحقيقي."""

    __slots__ = ("_raw", "_released")

    def __init__(self, raw):
        object.__setattr__(self, "_raw", raw)
        object.__setattr__(self, "_released", False)

    def __getattr__(self, name):
        return getattr(self._raw, name)

    def __setattr__(self, name, value):
        setattr(self._raw, name, value)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        if self._released:
            return
        object.__setattr__(self, "_released", True)
        raw = self._raw
        discard = _is_broken(raw)
        if not discard:
            try:
                # تنظيف أي معاملة معلّقة وإرجاع الإعدادات الافتراضية قبل إعادة الاستخدام
                if raw.info.transaction_status != psycopg2.extensions.TRANSACTION_STATUS_IDLE:
                    raw.rollback()
                if raw.autocommit:
                    raw.autocommit = False
            except Exception:
                discard = True
        try:
            if discard:
                _last_used.pop(id(raw), None)
            else:
                _last_used[id(raw)] = time.time()
            if _pool is not None:
                _pool.putconn(raw, close=discard)
        except Exception as e:
            logger.warning(f"Pool putconn failed: {e}")
        finally:
            if _slots is not None:
                _slots.release()

    def __del__(self):
        # حماية من تسرب الاتصالات إذا نُسي close()
        try:
            if not self._released:
                self.close()
        except Exception:
            pass


def get_pooled_connection(database_url: str) -> Optional[PooledConnection]:
    if not database_url:
        return None
    try:
        pool = _ensure_pool(database_url)
    except Exception as e:
        logger.error(f"Database pool init error: {e}")
        return None

    if not _slots.acquire(timeout=ACQUIRE_TIMEOUT_SECONDS):
        logger.error("Database pool exhausted: no free connection within timeout")
        return None

    for attempt in range(3):
        try:
            raw = pool.getconn()
            if not _is_alive(raw):
                _last_used.pop(id(raw), None)
                pool.putconn(raw, close=True)
                continue
            return PooledConnection(raw)
        except Exception as e:
            logger.error(f"Database connection error (attempt {attempt + 1}): {e}")
            time.sleep(0.4 * (attempt + 1))
    _slots.release()
    return None


def close_pool():
    global _pool
    with _pool_lock:
        if _pool is not None:
            try:
                _pool.closeall()
            except Exception:
                pass
            _pool = None
