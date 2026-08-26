from flask import Blueprint, render_template, request, redirect, url_for, session, jsonify, current_app, Response, stream_with_context
from flask_login import login_required, current_user
from app import db
from app.profiler import profile_database
from app.services.sql_chain_service import SQLChainService
from app.models import SavedQuery, PublishedModel
from datetime import datetime
from app.services.query_executor import QueryExecutor
from app.services import cache_manager
from app.services.personas import run_persona
from app.services.result_guardrails import (
    classify_result_size,
    build_pivot_suggestions,
)
from app.services.rbac import (
    get_member_allowed_tables,
    get_member_column_permissions,
    save_member_allowed_tables,
    get_effective_tables,
    get_disallowed_tables,
    get_disallowed_columns,
    is_admin,
)
from app.services.column_rbac_guard import validate_query_columns
import logging
import json
import re
import urllib.parse
from datetime import date, datetime
import os

main = Blueprint('main', __name__)
logger = logging.getLogger(__name__)

class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (date, datetime)):
            return obj.isoformat()
        return super().default(obj)

@main.route('/')
def index():
    """Public marketing/landing page. Authenticated users see this too."""
    from datetime import datetime as _now
    return render_template('index.html', current_year=_now.now().year)

@main.route('/settings/api-keys')
@login_required
def settings_api_keys():
    """Render the BYOK key-management settings page (admin only)."""
    if current_user.role != 'admin':
        return render_template('403.html', title='Access Restricted', reason='Access denied. Only admins can configure workspace API keys and LLM providers.'), 403
    return render_template('settings_api_keys.html', user=current_user)


@main.route('/api/settings/byok', methods=['POST'])
@login_required
def byok_save():
    """Encrypt + persist the user's provider API keys and enable BYOK.

    ADMIN ONLY - BYOK keys are workspace-scoped: members inherit the
    admin's provider configuration automatically.
    NEVER returns or logs plain-text keys.
    """
    if current_user.role != 'admin':
        return jsonify({"error": "Admin access required. Only admins can configure workspace API keys."}), 403
    from app.utils.encryption import encrypt_secret
    data = request.get_json(silent=True) or {}
    provider = (data.get('llm_provider') or data.get('provider') or '').strip().lower()
    if provider not in ('openai', 'anthropic', 'openrouter', 'gemini', 'custom'):
        return jsonify({"error": "Unsupported provider"}), 400

    key = (data.get('api_key') or data.get('llm_api_key') or '').strip()
    base_url = (data.get('llm_base_url') or '').strip() or None
    model_id = (data.get('llm_model_id') or '').strip() or None
    encryption_note = 'stored (Fernet-encrypted)'

    # Persist provider metadata (universal Cline-style config)
    current_user.llm_provider = provider
    current_user.active_llm_provider = provider
    current_user.llm_base_url = base_url
    current_user.llm_model_id = model_id

    # Store encrypted key in the appropriate legacy column
    if provider == 'anthropic':
        if key:
            current_user.anthropic_api_key_encrypted = encrypt_secret(key)
        if not getattr(current_user, 'anthropic_api_key_encrypted', None):
            encryption_note = 'provider key required to enable BYOK for anthropic'
    else:
        if key:
            current_user.openai_api_key_encrypted = encrypt_secret(key)
        if not getattr(current_user, 'openai_api_key_encrypted', None):
            encryption_note = 'provider key required to enable BYOK for ' + provider

    current_user.byok_enabled = bool(data.get('byok_enabled', key and True))
    if current_user.byok_enabled and not (
        getattr(current_user, 'openai_api_key_encrypted', None) or
        getattr(current_user, 'anthropic_api_key_encrypted', None)
    ):
        return jsonify({"error": "Cannot enable BYOK without a stored provider key."}), 400

    db.session.commit()
    return jsonify({
        "success": True,
        "active_provider": current_user.active_llm_provider,
        "llm_provider": current_user.llm_provider,
        "llm_base_url": current_user.llm_base_url,
        "llm_model_id": current_user.llm_model_id,
        "byok_enabled": current_user.byok_enabled,
        "note": encryption_note,
        "openai_has": bool(current_user.openai_api_key_encrypted),
        "anthropic_has": bool(current_user.anthropic_api_key_encrypted),
    })


@main.route('/api/settings/byok/test', methods=['POST'])
@login_required
def byok_test():
    """Live validation via a lightweight provider call before saving (admin only)."""
    if current_user.role != 'admin':
        return jsonify({"error": "Admin access required."}), 403
    from app.agents.llm_router import test_provider_key
    data = request.get_json(silent=True) or {}
    provider = (data.get('llm_provider') or data.get('provider') or '').strip().lower()
    key = (data.get('api_key') or data.get('llm_api_key') or '').strip()
    base_url = (data.get('llm_base_url') or '').strip() or None
    if provider not in ('openai', 'anthropic', 'openrouter', 'gemini', 'custom') or not key:
        return jsonify({"error": "provider + api_key are required"}), 400
    ok, msg = test_provider_key(provider, key, base_url=base_url)
    return jsonify({"valid": ok, "message": msg})


@main.route('/api/settings/byok/status')
@login_required
def byok_status():
    """Return masked BYOK status (never the plaintext key)."""
    from app.utils.encryption import mask_key
    from app.agents.llm_router import get_byok_state
    enabled, active, has_openai, has_anthropic = get_byok_state(current_user)
    return jsonify({
        "byok_enabled": enabled,
        "active_provider": active,
        "llm_provider": getattr(current_user, 'llm_provider', 'openai') or 'openai',
        "llm_base_url": getattr(current_user, 'llm_base_url', None),
        "llm_model_id": getattr(current_user, 'llm_model_id', None),
        "openai_has": has_openai,
        "anthropic_has": has_anthropic,
        "openai_masked": mask_key(decrypt_placeholder(current_user.openai_api_key_encrypted)),
        "anthropic_masked": mask_key(decrypt_placeholder(current_user.anthropic_api_key_encrypted)),
    })


def decrypt_placeholder(ciphertext):
    """Helper: decrypted key for masking only (never serialized plaintext)."""
    if not ciphertext:
        return None
    from app.utils.encryption import decrypt_secret
    return decrypt_secret(ciphertext)


@main.route('/connect')
@login_required
def connect():
    # Members cannot access database connection settings
    if current_user.role != 'admin':
        return render_template('403.html', title='Access Restricted', reason='Access denied. Only admins can configure database connections.'), 403
    return render_template('connection_form.html')

@main.route('/set_connection', methods=['POST'])
@login_required
def set_connection():
    # Members cannot change database connections
    if current_user.role != 'admin':
        return jsonify({"error": "Access denied. Only admins can configure connections."}), 403
    db_type = request.form.get('db_type')
    server = request.form.get('server')
    database = request.form.get('database')
    username = request.form.get('username')
    password = request.form.get('password')
    schema = request.form.get('schema') or ''
    credentials_path = request.form.get('credentials_path', '')

    try:
        from app.profiler import _build_connection_string_for
        connection_string = _build_connection_string_for(
            db_type,
            server=server or '',
            database=database or '',
            username=username or '',
            password=password or '',
            credentials_path=credentials_path,
            http_path=request.form.get('http_path', '') or '',
            catalog=request.form.get('catalog', '') or '',
        )

        profile = profile_database(connection_string, schema=schema if schema else 'public')
        
        session['connection_string'] = connection_string
        session['db_profile'] = profile
        session['schema_name'] = schema
        # Persist the connection context immediately so dashboard widget
        # refreshes / background execution work even before a re-publish.
        from app.models import PublishedModel as PM
        saved_pm = PM.query.filter_by(user_id=current_user.id).order_by(PM.updated_at.desc()).first()
        if saved_pm:
            saved_pm.connection_string = connection_string
            db.session.commit()
        # Invalidate any previously published semantic layer and SQL cache when connecting a new database
        session.pop('published_semantic_layer', None)
        session.pop('chat_history', None)
        cache_manager.clear_cache()
        
        return redirect(url_for('main.semantic_studio'))
    except Exception as e:
        logger.error(f"Connection failed: {e}")
        return render_template('connection_form.html', error=f"Connection Error: {str(e)}")

@main.route('/api/test-connection', methods=['POST'])
@login_required
def test_connection():
    """Live DB connectivity test for the connection form (admin only).

    Mirrors set_connection's field parsing and connection-string building,
    but only runs a lightweight `SELECT 1` with a short connect timeout.
    Returns JSON so the frontend can show a clear success/failure message
    instead of a silent failure or hang.
    """
    if current_user.role != 'admin':
        return jsonify({"success": False, "error": "Access denied. Only admins can test connections."}), 403

    db_type = (request.form.get('db_type') or request.json.get('db_type') if request.is_json else None) or request.form.get('db_type') or ''
    server = request.form.get('server') or (request.json.get('server') if request.is_json else '') or ''
    database = request.form.get('database') or (request.json.get('database') if request.is_json else '') or ''
    username = request.form.get('username') or (request.json.get('username') if request.is_json else '') or ''
    password = request.form.get('password') or (request.json.get('password') if request.is_json else '') or ''
    credentials_path = request.form.get('credentials_path') or (request.json.get('credentials_path') if request.is_json else '') or ''

    if not db_type or not server:
        return jsonify({"success": False, "error": "Missing required fields: database engine and host are required."}), 400

    try:
        from app.profiler import _build_connection_string_for
        conn_str = _build_connection_string_for(
            db_type,
            server=server or '',
            database=database or '',
            username=username or '',
            password=password or '',
            credentials_path=credentials_path,
            http_path=(request.form.get('http_path') or (request.json.get('http_path') if request.is_json else '') or ''),
            catalog=(request.form.get('catalog') or (request.json.get('catalog') if request.is_json else '') or ''),
        )
    except Exception as e:
        current_app.logger.warning("test-connection build failed: %s", e)
        return jsonify({"success": False, "error": f"Could not build connection string: {e}"}), 400

    try:
        from sqlalchemy import create_engine, text
        engine = create_engine(conn_str, connect_args={"connect_timeout": 5}, pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return jsonify({"success": True, "message": "Connection successful. Engine reachable and authenticated."})
    except ImportError as e:
        # Most commonly pyodbc / MSSQL driver missing on the container.
        msg = str(e)
        if "pyodbc" in msg:
            msg = "pyodbc is not installed. Add pyodbc to requirements.txt and ensure unixODBC drivers are installed in the container."
        return jsonify({"success": False, "error": f"Missing driver module: {msg}"}), 500
    except Exception as e:
        # Sanitize: never echo credentials back to the client.
        safe = type(e).__name__
        detail = str(e)
        # Keep only the first meaningful line, strip anything that looks like a password/token.
        for token in (password, username):
            if token and token in detail:
                detail = detail.replace(token, "*****")
        first = (detail or "").splitlines()[0][:220] if detail else ""
        return jsonify({"success": False, "error": f"Connection failed ({safe}): {first}"}), 400


@main.route('/semantic-studio')
@login_required
def semantic_studio():
    # Members cannot access Semantic Studio
    if current_user.role != 'admin':
        return render_template('403.html', title='Access Restricted', reason='Access denied. Only admins can access Semantic Studio.'), 403
    profile = session.get('db_profile')
    if not profile:
        return redirect(url_for('main.index'))
    # Load the user's published model from the database (account-isolated)
    published_model = session.get('published_semantic_layer')
    if not published_model:
        saved = PublishedModel.query.filter_by(user_id=current_user.id).order_by(PublishedModel.updated_at.desc()).first()
        if saved:
            try:
                published_model = json.loads(saved.model_json)
                session['published_semantic_layer'] = published_model
            except Exception:
                published_model = None
    fk_constraints = profile.get('fk_constraints', [])
    primary_keys = profile.get('primary_keys', {})
    return render_template('semantic_studio.html', profile=profile, published_model=published_model, fk_constraints=fk_constraints, primary_keys=primary_keys)

@main.route('/publish-semantic-layer', methods=['POST'])
@login_required
def publish_semantic_layer():
    # Members cannot publish semantic layers
    if current_user.role != 'admin':
        return jsonify({"error": "Access denied. Only admins can publish semantic layers."}), 403
    active_tables = request.form.getlist('active_tables')
    full_profile = session.get('db_profile')
    
    # Global settings (sparse)
    global_joins = request.form.get('global_joins', '').strip()
    global_filters = request.form.get('global_filters', '').strip()
    
    tables_config = {}
    for table in active_tables:
        table_info = full_profile["tables"][table]
        
        # Table level
        alias = request.form.get(f'table_alias_{table}', '').strip()
        desc = request.form.get(f'table_desc_{table}', '').strip()
        
        # Dynamic Custom Metrics parsing
        metrics = []
        i = 0
        while True:
            m_name = request.form.get(f'metric_name_{table}_{i}')
            if m_name is None:
                break
            m_sql = request.form.get(f'metric_sql_{table}_{i}', '').strip()
            m_desc = request.form.get(f'metric_desc_{table}_{i}', '').strip()
            if m_name.strip() and m_sql:
                metric_dict = {"name": m_name.strip(), "sql": m_sql}
                if m_desc:
                    metric_dict["description"] = m_desc
                metrics.append(metric_dict)
            i += 1
            
        # Column level (Sparse serialization: skip empty alias, enums, synonyms)
        columns_config = {}
        for col in table_info["columns"].keys():
            col_alias = request.form.get(f'col_alias_{table}_{col}', '').strip()
            col_enum = request.form.get(f'col_enum_{table}_{col}', '').strip()
            col_syn = request.form.get(f'col_syn_{table}_{col}', '').strip()
            
            col_data = {"type": table_info["columns"][col]["type"]}
            if col_alias:
                col_data["alias"] = col_alias
            if col_enum:
                col_data["enum"] = col_enum
            if col_syn:
                col_data["synonyms"] = [s.strip() for s in col_syn.split(',') if s.strip()]
                
            columns_config[col] = col_data
            
        table_entry = {"columns": columns_config}
        if alias:
            table_entry["alias"] = alias
        if desc:
            table_entry["description"] = desc
        if metrics:
            table_entry["metrics"] = metrics
            
        tables_config[table] = table_entry
    
    published_model = {
        "tables": tables_config,
        "db_type": full_profile["db_type"]
    }
    if global_joins:
        published_model["global_joins"] = global_joins
    if global_filters:
        published_model["global_filters"] = global_filters
    
    session['published_semantic_layer'] = published_model
    # Persist to the database for account isolation
    saved = PublishedModel.query.filter_by(user_id=current_user.id).order_by(PublishedModel.updated_at.desc()).first()
    if saved:
        saved.model_json = json.dumps(published_model)
        saved.db_type = published_model.get('db_type')
        # Persist the active connection string for background execution
        if session.get('connection_string'):
            saved.connection_string = session.get('connection_string')
    else:
        saved = PublishedModel(
            user_id=current_user.id,
            model_json=json.dumps(published_model),
            db_type=published_model.get('db_type'),
            connection_string=session.get('connection_string')
        )
        db.session.add(saved)
    db.session.commit()
    # Clear SQL cache so newly published tables are picked up immediately
    cache_manager.clear_cache()
    logger.info("Streamlined sparse semantic layer published successfully.")
    return redirect(url_for('main.chat_interface'))

def _build_live_semantic_model(profile=None, published=None):
    """Build the semantic model LIVE from the introspected DB profile.

    Table/column membership is ALWAYS resolved from the current connection's
    introspection (db_profile) so admin permission changes appear instantly
    with ZERO publishing. Semantic enrichments (aliases, descriptions, metrics,
    enums, synonyms, global joins/filters) are layered from the stored model.
    """
    profile = profile if profile is not None else session.get('db_profile') or {}
    published = published if published is not None else session.get('published_semantic_layer') or {}
    if not isinstance(published, dict):
        published = {}
    pub_tables = published.get('tables') or {}
    _SYSTEM = {'user', 'users', 'sessions', 'alembic_version', 'sqlite_sequence'}
    tables = {}
    # LIVE MEMBERSHIP GATE: when a published model exists, ONLY tables present
    # in its active-table set (pub_tables) may appear in chat + LLM context.
    # Admin toggle-offs remove the table from pub_tables, so it must also
    # disappear from /get-available-tables, chips, and the prompt immediately.
    for tname, tinfo in (profile.get('tables') or {}).items():
        if tname.lower() in _SYSTEM:
            continue
        if pub_tables and tname not in pub_tables:
            continue
        cols = {}
        tcols = tinfo.get('columns') or {}
        pub_t = pub_tables.get(tname) or {}
        pub_cols = pub_t.get('columns') or {}
        for cname, cinfo in tcols.items():
            entry = {'type': cinfo.get('type', 'unknown') if isinstance(cinfo, dict) else 'unknown'}
            pub_c = pub_cols.get(cname) or {}
            for key in ('alias', 'enum', 'synonyms'):
                if pub_c.get(key):
                    entry[key] = pub_c[key]
            cols[cname] = entry
        entry = {'columns': cols}
        for key in ('alias', 'description', 'metrics'):
            if pub_t.get(key):
                entry[key] = pub_t[key]
        tables[tname] = entry
    model = {'tables': tables, 'db_type': profile.get('db_type', '')}
    for key in ('global_joins', 'global_filters'):
        if published.get(key):
            model[key] = published[key]
    return model


def _load_workspace_semantic_context():
    """Resolve the published semantic layer + connection string for the
    current user's WORKSPACE.

    - Admins: use their own session data / published model.
    - Members: inherit the workspace owner's (admin's) published model and
      connection string, strictly scoped by member.workspace_id. This never
      leaks across workspaces because rows are keyed by the workspace owner id.

    Returns (semantic_model_dict or None, connection_string or None).
    Also primes the current user's session so downstream routes (chat,
    generate-sql) work with the resolved context.
    """
    semantic_model = session.get('published_semantic_layer')
    conn_str = session.get('connection_string')
    if semantic_model:
        # MEMBER SYNC FIX: non-admin users must re-read the workspace owner's
        # latest PublishedModel row from the DB (source of truth) on EVERY load.
        # The session snapshot alone is stale after the admin publishes new
        # semantics or grants tables — members would keep seeing/querying the
        # old allowed set. If the DB row exists, it WINS over the snapshot.
        if current_user.role != 'admin':
            owner_id = _workspace_owner_id()
            if owner_id:
                saved = PublishedModel.query.filter_by(user_id=owner_id).order_by(PublishedModel.updated_at.desc()).first()
                if saved:
                    try:
                        semantic_model = json.loads(saved.model_json)
                        session['published_semantic_layer'] = semantic_model
                    except Exception:
                        pass
                    if saved.connection_string:
                        conn_str = saved.connection_string
                        session['connection_string'] = conn_str
        if not conn_str:
            # Still try to backfill the connection string from DB for members.
            owner_id = _workspace_owner_id()
            if owner_id:
                saved = PublishedModel.query.filter_by(user_id=owner_id).order_by(PublishedModel.updated_at.desc()).first()
                if saved and saved.connection_string:
                    conn_str = saved.connection_string
                    session['connection_string'] = conn_str
        # ZERO-PUBLISH: still rebuild from live introspection so newly-unlocked
        # tables always appear instantly even when a session snapshot exists.
        live = _build_live_semantic_model(profile=session.get('db_profile'), published=semantic_model)
        if live and live.get('tables'):
            session['published_semantic_layer'] = live
            return live, conn_str
        return semantic_model, conn_str

    from app.models import Workspace
    saved = None
    if current_user.role == 'admin':
        saved = PublishedModel.query.filter_by(user_id=current_user.id).order_by(PublishedModel.updated_at.desc()).first()
    elif current_user.workspace_id:
        workspace = Workspace.query.get(current_user.workspace_id)
        if workspace:
            # PublishedModel is keyed by user_id; find the workspace owner's
            # latest publish. This never leaks across workspaces because we
            # scope by the workspace owner id.
            saved = PublishedModel.query.filter_by(user_id=workspace.owner_id).order_by(PublishedModel.updated_at.desc()).first()
    if saved:
        try:
            semantic_model = json.loads(saved.model_json)
            session['published_semantic_layer'] = semantic_model
        except Exception:
            semantic_model = None
        if saved.connection_string:
            conn_str = saved.connection_string
            session['connection_string'] = conn_str

    # ZERO-PUBLISH: compile the prompt model LIVE from the introspected DB
    # profile (layering stored aliases/metrics/enums on top). Newly-unlocked
    # tables appear instantly in chat with no publish/refresh.
    live = _build_live_semantic_model(profile=session.get('db_profile'), published=semantic_model)
    if live and live.get('tables'):
        session['published_semantic_layer'] = live
        return live, conn_str
    return semantic_model, conn_str


def _workspace_owner_id():
    """Return the user_id that owns the current user's workspace (or None)."""
    from app.models import Workspace
    if getattr(current_user, 'workspace_id', None):
        ws = Workspace.query.get(current_user.workspace_id)
        if ws:
            return ws.owner_id
    return None


@main.route('/chat')
@login_required
def chat_interface():
    semantic_model, _ = _load_workspace_semantic_context()
    if not semantic_model:
        return redirect(url_for('main.index'))

    from flask_login import current_user as cu
    # Apply RBAC: filter out restricted tables for non-admin users
    filtered_model = get_effective_tables(semantic_model, cu)

    # Resolve the workspace name so the UI can label defaults correctly
    workspace_name = ''
    try:
        from app.models import Workspace as WorkspaceModel
        wid = getattr(cu, 'workspace_id', None)
        if wid:
            w = WorkspaceModel.query.get(wid)
            if w:
                workspace_name = w.name or ''
    except Exception:
        workspace_name = ''

    return render_template(
        'text_to_sql.html',
        schema=json.dumps(filtered_model),
        workspace_name=workspace_name,
    )

# ===== Team Invites & Seat Limits =====

def get_member_seat_limit(user):
    """Return max MEMBER seats allowed based on the license tier (excluding admin).

    Community (free)   : 1 member (1 Admin / 1 Member total).
    Team / Pro ($399)  : up to 15 team members.
    Enterprise (custom): None = unlimited seats.
    """
    from app.services.licensing import get_seat_limit, normalize_tier
    return get_seat_limit(getattr(user, 'subscription_tier', 'community') or 'community')


@main.route('/admin/invites', methods=['GET'])
@login_required
def admin_invites_get():
    """List all pending invites for the admin's workspace (admin only)."""
    if current_user.role != 'admin':
        return jsonify({"error": "Admin access required"}), 403

    from app.models import TeamInvite, Workspace, User
    workspace_id = current_user.workspace_id
    if not workspace_id:
        return jsonify({"invites": [], "members": [], "seat_limit": get_member_seat_limit(current_user)}), 200

    invites = TeamInvite.query.filter_by(workspace_id=workspace_id, status='pending').all()
    # Count existing members in this workspace
    members = User.query.filter_by(workspace_id=workspace_id, role='member').all()
    return jsonify({
        "invites": [{
            "id": inv.id, "email": inv.email, "token": inv.token,
            "role": getattr(inv, 'role', 'member') or 'member',
            "email_sent": bool(getattr(inv, 'email_sent', False)),
            "created_at": inv.created_at.isoformat() if inv.created_at else None
        } for inv in invites],
        "members": [{
            "id": m.id, "email": m.email, "username": m.username
        } for m in members],
        "member_count": len(members),
        "seat_limit": get_member_seat_limit(current_user),
        "plan": current_user.subscription_tier
    })


@main.route('/admin/invites', methods=['POST'])
@login_required
def admin_invites_post():
    """Invite a team member (admin only). Enforces plan-based seat limits."""
    if current_user.role != 'admin':
        return jsonify({"error": "Admin access required"}), 403

    from app.models import TeamInvite, Workspace, User as UserModel
    import secrets

    data = request.json or {}
    email = (data.get('email') or '').strip().lower()
    role = (data.get('role') or 'member').strip().lower()
    if role not in ('member', 'admin'):
        role = 'member'
    if not email or '@' not in email:
        return jsonify({"error": "A valid email is required"}), 400

    workspace_id = current_user.workspace_id
    if not workspace_id:
        workspace = Workspace.query.filter_by(owner_id=current_user.id).first()
        if not workspace:
            # Create a workspace for this admin
            workspace = Workspace(name=f"{current_user.username or 'My'}'s Workspace", owner_id=current_user.id)
            db.session.add(workspace)
            db.session.flush()
        current_user.workspace_id = workspace.id
        db.session.commit()
        workspace_id = workspace.id

    # Enforce seat limit (role=admin invites still consume one seat)
    member_count = UserModel.query.filter_by(workspace_id=workspace_id, role='member').count()
    pending_invites = TeamInvite.query.filter_by(workspace_id=workspace_id, status='pending').count()
    seat_limit = get_member_seat_limit(current_user)

    # Enterprise = unlimited seats
    if seat_limit is not None and member_count + pending_invites >= seat_limit:
        return jsonify({
            "error": f"Seat limit reached. Your {current_user.subscription_tier} plan allows {seat_limit} member(s). Upgrade to Pro for more seats.",
            "upgrade_required": True
        }), 402

    # Don't invite users who are already members
    if UserModel.query.filter_by(email=email, workspace_id=workspace_id).first():
        return jsonify({"error": "This user is already a member of your workspace."}), 400

    # Don't duplicate pending invites
    if TeamInvite.query.filter_by(workspace_id=workspace_id, email=email, status='pending').first():
        return jsonify({"error": "An invite for this email is already pending."}), 400

    token = secrets.token_urlsafe(32)
    invite = TeamInvite(
        workspace_id=workspace_id,
        email=email,
        token=token,
        role=role,
    )
    db.session.add(invite)
    db.session.flush()

    # Trigger branded invitation email from support@dataconvo.app
    from app.services.email_service import send_invite_email
    workspace = Workspace.query.get(workspace_id)
    ws_name = workspace.name if workspace else 'the workspace'
    site = (os.getenv('SITE_URL') or '').strip().rstrip('/')
    if site:
        invite_url = f'{site}/invite/{token}'
    else:
        invite_url = url_for('main.invite_accept', token=token, _external=True)
    email_ok, email_msg = send_invite_email(
        email,
        invite_url,
        ws_name,
        inviter_name=current_user.username or 'Data Convo Admin',
    )
    invite.email_sent = email_ok
    db.session.commit()

    logger.info(f"Admin {current_user.id} invited {email} (role={role}) to workspace {workspace_id}")
    return jsonify({
        "success": True,
        "invite_id": invite.id,
        "invite_url": invite_url,
        "token": token,
        "role": role,
        "email_sent": email_ok,
        "email_note": email_msg,
        "seat_usage": member_count + pending_invites + 1,
        "seat_limit": seat_limit
    })


@main.route('/api/workspace/invites/<int:invite_id>/revoke', methods=['POST'])
@login_required
def revoke_invite(invite_id):
    """Revoke a pending team invite (admin only)."""
    if current_user.role != 'admin':
        return jsonify({"error": "Admin access required"}), 403
    from app.models import TeamInvite
    invite = TeamInvite.query.get(invite_id)
    if not invite or invite.workspace_id != current_user.workspace_id:
        return jsonify({"error": "Invite not found."}), 404
    if invite.status != 'pending':
        return jsonify({"error": "Only pending invites can be revoked."}), 400
    invite.status = 'revoked'
    db.session.commit()
    logger.info('Admin %s revoked invite %s for %s', current_user.id, invite_id, invite.email)
    return jsonify({"success": True, "invite_id": invite_id})


@main.route('/workspace/members/<int:user_id>/remove', methods=['POST'])
@login_required
def remove_workspace_member(user_id):
    """Admin-only removal of a member from the current workspace.

    Security: login, admin role, same-workspace, no self/owner removal.
    Action: unlink member (workspace_id=None, role reset) + scrub
    their table_permission rows, allowing a clean future re-invite.
    """
    if current_user.role != 'admin':
        return jsonify({"error": "Admin access required."}), 403

    from app.models import User, TablePermission, Workspace
    workspace_id = current_user.workspace_id
    if not workspace_id:
        return jsonify({"error": "No workspace found."}), 400

    workspace = Workspace.query.get(workspace_id)
    if not workspace:
        return jsonify({"error": "Workspace not found."}), 404

    target = User.query.get(user_id)
    if not target:
        return jsonify({"error": "User not found."}), 404

    if target.workspace_id != workspace_id:
        return jsonify({"error": "User is not a member of this workspace."}), 403

    if target.id == current_user.id:
        return jsonify({"error": "You cannot remove yourself from the workspace."}), 400

    if workspace.owner_id == target.id:
        return jsonify({"error": "The workspace owner cannot be removed."}), 400

    try:
        TablePermission.query.filter_by(user_id=target.id).delete()
        target.workspace_id = None
        target.role = 'member'
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error('Member removal failed for user %s: %s', user_id, e)
        return jsonify({"error": "Failed to remove member. Please try again."}), 500

    logger.info('Admin %s removed member %s from workspace %s', current_user.id, user_id, workspace_id)
    return jsonify({"success": True, "removed_user_id": user_id})


@main.route('/invite/<token>')
def invite_accept(token):
    """Public invite landing page — user can view and accept the invite."""
    from app.models import TeamInvite, Workspace
    invite = TeamInvite.query.filter_by(token=token, status='pending').first()
    if not invite:
        return "This invite is invalid or has expired.", 404
    workspace = Workspace.query.get(invite.workspace_id)
    return render_template(
        'invite.html',
        invite=invite,
        workspace_name=workspace.name if workspace else 'the workspace',
        already_logged_in=current_user.is_authenticated
    )


@main.route('/auth/accept-invite')
def accept_invite_redirect():
    """Verify token validity and redirect to the invite landing page.

    The invite landing page (/invite/<token>) locks the invite email (it
    cannot be changed) and after accepting, new members are routed to the
    MFA setup page. This keeps the acceptance flow unified and secure.
    """
    from app.models import TeamInvite
    from datetime import datetime as _dt
    token = request.args.get('token', '').strip()
    if not token:
        return 'Missing invitation token.', 400
    invite = TeamInvite.query.filter_by(token=token, status='pending').first()
    if not invite:
        return 'This invitation is invalid or has already been used.', 404
    if invite.expires_at and invite.expires_at < _dt.utcnow():
        return 'This invitation has expired.', 410
    # Redirect to the invite landing page (locked email + MFA-on-join flow)
    return redirect(url_for('main.invite_accept', token=token))


@main.route('/invite/<token>/accept', methods=['POST'])
def invite_accept_post(token):
    """Accept an invite. Creates a new user as 'member' if needed, or links existing user to workspace."""
    from app.models import TeamInvite, Workspace, User as UserModel

    invite = TeamInvite.query.filter_by(token=token, status='pending').first()
    if not invite:
        return jsonify({"error": "This invite is invalid or has expired."}), 404

    # If not logged in, create or sign in the user
    if not current_user.is_authenticated:
        email = request.form.get('email', invite.email).strip().lower()
        password = request.form.get('password', '')

        # STRICT: the invite email is immutable and locked to the invite.
        # A submitted email that does not match the invite cannot proceed.
        if email != invite.email:
            return "This invitation is linked to a specific email address and cannot be changed.", 403

        user = UserModel.query.filter_by(email=email).first()
        is_new_user = False
        if not user:
            if not password or len(password) < 6:
                return "Password must be at least 6 characters to create an account.", 400
            user = UserModel(email=email, username=email.split('@')[0])
            user.set_password(password)
            db.session.add(user)
            db.session.flush()
            is_new_user = True
        else:
            if password and not user.check_password(password):
                return "Invalid password for this email.", 401
    else:
        user = current_user
        is_new_user = False

    # Assign to the workspace as a member
    user.workspace_id = invite.workspace_id
    user.role = 'member'
    invite.status = 'accepted'
    db.session.commit()

    from flask_login import login_user
    login_user(user)

    # First-time members: require MFA enrollment immediately after accepting.
    # Existing users without MFA also get routed to the MFA setup page.
    if not getattr(user, 'mfa_verified', False) or not getattr(user, 'totp_secret', None):
        return redirect(url_for('auth.setup_mfa_page'))

    return redirect(url_for('main.chat_interface'))

# ===== RBAC Admin endpoints =====

@main.route('/admin/member-tables', methods=['GET'])
@login_required
def admin_member_tables_get():
    """Return the tables (and optional column whitelists) for the member role.

    Supports a per-member workflow: pass ?user_id=<id> to load the targeted
    member's personal overrides (falls back to workspace-wide defaults).
    Also returns the workspace's member list so the UI can populate a
    member-selection dropdown.
    """
    if not is_admin():
        return jsonify({"error": "Admin access required"}), 403

    profile = session.get('db_profile')
    if not profile or not profile.get('tables'):
        return jsonify({"tables": [], "allowed_tables": [], "members": [],
                        "error": "No DB profile"}), 200

    from app.models import User as UserModel
    ws_id = current_user.workspace_id
    # The member this view is editing (None = workspace defaults for all members)
    target_user_id = request.args.get('user_id', type=int)
    # The workspace's actual members (for the dropdown)
    members = []
    if ws_id:
        members = UserModel.query.filter_by(workspace_id=ws_id, role='member').all()

    all_tables = _get_all_tables()
    allowed = get_member_allowed_tables(workspace_id=ws_id, user_id=target_user_id)
    col_map = get_member_column_permissions(workspace_id=ws_id, user_id=target_user_id)
    all_columns = {}
    if profile.get('tables'):
        for tname in all_tables:
            tdata = profile['tables'].get(tname)
            if tdata and tdata.get('columns'):
                all_columns[tname] = list(tdata['columns'].keys())
    # Resolve the workspace's real name so the UI dropdown shows
    # "<WorkspaceName> default" instead of a generic label.
    workspace_name = ''
    if ws_id:
        from app.models import Workspace as WorkspaceModel
        w = WorkspaceModel.query.get(ws_id)
        if w:
            workspace_name = w.name or ''

    return jsonify({
        "all_tables": all_tables,
        "allowed_tables": allowed,
        "column_permissions": col_map,
        "all_tables_columns": all_columns,
        "members": [{"id": m.id, "email": m.email, "username": m.username} for m in members],
        "selected_user_id": target_user_id,
        "user_role": current_user.role,
        "scope": "workspace" if target_user_id is None else "member",
        "workspace_name": workspace_name or getattr(current_user, 'workspace_name', None) or '',
    })


@main.route('/admin/member-tables', methods=['POST'])
@login_required
def admin_member_tables_post():
    """Save the member table (and column) permissions (admin only).

    Payload shape:
      {
        "workspace_id": <int>,            # optional; defaults to admin's workspace
        "user_id": <int | null>,          # null / absent = workspace-wide default
        "table_permissions": [...],       # list of allowed table names
        "column_permissions": {...}       # {table: [allowed columns]}
      }
    """
    if not is_admin():
        return jsonify({"error": "Admin access required"}), 403

    data = request.json or {}
    ws_id = data.get('workspace_id') or current_user.workspace_id
    target_user_id = data.get('user_id')

    # Backward-compatible: accept either `allowed_tables` or `table_permissions`.
    allowed_tables = data.get('table_permissions') or data.get('allowed_tables') or []
    # Only accept tables that actually exist in the DB profile.
    profile = session.get('db_profile') or {}
    valid = set(profile.get('tables', {}).keys())
    allowed = [t for t in allowed_tables if t in valid]

    # Granular column whitelists: {"table": ["col_a", "col_b"]}.
    # Only keep columns that exist in the DB profile for allowed tables.
    column_permissions = None
    raw_cols = data.get('column_permissions')
    if isinstance(raw_cols, dict):
        column_permissions = {}
        for tname, cols in raw_cols.items():
            if tname not in allowed:
                continue
            tdata = profile.get('tables', {}).get(tname)
            if not tdata or not isinstance(cols, list):
                continue
            valid_cols = set(tdata.get('columns', {}).keys())
            column_permissions[tname] = [c for c in cols if c in valid_cols]

    # Per-member save when user_id is provided; otherwise workspace-wide default.
    save_member_allowed_tables(
        allowed,
        user_id=target_user_id,
        workspace_id=ws_id,
        column_permissions=column_permissions,
    )

    # RBAC INSTANT-REFLECT: clear the SQL cache so no member can reuse a cached
    # query that references tables/columns they are no longer allowed to see.
    # The very next /generate-sql call re-filters the semantic layer from the
    # live TablePermission rows, so permission changes apply immediately.
    cache_manager.clear_cache()
    session.pop('chat_history', None)
    scope = "member" if target_user_id else "workspace"
    logger.info(
        "Admin %s published member%s allowed tables (scope=%s): %s",
        current_user.id, f" user={target_user_id}" if target_user_id else "",
        scope, allowed,
    )
    return jsonify({
        "success": True,
        "allowed_tables": allowed,
        "column_permissions": column_permissions or {},
        "scope": scope,
        "user_id": target_user_id,
    })

@main.route('/get-available-tables', methods=['GET'])
@login_required
def get_available_tables():
    """Return list of available tables from the semantic layer"""
    # Resolve the workspace's published model (works for admins AND members —
    # members inherit the workspace owner's semantic layer scoped by workspace_id).
    published_model, _ = _load_workspace_semantic_context()
    if not published_model:
        return jsonify({"tables": [], "error": "No semantic layer found"})
    
    # Members: only show tables they are ALLOWED to see (RBAC).
    if not is_admin():
        from flask_login import current_user as cu
        published_model = get_effective_tables(published_model, cu)

    tables = []
    if isinstance(published_model, dict) and 'tables' in published_model:
        for table_name in published_model['tables'].keys():
            if table_name.lower() not in {'user', 'users', 'sessions', 'alembic_version', 'sqlite_sequence'}:
                tables.append(table_name)
    return jsonify({"tables": tables})

def _get_all_tables():
    """Return all tables from the DB profile (including inactive/system-ignored)."""
    profile = session.get('db_profile')
    if not profile or not profile.get('tables'):
        return []
    # Exclude only internal system tables
    internal = {'user', 'users', 'sessions', 'alembic_version', 'sqlite_sequence'}
    return [t for t in profile['tables'].keys() if t.lower() not in internal]


@main.route('/api/saved-queries', methods=['GET'])
@login_required
def get_saved_queries():
    """Return all saved queries for the current user."""
    queries = SavedQuery.query.filter_by(user_id=current_user.id).order_by(SavedQuery.is_favorite.desc(), SavedQuery.created_at.desc()).all()
    return jsonify([{
        "id": q.id,
        "title": q.title,
        "sql_query": q.sql_query,
        "is_favorite": q.is_favorite,
        "created_at": q.created_at.isoformat() if q.created_at else None
    } for q in queries])


@main.route('/api/saved-queries', methods=['POST'])
@login_required
def save_query():
    """Save a new query for the current user."""
    data = request.json
    title = data.get('title', 'Untitled Query')
    sql_query = data.get('sql_query', '')
    if not sql_query.strip():
        return jsonify({"error": "SQL query is required"}), 400
    q = SavedQuery(title=title, sql_query=sql_query, user_id=current_user.id)
    db.session.add(q)
    db.session.commit()
    return jsonify({
        "id": q.id,
        "title": q.title,
        "sql_query": q.sql_query,
        "is_favorite": q.is_favorite,
        "created_at": q.created_at.isoformat() if q.created_at else None
    }), 201


@main.route('/api/saved-queries/<int:query_id>/favorite', methods=['POST'])
@login_required
def toggle_favorite(query_id):
    """Toggle the is_favorite status of a saved query (owner only)."""
    q = SavedQuery.query.filter_by(id=query_id, user_id=current_user.id).first()
    if not q:
        return jsonify({"error": "Query not found"}), 404
    q.is_favorite = not q.is_favorite
    db.session.commit()
    return jsonify({"id": q.id, "is_favorite": q.is_favorite})


@main.route('/api/saved-queries/<int:query_id>', methods=['DELETE'])
@login_required
def delete_saved_query(query_id):
    """Delete a saved query (owner only)."""
    q = SavedQuery.query.filter_by(id=query_id, user_id=current_user.id).first()
    if not q:
        return jsonify({"error": "Query not found"}), 404
    db.session.delete(q)
    db.session.commit()
    return jsonify({"success": True})


def _byok_blocked_response():
    """Return a friendly JSON response when BYOK is required but not configured."""
    return jsonify({
        "error": None,
        "type": "chat_message",
        "response": (
            "⚠️ **BYOK Required** — This self-hosted deployment has no platform API key fallback. "
            "Connect your own OpenAI / Anthropic / OpenRouter key in **Account → Security → Bring Your Own Key** "
            "before running queries."
        ),
    }), 200


@main.route('/api/chat/deepbot', methods=['POST'])
@login_required
def deepbot_route():
    """DeepBot — interactive data-science agent for the AI Chat terminal.

    Takes a natural-language prompt, the active connection, and the last
    dataset preview rows. Generates Python code via LLM, runs it in a
    restricted sandbox (pd/np/sklearn/matplotlib), and returns markdown
    explanation + code + base64 matplotlib image.
    """
    # BYOK-ONLY enforcement: block before any LLM call if no workspace key.
    from app.agents.llm_router import get_byok_state, byok_required
    if byok_required(current_user):
        _be, _bp, _bo, _ba = get_byok_state(current_user)
        if not (_be and (_bo or _ba)):
            return _byok_blocked_response()

    import sys
    data = request.get_json(silent=True) or {}
    prompt = (data.get('prompt') or data.get('question') or '').strip()
    if not prompt:
        return jsonify({"error": "prompt is required."}), 400

    try:
        from app.services.deepbot_service import run_deepbot

        # Source dataset rows: from the session's last query result (or preview rows supplied)
        rows = data.get('rows') or []
        if not rows:
            # Fall back to the last data response stored in session
            rows = session.get('last_chart_data') or []

        # Fallback: execute a simple SELECT to build a preview if we have a connection
        if not rows:
            conn_str = session.get('connection_string')
            if conn_str:
                try:
                    service = SQLChainService(conn_str, user=current_user)

                    # Use the last known SQL if available, otherwise list active tables
                    last_sql = session.get('last_query_sql')
                    tables = [t for t in (session.get('published_semantic_layer', {}).get('tables', {}).keys())]
                    if last_sql:
                        raw = QueryExecutor.validate_and_execute(service.db, last_sql)
                        if isinstance(raw, list):
                            rows = raw  # full dataset
                    elif tables:
                        import urllib.parse as _up
                        q = _up.quote(tables[0])
                        raw = QueryExecutor.validate_and_execute(service.db,
                            f'SELECT * FROM "{tables[0]}" LIMIT 100')
                        if isinstance(raw, list):
                            rows = raw  # full dataset for DeepBot
                except Exception as e:
                    logger.warning(f'DeepBot fallback query failed: {e}')

        # Store the rows in session so the terminal can offer follow-ups
        session['last_chart_data'] = rows  # full dataset for DeepBot

        result = run_deepbot(prompt, rows, user=current_user)

        return jsonify({
            "explanation": result.get("explanation", ""),
            "code": result.get("code", ""),
            "output": result.get("output", ""),
            "html": result.get("html"),
            "image": result.get("image"),
            "error": result.get("error"),
            "data": result.get("data"),
            "persona": "deepbot",
        })
    except Exception as e:
        logger.error(f"DeepBot route error: {e}")
        return jsonify({"error": str(e)}), 500




@main.route('/api/chat/internetbot', methods=['POST'])
@login_required
def internetbot_route():
    """InternetBot — multi-agent orchestrator (QueryBot + DeepBot + web search).

    InternetBot does NOT query the database directly. It delegates:
      - ask_querybot(query)  -> internal SQL metrics
      - ask_deepbot(query)   -> statistical analysis / trends
      - web_search(query)    -> external market context
    Then synthesizes a final answer from both internal + external data.
    """
    # BYOK-ONLY enforcement: block before any LLM call if no workspace key.
    from app.agents.llm_router import get_byok_state, byok_required
    if byok_required(current_user):
        _be, _bp, _bo, _ba = get_byok_state(current_user)
        if not (_be and (_bo or _ba)):
            return _byok_blocked_response()

    data = request.get_json(silent=True) or {}
    prompt = (data.get('prompt') or data.get('question') or '').strip()
    if not prompt:
        return jsonify({"error": "prompt is required."}), 400

    try:
        from app.services.internetbot_service import (
            run_internetbot_agentic, web_search,
            _TOOL_ASK_QUERYBOT, _TOOL_ASK_DEEPBOT, _TOOL_WEB_SEARCH,
        )

        # Source dataset rows from session (same as DeepBot)
        rows = data.get('rows') or []
        if not rows:
            rows = session.get('last_chart_data') or []

        # Fallback: run a simple SELECT if we have a connection
        if not rows:
            conn_str = session.get('connection_string')
            if conn_str:
                try:
                    service_ib = SQLChainService(conn_str, user=current_user)
                    last_sql = session.get('last_query_sql')
                    tables = list((session.get('published_semantic_layer', {}).get('tables', {}) or {}).keys())
                    if last_sql:
                        raw_ib = QueryExecutor.validate_and_execute(service_ib.db, last_sql)
                        if isinstance(raw_ib, list):
                            rows = raw_ib  # full dataset
                    elif tables:
                        raw_ib = QueryExecutor.validate_and_execute(
                            service_ib.db, f'SELECT * FROM "{tables[0]}" LIMIT 100')
                        if isinstance(raw_ib, list):
                            rows = raw_ib  # full dataset for DeepBot
                except Exception as e:
                    logger.warning(f'InternetBot fallback query failed: {e}')

        # Store rows so follow-ups can reuse them
        session['last_chart_data'] = rows  # full dataset for DeepBot

        # ---- Agentic loop: invoke LLM, execute tools, feed results back ----
        def _tool_executor(tool_name, tool_query, src_rows):
            """Resolve a delegated tool call with real DB / DeepBot / web access."""
            try:
                if tool_name == _TOOL_ASK_QUERYBOT or tool_name == 'ask_querybot':
                    service_qb = SQLChainService(session['connection_string'], user=current_user)
                    pub_model = session.get('published_semantic_layer')
                    gen = service_qb.generate_sql(tool_query, pub_model, conversation_history=[])
                    if gen.get('type') == 'sql' and gen.get('sql'):
                        raw_qb, _g = QueryExecutor.validate_and_execute_tiered(
                            service_qb.db, gen['sql'], db_type=service_qb.dialect)
                        out_rows = []
                        if isinstance(raw_qb, list):
                            for r in raw_qb:  # full dataset
                                if isinstance(r, (tuple, list)):
                                    out_rows.append({f"col_{i}": v for i, v in enumerate(r)})
                                elif isinstance(r, dict):
                                    out_rows.append(r)
                                else:
                                    out_rows.append({"result": r})
                        return {"rows": out_rows, "sql": gen['sql'], "data": out_rows}
                    return {"error": gen.get('response', 'QueryBot could not generate SQL')}
                if tool_name == _TOOL_ASK_DEEPBOT or tool_name == 'ask_deepbot':
                    from app.services.deepbot_service import run_deepbot
                    db_res = run_deepbot(tool_query, src_rows, user=current_user)
                    return {
                        "explanation": db_res.get('explanation', ''),
                        "data": db_res.get('data'),
                        "error": db_res.get('error'),
                    }
                if tool_name == _TOOL_WEB_SEARCH or tool_name == 'web_search':
                    return {"sources": web_search(tool_query or prompt)}
            except Exception as exc:
                return {"error": str(exc)}
            return {"error": f"Unknown tool: {tool_name}"}

        # ---- SSE STREAMING (default off; enabled when `stream=true`) ----
        # When streaming, we emit `event: thought/tool_start/tool_end/synthesis`
        # packets in real time so the client can render a live "Agent Thought
        # Stream" while the orchestrator runs. The final payload is the last
        # `event: done` packet.
        if data.get('stream'):
            def _step_callback(event_type, payload):
                # Called from within run_internetbot_agentic on every phase.
                # We write directly to the streaming generator state.
                sse_events.append({
                    'type': event_type,
                    **payload,
                })

            sse_events = []

            def _generate():
                result = run_internetbot_agentic(
                    prompt,
                    rows,
                    tool_executor=_tool_executor,
                    max_iterations=8,
                    user=current_user,
                    step_callback=_step_callback,
                )
                # Stream thought/tool events first
                for ev in sse_events:
                    yield f"event: {ev.get('type', 'thought')}\n"
                    yield f"data: {json.dumps(ev)}\n\n"
                # Final done event with full result payload
                final = {
                    "explanation": result.get('explanation', ''),
                    "sources": result.get('sources', []),
                    "tool_calls": result.get('tool_calls', []),
                    "tool_outputs": [],
                    "data": result.get('data'),
                    "error": result.get('error'),
                    "persona": "internetbot",
                }
                yield f"event: done\n"
                yield f"data: {json.dumps(final)}\n\n"

            return Response(
                stream_with_context(_generate()),
                mimetype='text/event-stream',
                headers={
                    'Cache-Control': 'no-cache',
                    'X-Accel-Buffering': 'no',
                },
            )

        # ---- Non-streaming path (legacy / simpler environments) ----
        result = run_internetbot_agentic(
            prompt,
            rows,
            tool_executor=_tool_executor,
            max_iterations=8,
            user=current_user,
        )

        return jsonify({
            "explanation": result.get('explanation', ''),
            "sources": result.get('sources', []),
            "tool_calls": result.get('tool_calls', []),
            "tool_outputs": [],
            "data": result.get('data'),
            "error": result.get('error'),
            "persona": "internetbot",
        })
    except Exception as e:
        logger.error(f"InternetBot route error: {e}")
        return jsonify({"error": str(e)}), 500


@main.route('/api/settings/active-table', methods=['POST'])
@login_required
def set_active_table():
    """Persist the user's currently-selected table in session state.

    Called when a user clicks a table bubble in the UI. Subsequent short
    commands (e.g. 'show me all rows') will default to this table via the
    context-prepending logic in /generate-sql.
    """
    data = request.get_json(silent=True) or {}
    table_name = (data.get('table') or '').strip()
    if not table_name:
        return jsonify({"error": "table name is required."}), 400
    session['active_table'] = table_name
    logger.info('Active table set for user %s: %s', current_user.id, table_name)
    return jsonify({"success": True, "active_table": table_name})


@main.route('/generate-sql', methods=['POST'])
@login_required
def generate_sql():
    import sys
    print(f"DEBUG: /generate-sql hit. Data: {request.json}")
    sys.stdout.flush()
    try:
        data = request.json
        question = data.get('question')
        persona = (data.get('persona') or 'querybot').lower()
        sql_override = data.get('sql_override')
        # Load published semantic layer FIRST - required by the active-table
        # context prepending below (fixes UnboundLocalError).
        # Uses the workspace-scoped resolver so MEMBERS inherit the workspace
        # owner's semantic layer + connection string (strict tenant isolation).
        published_model, _conn_resolved = _load_workspace_semantic_context()

        # ---- ACTIVE-TABLE CONTEXT PREPENDING ----
        # If the user selected a table earlier (session['active_table']) and
        # this follow-up message does NOT explicitly name any table from the
        # published semantic layer, prepend a context directive so the LLM
        # never has to ask "which table?" again.
        active_table = session.get('active_table')
        if active_table and question:
            published_tables = (published_model or {}).get('tables', {}) or {}
            q_lower = question.lower()
            mentions_table = any(str(t).lower() in q_lower for t in published_tables.keys())
            if not mentions_table:
                question = f"[Context: Active Table is {active_table}] {question}"
                logger.info('Prepended active-table context: %s', active_table)
        
        print(f"DEBUG: question: {question}")
        sys.stdout.flush()
        print(f"DEBUG: sql_override: {sql_override}")
        sys.stdout.flush()
        print(f"DEBUG: published_model: {published_model}")
        sys.stdout.flush()

        if not published_model:
            return jsonify({"error": "No published semantic layer found. Please configure connection and tables first."}), 400

        # ===== RBAC GUARD: filter tables based on user role =====
        # Admins: full access. Members: only tables in their allowed list.
        if is_admin():
            effective_model = published_model  # Admin bypass — full access
        else:
            effective_model = get_effective_tables(published_model, current_user)
            if not effective_model.get('tables'):
                return jsonify({
                    "type": "chat_message",
                    "response": "You don't have access to any tables. Please contact your administrator to enable table access for your role.",
                    "message": "You don't have access to any tables. Please contact your administrator to enable table access for your role.",
                    "generated_sql": None, "data": None, "error": None
                }), 200

        # ===== SECURITY INTERCEPTOR: block member queries on restricted tables =====
        if not is_admin():
            disallowed = get_disallowed_tables(published_model, current_user)
            q_lower_rbac = (question or '').lower()
            for t in disallowed:
                if t.lower() in q_lower_rbac:
                    return jsonify({
                        "type": "chat_message",
                        "response": f"Access denied: The table `{t}` is restricted for your role. Please contact your administrator if you need access.",
                        "message": f"Access denied: The table `{t}` is restricted for your role. Please contact your administrator if you need access.",
                        "generated_sql": None, "data": None, "error": None,
                        "restricted_table": t
                    }), 200

        # Keep the ORIGINAL model for the column-level guard below (it must
        # know the full column set to detect restricted column references).
        original_published_model = published_model
        # Replace the model used downstream with the role-filtered one
        published_model = effective_model

        # ---- SQL OVERRIDE FAST-PATH: execute saved/edited SQL directly, no LLM ----
        if sql_override and sql_override.strip():
            # Store context for DeepBot follow-ups
            session['last_query_sql'] = sql_override.strip()
            try:
                from app import db as _db
            except Exception:
                pass
            print(f"DEBUG: SQL override detected, executing directly: {sql_override!r}", flush=True)
            service = SQLChainService(session['connection_string'], user=current_user)
            raw_results, override_guardrail = QueryExecutor.validate_and_execute_tiered(
                service.db, sql_override, db_type=service.dialect
            )
            print(f"DEBUG: SQL override executed, returned {len(raw_results) if isinstance(raw_results, list) else 'N/A'} rows", flush=True)
            
            formatted_data = []
            if isinstance(raw_results, str):
                formatted_data = [{"result": raw_results}]
            elif isinstance(raw_results, list):
                for row in raw_results:
                    if isinstance(row, (tuple, list)):
                        formatted_data.append({f"col_{i}": val for i, val in enumerate(row)})
                    elif isinstance(row, dict):
                        formatted_data.append(row)
                    else:
                        formatted_data.append({"result": row})
            
            if isinstance(formatted_data, list) and formatted_data:
                session['last_chart_data'] = formatted_data  # full dataset for DeepBot

            # ----- Dynamic result tiering (medium/large) -----
            tier, tier_info = classify_result_size(
                len(formatted_data),
                limit_applied=bool(override_guardrail.get('limit_applied'))
            )
            pivot_suggestions = []
            if tier == 'large':
                first_table = next(iter((published_model or {}).get('tables', {})), None)
                pivot_suggestions = build_pivot_suggestions(first_table, question or 'Saved query')

            return jsonify({
                "type": "data_response",
                "query": question or "Saved query",
                "generated_sql": sql_override,
                "data": formatted_data,
                "related_questions": [],
                "error": None,
                "guardrail": override_guardrail,
                "result_tier": tier,
                "tier_info": tier_info,
                "pivot_suggestions": pivot_suggestions,
            })

        # ---- TIER-1: Dynamic Intent Classification (feature-flagged, OFF by default) ----
        # Streamlined single-pass mode: the main LLM handles schema mapping and SQL
        # generation natively in one turn (no pre-flight classifier latency).
        # Set ENABLE_QUERY_CLASSIFIER=true in .env to re-enable this legacy path.
        if current_app.config.get('ENABLE_QUERY_CLASSIFIER', False):
            # ---- TIER-1: Dynamic Intent Classification (OpenRouter, free) ----
            try:
                from app.services.intent_classifier import IntentClassifier
                active_tables = list(published_model.get("tables", {}).keys())
                all_tables = _get_all_tables()
                classifier = IntentClassifier()
                # Build column map for classifier: table -> [column names]
                active_columns = {}
                for _t, t_data in (published_model.get('tables', {}) or {}).items():
                    active_columns[_t] = list((t_data or {}).get('columns', {}).keys())
                intent_result = classifier.classify(question, active_tables, all_tables, active_columns=active_columns)
                intent = intent_result.get("intent", "DATA_QUERY")
                target_table = intent_result.get("target_table")

                # ----- GRACEFUL FALLBACK: no table named -> never DISABLED_TABLE -----
                # If the classifier returned DISABLED_TABLE without a real active target
                # (e.g. no table was mentioned at all, or it guessed from a column/trailing word),
                # downgrade to DATA_QUERY so the request proceeds normally.
                if intent == "DISABLED_TABLE":
                    active_name_set = {str(t).lower() for t in active_tables}
                    has_valid_target = target_table and str(target_table).lower() in active_name_set
                    if not has_valid_target:
                        intent = "DATA_QUERY"
                        target_table = None

                if intent == "GREETING":
                    return jsonify({
                        "type": "chat_message",
                        "response": "Hello! I'm ready to help you analyze your database. What metrics or tables would you like to inspect today?",
                        "message": "Hello! I'm ready to help you analyze your database. What metrics or tables would you like to inspect today?",
                        "generated_sql": None, "data": None, "execution_time_ms": 0, "error": None
                    }), 200

                if intent == "OFF_TOPIC":
                    return jsonify({
                        "type": "chat_message",
                        "response": "I can only answer questions about your connected database. Try asking about your tables, e.g. 'What are the total sales?'.",
                        "message": "I can only answer questions about your connected database. Try asking about your tables, e.g. 'What are the total sales?'.",
                        "generated_sql": None, "data": None, "execution_time_ms": 0, "error": None
                    }), 200

                if intent == "DISABLED_TABLE" and target_table:
                    # ----- Post-classification COLUMN GUARD -----
                    # If the flagged identifier is actually a COLUMN inside one of the
                    # active/published tables, do NOT block it — fall through to DATA_QUERY.
                    is_actually_column = False
                    col_lower = str(target_table).lower()
                    if isinstance(published_model, dict):
                        tables = published_model.get('tables', {}) or {}
                        for _t, t_data in tables.items():
                            cols = (t_data or {}).get('columns', {}) or {}
                            for col_name in cols.keys():
                                if str(col_name).lower() == col_lower:
                                    is_actually_column = True
                                    break
                            if is_actually_column:
                                break

                    if not is_actually_column:
                        return jsonify({
                            "type": "chat_message",
                            "response": f"The table `{target_table}` is not currently active in your semantic layer. Please activate it in Semantic Studio or select from available tables below.",
                            "message": f"The table `{target_table}` is not currently active in your semantic layer. Please activate it in Semantic Studio or select from available tables below.",
                            "generated_sql": None, "data": None, "execution_time_ms": 0, "error": None,
                            "inactive_table": target_table
                        }), 200
            except Exception as e:
                logger.warning(f"IntentClassifier skipped (fallback to legacy routing): {e}")

        # ---- LOCAL FAST-PATHS: instant returns, no LLM, no DB ----
        question_clean = (question or "").strip().lower()

        # A. Greetings & small talk fast-path (regex, word-boundary, short messages only)
        greeting_keywords = [r"\bhey\b", r"\bhello\b", r"\bhi\b", r"\bhowdy\b", r"\bwho are you\b", r"\bgood morning\b", r"\bgood afternoon\b", r"\bgood evening\b", r"\bthanks\b", r"\bthank you\b"]
        if any(re.search(pattern, question_clean) for pattern in greeting_keywords) and len(question_clean.split()) <= 4:
            return jsonify({
                "type": "chat_message",
                "response": "Hello! I'm ready to help you analyze your database. What metrics or tables would you like to inspect today?",
                "message": "Hello! I'm ready to help you analyze your database. What metrics or tables would you like to inspect today?",
                "generated_sql": None,
                "data": None,
                "execution_time_ms": 0,
                "error": None
            }), 200

        # B. Schema discovery fast-path: return active tables from session state as a data grid, no LLM
        # Only trigger for exact schema questions, not queries about specific tables
        schema_keywords = ["what tables", "list tables", "available tables", "show tables", "see tables", "tables can you see", "show schema", "tables are in here", "tables do you see"]
        
        # Check if it's a pure schema question (not asking about specific data in tables)
        is_pure_schema_question = any(kw in question_clean for kw in schema_keywords)
        
        # Also check if the question contains table names - if it does, it's not a pure schema question
        contains_table_name = False
        if isinstance(published_model, dict) and 'tables' in published_model:
            table_names = list(published_model['tables'].keys())
            contains_table_name = any(table_name.lower() in question_clean for table_name in table_names)
        
        if is_pure_schema_question and not contains_table_name:
            active_tables = list(published_model.get("tables", {}).keys())
            table_rows = [{"table_name": t} for t in active_tables]
            return jsonify({
                "type": "data_response",
                "query": question,
                "generated_sql": "-- Local schema discovery (no LLM)\nSELECT table_name FROM information_schema.tables;",
                "data": table_rows,
                "related_questions": [
                    "Show me a sample of each table",
                    "What are the total sales?",
                    "Show me top 10 customers"
                ],
                "execution_time_ms": 0,
                "error": None
            }), 200

        # Check local cache first (zero-dependency, skips LLM on hit)
        cached = cache_manager.get_cached_sql(question)
        if cached:
            logger.info(f"Cache hit for prompt: {question[:60]}...")
            _cache_service = SQLChainService(session['connection_string'], user=current_user)
            raw_results, _cache_guardrail = QueryExecutor.validate_and_execute_tiered(
                _cache_service.db,
                cached["generated_sql"],
                db_type=_cache_service.dialect
            )
            formatted_data = []
            if isinstance(raw_results, str):
                formatted_data = [{"result": raw_results}]
            elif isinstance(raw_results, list):
                for row in raw_results:
                    if isinstance(row, (tuple, list)):
                        formatted_data.append({f"col_{i}": val for i, val in enumerate(row)})
                    elif isinstance(row, dict):
                        formatted_data.append(row)
                    else:
                        formatted_data.append({"result": row})
            return jsonify({
                "type": "data_response",
                "query": question,
                "generated_sql": cached["generated_sql"],
                "data": formatted_data,
                "related_questions": cached["related_questions"],
                "error": None
            })

        service = SQLChainService(session['connection_string'], user=current_user)
        
        # Build conversation history with user + assistant SQL turns
        history = session.get('chat_history', [])
        history.append({"role": "user", "content": question})
        
        # ---- PRONOUN RESOLUTION: replace relative pronouns with last_mentioned_table ----
        last_table = session.get('last_mentioned_table')
        if last_table:
            pronoun_patterns = ['this table', 'that table', 'the table', 'this one', 'that one', 'it']
            resolved_question = question
            for pattern in pronoun_patterns:
                if pattern in question.lower():
                    resolved_question = question.replace(pattern, last_table)
                    print(f"DEBUG: Resolved pronoun '{pattern}' -> '{last_table}'", flush=True)
                    break
            question = resolved_question

        # Track the last mentioned table for future pronoun resolution
        if isinstance(published_model, dict) and 'tables' in published_model:
            for t_name in published_model['tables'].keys():
                if t_name.lower() in question.lower():
                    session['last_mentioned_table'] = t_name
                    break

        # ---- PRONOUN RESOLUTION: replace relative pronouns with last_mentioned_table ----
        last_table = session.get('last_mentioned_table')
        if last_table:
            pronoun_patterns = ['this table', 'that table', 'the table', 'this one', 'that one', 'it']
            resolved_question = question
            for pattern in pronoun_patterns:
                if pattern in question.lower():
                    resolved_question = question.replace(pattern, last_table)
                    print(f"DEBUG: Resolved pronoun '{pattern}' -> '{last_table}'", flush=True)
                    break
            question = resolved_question

        # Track the last mentioned table for future pronoun resolution
        if isinstance(published_model, dict) and 'tables' in published_model:
            for t_name in published_model['tables'].keys():
                if t_name.lower() in question.lower():
                    session['last_mentioned_table'] = t_name
                    break

        # DEBUG: Log the exact prompt being sent to the LLM
        print(f"DEBUG: Calling service.generate_sql() with question: {question}", flush=True)
        print(f"DEBUG: Published model tables: {list(published_model.get('tables', {}).keys())}", flush=True)
        
        result = service.generate_sql(question, published_model, conversation_history=history)
        
        # DEBUG: Log the raw result from the LLM (especially the SQL string)
        print(f"DEBUG: service.generate_sql() returned type={result.get('type')}", flush=True)
        if result.get('type') == 'sql':
            print(f"DEBUG: Raw SQL generated by LLM: {result.get('sql')!r}", flush=True)
        elif result.get('type') == 'chat_message':
            print(f"DEBUG: Chat message response: {result.get('response', '')[:100]}", flush=True)
        elif result.get('type') == 'clarification':
            print(f"DEBUG: Clarification response: {result.get('response', '')[:100]}", flush=True)
        else:
            print(f"DEBUG: Unexpected result type: {result}", flush=True)
        
        # FIX: Guardrail for empty SQL - do NOT execute empty queries
        if result.get("type") == "sql" and not result.get("sql", "").strip():
            return jsonify({
                "status": "error",
                "message": "No valid SQL generated. Please check your query or active tables.",
                "error": "No valid SQL generated. Please check your query or active tables.",
                "data": [],
                "generated_sql": ""
            }), 200
        
        # Non-SQL tools return instantly, no DB execution
        if result.get("type") == "chat_message":
            history.append({"role": "assistant", "content": result["response"]})
            session['chat_history'] = history[-10:]
            return jsonify({
                "type": "chat_message",
                "response": result["response"],
                "error": None
            })
        
        if result.get("type") == "clarification":
            history.append({"role": "assistant", "content": result["response"]})
            session['chat_history'] = history[-10:]
            return jsonify({
                "type": "clarification",
                "response": result["response"],
                "suggested_tables": result.get("suggested_tables", []),
                "error": None
            })
        
        # SQL tool: execute against database with SELF-HEALING AGENTIC FEEDBACK LOOP
        sql = result["sql"]
        related_questions = result.get("related_questions", [])
        max_retries = 3  # Max self-correction attempts before giving up

        raw_results = None
        last_error = None
        retry_count = 0
        execution_guardrail = None

        # ---- COLUMN-LEVEL RBAC GUARD: reject restricted column references BEFORE execution ----
        if not is_admin():
            try:
                validate_query_columns(sql, get_disallowed_columns(original_published_model, current_user))
            except ValueError as col_err:
                history.append({"role": "assistant", "content": str(col_err)})
                session['chat_history'] = history[-10:]
                return jsonify({
                    "type": "chat_message",
                    "response": str(col_err),
                    "message": str(col_err),
                    "generated_sql": sql,
                    "data": None,
                    "error": None,
                    "restricted_column": True,
                }), 200

        for attempt in range(max_retries + 1):  # Original + up to 3 retries
            try:
                print(f"DEBUG: Executing SQL via QueryExecutor (attempt {attempt + 1}): {sql!r}", flush=True)
                print(f"DEBUG: SQL dialect: {service.dialect}", flush=True)
                raw_results, execution_guardrail = QueryExecutor.validate_and_execute_tiered(
                    service.db, sql, db_type=service.dialect
                )
                print(f"DEBUG: QueryExecutor returned {len(raw_results) if isinstance(raw_results, list) else 'N/A'} rows", flush=True)
                # Success: break out of the retry loop
                break
            except Exception as e:
                last_error = str(e)
                logger.warning(f"SQL execution failed (attempt {attempt + 1}/{max_retries + 1}): {e}")

                # If we've exhausted retries, break
                if attempt >= max_retries:
                    break

                print(f"DEBUG: Self-healing attempt {attempt + 1}: feeding error back to LLM", flush=True)
                print(f"DEBUG: Error message: {last_error}", flush=True)

                try:
                    # Feed the error back to the LLM for self-correction
                    corrected = service.correct_sql(
                        question,
                        published_model,
                        failed_sql=sql,
                        error_message=last_error,
                        conversation_history=history,
                        retry_count=retry_count
                    )

                    if corrected.get("type") == "sql":
                        sql = corrected["sql"]
                        retry_count = corrected.get("retry_count", retry_count + 1)
                        if corrected.get("related_questions"):
                            related_questions = corrected["related_questions"]
                        print(f"DEBUG: Self-healing corrected SQL: {sql!r}", flush=True)
                        continue  # Retry with the corrected SQL
                    elif corrected.get("type") == "clarification":
                        # LLM asked for clarification during self-heal - return it
                        history.append({"role": "assistant", "content": corrected["response"]})
                        session['chat_history'] = history[-10:]
                        return jsonify({
                            "type": "clarification",
                            "response": corrected["response"],
                            "suggested_tables": corrected.get("suggested_tables", []),
                            "error": None
                        })
                    elif corrected.get("type") == "chat_message":
                        # LLM gave up - return the message
                        history.append({"role": "assistant", "content": corrected["response"]})
                        session['chat_history'] = history[-10:]
                        return jsonify({
                            "type": "chat_message",
                            "response": corrected["response"],
                            "error": None
                        })
                except Exception as heal_err:
                    logger.error(f"Self-healing correction call failed: {heal_err}")
                    # Continue to next retry attempt with the same SQL? No — break to avoid loop
                    break

        # If all attempts failed, return a user-friendly error
        if raw_results is None:
            error_msg = last_error or "Unknown database error"
            logger.error(f"SQL execution failed after {max_retries + 1} attempts: {error_msg}")

            # Add a helpful context note about type casting for TEXT columns
            if "function sum(text)" in error_msg or "function avg(text)" in error_msg:
                user_hint = (
                    "The database reported that one or more numeric operations (SUM/AVG) were attempted on a TEXT column. "
                    "This usually happens with raw CSV imports. Try asking with a more specific phrasing like "
                    "'SUM of [column] as a number' or cast the column in the semantic studio."
                )
            else:
                user_hint = ""

        formatted_data = []
        if isinstance(raw_results, str):
            formatted_data = [{"result": raw_results}]
        elif isinstance(raw_results, list):
            for row in raw_results:
                if isinstance(row, (tuple, list)):
                    formatted_data.append({f"col_{i}": val for i, val in enumerate(row)})
                elif isinstance(row, dict):
                    formatted_data.append(row)
                else:
                    formatted_data.append({"result": row})

        # Store assistant SQL turn
        history.append({"role": "assistant", "sql": sql})
        session['chat_history'] = history[-10:]

        # Cache successful SQL for future identical prompts
        cache_manager.set_cached_sql(question, sql, related_questions)

        # Store last query context so DeepBot can rerun/summarize the same dataset
        session['last_query_sql'] = sql
        if isinstance(formatted_data, list) and formatted_data:
            session['last_chart_data'] = formatted_data  # full dataset for DeepBot

        # ===== PERSONA POST-PROCESSING =====
        try:
            persona_result = run_persona(
                persona, raw_results, sql, question,
                table_names=list((published_model or {}).get('tables', {}).keys())
            )
        except Exception as e:
            logging.getLogger(__name__).warning(f"Persona processing failed: {e}")
            persona_result = {"persona": persona, "persona_name": persona.title()}

        # ----- Dynamic result tiering (medium/large) -----
        tier, tier_info = classify_result_size(
            len(formatted_data),
            limit_applied=bool(execution_guardrail and execution_guardrail.get('limit_applied'))
        )
        pivot_suggestions = []
        if tier == 'large':
            first_table = next(iter((published_model or {}).get('tables', {})), None)
            pivot_suggestions = build_pivot_suggestions(first_table, question or 'Query')

        return jsonify({
            "type": "data_response",
            "query": question,
            "generated_sql": sql,
            "data": formatted_data,
            "related_questions": related_questions,
            "error": None,
            "self_healed": retry_count,
            "guardrail": execution_guardrail,
            "result_tier": tier,
            "tier_info": tier_info,
            "pivot_suggestions": pivot_suggestions,
            "persona": persona,
            "persona_name": persona_result.get("persona_name", persona.title()),
            "persona_summary": persona_result.get("summary", ""),
            "viz_schema": persona_result.get("viz_schema"),
            "hygiene": persona_result.get("hygiene"),
            "analysis": persona_result.get("analysis"),
            "enrichment": persona_result.get("enrichment"),
        })
    except Exception as e:
        logger.error(f"SQL Generation/Execution error: {e}")
        return jsonify({
            "query": data.get('question'),
            "generated_sql": "",
            "data": [],
            "error": str(e)
        }), 500
