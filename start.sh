#!/bin/bash
# تشغيل خدمة واتساب في الخلفية
node whatsapp_service.js &

# تشغيل خادم المبيعات FastAPI
uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
