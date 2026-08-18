# ============================================================================
# FD-Sales: Multi-Stage Docker Build for Railway Deployment
# Stage 1: Builder (Node.js LTS Alpine)
# Stage 2: Production (Nginx Alpine)
# ============================================================================

# --- STAGE 1: BUILDER ---
FROM node:20-alpine AS builder

WORKDIR /app

# نسخ ملفات المشروع
COPY package*.json ./
COPY tsconfig.json ./
COPY vite.config.ts ./
COPY index.html ./

# تثبيت المكتبات
RUN npm ci

# نسخ كود المصدر
COPY src ./src
COPY public ./public

# بناء التطبيق
RUN npm run build

# --- STAGE 2: PRODUCTION ---
FROM nginx:alpine

WORKDIR /usr/share/nginx/html

# نسخ ملف الإعدادات
COPY nginx.conf /etc/nginx/nginx.conf

# نسخ الملفات المبنية من مرحلة البناء
COPY --from=builder /app/dist ./

# فتح المنفذ
EXPOSE 3000

# تشغيل Nginx
CMD ["nginx", "-g", "daemon off;"]
