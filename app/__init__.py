from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_session import Session
import os
from dotenv import load_dotenv

# Import the CustomJSONProvider from upload_chat.py
from .upload_chat  import upload_chat,CustomJSONProvider

# Load environment variables
load_dotenv()

# Initialize extensions
db = SQLAlchemy()
login_manager = LoginManager()

def create_app():
    app = Flask(__name__)
    
    # Configure custom JSON provider so that NaN values are handled correctly.
    app.json_provider_class = CustomJSONProvider
    app.json = app.json_provider_class(app)
    
    # Config settings
    app.secret_key = os.getenv('SECRET_KEY', 'dev-key-please-change')
    app.config.update(
        UPLOAD_FOLDER=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads'),
        MAX_CONTENT_LENGTH=16 * 1024 * 1024,
        SQLALCHEMY_DATABASE_URI=os.environ.get('DATABASE_URL') or 'sqlite:///' + os.path.join(os.path.dirname(os.path.abspath(__file__)), 'app.db'),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SESSION_TYPE='filesystem',
        SESSION_PERMANENT=False,
        PERMANENT_SESSION_LIFETIME=1800  # 30 minutes
    )
    
    # Initialize extensions
    db.init_app(app)
    login_manager.init_app(app)
    Session(app)
    
    # Create uploads folder
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    
    # Register blueprints
    from .routes import main
    from .upload_chat import upload_chat
    
    app.register_blueprint(main)
    app.register_blueprint(upload_chat, url_prefix='/upload_chat')
    
    # Import models
    from .models import User
    
    # Create database tables
    with app.app_context():
        db.create_all()
    
    return app