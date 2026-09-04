#!/bin/sh

# التأكد من تعيين قيمة رقمية صريحة للمنفذ
APP_PORT=${PORT:-8000}

# تشغيل خدمة واتساب الخلفية
node whatsapp_service.js &

# تشغيل خادم FastAPI باستخدام المتغير المقيم كرقم صحيح
exec uvicorn main:app --host 0.0.0.0 --port "$APP_PORT"
