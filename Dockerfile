FROM node:20-bullseye-slim

# تثبيت بيئة بايثون وأدوات البناء
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-dev \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 1. تثبيت مكتبات Node.js
COPY package*.json ./
RUN npm install --production

# 2. تثبيت مكتبات Python
COPY requirements.txt ./
RUN pip3 install --no-cache-dir -r requirements.txt

# 3. نسخ باقي ملفات المشروع
COPY . .

RUN chmod +x start.sh

EXPOSE 8000

CMD ["./start.sh"]
