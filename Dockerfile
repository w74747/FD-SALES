FROM node:20-bookworm-slim

WORKDIR /app

# تثبيت git وبايثون وأدوات التجميع لحزم Baileys و PostgreSQL
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    python3 \
    python3-pip \
    python3-dev \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# تثبيت حزم Node.js
COPY package*.json ./
RUN npm install --production

# تثبيت متطلبات بايثون
COPY requirements.txt ./
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

# نسخ باقي ملفات التطبيق
COPY . .

ENV PORT=8000
EXPOSE 8000

CMD ["python3", "main.py"]
