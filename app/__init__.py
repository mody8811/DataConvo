from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_session import Session
import os
from dotenv import load_dotenv

# Initialize extensions
db = SQLAlchemy()
login_manager = LoginManager()
sess = Session()

def create_app():
    app = Flask(__name__)
    
    # Load environment variables
    load_dotenv()
    
    # Configure app
    app.config.update(
        # Self-hosted production: FLASK_SECRET_KEY is preferred;
        # SECRET_KEY remains the legacy fallback.
        SECRET_KEY=os.getenv('FLASK_SECRET_KEY') or os.getenv('SECRET_KEY', 'dev-key-please-change'),
        # Production: DATABASE_URL is set on Render (Supabase PostgreSQL).
        # Local development falls back to SQLite in the instance/ folder.
        SQLALCHEMY_DATABASE_URI=os.getenv('DATABASE_URL', 'sqlite:///dataconvo.db'),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SESSION_TYPE='filesystem',
        SESSION_FILE_DIR=os.path.join(os.getcwd(), 'flask_sessions'),
        SESSION_COOKIE_SAMESITE=None,
        SESSION_COOKIE_SECURE=False,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_DOMAIN=None,
        SESSION_PERMANENT=True,
        PERMANENT_SESSION_LIFETIME=86400,  # 24 hours
        # ---- Feature flags ----
        # ENABLE_QUERY_CLASSIFIER: pre-flight intent classifier (legacy, ~5s latency).
        # False (default) = streamlined single-pass LLM handles schema mapping natively.
        # True = re-enable the legacy IntentClassifier step for comparative testing.
        ENABLE_QUERY_CLASSIFIER=os.getenv('ENABLE_QUERY_CLASSIFIER', 'false').strip().lower() in ('1', 'true', 'yes', 'on'),
    )
    
     # Initialize extensions
    db.init_app(app)
    login_manager.init_app(app)
    sess.init_app(app)
    
    # Setup user loader
    from .models import User, SavedQuery
    from app import models_blog  # noqa: F401  (registers BlogPost for create_all)
    
    # Configure Flask-Login: redirect unauthenticated users to /login
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Please log in to access this page.'
    login_manager.login_message_category = 'info'
    
    # Ensure schema matches models.py (PostgreSQL-safe) BEFORE load_user is wired.
    with app.app_context():
        db.create_all()
        try:
            from sqlalchemy import inspect as sa_inspect
            from sqlalchemy import text as sa_text

            # Detect dialect for correct identifier quoting.
            # 'user' is a SQL reserved keyword; PostgreSQL requires "user".
            user_table_ref = 'user'
            # TablePermission workspace scoping (multi-tenant isolation)
            try:
                db.session.execute(sa_text('ALTER TABLE table_permission ADD COLUMN IF NOT EXISTS workspace_id INTEGER'))
                db.session.commit()
            except Exception:
                db.session.rollback()
            # TablePermission granular column-level whitelists (per-member access)
            try:
                db.session.execute(sa_text('ALTER TABLE table_permission ADD COLUMN IF NOT EXISTS column_permissions TEXT'))
                db.session.commit()
            except Exception:
                db.session.rollback()
            if db.engine.dialect.name == 'postgresql':
                user_table_ref = '"user"'

            inspector = sa_inspect(db.engine)
            table_names = set(inspector.get_table_names())

            # === USER TABLE COLUMN SYNC ===
            # Every column the User model declares must exist remotely.
            if 'user' in table_names:
                user_cols = {col['name'] for col in inspector.get_columns('user')}
                user_columns_ddl = {
                    'username': 'VARCHAR(80)',
                    'password_hash': 'VARCHAR(256)',
                    'is_active': 'BOOLEAN DEFAULT 1',
                    'stripe_customer_id': 'VARCHAR(120)',
                    'subscription_tier': "VARCHAR(20) DEFAULT 'free'",
                    'created_at': 'DATETIME',
                    'supabase_id': 'VARCHAR(128)',
                    'role': "VARCHAR(20) DEFAULT 'member'",
                    'workspace_id': 'INTEGER REFERENCES workspace(id)',
                    'totp_secret': 'VARCHAR(32)',
                    'mfa_verified': 'BOOLEAN DEFAULT 0',
                    'openai_api_key_encrypted': 'TEXT',
                    'anthropic_api_key_encrypted': 'TEXT',
                    'active_llm_provider': "VARCHAR(20) DEFAULT 'openai'",
                    'byok_enabled': 'BOOLEAN DEFAULT 0',
                    'llm_provider': "VARCHAR(30) DEFAULT 'openai'",
                    'llm_base_url': 'VARCHAR(500)',
                    'llm_model_id': 'VARCHAR(200)',
                }
                for col_name, ddl in user_columns_ddl.items():
                    if col_name not in user_cols:
                        db.session.execute(sa_text(
                            f'ALTER TABLE {user_table_ref} ADD COLUMN IF NOT EXISTS {col_name} {ddl}'
                        ))
                        db.session.commit()
                        app.logger.info(
                            'Migration: added %s.%s column', 'user', col_name
                        )

                # === Self-hosted license key column ===
                if 'license_key' not in user_cols:
                    db.session.execute(sa_text(
                        f'ALTER TABLE {user_table_ref} ADD COLUMN IF NOT EXISTS license_key VARCHAR(512)'
                    ))
                    db.session.commit()
                    app.logger.info('Migration: added user.license_key')

            # === published_model.connection_string ===
            if 'published_model' in table_names:
                pm_cols = {c['name'] for c in inspector.get_columns('published_model')}
                if 'connection_string' not in pm_cols:
                    db.session.execute(sa_text(
                        'ALTER TABLE published_model ADD COLUMN IF NOT EXISTS connection_string TEXT'
                    ))
                    db.session.commit()
                    app.logger.info('Migration: added published_model.connection_string')

            # === data_quality_monitor.notification_channels ===
            if 'data_quality_monitor' in table_names:
                dq_cols = {c['name'] for c in inspector.get_columns('data_quality_monitor')}
                if 'notification_channels' not in dq_cols:
                    db.session.execute(sa_text(
                        'ALTER TABLE data_quality_monitor ADD COLUMN IF NOT EXISTS notification_channels TEXT'
                    ))
                    db.session.commit()
                    app.logger.info(
                        'Migration: added data_quality_monitor.notification_channels'
                    )

            # === team_invite extra columns ===
            if 'team_invite' in table_names:
                ti_cols = {c['name'] for c in inspector.get_columns('team_invite')}
                team_invite_ddl = {
                    'role': "VARCHAR(20) DEFAULT 'member'",
                    'expires_at': 'DATETIME',
                    'accepted_by': 'INTEGER',
                    'email_sent': 'BOOLEAN DEFAULT 0',
                }
                for col_name, ddl in team_invite_ddl.items():
                    if col_name not in ti_cols:
                        db.session.execute(sa_text(
                            f'ALTER TABLE team_invite ADD COLUMN IF NOT EXISTS {col_name} {ddl}'
                        ))
                        db.session.commit()
                        app.logger.info(
                            'Migration: added team_invite.%s', col_name
                        )

            # Ensure all remaining tables (workspace, dashboard_widget, etc.) exist.
            db.create_all()
        except Exception as e:
            db.session.rollback()
            app.logger.warning(f"Startup auto-migration skipped: {e}")

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))
    
    # Import and register blueprints
    from .routes import main as main_blueprint
    from .routes.auth import auth as auth_blueprint
    from .routes.billing import billing as billing_blueprint
    from .routes.blog import blog as blog_blueprint
    from .routes.dashboard import dashboard as dashboard_blueprint
    from .routes.data_quality import data_quality as data_quality_blueprint
    
    app.register_blueprint(main_blueprint)
    app.register_blueprint(auth_blueprint)
    app.register_blueprint(billing_blueprint)
    app.register_blueprint(blog_blueprint)
    app.register_blueprint(dashboard_blueprint)
    app.register_blueprint(data_quality_blueprint)
    
    # Create database tables if they don't exist (after models are imported),
    # then run a dialect-aware auto-migration that safely syncs all columns
    # defined in app/models.py (local SQLite + Supabase PostgreSQL).
    
    return app
