/**
 * ============================================================================
 * FD-Sales: Refine AuthProvider with Device Binding for Supabase
 * File: src/providers/authProvider.ts
 * منطق المصادقة الكامل مع فرض قيد الجهاز الواحد لمندوبي المبيعات
 * ============================================================================
 */

import { AuthProvider } from '@refinedev/core';
import { createClient, SupabaseClient } from '@supabase/supabase-js';
import {
  getDeviceFingerprint,
  getDeviceFingerprintWithDetails,
  clearDeviceFingerprintCache,
  validateFingerprint,
  DeviceFingerprint,
} from '../utils/fingerprint';
import { User, UserDevice } from '../types';

/**
 * SUPABASE CLIENT INITIALIZATION
 */
const SUPABASE_URL = import.meta.env.VITE_SUPABASE_URL;
const SUPABASE_ANON_KEY = import.meta.env.VITE_SUPABASE_ANON_KEY;

if (!SUPABASE_URL || !SUPABASE_ANON_KEY) {
  throw new Error(
    'متغيرات Supabase مفقودة. تحقق من VITE_SUPABASE_URL و VITE_SUPABASE_ANON_KEY'
  );
}

export const supabase: SupabaseClient = createClient(
  SUPABASE_URL,
  SUPABASE_ANON_KEY
);

/**
 * STORAGE KEYS
 */
const USER_STORAGE_KEY = 'fd_sales_user';
const DEVICE_STORAGE_KEY = 'fd_sales_device';
const TOKEN_STORAGE_KEY = 'fd_sales_token';

/**
 * DEVICE BINDING VERIFICATION
 * ============================================================================
 * منطق التحقق من ربط الجهاز:
 *
 * لمندوب المبيعات (sales_rep):
 *   - الحالة أ: أول مرة تسجيل دخول -> تسجيل الجهاز الحالي
 *   - الحالة ب: جهاز متطابق -> السماح بالدخول
 *   - الحالة ج: جهاز مختلف -> رفض فوري مع رسالة عربية
 *
 * لمدير/قائد فريق:
 *   - تجاوز قيد الجهاز الواحد
 *   - السماح بعدة أجهزة
 * ============================================================================
 */

/**
 * registerOrVerifyDevice
 * تسجيل أو التحقق من جهاز المستخدم
 */
async function registerOrVerifyDevice(
  userId: string,
  userRole: string
): Promise<UserDevice> {
  const deviceFingerprint = await getDeviceFingerprintWithDetails();

  // للمديرين وقادة الفريق: تسجيل جهاز جديد دون قيود
  if (userRole === 'team_leader' || userRole === 'sales_director') {
    try {
      const { data: existingDevice } = await supabase
        .from('user_devices')
        .select('*')
        .eq('user_id', userId)
        .eq('device_fingerprint', deviceFingerprint.fingerprint)
        .single();

      if (existingDevice) {
        // الجهاز موجود، لا تفعل شيء
        localStorage.setItem(DEVICE_STORAGE_KEY, JSON.stringify(existingDevice));
        return existingDevice;
      }

      // تسجيل جهاز جديد
      const { data: newDevice, error } = await supabase
        .from('user_devices')
        .insert({
          user_id: userId,
          device_fingerprint: deviceFingerprint.fingerprint,
          device_name: deviceFingerprint.deviceName,
        })
        .select()
        .single();

      if (error) throw error;
      localStorage.setItem(DEVICE_STORAGE_KEY, JSON.stringify(newDevice));
      return newDevice;
    } catch (error) {
      console.warn('Error registering device for director/leader:', error);
      throw error;
    }
  }

  // لمندوب المبيعات (sales_rep): فرض قيد الجهاز الواحد
  const { data: registeredDevices, error: queryError } = await supabase
    .from('user_devices')
    .select('*')
    .eq('user_id', userId);

  if (queryError) throw queryError;

  // الحالة أ: أول مرة تسجيل دخول
  if (!registeredDevices || registeredDevices.length === 0) {
    const { data: newDevice, error } = await supabase
      .from('user_devices')
      .insert({
        user_id: userId,
        device_fingerprint: deviceFingerprint.fingerprint,
        device_name: deviceFingerprint.deviceName,
      })
      .select()
      .single();

    if (error) throw error;
    localStorage.setItem(DEVICE_STORAGE_KEY, JSON.stringify(newDevice));
    return newDevice;
  }

  // الحالة ب: جهاز متطابق
  const boundDevice = registeredDevices.find(
    (d) => d.device_fingerprint === deviceFingerprint.fingerprint
  );

  if (boundDevice) {
    localStorage.setItem(DEVICE_STORAGE_KEY, JSON.stringify(boundDevice));
    return boundDevice;
  }

  // الحالة ج: جهاز مختلف - رفض الدخول
  const errorMsg =
    'هذا الحساب مرتبط بجهاز مصرح آخر. يرجى التواصل مع مدير المبيعات لإعادة ضبط الجهاز.';
  throw new Error(errorMsg);
}

/**
 * fetchUserProfile
 * جلب بيانات المستخدم من جدول users
 */
async function fetchUserProfile(userId: string): Promise<User> {
  const { data: user, error } = await supabase
    .from('users')
    .select('id, email, full_name, role, department')
    .eq('id', userId)
    .single();

  if (error || !user) {
    throw new Error('فشل جلب بيانات المستخدم');
  }

  return user as User;
}

/**
 * REFINE AUTH PROVIDER IMPLEMENTATION
 * ============================================================================
 */

export const authProvider: AuthProvider = {
  /**
   * login
   * معالج تسجيل الدخول الرئيسي
   *
   * الخطوات:
   * 1. التحقق من بيانات الدخول (email/password)
   * 2. جلب بيانات المستخدم من جدول users
   * 3. التحقق من ربط الجهاز (بناءً على الدور)
   * 4. تخزين بيانات الجلسة محليًا
   */
  login: async (options) => {
    try {
      if (!options || typeof options !== 'object') {
        return { success: false, error: { message: 'بيانات دخول غير صحيحة' } };
      }

      const { email, password } = options as { email: string; password: string };

      if (!email || !password) {
        return {
          success: false,
          error: { message: 'البريد الإلكتروني وكلمة المرور مطلوبان' },
        };
      }

      // 1. التحقق من بيانات الدخول
      const { data: authData, error: authError } =
        await supabase.auth.signInWithPassword({
          email,
          password,
        });

      if (authError || !authData.user) {
        await supabase.auth.signOut();
        return {
          success: false,
          error: {
            message:
              authError?.message === 'Invalid login credentials'
                ? 'البريد الإلكتروني أو كلمة المرور غير صحيحة'
                : authError?.message || 'خطأ في المصادقة',
          },
        };
      }

      // 2. جلب بيانات المستخدم
      const user = await fetchUserProfile(authData.user.id);

      // 3. التحقق من ربط الجهاز
      const device = await registerOrVerifyDevice(user.id, user.role);

      // 4. تخزين بيانات الجلسة
      localStorage.setItem(USER_STORAGE_KEY, JSON.stringify(user));
      localStorage.setItem(DEVICE_STORAGE_KEY, JSON.stringify(device));

      if (authData.session?.access_token) {
        localStorage.setItem(TOKEN_STORAGE_KEY, authData.session.access_token);
      }

      return { success: true, redirectTo: '/' };
    } catch (error) {
      // فحص هل هو خطأ ربط جهاز محدد
      const errorMsg = (error as Error).message;
      if (errorMsg.includes('مرتبط بجهاز')) {
        return {
          success: false,
          error: { message: errorMsg },
        };
      }

      console.error('Login error:', error);
      return {
        success: false,
        error: {
          message: errorMsg || 'حدث خطأ أثناء تسجيل الدخول',
        },
      };
    }
  },

  /**
   * logout
   * معالج تسجيل الخروج
   */
  logout: async () => {
    try {
      await supabase.auth.signOut();
      localStorage.removeItem(USER_STORAGE_KEY);
      localStorage.removeItem(DEVICE_STORAGE_KEY);
      localStorage.removeItem(TOKEN_STORAGE_KEY);
      clearDeviceFingerprintCache();
      return { success: true, redirectTo: '/login' };
    } catch (error) {
      console.error('Logout error:', error);
      return {
        success: false,
        error: { message: (error as Error).message },
      };
    }
  },

  /**
   * check
   * التحقق من صحة الجلسة الحالية
   */
  check: async () => {
    try {
      const {
        data: { session },
      } = await supabase.auth.getSession();

      if (!session) {
        return { authenticated: false, redirectTo: '/login' };
      }

      const storedUser = localStorage.getItem(USER_STORAGE_KEY);
      if (!storedUser) {
        return { authenticated: false, redirectTo: '/login' };
      }

      const user: User = JSON.parse(storedUser);

      // إعادة التحقق من جهاز مندوب المبيعات
      if (user.role === 'sales_rep') {
        const currentFingerprint = await getDeviceFingerprint();
        const storedDevice = localStorage.getItem(DEVICE_STORAGE_KEY);

        if (storedDevice) {
          const device: UserDevice = JSON.parse(storedDevice);
          if (device.device_fingerprint !== currentFingerprint) {
            // الجهاز تغير - سجل خروج فوري
            await supabase.auth.signOut();
            localStorage.removeItem(USER_STORAGE_KEY);
            localStorage.removeItem(DEVICE_STORAGE_KEY);
            localStorage.removeItem(TOKEN_STORAGE_KEY);
            return {
              authenticated: false,
              redirectTo: '/login',
              error: { message: 'تم التعرف على تغيير الجهاز. تم تسجيل الخروج.' },
            };
          }
        }
      }

      return { authenticated: true };
    } catch (error) {
      console.error('Check session error:', error);
      return { authenticated: false, redirectTo: '/login' };
    }
  },

  /**
   * getPermissions
   * الحصول على صلاحيات المستخدم
   */
  getPermissions: async () => {
    try {
      const storedUser = localStorage.getItem(USER_STORAGE_KEY);
      if (!storedUser) return null;

      const user: User = JSON.parse(storedUser);
      return [user.role];
    } catch {
      return null;
    }
  },

  /**
   * getIdentity
   * الحصول على بيانات المستخدم الحالي
   */
  getIdentity: async () => {
    try {
      const storedUser = localStorage.getItem(USER_STORAGE_KEY);
      if (!storedUser) return null;

      const user: User = JSON.parse(storedUser);
      return {
        id: user.id,
        name: user.full_name,
        email: user.email,
        role: user.role,
      };
    } catch {
      return null;
    }
  },

  /**
   * onError
   * معالج الأخطاء العامة
   */
  onError: async (error) => {
    console.error('Auth error:', error);
    return { redirectTo: '/login' };
  },
};

/**
 * UTILITY FUNCTIONS
 */

/**
 * getCurrentUser
 * الحصول على المستخدم الحالي
 */
export function getCurrentUser(): User | null {
  try {
    const stored = localStorage.getItem(USER_STORAGE_KEY);
    return stored ? (JSON.parse(stored) as User) : null;
  } catch {
    return null;
  }
}

/**
 * hasPermission
 * التحقق من وجود صلاحية معينة
 */
export function hasPermission(
  requiredRole: string | string[]
): boolean {
  const user = getCurrentUser();
  if (!user) return false;

  if (Array.isArray(requiredRole)) {
    return requiredRole.includes(user.role);
  }

  return user.role === requiredRole;
}

/**
 * getSupabaseClient
 * الحصول على عميل Supabase
 */
export function getSupabaseClient(): SupabaseClient {
  return supabase;
}
