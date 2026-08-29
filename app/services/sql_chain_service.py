from langchain_core.prompts import ChatPromptTemplate
from app.services.intent_classifier import quote_identifier
from langchain_openai import ChatOpenAI
from app.core.database import DatabaseManager
from app.agents.llm_router import build_chat_openai, workspace_byok_enabled, BYOKRequiredError
import re
import json

class SQLChainService:
    SYSTEM_TABLES_BLACKLIST = {'user', 'users', 'sessions', 'alembic_version', 'sqlite_sequence'}
    BYOK_BLOCK_MESSAGE = (
        "⚠️ **BYOK Required** — This self-hosted deployment has no platform API key fallback. "
        "Connect your own OpenAI / Anthropic / OpenRouter key in **Account → Security → Bring Your Own Key** "
        "before running queries."
    )

    # These are *only* triggered when the question is a standalone, clearly non-data
    # request. A keyword like "weather" appearing inside a longer data question
    # (e.g. "show weather conditions for top voyages") must NOT trigger a rejection.
    NON_DATA_PATTERNS = [
        'what is the weather', 'weather outside', 'weather today', 'weather forecast',
        'what\'s the current time', 'current time', 'what time is it', 'time is it',
        'what day is it', 'date today', 'what is today\'s date',
        'tell me a joke', 'tell me something funny', 'who won the',
        'latest news', 'what news', 'stock price of', 'bitcoin price', 'crypto price',
        'recipe for', 'how to cook', 'what movie', 'what song is', 'write a poem',
        'tell me a story', 'translate this', 'translate the following',
        'how are you doing', 'tell me about yourself', 'what can you do',
        'what is your name', 'who made you', 'thank you', 'thanks'
    ]

    GREETING_PATTERNS = {'hi', 'hello', 'hey', 'good morning', 'good afternoon', 'good evening', 'how are you', 'what is up', 'sup', 'greetings', 'thanks', 'thank you', 'who are you'}

    DISCOVERY_PATTERNS = {'what tables', 'show schema', 'list tables', 'available tables', 'what tables can you see', 'show tables'}

    def __init__(self, connection_string, user=None, schema=None):
        self.db = DatabaseManager.get_sql_database(connection_string, schema=schema)
        self.dialect = self.db.dialect
        self._byok_blocked_reason = None
        # BYOK-ONLY router: no platform API key fallback. Both the SQL LLM and
        # the router LLM MUST be powered by the workspace admin's own key.
        # If BYOK is not configured we don't crash construction — we record the
        # block and return a friendly message at query time (generate_sql).
        try:
            self.sql_llm = build_chat_openai(user=user, model="gpt-4o-mini", temperature=0)
            self.router_llm = build_chat_openai(user=user, model="gpt-4o-mini", temperature=0)
        except BYOKRequiredError:
            self.sql_llm = None
            self.router_llm = None
            self._byok_blocked_reason = self.BYOK_BLOCK_MESSAGE
        except Exception as e:
            # Construction must never crash chat page load; defer to query time.
            self.sql_llm = None
            self.router_llm = None
            self._byok_blocked_reason = (
                "⚠️ LLM provider unavailable. Configure your BYOK API key in "
                "Account → Security before running queries. "
                f"({type(e).__name__})"
            )

    def _blocked_response(self):
        """Return the JSON shape the chat route expects when BYOK is missing."""
        return {
            "type": "chat_message",
            "response": self._byok_blocked_reason or self.BYOK_BLOCK_MESSAGE,
        }

    # Keywords that indicate a data/business question even if non-data
    # words like "weather" appear in it (e.g. "show weather conditions for top voyages")
    DATA_SIGNAL_KEYWORDS = [
        'top', 'highest', 'lowest', 'rank', 'count', 'sum', 'average', 'avg',
        'show', 'list', 'compare', 'sales', 'revenue', 'total', 'metrics',
        'metric', 'orders', 'customers', 'voyage', 'voyages', 'shipment',
        'weight', 'quantity', 'price', 'cost', 'efficiency', 'condition',
        'status', 'date', 'month', 'year', 'report', 'pivot', 'group by',
        'order by', 'where', 'filter', 'rows', 'table', 'schema', 'column',
        'data', 'database', 'query', 'records', 'breakdown', 'analysis'
    ]

    def _is_non_data_question(self, question):
        q = question.lower().strip()

        # If the question contains any clear data/business signal,
        # NEVER classify it as non-data — even if it also mentions
        # words like "weather", "news", etc.
        if any(kw in q for kw in self.DATA_SIGNAL_KEYWORDS):
            return False

        return any(p in q for p in self.NON_DATA_PATTERNS)

    def _is_greeting(self, question):
        q = question.lower().strip()
        return q in self.GREETING_PATTERNS or any(q.startswith(g + ' ') for g in self.GREETING_PATTERNS)

    def _is_discovery(self, question):
        q = question.lower().strip()
        # Check for exact phrase matches first
        if any(p in q for p in self.DISCOVERY_PATTERNS):
            return True
        
        # Check for variations and typos
        # Common discovery question patterns (with fuzzy matching)
        discovery_patterns = [
            r'what.*tabl[eo]s',  # Matches "what tables" or "what tabloes"
            r'which.*tabl[eo]s',
            r'show.*tabl[eo]s',
            r'list.*tabl[eo]s',
            r'available.*tabl[eo]s',
            r'can you see.*tabl[eo]s',
            r'do you have.*tabl[eo]s',
            r'what.*schema',
            r'show.*schema',
            r'list.*schema'
        ]
        
        for pattern in discovery_patterns:
            if re.search(pattern, q):
                return True
        
        # Check for keyword combinations
        has_table_word = re.search(r'tabl[eo]s?', q) is not None
        has_schema_word = 'schema' in q
        has_discovery_word = any(word in q for word in ['what', 'which', 'show', 'list', 'available', 'see', 'have'])
        
        if (has_table_word or has_schema_word) and has_discovery_word:
            return True
        
        return False

    def _parse_join_edges(self, model):
        """Parse the Global Join Graph into (left_table, left_col, right_table, right_col) pairs.

        Edges may be declared either as 'a.col = b.col' lines in model['global_joins'],
        or as {from_table, from_column, to_table, to_column} dicts. Both forms are
        accepted so the join closure below always knows the real FK relationships —
        the LLM must NEVER be left guessing joins blindly.
        """
        edges = set()
        gjoins = model.get('global_joins') or ''
        if isinstance(gjoins, str):
            for line in gjoins.split('\n'):
                line = line.strip().replace('->','=')
                if not line or '=' not in line:
                    continue
                left, right = [p.strip() for p in line.split('=', 1)]
                lp = left.split('.')
                rp = right.split('.')
                if len(lp) == 2 and len(rp) == 2:
                    edges.add((lp[0].strip('"[]` '), lp[1].strip('"[]` '),
                               rp[0].strip('"[]` '), rp[1].strip('"[]` ')))
        elif isinstance(gjoins, list):
            for edge in gjoins:
                if isinstance(edge, dict) and edge.get('from_table') and edge.get('to_table'):
                    edges.add((str(edge['from_table']), str(edge.get('from_column', '')),
                               str(edge['to_table']), str(edge.get('to_column', ''))))
        return edges

    def _expand_join_closure(self, model, selected):
        """Expand a candidate table set to its FULL connected component in the
        Global Join Graph.

        Multi-hop questions (e.g. 'best-selling products' via
        olist_products -> olist_order_items -> olist_orders -> olist_customers)
        require every table on the join path. The router may only name the
        "obvious" table; adding the closure guarantees the LLM prompt always
        contains the whole traversal path instead of being trapped into a
        single-table query or an unnecessary clarification.
        """
        edges = self._parse_join_edges(model)
        if not edges or not selected:
            return selected

        # Build adjacency limited to the provider's published tables.
        tables = set()
        for lt, _lc, rt, _rc in edges:
            tables.add(lt)
            tables.add(rt)

        selected = set(selected)
        changed = True
        while changed:
            changed = False
            for lt, _lc, rt, _rc in edges:
                if lt in selected and rt not in selected and rt in tables:
                    selected.add(rt)
                    changed = True
                elif rt in selected and lt not in selected and lt in tables:
                    selected.add(lt)
                    changed = True
        return selected

    def _select_relevant_tables(self, model, question):
        """Uses a lightning-fast, cost-effective model (gpt-4o-mini) to select 
        ONLY the necessary table(s) for the user's question, preventing prompt bloat and reducing API cost.

        JOIN-CLOSURE FIX: after router / keyword selection, the selected set is
        expanded to the full connected component in the Global Join Graph so
        multi-hop FK paths (e.g. olist_products -> olist_order_items ->
        olist_orders -> olist_customers) are ALWAYS present in the LLM prompt.
        """
        tables = model.get('tables', {})
        # FIX: Skip router LLM for small schemas (3 or fewer tables)
        if len(tables) <= 3:
            return tables

        valid_tables = {k: v for k, v in tables.items() if k.lower() not in self.SYSTEM_TABLES_BLACKLIST}
        
        if len(valid_tables) <= 1:
            return valid_tables

        # General / schema discovery prompts: keep all tables
        if self._is_discovery(question) or self._is_greeting(question):
            return valid_tables

        # Build a compact table summary for the router prompt
        table_summaries = []
        for t_name, t_data in valid_tables.items():
            cols = list(t_data.get('columns', {}).keys())
            desc = t_data.get('description', '')
            alias = t_data.get('alias', '')
            # Pre-compute the join OUTSIDE the f-string: Python <=3.11 does not
            # allow a backslash inside an f-string expression part {...}.
            summarized_cols = ', '.join(cols[:15])
            table_summaries.append(
                f"- Table: `{t_name}` (Alias: {alias}, Desc: {desc})\n  Columns: {summarized_cols}"
            )

        # Pre-compute OUTSIDE the f-string: Python <=3.11 forbids backslashes
        # inside an f-string expression part {...} (same fix as above).
        available_tables_text = '\n'.join(table_summaries)

        router_prompt = f"""Given the user's question and the available database tables below, identify which table(s) are strictly required to answer the question.
Available Tables:
{available_tables_text}

User Question: "{question}"

Instructions:
Return ONLY a JSON list of table names required, e.g. ["TableName"]. Do not include any other text.
"""
        try:
            router_res = self.router_llm.invoke(router_prompt)
            content = router_res.content if hasattr(router_res, 'content') else str(router_res)
            match = re.search(r'\[(.*?)\]', content, re.DOTALL)
            if match:
                selected_names = json.loads(match.group(0))
                matched = {name: valid_tables[name] for name in selected_names if name in valid_tables}
                if matched:
                    # JOIN-CLOSURE: add every table on the FK path so multi-hop
                    # questions (products->order_items->orders->customers) cannot
                    # lose a required join table.
                    closed = self._expand_join_closure(model, matched.keys())
                    return {name: valid_tables[name] for name in closed if name in valid_tables}
        except Exception as e:
            print(f"Table router warning: {e}")

        # Fallback to keyword matching if router fails
        q_lower = question.lower()
        matched = {}
        for t_name, t_data in valid_tables.items():
            if t_name.lower() in q_lower or any(c.lower() in q_lower for c in t_data.get('columns', {}).keys()):
                matched[t_name] = t_data

        # Vague follow-ups like "top 5 from this table" need all tables available
        vague_patterns = {'this table', 'that table', 'the table', 'this one', 'that one'}
        if any(p in q_lower for p in vague_patterns):
            return valid_tables

        if matched:
            # JOIN-CLOSURE: same expansion as the router path — keyword matches
            # (e.g. only 'olist_products') are expanded to the entire FK path
            # (olist_order_items, olist_orders, olist_customers) so the prompt
            # always shows the full multi-hop traversal candidates.
            closed = self._expand_join_closure(model, matched.keys())
            return {name: valid_tables[name] for name in closed if name in valid_tables}

        return valid_tables

    def _model_to_markdown(self, model, question=""):
        relevant_tables = self._select_relevant_tables(model, question)

        lines = []
        lines.append(f"Database Type: {model.get('db_type', 'unknown')}")
        
        if model.get('global_joins'):
            lines.append("\n### Global Join Graph:")
            for j in model['global_joins'].split('\n'):
                if j.strip():
                    lines.append(f"- {j.strip()}")
                    
        if model.get('global_filters'):
            lines.append(f"\n### Default Business Filters:\n- {model['global_filters']}")
            
        lines.append("\n### Tables:")
        for t_name, t_data in relevant_tables.items():
            alias_str = f" (Alias: {t_data['alias']})" if t_data.get('alias') else ""
            desc_str = f" - Description: {t_data['description']}" if t_data.get('description') else ""
            quoted_name = quote_identifier(t_name, self.dialect)
            lines.append(f"\n- **Table: {quoted_name}**{alias_str}{desc_str}")
            
            if t_data.get('metrics'):
                lines.append("  * Custom Metrics:")
                for m in t_data['metrics']:
                    m_desc = f" ({m['description']})" if m.get('description') else ""
                    lines.append(f"    - `{m['name']}` := `{m['sql']}`{m_desc}")
                    
            lines.append("  * Columns:")
            for c_name, c_data in t_data.get('columns', {}).items():
                c_type = c_data.get('type', '')
                c_alias = f" [Alias: {c_data['alias']}]" if c_data.get('alias') else ""
                c_enum = f" [Enums: {c_data['enum']}]" if c_data.get('enum') else ""
                c_syn = f" [Synonyms: {', '.join(c_data['synonyms'])}]" if c_data.get('synonyms') else ""
                quoted_col = quote_identifier(c_name, self.dialect)
                lines.append(f"    - `{quoted_col}` ({c_type}){c_alias}{c_enum}{c_syn}")
                
        return "\n".join(lines)

    def _build_tools(self):
        return [
            {
                "type": "function",
                "function": {
                    "name": "respond_conversational",
                    "description": "Respond to greetings, compliments, or general non-data chat.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "message": {"type": "string", "description": "The conversational response to show the user."}
                        },
                        "required": ["message"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "request_clarification",
                    "description": "Ask the user to clarify an ambiguous or underspecified data question.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "message": {"type": "string", "description": "The clarification question to show the user."},
                            "suggested_tables": {"type": "array", "items": {"type": "string"}, "description": "List of table names the user might mean."}
                        },
                        "required": ["message", "suggested_tables"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "execute_sql_query",
                    "description": "Generate a valid SELECT SQL query for a data question, plus 3 schema-grounded follow-up questions.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "sql_query": {"type": "string", "description": "The raw SELECT SQL query to execute."},
                            "related_questions": {"type": "array", "items": {"type": "string"}, "description": "3 follow-up questions the user might ask next."}
                        },
                        "required": ["sql_query", "related_questions"]
                    }
                }
            }
        ]

    def _build_messages(self, question, markdown_schema, conversation_history, table_names_only=False):
        # Lightweight greeting / conversational fast-path: no schema markdown
        if self._is_greeting(question):
            system_content = "You are an expert database assistant for Data Convo. Respond conversationally using the `respond_conversational` tool."
            messages = [{"role": "system", "content": system_content}]
            messages.append({"role": "user", "content": question})
            return messages

        # Schema discovery fast-path: only table names, no columns/joins
        if table_names_only:
            system_content = f"""You are an expert database assistant for Data Convo. 
The user is asking what tables are available. Respond with the list of table names using the `respond_conversational` tool.

Available tables:
{markdown_schema}
"""
            messages = [{"role": "system", "content": system_content}]
            messages.append({"role": "user", "content": question})
            return messages

        dialect_instructions = ""
        dialect_key = self.dialect.lower()
        if 'mssql' in dialect_key:
            dialect_instructions = """
- This is a Microsoft SQL Server (T-SQL) database.
- DO NOT use 'LIMIT'. Always use 'SELECT TOP N' to limit rows (e.g., 'SELECT TOP 1 ...').
- Use square brackets [table].[column] to quote identifiers if needed.
"""
        elif 'postgres' in dialect_key:
            dialect_instructions = """
- This is a PostgreSQL / Supabase database.
- Use 'LIMIT N' at the end of the query.
- Use double quotes "table"."column" to quote identifiers if needed.
- Schema-qualified table names like "schema"."table" are valid.
"""
        elif 'mysql' in dialect_key:
            dialect_instructions = """
- This is a MySQL database.
- Use 'LIMIT N' at the end of the query.
- Use backticks `table`.`column` to quote identifiers if needed.
"""
        elif 'databricks' in dialect_key:
            dialect_instructions = """
- This is a Databricks (Unity Catalog) database.
- ALWAYS fully qualify every table as `catalog`.`schema`.`table` using the exact
  catalog and schema values shown in the Tables section (e.g. peoplecert.prod.my_table).
- Never rely on the default catalog ('peoplecert.default') — use the configured schema.
- Use backticks `catalog`.`schema`.`table` to quote identifiers.
- Use 'LIMIT N' at the end of the query.
"""
        elif 'sqlite' in dialect_key:
            dialect_instructions = """
- This is a SQLite database.
- Use 'LIMIT N' at the end of the query.
- Use double quotes "table_name" to quote identifiers if needed.
- SQLite is dynamically typed, so TEXT/INTEGER/REAL are normal.
"""
        elif 'bigquery' in dialect_key:
            dialect_instructions = """
- This is a Google BigQuery database.
- Use 'LIMIT N' at the end of the query.
- Use backticks `project.dataset.table` for fully-qualified table references.
- BigQuery uses 1-based standard SQL syntax, no semicolons required.
"""

        system_content = f"""You are an expert {self.dialect} database assistant. 
Your task is to convert natural language business questions into valid, executable {self.dialect} SELECT queries using ONLY the Streamlined Markdown Semantic Layer provided below. Do not reference tables or columns not present in this schema.

ACTIVE-TABLE CONTEXT DIRECTIVE: If an active table is provided in the system context (e.g. "[Context: Active Table is TableName]"), default to querying that table unless the user explicitly specifies a different one. Never ask "which table" if an active table is already set in the context.

CRITICAL DIALECT RULES:{dialect_instructions}
SEMANTIC LAYER GUIDELINES:
- Respect table and column human aliases, descriptions, custom metrics, column enums, and synonyms.
- Use defined Custom Metrics where applicable.
- Adhere to the Global Join Graph for foreign key joins.
- Apply Default Business Filters to the WHERE clause if specified.

Rules:
1. Use the provided tools to respond. Choose exactly one tool per user message.
2. For data questions, call execute_sql_query with a valid SELECT query and 3 related follow-up questions.
3. For greetings or non-data chat, call respond_conversational.
4. For ambiguous questions, call request_clarification with suggested tables.
5. Semantic Layer (Markdown):
{markdown_schema}
"""

        messages = [{"role": "system", "content": system_content}]

        # Add last 4 conversational turns (user + assistant SQL outputs)
        if conversation_history:
            for turn in conversation_history[-4:]:
                if turn.get("role") == "user":
                    messages.append({"role": "user", "content": turn["content"]})
                elif turn.get("role") == "assistant" and turn.get("sql"):
                    messages.append({"role": "assistant", "content": f"SQL: {turn['sql']}"})
                elif turn.get("role") == "assistant" and turn.get("content"):
                    messages.append({"role": "assistant", "content": turn["content"]})

        messages.append({"role": "user", "content": question})

        # DEBUG: Log the final compiled system prompt (markdown semantic layer +
        # global joins + aliases + metrics + enums) sent to the LLM. This is the
        # definitive proof that semantic metadata is present in the payload.
        print("\n===== DEBUG: FINAL SYSTEM PROMPT SENT TO LLM =====", flush=True)
        print(messages[0]["content"], flush=True)
        print("===== END DEBUG: FINAL SYSTEM PROMPT =====\n", flush=True)

        return messages

    def correct_sql(self, question, published_model, failed_sql, error_message, conversation_history=None, retry_count=0):
        """Agentic self-healing: feed the database error back to the LLM
        and request a corrected SQL query. Reuses the same tool-calling flow
        as generate_sql but includes the error context in the prompt.
        """
        if self.sql_llm is None:
            return self._blocked_response()

        markdown_schema = self._model_to_markdown(published_model, question=question)
        dialect_key = self.dialect.lower()

        # Build dialect-specific hint (reuse the same rules as _build_messages)
        dialect_hint = ""
        if 'mssql' in dialect_key:
            dialect_hint = "Use SELECT TOP N for limits and [brackets] for quoting."
        elif 'postgres' in dialect_key:
            dialect_hint = "Use LIMIT N for limits and double quotes for quoting. If an aggregate like SUM/AVG fails on a TEXT column, use explicit casting (e.g. CAST(col AS NUMERIC) or column::NUMERIC)."
        elif 'mysql' in dialect_key:
            dialect_hint = "Use LIMIT N for limits and backticks for quoting."
        elif 'sqlite' in dialect_key:
            dialect_hint = "Use LIMIT N for limits. SQLite is dynamically typed."
        elif 'bigquery' in dialect_key:
            dialect_hint = "Use backticks for fully-qualified table names and standard SQL."

        correction_prompt = f"""The SQL query you generated failed to execute against the database.

Original question: "{question}"

Your previous SQL that failed:
```sql
{failed_sql}
```

Database error message:
```
{error_message}
```

Please rewrite the SQL query to fix the issue. Common fixes include:
- If an aggregate function (SUM, AVG, COUNT) fails on a TEXT column, cast the column to a numeric type first: CAST(column AS NUMERIC) or column::NUMERIC
- If identifiers are incorrectly quoted, use the correct quoting style for {self.dialect}
- If a column name is wrong, check the schema again
- If a table is schema-qualified, keep the same qualification

CRITICAL DIALECT RULES for {self.dialect}: {dialect_hint}

Return ONLY the corrected SELECT statement via the execute_sql_query tool.
"""

        system_content = f"""You are an expert {self.dialect} database assistant fixing an SQL error.
The user asked a data question, and your previous SQL failed with a database exception.
Analyze the error and generate a corrected SELECT query.

SEMANTIC LAYER (Markdown):
{markdown_schema}

Respond by calling the execute_sql_query tool with the corrected SQL and 3 related follow-up questions.
"""

        messages = [{"role": "system", "content": system_content}]

        # Include conversation history for context
        if conversation_history:
            for turn in conversation_history[-4:]:
                if turn.get("role") == "user":
                    messages.append({"role": "user", "content": turn["content"]})
                elif turn.get("role") == "assistant" and turn.get("sql"):
                    messages.append({"role": "assistant", "content": f"SQL: {turn['sql']}"})

        messages.append({"role": "user", "content": correction_prompt})

        tools = self._build_tools()
        response = self.sql_llm.invoke(messages, tools=tools, tool_choice="required")

        tool_calls = getattr(response, 'tool_calls', None) or []
        if not tool_calls:
            return {
                "type": "chat_message",
                "response": "I couldn't fix the query automatically. Please try rephrasing your question."
            }

        tool_call = tool_calls[0]
        tool_name = tool_call.get("name", "")
        tool_args = tool_call.get("args", {})

        if tool_name == "execute_sql_query":
            sql = tool_args.get("sql_query", "").strip()
            if not sql.upper().startswith('SELECT'):
                return {
                    "type": "chat_message",
                    "response": "The AI did not generate a valid corrected SELECT query."
                }
            return {
                "type": "sql",
                "sql": sql,
                "related_questions": tool_args.get("related_questions", []),
                "retry_count": retry_count + 1  # track how many self-heal attempts happened
            }
        elif tool_name == "request_clarification":
            return {
                "type": "clarification",
                "response": tool_args.get("message", "Could you clarify your question?"),
                "suggested_tables": tool_args.get("suggested_tables", [])
            }
        elif tool_name == "respond_conversational":
            return {
                "type": "chat_message",
                "response": tool_args.get("message", "I couldn't generate a corrected query.")
            }
        else:
            return {
                "type": "chat_message",
                "response": "I couldn't fix the query automatically. Please try rephrasing your question."
            }

    def generate_sql(self, question, published_model, conversation_history=None):
        # BYOK-ONLY enforcement: no platform key fallback. If the service was
        # constructed without a valid BYOK key, block here with a friendly message.
        if self.sql_llm is None:
            return self._blocked_response()

        # INSTANT local fast-paths: no LLM call, no DB call
        if self._is_greeting(question):
            return {
                "type": "chat_message",
                "response": "Hello! I'm ready to help you analyze your database. What metrics or tables would you like to inspect today?"
            }

        if self._is_non_data_question(question):
            return {
                "type": "chat_message",
                "response": "I can only answer questions about your connected database. Try asking about your tables, e.g. 'What are the total sales?'."
            }

        # Schema discovery fast-path: only table names, no columns/joins
        # IMPORTANT: If the question mentions a specific table name, it's a DATA query,
        # NOT a discovery question - force the full semantic layer prompt.
        is_discovery = self._is_discovery(question)
        if is_discovery:
            # Check if the question mentions any actual table name
            table_names = list(published_model.get('tables', {}).keys())
            mentions_table = any(t.lower() in question.lower() for t in table_names)
            if mentions_table:
                # It's a data query about a specific table - use full schema
                is_discovery = False

        if is_discovery:
            table_names = [t for t in published_model.get('tables', {}).keys() if t.lower() not in self.SYSTEM_TABLES_BLACKLIST]
            markdown_schema = "\n".join(f"- {quote_identifier(t, self.dialect)}" for t in table_names)
            messages = self._build_messages(question, markdown_schema, conversation_history, table_names_only=True)
        else:
            markdown_schema = self._model_to_markdown(published_model, question=question)
            messages = self._build_messages(question, markdown_schema, conversation_history)
        
        tools = self._build_tools()

        response = self.sql_llm.invoke(messages, tools=tools, tool_choice="required")
        
        # Extract tool call
        tool_calls = getattr(response, 'tool_calls', None) or []
        if not tool_calls:
            # Graceful fallback: return plain text as chat message, skip DB execution
            content = response.content if hasattr(response, 'content') else str(response)
            if content and content.strip():
                return {
                    "type": "chat_message",
                    "response": content.strip()
                }
            return {
                "type": "chat_message",
                "response": "I couldn't generate a response. Please try rephrasing your question."
            }

        tool_call = tool_calls[0]
        tool_name = tool_call.get("name", "")
        tool_args = tool_call.get("args", {})

        if tool_name == "respond_conversational":
            return {
                "type": "chat_message",
                "response": tool_args.get("message", "Hello! How can I help you today?")
            }
        elif tool_name == "request_clarification":
            return {
                "type": "clarification",
                "response": tool_args.get("message", "Could you clarify your question?"),
                "suggested_tables": tool_args.get("suggested_tables", [])
            }
        elif tool_name == "execute_sql_query":
            sql = tool_args.get("sql_query", "").strip()
            if not sql.upper().startswith('SELECT'):
                raise ValueError("The AI did not generate a valid SELECT query. Please rephrase your question.")
            return {
                "type": "sql",
                "sql": sql,
                "related_questions": tool_args.get("related_questions", [])
            }
        else:
            return {
                "type": "chat_message",
                "response": "I'm not sure how to respond to that. Could you rephrase?"
            }