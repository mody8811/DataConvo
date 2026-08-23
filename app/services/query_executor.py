import sqlparse
import logging
import re

from app.services.result_guardrails import enforce_dialect_limit

logger = logging.getLogger(__name__)

# Hard safety cap: a raw SELECT * can never exhaust memory even if the DB
# ignores the injected LIMIT (stream-side belt-and-suspenders).
MAX_STREAM_ROWS = 5000 + 1


class QueryExecutor:
    # ------------------------------------------------------------------
    # Original API (backward compatible): returns just the rows list.
    # Used by saved/edited query routes, dashboard widgets, data-quality
    # monitors, etc. Dialect-aware limit enforcement is applied internally.
    # ------------------------------------------------------------------
    @staticmethod
    def validate_and_execute(db, sql_query, db_type=None):
        rows, _guardrail = QueryExecutor.validate_and_execute_tiered(
            db, sql_query, db_type=db_type
        )
        return rows

    # ------------------------------------------------------------------
    # Tiered API: returns (rows, guardrail) so the chat pipeline can do
    # dynamic result tiering (small/medium/large).
    # ------------------------------------------------------------------
    @staticmethod
    def validate_and_execute_tiered(db, sql_query, db_type=None):
        # 0. Clean the query: strip markdown, comments, and whitespace
        cleaned = (sql_query or '').strip()
        cleaned = re.sub(r"^```sql\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"^```\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip()

        # 1. Parse and validate
        parsed = sqlparse.parse(cleaned)
        if not parsed:
            raise ValueError("Invalid SQL")

        statement = None
        for stmt in parsed:
            token_type = stmt.get_type()
            if token_type and token_type != 'UNKNOWN':
                statement = stmt
                break

        if not statement:
            raise ValueError("Could not parse a valid SQL statement.")

        first_token = None
        for token in statement.flatten():
            if token.ttype in (sqlparse.tokens.Keyword, sqlparse.tokens.Keyword.DML):
                first_token = token.value.upper()
                break
            elif token.ttype in sqlparse.tokens.Comment:
                continue
            elif token.ttype in sqlparse.tokens.Whitespace:
                continue
            elif token.ttype == sqlparse.tokens.Name:
                first_token = token.value.upper()
                break

        if first_token != 'SELECT':
            raise ValueError("Only SELECT statements are permitted for security reasons.")

        if len(parsed) > 1:
            raise ValueError("Multi-statement queries are not allowed.")

        # 2. Apply dialect-aware row limit (aggregation-exempt) via SQLGlot
        limited_sql, applied, reason = enforce_dialect_limit(cleaned, db_type)
        guardrail = {
            'db_type': db_type,
            'limit_applied': applied,
            'limit_reason': reason,
        }

        # 3. Execute safely via SQLAlchemy
        engine = db._engine
        with engine.connect() as conn:
            cursor = conn.exec_driver_sql(limited_sql)
            columns = list(cursor.keys())
            rows = []
            for row in cursor:
                row_dict = {}
                for idx, col in enumerate(columns):
                    value = row[idx]
                    if hasattr(value, 'isoformat'):
                        row_dict[col] = value.isoformat()
                    else:
                        row_dict[col] = value
                rows.append(row_dict)
                if len(rows) >= MAX_STREAM_ROWS:
                    guardrail['stream_capped'] = True
                    logger.warning(
                        f"QueryExecutor stream capped at {MAX_STREAM_ROWS} rows "
                        f"(db_type={db_type}, reason={reason})"
                    )
                    break
            guardrail['row_count'] = len(rows)

        logger.info(
            f"QueryExecutor executed SQL: {limited_sql} "
            f"(db_type={db_type}, limit_reason={reason})"
        )
        logger.info(f"QueryExecutor returned {len(rows)} rows")
        return rows, guardrail