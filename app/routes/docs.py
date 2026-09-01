"""Documentation hub: infographic guides for every data source + workflows."""
from flask import Blueprint, render_template, abort

docs = Blueprint('docs', __name__)

SOURCES = {
    'snowflake': dict(
        name='Snowflake', icon='❄️', tag='Cloud Data',
        fields=[('Account Identifier', 'xy12345.us-east-1'), ('Port', '443'),
                ('Database', 'my_db'), ('Schema', 'PUBLIC'),
                ('User', 'readonly_user'), ('Password', '••••••••')],
        url='snowflake://{user}:{password}@{account}/{database}',
        extra='snowflake-sqlalchemy is bundled with Data Convo. Use the Account Identifier (not the full URL) in the connection form.'),
    'postgresql': dict(
        name='PostgreSQL', icon='🐘', tag='Relational',
        fields=[('Host', 'db.example.com'), ('Port', '5432'),
                ('Database', 'my_db'), ('Schema', 'public'),
                ('User', 'readonly_user'), ('Password', '••••••••')],
        url='postgresql://{user}:{password}@{host}:{port}/{database}',
        extra='Create a read-only role for safety: GRANT SELECT ON ALL TABLES IN SCHEMA public TO readonly_user;'),
    'mysql': dict(
        name='MySQL', icon='🐬', tag='Relational',
        fields=[('Host', 'mysql.example.com'), ('Port', '3306'),
                ('Database', 'my_db'), ('User', 'readonly_user'), ('Password', '••••••••')],
        url='mysql+pymysql://{user}:{password}@{host}:{port}/{database}',
        extra='PyMySQL ships by default. Add the IPs listed on the connect page to your firewall whitelist.'),
    'sqlserver': dict(
        name='Microsoft SQL Server', icon='🗄️', tag='Enterprise',
        fields=[('Host', 'sql.example.com\\MSSQL2016'), ('Port', '1433'),
                ('Database', 'my_db'), ('Schema', 'dbo'),
                ('User', 'readonly_user'), ('Password', '••••••••')],
        url='mssql+pyodbc://{user}:{password}@{host}:{port}/{database}?driver=ODBC+Driver+18+for+SQL+Server',
        extra='Uses the ODBC Driver 18; the ODBC driver must be installed on the server host.'),
    'sqlite': dict(
        name='SQLite', icon='📁', tag='Local / Embedded',
        fields=[('File Path', '/path/to/app.db'), ('Port', '—'), ('Database', '—'),
                ('User', '(none)'), ('Password', '(none)')],
        url='sqlite:///app.db',
        extra='Point the connection form at the physical .db file path. Zero migration needed.'),
    'databricks': dict(
        name='Databricks', icon='💻', tag='Lakehouse',
        fields=[('Server Hostname', 'adb-123.456.7.8.9.azuredatabricks.net'), ('HTTP Path', '/sql/1.0/warehouses/xxx'),
                ('Port', '443'), ('Catalog', 'main'), ('Schema', 'default'), ('Token', 'dapi••••')],
        url='databricks://token:{token}@{host}?http_path={http_path}&catalog={catalog}&schema={schema}',
        extra='Found in Compute → your SQL Warehouse → Connection details → Serverless HTTP path.'),
    'bigquery': dict(
        name='Google BigQuery', icon='🔍', tag='Cloud Data',
        fields=[('Project ID', 'my-gcp-project'), ('Port', '443'), ('Dataset', 'analytics'),
                ('Service Account', 'SA JSON credentials')],
        url='bigquery://{project}/?credentials_path={credentials_path}',
        extra='Upload a service-account JSON with BigQuery Data Viewer + Job User roles.'),
    'redshift': dict(
        name='Amazon Redshift', icon='🔺', tag='Cloud Data',
        fields=[('Cluster Endpoint', 'cluster.region.redshift.amazonaws.com'), ('Port', '5439'),
                ('Database', 'dev'), ('Schema', 'public'),
                ('User', 'readonly_user'), ('Password', '••••••••')],
        url='redshift+psycopg2://{user}:{password}@{host}:{port}/{database}',
        extra='Redshift speaks the PostgreSQL protocol, so psycopg2 handles it as a postgres dialect.'),
}

WORKFLOWS = {
    'semantic-layer': dict(
        name='Semantic Layer Studio', icon='🗺️', tag='Build your AI-ready model',
        steps=[
            ('Connect', 'Add your database from the connection form — fields auto-adjust per engine.'),
            ('Profile', 'Data Convo introspects tables, columns, and PK/FK constraints automatically.'),
            ('Model visually', 'Drag columns to link tables in the ERD canvas — magenta threads confirm every join.'),
            ('Define business logic', 'Add aliases, synonyms, enums, custom metrics, and Global Business Definitions.'),
            ('Publish', 'The model is persisted to model_json and injected into every LLM prompt.'),
            ('Chat & dashboards', 'Ask plain-English questions; the AI embeds your joins and definitions.'),
        ]),
    'anomaly': dict(
        name='Anomaly Studio', icon='🚨', tag='Continuous monitoring',
        steps=[
            ('Connect & pick tables', 'Choose the tables in your monitored schema to watch.'),
            ('Build a baseline', 'Historical stats per column: distribution, volume, type checks.'),
            ('Detect shifts', 'Null spikes, volume changes, and type mismatches flagged ±3σ from baseline.'),
            ('Alert automatically', 'Email / Slack webhook fired the moment a metric deviates.'),
            ('See it on the dashboard', 'Live status pulses + historical trend cards keep teams informed.'),
        ]),
}


@docs.route('/docs')
def docs_home():
    return render_template('docs.html', page='home', sources=SOURCES,
                           workflows=WORKFLOWS)


@docs.route('/docs/sources/<source_key>')
def docs_source(source_key):
    src = SOURCES.get(source_key)
    if not src:
        abort(404)
    return render_template('docs.html', page='source', sources=SOURCES,
                           workflows=WORKFLOWS, source=src, source_key=source_key)


@docs.route('/docs/<workflow_key>')
def docs_workflow(workflow_key):
    wf = WORKFLOWS.get(workflow_key)
    if not wf:
        abort(404)
    return render_template('docs.html', page='workflow', sources=SOURCES,
                           workflows=WORKFLOWS, workflow=wf, workflow_key=workflow_key)
