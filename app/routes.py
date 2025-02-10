from flask import Blueprint, render_template, request, jsonify, redirect, url_for, session, flash, make_response
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash, check_password_hash
from . import db  # Import the SQLAlchemy instance from __init__.py
import logging
import os
from dotenv import load_dotenv
import bcrypt
from .models import User  # Import User model
from sqlalchemy import create_engine, text
import pandas as pd
import urllib.parse
from openai import OpenAI
import io
import base64
import matplotlib.pyplot as plt
import seaborn as sns
from fpdf import FPDF
import json
import tempfile
from sqlalchemy.exc import OperationalError, ProgrammingError
import numpy as np
import stripe
from datetime import datetime, timedelta
from flask_mail import Mail, Message
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user,login_required
# For OAuth authentication
from authlib.integrations.flask_client import OAuth
from .models import User  # Import the User model
from . import login_manager
import re
import paypalrestsdk
import time
import pyodbc
# Load environment variables from .env file
load_dotenv()

# Initialize blueprint for routes
main = Blueprint('main', __name__)

# Set up logging
log_level = os.getenv('LOG_LEVEL', 'INFO')  # Default to 'INFO' if not set
logging.basicConfig(level=log_level)
logger = logging.getLogger(__name__)
logger.info("Routes module loaded.")  # This is valid

# Set up OpenAI API key directly in code
client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

# Set Stripe API key
stripe.api_key = os.getenv('STRIPE_SECRET_KEY')
print(f"Stripe API Key: {stripe.api_key}")  # Debugging

# Set up SQLAlchemy logging
logging.getLogger('sqlalchemy.engine').setLevel(logging.DEBUG)

# Initialize OAuth client
oauth = OAuth()

# Set up Google and Microsoft OAuth
google = oauth.register(
    'google',
    client_id=os.getenv('GOOGLE_CLIENT_ID'),
    client_secret=os.getenv('GOOGLE_CLIENT_SECRET'),
    authorize_url='https://accounts.google.com/o/oauth2/auth',
    access_token_url='https://accounts.google.com/o/oauth2/token',
    client_kwargs={'scope': 'openid profile email'},
)

microsoft = oauth.register(
    'microsoft',
    client_id=os.getenv('MICROSOFT_CLIENT_ID'),
    client_secret=os.getenv('MICROSOFT_CLIENT_SECRET'),
    authorize_url='https://login.microsoftonline.com/common/oauth2/v2.0/authorize',
    access_token_url='https://login.microsoftonline.com/common/oauth2/v2.0/token',
    client_kwargs={'scope': 'openid profile email'},
)





 
# Flask-Login user loader
@login_manager.user_loader  # Correct usage of the decorator
def load_user(user_id):
    return User.query.get(int(user_id))

@main.route('/')
def index():
    return render_template('landing.html')

# Map plan to Stripe price IDs
def plan_to_price_id(plan):
    price_mapping = {
        'pro': os.getenv('STRIPE_PRO_PRICE_ID'),
        'enterprise': os.getenv('STRIPE_ENTERPRISE_PRICE_ID'),
    }
    return price_mapping.get(plan)

# Validate email format
def is_valid_email(email):
    email_regex = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(email_regex, email) is not None

@main.route('/pricing', methods=['GET'])
def pricing():
    return render_template('pricing.html')

@main.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'GET':
        # Render the signup page
        return render_template('signup.html', stripe_public_key=os.getenv('STRIPE_PUBLIC_KEY'))

    # Handle POST request
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided.'}), 400

    email = data.get('email')
    password = data.get('password')
    plan = data.get('plan')

    # Validate input
    if not email or not password or not plan:
        return jsonify({'error': 'All fields are required.'}), 400

    if not is_valid_email(email):
        return jsonify({'error': 'Invalid email address.'}), 400

    if len(password) < 8:
        return jsonify({'error': 'Password must be at least 8 characters long.'}), 400

    if plan not in ['free', 'pro', 'enterprise']:
        return jsonify({'error': 'Invalid plan selected.'}), 400

    # Check if the user already exists
    user = User.query.filter_by(email=email).first()
    if user:
        if user.plan == plan:
            return jsonify({'error': f'You are already registered with the {plan} plan.'}), 400
        elif user.plan == 'free' and plan in ['pro', 'enterprise']:
            # Allow free users to upgrade to a paid plan
            try:
                session_url = create_checkout_session(plan)  # Call the function with the plan argument
                return jsonify({'redirect_url': session_url}), 200
            except Exception as e:
                logger.error(f"Error creating Stripe Checkout session: {e}")
                return jsonify({'error': 'Failed to create payment session.'}), 500
        else:
            return jsonify({'error': f'You are currently on the {user.plan} plan. Please contact support to change your plan.'}), 400

    # Handle free plan signup
    if plan == 'free':
        try:
            hashed_password = generate_password_hash(password)
            trial_end_date = datetime.utcnow() + timedelta(days=14)  # Set trial_end_date to 14 days from now
            new_user = User(
                email=email,
                password=hashed_password,
                plan='free',
                trial_end_date=trial_end_date,  # Set the trial_end_date
                is_active=True
            )
            db.session.add(new_user)
            db.session.commit()
            login_user(new_user)
            return jsonify({'redirect_url': url_for('main.connection_form')}), 200
        except Exception as e:
            logger.error(f"Error creating free user: {e}")
            return jsonify({'error': 'Failed to create user.'}), 500

    # Handle paid plan signup
    try:
        session_url = create_checkout_session(plan)  # Call the function with the plan argument
        return jsonify({'redirect_url': session_url}), 200
    except Exception as e:
        logger.error(f"Error creating Stripe Checkout session: {e}")
        return jsonify({'error': 'Failed to create payment session.'}), 500
def is_trial_over(user):
    if user.plan == 'free' and user.trial_end_date:
        return datetime.utcnow() > user.trial_end_date
    return False

@main.route('/create-customer-portal', methods=['POST'])
def create_customer_portal():
    data = request.get_json()
    customer_id = data.get('customer_id')  # Pass the Stripe customer ID from the frontend

    if not customer_id:
        logger.error("Customer ID is missing in the request payload.")
        return jsonify({'error': 'Customer ID is required.'}), 400

    try:
        # Log the customer_id for debugging
        logger.info(f"Creating Stripe Customer Portal session for customer_id: {customer_id}")

        # Create a Stripe Customer Portal session
        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=url_for('main.connection_form', _external=True),  # Redirect after the portal session ends
        )

        # Log the session URL for debugging
        logger.info(f"Stripe Customer Portal session created successfully. URL: {session.url}")

        return jsonify({'url': session.url}), 200

    except stripe.error.StripeError as e:
        # Handle Stripe-specific errors
        logger.error(f"Stripe Error: {str(e)}")
        return jsonify({'error': f"Stripe Error: {str(e)}"}), 500

    except Exception as e:
        # Handle other unexpected errors
        logger.error(f"Unexpected Error: {str(e)}")
        return jsonify({'error': f"An unexpected error occurred: {str(e)}"}), 500
def create_checkout_session(plan):
    try:
        # Map the plan to a Stripe price ID
        price_id_map = {
            'pro': os.getenv('STRIPE_PRO_PRICE_ID'),  # Price ID for the Pro plan
            'enterprise': os.getenv('STRIPE_ENTERPRISE_PRICE_ID'),  # Price ID for the Enterprise plan
        }

        price_id = price_id_map.get(plan)
        if not price_id:
            raise ValueError(f"Invalid plan: {plan}")

        # Create a Stripe Checkout session
        session = stripe.checkout.Session.create(
            #payment_method_types=['card', 'paypal'],
            line_items=[{
                'price': price_id,
                'quantity': 1,
            }],
            mode='subscription',
            success_url=url_for('main.connection_form', _external=True),  # Redirect to the connection form after success
            cancel_url=url_for('main.signup', _external=True),  # Redirect back to the sign-up page if canceled
        )

        return session.url

    except stripe.error.StripeError as e:
        logger.error(f"Stripe Error: {str(e)}")
        raise e
    except Exception as e:
        logger.error(f"Unexpected Error: {str(e)}")
        raise e

@main.route('/customer-portal')
@login_required
def customer_portal():
    # Redirect to the Stripe-hosted Customer Portal
    return redirect('https://billing.stripe.com/p/login/00geVC3aN9NneIgaEE')

@main.route('/check-user', methods=['POST'])
def check_user():
    data = request.get_json()
    email = data.get('email')
    plan = data.get('plan')

    logger.info(f"Received check-user request: email={email}, plan={plan}")

    # Validate email
    if not is_valid_email(email):
        logger.error(f"Invalid email address: {email}")
        return jsonify({'error': 'Invalid email address.'}), 400

    # Validate plan
    if plan not in ['free', 'pro', 'enterprise']:
        logger.error(f"Invalid plan selected: {plan}")
        return jsonify({'error': 'Invalid plan selected.'}), 400

    # Check if user exists
    user = User.query.filter_by(email=email).first()
    if user:
        return jsonify({
            'alreadyRegistered': True,
            'currentPlan': user.plan,  # Return the user's current plan
            'message': f'You are currently on the {user.plan} plan.'
        }), 200

    logger.info(f"No existing user found with email: {email}")
    return jsonify({'alreadyRegistered': False}), 200

# Configure PayPal SDK
paypalrestsdk.configure({
    "mode": "live",  # Use "live" for production
    "client_id": os.getenv('PAYPAL_CLIENT_ID'),
    "client_secret": os.getenv('PAYPAL_CLIENT_SECRET'),
})

from flask import request, jsonify
import os
import paypalrestsdk
import logging

logger = logging.getLogger(__name__)

from flask import request, jsonify, url_for
import os
import paypalrestsdk
import logging

logger = logging.getLogger(__name__)

@main.route('/create-paypal-subscription', methods=['POST'])
def create_paypal_subscription():
    data = request.get_json()
    plan = data.get('plan')

    try:
        # Validate the PayPal plan ID
        paypal_plan_id_map = {
            'pro': os.getenv('PAYPAL_PRO_PLAN_ID'),  # Pro Plan ID from environment variables
            'enterprise': os.getenv('PAYPAL_ENTERPRISE_PLAN_ID'),  # Enterprise Plan ID from environment variables
        }
        paypal_plan_id = paypal_plan_id_map.get(plan)
        if not paypal_plan_id:
            raise ValueError(f"Invalid plan: {plan}")

        # Log the selected plan and PayPal plan ID for debugging
        logger.info(f"Selected Plan: {plan}, PayPal Plan ID: {paypal_plan_id}")

        # Build the subscription payload
        subscription_data = {
            "plan": {
                "id": paypal_plan_id,  # Use the dynamically selected plan ID
            },
            "payer": {
                "payment_method": "paypal",
            },
            "application_context": {
                "return_url": url_for('main.connection_form', _external=True),  # Redirect to your app's connection form on success
                "cancel_url": url_for('main.signup', _external=True),  # Redirect back to the sign-up page on cancellation
            }
        }

        # Create the subscription
        subscription = paypalrestsdk.BillingAgreement(subscription_data)

        # Log request for debugging
        logger.info(f"BillingAgreement Request: {subscription_data}")

        if subscription.create():
            # Return approval URL if successful
            for link in subscription.links:
                if link.rel == "approval_url":
                    return jsonify({
                        'redirect_url': link.href,
                        'plan_id': paypal_plan_id  # Return the plan ID to the frontend
                    }), 200

            raise Exception("Approval URL not found in PayPal response")
        else:
            # Log API error response
            logger.error(f"PayPal API Error: {subscription.error}")
            return jsonify({'error': subscription.error}), 400

    except Exception as e:
        # Log unexpected errors
        logger.error(f"Unexpected Error: {str(e)}")
        return jsonify({'error': f"Unexpected Error: {str(e)}"}), 500

@main.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        if not is_valid_email(email):
            flash('Invalid email address.', 'danger')
            return redirect(url_for('main.login'))

        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            # Manually set the session 'user_id' key for compatibility
            session['user_id'] = user.id
            session.permanent = True
            flash("Logged in successfully!", 'success')
            return redirect(url_for('main.connection_form'))
        else:
            flash("Invalid email or password.", 'danger')

    return render_template('login.html')

@main.route('/logout')
def logout():
    logout_user()
    session.clear()
    flash("You have been logged out.", 'info')
    return redirect(url_for('main.index'))
main.route('/protected')
@login_required
def protected():
    return f"Welcome, {current_user.email}! You are on the {current_user.plan} plan."
# Helper function to send upgrade email
def send_upgrade_email(email):
    # Implement your email sending logic here
    logger.info(f"Sending upgrade email to: {email}")

# Run the app
if __name__ == '__main__':
    with app.app_context():
        db.create_all()  # Create database tables
    app.run(debug=True)
# Google login callback
@main.route('/login/google')
def google_login():
    try:
        google_client = oauth.create_client('google')
        redirect_uri = url_for('main.google_authorize', _external=True)
        logger.debug(f"Redirecting to Google OAuth with redirect_uri: {redirect_uri}")
        return google_client.authorize_redirect(redirect_uri)
    except Exception as e:
        logger.error(f"Google OAuth login failed: {e}")
        flash("Google login failed. Please try again.")
        return redirect(url_for('main.login'))

# Google authorize route
@main.route('/login/google/authorize')
def google_authorize():
    try:
        google_client = oauth.create_client('google')
        token = google_client.authorize_access_token()
        logger.debug(f"Google OAuth token: {token}")
        
        user = google_client.parse_id_token(token)
        logger.debug(f"Google OAuth user: {user}")
        
        email = user.get('email')
        if not email:
            logger.error("Google OAuth failed: No email in user info.")
            flash("Google login failed. Please try again.")
            return redirect(url_for('main.login'))
        
        # Check if the user already exists in the database
        user_in_db = User.query.filter_by(email=email).first()
        if user_in_db:
            session['user_id'] = user_in_db.id
            session.permanent = True  # Make the session permanent
            logger.info(f"User logged in via Google: {email}")
        else:
            # Create a new user
            new_user = User(email=email)
            db.session.add(new_user)
            db.session.commit()
            session['user_id'] = new_user.id
            session.permanent = True  # Make the session permanent
            logger.info(f"New user created via Google: {email}")
        
        return redirect(url_for('main.connection_form'))
    except Exception as e:
        logger.error(f"Google OAuth authorization failed: {e}")
        flash("Google login failed. Please try again.")
        return redirect(url_for('main.login'))
# Microsoft login callback
@main.route('/login/microsoft')
def microsoft_login():
    microsoft_client = oauth.create_client('microsoft')
    redirect_uri = url_for('main.microsoft_authorize', _external=True)
    return microsoft_client.authorize_redirect(redirect_uri)

@main.route('/login/microsoft/authorize')
def microsoft_authorize():
    microsoft_client = oauth.create_client('microsoft')
    token = microsoft_client.authorize_access_token()
    user = microsoft_client.parse_id_token(token)
    logger.info(f"Microsoft OAuth user: {user}")
    
    # Add user to the database or log them in
    user_in_db = User.query.filter_by(email=user['email']).first()
    if user_in_db:
        session['user_id'] = user_in_db.id
    else:
        new_user = User(email=user['email'])
        db.session.add(new_user)
        db.session.commit()
        session['user_id'] = new_user.id
    return redirect(url_for('main.connection_form'))

# App Functionality (from older version)
@main.route('/connection_form')
def connection_form():
    # Redirect to login if user is not authenticated
    if not session.get('user_id'):
        return redirect(url_for('main.login'))
    return render_template('connection_form.html')

@main.route('/set_connection', methods=['POST'])
def set_connection():
    if not session.get('user_id'):
        return redirect(url_for('main.login'))
    
    db_type = request.form['db_type']
    server = request.form['server']
    auth_type = request.form['auth_type']
    database = request.form['database']
    schema = request.form['schema']  # Schema name from the form
    username = request.form['username']
    password = request.form['password']

    logger.info(f"Connection set for database: {db_type} at {server}")
    
    if db_type == "mssql":
        if auth_type == "windows":
            params = urllib.parse.quote_plus(
                "DRIVER={ODBC Driver 17 for SQL Server};"
                f"SERVER={server};"
                f"DATABASE={database};"
                "Trusted_Connection=yes;"
            )
        else:
            params = urllib.parse.quote_plus(
                "DRIVER={ODBC Driver 17 for SQL Server};"
                f"SERVER={server};"
                f"DATABASE={database};"
                f"UID={username};"
                f"PWD={password};"
                "Timeout=60;"
            )
        connection_string = f"mssql+pyodbc:///?odbc_connect={params}"
    elif db_type == "mysql":
        connection_string = f"mysql+pymysql://{username}:{password}@{server}/{database}"
    elif db_type == "postgresql":
        connection_string = f"postgresql://{username}:{password}@{server}/{database}"
    elif db_type == "azure_sql":
        connection_string = f"mssql+pyodbc://{username}:{password}@{server}:1433/{database}?driver=ODBC+Driver+17+for+SQL+Server"
    elif db_type == "aws_rds":
        connection_string = f"postgresql://{username}:{password}@{server}:5432/{database}"
    elif db_type == "google_sql":
        connection_string = f"mysql+pymysql://{username}:{password}@{server}/{database}"

    # Set connection details in the session
    session['connection_string'] = connection_string
    session['db_type'] = db_type
    session['schema'] = schema  # Set the schema name in the session

    # Debugging: Print the session data
    print("Session Data After Login:", session)

    return redirect(url_for('main.chat'))


@main.route('/chat')
def chat():
    if not session.get('user_id'):
        return redirect(url_for('main.login'))
    
    # Clear any upload-related session data
    upload_keys = ['upload_data', 'upload_analysis', 'current_sheet']
    for key in upload_keys:
        session.pop(key, None)
    
    # Set database-specific session data with prefixes
    session['db_state'] = 'INIT'
    session['db_table'] = None
    session['db_last_query'] = None
    session['db_tables_in_query'] = []
    
    logger.info(f"Database chat session initialized: {session}")
    schema_info = get_schema_info()
    return render_template('chat.html', schema_info=schema_info)


@main.route('/export_csv')
def export_csv():
    # Get the dataframe from the session
    df_dict = session.get('last_dataframe')
    
    # Check if data exists
    if not df_dict:
        logging.error("No data found in session for export.")
        return "No data to export", 400

    try:
        # Convert the dictionary to a DataFrame
        df = pd.DataFrame(df_dict)
        
        # Convert the DataFrame to CSV
        csv = df.to_csv(index=False)
        
        # Create a response with the CSV data
        response = make_response(csv)
        response.headers["Content-Disposition"] = "attachment; filename=data.csv"
        response.headers["Content-Type"] = "text/csv"
        
        return response
    
    except Exception as e:
        # Log the error and return a 500 error
        logging.error(f"Error exporting CSV: {e}")
        return "An error occurred while exporting the data", 500

def get_schema_info():
    schema_info = {}
    engine = create_engine(session.get('connection_string'))
    schema = session.get('schema')  # Retrieve schema name from session
    db_type = session.get('db_type')

    # Fetch table names and columns
    if db_type == "mssql":
        query_tables = f"""
        SELECT TABLE_NAME
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_TYPE = 'BASE TABLE' AND TABLE_SCHEMA = '{schema}'
        """
        query_columns = """
        SELECT COLUMN_NAME
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_NAME = '{}'
        """
        query_foreign_keys = """
        SELECT 
            fk.TABLE_NAME AS foreign_table,
            fk.COLUMN_NAME AS foreign_column,
            pk.TABLE_NAME AS primary_table,
            pk.COLUMN_NAME AS primary_column
        FROM INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS rc
        JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE fk ON rc.CONSTRAINT_NAME = fk.CONSTRAINT_NAME
        JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE pk ON rc.UNIQUE_CONSTRAINT_NAME = pk.CONSTRAINT_NAME
        WHERE fk.TABLE_SCHEMA = '{}'
        """
    elif db_type == "mysql":
        query_tables = f"""
        SELECT TABLE_NAME
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_TYPE = 'BASE TABLE' AND TABLE_SCHEMA = '{schema}'
        """
        query_columns = """
        SELECT COLUMN_NAME
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_NAME = '{}'
        """
        query_foreign_keys = """
        SELECT 
            TABLE_NAME,
            COLUMN_NAME,
            REFERENCED_TABLE_NAME,
            REFERENCED_COLUMN_NAME
        FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE
        WHERE TABLE_SCHEMA = '{}' AND REFERENCED_TABLE_NAME IS NOT NULL
        """
    elif db_type == "postgresql":
        query_tables = f"""
        SELECT TABLE_NAME
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_TYPE = 'BASE TABLE' AND TABLE_SCHEMA = '{schema}'
        """
        query_columns = """
        SELECT COLUMN_NAME
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_NAME = '{}'
        """
        query_foreign_keys = """
        SELECT 
            tc.table_name AS foreign_table,
            kcu.column_name AS foreign_column,
            ccu.table_name AS primary_table,
            ccu.column_name AS primary_column
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu ON tc.constraint_name = kcu.constraint_name
        JOIN information_schema.constraint_column_usage ccu ON tc.constraint_name = ccu.constraint_name
        WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = '{}'
        """
    else:
        query_tables = f"""
        SELECT TABLE_NAME
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_TYPE = 'BASE TABLE' AND TABLE_SCHEMA = '{schema}'
        """
        query_columns = """
        SELECT COLUMN_NAME
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_NAME = '{}'
        """
        query_foreign_keys = """
        SELECT 
            fk.TABLE_NAME AS foreign_table,
            fk.COLUMN_NAME AS foreign_column,
            pk.TABLE_NAME AS primary_table,
            pk.COLUMN_NAME AS primary_column
        FROM INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS rc
        JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE fk ON rc.CONSTRAINT_NAME = fk.CONSTRAINT_NAME
        JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE pk ON rc.UNIQUE_CONSTRAINT_NAME = pk.CONSTRAINT_NAME
        WHERE fk.TABLE_SCHEMA = '{}'
        """

    with engine.connect() as connection:
        # Fetch tables and columns
        result = connection.execute(text(query_tables))
        tables = [row[0] for row in result]
        for table in tables:
            result = connection.execute(text(query_columns.format(table)))
            columns = [row[0] for row in result]
            schema_info[table] = {"columns": columns, "relationships": []}

        # Fetch foreign key relationships
        result = connection.execute(text(query_foreign_keys.format(schema)))
        for row in result:
            foreign_table, foreign_column, primary_table, primary_column = row
            if foreign_table in schema_info and primary_table in schema_info:
                schema_info[foreign_table]["relationships"].append({
                    "foreign_column": foreign_column,
                    "primary_table": primary_table,
                    "primary_column": primary_column
                })

    relationships = []
    for table, info in schema_info.items():
        for rel in info["relationships"]:
            relationships.append(
                f"Table {table}.{rel['foreign_column']} is related to Table {rel['primary_table']}.{rel['primary_column']}"
            )

    return schema_info

def format_sql_error(error):
    """Convert SQL errors into user-friendly messages."""
    error_str = str(error)
    
    # Common SQL error patterns and their friendly messages
    error_patterns = {
        "Incorrect syntax": "I couldn't understand that request. Please try rephrasing it.",
        "Invalid column name": "I don't recognize one of the columns you mentioned.",
        "Invalid object name": "I couldn't find the table you're referring to.",
        "Conversion failed": "There's a mismatch in the data types I'm trying to work with.",
        "Permission denied": "You don't have permission to access this data.",
        "timeout": "The query took too long to complete. Please try a simpler request.",
        "non-boolean type": "I tried to use a value in a way that doesn't make sense for comparison.",
    }

    # Check for known error patterns and return friendly message
    for pattern, message in error_patterns.items():
        if pattern.lower() in error_str.lower():
            return message

    # Default friendly message if no pattern matches
    return "I encountered an error processing your request. Please try rephrasing it or ask for a different analysis."

@main.route('/get_response', methods=['POST'])
def get_response():
    try:
        user_message = request.json.get('message')
        message_type = request.json.get('type')  # New field to identify table selections
        state = session.get('state', 'INIT')
        db_type = session.get('db_type')
        engine = create_engine(session.get('connection_string'))
        
        # Handle explicit table selection
        if message_type == 'table_selection':
            schema_info = get_schema_info()
            if user_message in schema_info.keys():
                session['table'] = user_message
                session['state'] = 'TABLE_SELECTED'
                return jsonify({'response': f"Great! You've selected the {user_message} table. What would you like to know about it?"})
            else:
                return jsonify({'response': "I couldn't find that table. Please select a valid table."})

        # Rest of your existing logic remains unchanged
        if state == 'INIT':
            schema_info = get_schema_info()
            table_names = list(schema_info.keys())
            session['state'] = 'WAITING_FOR_TABLE'
            return jsonify({'response': f"Hello! Which table would you like to query? Available tables: {', '.join(table_names)}"})
        
        elif state == 'TABLE_SELECTED':
            try:
                schema_info = get_schema_info()
                table_name = session.get('table')
                schema_name = session.get('schema_name', 'dbo')

                if "plot" in user_message.lower() or "visualise" in user_message.lower():
                    last_data = session.get('last_dataframe')
                    if last_data:
                        logger.info("Generating visualization")
                        response = handle_visualization_request(user_message, last_data)
                        return jsonify({'response': response})
                    else:
                        return jsonify({'response': "AI: No previous query found to visualize."})
                elif "join" in user_message.lower():
                    logger.info("Handling join request")
                    return handle_join_request(user_message, schema_info, table_name)
                else:
                    sql_query = convert_nl_to_sql(user_message, table_name, schema_info[table_name], db_type, schema_name)
                    
                    if sql_query:
                        try:
                            rows, columns = execute_query_with_retry(sql_query, db_type, engine)
                            df = pd.DataFrame(rows, columns=columns)
                            
                            session['last_dataframe'] = {
                                'data': df.values.tolist(),
                                'columns': df.columns.tolist(),
                                'sql_query': sql_query,
                                'user_query': user_message
                            }
                            
                            response = f"<p><strong>SQL Query:</strong> {sql_query}</p>" + df.to_html(classes='table table-striped table-hover')
                            
                        except Exception as e:
                            logger.error(f"Query execution error: {str(e)}")
                            response = "AI: I had trouble running that query. Could you try asking in a different way?"
                    else:
                        response = "AI: I'm not sure what you're asking. Could you rephrase your question?"
                
                return jsonify({'response': response + "<br>AI: What else would you like to know? Or you can choose a different table."})
                
            except Exception as e:
                logger.error(f"Error in TABLE_SELECTED state: {str(e)}")
                return jsonify({'response': "AI: I encountered an error. Could you try rephrasing your request?"})

    except Exception as e:
        logger.error(f"Error in get_response: {str(e)}")
        return jsonify({'response': "AI: Something went wrong. Please try again."})

def generate_follow_up_suggestions(df, table_name, schema_info):
    """Generate contextual follow-up suggestions based on the query results."""
    suggestions = []
    columns = schema_info[table_name]['columns']
    
    # Add basic suggestions
    suggestions.append("- View all columns in this table")
    
    # Add numeric column suggestions
    numeric_columns = df.select_dtypes(include=['int64', 'float64']).columns
    if len(numeric_columns) > 0:
        suggestions.append(f"- Get statistics for {', '.join(numeric_columns)}")
        suggestions.append("- Find highest/lowest values")
    
    # Add date column suggestions
    date_columns = [col for col in df.columns if 'date' in col.lower()]
    if date_columns:
        suggestions.append("- Analyze trends over time")
        suggestions.append("- Group by time periods")
    
    # Add relationship suggestions
    relationships = schema_info[table_name].get('relationships', [])
    if relationships:
        suggestions.append("- Explore related tables")
        for rel in relationships:
            suggestions.append(f"- Join with {rel['related_table']}")
    
    return "\n".join(suggestions)

@main.route('/save_favorite', methods=['POST'])
def save_favorite():
    try:
        data = request.json
        name = data.get('name')
        
        # Get the last query data from session
        last_data = session.get('last_dataframe')
        
        if not last_data or 'sql_query' not in last_data:
            return jsonify({'error': 'No query available to save'}), 400
            
        favorite = {
            'name': name,
            'sql_query': last_data['sql_query'],
            'user_query': last_data.get('user_query', ''),
            'timestamp': datetime.now().isoformat()
        }
        
        # Get existing favorites from session or initialize empty list
        favorites = session.get('favorites', [])
        favorites.append(favorite)
        session['favorites'] = favorites
        
        return jsonify({'success': True, 'message': 'Favorite saved successfully'})
        
    except Exception as e:
        logger.error(f"Error saving favorite: {e}")
        return jsonify({'error': str(e)}), 500

@main.route('/get_favorites', methods=['GET'])
def get_favorites():
    try:
        favorites = session.get('favorites', [])
        return jsonify({'favorites': favorites})
    except Exception as e:
        logger.error(f"Error getting favorites: {e}")
        return jsonify({'error': str(e)}), 500

def handle_join_request(user_message, schema_info, table_name):
    session['last_query'] = user_message
    tables_in_query = [table_name] + [table for table in schema_info.keys() if table in user_message and table != table_name]
    session['tables_in_query'] = tables_in_query
    engine = create_engine(session.get('connection_string'))

    if len(tables_in_query) == 2:
        table1, table2 = tables_in_query
        common_keys = set(schema_info[table1]) & set(schema_info[table2])
        if common_keys:
            common_key = list(common_keys)[0]
            join_query = f"SELECT * FROM {table1} INNER JOIN {table2} ON {table1}.{common_key} = {table2}.{common_key}"
            session['last_query'] = join_query
            session['tables_in_query'] = []
            try:
                with engine.connect() as connection:
                    result = connection.execute(text(join_query))
                    df = pd.DataFrame(result.fetchall(), columns=result.keys())
                    response = df.to_html(classes='dataframe')
            except Exception as e:
                response = f"AI: Error: {str(e)}"
            return jsonify({'response': response + "<br>AI: What more do you want to query? Or you can choose a different table."})
        else:
            fact_tables = [table for table in schema_info.keys() if "Fact" in table]
            if fact_tables:
                return jsonify({'response': f"AI: No direct common keys found to join {table1} and {table2}. Please specify a fact table for joining. Available fact tables: {', '.join(fact_tables)}"})
            else:
                return jsonify({'response': "AI: No fact tables available for joining."})
    elif len(tables_in_query) > 2:
        fact_tables = [table for table in schema_info.keys() if "Fact" in table]
        if fact_tables:
            return jsonify({'response': f"AI: Multiple tables specified. Please specify a fact table for joining. Available fact tables: {', '.join(fact_tables)}"})
        else:
            return jsonify({'response': "AI: No fact tables available for joining."})
    else:
        return jsonify({'response': "AI: Could not determine the tables to join. Please specify the tables clearly."})
def understand_user_intent(user_message):
    """
    Determine the user's intent from the message, specifically whether they want to view data in a table
    or visualize it through a plot/chart.
    """
    user_message_lower = user_message.lower()
    
    if any(keyword in user_message_lower for keyword in ["plot", "visualize", "chart", "graph"]):
        return "visualization"
    
    # If the user uses terms associated with displaying data in a tabular format
    elif any(keyword in user_message_lower for keyword in ["show", "display", "view"]):
        return "table"
    
    # If unclear, ask the user to clarify their intent
    return "unclear"


def handle_user_query(user_message):
    """
    Main function to process the user query, visualize or show the data accordingly.
    """
    intent = understand_user_intent(user_message)
    
    if intent == "visualization":
        # Get the last query stored in the session
        sql_query = session.get('last_dataframe')
        
        if sql_query:
            # Generate visualization (chart, graph, etc.)
            response = handle_visualization_request(user_message, sql_query)
        else:
            response = "AI: No previous query found to visualize."
    
    elif intent == "table":
        # Handle the table view logic
        sql_query = session.get('last_query')
        
        if sql_query:
            # Show the data as a table (you could fetch the data or format it as needed)
            response = show_data_as_table(sql_query)
        else:
            response = "AI: No previous query found to display as a table."
    
    else:
        response = "AI: I'm not sure if you'd like to view the data in a table or visualize it. Could you clarify?"

    return response

def handle_visualization_request(chart_type, last_data, columns):
    # Set the backend before importing pyplot
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import seaborn as sns
    
    try:
        if not last_data:
            return "No data available to visualize. Please run a query first."
        
        df = pd.DataFrame(last_data['data'], columns=last_data['columns'])
        
        # Set style before creating figure
        plt.style.use('seaborn-v0_8')  # Use valid matplotlib style
        plt.figure(figsize=(10, 6))
        
        # Set color palette for consistent look
        sns.set_palette("husl")
        
        if chart_type == 'pie':
            values_col = columns.get('values')
            labels_col = columns.get('labels')
            if not (values_col and labels_col):
                return "Missing required columns for pie chart"
                
            plt.pie(df[values_col], labels=df[labels_col], autopct='%1.1f%%', 
                   startangle=140)
            plt.title(f'Distribution of {values_col} by {labels_col}')

        elif chart_type == 'bar':
            x_col = columns.get('x')
            y_col = columns.get('y')
            if not (x_col and y_col):
                return "Missing required columns for bar chart"
                
            sns.barplot(data=df, x=x_col, y=y_col)
            plt.xticks(rotation=45, ha='right')
            plt.xlabel(x_col)
            plt.ylabel(y_col)
            plt.title(f'{y_col} by {x_col}')
            plt.tight_layout()

        elif chart_type == 'line':
            x_col = columns.get('x')
            y_col = columns.get('y')
            if not (x_col and y_col):
                return "Missing required columns for line chart"
                
            sns.lineplot(data=df, x=x_col, y=y_col, marker="o")
            plt.xticks(rotation=45, ha='right')
            plt.xlabel(x_col)
            plt.ylabel(y_col)
            plt.title(f'Trend of {y_col} over {x_col}')
            plt.tight_layout()

        elif chart_type == 'scatter':
            x_col = columns.get('x')
            y_col = columns.get('y')
            if not (x_col and y_col):
                return "Missing required columns for scatter plot"
                
            sns.scatterplot(data=df, x=x_col, y=y_col)
            plt.xlabel(x_col)
            plt.ylabel(y_col)
            plt.title(f'{y_col} vs {x_col}')
            plt.tight_layout()

        else:
            return "Unsupported plot type. Try: pie, bar, line, or scatter"

        # Convert to image
        buf = io.BytesIO()
        plt.savefig(buf, format='png', bbox_inches='tight', dpi=300)
        buf.seek(0)
        plt.close()

        # Return HTML img tag with base64 encoded image
        img_str = base64.b64encode(buf.getvalue()).decode()
        return f'<img src="data:image/png;base64,{img_str}" alt="Visualization" style="max-width:100%; height:auto;"/>'
        
    except Exception as e:
        logger.error(f"Error creating visualization: {e}")
        return f"Error generating visualization: {str(e)}"

def escape_identifier(identifier, db_type):
    """Escape table/column names based on the database type."""
    if db_type == "mysql":
        return f"`{identifier}`"
    elif db_type == "postgresql":
        return f'"{identifier}"'
    elif db_type == "mssql":
        return f'[{identifier}]'
    else:
        return identifier  # Default to no escaping

def convert_nl_to_sql(prompt, table_name, table_info, db_type, schema_name):
    """Convert natural language to SQL using OpenAI."""
    try:
        # Add visualization check at the start
        if any(word in prompt.lower() for word in ['plot', 'chart', 'graph', 'visualize', 'show distribution']):
            return handle_visualization_request(prompt, session.get('last_dataframe'))
            
        schema_info = get_schema_info()
        
        # Ensure we have valid table name
        if not table_name:
            table_name = session.get('table')
        
        # Escape table name based on DB type
        escaped_table_name = escape_identifier(table_name, db_type)

        # Handle basic show/select patterns without using OpenAI
        if any(word in prompt.lower() for word in ['show', 'display', 'list', 'get']):
            if any(word in prompt.lower() for word in ['top', 'first']):
                num = re.search(r'\d+', prompt)
                limit = num.group() if num else "5"
                if db_type == "mssql":
                    return f"SELECT TOP {limit} * FROM {escaped_table_name}"
                else:
                    return f"SELECT * FROM {escaped_table_name} LIMIT {limit}"
            
        # For more complex queries, use OpenAI
        schema_details = "\n".join([
            f"Table {table}: Columns({', '.join(info['columns'])})" 
            for table, info in schema_info.items()
        ])

        relationships = []
        for table, info in schema_info.items():
            for rel in info.get("relationships", []):
                relationships.append(
                    f"{table}.{rel['foreign_column']} → {rel['primary_table']}.{rel['primary_column']}"
                )

        prompt_template = f"""Convert this to {db_type} SQL: {prompt}

Available Schema:
{schema_details}

Table Relationships:
{relationships}

Common Queries:
1. "Show recent hires" → SELECT * FROM Employees ORDER BY hire_date DESC
2. "Salary information" → SELECT * FROM Salaries WHERE employee_id = X
3. "Department details" → SELECT * FROM Departments
4. "Employee count" → SELECT COUNT(*) FROM Employees

Notes:
- Use {escaped_table_name} as the main table
- Include schema name {schema_name} if needed
- Use proper {db_type} syntax
"""

        client = OpenAI()
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a SQL expert. Return only the SQL query."},
                {"role": "user", "content": prompt_template}
            ],
            temperature=0.3
        )

        sql_query = extract_sql_query(response.choices[0].message.content)

        if db_type == "postgresql" and schema_name:
            if not sql_query.lower().find(f"{schema_name.lower()}."):
                sql_query = sql_query.replace("FROM ", f"FROM {schema_name}.")
                sql_query = sql_query.replace("JOIN ", f"JOIN {schema_name}.")

        return sql_query

    except Exception as e:
        logger.error(f"Error in convert_nl_to_sql: {str(e)}")
        return None

def extract_sql_query(response_text):
    """Extract the SQL query from response."""
    try:
        # Remove any markdown formatting
        clean_text = response_text.replace("```sql", "").replace("```", "").strip()
        
        # Find the SELECT statement
        if clean_text.upper().startswith("SELECT"):
            # Ensure it ends with semicolon
            if not clean_text.rstrip().endswith(';'):
                clean_text += ';'
            return clean_text
            
        # If no direct SELECT, try to find it in the text
        match = re.search(r'(SELECT\s+.*?;)', clean_text, re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1).strip()
            
        return None

    except Exception as e:
        logger.error(f"Error extracting SQL: {str(e)}")
        return None

def get_table_relationships(tables):
    """Get relationships between tables."""
    try:
        engine = create_engine(session.get('connection_string'))
        with engine.connect() as connection:
            query = """
            SELECT 
                OBJECT_NAME(f.parent_object_id) AS foreign_table,
                COL_NAME(fc.parent_object_id, fc.parent_column_id) AS foreign_column,
                OBJECT_NAME(f.referenced_object_id) AS primary_table,
                COL_NAME(fc.referenced_object_id, fc.referenced_column_id) AS primary_column
            FROM 
                sys.foreign_keys AS f
                INNER JOIN sys.foreign_key_columns AS fc 
                    ON f.object_id = fc.constraint_object_id
            WHERE 
                OBJECT_NAME(f.parent_object_id) IN :tables 
                OR OBJECT_NAME(f.referenced_object_id) IN :tables
            """
            result = connection.execute(text(query), {'tables': tuple(tables)})
            return [dict(row) for row in result]
    except Exception as e:
        logger.error(f"Error getting table relationships: {str(e)}")
        return []

def execute_query_with_retry(sql_query, db_type, engine, max_retries=3):
    """Execute SQL query with smart retry logic."""
    retries = 0
    last_error = None
    
    while retries < max_retries:
        try:
            with engine.begin() as connection:
                # First try the original query
                if retries == 0:
                    result = connection.execute(text(sql_query))
                else:
                    # On retry, attempt to fix common issues
                    fixed_query = fix_common_sql_issues(sql_query, last_error, db_type)
                    result = connection.execute(text(fixed_query))
                    
                return result.fetchall(), result.keys()
                
        except Exception as e:
            last_error = str(e)
            retries += 1
            logger.warning(f"Query attempt {retries} failed: {e}")
            time.sleep(1)  # Brief pause before retry
            
    logger.error(f"All retry attempts failed. Last error: {last_error}")
    raise Exception(f"Query failed after {max_retries} attempts: {last_error}")

def fix_common_sql_issues(query, error, db_type):
    """Smart query fixing based on error message."""
    error_lower = error.lower()
    
    # Handle reserved word issues
    if "keyword" in error_lower or "reserved word" in error_lower:
        # Replace problematic aliases with safe alternatives
        query = re.sub(r'AS (\w+)', lambda m: f'AS total_{m.group(1)}' 
                      if m.group(1).lower() in ['count', 'rowcount', 'sum', 'total'] 
                      else m.group(0), query, flags=re.IGNORECASE)
    
    # Handle syntax differences between DB types
    if db_type == "postgresql" and "limit" in error_lower:
        query = query.replace("TOP ", "").replace("LIMIT", "")
        query += " LIMIT 10"  # Safe default
        
    return query

def generate_dynamic_prompts(df):
    """Generate sophisticated, data-agnostic prompts based on the data structure."""
    prompts = []
    
    # Get column types
    numeric_cols = df.select_dtypes(include=['number']).columns.tolist()
    categorical_cols = df.select_dtypes(include=['object']).columns.tolist()
    date_cols = [col for col in df.columns if 'date' in col.lower() or df[col].dtype == 'datetime64[ns]']

    # Basic Statistical Analysis
    if numeric_cols:
        for col in numeric_cols[:2]:  # Limit to 2 columns to avoid too many prompts
            prompts.extend([
                f"Show me the distribution of {col} across different categories",
                f"What are the highest and lowest values of {col}?",
                f"Calculate the average {col} and show how it varies across groups"
            ])

    # Comparative Analysis
    if len(categorical_cols) > 0 and len(numeric_cols) > 0:
        cat_col = categorical_cols[0]
        num_col = numeric_cols[0]
        prompts.extend([
            f"Compare {num_col} across different {cat_col} categories",
            f"Which {cat_col} has the highest average {num_col}?",
            f"Show me the top performers based on {num_col}",
            f"Find unusual patterns in {num_col} for each {cat_col}"
        ])

    # Time-based Analysis
    if date_cols:
        date_col = date_cols[0]
        prompts.extend([
            f"Show me the trends over time using {date_col}",
            "What are the month-over-month changes?",
            "Identify any seasonal patterns in the data",
            "Compare performance between different time periods"
        ])

    # Multi-dimensional Analysis
    if len(categorical_cols) >= 2 and len(numeric_cols) >= 1:
        prompts.extend([
            f"Analyze how {numeric_cols[0]} varies across different combinations of {categorical_cols[0]} and {categorical_cols[1]}",
            "Show me the relationships between different categories",
            "Find interesting patterns across multiple dimensions"
        ])

    # Pattern Detection
        prompts.extend([
        "Identify any outliers in the data",
        "Show me any unusual patterns or anomalies",
        "What are the most common combinations of values?",
        "Find correlations between different metrics"
    ])

    # Advanced Analytics
    if len(numeric_cols) >= 2:
        prompts.extend([
            "Show me relationships between different numeric metrics",
            "Which factors have the strongest correlation?",
            "Compare different metrics and their relationships"
        ])

    # Group Analysis
    if categorical_cols:
        prompts.extend([
            "Show me the breakdown by different categories",
            "Compare performance across different groups",
            "Which groups are performing above average?"
        ])

    # Remove duplicates and empty prompts
    prompts = list(set([p for p in prompts if p.strip()]))
    
    # Sort prompts by complexity and relevance
    prompts.sort(key=len, reverse=True)
    
    # Return top 10 most relevant prompts
    return prompts[:10]

@main.route('/get_columns', methods=['GET'])
def get_columns():
    try:
        # Get the last table data from the session
        df_dict = session.get('last_dataframe')
        if not df_dict:
            return jsonify({"error": "No table data found in session. Please run a query first."}), 400

        # Extract columns from the DataFrame
        columns = df_dict.get('columns', [])
        
        # Add column types for better anonymization handling
        column_info = []
        if df_dict.get('data'):
            df = pd.DataFrame(df_dict['data'], columns=columns)
            for col in columns:
                dtype = str(df[col].dtype)
                column_info.append({
                    'name': col,
                    'type': dtype,
                    'can_anonymize': dtype == 'object' or 'string' in dtype.lower()  # Only anonymize text columns
                })
        
        return jsonify({
            "columns": column_info
        })

    except Exception as e:
        logging.error(f"Error fetching columns: {e}")
        return jsonify({"error": "An error occurred while fetching columns."}), 500

@main.route('/anonymize', methods=['POST'])
def anonymize():
    try:
        data = request.json
        selected_columns = data.get('columns', [])

        # Get the last dataframe from session
        last_data = session.get('last_dataframe')
        if not last_data:
            return jsonify({"error": "No data available to anonymize"}), 400

        # Convert the stored data back to DataFrame
        df = pd.DataFrame(last_data['data'], columns=last_data['columns'])
        
        # Perform anonymization on selected columns
        for column in selected_columns:
            if column in df.columns:
                # Anonymize based on data type
                if df[column].dtype == 'object' or 'string' in str(df[column].dtype).lower():
                    df[column] = df[column].apply(lambda x: '****' if pd.notnull(x) else x)

        # Convert DataFrame back to dictionary format
        anonymized_data = {
            "data": df.values.tolist(),
            "columns": df.columns.tolist()
        }

        return jsonify({"data": anonymized_data})

    except Exception as e:
        logger.error(f"Error in anonymize: {str(e)}")
        return jsonify({"error": str(e)}), 500

@main.route('/data-tools', methods=['POST'])
def data_tools():
    tool = request.json.get('tool')
    if tool == 'clean':
        # Fetch the last table data from the session
        df_dict = session.get('last_dataframe')
        if not df_dict:
            return jsonify({"error": "No table data found in session. Please run a query first."}), 400

        # Convert the dictionary to a DataFrame
        df = pd.DataFrame(df_dict['data'], columns=df_dict['columns'])

        # Generate cleaning suggestions
        suggestions = generate_cleaning_suggestions(df)
        return jsonify({"suggestions": suggestions})

def generate_cleaning_suggestions(df):
    """Generate detailed cleaning suggestions based on the DataFrame."""
    suggestions = []

    # Check for missing values
    for col in df.columns:
        missing_count = df[col].isnull().sum()
        if missing_count > 0:
            percentage_missing = (missing_count / len(df)) * 100
            if percentage_missing > 20:
                suggestions.append(f"Column '{col}' has {missing_count} missing values ({percentage_missing:.2f}%). Consider filling or removing this column, especially if the missing data exceeds 20%.")
            else:
                suggestions.append(f"Column '{col}' has {missing_count} missing values ({percentage_missing:.2f}%). You might want to fill these missing values with appropriate strategies like mean/median or use interpolation.")

    # Check for negative values in numeric columns
    numeric_columns = df.select_dtypes(include=['number']).columns
    for col in numeric_columns:
        negative_count = (df[col] < 0).sum()
        if negative_count > 0:
            suggestions.append(f"Column '{col}' has {negative_count} negative values. These could be data entry errors. You may want to investigate or handle them based on your domain knowledge (e.g., replacing with zero or removing).")

    # Check for single-value columns
    for col in df.columns:
        if df[col].nunique() == 1:
            if len(df) > 50:  # If dataset is large, single-value columns are typically not useful
                suggestions.append(f"Column '{col}' has only one unique value. This column likely doesn't provide much information and could be removed.")
            else:
                suggestions.append(f"Column '{col}' has only one unique value. If this column is essential, consider consolidating or removing it based on your analysis.")

    # Check for duplicates in rows
    if df.duplicated().any():
        suggestions.append("The dataset contains duplicate rows. You might want to remove them to ensure data integrity.")

    # Check for constant columns (i.e., columns where all values are the same, but not necessarily one value)
    for col in df.columns:
        if df[col].nunique() == len(df):
            continue  # Skip columns with more than one unique value
        if df[col].nunique() == 1:
            suggestions.append(f"Column '{col}' has constant values across all rows. Consider removing this column.")

    return suggestions

@main.route('/get_insights', methods=['POST'])
def get_insights():
    try:
        # Get the last table data from the session
        df_dict = session.get('last_dataframe')
        if not df_dict:
            return jsonify({"error": "No table data found in session. Please run a query first."}), 400

        # Convert the dictionary to a DataFrame
        df = pd.DataFrame(df_dict['data'], columns=df_dict['columns'])

        # Generate dynamic prompts based on the table and columns
        prompts = generate_dynamic_prompts(df)

        return jsonify({"prompts": prompts})

    except Exception as e:
        logging.error(f"Error generating insights: {e}")
        return jsonify({"error": "An error occurred while generating insights"}), 500

def generate_sql_query(user_message, table_name, schema_info, db_type):
    """Generate SQL query from natural language with database-specific syntax."""
    try:
        # Handle table name escaping based on database type
        if db_type == "mysql":
            escaped_table_name = f"`{table_name}`"
        elif db_type == "postgresql":
            escaped_table_name = f'"{table_name}"'
        else:  # mssql
            escaped_table_name = f'[{table_name}]'
        
        # Handle LIMIT/TOP syntax based on database type
        def get_limit_clause(limit_num):
            if db_type == "mssql":
                return f"TOP {limit_num}"
            else:  # mysql or postgresql
                return f"LIMIT {limit_num}"

        # Handle common query patterns with database-specific syntax
        if 'top' in user_message.lower() or 'first' in user_message.lower():
            limit = re.search(r'\d+', user_message)
            limit_num = limit.group() if limit else "10"
            
            if db_type == "mssql":
                return f"SELECT TOP {limit_num} * FROM {escaped_table_name}"
            else:
                return f"SELECT * FROM {escaped_table_name} LIMIT {limit_num}"
            
        if 'show' in user_message.lower() and 'row' in user_message.lower():
            if 'all' in user_message.lower():
                return f"SELECT * FROM {escaped_table_name}"
            # Default to showing top 100 rows
            limit_clause = get_limit_clause(100)
            if db_type == "mssql":
                return f"SELECT {limit_clause} * FROM {escaped_table_name}"
            else:
                return f"SELECT * FROM {escaped_table_name} {limit_clause}"
            
        if any(word in user_message.lower() for word in ['average', 'avg', 'mean']):
            # Extract column name or use all numeric columns
            columns = schema_info[table_name]['columns']
            numeric_columns = [col for col in columns if 'int' in str(col).lower() or 'float' in str(col).lower() or 'decimal' in str(col).lower()]
            if numeric_columns:
                # Handle AVG function (similar across databases)
                avg_expressions = [f"AVG({col}) as avg_{col}" for col in numeric_columns]
                return f"SELECT {', '.join(avg_expressions)} FROM {escaped_table_name}"
            return f"SELECT * FROM {escaped_table_name}"

        # Handle date functions based on database type
        if any(word in user_message.lower() for word in ['date', 'month', 'year']):
            date_columns = [col for col in schema_info[table_name]['columns'] if 'date' in str(col).lower()]
            if date_columns:
                date_col = date_columns[0]
                if db_type == "mssql":
                    return f"SELECT DATEPART(year, {date_col}) as year, DATEPART(month, {date_col}) as month FROM {escaped_table_name} GROUP BY DATEPART(year, {date_col}), DATEPART(month, {date_col})"
                elif db_type == "mysql":
                    return f"SELECT YEAR({date_col}) as year, MONTH({date_col}) as month FROM {escaped_table_name} GROUP BY YEAR({date_col}), MONTH({date_col})"
                else:  # postgresql
                    return f"SELECT EXTRACT(YEAR FROM {date_col}) as year, EXTRACT(MONTH FROM {date_col}) as month FROM {escaped_table_name} GROUP BY EXTRACT(YEAR FROM {date_col}), EXTRACT(MONTH FROM {date_col})"

        # Default query with database-specific LIMIT/TOP
        limit_clause = get_limit_clause(100)
        if db_type == "mssql":
            return f"SELECT {limit_clause} * FROM {escaped_table_name}"
        else:
            return f"SELECT * FROM {escaped_table_name} {limit_clause}"

    except Exception as e:
        logger.error(f"Error generating SQL query: {e}")
        # Return a safe default query
        return f"SELECT * FROM {escaped_table_name}"

@main.route('/get_last_query', methods=['GET'])
def get_last_query():
    try:
        # Get the last dataframe from session
        last_data = session.get('last_dataframe')
        if not last_data:
            return jsonify({"error": "No query available"}), 400

        return jsonify({
            "sql_query": last_data.get('sql_query'),
            "user_query": last_data.get('user_query')
        })

    except Exception as e:
        logger.error(f"Error getting last query: {e}")
        return jsonify({"error": str(e)}), 500

@main.route('/get_tables', methods=['GET'])
def get_tables():
    """Get list of available tables."""
    try:
        schema_info = get_schema_info()
        return jsonify({'tables': list(schema_info.keys())})
    except Exception as e:
        logger.error(f"Error getting tables: {str(e)}")
        return jsonify({'tables': []})

db_specific_examples = """
    Examples:
    1. Natural language: How many rows are in the table?
       SQL: SELECT COUNT(*) FROM table_name;
    ...
"""

def is_safe_query(sql_query):
    """Check if the query is safe (SELECT only, including CTEs)."""
    # Convert to uppercase for consistent checking
    sql_upper = sql_query.upper().strip()
    
    # Block dangerous keywords
    dangerous_keywords = [
        'INSERT', 'UPDATE', 'DELETE', 'DROP', 'TRUNCATE', 
        'ALTER', 'CREATE', 'GRANT', 'EXECUTE', 'MERGE',
        'UPSERT', 'REPLACE', 'COPY', 'CALL', 'LOCK'
    ]
    
    # Allow CTEs but ensure they only contain SELECTs
    if sql_upper.startswith('WITH '):
        # Split CTE parts and check each one
        cte_parts = sql_upper.split('SELECT')
        for part in cte_parts:
            for keyword in dangerous_keywords:
                if keyword in part:
                    logger.warning(f"Blocked CTE containing {keyword}: {sql_query}")
                    return False
    # Check regular SELECT queries
    elif not sql_upper.startswith('SELECT'):
        logger.warning(f"Blocked non-SELECT query: {sql_query}")
        return False
        
    # Check for dangerous keywords in main query
    for keyword in dangerous_keywords:
        if keyword in sql_upper:
            logger.warning(f"Blocked query containing {keyword}: {sql_query}")
            return False
            
    # Check for multiple statements (;) but allow semicolons in CTEs
    if ';' in sql_query[:-1]:  # Allow single semicolon at end
        logger.warning(f"Blocked multiple statement query: {sql_query}")
        return False
        
    return True

@main.route('/get_visualization_options', methods=['GET'])
def get_visualization_options():
    try:
        # Get the last table data from the session
        df_dict = session.get('last_dataframe')
        if not df_dict:
            return jsonify({"error": "No table data found. Please run a query first."}), 400

        df = pd.DataFrame(df_dict['data'], columns=df_dict['columns'])
        
        # Get column types
        numeric_cols = df.select_dtypes(include=['int64', 'float64']).columns.tolist()
        categorical_cols = df.select_dtypes(include=['object']).columns.tolist()
        
        # Define chart types and their required column types
        chart_options = {
            'pie': {
                'name': 'Pie Chart',
                'requires': {
                    'values': 'numeric',
                    'labels': 'categorical',
                },
                'description': 'Good for showing parts of a whole'
            },
            'bar': {
                'name': 'Bar Chart',
                'requires': {
                    'x': 'categorical',
                    'y': 'numeric'
                },
                'description': 'Good for comparing quantities across categories'
            },
            'line': {
                'name': 'Line Chart',
                'requires': {
                    'x': 'any',
                    'y': 'numeric'
                },
                'description': 'Good for showing trends over time'
            },
            'scatter': {
                'name': 'Scatter Plot',
                'requires': {
                    'x': 'numeric',
                    'y': 'numeric'
                },
                'description': 'Good for showing relationships between two numeric variables'
            }
        }

        return jsonify({
            'columns': {
                'numeric': numeric_cols,
                'categorical': categorical_cols
            },
            'charts': chart_options
        })

    except Exception as e:
        logger.error(f"Error getting visualization options: {e}")
        return jsonify({"error": str(e)}), 500

@main.route('/create_visualization', methods=['POST'])
def create_visualization():
    try:
        data = request.json
        chart_type = data.get('chartType')
        columns = data.get('columns', {})
        
        last_data = session.get('last_dataframe')
        if not last_data:
            return jsonify({"error": "No data available"}), 400

        response = handle_visualization_request(chart_type, last_data, columns)
        return jsonify({'visualization': response})

    except Exception as e:
        logger.error(f"Error creating visualization: {e}")
        return jsonify({"error": str(e)}), 500

@main.route('/delete_favorite', methods=['POST'])
def delete_favorite():
    try:
        data = request.json
        favorite_name = data.get('name')
        
        if not favorite_name:
            return jsonify({"error": "No favorite name provided"}), 400

        # Get current favorites from session
        favorites = session.get('favorites', [])
        
        # Remove the favorite with matching name
        favorites = [f for f in favorites if f['name'] != favorite_name]
        
        # Update session
        session['favorites'] = favorites
        
        return jsonify({"message": "Favorite deleted successfully"})

    except Exception as e:
        logger.error(f"Error deleting favorite: {str(e)}")
        return jsonify({"error": "Failed to delete favorite"}), 500

@main.route('/test-connection', methods=['POST'])
def test_connection():
    try:
        db_type = request.form.get('db_type')
        server = request.form.get('server')
        database = request.form.get('database')
        auth_type = request.form.get('auth_type')
        username = request.form.get('username')
        password = request.form.get('password')
        
        # Create connection string based on the database type
        connection_string = create_connection_string(
            db_type, server, database, 
            auth_type, username, password
        )
        
        # Try to establish a connection
        engine = create_engine(connection_string)
        with engine.connect() as connection:
            # Try a simple query to verify connection
            connection.execute(text("SELECT 1"))
        
        return jsonify({
            "success": True,
            "message": "Successfully connected to the database!"
        })
        
    except Exception as e:
        error_message = str(e)
        # Clean up error message for common issues
        if "password authentication failed" in error_message.lower():
            error_message = "Invalid username or password"
        elif "could not connect to server" in error_message.lower():
            error_message = "Could not connect to server. Please verify the server address"
            
        return jsonify({
            "success": False,
            "error": f"Connection failed: {error_message}"
        }), 400

def create_connection_string(db_type, server, database, auth_type, username=None, password=None):
    """Create database connection string based on database type and authentication."""
    try:
        if db_type == 'mssql':
            # List of possible SQL Server drivers in order of preference
            drivers = [
                'ODBC Driver 18 for SQL Server',
                'ODBC Driver 17 for SQL Server',
                'SQL Server Native Client 11.0',
                'SQL Server',
                'FreeTDS'
            ]
            
            # Find the first available driver
            driver = None
            import pyodbc
            for driver_name in drivers:
                if driver_name in [d for d in pyodbc.drivers()]:
                    driver = driver_name
                    break
                    
            if not driver:
                raise Exception("No SQL Server driver found. Please install an ODBC driver for SQL Server.")
            
            # Create connection string with the found driver
            driver_str = f"driver={driver}"
            if auth_type == 'windows':
                return f'mssql+pyodbc://{server}/{database}?{driver_str}&trusted_connection=yes'
            else:
                return f'mssql+pyodbc://{username}:{password}@{server}/{database}?{driver_str}'
        
        elif db_type == 'mysql':
            # MySQL connection string
            if username and password:
                return f'mysql+pymysql://{username}:{password}@{server}/{database}'
            return f'mysql+pymysql://{server}/{database}'
            
        elif db_type == 'postgresql':
            # PostgreSQL connection string
            if username and password:
                return f'postgresql://{username}:{password}@{server}/{database}'
            return f'postgresql://{server}/{database}'
            
        elif db_type == 'azure_sql':
            # Azure SQL connection string (using newer ODBC drivers)
            driver = 'ODBC Driver 17 for SQL Server'  # Azure SQL typically uses this driver
            driver_str = f"driver={driver}"
            return f'mssql+pyodbc://{username}:{password}@{server}/{database}?{driver_str}'
            
        elif db_type == 'aws_rds':
            # AWS RDS connection string
            if 'rds.amazonaws.com' in server.lower():
                if '.postgres.' in server.lower():
                    return f'postgresql://{username}:{password}@{server}/{database}'
                else:
                    return f'mysql+pymysql://{username}:{password}@{server}/{database}'
                    
        elif db_type == 'google_sql':
            # Google Cloud SQL connection string
            return f'mysql+pymysql://{username}:{password}@{server}/{database}'
            
        raise ValueError(f"Unsupported database type: {db_type}")
        
    except Exception as e:
        logger.error(f"Error creating connection string: {str(e)}")
        raise Exception(f"Failed to create connection string: {str(e)}")
