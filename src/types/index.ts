/**
 * ============================================================================
 * FD-Sales: TypeScript Type Definitions
 * File: src/types/index.ts
 * تعريفات كاملة لجميع الكيانات والعمليات في نظام FD-Sales
 * ============================================================================
 */

/**
 * ENUMS & CONSTANTS
 */

export enum UserRole {
  SALES_REP = 'sales_rep',
  TEAM_LEADER = 'team_leader',
  SALES_DIRECTOR = 'sales_director',
}

export enum LeadStatus {
  DISCOVERY = 'discovery',
  SAMPLE_SENT = 'sample_sent',
  FEEDBACK_PENDING = 'feedback_pending',
  PRODUCTION_REVIEW = 'production_review',
  WON = 'won',
  LOST = 'lost',
}

export enum SampleFeedbackStatus {
  PENDING = 'pending',
  APPROVED = 'approved',
  MODIFICATION_REQUESTED = 'modification_requested',
  REJECTED = 'rejected',
}

/**
 * DATABASE ENTITIES
 */

/**
 * User
 * الغرض: بيانات المستخدم والموظف
 */
export interface User {
  id: string; // UUID من Supabase Auth
  email: string; // بريد إلكتروني فريد
  full_name: string; // الاسم الكامل
  role: UserRole; // دور المستخدم
  department?: string | null; // القسم/الفريق
  is_active?: boolean; // حالة التفعيل
  created_at?: string; // ISO 8601
  updated_at?: string; // ISO 8601
}

/**
 * UserDevice
 * الغرض: ربط الأجهزة الفريدة بمندوبي المبيعات
 */
export interface UserDevice {
  id: string; // UUID
  user_id: string; // معرف المستخدم (FK)
  device_fingerprint: string; // بصمة الجهاز الفريدة
  device_name?: string | null; // اسم الجهاز البشري
  created_at?: string; // ISO 8601
  updated_at?: string; // ISO 8601
}

/**
 * Product
 * الغرض: المنتجات والخبز المتاحة
 */
export interface Product {
  id: string; // UUID
  sku_code: string; // رمز SKU فريد
  name: string; // اسم المنتج
  description?: string | null; // وصف
  weight_grams?: number | null; // الوزن بالجرام
  is_active?: boolean; // حالة النشاط
  created_at?: string; // ISO 8601
  updated_at?: string; // ISO 8601
}

/**
 * Lead
 * الغرض: العملاء المحتملين والعمليات في مسار المبيعات
 */
export interface Lead {
  id: string; // UUID
  company_name: string; // اسم الشركة/الفرع
  contact_person: string; // جهة الاتصال
  phone: string; // الهاتف
  branches_count: number; // عدد الفروع
  estimated_monthly_consumption?: Record<string, number> | null; // الاستهلاك المقدر
  assigned_to?: string | null; // معرف مندوب المبيعات (FK)
  status: LeadStatus; // حالة العملية
  created_at?: string; // ISO 8601
  updated_at?: string; // ISO 8601
}

/**
 * Sample
 * الغرض: شحنات العينات المرسلة للعملاء
 */
export interface Sample {
  id: string; // UUID
  lead_id: string; // معرف العملية (FK)
  dispatched_by: string; // معرف المندوب (FK)
  delivery_location: string; // موقع التسليم
  is_delivered: boolean; // حالة التسليم
  delivered_at?: string | null; // تاريخ التسليم
  created_at?: string; // ISO 8601
  updated_at?: string; // ISO 8601
}

/**
 * SampleItem
 * الغرض: منتجات العينة الفردية مع التقييمات
 */
export interface SampleItem {
  id: string; // UUID
  sample_id: string; // معرف العينة (FK)
  product_id: string; // معرف المنتج (FK)
  quantity: number; // الكمية
  feedback_status: SampleFeedbackStatus; // حالة التقييم
  feedback_notes?: string | null; // ملاحظات التقييم
  created_at?: string; // ISO 8601
  updated_at?: string; // ISO 8601
}

/**
 * Order
 * الغرض: الطلبات الموثقة من العملاء
 */
export interface Order {
  id: string; // UUID
  lead_id: string; // معرف العملية (FK)
  account_manager_id: string; // معرف مندوب المبيعات (FK)
  total_amount: number; // المبلغ الإجمالي
  order_date: string; // تاريخ الطلب
  status?: string; // حالة الطلب
  created_at?: string; // ISO 8601
  updated_at?: string; // ISO 8601
}

/**
 * FORM DTOs (Data Transfer Objects)
 */

/**
 * LoginRequest
 * طلب تسجيل الدخول
 */
export interface LoginRequest {
  email: string;
  password: string;
}

/**
 * CreateLeadRequest
 * طلب إنشاء عملية جديدة
 */
export interface CreateLeadRequest {
  company_name: string;
  contact_person: string;
  phone: string;
  branches_count: number;
  estimated_monthly_consumption?: Record<string, number>;
}

/**
 * DispatchSampleRequest
 * طلب إرسال عينة
 */
export interface DispatchSampleRequest {
  lead_id: string;
  delivery_location: string;
  items: {
    product_id: string;
    quantity: number;
  }[];
}

/**
 * SubmitFeedbackRequest
 * طلب تقديم تقييم العينة
 */
export interface SubmitFeedbackRequest {
  sample_item_id: string;
  feedback_status: SampleFeedbackStatus;
  feedback_notes: string;
}

/**
 * CreateOrderRequest
 * طلب إنشاء طلب جديد
 */
export interface CreateOrderRequest {
  lead_id: string;
  total_amount: number;
}

/**
 * RESPONSE TYPES
 */

/**
 * ApiResponse
 * صيغة موحدة لاستجابة API
 */
export interface ApiResponse<T = unknown> {
  success: boolean;
  data?: T;
  error?: {
    message: string;
    code?: string;
  };
}

/**
 * PaginatedResponse
 * استجابة موضحة مع الصفحات
 */
export interface PaginatedResponse<T> {
  data: T[];
  pagination: {
    page: number;
    pageSize: number;
    total: number;
    totalPages: number;
  };
}

/**
 * REFINE RESOURCES
 */

/**
 * LeadResource
 * موارد العمليات للعمل مع Refine
 */
export interface LeadResource extends Lead {
  assignee?: User | null; // المستخدم المسند إليه
}

/**
 * SampleResource
 * موارد العينات
 */
export interface SampleResource extends Sample {
  lead?: Lead | null;
  dispatcher?: User | null;
  items?: SampleItemResource[] | null;
}

/**
 * SampleItemResource
 * عناصر العينة مع بيانات المنتج
 */
export interface SampleItemResource extends SampleItem {
  product?: Product | null;
}

/**
 * OrderResource
 * موارد الطلبات
 */
export interface OrderResource extends Order {
  lead?: Lead | null;
  account_manager?: User | null;
}

/**
 * BUSINESS LOGIC TYPES
 */

/**
 * SalesProgressMetrics
 * مقاييس تقدم العملية
 */
export interface SalesProgressMetrics {
  lead_id: string;
  current_stage: LeadStatus;
  stage_entered_at: string;
  total_iterations: number; // عدد التكرارات
  approved_products: number;
  pending_products: number;
  rejected_products: number;
  days_in_stage: number;
}

/**
 * SalesRepPerformance
 * مقاييس أداء مندوب المبيعات
 */
export interface SalesRepPerformance {
  rep_id: string;
  rep_name: string;
  total_leads: number;
  won_leads: number;
  pending_leads: number;
  conversion_rate: number; // نسبة التحويل 0-1
  average_days_to_win: number;
  total_order_value: number;
}

/**
 * QualityFeedbackSummary
 * ملخص جودة المنتج من التقييمات
 */
export interface QualityFeedbackSummary {
  product_id: string;
  product_name: string;
  total_feedback_items: number;
  approval_rate: number; // 0-1
  common_issues: string[];
  recommended_action: 'approved' | 'needs_revision' | 'rejected';
}

/**
 * FILTER & QUERY TYPES
 */

/**
 * LeadFilter
 * تصفية العمليات
 */
export interface LeadFilter {
  status?: LeadStatus[];
  assigned_to?: string;
  company_name?: string;
  date_from?: string;
  date_to?: string;
  page?: number;
  limit?: number;
}

/**
 * SampleFilter
 * تصفية العينات
 */
export interface SampleFilter {
  lead_id?: string;
  dispatched_by?: string;
  is_delivered?: boolean;
  date_from?: string;
  date_to?: string;
  page?: number;
  limit?: number;
}

/**
 * ERROR TYPES
 */

/**
 * DeviceBindingError
 * خطأ ربط الجهاز
 */
export class DeviceBindingError extends Error {
  constructor(message: string = 'فشل التحقق من الجهاز') {
    super(message);
    this.name = 'DeviceBindingError';
  }
}

/**
 * UnauthorizedError
 * خطأ عدم التفويض
 */
export class UnauthorizedError extends Error {
  constructor(message: string = 'لا توجد صلاحية كافية') {
    super(message);
    this.name = 'UnauthorizedError';
  }
}

/**
 * NotFoundError
 * خطأ عدم العثور
 */
export class NotFoundError extends Error {
  constructor(resource: string, id: string) {
    super(`${resource} برقم ${id} غير موجود`);
    this.name = 'NotFoundError';
  }
}

/**
 * UTILITY TYPES
 */

/**
 * Nullable
 * جعل جميع الخصائص اختيارية وقابلة للقيمة الفارغة
 */
export type Nullable<T> = {
  [K in keyof T]: T[K] | null;
};

/**
 * Optional
 * جعل جميع الخصائص اختيارية
 */
export type Optional<T> = {
  [K in keyof T]?: T[K];
};

/**
 * DeepPartial
 * Deep partial type
 */
export type DeepPartial<T> = T extends object
  ? {
      [P in keyof T]?: DeepPartial<T[P]>;
    }
  : T;

/**
 * AUTH STATE TYPES
 */

/**
 * AuthState
 * حالة المصادقة
 */
export interface AuthState {
  isAuthenticated: boolean;
  user: User | null;
  device: UserDevice | null;
  role: UserRole | null;
  isLoading: boolean;
  error: string | null;
}

/**
 * SESSION STATE
 */
export interface SessionState {
  token: string | null;
  expiresAt: number | null;
  isValid: boolean;
}
