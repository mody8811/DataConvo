import os
from flask import Flask
from flask_session import Session
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Initialize SQLAlchemy
db = SQLAlchemy()

def create_app():
    app = Flask(__name__)

    # Set up configuration
    app.secret_key = os.getenv('SECRET_KEY', 'default_secret_key')  # Default for development
    app.config["SESSION_TYPE"] = "filesystem"
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
        'DATABASE_URL', 'sqlite:///default.db'  # Use SQLite as a fallback for development
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False  # Avoid overhead

    # Initialize extensions
    Session(app)
    db.init_app(app)

    # Import and register blueprints
    from .routes import main  # Ensure this import is correct
    app.register_blueprint(main)

    # Create database tables (development use only; prefer migrations in production)
    with app.app_context():
        db.create_all()

    return app
