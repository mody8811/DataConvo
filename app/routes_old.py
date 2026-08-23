from flask import Blueprint, render_template, request, redirect, url_for, session, jsonify, current_app
from app.profiler import profile_database
from app.services.sql_chain_service import SQLChainService
from app.services.query_executor import QueryExecutor
from app.core.database import DatabaseManager
import logging
import json
import urllib.parse

main = Blueprint('main', __name__)
logger = logging.getLogger(__name__)

@main.route('/')
def index():
    return render_template('connection_form.html')

@main.route('/set_connection', methods=['POST'])
def set_connection():
    db_type = request.form.get('db_type')
    server = request.form.get('server')
    database = request.form.get('database')
    username = request.form.get('username')
    password = request.form.get('password')
    schema = request.form.get('schema') or 'dbo'

    # Construct connection string
    try:
        if db_type == 'mssql':
            params = urllib.parse.quote_plus(
                f"DRIVER={{ODBC Driver 18 for SQL Server}};"
                f"SERVER={server};"
                f"DATABASE={database};"
                f"UID={username};"
                f"PWD={password};"
                "Encrypt=yes;TrustServerCertificate=yes;"
            )
            connection_string = f"mssql+pyodbc:///?odbc_connect={params}"
        elif db_type == 'postgresql':
            connection_string = f"postgresql+psycopg2://{username}:{password}@{server}/{database}"
        elif db_type == 'mysql':
            connection_string = f"mysql+mysqlconnector://{username}:{password}@{server}/{database}"
        else:
            raise ValueError('Unsupported database type')

        # Test connection and profile
        profile = profile_database(connection_string, schema=schema)
        
        session['connection_string'] = connection_string
        session['db_profile'] = profile
        session['schema_name'] = schema
        
        # Explicitly log the target of the redirect
        target = url_for('main.semantic_studio')
        logger.info(f"Connection successful. Redirecting to {target}")
        return redirect(target)
    except Exception as e:
        logger.error(f"Connection failed: {e}")
        return render_template('connection_form.html', error=f"Connection Error: {str(e)}")

@main.route('/semantic-studio')
def semantic_studio():
    profile = session.get('db_profile')
    if not profile:
        return redirect(url_for('main.index'))
    return render_template('semantic_studio.html', profile=profile)

@main.route('/publish-semantic-layer', methods=['POST'])
def publish_semantic_layer():
    active_tables = request.form.getlist('active_tables')
    full_profile = session.get('db_profile')
    
    # Prune inactive tables
    published_model = {
        "tables": {table: full_profile["tables"][table] for table in active_tables},
        "db_type": full_profile["db_type"]
    }
    
    session['published_semantic_layer'] = published_model
    logger.info(f"Published semantic layer: {published_model}")
    return redirect(url_for('main.chat_interface'))

@main.route('/chat')
def chat_interface():
    semantic_model = session.get('published_semantic_layer')
    
    # Debug logging
    logger.info(f"Accessing /chat - session keys: {list(session.keys())}")
    logger.info(f"Published semantic layer in session: {'yes' if semantic_model else 'no'}")
    print(f"DEBUG /chat - session keys: {list(session.keys())}")
    print(f"DEBUG /chat - published semantic layer: {'yes' if semantic_model else 'no'}")
    
    # If no published semantic layer exists, check if we have a database profile
    if not semantic_model:
        profile = session.get('db_profile')
        logger.info(f"Database profile in session: {'yes' if profile else 'no'}")
        print(f"DEBUG /chat - db_profile in session: {'yes' if profile else 'no'}")
        
        if not profile:
            logger.warning("Attempted to access /chat without database connection. Redirecting to index.")
            print("DEBUG /chat - No db_profile, redirecting to index")
            return redirect(url_for('main.index'))
        
        # Create a default semantic layer from the profile
        logger.info(f"Creating default semantic layer from database profile. Tables: {list(profile.get('tables', {}).keys())}")
        print(f"DEBUG /chat - Creating default semantic layer. Tables: {list(profile.get('tables', {}).keys())}")
        semantic_model = {
            "tables": profile["tables"],
            "db_type": profile["db_type"]
        }
        # Optionally store it in session for consistency
        session['published_semantic_layer'] = semantic_model
        session.modified = True  # Ensure session is saved
        logger.info("Default semantic layer created and stored in session")
        print("DEBUG /chat - Default semantic layer created and stored in session")
    
    return render_template('text_to_sql.html', schema=json.dumps(semantic_model))

@main.route('/debug-routes')
def debug_routes():
    """List all routes in the app"""
    output = []
    for rule in current_app.url_map.iter_rules():
        output.append({
            'endpoint': rule.endpoint,
            'methods': list(rule.methods),
            'rule': rule.rule
        })
    return jsonify(output)

@main.route('/debug-session')
def debug_session():
    """Debug endpoint to check session state"""
    session_info = {
        'keys': list(session.keys()),
        'has_db_profile': 'db_profile' in session,
        'has_published_semantic_layer': 'published_semantic_layer' in session,
        'has_connection_string': 'connection_string' in session,
    }
    if 'db_profile' in session:
        profile = session['db_profile']
        session_info['db_profile_tables'] = list(profile.get('tables', {}).keys()) if isinstance(profile, dict) else str(type(profile))
    return jsonify(session_info)

@main.route('/generate-sql', methods=['POST'])
def generate_sql():
    data = request.json
    question = data.get('question')
    sql_override = data.get('sql_override')
    published_model = session.get('published_semantic_layer')
    
    # Create service
    service = SQLChainService(session['connection_string'])
    
    # Generate SQL if not manually overridden
    schema_info = json.dumps(published_model)
    sql = sql_override if sql_override else service.generate_sql(question, schema_info)
    
    # Execute safely
    results = QueryExecutor.validate_and_execute(service.db, sql)
    
    # Simple summary for UI feedback
    row_count = len(results) if isinstance(results, list) else 0
    summary = f"The query returned {row_count} row{'s' if row_count != 1 else ''}. Use the grid above to inspect the results."
    
    logger.info(f"Generated SQL: {sql}")
    logger.info(f"Results type: {type(results)}, count: {row_count}")
    print(f"DEBUG /generate-sql -> SQL: {sql}")
    print(f"DEBUG /generate-sql -> results type: {type(results)}, count: {row_count}")
    print(f"DEBUG /generate-sql -> results: {results}")
    
    return jsonify({"sql": sql, "results": results, "summary": summary})

@main.route('/api-get-available-tables-xyz', methods=['GET'])
def get_available_tables():
    """Return list of available tables from the semantic layer"""
    print("DEBUG: /api-get-available-tables-xyz hit")
    logger.info(f"DEBUG /api-get-available-tables-xyz - session keys: {list(session.keys())}")
    logger.info(f"DEBUG /api-get-available-tables-xyz - published_semantic_layer in session: {'yes' if 'published_semantic_layer' in session else 'no'}")

    if 'published_semantic_layer' not in session:
        logger.warning("No semantic layer found in session for /get-available-tables")
        return jsonify({"tables": [], "error": "No semantic layer found"})
    
    published_model = session.get('published_semantic_layer')
    
    # Extract table names, filtering out system tables
    tables = []
    if isinstance(published_model, dict) and 'tables' in published_model:
        for table_name, table_data in published_model['tables'].items():
            # Filter out system tables (same logic as SQLChainService)
            if table_name.lower() not in {'user', 'users', 'sessions', 'alembic_version', 'sqlite_sequence'}:
                tables.append(table_name)
    
    logger.info(f"Returning {len(tables)} available tables")
    print(f"DEBUG /get-available-tables -> tables: {tables}")
    
    return jsonify({"tables": tables})
