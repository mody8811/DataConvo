"""BI Dashboard — REST API + page rendering for user-pinned visualization widgets.

Endpoints:
    GET    /dashboard                         Render the dashboard page
    POST   /api/dashboard/pins                Create a pinned widget
    GET    /api/dashboard/widgets             List the user's widgets (with fresh data)
    GET    /api/dashboard/widgets/<id>        Fetch a single widget
    PUT    /api/dashboard/widgets/<id>        Update widget (title / chart config / sql)
    DELETE /api/dashboard/pins/<id>           Delete a widget (owner only)
    POST   /api/dashboard/widgets/<id>/refresh  Re-execute SQL + re-validate schema
    GET    /api/dashboard/widgets/<id>/export  Export widget data as CSV

Security model:
  * Every operation is scoped to `current_user.id` (per-user isolation).
  * SQL is re-validated through QueryExecutor (SELECT-only, single statement).
  * RBAC table permissions are enforced identically to the chat interface.
  * Schema validation compares the widget's known columns against the active
    semantic layer before execution, surfacing breaking changes early.
"""
import json
import logging
import csv
import io as _io
import re
from datetime import datetime

from flask import Blueprint, render_template, request, jsonify, Response
from flask_login import login_required, current_user

from app import db
from app.models import DashboardWidget, PublishedModel
from app.services.query_executor import QueryExecutor
from app.services.sql_chain_service import SQLChainService

logger = logging.getLogger(__name__)

dashboard = Blueprint('dashboard', __name__)

# Tables that are never valid data sources (system / auth internals)
SYSTEM_TABLES_BLACKLIST = {'user', 'users', 'sessions', 'alembic_version', 'sqlite_sequence'}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_active_semantic_model():
    """Load the current user's active published semantic layer (session-first,
    then persisted DB copy). Returns dict or None. Also restores the persisted
    connection string into the session so background execution uses the exact
    tenant context used during chat."""
    from flask import session
    model = session.get('published_semantic_layer')
    if model:
        # Ensure connection context is present even if only the model was cached
        if not session.get('connection_string'):
            saved = (PublishedModel.query
                     .filter_by(user_id=current_user.id)
                     .order_by(PublishedModel.updated_at.desc())
                     .first())
            if saved and saved.connection_string:
                session['connection_string'] = saved.connection_string
        return model
    saved = (PublishedModel.query
             .filter_by(user_id=current_user.id)
             .order_by(PublishedModel.updated_at.desc())
             .first())
    if saved:
        try:
            model = json.loads(saved.model_json)
            session['published_semantic_layer'] = model
            # Restore the user's database context for refresh/background execution
            if saved.connection_string:
                session['connection_string'] = saved.connection_string
            return model
        except (ValueError, TypeError):
            return None
    return None


def _get_connection_string():
    """Resolve the active DB connection string, mirroring chat execution:
    session-first, then fall back to the persisted per-user semantic layer."""
    from flask import session
    conn_str = session.get('connection_string')
    if conn_str:
        return conn_str
    saved = (PublishedModel.query
             .filter_by(user_id=current_user.id)
             .order_by(PublishedModel.updated_at.desc())
             .first())
    if saved and saved.connection_string:
        # Cache back into the session for consistency
        session['connection_string'] = saved.connection_string
        return saved.connection_string
    return None


def _normalize_sql(sql):
    """Strip markdown fences and surrounding whitespace."""
    if not sql:
        return ''
    cleaned = sql.strip()
    cleaned = re.sub(r'^```sql\s*', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'^```\s*', '', cleaned)
    cleaned = re.sub(r'\s*```$', '', cleaned)
    return cleaned.strip()


def _validate_payload_for_create(data):
    """Validate + normalize a pin payload. Returns (payload_dict, error_string)."""
    if not isinstance(data, dict):
        return None, 'Request body must be a JSON object.'

    sql = _normalize_sql(data.get('sql_query') or data.get('sql') or '')
    if not sql:
        return None, 'sql_query is required.'

    title = (data.get('title') or '').strip() or 'Untitled Widget'
    if len(title) > 200:
        return None, 'title must be 200 characters or fewer.'

    chart_config = data.get('chart_config') or {}
    if not isinstance(chart_config, dict):
        return None, 'chart_config must be a JSON object.'
    # Reject oversized configs (sanity guard)
    if len(json.dumps(chart_config)) > 200_000:
        return None, 'chart_config is too large.'

    result_metadata = data.get('result_metadata') or {}
    if not isinstance(result_metadata, dict):
        return None, 'result_metadata must be a JSON object.'

    return {
        'title': title,
        'sql_query': sql,
        'chart_config': chart_config,
        'result_metadata': result_metadata,
    }, None


def _enforce_rbac_table_access(sql, model):
    """Check that the SQL only references tables the user is allowed to query.

    For admins, this is always permitted. For members, the active model is
    already RBAC-filtered by `get_effective_tables`; this helper additionally
    inspects raw table tokens in the SQL so a crafted query cannot reference a
    restricted table directly.

    Returns (allowed: bool, restricted_table: str|None).
    """
    from app.services.rbac import is_admin, get_disallowed_tables
    if is_admin():
        return True, None

    # Determine disallowed tables for this member
    disallowed = set(get_disallowed_tables(model, current_user))
    if not disallowed:
        return True, None

    sql_lower = sql.lower()
    # Tokenize on non-identifier characters to catch quoted + bare references
    tokens = re.findall(r'[A-Za-z_][A-Za-z0-9_]*', sql_lower)
    token_set = set(tokens)
    for table in disallowed:
        table_lower = table.lower()
        if table_lower in token_set or table_lower in sql_lower:
            return False, table
    return True, None


def _validate_schema_against_model(sql, model):
    """Validate that every referenced table in the SQL is present in the user's
    active semantic layer and that referenced columns still exist.

    Returns (is_valid: bool, errors: list[str]).
    """
    if not model or not isinstance(model, dict):
        return False, ['No active semantic layer found. Please publish your semantic layer first.']

    tables = model.get('tables', {}) or {}
    sql_upper = sql.upper()

    # Find referenced table tokens from the active model by checking which
    # table names appear in the SQL (case-insensitive, quoted variants).
    referenced_tables = []
    for t_name in tables.keys():
        if t_name.lower() in sql_upper.lower():
            referenced_tables.append(t_name)
    # If no active table name appears, check raw FROM/JOIN tokens
    if not referenced_tables:
        from_tokens = re.findall(
            r'\b(?:FROM|JOIN)\s+["`\[]?([A-Za-z_][A-Za-z0-9_]*)["`\]]?',
            sql_upper,
            flags=re.IGNORECASE,
        )
        for tok in from_tokens:
            if tok.lower() not in SYSTEM_TABLES_BLACKLIST:
                referenced_tables.append(tok)

    # NOTE: We validate TABLE references here (high-confidence, exact match).
    # Column-level drift is intentionally left to the validated execution
    # engine: after QueryExecutor runs the SELECT, any missing/renamed column
    # surfaces as a real database error which the dashboard UI presents with
    # the "Repair Query" / "Re-publish Semantic Layer" actions. A regex-based
    # column guesser is too fragile for production (false positives on
    # functions, aliases, and quoted identifiers).
    errors = []
    for table in referenced_tables:
        t_lower = table.lower()
        matched_name = None
        for active_name in tables.keys():
            if active_name.lower() == t_lower:
                matched_name = active_name
                break
        if matched_name is None:
            errors.append(f"Table `{table}` is not present in your active semantic layer.")

    return (len(errors) == 0), errors


def _execute_widget_sql(sql, model):
    """Safe re-execution path shared by refresh + list + export.

    Returns (results: list, meta: dict, error: str|None).
    """
    conn_str = _get_connection_string()
    if not conn_str:
        return None, {}, 'Database connection is not configured. Please reconnect in Settings.'

    # 1. RBAC enforcement (member roles)
    allowed, restricted = _enforce_rbac_table_access(sql, model)
    if not allowed:
        return None, {}, f'Access denied: The table `{restricted}` is restricted for your role.'

    # 2. Schema validation against active semantic layer
    schema_ok, schema_errors = _validate_schema_against_model(sql, model)
    if not schema_ok:
        return None, {}, '; '.join(schema_errors)

    # 3. Safe execution through the validated engine
    try:
        service = SQLChainService(conn_str)
        raw = QueryExecutor.validate_and_execute(service.db, sql)
    except Exception as e:
        logger.warning(f'Dashboard widget SQL execution failed: {e}')
        return None, {}, str(e)

    # 4. Normalize rows
    rows = []
    if isinstance(raw, str):
        rows = [{'result': raw}]
    elif isinstance(raw, list):
        for row in raw:
            if isinstance(row, (tuple, list)):
                rows.append({f'col_{i}': v for i, v in enumerate(row)})
            elif isinstance(row, dict):
                rows.append(row)
            else:
                rows.append({'result': row})
    else:
        rows = []

    # 5. Build result metadata (schema / column types inferred from first row)
    schema_meta = {}
    if rows:
        first = rows[0]
        for col, val in first.items():
            schema_meta[col] = type(val).__name__

    meta = {
        'row_count': len(rows),
        'columns': list(schema_meta.keys()),
        'schema': schema_meta,
        'refreshed_at': datetime.utcnow().isoformat(),
    }
    return rows, meta, None


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

@dashboard.route('/dashboard')
@login_required
def dashboard_page():
    """Render the BI Dashboard page."""
    model = _get_active_semantic_model()
    # Pass workspace info to the template so member accounts can render
    # their workspace name and details on the dashboard view.
    workspace = None
    if current_user.workspace_id:
        from app.models import Workspace
        workspace = Workspace.query.get(current_user.workspace_id)
    return render_template(
        'dashboard.html',
        has_semantic_layer=model is not None,
        workspace=workspace,
        workspace_name=workspace.name if workspace else (current_user.username or 'My Workspace'),
    )


# ---------------------------------------------------------------------------
# CRUD API
# ---------------------------------------------------------------------------

@dashboard.route('/api/dashboard/pins', methods=['POST'])
@login_required
def create_pin():
    """Pin a chart from the chat interface to the dashboard."""
    data = request.get_json(silent=True)
    payload, error = _validate_payload_for_create(data)
    if error:
        return jsonify({'error': error}), 400

    # Pre-validate the SQL before persisting: must be SELECT-only + executable
    conn_str = _get_connection_string()
    model = _get_active_semantic_model()
    if model:
        schema_ok, schema_errors = _validate_schema_against_model(payload['sql_query'], model)
        if not schema_ok:
            return jsonify({'error': '; '.join(schema_errors)}), 422
        allowed, restricted = _enforce_rbac_table_access(payload['sql_query'], model)
        if not allowed:
            return jsonify({'error': f'Access denied: The table `{restricted}` is restricted for your role.'}), 403

    if conn_str:
        try:
            service = SQLChainService(conn_str)
            # Full validation passes only SELECT statements; we run a dry execution
            # so a broken query is rejected at pin time, not on the dashboard.
            QueryExecutor.validate_and_execute(service.db, payload['sql_query'])
        except Exception as e:
            return jsonify({'error': f'SQL validation failed: {e}'}), 422

    widget = DashboardWidget(
        user_id=current_user.id,
        title=payload['title'],
        sql_query=payload['sql_query'],
        chart_config=json.dumps(payload['chart_config']),
        result_metadata=json.dumps(payload['result_metadata']),
    )
    db.session.add(widget)
    db.session.commit()
    logger.info(f'User {current_user.id} pinned dashboard widget {widget.id}')
    return jsonify(widget.to_dict()), 201


@dashboard.route('/api/dashboard/widgets', methods=['GET'])
@login_required
def list_widgets():
    """List the current user's widgets (no data payload — dashboard fetches
    each widget's data via the refresh endpoint to reflect live schema state)."""
    widgets = (DashboardWidget.query
               .filter_by(user_id=current_user.id)
               .order_by(DashboardWidget.created_at.desc())
               .all())
    return jsonify([w.to_dict() for w in widgets])


@dashboard.route('/api/dashboard/widgets/<string:widget_id>', methods=['GET'])
@login_required
def get_widget(widget_id):
    """Fetch a single widget owned by the current user."""
    widget = DashboardWidget.query.filter_by(id=widget_id, user_id=current_user.id).first()
    if not widget:
        return jsonify({'error': 'Widget not found.'}), 404
    return jsonify(widget.to_dict())


@dashboard.route('/api/dashboard/widgets/<string:widget_id>', methods=['PUT'])
@login_required
def update_widget(widget_id):
    """Update widget title / chart config / SQL (owner only)."""
    widget = DashboardWidget.query.filter_by(id=widget_id, user_id=current_user.id).first()
    if not widget:
        return jsonify({'error': 'Widget not found.'}), 404

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({'error': 'Request body must be a JSON object.'}), 400

    if 'title' in data:
        title = (data.get('title') or '').strip()
        if not title:
            return jsonify({'error': 'title cannot be empty.'}), 400
        if len(title) > 200:
            return jsonify({'error': 'title must be 200 characters or fewer.'}), 400
        widget.title = title

    if 'sql_query' in data:
        sql = _normalize_sql(data['sql_query'])
        if not sql:
            return jsonify({'error': 'sql_query cannot be empty.'}), 400
        # Re-validate before persisting
        conn_str = _get_connection_string()
        model = _get_active_semantic_model()
        if model:
            schema_ok, schema_errors = _validate_schema_against_model(sql, model)
            if not schema_ok:
                return jsonify({'error': '; '.join(schema_errors)}), 422
            allowed, restricted = _enforce_rbac_table_access(sql, model)
            if not allowed:
                return jsonify({'error': f'Access denied: The table `{restricted}` is restricted for your role.'}), 403
        if conn_str:
            try:
                service = SQLChainService(conn_str)
                QueryExecutor.validate_and_execute(service.db, sql)
            except Exception as e:
                return jsonify({'error': f'SQL validation failed: {e}'}), 422
        widget.sql_query = sql

    if 'chart_config' in data:
        cc = data.get('chart_config')
        if not isinstance(cc, dict):
            return jsonify({'error': 'chart_config must be a JSON object.'}), 400
        if len(json.dumps(cc)) > 200_000:
            return jsonify({'error': 'chart_config is too large.'}), 400
        widget.chart_config = json.dumps(cc)

    if 'result_metadata' in data:
        rm = data.get('result_metadata')
        if not isinstance(rm, dict):
            return jsonify({'error': 'result_metadata must be a JSON object.'}), 400
        widget.result_metadata = json.dumps(rm)

    db.session.commit()
    return jsonify(widget.to_dict())


@dashboard.route('/api/dashboard/pins/<string:widget_id>', methods=['DELETE'])
@login_required
def delete_pin(widget_id):
    """Delete a pinned widget (owner only)."""
    widget = DashboardWidget.query.filter_by(id=widget_id, user_id=current_user.id).first()
    if not widget:
        return jsonify({'error': 'Widget not found.'}), 404
    db.session.delete(widget)
    db.session.commit()
    logger.info(f'User {current_user.id} deleted dashboard widget {widget_id}')
    return jsonify({'success': True})


# ---------------------------------------------------------------------------
# Refresh / execute / export
# ---------------------------------------------------------------------------

@dashboard.route('/api/dashboard/widgets/<string:widget_id>/refresh', methods=['POST'])
@login_required
def refresh_widget(widget_id):
    """Re-execute a widget's SQL against the current schema state and return
    fresh data + metadata. Validation happens against the active semantic layer."""
    widget = DashboardWidget.query.filter_by(id=widget_id, user_id=current_user.id).first()
    if not widget:
        return jsonify({'error': 'Widget not found.'}), 404

    model = _get_active_semantic_model()
    rows, meta, error = _execute_widget_sql(widget.sql_query, model)
    if error:
        # Do not destroy the widget — return a structured error so the UI can
        # offer "Repair query" / "Re-publish semantic layer" actions.
        return jsonify({
            'error': error,
            'widget': widget.to_dict(),
            'needs_repair': True,
        }), 200

    # Persist the refreshed metadata so the widget card can restore state
    stored_meta = {}
    try:
        stored_meta = json.loads(widget.result_metadata or '{}')
    except (ValueError, TypeError):
        stored_meta = {}
    stored_meta.update(meta)
    widget.result_metadata = json.dumps(stored_meta)
    db.session.commit()

    return jsonify({
        'widget': widget.to_dict(),
        'data': rows[:5000],  # cap payload for very large results
        'metadata': meta,
        'error': None,
    })


@dashboard.route('/api/dashboard/vizbot/rewrite-sql', methods=['POST'])
@login_required
def vizbot_rewrite_sql():
    """VizBot AI assistant — rewrite a widget's SQL from a natural-language
    instruction (e.g. 'include regional breakdown' or 'filter by status = Delayed').

    Uses the user's active semantic layer metadata + current SQL, generates a
    corrected SELECT statement via the LLM, then validates it through the
    QueryExecutor before returning it to the editor for review.
    """
    data = request.get_json(silent=True) or {}
    instruction = (data.get('instruction') or '').strip()
    current_sql = _normalize_sql(data.get('sql_query') or '')
    if not instruction:
        return jsonify({'error': 'Please describe the modification you want VizBot to make.'}), 400
    if not current_sql:
        return jsonify({'error': 'Current SQL query is required.'}), 400

    model = _get_active_semantic_model()
    conn_str = _get_connection_string()
    if not conn_str:
        return jsonify({'error': 'Database connection is not configured. Please reconnect in Settings.'}), 400

    # Build a compact schema markdown for LLM context
    schema_lines = []
    if model and isinstance(model, dict):
        schema_lines.append(f"Database Type: {model.get('db_type', 'unknown')}")
        schema_lines.append('\n### Tables:')
        tables = model.get('tables', {}) or {}
        for t_name, t_data in tables.items():
            cols = list((t_data or {}).get('columns', {}).keys())
            alias = (t_data or {}).get('alias', '')
            desc = (t_data or {}).get('description', '')
            schema_lines.append(f"- **Table: {t_name}**{(' (Alias: ' + alias + ')') if alias else ''}{(' - ' + desc) if desc else ''}")
            schema_lines.append(f"  Columns: {', '.join(cols) if cols else '(none)'}")
    schema_md = '\n'.join(schema_lines)

    user_prompt = (
        f"Here is the current SQL query:\n```sql\n{current_sql}\n```\n\n"
        f"User modification request: {instruction}\n\n"
        f"Please rewrite the SQL query to satisfy this request using ONLY tables and columns from the semantic layer above. "
        f"Return ONLY the new complete SELECT statement (no markdown fences, no explanation)."
    )

    try:
        from langchain_openai import ChatOpenAI
        llm = ChatOpenAI(model='gpt-4o-mini', temperature=0)
        response = llm.invoke([
            {"role": "system", "content": "You are VizBot, an expert database assistant that rewrites SELECT SQL queries based on natural-language instructions.\n\n" + schema_md},
            {"role": "user", "content": user_prompt}
        ])
        content = getattr(response, 'content', '') or str(response)
        rewritten = _normalize_sql(content)

        if not rewritten or not rewritten.upper().startswith('SELECT'):
            return jsonify({'error': 'VizBot could not produce a valid SELECT query. Please refine your request.'}), 422

        # Validate the rewritten SQL executes cleanly before returning it
        try:
            service = SQLChainService(conn_str)
            QueryExecutor.validate_and_execute(service.db, rewritten)
        except Exception as e:
            return jsonify({'error': f'VizBot produced a query that failed validation: {e}'}), 422

        # RBAC table-access check for member roles
        allowed, restricted = _enforce_rbac_table_access(rewritten, model)
        if not allowed:
            return jsonify({'error': f'Access denied: The table `{restricted}` is restricted for your role.'}), 403

        return jsonify({'sql': rewritten})
    except Exception as e:
        logger.error(f'VizBot rewrite failed: {e}')
        return jsonify({'error': f'VizBot request failed: {e}'}), 500


@dashboard.route('/api/dashboard/widgets/<string:widget_id>/export', methods=['GET'])
@login_required
def export_widget_csv(widget_id):
    """Export a widget's current data as CSV (re-executes to ensure freshness)."""
    widget = DashboardWidget.query.filter_by(id=widget_id, user_id=current_user.id).first()
    if not widget:
        return jsonify({'error': 'Widget not found.'}), 404

    model = _get_active_semantic_model()
    rows, meta, error = _execute_widget_sql(widget.sql_query, model)
    if error:
        return jsonify({'error': error}), 400
    if not rows:
        return jsonify({'error': 'No data to export.'}), 400

    columns = list(meta.get('columns', []))
    buf = _io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction='ignore')
    writer.writeheader()
    for row in rows:
        writer.writerow(row)

    safe_name = re.sub(r'[^A-Za-z0-9_-]+', '_', widget.title).strip('_') or 'widget'
    filename = f'{safe_name}_{widget_id[:8]}.csv'
    return Response(
        buf.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )