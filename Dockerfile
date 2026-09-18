FROM node:20-bookworm-slim

WORKDIR /app

# تثبيت متطلبات بايثون الأساسية من مستودعات دبيان الحديثة والمستقرة
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-dev \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# تثبيت حزم Node.js للواتساب
COPY package*.json ./
RUN npm install --production

# تثبيت حزم بايثون
COPY requirements.txt ./
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

# نسخ باقي ملفات المشروع
COPY . .

# منفذ التشغيل
ENV PORT=8000
EXPOSE 8000

# أمر بدء التشغيل
CMD ["python3", "main.py"]
