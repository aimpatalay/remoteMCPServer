from fastmcp import FastMCP
from typing import Optional
from decimal import Decimal
from datetime import date as DateType, datetime
import os
import json
import psycopg
from psycopg.rows import dict_row


DATABASE_URL = os.getenv("DATABASE_URL")
CATEGORIES_PATH = os.path.join(os.path.dirname(__file__), "categories.json")

mcp = FastMCP("ExpenseTracker")


def get_connection():
    """
    Open a new connection to the Postgres expense database.

    This helper is used internally by every MCP tool that reads from or writes
    to the expenses table.

    The database URL must point to a Postgres database and should include SSL
    settings when required by the host.

    Expected format:
    postgresql://USER:PASSWORD@HOST/DATABASE?sslmode=require

    This is not an MCP tool. The LLM should never call this directly.
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
    Convert database values into JSON safe values before returning them to the LLM.

    Postgres can return Decimal, date, and datetime objects. Those types are not
    always directly JSON serializable, so this helper converts them into simple
    float or ISO date string values.

    This is not an MCP tool. It is used internally before returning query results.
    """
    if isinstance(value, Decimal):
        return float(value)

    if isinstance(value, (DateType, datetime)):
        return value.isoformat()

    return value


def serialize_row(row: dict):
    """
    Convert one Postgres row into a JSON friendly dictionary.

    This is used by list and summary tools before results are returned to the LLM.
    """
    return {key: serialize_value(value) for key, value in row.items()}


def init_db():
    """
    Initialize the expense tracking database.

    This function creates the expenses table and date index if they do not exist.

    This runs when the server starts. It is not exposed as an MCP tool and should
    not be called by the LLM.
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
    MCP tool name: add_expense

    Use this tool when the user wants to add, save, record, log, or create a new
    expense transaction.

    This tool inserts exactly one expense into the Postgres expenses table.

    Good user requests for this tool:
    "Add $12.50 for lunch today."
    "Log a $45 Uber ride under Transportation."
    "Record $120 for groceries on 2026-07-05."
    "I spent $30 on medicine yesterday."

    Required inputs:
        user_id: The user identifier for the person who owns this expense.
        date: The expense date in YYYY-MM-DD format.
        amount: The amount spent as a number. Do not include a currency symbol.
        category: The main expense category, such as Food & Dining,
            Transportation, Shopping, Healthcare, Travel, Business, or Other.

    Optional inputs:
        subcategory: More specific expense type, such as Groceries, Airline,
            Uber, Pharmacy, Rent, or Coffee.
        note: Extra details from the user, such as merchant name, purpose,
            location, or context.

    Important guidance for the LLM:
        Use this tool only for adding a new expense.
        Do not use this tool to retrieve old expenses.
        Do not use this tool to calculate totals.
        Do not use this tool to summarize categories.
        If the user gives a natural date like today or yesterday, convert it
        to YYYY-MM-DD before calling the tool.
        If the category is unclear, choose the closest category or use Other.
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
    MCP tool name: list_expenses

    Use this tool when the user wants to see individual expense records within
    a date range.

    This tool returns itemized expenses, not just a total.

    Good user requests for this tool:
    "Show my expenses from July 1 to July 5."
    "List all transactions this week."
    "What did I spend money on yesterday?"
    "Show my recent expenses."

    Required inputs:
        user_id: The user identifier whose expenses should be listed.
        start_date: First date to include, in YYYY-MM-DD format.
        end_date: Last date to include, in YYYY-MM-DD format.

    Output:
        A list of matching expense rows ordered by newest date first.
        Each row includes id, user_id, date, amount, category, subcategory,
        note, and created_at.

    Important guidance for the LLM:
        Use this tool when the user wants details or transaction history.
        Do not use this tool when the user only wants the total amount spent.
        Do not use this tool when the user wants a category breakdown.
        If the user says this month, this week, today, or yesterday, convert
        that phrase into a start_date and end_date before calling the tool.
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
    MCP tool name: summarize

    Use this tool when the user wants a category level summary or spending
    breakdown for a date range.

    This tool groups expenses by category and returns the total amount and
    number of expenses in each category.

    Good user requests for this tool:
    "Summarize my spending this month."
    "Break down my expenses by category."
    "How much did I spend by category last week?"
    "Summarize only my Travel expenses this year."

    Required inputs:
        user_id: The user identifier whose expenses should be summarized.
        start_date: First date to include, in YYYY-MM-DD format.
        end_date: Last date to include, in YYYY-MM-DD format.

    Optional input:
        category: Use this only when the user asks for one specific category.
            If omitted, the tool summarizes all categories.

    Output:
        A list of category summary rows. Each row includes category,
        total_amount, and count.

    Important guidance for the LLM:
        Use this tool for grouped summaries and category breakdowns.
        Do not use this tool to add a new expense.
        Do not use this tool when the user wants itemized transaction details.
        Do not use this tool when the user asks only for one grand total across
        all categories. For that, use get_total_expenses.
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
    MCP tool name: get_total_expenses

    Use this tool when the user wants the total amount spent in a date range.

    This tool returns one grand total and the number of matching expense records.

    Good user requests for this tool:
    "How much did I spend this month?"
    "What is my total spending today?"
    "Total my expenses from July 1 to July 5."
    "How many expenses did I record last week?"

    Required inputs:
        user_id: The user identifier whose expenses should be totaled.
        start_date: First date to include, in YYYY-MM-DD format.
        end_date: Last date to include, in YYYY-MM-DD format.

    Output:
        user_id, start_date, end_date, total_amount, and count.

    Important guidance for the LLM:
        Use this tool for one total amount across the whole date range.
        Do not use this tool to add a new expense.
        Do not use this tool when the user wants a category breakdown.
        Do not use this tool when the user wants itemized transaction details.
        If no expenses exist in the range, the tool returns total_amount as 0.
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
    """
    MCP resource URI: expense:///categories

    Use this resource when the LLM needs to understand the available expense
    categories before choosing a category for add_expense or summarize.

    This resource returns a JSON object containing supported spending categories.

    Good use cases:
    The user asks what categories are available.
    The user gives an expense and the LLM wants to select the closest category.
    The user asks to classify an expense before saving it.

    Important guidance for the LLM:
        This is a resource, not a tool that changes the database.
        Use this only to read available categories.
        To save an expense, call add_expense after choosing the category.
    """
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