"""
sales_reports_engine.py
محرك معالجة وتوليد تقارير المبيعات وتحليل البيانات المالية.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd


class SalesReportsEngine:

    def __init__(self, data: Optional[pd.DataFrame] = None):
        """
        تهيئة محرك التقارير مع إمكانية تمرير DataFrame مباشرة.
        الأعمدة المتوقعة في البيانات:
        - date: تاريخ المعاملة
        - order_id: رقم الطلب
        - customer_id: رقم العميل
        - product: اسم أو كود المنتج
        - category: تصنيف المنتج
        - quantity: الكمية
        - unit_price: سعر الوحدة
        - discount: الخصم الإجمالي أو النسبي
        - cost: تكلفة البضاعة المباعة (اختياري لحساب الأرباح)
        """
        self.df = data if data is not None else pd.DataFrame()

    def load_from_csv(self, file_path: str, **kwargs) -> "SalesReportsEngine":
        """تحميل البيانات من ملف CSV."""
        self.df = pd.read_csv(file_path, **kwargs)
        self._prepare_data()
        return self

    def load_from_dict(
        self, records: List[Dict[str, Any]]
    ) -> "SalesReportsEngine":
        """تحميل البيانات من قائمة قواميس (JSON/API response)."""
        self.df = pd.DataFrame(records)
        self._prepare_data()
        return self

    def _prepare_data(self) -> None:
        """تنظيف البيانات وحساب الحقول المالية التلقائية."""
        if self.df.empty:
            return

        # توحيد صيغة التاريخ
        if "date" in self.df.columns:
            self.df["date"] = pd.to_datetime(self.df["date"])

        # التأكد من صحة الأعمدة الرقمية
        numeric_cols = ["quantity", "unit_price", "discount", "cost"]
        for col in numeric_cols:
            if col in self.df.columns:
                self.df[col] = pd.to_numeric(self.df[col], errors="coerce").fillna(0)
            else:
                self.df[col] = 0.0

        # حساب إجمالي المبيعات، الصافي، والأرباح
        self.df["gross_sales"] = self.df["quantity"] * self.df["unit_price"]
        self.df["net_sales"] = self.df["gross_sales"] - self.df["discount"]
        self.df["total_cost"] = self.df["quantity"] * self.df["cost"]
        self.df["gross_profit"] = self.df["net_sales"] - self.df["total_cost"]

    def get_kpis(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> Dict[str, Any]:
        """استخراج المؤشرات الرئيسية للأداء (KPIs) للفترة المحددة."""
        filtered_df = self._filter_by_date(start_date, end_date)

        if filtered_df.empty:
            return {
                "total_revenue": 0.0,
                "total_orders": 0,
                "total_units_sold": 0,
                "gross_profit": 0.0,
                "average_order_value": 0.0,
                "profit_margin_pct": 0.0
            }

        total_rev = float(filtered_df["net_sales"].sum())
        total_profit = float(filtered_df["gross_profit"].sum())
        orders_count = (
            int(filtered_df["order_id"].nunique())
            if "order_id" in filtered_df.columns
            else len(filtered_df)
        )
        units_sold = int(filtered_df["quantity"].sum())

        return {
            "total_revenue": round(total_rev, 2),
            "total_orders": orders_count,
            "total_units_sold": units_sold,
            "gross_profit": round(total_profit, 2),
            "average_order_value": round(total_rev / orders_count, 2) if orders_count else 0.0,
            "profit_margin_pct": round((total_profit / total_rev * 100), 2) if total_rev else 0.0
        }

    def generate_periodic_report(
        self,
        freq: str = "ME"
    ) -> pd.DataFrame:
        """
        تجميع المبيعات زمنياً:
        freq: 'D' (يومي), 'W' (أسبوعي), 'ME' (شهري), 'QE' (ربع سنوي), 'YE' (سنوي)
        """
        if self.df.empty or "date" not in self.df.columns:
            return pd.DataFrame()

        report = (
            self.df.set_index("date")
            .resample(freq)
            .agg(
                orders_count=("order_id", "nunique"),
                units_sold=("quantity", "sum"),
                gross_revenue=("gross_sales", "sum"),
                total_discounts=("discount", "sum"),
                net_revenue=("net_sales", "sum"),
                gross_profit=("gross_profit", "sum")
            )
            .reset_index()
        )
        return report

    def breakdown_by_dimension(
        self,
        dimension: str = "category",
        top_n: Optional[int] = None
    ) -> pd.DataFrame:
        """
        تقسيم المبيعات حسب بعد معين (مثل category, product, customer_id).
        """
        if self.df.empty or dimension not in self.df.columns:
            return pd.DataFrame()

        breakdown = (
            self.df.groupby(dimension)
            .agg(
                orders_count=("order_id", "nunique"),
                units_sold=("quantity", "sum"),
                net_revenue=("net_sales", "sum"),
                gross_profit=("gross_profit", "sum")
            )
            .reset_index()
            .sort_values(by="net_revenue", ascending=False)
        )

        total_rev = breakdown["net_revenue"].sum()
        breakdown["revenue_share_pct"] = (
            (breakdown["net_revenue"] / total_rev * 100).round(2) if total_rev else 0.0
        )

        if top_n:
            return breakdown.head(top_n)
        return breakdown

    def _filter_by_date(
        self,
        start_date: Optional[str],
        end_date: Optional[str]
    ) -> pd.DataFrame:
        """تصفية البيانات داخلياً بناءً على نطاق زمني."""
        if self.df.empty or "date" not in self.df.columns:
            return self.df

        sub_df = self.df.copy()
        if start_date:
            sub_df = sub_df[sub_df["date"] >= pd.to_datetime(start_date)]
        if end_date:
            sub_df = sub_df[sub_df["date"] <= pd.to_datetime(end_date)]
        return sub_df

    def export_summary(self, export_path: str, file_format: str = "csv") -> None:
        """تصدير تقرير شهري موجز إلى ملف CSV أو Excel."""
        report = self.generate_periodic_report(freq="ME")
        if file_format.lower() == "excel":
            report.to_excel(export_path, index=False)
        else:
            report.to_csv(export_path, index=False)


# --- مثال استخدام مباشر للتجربة ---
if __name__ == "__main__":
    sample_data = [
        {"date": "2026-01-05", "order_id": 101, "customer_id": "C1", "product": "Laptop", "category": "Electronics", "quantity": 1, "unit_price": 1200.0, "discount": 50.0, "cost": 900.0},
        {"date": "2026-01-12", "order_id": 102, "customer_id": "C2", "product": "Mouse", "category": "Accessories", "quantity": 2, "unit_price": 25.0, "discount": 0.0, "cost": 10.0},
        {"date": "2026-02-01", "order_id": 103, "customer_id": "C1", "product": "Keyboard", "category": "Accessories", "quantity": 1, "unit_price": 75.0, "discount": 5.0, "cost": 35.0},
        {"date": "2026-02-18", "order_id": 104, "customer_id": "C3", "product": "Monitor", "category": "Electronics", "quantity": 1, "unit_price": 300.0, "discount": 20.0, "cost": 210.0}
    ]

    engine = SalesReportsEngine().load_from_dict(sample_data)

    print("=== مؤشرات الأداء الأساسية (KPIs) ===")
    print(engine.get_kpis())

    print("\n=== المبيعات حسب التصنيف (Category Breakdown) ===")
    print(engine.breakdown_by_dimension("category"))

    print("\n=== التقرير الشهري ===")
    print(engine.generate_periodic_report(freq="ME"))
