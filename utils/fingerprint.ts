/**
 * ============================================================================
 * FD-Sales: Device Fingerprinting & Identification
 * File: src/utils/fingerprint.ts
 * الغرض: إنشاء وتخزين بصمة جهاز فريدة وآمنة لفرض قيد الجهاز الواحد
 * ============================================================================
 */

import FingerprintJS, { GetResult } from '@fingerprintjs/fingerprintjs';

/**
 * DeviceFingerprint
 * معرف الجهاز الفريد مع البيانات الوصفية
 */
export interface DeviceFingerprint {
  fingerprint: string; // بصمة الجهاز الفريدة
  deviceName: string; // اسم الجهاز البشري
  timestamp: number; // وقت الحساب
}

/**
 * CACHE CONFIGURATION
 */
const CACHE_KEY = 'fd_sales_device_fingerprint';
const CACHE_TTL_MS = 8 * 60 * 60 * 1000; // 8 ساعات

/**
 * Module State
 */
let fpInstance: FingerprintJS | null = null;
let cachedFingerprint: DeviceFingerprint | null = null;

/**
 * initFingerprintJS
 * تهيئة مكتبة FingerprintJS بشكل آمن
 */
async function initFingerprintJS(): Promise<FingerprintJS> {
  if (fpInstance) {
    return fpInstance;
  }

  try {
    fpInstance = await FingerprintJS.load();
    return fpInstance;
  } catch (error) {
    console.error('Failed to initialize FingerprintJS:', error);
    throw new Error('فشل تحميل مكتبة التحديد: ' + (error as Error).message);
  }
}

/**
 * getDeviceName
 * استخراج اسم الجهاز من user-agent
 * يرجع: "Chrome on Windows 11" أو ما شابه ذلك
 */
function getDeviceName(): string {
  const ua = navigator.userAgent;

  let browser = 'Unknown';
  let os = 'Unknown';

  // تحديد المتصفح
  if (ua.includes('Chrome') && !ua.includes('Edge')) browser = 'Chrome';
  else if (ua.includes('Safari') && !ua.includes('Chrome')) browser = 'Safari';
  else if (ua.includes('Firefox')) browser = 'Firefox';
  else if (ua.includes('Edge')) browser = 'Edge';

  // تحديد نظام التشغيل
  if (ua.includes('Windows')) os = 'Windows';
  else if (ua.includes('Macintosh')) os = 'macOS';
  else if (ua.includes('Android')) os = 'Android';
  else if (ua.includes('iPhone') || ua.includes('iPad')) os = 'iOS';
  else if (ua.includes('Linux')) os = 'Linux';

  return `${browser} on ${os}`;
}

/**
 * getCachedFingerprint
 * جلب البصمة المخزنة مؤقتًا (إذا كانت لا تزال صالحة)
 */
function getCachedFingerprint(): DeviceFingerprint | null {
  if (cachedFingerprint) {
    const age = Date.now() - cachedFingerprint.timestamp;
    if (age < CACHE_TTL_MS) {
      return cachedFingerprint;
    }
    cachedFingerprint = null;
  }

  // محاولة جلب من localStorage
  try {
    const stored = localStorage.getItem(CACHE_KEY);
    if (stored) {
      const parsed = JSON.parse(stored) as DeviceFingerprint;
      const age = Date.now() - parsed.timestamp;
      if (age < CACHE_TTL_MS) {
        cachedFingerprint = parsed;
        return parsed;
      }
      localStorage.removeItem(CACHE_KEY);
    }
  } catch (error) {
    console.warn('Error reading cached fingerprint:', error);
  }

  return null;
}

/**
 * setCachedFingerprint
 * تخزين البصمة محليًا
 */
function setCachedFingerprint(fingerprint: DeviceFingerprint): void {
  cachedFingerprint = fingerprint;
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify(fingerprint));
  } catch (error) {
    console.warn('Error caching fingerprint:', error);
  }
}

/**
 * getDeviceFingerprint
 * الدالة الرئيسية: الحصول على بصمة جهاز فريدة
 *
 * المنطق:
 * 1. تحقق من الكاش المحلي أولاً
 * 2. إذا لم تكن موجودة، احسبها من FingerprintJS
 * 3. خزنها للاستخدام المستقبلي
 *
 * الاستخدام:
 * ```typescript
 * const fingerprint = await getDeviceFingerprint();
 * console.log(fingerprint.fingerprint); // البصمة الفريدة
 * console.log(fingerprint.deviceName); // اسم الجهاز
 * ```
 */
export async function getDeviceFingerprint(): Promise<string> {
  // محاولة جلب من الكاش أولاً
  const cached = getCachedFingerprint();
  if (cached) {
    return cached.fingerprint;
  }

  try {
    // تهيئة FingerprintJS
    const fp = await initFingerprintJS();

    // حساب البصمة
    const result: GetResult = await fp.get();

    // إنشاء كائن البصمة
    const fingerprint: DeviceFingerprint = {
      fingerprint: result.visitorId,
      deviceName: getDeviceName(),
      timestamp: Date.now(),
    };

    // تخزين مؤقت
    setCachedFingerprint(fingerprint);

    return fingerprint.fingerprint;
  } catch (error) {
    console.error('Error getting device fingerprint:', error);
    throw new Error('فشل الحصول على معرف الجهاز');
  }
}

/**
 * getDeviceFingerprintWithDetails
 * الحصول على البصمة مع البيانات الوصفية
 */
export async function getDeviceFingerprintWithDetails(): Promise<DeviceFingerprint> {
  const cached = getCachedFingerprint();
  if (cached) {
    return cached;
  }

  try {
    const fp = await initFingerprintJS();
    const result: GetResult = await fp.get();

    const fingerprint: DeviceFingerprint = {
      fingerprint: result.visitorId,
      deviceName: getDeviceName(),
      timestamp: Date.now(),
    };

    setCachedFingerprint(fingerprint);
    return fingerprint;
  } catch (error) {
    console.error('Error getting device fingerprint with details:', error);
    throw new Error('فشل الحصول على معرف الجهاز');
  }
}

/**
 * clearDeviceFingerprintCache
 * مسح الكاش (عند تسجيل الخروج)
 */
export function clearDeviceFingerprintCache(): void {
  cachedFingerprint = null;
  try {
    localStorage.removeItem(CACHE_KEY);
  } catch (error) {
    console.warn('Error clearing fingerprint cache:', error);
  }
}

/**
 * validateFingerprint
 * التحقق من توافق البصمة الحالية مع بصمة مخزنة
 */
export async function validateFingerprint(storedFingerprint: string): Promise<boolean> {
  try {
    const current = await getDeviceFingerprint();
    return current === storedFingerprint;
  } catch (error) {
    console.error('Error validating fingerprint:', error);
    return false;
  }
}
