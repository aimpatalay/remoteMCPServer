from fastmcp import FastMCP
from typing import Optional
from decimal import Decimal
from datetime import date as DateType, datetime
import os
import json
import psycopg
from psycopg.rows import dict_row


DATABASE_URL =  "postgresql://neondb_owner:npg_5GpVnUuhkC4W@ep-solitary-forest-apyq6bgu-pooler.c-7.us-east-1.aws.neon.tech/neondb?sslmode=require"
CATEGORIES_PATH = os.path.join(os.path.dirname(__file__), "categories.json")

mcp = FastMCP("ExpenseTracker")


def get_connection():
    """
    Create a new Postgres connection.

    DATABASE_URL should look like:
    postgresql://neondb_owner:YOUR_PASSWORD@POOLER_HOST/neondb?sslmode=require
    """
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL environment variable is not set")

    return psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row,
        prepare_threshold=None,
    )


def serialize_value(value):
    """
    Convert Postgres values into JSON friendly values.
    """
    if isinstance(value, Decimal):
        return float(value)

    if isinstance(value, (DateType, datetime)):
        return value.isoformat()

    return value


def serialize_row(row: dict):
    return {key: serialize_value(value) for key, value in row.items()}


def init_db():
    """
    Create the expenses table if it does not already exist.
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS expenses (
                        id BIGSERIAL PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        date DATE NOT NULL,
                        amount NUMERIC(12, 2) NOT NULL,
                        category TEXT NOT NULL,
                        subcategory TEXT DEFAULT '',
                        note TEXT DEFAULT '',
                        created_at TIMESTAMPTZ DEFAULT NOW()
                    );
                    """
                )

                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_expenses_user_date
                    ON expenses (user_id, date DESC);
                    """
                )

            conn.commit()

        print("Postgres database initialized successfully")

    except Exception as e:
        print(f"Database initialization error: {e}")
        raise


@mcp.tool()
def add_expense(
    user_id: str,
    date: str,
    amount: float,
    category: str,
    subcategory: str = "",
    note: str = "",
):
    """
    Add a new expense entry to the Postgres database.

    Args:
        user_id: User identifier. For now this is passed in directly.
        date: Date of the expense in YYYY-MM-DD format.
        amount: Amount spent.
        category: Top-level category, for example Transportation.
        subcategory: Optional subcategory, for example Airline.
        note: Optional free-text note.
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO expenses (
                        user_id,
                        date,
                        amount,
                        category,
                        subcategory,
                        note
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id;
                    """,
                    (
                        user_id,
                        date,
                        amount,
                        category,
                        subcategory,
                        note,
                    ),
                )

                row = cur.fetchone()
                conn.commit()

                return {
                    "status": "success",
                    "id": row["id"],
                    "message": "Expense added successfully",
                }

    except Exception as e:
        return {
            "status": "error",
            "message": f"Database error: {str(e)}",
        }


@mcp.tool()
def list_expenses(
    user_id: str,
    start_date: str,
    end_date: str,
):
    """
    List expense entries for one user within an inclusive date range.

    Args:
        user_id: User identifier.
        start_date: Start date in YYYY-MM-DD format.
        end_date: End date in YYYY-MM-DD format.
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        id,
                        user_id,
                        date,
                        amount,
                        category,
                        subcategory,
                        note,
                        created_at
                    FROM expenses
                    WHERE user_id = %s
                      AND date BETWEEN %s AND %s
                    ORDER BY date DESC, id DESC;
                    """,
                    (user_id, start_date, end_date),
                )

                rows = cur.fetchall()
                return [serialize_row(row) for row in rows]

    except Exception as e:
        return {
            "status": "error",
            "message": f"Error listing expenses: {str(e)}",
        }


@mcp.tool()
def summarize(
    user_id: str,
    start_date: str,
    end_date: str,
    category: Optional[str] = None,
):
    """
    Summarize expenses by category for one user.

    Args:
        user_id: User identifier.
        start_date: Start date in YYYY-MM-DD format.
        end_date: End date in YYYY-MM-DD format.
        category: Optional category filter.
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                if category:
                    cur.execute(
                        """
                        SELECT
                            category,
                            SUM(amount) AS total_amount,
                            COUNT(*) AS count
                        FROM expenses
                        WHERE user_id = %s
                          AND date BETWEEN %s AND %s
                          AND category = %s
                        GROUP BY category
                        ORDER BY total_amount DESC;
                        """,
                        (user_id, start_date, end_date, category),
                    )
                else:
                    cur.execute(
                        """
                        SELECT
                            category,
                            SUM(amount) AS total_amount,
                            COUNT(*) AS count
                        FROM expenses
                        WHERE user_id = %s
                          AND date BETWEEN %s AND %s
                        GROUP BY category
                        ORDER BY total_amount DESC;
                        """,
                        (user_id, start_date, end_date),
                    )

                rows = cur.fetchall()
                return [serialize_row(row) for row in rows]

    except Exception as e:
        return {
            "status": "error",
            "message": f"Error summarizing expenses: {str(e)}",
        }


@mcp.tool()
def get_total_expenses(
    user_id: str,
    start_date: str,
    end_date: str,
):
    """
    Get total expenses for one user within a date range.
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        COALESCE(SUM(amount), 0) AS total_amount,
                        COUNT(*) AS count
                    FROM expenses
                    WHERE user_id = %s
                      AND date BETWEEN %s AND %s;
                    """,
                    (user_id, start_date, end_date),
                )

                row = cur.fetchone()

                return {
                    "user_id": user_id,
                    "start_date": start_date,
                    "end_date": end_date,
                    "total_amount": serialize_value(row["total_amount"]),
                    "count": row["count"],
                }

    except Exception as e:
        return {
            "status": "error",
            "message": f"Error calculating total expenses: {str(e)}",
        }


@mcp.resource("expense:///categories", mime_type="application/json")
def categories():
    default_categories = {
        "categories": [
            "Food & Dining",
            "Transportation",
            "Shopping",
            "Entertainment",
            "Bills & Utilities",
            "Healthcare",
            "Travel",
            "Education",
            "Business",
            "Other",
        ]
    }

    try:
        with open(CATEGORIES_PATH, "r", encoding="utf-8") as f:
            return f.read()

    except FileNotFoundError:
        return json.dumps(default_categories, indent=2)

    except Exception as e:
        return json.dumps(
            {"error": f"Could not load categories: {str(e)}"},
            indent=2,
        )


if __name__ == "__main__":
    init_db()

    port = int(os.getenv("PORT", "8000"))

    mcp.run(
        transport="http",
        host="0.0.0.0",
        port=port,
    )