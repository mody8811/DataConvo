from flask import Blueprint, render_template, request, jsonify, redirect, url_for, session, flash
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

# For OAuth authentication
from authlib.integrations.flask_client import OAuth

# Load environment variables from .env file
load_dotenv()

main = Blueprint('main', __name__)

# Set up OpenAI API key directly in code
client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

# Set up logging
log_level = os.getenv('LOG_LEVEL', 'INFO')
logging.basicConfig(level=log_level)
logger = logging.getLogger(__name__)

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

# Login Routes (from newer version)
@main.route('/')
def index():
    # Serve the landing page at "/"
    return render_template('landing.html')

@main.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')

        # Check if the user is already registered
        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            logger.info(f"Signup failed: Email {email} is already registered.")
            return render_template('signup.html', error='Email already registered. Please log in.')

        # If not registered, hash the password and add the user to the database
        hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())

        try:
            new_user = User(email=email, password=hashed_password.decode('utf-8'))
            db.session.add(new_user)
            db.session.commit()
            session['user_id'] = new_user.id  # Log the user in
            logger.info(f"New user created and logged in: {email}")
            return redirect(url_for('main.connection_form'))
        except IntegrityError:
            db.session.rollback()
            logger.error(f"Signup failed: Integrity error for email {email}.")
            return render_template('signup.html', error='An unexpected error occurred. Please try again.')

    return render_template('signup.html')

@main.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        
        user = User.query.filter_by(email=email).first()
        if user:
            # Compare the hashed password
            if bcrypt.checkpw(password.encode('utf-8'), user.password.encode('utf-8')):
                session['user_id'] = user.id
                flash("Logged in successfully!")
                return redirect(url_for('main.connection_form'))  # Redirect to connection form
            else:
                flash("Invalid email or password.")
        else:
            flash("User not found.")
    
    return render_template('login.html')

@main.route('/logout')
def logout():
    # Clear the session to log the user out
    session.clear()
    logger.info("User logged out successfully.")
    return redirect(url_for('main.index'))

# Google login callback
@main.route('/login/google')
def google_login():
    google_client = oauth.create_client('google')
    redirect_uri = url_for('main.google_authorize', _external=True)
    return google_client.authorize_redirect(redirect_uri)

@main.route('/login/google/authorize')
def google_authorize():
    google_client = oauth.create_client('google')
    token = google_client.authorize_access_token()
    user = google_client.parse_id_token(token)
    logger.info(f"Google OAuth user: {user}")
    
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
                f"DRIVER={{SQL Server}};"
                f"SERVER={server};"
                f"DATABASE={database};"
                "Trusted_Connection=yes;"
            )
        else:
            params = urllib.parse.quote_plus(
                f"DRIVER={{SQL Server}};"
                f"SERVER={server};"
                f"DATABASE={database};"
                f"UID={username};"
                f"PWD={password};"
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
    
    session['state'] = 'INIT'
    session['table'] = None
    session['last_query'] = None
    session['tables_in_query'] = []
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
    
    with engine.connect() as connection:
        result = connection.execute(text(query_tables))
        tables = [row[0] for row in result]  # Accessing tuple element by index
        for table in tables:
            result = connection.execute(text(query_columns.format(table)))
            columns = [row[0] for row in result]  # Accessing tuple element by index
            schema_info[table] = columns
            
    return schema_info

@main.route('/get_response', methods=['POST'])
def get_response():
    user_message = request.json.get('message')
    state = session.get('state', 'INIT')
    table_name = session.get('table')
    db_type = session.get('db_type')  # Get the db_type from session
    engine = create_engine(session.get('connection_string'))
    
    logger.info(f"User message: {user_message}, State: {state}, Table: {table_name}")
    
    if state == 'INIT':
        schema_info = get_schema_info()
        table_names = list(schema_info.keys())
        session['state'] = 'WAITING_FOR_TABLE'
        logger.info("State set to WAITING_FOR_TABLE")
        return jsonify({'response': f"AI: Hello! Which table would you like to query? Available tables: {', '.join(table_names)}"})
    
    elif state == 'WAITING_FOR_TABLE':
        schema_info = get_schema_info()
        table_names = list(schema_info.keys())
        if user_message in table_names:
            session['table'] = user_message
            session['state'] = 'TABLE_SELECTED'
            session['tables_in_query'] = [user_message]
            logger.info(f"Table selected: {user_message}")
            return jsonify({'response': f"AI: Great! You chose the {user_message} table. What do you want to know about it?"})
        else:
            logger.warning("Invalid table name provided")
            return jsonify({'response': "AI: I couldn't find that table. Please specify a valid table name."})
    
    elif state == 'TABLE_SELECTED':
        schema_info = get_schema_info()
        table_name = session.get('table')
        if any(table in user_message for table in schema_info.keys()):
            session['table'] = user_message
            table_name = user_message
            session['tables_in_query'].append(user_message)
            logger.info(f"Table changed to: {user_message}")
            return jsonify({'response': f"AI: Great! You chose the {user_message} table. What do you want to know about it?"})

        if "plot" in user_message.lower() or "visualise" in user_message.lower():
            sql_query = session.get('last_query')
            if sql_query:
                logger.info("Generating visualization")
                response = generate_visualization(sql_query, user_message)
            else:
                response = "AI: No previous query found to visualize."
        elif "join" in user_message.lower():
            logger.info("Handling join request")
            return handle_join_request(user_message, schema_info, table_name)
        else:
            sql_query = parse_natural_language_query(user_message, schema_info, table_name)
            if sql_query:
                session['last_query'] = sql_query
                try:
                    with engine.connect() as connection:
                        result = connection.execute(text(sql_query))
                        df = pd.DataFrame(result.fetchall(), columns=result.keys())
                        session['last_dataframe'] = df.to_dict(orient='split')  # Convert DataFrame to dictionary
                        response = f"<p><strong>SQL Query:</strong> {sql_query}</p>" + df.to_html(classes='dataframe')
                except Exception as e:
                    response = f"AI: Error: {str(e)}"
            else:
                response = "AI: Could not parse query"
        logger.info("Query processed")
        return jsonify({'response': response + "<br>AI: What more do you want to query? Or you can choose a different table."})

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

def generate_visualization(sql_query, plot_type):
    engine = create_engine(session.get('connection_string'))
    try:
        with engine.connect() as connection:
            df = pd.read_sql(sql_query, connection)
        
        # Check if the dataframe is not empty
        if df.empty:
            return "The query returned no data for visualization."

        # Generate a simple plot based on user request (e.g., a bar plot or pie chart)
        plt.figure(figsize=(10, 6))

        if "pie" in plot_type.lower():
            colors = sns.color_palette("viridis", len(df))
            plt.pie(df.iloc[:, 1], labels=df.iloc[:, 0], autopct='%1.1f%%', startangle=140, colors=colors)
            plt.title('Pie Chart')
        else:
            sns.barplot(x=df.iloc[:, 0], y=df.iloc[:, 1], palette="viridis")
            plt.xticks(rotation=50, ha='right')
            plt.xlabel(df.columns[0])
            plt.ylabel(df.columns[1])
            plt.title('Bar Plot')
            plt.tight_layout()

        # Save the plot to a string buffer
        buffer = io.BytesIO()
        plt.savefig(buffer, format='png')
        buffer.seek(0)

        # Encode the image to base64 string
        img_str = base64.b64encode(buffer.getvalue()).decode()

        # Return the image tag to embed in HTML
        return f'<img src="data:image/png;base64,{img_str}" alt="Visualization"/>'
    except Exception as e:
        return f"Error generating visualization: {str(e)}"

def parse_natural_language_query(nl_query, schema_info, table_name):
    db_type = session.get('db_type', 'mssql')  # Default to 'mssql' if not set
    schema_name = session.get('schema')  # Retrieve schema name from session
    
    # Debugging: Print the session data
    print("Session Data in parse_natural_language_query:", session)

    # Debugging: Print the schema name and its type
    print("Schema Name from Session:", schema_name)
    print("Type of Schema Name:", type(schema_name))

    # Ensure schema_name is a valid string for PostgreSQL
    if db_type == "postgresql":
        if not schema_name or not isinstance(schema_name, str):
            raise ValueError("Schema name is not set or is invalid. Please log in again.")

    schema_details = "\n".join([f"Table {table}: {', '.join(columns)}" for table, columns in schema_info.items()])

    if db_type == "mssql":
        db_specific_examples = """
        Examples:
        1. Natural language: How many rows are in the table?
           SQL: SELECT COUNT(*) FROM table_name;
        2. Natural language: Show all columns from the table.
           SQL: SELECT * FROM table_name;
        3. Natural language: Join table1 and table2 on column1.
           SQL: SELECT * FROM table1 INNER JOIN table2 ON table1.column1 = table2.column1;
        4. Natural language: Show the top 10 rows by column1.
           SQL: SELECT TOP 10 * FROM table_name ORDER BY column1;
        """
    elif db_type == "mysql":
        db_specific_examples = """
        Examples:
        1. Natural language: How many rows are in the table?
           SQL: SELECT COUNT(*) FROM table_name;
        2. Natural language: Show all columns from the table.
           SQL: SELECT * FROM table_name;
        3. Natural language: Join table1 and table2 on column1.
           SQL: SELECT * FROM table1 INNER JOIN table2 ON table1.column1 = table2.column1;
        4. Natural language: Show the top 10 rows by column1.
           SQL: SELECT * FROM table_name ORDER BY column1 LIMIT 10;
        """
    elif db_type == "postgresql":
        db_specific_examples = f"""
        Examples:
        1. Natural language: How many rows are in the table?
           SQL: SELECT COUNT(*) FROM {schema_name}.table_name;
        2. Natural language: Show all columns from the table.
           SQL: SELECT * FROM {schema_name}.table_name;
        3. Natural language: Join table1 and table2 on column1.
           SQL: SELECT * FROM {schema_name}.table1 INNER JOIN {schema_name}.table2 ON table1.column1 = table2.column1;
        4. Natural language: Show the top 10 rows by column1.
           SQL: SELECT * FROM {schema_name}.table_name ORDER BY column1 LIMIT 10;
        """
    else:
        db_specific_examples = "No specific examples available."

    # Add schema handling for PostgreSQL
    if db_type == "postgresql":
        prompt = (
            f"You are a SQL expert specializing in PostgreSQL. "
            f"Convert the following natural language query into a PostgreSQL-compatible SQL query. "
            f"Schema details:\n{schema_details}\n\n"
            f"The table to query is {table_name} in the schema {schema_name}. "
            f"Always include the schema name in the SQL query. "
            f"Query: {nl_query}\n\n"
            f"Examples of PostgreSQL queries:\n"
            f"1. Natural language: How many rows are in the table?\n"
            f"   SQL: SELECT COUNT(*) FROM {schema_name}.table_name;\n"
            f"2. Natural language: Show all columns from the table.\n"
            f"   SQL: SELECT * FROM {schema_name}.table_name;\n"
            f"3. Natural language: Join table1 and table2 on column1.\n"
            f"   SQL: SELECT * FROM {schema_name}.table1 INNER JOIN {schema_name}.table2 ON table1.column1 = table2.column1;\n"
            f"4. Natural language: Show the top 10 rows by column1.\n"
            f"   SQL: SELECT * FROM {schema_name}.table_name ORDER BY column1 LIMIT 10;\n"
        )
    else:
        prompt = (
            f"Schema details:\n{schema_details}\n\n"
            f"Convert this natural language query to SQL syntax for {db_type}. "
            f"The table to query is {table_name}: {nl_query}\n\n"
        )

    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "You are a helpful assistant that converts natural language queries into SQL queries."},
            {"role": "user", "content": prompt}
        ]
    )

    response_text = response.choices[0].message.content

    # Fallback: Ensure the schema name is included for PostgreSQL
    if db_type == "postgresql":
        # Check if the schema name is already included in the query
        if f"{schema_name}." not in response_text:
            # Append the schema name to the table name
            response_text = response_text.replace("FROM ", f"FROM {schema_name}.")
            response_text = response_text.replace("JOIN ", f"JOIN {schema_name}.")

    # Extract SQL query from response
    sql_query = extract_sql_query(response_text)
    return sql_query

def extract_sql_query(response_text):
    # Allow only SELECT queries
    keywords = ["SELECT"]
    start = -1
    for keyword in keywords:
        start = response_text.upper().find(keyword)
        if start != -1:
            break
    if start == -1:
        return None  # No SQL query found

    # Extract the SQL query portion from the response
    end = response_text.find("```", start)  # Assuming the SQL query is within triple backticks
    if end == -1:
        end = len(response_text)
    
    return response_text[start:end].strip()