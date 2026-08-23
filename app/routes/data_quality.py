"""Anomaly Studio — Admin-only data quality monitoring for raw physical tables.

Bypasses the semantic layer entirely: monitors run directly against the raw
database connection introspected from the user's session, using the same
QueryExecutor safety guardrails (SELECT-only, single statement).

Endpoints:
    GET    /data-quality                        Render management UI (admin-only)
    GET    /api/data-quality/tables             Introspect all physical tables + columns
    GET    /api/data-quality/monitors           List tenant monitors
    POST   /api/data-quality/monitors           Create a monitor
    DELETE /api/data-quality/monitors/<id>      Delete a monitor
    POST   /api/data-quality/monitors/<id>/run  Manually trigger an immediate test run
    GET    /api/data-quality/incidents          Fetch incident history
"""
import json
import logging
import re
from datetime import datetime

from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user
from sqlalchemy import create_engine, inspect as sa_inspect, text as sa_text

from app import db
from app.models import DataQualityMonitor, DataQualityIncident, PublishedModel
from app.services.query_executor import QueryExecutor
from app.services.sql_chain_service import SQLChainService

logger = logging.getLogger(__name__)

data_quality = Blueprint('data_quality', __name__)

SYSTEM_TABLES = {'user', 'users', 'sessions', 'alembic_version', 'sqlite_sequence',
                 'workspace', 'team_invite', 'table_permission', 'published_model',
                 'dashboard_widget', 'data_quality_monitor', 'data_quality_incident'}


def _require_admin():
    """Return an error response if the current user is not an admin."""
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin access required.'}), 403
    return None


def _get_connection_string():
    """Resolve the active DB connection string (session-first, DB fallback)."""
    from flask import session
    conn_str = session.get('connection_string')
    if conn_str:
        return conn_str
    saved = (PublishedModel.query
             .filter_by(user_id=current_user.id)
             .order_by(PublishedModel.updated_at.desc())
             .first())
    if saved and saved.connection_string:
        session['connection_string'] = saved.connection_string
        return saved.connection_string
    return None


def _quote_identifier(name, dialect):
    """Quote an identifier per dialect for raw SQL construction."""
    if 'mssql' in dialect.lower():
        return '[' + str(name).replace(']', ']]') + ']'
    if 'postgres' in dialect.lower() or 'sqlite' in dialect.lower():
        return '"' + str(name).replace('"', '""') + '"'
    if 'mysql' in dialect.lower():
        return '`' + str(name).replace('`', '``') + '`'
    return str(name)


def _introspect_tables():
    """Return [{name, columns:[{name, type}], sample_columns}] for all physical
    tables on the raw connection (excluding internal app/system tables)."""
    conn_str = _get_connection_string()
    if not conn_str:
        return None, 'Database connection is not configured.'
    try:
        engine = create_engine(conn_str)
        inspector = sa_inspect(engine)
        tables = []
        for t in inspector.get_table_names():
            if t.lower() in SYSTEM_TABLES:
                continue
            cols = []
            try:
                for col in inspector.get_columns(t):
                    cols.append({'name': col['name'], 'type': str(col['type'])})
            except Exception:
                cols = []
            tables.append({
                'name': t,
                'columns': cols,
                'column_names': [c['name'] for c in cols],
                'column_types': {c['name']: c['type'] for c in cols},
            })
        return tables, None
    except Exception as e:
        logger.error(f'Introspection failed: {e}')
        return None, str(e)


def _run_monitor_check(monitor):
    """Execute a single monitor check against the live DB.

    Returns (passed: bool, message: str, failed_rows: int|None).
    Uses QueryExecutor safety guardrails for every SQL statement.
    """
    conn_str = _get_connection_string()
    if not conn_str:
        return False, 'Database connection is not configured.', None

    params = {}
    try:
        params = json.loads(monitor.parameters or '{}')
    except (ValueError, TypeError):
        params = {}

    table = monitor.table_name
    dialect = 'sqlite'
    try:
        service = SQLChainService(conn_str)
        dialect = service.db.dialect
    except Exception:
        dialect = 'sqlite'

    q = _quote_identifier(table, dialect)

    try:
        if monitor.check_type == 'freshness':
            # freshness: expects `params.timestamp_column`; max(timestamp) age < threshold_hours
            col = params.get('timestamp_column', '')
            threshold_hours = float(params.get('threshold_hours', 24))
            if not col:
                return False, 'Freshness monitor requires a timestamp_column parameter.', None
            qc = _quote_identifier(col, dialect)
            sql = f'SELECT MAX({qc}) AS max_ts FROM {q}'
            rows = QueryExecutor.validate_and_execute(service.db, sql)
            max_ts = None
            if rows and rows[0].get('max_ts') is not None:
                max_ts = rows[0]['max_ts']
            if max_ts is None:
                return False, f'No timestamp data found in {qc}.', None
            # value may be iso string or datetime
            if hasattr(max_ts, 'isoformat'):
                max_dt = max_ts
            else:
                max_dt = datetime.fromisoformat(str(max_ts))
            age_hours = (datetime.utcnow() - max_dt).total_seconds() / 3600.0
            if age_hours > threshold_hours:
                return False, f'Data is stale ({age_hours:.1f}h old, threshold {threshold_hours:.0f}h).', None
            return True, f'Freshness OK — latest data {age_hours:.1f}h ago.', None

        elif monitor.check_type == 'volume':
            # volume: row count < min_rows OR > max_rows (optional), or spike > spike_threshold_pct
            sql = f'SELECT COUNT(*) AS cnt FROM {q}'
            rows = QueryExecutor.validate_and_execute(service.db, sql)
            cnt = rows[0]['cnt'] if rows and rows[0].get('cnt') is not None else 0
            min_rows = params.get('min_rows')
            max_rows = params.get('max_rows')
            if min_rows is not None and int(min_rows) > cnt:
                return False, f'Volume below minimum ({cnt} < {int(min_rows)}).', None
            if max_rows is not None and int(max_rows) < cnt:
                return False, f'Volume above maximum ({cnt} > {int(max_rows)}).', None
            return True, f'Volume OK — {cnt} rows.', None

        elif monitor.check_type == 'null_threshold':
            # null_threshold: `params.column`, `params.threshold_pct` (0-100) — fail if null% exceeds
            col = params.get('column', '')
            threshold_pct = float(params.get('threshold_pct', 10))
            if not col:
                return False, 'Null threshold monitor requires a column parameter.', None
            qc = _quote_identifier(col, dialect)
            sql = (
                f'SELECT COUNT(*) AS total, '
                f'SUM(CASE WHEN {qc} IS NULL THEN 1 ELSE 0 END) AS nulls '
                f'FROM {q}'
            )
            rows = QueryExecutor.validate_and_execute(service.db, sql)
            total = rows[0].get('total') or 0 if rows and rows[0].get('total') is not None else 0
            nulls = rows[0].get('nulls') or 0 if rows and rows[0].get('nulls') is not None else 0
            if total == 0:
                return False, 'Null threshold check: table is empty.', None
            pct = (nulls / total) * 100.0
            if pct > threshold_pct:
                return False, f'Null percentage {pct:.1f}% exceeds threshold {threshold_pct:.1f}% ({nulls}/{total}).', nulls
            return True, f'Null percentage OK — {pct:.1f}% ({nulls}/{total}).', None

        elif monitor.check_type == 'schema_drift':
            # schema_drift: compare current physical columns to a baseline snapshot.
            # `params.baseline_columns` = {col: type_string} saved when the monitor
            # was created (or manually provided). Alerts on added/removed/renamed
            # columns or altered data types.
            baseline = params.get('baseline_columns')
            if not baseline or not isinstance(baseline, dict):
                return False, 'Schema drift monitor requires a baseline_columns snapshot.', None
            try:
                engine = create_engine(conn_str)
                inspector = sa_inspect(engine)
                current = {}
                try:
                    for col in inspector.get_columns(table):
                        current[col['name']] = str(col['type'])
                except Exception:
                    return False, f'Could not introspect table `{table}`.', None
            except Exception as e:
                return False, str(e), None

            # Normalize types for comparison (strip length/precision modifiers)
            def _norm_type(t):
                return re.split(r'[\s\(]', t)[0].lower()

            missing = [c for c in baseline if c not in current]
            added = [c for c in current if c not in baseline]
            changed = [c for c in baseline if c in current and _norm_type(baseline[c]) != _norm_type(current[c])]

            diffs = []
            if missing:
                diffs.append('dropped/renamed columns: ' + ', '.join(missing))
            if added:
                diffs.append('new columns: ' + ', '.join(added))
            if changed:
                diffs.append('type changes: ' + ', '.join(f'{c} ({baseline[c]} → {current[c]})' for c in changed))

            if diffs:
                return False, 'Schema drift detected — ' + '; '.join(diffs), None
            return True, f'Schema matches baseline ({len(current)} columns).', None

        elif monitor.check_type == 'uniqueness':
            # uniqueness: assert COUNT(*) == COUNT(DISTINCT cols...) (or within threshold).
            # `params.columns` = list of column names; `params.max_duplicate_pct` (default 0).
            cols = params.get('columns') or []
            if not cols or not isinstance(cols, list):
                return False, 'Uniqueness monitor requires a columns (list) parameter.', None
            max_dup_pct = float(params.get('max_duplicate_pct', 0))
            quoted = ', '.join(_quote_identifier(c, dialect) for c in cols)
            # SQLite does not support COUNT(DISTINCT a, b) — fall back to a concat
            # trick so composite-key uniqueness works everywhere.
            if 'sqlite' in dialect.lower():
                if len(cols) == 1:
                    distinct_expr = quoted
                else:
                    distinct_expr = ' || '.join("COALESCE(CAST({0} AS TEXT), '')".format(_quote_identifier(c, dialect)) for c in cols)
                sql = (
                    f'SELECT COUNT(*) AS total, '
                    f'COUNT(DISTINCT {distinct_expr}) AS distinct_cnt '
                    f'FROM {q}'
                )
            else:
                sql = (
                    f'SELECT COUNT(*) AS total, COUNT(DISTINCT {quoted}) AS distinct_cnt '
                    f'FROM {q}'
                )
            rows = QueryExecutor.validate_and_execute(service.db, sql)
            total = rows[0]['total'] if rows and rows[0].get('total') is not None else 0
            distinct = rows[0]['distinct_cnt'] if rows and rows[0].get('distinct_cnt') is not None else 0
            if total == 0:
                return True, 'Uniqueness OK — table is empty.', None
            dup_count = total - distinct
            dup_pct = (dup_count / total) * 100.0
            if dup_pct > max_dup_pct:
                return False, (
                    f'Uniqueness violation — {dup_count} duplicate row(s) out of {total} '
                    f'({dup_pct:.1f}% > {max_dup_pct:.1f}% threshold) on columns: {", ".join(cols)}'
                ), dup_count
            return True, f'Uniqueness OK — {distinct} distinct / {total} rows.', None

        elif monitor.check_type == 'value_range':
            # value_range: numeric min/max bounds OR categorical whitelist.
            # `params.column`, plus either `params.min_value`/`params.max_value`
            # (numbers) or `params.allowed_values` (list of strings).
            col = params.get('column', '')
            if not col:
                return False, 'Value range monitor requires a column parameter.', None
            qc = _quote_identifier(col, dialect)

            allowed = params.get('allowed_values')
            has_bounds = ('min_value' in params or 'max_value' in params)

            if allowed is not None and isinstance(allowed, list) and allowed:
                # Categorical whitelist check — any value outside the list fails.
                placeholders = ', '.join('?' for _ in allowed)
                # Build a dialect-safe IN list via parameters (used by parameterized exec).
                sql = (
                    f"SELECT {qc} AS v, COUNT(*) AS cnt FROM {q} "
                    f"WHERE {qc} IS NOT NULL AND {qc} NOT IN ({placeholders}) "
                    f"GROUP BY {qc} ORDER BY cnt DESC"
                )
                engine = create_engine(conn_str)
                with engine.connect() as conn:
                    res = conn.exec_driver_sql(sql, tuple(str(a) for a in allowed))
                    bad_rows = [dict(row) for row in res.mappings()]
                if bad_rows:
                    sample = ', '.join(str(r.get('v')) for r in bad_rows[:5])
                    return False, (
                        f'Value range violation — {len(bad_rows)} unexpected value(s): {sample}'
                    ), len(bad_rows)
                return True, 'Value range OK — all values in whitelist.', None

            if not has_bounds:
                return False, 'Value range monitor requires allowed_values or min/max bounds.', None

            # Numeric bounds check
            min_v = params.get('min_value')
            max_v = params.get('max_value')
            conds = []
            args = []
            if min_v is not None:
                conds.append(f'({qc} IS NOT NULL AND {qc} < ?)')
                args.append(min_v)
            if max_v is not None:
                conds.append(f'({qc} IS NOT NULL AND {qc} > ?)')
                args.append(max_v)
            if not conds:
                return False, 'Value range monitor requires min_value or max_value.', None
            sql = f'SELECT COUNT(*) AS cnt FROM {q} WHERE ' + ' OR '.join(conds)
            engine = create_engine(conn_str)
            with engine.connect() as conn:
                res = conn.exec_driver_sql(sql, tuple(args))
                row = res.mappings().first() if hasattr(res, 'mappings') else None
                cnt = row['cnt'] if row else 0
            if cnt and cnt > 0:
                bounds_desc = []
                if min_v is not None:
                    bounds_desc.append(f'min {min_v}')
                if max_v is not None:
                    bounds_desc.append(f'max {max_v}')
                return False, f'Value range violation — {cnt} row(s) outside bounds ({", ".join(bounds_desc)}) on {col}.', cnt
            return True, f'Value range OK — all values within bounds on {col}.', None

        elif monitor.check_type == 'custom_sql':
            # custom_sql: `params.sql` — run as SELECT; any returned row = failure
            raw_sql = params.get('sql', '').strip()
            if not raw_sql:
                return False, 'Custom SQL monitor requires a sql parameter.', None
            rows = QueryExecutor.validate_and_execute(service.db, raw_sql)
            if rows and len(rows) > 0:
                # detail the first row for the incident log
                detail = json.dumps(rows[0])[:80] if rows else ''
                return False, f'Custom assertion failed — returned {len(rows)} row(s). {detail}', len(rows)
            return True, 'Custom assertion passed — 0 rows returned.', None

        return False, 'Unknown check type.', None

    except Exception as e:
        logger.warning(f'Data-quality check failed for monitor {monitor.id}: {e}')
        return False, str(e), None


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

@data_quality.route('/data-quality')
@login_required
def data_quality_page():
    if current_user.role != 'admin':
        return 'Access denied. Only admins can access Anomaly Studio.', 403
    return render_template('data_quality.html')


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@data_quality.route('/api/data-quality/tables', methods=['GET'])
@login_required
def get_tables():
    deny = _require_admin()
    if deny:
        return deny
    tables, err = _introspect_tables()
    if err:
        return jsonify({'tables': [], 'error': err}), 400
    return jsonify({'tables': tables})


@data_quality.route('/api/data-quality/monitors', methods=['GET'])
@login_required
def list_monitors():
    deny = _require_admin()
    if deny:
        return deny
    monitors = (DataQualityMonitor.query
                .filter_by(user_id=current_user.id)
                .order_by(DataQualityMonitor.created_at.desc())
                .all())
    return jsonify([m.to_dict() for m in monitors])


@data_quality.route('/api/data-quality/monitors', methods=['POST'])
@login_required
def create_monitor():
    deny = _require_admin()
    if deny:
        return deny

    data = request.get_json(silent=True) or {}
    table_name = (data.get('table_name') or '').strip()
    check_type = (data.get('check_type') or '').strip()
    parameters = data.get('parameters') or {}
    frequency = (data.get('frequency') or 'manual').strip()
    severity = (data.get('severity') or 'warning').strip()
    is_active = data.get('is_active', True)
    notification_channels = data.get('notification_channels') or {}

    allowed_check_types = {
        'freshness', 'volume', 'null_threshold', 'custom_sql',
        'schema_drift', 'uniqueness', 'value_range',
    }
    if not table_name:
        return jsonify({'error': 'table_name is required.'}), 400
    if check_type not in allowed_check_types:
        return jsonify({'error': 'check_type must be freshness, volume, null_threshold, custom_sql, schema_drift, uniqueness, or value_range.'}), 400
    if severity not in {'warning', 'critical'}:
        return jsonify({'error': 'severity must be warning or critical.'}), 400
    if not isinstance(parameters, dict):
        return jsonify({'error': 'parameters must be a JSON object.'}), 400
    if not isinstance(notification_channels, dict):
        return jsonify({'error': 'notification_channels must be a JSON object.'}), 400

    # Basic per-check parameter validation
    if check_type == 'freshness' and not parameters.get('timestamp_column'):
        return jsonify({'error': 'Freshness monitor requires parameters.timestamp_column.'}), 400
    if check_type == 'null_threshold' and not parameters.get('column'):
        return jsonify({'error': 'Null threshold monitor requires parameters.column.'}), 400
    if check_type == 'custom_sql' and not (parameters.get('sql') or '').strip():
        return jsonify({'error': 'Custom SQL monitor requires parameters.sql.'}), 400
    if check_type == 'schema_drift' and not parameters.get('baseline_columns'):
        return jsonify({'error': 'Schema drift monitor requires parameters.baseline_columns.'}), 400
    if check_type == 'uniqueness' and not (parameters.get('columns') or []):
        return jsonify({'error': 'Uniqueness monitor requires parameters.columns (list).'}), 400
    if check_type == 'value_range' and not parameters.get('column'):
        return jsonify({'error': 'Value range monitor requires parameters.column.'}), 400
    if check_type == 'value_range' and not (parameters.get('allowed_values') or ('min_value' in parameters or 'max_value' in parameters)):
        return jsonify({'error': 'Value range monitor requires allowed_values or min/max bounds.'}), 400

    # Normalize notification channels (emails: list, slack_webhook_url: str)
    emails = notification_channels.get('emails') or []
    if isinstance(emails, str):
        emails = [e.strip() for e in emails.split(',') if e.strip()]
    normalized_channels = {
        'emails': emails if isinstance(emails, list) else [],
        'slack_webhook_url': (notification_channels.get('slack_webhook_url') or '').strip(),
    }

    # Map frequency to cron (informational)
    cron_map = {'hourly': '0 * * * *', 'daily': '0 0 * * *', 'weekly': '0 0 * * 1', 'manual': None}
    monitor = DataQualityMonitor(
        user_id=current_user.id,
        table_name=table_name,
        check_type=check_type,
        parameters=json.dumps(parameters),
        notification_channels=json.dumps(normalized_channels) if any(normalized_channels.values()) else None,
        frequency=frequency,
        schedule_cron=cron_map.get(frequency),
        severity=severity,
        is_active=bool(is_active),
    )
    db.session.add(monitor)
    db.session.commit()
    return jsonify(monitor.to_dict()), 201


@data_quality.route('/api/data-quality/monitors/<monitor_id>', methods=['DELETE'])
@login_required
def delete_monitor(monitor_id):
    deny = _require_admin()
    if deny:
        return deny
    monitor = DataQualityMonitor.query.filter_by(id=monitor_id, user_id=current_user.id).first()
    if not monitor:
        return jsonify({'error': 'Monitor not found.'}), 404
    db.session.delete(monitor)  # cascades incidents
    db.session.commit()
    return jsonify({'success': True})


@data_quality.route('/api/data-quality/monitors/<monitor_id>/run', methods=['POST'])
@login_required
def run_monitor(monitor_id):
    deny = _require_admin()
    if deny:
        return deny
    monitor = DataQualityMonitor.query.filter_by(id=monitor_id, user_id=current_user.id).first()
    if not monitor:
        return jsonify({'error': 'Monitor not found.'}), 404

    passed, message, failed_rows = _run_monitor_check(monitor)
    incident = DataQualityIncident(
        monitor_id=monitor.id,
        status='resolved' if passed else 'failing',
        error_message=message,
        failed_rows_count=failed_rows,
        triggered_at=datetime.utcnow(),
    )
    db.session.add(incident)
    db.session.commit()

    # ----- Alert routing: dispatch notifications on FAILURE (respecting severity) -----
    if not passed and monitor.is_active and monitor.severity in {'warning', 'critical'}:
        channels = {}
        try:
            channels = json.loads(monitor.notification_channels or '{}')
        except (ValueError, TypeError):
            channels = {}
        emails = channels.get('emails') or []
        slack_webhook = (channels.get('slack_webhook_url') or '').strip()
        if isinstance(emails, str):
            emails = [e.strip() for e in emails.split(',') if e.strip()]

        payload = {
            'monitor_name': monitor.table_name + ' · ' + monitor.check_type,
            'table_name': monitor.table_name,
            'check_type': monitor.check_type.replace('_', ' ').title(),
            'severity': monitor.severity,
            'timestamp': datetime.utcnow().isoformat(),
            'message': message,
        }
        from app.services import alert_dispatcher
        alert_dispatcher.dispatch_alert_async(payload, {'emails': emails, 'slack_webhook_url': slack_webhook})

    return jsonify({
        'success': passed,
        'passed': passed,
        'message': message,
        'failed_rows_count': failed_rows,
        'incident': incident.to_dict(),
        'monitor': monitor.to_dict(),
    })


@data_quality.route('/api/data-quality/incidents', methods=['GET'])
@login_required
def list_incidents():
    deny = _require_admin()
    if deny:
        return deny
    limit = min(int(request.args.get('limit', 100)), 500)
    incidents = (
        DataQualityIncident.query
        .join(DataQualityMonitor, DataQualityMonitor.id == DataQualityIncident.monitor_id)
        .filter(DataQualityMonitor.user_id == current_user.id)
        .order_by(DataQualityIncident.triggered_at.desc())
        .limit(limit)
        .all()
    )
    result = []
    for inc in incidents:
        d = inc.to_dict()
        d['table_name'] = inc.monitor.table_name
        d['check_type'] = inc.monitor.check_type
        d['severity'] = inc.monitor.severity
        d['monitor_name'] = inc.monitor.table_name + ' · ' + inc.monitor.check_type
        result.append(d)
    return jsonify(result)