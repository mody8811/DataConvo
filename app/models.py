from datetime import datetime
from . import db  # Import the db instance from your package
from flask_login import UserMixin




class User(UserMixin, db.Model):
    __tablename__ = 'user'  # Explicitly set the table name
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(120), nullable=False)
    plan = db.Column(db.String(20), nullable=False, default='free')
    stripe_customer_id = db.Column(db.String(120), nullable=True)
    trial_end_date = db.Column(db.DateTime, nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    def is_trial_active(self):
        """
        Check if the user's trial is still active.
        """
        if self.plan == 'free' and self.trial_end_date:
            return datetime.utcnow() < self.trial_end_date
        return True  # Pro/Enterprise users are always active