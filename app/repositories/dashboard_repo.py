from datetime import date
from sqlmodel import Session, text


class DashboardRepo:
    def __init__(self, session: Session):
        self.session = session

    def get_monthly_iop_profit(self):
        result = self.session.exec(text("""
            SELECT
                TO_CHAR(collection_date, 'YYYY-MM') AS month,
                SUM(amount) AS iop_profit
            FROM tbl_iop_transactions
            WHERE payment_status = 'PAID'
            GROUP BY TO_CHAR(collection_date, 'YYYY-MM')
            ORDER BY TO_CHAR(collection_date, 'YYYY-MM')
        """))
        return [dict(row._mapping) for row in result]

    def get_monthly_edi_profit(self):
        result = self.session.exec(text("""
            SELECT
                TO_CHAR(loan_start_date, 'YYYY-MM') AS month,
                SUM(interest) AS edi_profit
            FROM tbl_edi_customer
            GROUP BY TO_CHAR(loan_start_date, 'YYYY-MM')
            ORDER BY TO_CHAR(loan_start_date, 'YYYY-MM')
        """))
        return [dict(row._mapping) for row in result]

    def get_monthly_expense(self):
        result = self.session.exec(text("""
            SELECT
                TO_CHAR(date, 'YYYY-MM') AS month,
                SUM(amount) AS expense
            FROM tbl_expense
            GROUP BY TO_CHAR(date, 'YYYY-MM')
            ORDER BY TO_CHAR(date, 'YYYY-MM')
        """))
        return [dict(row._mapping) for row in result]

    def get_current_month_iop_profit(self) -> float:
        result = self.session.exec(text("""
            SELECT COALESCE(SUM(amount), 0) AS total
            FROM tbl_iop_transactions
            WHERE payment_status = 'PAID'
            AND DATE_TRUNC('month', collection_date) = DATE_TRUNC('month', CURRENT_DATE)
        """))
        row = result.first()
        return float(row.total) if row else 0.0

    def get_current_month_edi_profit(self) -> float:
        result = self.session.exec(text("""
            SELECT COALESCE(SUM(interest), 0) AS total
            FROM tbl_edi_customer
            WHERE DATE_TRUNC('month', loan_start_date) = DATE_TRUNC('month', CURRENT_DATE)
        """))
        row = result.first()
        return float(row.total) if row else 0.0

    def get_current_month_expense(self) -> float:
        result = self.session.exec(text("""
            SELECT COALESCE(SUM(amount), 0) AS total
            FROM tbl_expense
            WHERE DATE_TRUNC('month', date) = DATE_TRUNC('month', CURRENT_DATE)
        """))
        row = result.first()
        return float(row.total) if row else 0.0

    def get_daily_activity(self, days: int = 30):
        result = self.session.exec(text(f"""
            SELECT
                d.day::date AS date,
                COALESCE(e.edi_count, 0) AS edi_count,
                COALESCE(e.edi_amount, 0) AS edi_amount,
                COALESCE(i.iop_count, 0) AS iop_count,
                COALESCE(i.iop_amount, 0) AS iop_amount
            FROM generate_series(
                CURRENT_DATE - INTERVAL '{days} days',
                CURRENT_DATE,
                '1 day'
            ) d(day)
            LEFT JOIN (
                SELECT collection_date, COUNT(*) AS edi_count, SUM(amount) AS edi_amount
                FROM tbl_edi_transactions GROUP BY collection_date
            ) e ON e.collection_date = d.day::date
            LEFT JOIN (
                SELECT collection_date, COUNT(*) AS iop_count, SUM(amount) AS iop_amount
                FROM tbl_iop_transactions GROUP BY collection_date
            ) i ON i.collection_date = d.day::date
            ORDER BY d.day
        """))
        return [dict(row._mapping) for row in result]
