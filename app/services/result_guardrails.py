"""Production-grade execution guardrails.

Implements:
  1. Dynamic dialect-aware row-limit injection via SQLGlot AST (TOP for T-SQL,
     LIMIT for Postgres/MySQL/SQLite/ClickHouse/DuckDB/Snowflake/BigQuery,
     FETCH FIRST for Oracle) with a STRICT exemption for aggregation queries.
  2. Dynamic result tiering (Databricks strategy) so raw multi-thousand-row
     dumps never crash the app or spam the chat payload:
         small   (< 500 rows)         -> full dataset in chat
         medium  (500 .. 5,000 rows)  -> capped payload, client-side pagination
         large   (> 5,000 or SELECT *) -> intercept + pivot suggestions
"""
import logging
import re

import sqlglot
from sqlglot import exp

logger = logging.getLogger(__name__)

# ---- Tier boundaries ------------------------------------------------------
SMALL_RESULT_MAX = 500          # < 500 rows: return full dataset
MEDIUM_RESULT_MAX = 5000        # 500 .. 5,000: payload capped, paginate client-side

# SQLGlot dialect name mapping (from our app's db_type values)
DIALECT_ALIASES = {
    'postgresql': 'postgres',
    'postgres': 'postgres',
    'mssql': 'tsql',
    'sqlserver': 'tsql',
    'mysql': 'mysql',
    'sqlite': 'sqlite',
    'clickhouse': 'clickhouse',
    'duckdb': 'duckdb',
    'snowflake': 'snowflake',
    'bigquery': 'bigquery',
    'oracle': 'oracle',
}


def _to_sqlglot_dialect(db_type):
    """Map our app db_type strings to SQLGlot dialect names."""
    if not db_type:
        return None
    key = str(db_type).lower().replace('-', '').replace('_', '')
    return DIALECT_ALIASES.get(key, key)


def _is_aggregate_query(parsed):
    """STRICT EXEMPTION: never inject caps into analytical queries.

    Detects GROUP BY / HAVING as well as aggregate functions
    (COUNT, SUM, AVG, MIN, MAX) even without an explicit GROUP BY clause.
    """
    if parsed.find(exp.Group):
        return True
    if parsed.find(exp.Having):
        return True
    agg_funcs = (exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max)
    return any(parsed.find(f) for f in agg_funcs)


def _has_existing_limit(parsed):
    if parsed.find(exp.Limit):
        return True
    # Oracle / standard FETCH FIRST … ROWS ONLY
    if parsed.find(exp.Fetch):
        return True
    return False


def enforce_dialect_limit(sql_string, db_type, limit=5000):
    """Parse a SELECT via SQLGlot and safely apply row limits by dialect.

    Aggregation queries (GROUP BY / aggregates) are returned untouched so the
    database engine can process millions of rows and stream the lightweight
    aggregate summary. Queries that already contain a LIMIT/TOP/FETCH are
    preserved (only capped down if they exceed `limit`).

    Returns (sql, applied: bool, reason: str).
    """
    if not sql_string or not sql_string.strip():
        return sql_string, False, 'empty'

    cleaned = sql_string.strip()
    cleaned = re.sub(r"^```sql\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^```\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = cleaned.strip()

    dialect = _to_sqlglot_dialect(db_type)
    try:
        parsed = sqlglot.parse_one(cleaned, read=dialect) if dialect else sqlglot.parse_one(cleaned)
    except Exception as e:
        logger.warning('SQLGlot parse failed (%s) — skipping limit injection: %s', db_type, e)
        return cleaned, False, 'parse_error'

    # 1. STRICT EXEMPTION for aggregation queries
    if _is_aggregate_query(parsed):
        return parsed.sql(dialect=dialect) if dialect else parsed.sql(), False, 'aggregate_exempt'

    # 2. If the query already has a limit, only tighten if it exceeds ours.
    #    SQLGlot normalizes T-SQL TOP into exp.Limit, so exp.Limit covers both
    #    LIMIT (Postgres/MySQL/SQLite) and TOP (SQL Server).
    if _has_existing_limit(parsed):
        existing = None
        found_limit = parsed.find(exp.Limit)
        if found_limit:
            try:
                expression = found_limit.args.get('expression')
                existing = int(expression.this)
            except (TypeError, ValueError, AttributeError):
                existing = None
        # Existing limit tighter than or equal to our cap -> leave untouched
        if existing is not None and existing <= limit:
            return parsed.sql(dialect=dialect) if dialect else parsed.sql(), False, 'existing_limit'

    # 3. Inject the limit AST node and transpile back to the target dialect.
    #    SQLGlot automatically renders TOP for T-SQL, LIMIT for Postgres/DuckDB,
    #    and FETCH FIRST for Oracle.
    try:
        limited = parsed.limit(limit)
        return limited.sql(dialect=dialect) if dialect else limited.sql(), True, 'limit_injected'
    except Exception as e:
        logger.warning('SQLGlot limit injection failed (%s): %s', db_type, e)
        return cleaned, False, 'inject_error'


def is_broad_select_star(sql_string, db_type=None):
    """Detect unconstrained `SELECT * FROM table` (no WHERE/TOP/LIMIT/agg)."""
    if not sql_string:
        return False
    cleaned = re.sub(r"```sql|```", "", sql_string, flags=re.IGNORECASE).strip()
    try:
        dialect = _to_sqlglot_dialect(db_type)
        parsed = sqlglot.parse_one(cleaned, read=dialect) if dialect else sqlglot.parse_one(cleaned)
    except Exception:
        return False

    star = any(True for _ in parsed.find_all(exp.Star))

    return (
        star
        and not _is_aggregate_query(parsed)
        and not _has_existing_limit(parsed)
        and not parsed.find(exp.Where)
        and not parsed.find(exp.Order)
    )


def classify_result_size(row_count, limit_applied=False):
    """Databricks-style dynamic result tiering.

    `limit_applied` should be the guardrail's `limit_applied` flag — a row cap
    was actually injected into the query. A result only counts as "capped"
    when a limit was applied AND the returned count reached/exceeded the cap;
    an exact 5,000-row result with no injected limit is a complete delivery.
    """
    if row_count < SMALL_RESULT_MAX:
        return 'small', {
            'tier': 'small',
            'row_count': row_count,
            'delivery': 'full',
            'capped': False,
            'message': 'Complete dataset displayed.',
        }
    if row_count <= MEDIUM_RESULT_MAX:
        # "capped" only reflects whether the returned dataset actually hit or
        # exceeded the injected limit; 500..4,999 rows (and exact-5,000 with no
        # injected cap) are complete deliveries just rendered with pagination.
        capped = bool(limit_applied) and row_count >= MEDIUM_RESULT_MAX
        return 'medium', {
            'tier': 'medium',
            'row_count': row_count,
            'delivery': 'capped',
            'capped': capped,
            'page_size': 100,
            'pages': max(1, (row_count + 99) // 100),
            'message': (
                f'⚠️ Capped result: Displaying the first {MEDIUM_RESULT_MAX} rows of a larger dataset.'
                if capped else
                f'ℹ️ Complete result: Delivered all {row_count} rows — paginated for performance.'
            ),
        }
    return 'large', {
        'tier': 'large',
        'row_count': row_count,
        'delivery': 'pivot',
        'capped': False,
        'message': (
            f'This query would return {row_count:,} rows — too many for the chat window. '
            'Consider an aggregate summary, a top-N sample, or export.'
        ),
    }


def build_pivot_suggestions(table_name=None, question=''):
    """Suggest pivot actions for large unconstrained result sets."""
    suggestions = []
    if table_name:
        suggestions.append({
            'type': 'aggregate',
            'label': 'Summarize with GROUP BY',
            'sql': None,
            'prompt': f'Summarize {table_name} with an aggregate GROUP BY query (counts / sums / averages per key column).',
        })
        suggestions.append({
            'type': 'top_n',
            'label': 'Top-N sample',
            'sql': None,
            'prompt': f'Show me a sample of records from {table_name} ordered by a meaningful column, capped at 50 rows.',
        })
    suggestions.append({
        'type': 'dashboard',
        'label': 'Push to Dashboard Studio',
        'sql': None,
        'prompt': question,
    })
    suggestions.append({
        'type': 'export',
        'label': 'Bulk CSV / Parquet export',
        'sql': None,
        'prompt': question,
    })
    return suggestions