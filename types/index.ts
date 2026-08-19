/**
 * ============================================================================
 * FD-Sales: TypeScript Type Definitions
 * File: src/types/index.ts
 * ============================================================================
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

export interface User {
  id: string;
  email: string;
  full_name: string;
  role: UserRole | string;
  department?: string | null;
  is_active?: boolean;
  created_at?: string;
  updated_at?: string;
}

export interface UserDevice {
  id: string;
  user_id: string;
  device_fingerprint: string;
  device_name?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface Product {
  id: string;
  sku_code: string;
  name: string;
  description?: string | null;
  weight_grams?: number | null;
  is_active?: boolean;
  created_at?: string;
  updated_at?: string;
}

export interface Lead {
  id: string;
  company_name: string;
  contact_person: string;
  phone: string;
  branches_count: number;
  estimated_monthly_consumption?: Record<string, number> | null;
  assigned_to?: string | null;
  status: LeadStatus;
  created_at?: string;
  updated_at?: string;
}

export interface Sample {
  id: string;
  lead_id: string;
  dispatched_by: string;
  delivery_location: string;
  is_delivered: boolean;
  delivered_at?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface SampleItem {
  id: string;
  sample_id: string;
  product_id: string;
  quantity: number;
  feedback_status: SampleFeedbackStatus;
  feedback_notes?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface Order {
  id: string;
  lead_id: string;
  account_manager_id: string;
  total_amount: number;
  order_date: string;
  status?: string;
  created_at?: string;
  updated_at?: string;
}

export interface LoginRequest {
  email: string;
  password: string;
}

export interface CreateLeadRequest {
  company_name: string;
  contact_person: string;
  phone: string;
  branches_count: number;
  estimated_monthly_consumption?: Record<string, number>;
}

export interface DispatchSampleRequest {
  lead_id: string;
  delivery_location: string;
  items: {
    product_id: string;
    quantity: number;
  }[];
}

export interface SubmitFeedbackRequest {
  sample_item_id: string;
  feedback_status: SampleFeedbackStatus;
  feedback_notes: string;
}

export interface CreateOrderRequest {
  lead_id: string;
  total_amount: number;
}

export interface ApiResponse<T = unknown> {
  success: boolean;
  data?: T;
  error?: {
    message: string;
    code?: string;
  };
}

export interface PaginatedResponse<T> {
  data: T[];
  pagination: {
    page: number;
    pageSize: number;
    total: number;
    totalPages: number;
  };
}

export interface LeadResource extends Lead {
  assignee?: User | null;
}

export interface SampleResource extends Sample {
  lead?: Lead | null;
  dispatcher?: User | null;
  items?: SampleItem[] | null;
}

export interface OrderResource extends Order {
  lead?: Lead | null;
  account_manager?: User | null;
}

export interface SalesProgressMetrics {
  lead_id: string;
  current_stage: LeadStatus;
  stage_entered_at: string;
  total_iterations: number;
  approved_products: number;
  pending_products: number;
  rejected_products: number;
  days_in_stage: number;
}

export interface SalesRepPerformance {
  rep_id: string;
  rep_name: string;
  total_leads: number;
  won_leads: number;
  pending_leads: number;
  conversion_rate: number;
  average_days_to_win: number;
  total_order_value: number;
}

export interface LeadFilter {
  status?: LeadStatus[];
  assigned_to?: string;
  company_name?: string;
  date_from?: string;
  date_to?: string;
  page?: number;
  limit?: number;
}

export class DeviceBindingError extends Error {
  constructor(message: string = 'فشل التحقق من الجهاز') {
    super(message);
    this.name = 'DeviceBindingError';
  }
}

export class UnauthorizedError extends Error {
  constructor(message: string = 'لا توجد صلاحية كافية') {
    super(message);
    this.name = 'UnauthorizedError';
  }
}

export class NotFoundError extends Error {
  constructor(resource: string, id: string) {
    super(`${resource} برقم ${id} غير موجود`);
    this.name = 'NotFoundError';
  }
}

export type Nullable<T> = {
  [K in keyof T]: T[K] | null;
};

export type Optional<T> = {
  [K in keyof T]?: T[K];
};

export type DeepPartial<T> = T extends object
  ? {
      [P in keyof T]?: DeepPartial<T[P]>;
    }
  : T;

export interface AuthState {
  isAuthenticated: boolean;
  user: User | null;
  device: UserDevice | null;
  role: UserRole | null;
  isLoading: boolean;
  error: string | null;
}

export interface SessionState {
  token: string | null;
  expiresAt: number | null;
  isValid: boolean;
}
