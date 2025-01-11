from flask import Flask
from flask_session import Session

def create_app():
    app = Flask(__name__)
    app.secret_key = 'Jana&rayan'  # Ensure you set the secret key

    app.config["SESSION_TYPE"] = "filesystem"
    Session(app)
    
    with app.app_context():
        from . import routes
        app.register_blueprint(routes.main)

    return app
