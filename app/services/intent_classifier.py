"""
Tier-1 Dynamic Intent Classifier (DEPRECATED — feature-flagged, OFF by default)
===============================================================================
NOTE: This module is DEPRECATED. The pre-flight classifier step is BYPASSED by
default. Set `ENABLE_QUERY_CLASSIFIER=true` in the environment to re-enable it
for comparative testing.

Kept intact (not deleted) so it can be rapidly restored if A/B testing shows
the legacy classifier route is necessary for edge cases.

Uses OpenRouter's free `openai/gpt-oss-20b:free` endpoint to classify
user prompts BEFORE the heavy SQL generation LLM. Replaces brittle
hardcoded string-array routing with a dynamic LLM-powered classifier.

Also exposes a `quote_identifier` helper that safely quotes SQL
reserved words per dialect. NOTE: `quote_identifier` is still actively
used by SQLChainService and must remain available.
"""
import json
import os
import logging

from openai import OpenAI

logger = logging.getLogger(__name__)

_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
_OPENROUTER_MODEL = "openai/gpt-oss-20b:free"

SQL_RESERVED_WORDS = {
    "user", "users", "order", "orders", "group", "groups", "select",
    "from", "where", "having", "table", "index", "key", "primary",
    "foreign", "default", "check", "unique", "constraint", "references",
    "join", "inner", "outer", "left", "right", "full", "cross", "on",
    "as", "and", "or", "not", "null", "is", "in", "like", "between",
    "exists", "all", "any", "union", "distinct", "top", "limit",
    "offset", "case", "when", "then", "else", "end", "cast", "convert",
    "database", "schema", "column", "value", "values", "insert",
    "update", "delete", "create", "drop", "alter", "grant", "revoke",
    "set", "show", "use", "describe", "explain", "analyze", "if",
    "else", "while", "for", "return", "view", "procedure", "function",
    "trigger", "date", "time", "timestamp", "year", "char", "varchar",
    "text", "int", "integer", "bigint", "decimal", "numeric", "float",
    "double", "real", "boolean", "bit", "enum", "json", "xml",
}


class IntentClassifier:
    """Classifies user prompts into an intent using a fast, cheap LLM."""

    def __init__(self):
        self.client = OpenAI(
            base_url=_OPENROUTER_BASE_URL,
            api_key=os.environ.get("OPENROUTER_API_KEY", ""),
            default_headers={
                "HTTP-Referer": os.environ.get("SITE_URL", "https://data-convo.app"),
                "X-Title": "Data Convo"
            },
        )

    def classify(self, prompt: str, active_tables: list, all_tables: list,
                 active_columns: dict = None) -> dict:
        """
        Classify a user prompt.

        active_columns: optional dict mapping active table name -> [column names].
        Used to prevent column names from being misclassified as disabled tables.

        Returns:
            {"intent": "GREETING"|"OFF_TOPIC"|"DISABLED_TABLE"|"DATA_QUERY",
             "target_table": "table_name_or_null"}
        """
        # Graceful fallback if no API key configured: treat as data query
        if not os.environ.get("OPENROUTER_API_KEY"):
            logger.warning("OPENROUTER_API_KEY not set. Defaulting to DATA_QUERY.")
            return {"intent": "DATA_QUERY", "target_table": None}

        active_str = ", ".join(active_tables) if active_tables else "(none)"
        all_str = ", ".join(all_tables) if all_tables else "(none)"

        system_prompt = (
            "You are an intent classifier for a database chat assistant. "
            "Given the user's prompt and the list of ALL database tables (both active and inactive), "
            "classify the intent into exactly one of: "
            "GREETING (salutations, small talk, thanks), "
            "OFF_TOPIC (ONLY standalone, clearly non-database requests like 'What is the weather outside?', "
            "'Tell me a joke', 'What's the latest news?' — NOT data questions that merely mention "
            "weather conditions, news articles, or other fields as part of a database query), "
            "DISABLED_TABLE (ONLY when the user EXPLICITLY names a table that is NOT in the active list "
            "or exists in the database but is currently disabled — NEVER from a column name, "
            "NEVER from a trailing word, and NEVER when a valid active table is clearly stated), "
            "DATA_QUERY (ANY question about data from the active tables — the DEFAULT). "
            "\n\nCONTEXT-AWARE TABLE EXTRACTION: If a valid ACTIVE table name appears ANYWHERE in the prompt "
            "(e.g. 'Using Ship_Performance_Dataset...', 'show data from Orders'), treat that as the target "
            "table and classify as DATA_QUERY. Do NOT scan individual columns or trailing words as separate "
            "table references. A word that matches a COLUMN (like 'Maintenance_Status') is a data field, "
            "never a disabled table. "
            "\n\nWhen in doubt, classify as DATA_QUERY. "
            "Return STRICT JSON only, with no markdown, no commentary. Format:\n"
            '{"intent": "GREETING|OFF_TOPIC|DISABLED_TABLE|DATA_QUERY", "target_table": null_or_table_name}\n'
            'Set "target_table" ONLY to a real, active table name. Otherwise null.'
        )

        # Build a compact column map so the classifier knows which identifiers
        # are columns (not tables). Prevents false DISABLED_TABLE on columns.
        col_lines = []
        if active_columns:
            for _t, cols in active_columns.items():
                if cols:
                    col_lines.append(f"  {_t}: {', '.join(cols[:20])}")
        columns_hint = "\n".join(col_lines) if col_lines else "(no column info)"

        user_prompt = (
            f"Active tables: {active_str}\n"
            f"All database tables (including disabled): {all_str}\n"
            f"Columns on active tables:\n{columns_hint}\n\n"
            f"User prompt: \"{prompt}\"\n\n"
            "IMPORTANT: If an entity mentioned in the prompt matches a COLUMN name "
            "listed above (not a table), classify the intent as DATA_QUERY — never "
            "DISABLED_TABLE. If a valid ACTIVE table name appears anywhere, prioritize "
            "it as the target table. DISABLED_TABLE should ONLY be used when the user "
            "clearly references a real TABLE that is excluded from active_tables.\n\n"
            "Return the classification JSON now."
        )

        try:
            response = self.client.chat.completions.create(
                model=_OPENROUTER_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=60,
                temperature=0,
            )
            content = response.choices[0].message.content
            if not content:
                logger.warning("OpenRouter returned empty content - defaulting to DATA_QUERY")
                return {"intent": "DATA_QUERY", "target_table": None}
            raw = content.strip()
            # Extract JSON object (may have code fences)
            if "```" in raw:
                raw = raw.split("```")[1].strip()
                if raw.startswith("json"):
                    raw = raw[4:].strip()
            data = json.loads(raw)
            intent = data.get("intent", "DATA_QUERY").upper()
            target = data.get("target_table")
            valid = {"GREETING", "OFF_TOPIC", "DISABLED_TABLE", "DATA_QUERY"}
            if intent not in valid:
                intent = "DATA_QUERY"
            return {"intent": intent, "target_table": target}
        except Exception as e:
            import traceback
            logger.error(f"Intent classification failed, defaulting to DATA_QUERY: {e}")
            logger.error(traceback.format_exc())
            print(f"DEBUG: IntentClassifier error: {e}", flush=True)
            return {"intent": "DATA_QUERY", "target_table": None}


def quote_identifier(name: str, dialect: str) -> str:
    """
    Quote a table/column identifier if it's a reserved word, per SQL dialect.

    - PostgreSQL / SQLite / ANSI: `"name"`
    - MySQL / MariaDB:            `` `name` `` or `"name"` (portable)
    - SQL Server (T-SQL):          `[name]` or `"name"`

    Non-reserved names are returned unchanged.
    """
    if not name:
        return name
    lower = name.lower().strip()
    if lower not in SQL_RESERVED_WORDS:
        return name

    if "postgres" in dialect.lower() or "sqlite" in dialect.lower():
        return f'"{name}"'
    elif "mysql" in dialect.lower() or "mariadb" in dialect.lower():
        return f"`{name}`"
    elif "mssql" in dialect.lower() or "sql server" in dialect.lower():
        return f"[{name}]"
    return f'"{name}"'
