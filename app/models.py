from . import db

class User(db.Model):
    """Database model for storing user information."""
    id = db.Column(db.Integer, primary_key=True)  # Unique ID for each user
    email = db.Column(db.String(120), unique=True, nullable=False)  # User's email address
    password = db.Column(db.String(200), nullable=False)  # Hashed password

    def __repr__(self):
        return f"<User {self.email}>"
