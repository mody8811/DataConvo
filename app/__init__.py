import os
from flask import Flask
from flask_session import Session

def create_app():
    app = Flask(__name__)
    app.secret_key = os.getenv('SECRET_KEY')

    app.config["SESSION_TYPE"] = "filesystem"
    Session(app)
    
    with app.app_context():
        from . import routes
        app.register_blueprint(routes.main)

    return app