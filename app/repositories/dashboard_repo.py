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
        # EDI profit per month = interest income from loans originated that month
        result = self.session.exec(text("""
            SELECT
                TO_CHAR(loan_start_date, 'YYYY-MM') AS month,
                SUM(interest) AS edi_profit
            FROM tbl_edi_customer
            WHERE loan_start_date IS NOT NULL
            GROUP BY TO_CHAR(loan_start_date, 'YYYY-MM')
            ORDER BY TO_CHAR(loan_start_date, 'YYYY-MM')
        """))
        return [dict(row._mapping) for row in result]

    def get_monthly_expense(self):
        result = self.session.exec(text("""
            SELECT
                TO_CHAR(e."date", 'YYYY-MM') AS month,
                SUM(e.amount) AS expense
            FROM tbl_expense e
            GROUP BY TO_CHAR(e."date", 'YYYY-MM')
            ORDER BY TO_CHAR(e."date", 'YYYY-MM')
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
        # Total interest from all EDI customers — no date filter since interest
        # is a fixed per-loan field representing the portfolio's monthly income.
        result = self.session.exec(text("""
            SELECT COALESCE(SUM(interest), 0) AS total FROM tbl_edi_customer
        """))
        row = result.first()
        return float(row.total) if row else 0.0

    def get_current_month_expense(self) -> float:
        result = self.session.exec(text("""
            SELECT COALESCE(SUM(e.amount), 0) AS total
            FROM tbl_expense e
            WHERE DATE_TRUNC('month', e."date") = DATE_TRUNC('month', CURRENT_DATE)
        """))
        row = result.first()
        return float(row.total) if row else 0.0

    def get_monthly_unclaimed(self):
        result = self.session.exec(text("""
            SELECT
                TO_CHAR(date, 'YYYY-MM') AS month,
                SUM(amount) AS unclaimed
            FROM tbl_unclaimed_balance
            GROUP BY TO_CHAR(date, 'YYYY-MM')
            ORDER BY TO_CHAR(date, 'YYYY-MM')
        """))
        return [dict(row._mapping) for row in result]

    def get_monthly_defaulted(self):
        result = self.session.exec(text("""
            SELECT
                TO_CHAR(date, 'YYYY-MM') AS month,
                SUM(amount) AS defaulted
            FROM tbl_defaulted_balance
            GROUP BY TO_CHAR(date, 'YYYY-MM')
            ORDER BY TO_CHAR(date, 'YYYY-MM')
        """))
        return [dict(row._mapping) for row in result]

    def get_current_month_unclaimed(self) -> float:
        result = self.session.exec(text("""
            SELECT COALESCE(SUM(amount), 0) AS total
            FROM tbl_unclaimed_balance
            WHERE DATE_TRUNC('month', date) = DATE_TRUNC('month', CURRENT_DATE)
        """))
        row = result.first()
        return float(row.total) if row else 0.0

    def get_current_month_defaulted(self) -> float:
        result = self.session.exec(text("""
            SELECT COALESCE(SUM(amount), 0) AS total
            FROM tbl_defaulted_balance
            WHERE DATE_TRUNC('month', date) = DATE_TRUNC('month', CURRENT_DATE)
        """))
        row = result.first()
        return float(row.total) if row else 0.0

    # ── Loan summary ─────────────────────────────────────────────────────────

    def get_loan_summary_edi(self) -> dict:
        result = self.session.exec(text("""
            SELECT
                COALESCE(SUM(c.loan_amount), 0)::float AS total_loan,
                COALESCE(
                    SUM(c.loan_amount) - COALESCE(SUM(paid.total_paid), 0),
                    0
                )::float AS total_receivable
            FROM tbl_edi_customer c
            LEFT JOIN (
                SELECT customer_id, SUM(amount) AS total_paid
                FROM tbl_edi_transactions
                WHERE payment_status = 'PAID'
                GROUP BY customer_id
            ) paid ON paid.customer_id = c.customer_id
            WHERE c.is_closed = false
        """))
        row = result.first()
        return {"total_loan": float(row.total_loan), "total_receivable": float(row.total_receivable)} if row else {"total_loan": 0.0, "total_receivable": 0.0}

    def get_loan_summary_iop(self) -> dict:
        result = self.session.exec(text("""
            SELECT
                COALESCE(SUM(c.loan_amount), 0)::float AS total_loan,
                COALESCE(
                    SUM(c.loan_amount) - COALESCE(SUM(paid.total_paid), 0),
                    0
                )::float AS total_receivable
            FROM tbl_iop_customer c
            LEFT JOIN (
                SELECT customer_id, SUM(amount) AS total_paid
                FROM tbl_iop_transactions
                WHERE payment_status = 'PAID'
                GROUP BY customer_id
            ) paid ON paid.customer_id = c.customer_id
            WHERE c.is_closed = false
        """))
        row = result.first()
        return {"total_loan": float(row.total_loan), "total_receivable": float(row.total_receivable)} if row else {"total_loan": 0.0, "total_receivable": 0.0}

    # ── Active IOP customers (for reminder / calendar / dues logic) ───────────

    def get_active_iop_customers(self) -> list[dict]:
        result = self.session.exec(text("""
            SELECT
                c.customer_id,
                COALESCE(c.customer_name, '') AS customer_name,
                COALESCE(nm.customer_name_ta, '') AS tamil_name,
                COALESCE(c.loan_amount, 0)::float AS loan_amount,
                c.loan_start_date,
                COALESCE(c.interest_payment_frequency, 1)::float AS frequency,
                COALESCE(c.interest, 0)::float AS monthly_interest,
                COALESCE(c.ignore, false) AS ignore
            FROM tbl_iop_customer c
            LEFT JOIN tbl_iop_name_map nm ON nm.customer_id = c.customer_id
            WHERE c.is_closed = false
            AND c.loan_start_date IS NOT NULL
            ORDER BY c.customer_id
        """))
        return [dict(row._mapping) for row in result]

    def get_iop_paid_this_month(self) -> dict:
        result = self.session.exec(text("""
            SELECT customer_id, SUM(amount)::float AS paid
            FROM tbl_iop_transactions
            WHERE payment_status = 'PAID'
            AND DATE_TRUNC('month', collection_date) = DATE_TRUNC('month', CURRENT_DATE)
            GROUP BY customer_id
        """))
        return {row.customer_id: float(row.paid) for row in result}

    # ── EDI inactive / defaulters ─────────────────────────────────────────────

    def get_edi_overdue(self, min_days: int) -> list[dict]:
        result = self.session.exec(text("""
            SELECT
                c.customer_id,
                COALESCE(c.customer_name, '') AS customer_name,
                COALESCE(nm.customer_name_ta, '') AS tamil_name,
                COALESCE(c.loan_amount, 0)::float AS loan_amount,
                COALESCE(c.outstanding_balance, 0)::float AS outstanding_balance,
                COALESCE(c.ignore, false) AS ignore,
                MAX(t.collection_date) AS last_payment_date,
                (CURRENT_DATE - COALESCE(MAX(t.collection_date), c.loan_start_date, CURRENT_DATE)) AS days_since_payment
            FROM tbl_edi_customer c
            LEFT JOIN tbl_edi_transactions t
                ON t.customer_id = c.customer_id AND t.payment_status = 'PAID'
            LEFT JOIN tbl_edi_name_map nm ON nm.customer_id = c.customer_id
            WHERE c.is_closed = false
            GROUP BY c.customer_id, c.customer_name, nm.customer_name_ta,
                     c.loan_amount, c.outstanding_balance, c.loan_start_date, c.ignore
            HAVING (CURRENT_DATE - COALESCE(MAX(t.collection_date), c.loan_start_date, CURRENT_DATE)) >= :min_days
            ORDER BY days_since_payment DESC
        """).bindparams(min_days=min_days))
        return [dict(row._mapping) for row in result]

    def get_daily_activity(self, days: int = 30):
        result = self.session.exec(text("""
            SELECT
                d.day::date AS date,
                COALESCE(e.edi_count, 0) AS edi_count,
                COALESCE(e.edi_amount, 0) AS edi_amount,
                COALESCE(i.iop_count, 0) AS iop_count,
                COALESCE(i.iop_amount, 0) AS iop_amount
            FROM generate_series(
                CURRENT_DATE - (:days * INTERVAL '1 day'),
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
        """), {"days": days})
        return [dict(row._mapping) for row in result]
