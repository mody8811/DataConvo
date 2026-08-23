from . import db
from flask_login import UserMixin
from datetime import datetime
import uuid
from werkzeug.security import generate_password_hash, check_password_hash


class Workspace(db.Model):
    """A team workspace. New signups create one and become its admin."""
    __tablename__ = 'workspace'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    owner_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    members = db.relationship('User', backref='workspace', lazy=True, foreign_keys='User.workspace_id')


class TeamInvite(db.Model):
    """Pending invite to join a workspace as a member."""
    __tablename__ = 'team_invite'

    id = db.Column(db.Integer, primary_key=True)
    workspace_id = db.Column(db.Integer, db.ForeignKey('workspace.id'), nullable=False)
    email = db.Column(db.String(120), nullable=False)
    token = db.Column(db.String(64), unique=True, nullable=False)
    role = db.Column(db.String(20), default='member', nullable=False)  # member | admin
    status = db.Column(db.String(20), default='pending', nullable=False)  # pending | accepted | revoked
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=True)  # NULL = never expires
    accepted_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    email_sent = db.Column(db.Boolean, default=False, nullable=False)


class User(db.Model, UserMixin):
    """Maps to the existing `user` table. New columns are added via ALTER TABLE
    migration (see migrations/001_add_auth_columns.py), preserving legacy data."""
    __tablename__ = 'user'

    id = db.Column(db.Integer, primary_key=True)
    # Legacy columns (pre-existing)
    username = db.Column(db.String(80), unique=True, nullable=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(120), nullable=True)  # legacy plaintext (kept for compat)
    # New auth/subscription columns (added via migration)
    supabase_id = db.Column(db.String(128), nullable=True)  # Supabase Auth user UUID
    password_hash = db.Column(db.String(256), nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    stripe_customer_id = db.Column(db.String(120), nullable=True)
    subscription_tier = db.Column(db.String(20), default='free', nullable=False)
    # Self-hosted license key (HMAC-signed). Populated via License & Subscription.
    license_key = db.Column(db.String(512), nullable=True)
    # BYOK (Bring Your Own Key) — encrypted provider credentials
    openai_api_key_encrypted = db.Column(db.Text, nullable=True)
    anthropic_api_key_encrypted = db.Column(db.Text, nullable=True)
    active_llm_provider = db.Column(db.String(20), default='openai', nullable=False)
    byok_enabled = db.Column(db.Boolean, default=False, nullable=False)
    # Universal multi-provider BYOK metadata (universal Cline-style config)
    llm_provider = db.Column(db.String(30), default='openai', nullable=False)
    llm_base_url = db.Column(db.String(500), nullable=True)
    llm_model_id = db.Column(db.String(200), nullable=True)
    # TOTP MFA secret (ISO 27001) — nullable until enrolled
    totp_secret = db.Column(db.String(32), nullable=True)
    # True once the user has verified a TOTP code (requires MFA on subsequent logins)
    mfa_verified = db.Column(db.Boolean, default=False, nullable=False)
    # RBAC role: 'admin' (full access) or 'member' (restricted by table permissions)
    role = db.Column(db.String(20), default='member', nullable=False)
    # Workspace: the team this user belongs to
    workspace_id = db.Column(db.Integer, db.ForeignKey('workspace.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships (account isolation)
    saved_queries = db.relationship('SavedQuery', backref='owner', lazy=True)
    published_models = db.relationship('PublishedModel', backref='owner', lazy=True)
    dashboard_widgets = db.relationship('DashboardWidget', backref='owner', lazy=True)

    def set_password(self, password):
        """Hash and store the password. Also sets legacy `password` column for
        backward compatibility with any code reading it directly."""
        self.password_hash = generate_password_hash(password)
        self.password = password  # legacy column (best-effort)

    def check_password(self, password):
        """Verify password against the hash. Falls back to legacy plaintext
        column if no hash exists yet (e.g. pre-migration users)."""
        if self.password_hash:
            return check_password_hash(self.password_hash, password)
        # Legacy fallback: compare plaintext password column
        return self.password == password

    # Flask-Login requires `is_active` as a property; map to the column
    @property
    def is_authenticated(self):
        return True

    @property
    def is_anonymous(self):
        return False

    def get_id(self):
        return str(self.id)


class SavedQuery(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    # Account isolation: FK to user table
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    title = db.Column(db.String(200), nullable=False)
    sql_query = db.Column(db.Text, nullable=False)
    is_favorite = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class TablePermission(db.Model):
    """RBAC workspace-scoped: maps role -> allowed tables (and optional
    per-table column whitelists) in ONE workspace.

    Rows are keyed by (workspace_id, role='member') for absolute tenant
    isolation. Legacy user_id / workspace_id=NULL rows are deprecated but
    kept readable for backward compatibility.

    Columns:
      - allowed_tables      : JSON list of table names the member may query.
      - column_permissions  : JSON object mapping table -> [allowed columns].
                              e.g. {"apple_sales_2024": ["date","region","revenue"],
                                    "Salaries": ["department"]}
                              A table with no entry (or empty list) means ALL
                              columns are allowed. This preserves backward
                              compatibility with pre-granular rows.
    """
    id = db.Column(db.Integer, primary_key=True)
    workspace_id = db.Column(db.Integer, db.ForeignKey('workspace.id'), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)  # legacy
    role = db.Column(db.String(20), default='member', nullable=False)
    allowed_tables = db.Column(db.Text, nullable=False, default='[]')  # JSON list of table names
    # Granular column whitelist: {"table_name": ["col_a", "col_b"]}
    column_permissions = db.Column(db.Text, nullable=True)  # JSON object (None = all columns allowed)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def get_column_map(self):
        """Return dict {table: [allowed columns]} — empty dict = no column restrictions."""
        import json as _json
        if not self.column_permissions:
            return {}
        try:
            data = _json.loads(self.column_permissions)
            if isinstance(data, dict):
                return {str(k): list(v) for k, v in data.items() if isinstance(v, list)}
        except Exception:
            pass
        return {}

    def set_column_map(self, column_map):
        """Persist the column map as JSON (empty dict stores '{}')."""
        import json as _json
        self.column_permissions = _json.dumps(column_map or {})


class PublishedModel(db.Model):
    """Account-isolated published semantic layer. Each user's published tables,
    enums, metrics, and joins are stored per account."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    # The serialized semantic layer model (JSON string)
    model_json = db.Column(db.Text, nullable=False)
    db_type = db.Column(db.String(20), nullable=True)
    # Persisted connection string so background/refresh execution can restore
    # the user's database context even if the filesystem session is lost.
    connection_string = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DataQualityMonitor(db.Model):
    """Admin-configured scheduled data-quality monitor on a physical table.

    Bypasses the semantic layer — operates directly on raw database tables
    selected from the full schema introspection. Check types:
      - freshness       : ensure the table has received recent data (age of max timestamp)
      - volume          : detect volume spikes vs. the historical average (row count)
      - null_threshold  : flag columns exceeding a null-percentage threshold
      - custom_sql      : run a custom assertion; any returned row means failure
    """
    __tablename__ = 'data_quality_monitor'

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    # Tenant isolation: FK to user table
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    table_name = db.Column(db.String(255), nullable=False)
    check_type = db.Column(db.String(30), nullable=False)  # freshness | volume | null_threshold | custom_sql | schema_drift | uniqueness | value_range
    # JSON: thresholds, column names, custom SQL expressions, etc.
    parameters = db.Column(db.Text, nullable=False, default='{}')
    # JSON: {emails: [...], slack_webhook_url: '...'} destination channels for alerts
    notification_channels = db.Column(db.Text, nullable=True)
    schedule_cron = db.Column(db.String(64), nullable=True)   # e.g. 0 * * * * (hourly), 0 0 * * * (daily), null = manual
    frequency = db.Column(db.String(30), nullable=True)       # hourly | daily | weekly | manual
    severity = db.Column(db.String(20), default='warning', nullable=False)  # warning | critical
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationship with incidents
    incidents = db.relationship('DataQualityIncident', backref='monitor', lazy=True, cascade='all, delete-orphan')

    def to_dict(self):
        import json
        try:
            params = json.loads(self.parameters or '{}')
        except (ValueError, TypeError):
            params = {}
        try:
            channels = json.loads(self.notification_channels or '{}')
        except (ValueError, TypeError):
            channels = {}
        # Determine current status from latest incident
        latest = None
        if self.incidents:
            latest = sorted(
                self.incidents,
                key=lambda i: i.triggered_at or i.created_at or datetime.min,
                reverse=True
            )[0]
        status = 'passing'
        if latest and latest.status == 'failing':
            status = 'failing'
        return {
            'id': self.id,
            'user_id': self.user_id,
            'table_name': self.table_name,
            'check_type': self.check_type,
            'parameters': params,
            'notification_channels': channels,
            'schedule_cron': self.schedule_cron,
            'frequency': self.frequency,
            'severity': self.severity,
            'is_active': self.is_active,
            'status': status,
            'last_run': latest.triggered_at.isoformat() if latest and latest.triggered_at else None,
            'last_status': latest.status if latest else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class DataQualityIncident(db.Model):
    """Tracked failure / resolution event for a data-quality monitor."""
    __tablename__ = 'data_quality_incident'

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    monitor_id = db.Column(db.String(36), db.ForeignKey('data_quality_monitor.id'), nullable=False, index=True)
    status = db.Column(db.String(20), default='failing', nullable=False)  # failing | resolved
    error_message = db.Column(db.Text, nullable=False, default='')
    failed_rows_count = db.Column(db.Integer, nullable=True)
    triggered_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'monitor_id': self.monitor_id,
            'status': self.status,
            'error_message': self.error_message,
            'failed_rows_count': self.failed_rows_count,
            'triggered_at': self.triggered_at.isoformat() if self.triggered_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class DashboardWidget(db.Model):
    """User-pinned visualization widget for the BI Dashboard.

    Stores the verified SQL, the serialized chart configuration (ECharts/Chart.js),
    and result metadata so widgets can be re-executed and re-rendered independently
    of the chat session.
    """
    __tablename__ = 'dashboard_widget'

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    # Account isolation: FK to user table
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    sql_query = db.Column(db.Text, nullable=False)
    # JSON storage for the chart rendering configuration (ECharts / Chart.js)
    chart_config = db.Column(db.Text, nullable=False, default='{}')
    # JSON metadata: result schema (column names/types), row count, generated question, etc.
    result_metadata = db.Column(db.Text, nullable=False, default='{}')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        """Serialize for API responses (safe, no raw DB internals)."""
        import json
        try:
            chart_cfg = json.loads(self.chart_config or '{}')
        except (ValueError, TypeError):
            chart_cfg = {}
        try:
            meta = json.loads(self.result_metadata or '{}')
        except (ValueError, TypeError):
            meta = {}
        return {
            'id': self.id,
            'user_id': self.user_id,
            'title': self.title,
            'sql_query': self.sql_query,
            'chart_config': chart_cfg,
            'result_metadata': meta,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
