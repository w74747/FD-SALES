-- ============================================================================
-- FD-Sales: Supabase PostgreSQL Schema & Row-Level Security
-- File: supabase/migrations/01_init_schema.sql
-- التهيئة الكاملة لقاعدة البيانات مع سياسات الأمان على مستوى الصفوف
-- ============================================================================

-- تمكين التوسيعات المطلوبة
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================================
-- ENUMS: أنواع البيانات المخصصة
-- ============================================================================

-- نوع دور المستخدم
CREATE TYPE user_role_enum AS ENUM ('sales_rep', 'team_leader', 'sales_director');

-- حالة العملية في مسار المبيعات
CREATE TYPE lead_status_enum AS ENUM (
  'discovery',
  'sample_sent',
  'feedback_pending',
  'production_review',
  'won',
  'lost'
);

-- حالة تقييم العينة
CREATE TYPE sample_feedback_enum AS ENUM (
  'pending',
  'approved',
  'modification_requested',
  'rejected'
);

-- ============================================================================
-- TABLE: users
-- الغرض: بيانات المستخدمين والموظفين
-- ============================================================================
CREATE TABLE IF NOT EXISTS public.users (
  id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  email TEXT UNIQUE NOT NULL,
  full_name TEXT NOT NULL,
  role user_role_enum NOT NULL DEFAULT 'sales_rep',
  -- قسم/فريق المستخدم (اختياري)
  department TEXT,
  -- حالة تفعيل المستخدم
  is_active BOOLEAN DEFAULT TRUE,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- TABLE: user_devices
-- الغرض: ربط الأجهزة الفريدة بمندوبي المبيعات (أمان متعدد المستويات)
-- ============================================================================
CREATE TABLE IF NOT EXISTS public.user_devices (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  -- بصمة الجهاز الفريدة (من FingerprintJS)
  device_fingerprint TEXT NOT NULL UNIQUE,
  -- اسم الجهاز البشري (مثال: "Chrome on Windows 11")
  device_name TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- TABLE: products
-- الغرض: قائمة المنتجات والخبز المتاحة
-- ============================================================================
CREATE TABLE IF NOT EXISTS public.products (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  -- رمز SKU فريد للمنتج (مثال: BRD-001, CKE-045)
  sku_code TEXT UNIQUE NOT NULL,
  -- اسم المنتج
  name TEXT NOT NULL,
  -- وصف تفصيلي
  description TEXT,
  -- وزن المنتج بالجرام
  weight_grams NUMERIC(10, 2),
  -- حالة المنتج: نشط أم لا
  is_active BOOLEAN DEFAULT TRUE,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- TABLE: leads
-- الغرض: تتبع العملاء المحتملين والعمليات في مسار المبيعات
-- ============================================================================
CREATE TABLE IF NOT EXISTS public.leads (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  -- اسم الشركة/الفرع العميل
  company_name TEXT NOT NULL,
  -- اسم جهة الاتصال الرئيسية
  contact_person TEXT NOT NULL,
  -- رقم الهاتف
  phone TEXT NOT NULL,
  -- عدد الفروع التي يمتلكها العميل
  branches_count INT DEFAULT 1,
  -- الاستهلاك المقدر شهريًا (JSON: {product_id: quantity})
  estimated_monthly_consumption JSONB,
  -- معرف مندوب المبيعات المسؤول
  assigned_to UUID REFERENCES public.users(id) ON DELETE SET NULL,
  -- حالة العملية الحالية
  status lead_status_enum DEFAULT 'discovery',
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- TABLE: samples
-- الغرض: تتبع شحنات العينات المرسلة للعملاء
-- ============================================================================
CREATE TABLE IF NOT EXISTS public.samples (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  -- معرف العملية المحتملة
  lead_id UUID NOT NULL REFERENCES public.leads(id) ON DELETE CASCADE,
  -- معرف مندوب المبيعات الذي أرسل العينة
  dispatched_by UUID NOT NULL REFERENCES public.users(id) ON DELETE RESTRICT,
  -- موقع التسليم (عنوان الفرع)
  delivery_location TEXT NOT NULL,
  -- هل تم تسليم العينة
  is_delivered BOOLEAN DEFAULT FALSE,
  -- تاريخ التسليم الفعلي
  delivered_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- TABLE: sample_items
-- الغرض: تفاصيل كل منتج في عينة مع تقييم العميل
-- ============================================================================
CREATE TABLE IF NOT EXISTS public.sample_items (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  -- معرف العينة الأب
  sample_id UUID NOT NULL REFERENCES public.samples(id) ON DELETE CASCADE,
  -- معرف المنتج
  product_id UUID NOT NULL REFERENCES public.products(id) ON DELETE RESTRICT,
  -- الكمية المرسلة
  quantity INT NOT NULL CHECK (quantity > 0),
  -- حالة تقييم العميل
  feedback_status sample_feedback_enum DEFAULT 'pending',
  -- ملاحظات التقييم من العميل
  feedback_notes TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- TABLE: orders
-- الغرض: الطلبات الموثقة من العملاء (بعد اجتياز حلقة العينة بنجاح)
-- ============================================================================
CREATE TABLE IF NOT EXISTS public.orders (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  -- معرف العملية المحتملة الأصلية
  lead_id UUID NOT NULL REFERENCES public.leads(id) ON DELETE RESTRICT,
  -- معرف مندوب المبيعات مدير الحساب
  account_manager_id UUID NOT NULL REFERENCES public.users(id) ON DELETE RESTRICT,
  -- المبلغ الإجمالي للطلب
  total_amount NUMERIC(15, 2) NOT NULL,
  -- تاريخ الطلب
  order_date TIMESTAMPTZ DEFAULT NOW(),
  -- حالة الطلب
  status TEXT DEFAULT 'pending',
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- INDEXES: تحسين الأداء
-- ============================================================================

CREATE INDEX idx_users_email ON public.users(email);
CREATE INDEX idx_users_role ON public.users(role);

CREATE INDEX idx_user_devices_user_id ON public.user_devices(user_id);
CREATE INDEX idx_user_devices_fingerprint ON public.user_devices(device_fingerprint);

CREATE INDEX idx_products_sku ON public.products(sku_code);
CREATE INDEX idx_products_active ON public.products(is_active);

CREATE INDEX idx_leads_assigned_to ON public.leads(assigned_to);
CREATE INDEX idx_leads_status ON public.leads(status);
CREATE INDEX idx_leads_company ON public.leads(company_name);

CREATE INDEX idx_samples_lead_id ON public.samples(lead_id);
CREATE INDEX idx_samples_dispatched_by ON public.samples(dispatched_by);
CREATE INDEX idx_samples_is_delivered ON public.samples(is_delivered);

CREATE INDEX idx_sample_items_sample_id ON public.sample_items(sample_id);
CREATE INDEX idx_sample_items_feedback_status ON public.sample_items(feedback_status);

CREATE INDEX idx_orders_lead_id ON public.orders(lead_id);
CREATE INDEX idx_orders_account_manager ON public.orders(account_manager_id);

-- ============================================================================
-- FUNCTIONS: دوال تلقائية
-- ============================================================================

-- دالة تحديث الطابع الزمني تلقائيًا
CREATE OR REPLACE FUNCTION public.update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- تطبيق الدالة على جميع الجداول
CREATE TRIGGER users_update_timestamp
BEFORE UPDATE ON public.users FOR EACH ROW
EXECUTE FUNCTION public.update_updated_at();

CREATE TRIGGER user_devices_update_timestamp
BEFORE UPDATE ON public.user_devices FOR EACH ROW
EXECUTE FUNCTION public.update_updated_at();

CREATE TRIGGER products_update_timestamp
BEFORE UPDATE ON public.products FOR EACH ROW
EXECUTE FUNCTION public.update_updated_at();

CREATE TRIGGER leads_update_timestamp
BEFORE UPDATE ON public.leads FOR EACH ROW
EXECUTE FUNCTION public.update_updated_at();

CREATE TRIGGER samples_update_timestamp
BEFORE UPDATE ON public.samples FOR EACH ROW
EXECUTE FUNCTION public.update_updated_at();

CREATE TRIGGER sample_items_update_timestamp
BEFORE UPDATE ON public.sample_items FOR EACH ROW
EXECUTE FUNCTION public.update_updated_at();

CREATE TRIGGER orders_update_timestamp
BEFORE UPDATE ON public.orders FOR EACH ROW
EXECUTE FUNCTION public.update_updated_at();

-- ============================================================================
-- ROW-LEVEL SECURITY (RLS): سياسات الأمان على مستوى الصفوف
-- ============================================================================

-- تمكين RLS على جميع الجداول
ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_devices ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.products ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.leads ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.samples ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.sample_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.orders ENABLE ROW LEVEL SECURITY;

-- ============================================================================
-- USERS TABLE POLICIES
-- ============================================================================

-- كل مستخدم يرى بيانات نفسه فقط
CREATE POLICY users_select_own ON public.users FOR SELECT
USING (auth.uid() = id);

-- مدير المبيعات يرى جميع المستخدمين
CREATE POLICY users_select_director ON public.users FOR SELECT
USING (
  EXISTS (
    SELECT 1 FROM public.users
    WHERE id = auth.uid() AND role = 'sales_director'
  )
);

-- تحديث البيانات الشخصية
CREATE POLICY users_update_own ON public.users FOR UPDATE
USING (auth.uid() = id)
WITH CHECK (auth.uid() = id);

-- ============================================================================
-- USER_DEVICES TABLE POLICIES
-- ============================================================================

-- كل مستخدم يرى أجهزته فقط
CREATE POLICY user_devices_select_own ON public.user_devices FOR SELECT
USING (auth.uid() = user_id);

-- مدير المبيعات يرى جميع الأجهزة
CREATE POLICY user_devices_select_director ON public.user_devices FOR SELECT
USING (
  EXISTS (
    SELECT 1 FROM public.users
    WHERE id = auth.uid() AND role = 'sales_director'
  )
);

-- إدراج جهاز جديد
CREATE POLICY user_devices_insert_own ON public.user_devices FOR INSERT
WITH CHECK (auth.uid() = user_id);

-- تحديث الجهاز الخاص بك
CREATE POLICY user_devices_update_own ON public.user_devices FOR UPDATE
USING (auth.uid() = user_id)
WITH CHECK (auth.uid() = user_id);

-- حذف الجهاز (مدير المبيعات فقط)
CREATE POLICY user_devices_delete_director ON public.user_devices FOR DELETE
USING (
  EXISTS (
    SELECT 1 FROM public.users
    WHERE id = auth.uid() AND role = 'sales_director'
  )
);

-- ============================================================================
-- PRODUCTS TABLE POLICIES
-- ============================================================================

-- الجميع يرون المنتجات النشطة
CREATE POLICY products_select_active ON public.products FOR SELECT
USING (is_active = TRUE);

-- مدير المبيعات يرى جميع المنتجات
CREATE POLICY products_select_director ON public.products FOR SELECT
USING (
  EXISTS (
    SELECT 1 FROM public.users
    WHERE id = auth.uid() AND role = 'sales_director'
  )
);

-- إدراج/تحديث المنتجات (مدير المبيعات فقط)
CREATE POLICY products_insert_director ON public.products FOR INSERT
WITH CHECK (
  EXISTS (
    SELECT 1 FROM public.users
    WHERE id = auth.uid() AND role = 'sales_director'
  )
);

CREATE POLICY products_update_director ON public.products FOR UPDATE
USING (
  EXISTS (
    SELECT 1 FROM public.users
    WHERE id = auth.uid() AND role = 'sales_director'
  )
);

CREATE POLICY products_delete_director ON public.products FOR DELETE
USING (
  EXISTS (
    SELECT 1 FROM public.users
    WHERE id = auth.uid() AND role = 'sales_director'
  )
);

-- ============================================================================
-- LEADS TABLE POLICIES
-- ============================================================================

-- مندوب المبيعات يرى عملياته المسندة إليه فقط
CREATE POLICY leads_select_own_rep ON public.leads FOR SELECT
USING (auth.uid() = assigned_to);

-- قائد الفريق يرى عمليات فريقه
CREATE POLICY leads_select_team_leader ON public.leads FOR SELECT
USING (
  EXISTS (
    SELECT 1 FROM public.users team_member
    WHERE team_member.id = leads.assigned_to
    AND team_member.department = (
      SELECT department FROM public.users WHERE id = auth.uid()
    )
    AND (SELECT role FROM public.users WHERE id = auth.uid()) = 'team_leader'
  )
);

-- مدير المبيعات يرى جميع العمليات
CREATE POLICY leads_select_director ON public.leads FOR SELECT
USING (
  EXISTS (
    SELECT 1 FROM public.users
    WHERE id = auth.uid() AND role = 'sales_director'
  )
);

-- إدراج عملية جديدة
CREATE POLICY leads_insert_rep ON public.leads FOR INSERT
WITH CHECK (
  (SELECT role FROM public.users WHERE id = auth.uid()) IN ('sales_rep', 'sales_director', 'team_leader')
);

-- تحديث العملية
CREATE POLICY leads_update_rep ON public.leads FOR UPDATE
USING (auth.uid() = assigned_to)
WITH CHECK (auth.uid() = assigned_to);

CREATE POLICY leads_update_director ON public.leads FOR UPDATE
USING (
  EXISTS (
    SELECT 1 FROM public.users
    WHERE id = auth.uid() AND role = 'sales_director'
  )
);

-- ============================================================================
-- SAMPLES TABLE POLICIES
-- ============================================================================

-- مندوب يرى عيناته فقط
CREATE POLICY samples_select_own ON public.samples FOR SELECT
USING (auth.uid() = dispatched_by);

-- قائد الفريق يرى عينات فريقه
CREATE POLICY samples_select_team_leader ON public.samples FOR SELECT
USING (
  EXISTS (
    SELECT 1 FROM public.users dispatcher
    WHERE dispatcher.id = samples.dispatched_by
    AND dispatcher.department = (
      SELECT department FROM public.users WHERE id = auth.uid()
    )
    AND (SELECT role FROM public.users WHERE id = auth.uid()) = 'team_leader'
  )
);

-- مدير المبيعات يرى جميع العينات
CREATE POLICY samples_select_director ON public.samples FOR SELECT
USING (
  EXISTS (
    SELECT 1 FROM public.users
    WHERE id = auth.uid() AND role = 'sales_director'
  )
);

-- إدراج عينة جديدة
CREATE POLICY samples_insert_rep ON public.samples FOR INSERT
WITH CHECK (auth.uid() = dispatched_by);

-- تحديث العينة
CREATE POLICY samples_update_rep ON public.samples FOR UPDATE
USING (auth.uid() = dispatched_by)
WITH CHECK (auth.uid() = dispatched_by);

-- ============================================================================
-- SAMPLE_ITEMS TABLE POLICIES
-- ============================================================================

-- مندوب يرى عناصر عيناته
CREATE POLICY sample_items_select_rep ON public.sample_items FOR SELECT
USING (
  EXISTS (
    SELECT 1 FROM public.samples
    WHERE samples.id = sample_items.sample_id
    AND samples.dispatched_by = auth.uid()
  )
);

-- قائد الفريق يرى عناصر عينات فريقه
CREATE POLICY sample_items_select_leader ON public.sample_items FOR SELECT
USING (
  EXISTS (
    SELECT 1 FROM public.samples
    JOIN public.users ON users.id = samples.dispatched_by
    WHERE samples.id = sample_items.sample_id
    AND users.department = (SELECT department FROM public.users WHERE id = auth.uid())
    AND (SELECT role FROM public.users WHERE id = auth.uid()) = 'team_leader'
  )
);

-- مدير يرى الكل
CREATE POLICY sample_items_select_director ON public.sample_items FOR SELECT
USING (
  EXISTS (
    SELECT 1 FROM public.users
    WHERE id = auth.uid() AND role = 'sales_director'
  )
);

-- إدراج عنصر جديد
CREATE POLICY sample_items_insert_rep ON public.sample_items FOR INSERT
WITH CHECK (
  EXISTS (
    SELECT 1 FROM public.samples
    WHERE samples.id = sample_items.sample_id
    AND samples.dispatched_by = auth.uid()
  )
);

-- تحديث التقييم
CREATE POLICY sample_items_update_rep ON public.sample_items FOR UPDATE
USING (
  EXISTS (
    SELECT 1 FROM public.samples
    WHERE samples.id = sample_items.sample_id
    AND samples.dispatched_by = auth.uid()
  )
);

-- ============================================================================
-- ORDERS TABLE POLICIES
-- ============================================================================

-- مندوب يرى طلباته
CREATE POLICY orders_select_rep ON public.orders FOR SELECT
USING (auth.uid() = account_manager_id);

-- قائد يرى طلبات فريقه
CREATE POLICY orders_select_leader ON public.orders FOR SELECT
USING (
  EXISTS (
    SELECT 1 FROM public.users
    WHERE users.id = orders.account_manager_id
    AND users.department = (SELECT department FROM public.users WHERE id = auth.uid())
    AND (SELECT role FROM public.users WHERE id = auth.uid()) = 'team_leader'
  )
);

-- مدير يرى الكل
CREATE POLICY orders_select_director ON public.orders FOR SELECT
USING (
  EXISTS (
    SELECT 1 FROM public.users
    WHERE id = auth.uid() AND role = 'sales_director'
  )
);

-- إدراج طلب جديد
CREATE POLICY orders_insert_rep ON public.orders FOR INSERT
WITH CHECK (auth.uid() = account_manager_id);

-- تحديث الطلب
CREATE POLICY orders_update_rep ON public.orders FOR UPDATE
USING (auth.uid() = account_manager_id)
WITH CHECK (auth.uid() = account_manager_id);

-- ============================================================================
-- END OF MIGRATION SCRIPT
-- ============================================================================
