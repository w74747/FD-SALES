/**
 * ============================================================================
 * FD-Sales: Supabase PostgreSQL Schema with Row-Level Security (RLS)
 * File: supabase/migrations/01_init_schema.sql
 * تهيئة كاملة لقاعدة البيانات مع سياسات الأمان على مستوى الصفوف
 * ============================================================================
 */

-- Enable necessary extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ============================================================================
-- ENUMS (أنواع البيانات المعددة)
-- ============================================================================

CREATE TYPE user_role AS ENUM ('sales_rep', 'team_leader', 'sales_director');
CREATE TYPE lead_status AS ENUM (
  'discovery',
  'sample_sent',
  'feedback_pending',
  'production_review',
  'won',
  'lost'
);
CREATE TYPE sample_feedback_status AS ENUM (
  'pending',
  'approved',
  'modification_requested',
  'rejected'
);

-- ============================================================================
-- TABLES (الجداول الرئيسية)
-- ============================================================================

/**
 * users
 * جدول بيانات المستخدمين والموظفين
 */
CREATE TABLE users (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  email TEXT UNIQUE NOT NULL,
  full_name TEXT NOT NULL,
  role user_role NOT NULL DEFAULT 'sales_rep',
  department TEXT,
  is_active BOOLEAN DEFAULT true,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

/**
 * user_devices
 * جدول ربط الأجهزة الفريدة بمندوبي المبيعات لفرض قيد الجهاز الواحد
 */
CREATE TABLE user_devices (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  device_fingerprint TEXT NOT NULL,
  device_name TEXT,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(user_id, device_fingerprint)
);

/**
 * products
 * جدول المنتجات والخبز المتاحة
 */
CREATE TABLE products (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  sku_code TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  description TEXT,
  weight_grams DECIMAL(10, 2),
  is_active BOOLEAN DEFAULT true,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

/**
 * leads
 * جدول العملاء المحتملين والعمليات في مسار المبيعات
 * منطق المبيعات:
 *   - Discovery: البحث والاكتشاف الأولي
 *   - Sample Sent: تم إرسال العينات
 *   - Feedback Pending: في انتظار التقييم
 *   - Production Review: مرحلة المراجعة الإنتاجية
 *   - Won: تمت الموافقة والطلب الأول
 *   - Lost: فقدنا الفرصة
 */
CREATE TABLE leads (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  company_name TEXT NOT NULL,
  contact_person TEXT NOT NULL,
  phone TEXT NOT NULL,
  branches_count INTEGER NOT NULL DEFAULT 1,
  estimated_monthly_consumption JSONB,
  assigned_to UUID REFERENCES users(id) ON DELETE SET NULL,
  status lead_status NOT NULL DEFAULT 'discovery',
  created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

/**
 * samples
 * جدول شحنات العينات المرسلة للعملاء
 */
CREATE TABLE samples (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  lead_id UUID NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
  dispatched_by UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  delivery_location TEXT NOT NULL,
  is_delivered BOOLEAN DEFAULT false,
  delivered_at TIMESTAMP WITH TIME ZONE,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

/**
 * sample_items
 * جدول منتجات العينة الفردية مع التقييمات
 * يربط العينة بالمنتجات ويتتبع تقييمات الجودة
 */
CREATE TABLE sample_items (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  sample_id UUID NOT NULL REFERENCES samples(id) ON DELETE CASCADE,
  product_id UUID NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
  quantity INTEGER NOT NULL DEFAULT 1,
  feedback_status sample_feedback_status DEFAULT 'pending',
  feedback_notes TEXT,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

/**
 * orders
 * جدول الطلبات الموثقة من العملاء
 */
CREATE TABLE orders (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  lead_id UUID NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
  account_manager_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  total_amount DECIMAL(15, 2) NOT NULL,
  order_date TIMESTAMP WITH TIME ZONE NOT NULL,
  status TEXT DEFAULT 'pending',
  created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- INDEXES (الفهارس لتحسين الأداء)
-- ============================================================================

CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_users_role ON users(role);
CREATE INDEX idx_user_devices_user_id ON user_devices(user_id);
CREATE INDEX idx_user_devices_fingerprint ON user_devices(device_fingerprint);
CREATE INDEX idx_leads_assigned_to ON leads(assigned_to);
CREATE INDEX idx_leads_status ON leads(status);
CREATE INDEX idx_samples_lead_id ON samples(lead_id);
CREATE INDEX idx_samples_dispatched_by ON samples(dispatched_by);
CREATE INDEX idx_sample_items_sample_id ON sample_items(sample_id);
CREATE INDEX idx_sample_items_product_id ON sample_items(product_id);
CREATE INDEX idx_orders_lead_id ON orders(lead_id);
CREATE INDEX idx_orders_account_manager_id ON orders(account_manager_id);

-- ============================================================================
-- AUTOMATIC UPDATED_AT TRIGGERS (محفزات التحديث التلقائي)
-- ============================================================================

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = CURRENT_TIMESTAMP;
  RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER users_update_updated_at BEFORE UPDATE ON users
  FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER user_devices_update_updated_at BEFORE UPDATE ON user_devices
  FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER products_update_updated_at BEFORE UPDATE ON products
  FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER leads_update_updated_at BEFORE UPDATE ON leads
  FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER samples_update_updated_at BEFORE UPDATE ON samples
  FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER sample_items_update_updated_at BEFORE UPDATE ON sample_items
  FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER orders_update_updated_at BEFORE UPDATE ON orders
  FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ============================================================================
-- ROW-LEVEL SECURITY (RLS) - سياسات الأمان على مستوى الصفوف
-- ============================================================================

-- تفعيل RLS على جميع الجداول
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_devices ENABLE ROW LEVEL SECURITY;
ALTER TABLE products ENABLE ROW LEVEL SECURITY;
ALTER TABLE leads ENABLE ROW LEVEL SECURITY;
ALTER TABLE samples ENABLE ROW LEVEL SECURITY;
ALTER TABLE sample_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE orders ENABLE ROW LEVEL SECURITY;

-- ============================================================================
-- USERS RLS POLICIES
-- ============================================================================

-- السماح لكل مستخدم برؤية بيانات نفسه فقط
CREATE POLICY "Users can view their own profile"
  ON users FOR SELECT
  USING (auth.uid()::TEXT = id::TEXT);

-- السماح للمديرين برؤية جميع المستخدمين
CREATE POLICY "Directors can view all users"
  ON users FOR SELECT
  USING (
    (SELECT role FROM users WHERE id = auth.uid()::TEXT::uuid) = 'sales_director'::user_role
  );

-- ============================================================================
-- USER_DEVICES RLS POLICIES
-- ============================================================================

-- كل مستخدم يرى أجهزته الخاصة فقط
CREATE POLICY "Users can view their own devices"
  ON user_devices FOR SELECT
  USING (
    user_id = auth.uid()::TEXT::uuid
    OR (SELECT role FROM users WHERE id = auth.uid()::TEXT::uuid) = 'sales_director'::user_role
  );

-- مندوبو المبيعات لا يستطيعون تعديل الأجهزة (يتعامل المدير)
CREATE POLICY "Only directors can manage devices"
  ON user_devices FOR ALL
  USING (
    (SELECT role FROM users WHERE id = auth.uid()::TEXT::uuid) = 'sales_director'::user_role
  );

-- ============================================================================
-- PRODUCTS RLS POLICIES
-- ============================================================================

-- الجميع يمكنهم قراءة المنتجات النشطة
CREATE POLICY "Anyone can view active products"
  ON products FOR SELECT
  USING (is_active = true);

-- فقط المديرون يمكنهم تعديل المنتجات
CREATE POLICY "Only directors can manage products"
  ON products FOR INSERT
  WITH CHECK (
    (SELECT role FROM users WHERE id = auth.uid()::TEXT::uuid) = 'sales_director'::user_role
  );

CREATE POLICY "Only directors can update products"
  ON products FOR UPDATE
  USING (
    (SELECT role FROM users WHERE id = auth.uid()::TEXT::uuid) = 'sales_director'::user_role
  );

-- ============================================================================
-- LEADS RLS POLICIES
-- ============================================================================

-- مندوبو المبيعات يرون عملياتهم المسندة إليهم فقط
CREATE POLICY "Sales reps see only their assigned leads"
  ON leads FOR SELECT
  USING (
    assigned_to = auth.uid()::TEXT::uuid
    OR (SELECT role FROM users WHERE id = auth.uid()::TEXT::uuid) IN ('team_leader', 'sales_director')
  );

-- كل شخص يمكنه إنشاء عملية جديدة
CREATE POLICY "Anyone can create leads"
  ON leads FOR INSERT
  WITH CHECK (true);

-- مندوبو المبيعات يمكنهم تعديل عملياتهم فقط
CREATE POLICY "Sales reps can update their own leads"
  ON leads FOR UPDATE
  USING (
    assigned_to = auth.uid()::TEXT::uuid
    OR (SELECT role FROM users WHERE id = auth.uid()::TEXT::uuid) IN ('team_leader', 'sales_director')
  );

-- ============================================================================
-- SAMPLES RLS POLICIES
-- ============================================================================

-- رؤية العينات المرتبطة بالعمليات المسموحة
CREATE POLICY "Users can view samples from their leads"
  ON samples FOR SELECT
  USING (
    (SELECT assigned_to FROM leads WHERE id = lead_id) = auth.uid()::TEXT::uuid
    OR (SELECT role FROM users WHERE id = auth.uid()::TEXT::uuid) IN ('team_leader', 'sales_director')
    OR dispatched_by = auth.uid()::TEXT::uuid
  );

-- السماح بإنشاء عينات للعمليات المسموحة
CREATE POLICY "Users can create samples for their leads"
  ON samples FOR INSERT
  WITH CHECK (
    (SELECT assigned_to FROM leads WHERE id = lead_id) = auth.uid()::TEXT::uuid
    OR (SELECT role FROM users WHERE id = auth.uid()::TEXT::uuid) IN ('team_leader', 'sales_director')
  );

-- تعديل العينات
CREATE POLICY "Users can update their own samples"
  ON samples FOR UPDATE
  USING (
    (SELECT assigned_to FROM leads WHERE id = lead_id) = auth.uid()::TEXT::uuid
    OR (SELECT role FROM users WHERE id = auth.uid()::TEXT::uuid) IN ('team_leader', 'sales_director')
    OR dispatched_by = auth.uid()::TEXT::uuid
  );

-- ============================================================================
-- SAMPLE_ITEMS RLS POLICIES
-- ============================================================================

-- رؤية عناصر العينة
CREATE POLICY "Users can view sample items from their samples"
  ON sample_items FOR SELECT
  USING (
    (SELECT dispatched_by FROM samples WHERE id = sample_id) = auth.uid()::TEXT::uuid
    OR (SELECT assigned_to FROM leads WHERE id = (SELECT lead_id FROM samples WHERE id = sample_id)) = auth.uid()::TEXT::uuid
    OR (SELECT role FROM users WHERE id = auth.uid()::TEXT::uuid) IN ('team_leader', 'sales_director')
  );

-- إنشاء وتعديل عناصر العينة
CREATE POLICY "Users can manage sample items in their samples"
  ON sample_items FOR INSERT
  WITH CHECK (
    (SELECT dispatched_by FROM samples WHERE id = sample_id) = auth.uid()::TEXT::uuid
    OR (SELECT role FROM users WHERE id = auth.uid()::TEXT::uuid) = 'sales_director'::user_role
  );

CREATE POLICY "Users can update sample items in their samples"
  ON sample_items FOR UPDATE
  USING (
    (SELECT dispatched_by FROM samples WHERE id = sample_id) = auth.uid()::TEXT::uuid
    OR (SELECT assigned_to FROM leads WHERE id = (SELECT lead_id FROM samples WHERE id = sample_id)) = auth.uid()::TEXT::uuid
    OR (SELECT role FROM users WHERE id = auth.uid()::TEXT::uuid) = 'sales_director'::user_role
  );

-- ============================================================================
-- ORDERS RLS POLICIES
-- ============================================================================

-- رؤية الطلبات
CREATE POLICY "Users can view orders from their leads"
  ON orders FOR SELECT
  USING (
    (SELECT assigned_to FROM leads WHERE id = lead_id) = auth.uid()::TEXT::uuid
    OR (SELECT role FROM users WHERE id = auth.uid()::TEXT::uuid) IN ('team_leader', 'sales_director')
    OR account_manager_id = auth.uid()::TEXT::uuid
  );

-- إنشاء الطلبات
CREATE POLICY "Sales reps can create orders for their leads"
  ON orders FOR INSERT
  WITH CHECK (
    (SELECT assigned_to FROM leads WHERE id = lead_id) = auth.uid()::TEXT::uuid
    OR (SELECT role FROM users WHERE id = auth.uid()::TEXT::uuid) = 'sales_director'::user_role
  );

-- تعديل الطلبات
CREATE POLICY "Users can update their own orders"
  ON orders FOR UPDATE
  USING (
    (SELECT assigned_to FROM leads WHERE id = lead_id) = auth.uid()::TEXT::uuid
    OR (SELECT role FROM users WHERE id = auth.uid()::TEXT::uuid) = 'sales_director'::user_role
    OR account_manager_id = auth.uid()::TEXT::uuid
  );

-- ============================================================================
-- INITIAL DATA (بيانات أولية للاختبار - اختياري)
-- ============================================================================

-- INSERT INTO users (email, full_name, role, department)
-- VALUES
--   ('admin@fd-sales.com', 'Admin Director', 'sales_director', 'Management'),
--   ('leader@fd-sales.com', 'Team Leader', 'team_leader', 'Sales'),
--   ('rep@fd-sales.com', 'Sales Rep', 'sales_rep', 'Sales');

-- INSERT INTO products (sku_code, name, description, weight_grams, is_active)
-- VALUES
--   ('BREAD001', 'خبز الفينو الأسود', 'خبز فرنسي أسود عالي الجودة', 500, true),
--   ('BREAD002', 'خبز البشاميل', 'خبز لبناني تقليدي', 400, true),
--   ('BREAD003', 'خبز الحبوب الكاملة', 'خبز صحي بالحبوب الكاملة', 450, true);
