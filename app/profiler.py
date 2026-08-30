from sqlalchemy import create_engine, inspect, text
import pandas as pd
import logging
from datetime import date, datetime

logger = logging.getLogger(__name__)


def json_serializable(val):
    if isinstance(val, (date, datetime)):
        return val.isoformat()
    return str(val)


def _schemas_for_dialect(inspector):
    """Return the list of schemas to introspect, based on the dialect."""
    dialect = inspector.dialect.name
    try:
        schemas = inspector.get_schema_names()
    except Exception as e:
        logger.warning(f"Could not get schema names for {dialect}: {e}")
        return [None]

    # Normalize: Snowflake/Fabric return TUPLE rows like (database, schema)
    # instead of bare strings. Flatten to the actual schema name so it never
    # leaks into SQL as "<str>..None".
    def _schema_name(s):
        if isinstance(s, (list, tuple)):
            s = s[1] if len(s) > 1 else (s[0] if s else None)
        return s

    schemas = [s for s in (_schema_name(x) for x in schemas) if s]

    # Filter out system schemas per dialect
    system_schemas = {
        'postgresql': {'pg_catalog', 'information_schema'},
        'mssql': {'sys', 'INFORMATION_SCHEMA', 'guest', 'db_owner', 'db_accessadmin', 'db_securityadmin', 'db_ddladmin', 'db_backupoperator', 'db_datareader', 'db_datawriter', 'db_denydatareader', 'db_denydatawriter'},
        'mysql': {'information_schema', 'performance_schema', 'mysql', 'sys'},
        'snowflake': {'INFORMATION_SCHEMA', 'information_schema', 'PUBLIC'},
        'sqlite': set(),  # SQLite has no real schemas; uses main/temp
    }
    exclude = system_schemas.get(dialect, set())
    filtered = [s for s in schemas if s not in exclude]
    # Snowflake: keep PUBLIC (the default user schema) as the FIRST candidate so
    # unqualified access works against the database the user authenticated into.
    if dialect == 'snowflake' and 'PUBLIC' not in filtered and 'PUBLIC' in schemas:
        filtered.insert(0, 'PUBLIC')
    return filtered or [None]  # fall back to default / no schema


def introspect_foreign_keys(engine, schema=None):
    """Use SQLAlchemy Inspector to get foreign keys across dialects."""
    inspector = inspect(engine)
    dialect = engine.dialect.name
    fk_constraints = []

    schemas = [schema] if schema else _schemas_for_dialect(inspector)

    for sch in schemas:
        try:
            tables = inspector.get_table_names(schema=sch)
        except Exception:
            try:
                tables = inspector.get_table_names()
            except Exception as e:
                logger.warning(f"Could not list tables for schema {sch}: {e}")
                tables = []

        for table in tables:
            try:
                fks = inspector.get_foreign_keys(table, schema=sch)
                for fk in fks:
                    # Normalize names across dialects
                    from_cols = fk.get('constrained_columns', []) or []
                    to_cols = fk.get('referred_columns', []) or []
                    if not from_cols or not to_cols:
                        continue
                    fk_constraints.append({
                        "from_table": fk.get('constrained_table') or table,
                        "from_column": from_cols[0],
                        "to_table": fk.get('referred_table') or (fk.get('referred_schema') + '.' + fk['referred_table'] if fk.get('referred_table') and fk.get('referred_schema') else fk.get('referred_table', '')),
                        "to_column": to_cols[0],
                        "constraint_name": fk.get('name') or f"fk_{table}_{from_cols[0]}"
                    })
            except Exception as e:
                logger.debug(f"Foreign keys for {table} failed: {e}")

    # De-dup (some dialects return duplicates when cross-schema)
    seen = set()
    unique_fks = []
    for fk in fk_constraints:
        key = (fk['from_table'], fk['from_column'], fk['to_table'], fk['to_column'])
        if key not in seen:
            seen.add(key)
            unique_fks.append(fk)
    return unique_fks


def introspect_primary_keys(engine, schema=None):
    """Use SQLAlchemy Inspector to get primary keys across dialects."""
    inspector = inspect(engine)
    dialect = engine.dialect.name
    pk_map = {}

    schemas = [schema] if schema else _schemas_for_dialect(inspector)

    for sch in schemas:
        try:
            tables = inspector.get_table_names(schema=sch)
        except Exception:
            try:
                tables = inspector.get_table_names()
            except Exception as e:
                logger.warning(f"Could not list tables for schema {sch}: {e}")
                tables = []

        for table in tables:
            try:
                pk = inspector.get_pk_constraint(table, schema=sch)
                cols = pk.get('constrained_columns', []) or []
                if cols:
                    # Use the fully-qualified table name to avoid cross-schema collisions
                    display_name = f"{sch}.{table}" if sch and dialect in ('postgresql', 'mssql', 'databricks') else table
                    pk_map[display_name] = cols
                    # Also store under the bare name for backward compatibility
                    if table not in pk_map:
                        pk_map[table] = cols
            except Exception as e:
                logger.debug(f"Primary key for {table} failed: {e}")

    return pk_map


def _build_connection_string_for(dialect, **kwargs):
    """Build a SQLAlchemy connection string for the given dialect."""
    if dialect == 'postgresql':
        return f"postgresql+psycopg2://{kwargs['username']}:{kwargs['password']}@{kwargs['server']}/{kwargs['database']}"
    elif dialect == 'mysql':
        return f"mysql+mysqlconnector://{kwargs['username']}:{kwargs['password']}@{kwargs['server']}/{kwargs['database']}"
    elif dialect == 'mssql':
        import urllib.parse
        params = urllib.parse.quote_plus(
            f"DRIVER={{ODBC Driver 18 for SQL Server}};"
            f"SERVER={kwargs['server']};"
            f"DATABASE={kwargs['database']};"
            f"UID={kwargs['username']};"
            f"PWD={kwargs['password']};"
            "Encrypt=yes;TrustServerCertificate=yes;"
        )
        return f"mssql+pyodbc:///?odbc_connect={params}"
    elif dialect == 'sqlite':
        # SQLite: server field is the file path
        path = kwargs.get('database') or kwargs.get('server') or ':memory:'
        return f"sqlite:///{path}"
    elif dialect == 'bigquery':
        # BigQuery: google-cloud-sqlalchemy required
        # server is the project, database is the dataset
        return (
            f"bigquery://{kwargs['server']}/{kwargs['database']}"
            f"?credentials_path={kwargs.get('credentials_path', '')}"
        ) if kwargs.get('credentials_path') else f"bigquery://{kwargs['server']}/{kwargs['database']}"
    elif dialect == 'snowflake':
        # Snowflake: server is the account identifier
        return f"snowflake://{kwargs['username']}:{kwargs['password']}@{kwargs['server']}/{kwargs['database']}"
    elif dialect == 'redshift':
        # Redshift: postgres-compatible
        return f"redshift+psycopg2://{kwargs['username']}:{kwargs['password']}@{kwargs['server']}/{kwargs['database']}"
    elif dialect == 'databricks':
        # Databricks SQL Warehouse: databricks-sql-connector + databricks-sqlalchemy.
        # URI: databricks://token:<token>@<host>?http_path=<http_path>&catalog=<catalog>&schema=<schema>
        import urllib.parse
        token = kwargs.get('username') or kwargs.get('password') or ''
        host = kwargs.get('server') or ''
        http_path = urllib.parse.quote(kwargs.get('http_path') or '/sql/1.0/warehouses/default', safe='')
        catalog = urllib.parse.quote(kwargs.get('catalog') or 'main', safe='')
        schema = urllib.parse.quote(kwargs.get('schema') or 'default', safe='')
        return (
            f"databricks://token:{token}@{host}"
            f"?http_path={http_path}&catalog={catalog}&schema={schema}"
        )
    else:
        raise ValueError(f"Unsupported database type: {dialect}")


def discover_connection(engine, schema=None):
    """Discover tables, columns, and types using SQLAlchemy Inspector.

    Returns:
        {
            "db_type": dialect name,
            "tables": {table_name: {"columns": {col: {"type": str}}, "sample": [...]}},
            "fk_constraints": [...],
            "primary_keys": {...}
        }
    """
    inspector = inspect(engine)
    dialect = engine.dialect.name
    profile = {
        "tables": {},
        "db_type": dialect,
        "fk_constraints": [],
        "primary_keys": {}
    }

    # Use the actively-selected schema when supplied (e.g. Snowflake PUBLIC);
    # otherwise fall back to the dialect's discovered schema list.
    if schema:
        schemas = [schema]
    else:
        schemas = _schemas_for_dialect(inspector)

    # Get FKs and PKs via Inspector (dialect-agnostic) with the same schema.
    profile["fk_constraints"] = introspect_foreign_keys(engine, schema=schema)
    profile["primary_keys"] = introspect_primary_keys(engine, schema=schema)

    for sch in schemas:
        try:
            table_names = inspector.get_table_names(schema=sch)
        except Exception:
            try:
                table_names = inspector.get_table_names()
            except Exception as e:
                logger.warning(f"Could not list tables: {e}")
                table_names = []

        for table in table_names:
            # Compute display name
            display_name = table
            schema_display = None
            if dialect == 'snowflake' and sch:
                # Snowflake: ALWAYS schema-qualify (e.g. PUBLIC.olist_sellers_dataset)
                # so generated SQL targets the user-selected schema instead of the
                # connection default — mirrors the Databricks catalog.schema.table
                # qualification below.
                display_name = f"{sch}.{table}"
                schema_display = sch
            elif sch and sch not in ('main', 'public', 'dbo'):
                # Keep schema-qualified for non-default schemas so queries target the right one
                if dialect in ('postgresql', 'mssql'):
                    display_name = f"{sch}.{table}"
                    schema_display = sch
                elif dialect == 'databricks':
                    # Databricks get_schema_names() returns "catalog.schema" (two-level).
                    # Qualify table names as catalog.schema.table so generated SQL
                    # never falls back to the default catalog (peoplecert.default).
                    display_name = f"{sch}.{table}"
                    schema_display = sch
            elif sch in ('public', 'dbo'):
                display_name = table  # Don't prefix default schemas

            try:
                columns_raw = inspector.get_columns(table, schema=sch)
                columns = {}
                for col in columns_raw:
                    col_name = col.get('name', '')
                    col_type = col.get('type', '')
                    # Convert type to string safely
                    type_str = str(col_type) if col_type is not None else 'unknown'
                    columns[col_name] = {"type": type_str}

                # Fetch sample rows with dialect-aware LIMIT
                try:
                    sample_df = _fetch_sample(engine, sch, table, dialect, limit=5)
                    cleaned_samples = [_clean_row(r) for r in sample_df.to_dict(orient='records')]
                except Exception as e:
                    logger.warning(f"Could not sample table {table}: {e}")
                    cleaned_samples = []

                profile["tables"][display_name] = {
                    "columns": columns,
                    "sample": cleaned_samples,
                    "schema": schema_display  # optional metadata
                }
            except Exception as e:
                logger.warning(f"Could not profile table {table}: {e}")
                profile["tables"][display_name] = {"columns": {}, "sample": [], "schema": schema_display}

    return profile


def _fetch_sample(engine, schema, table, dialect, limit=5):
    """Fetch a small sample of rows from a table using dialect-safe LIMIT syntax."""
    if schema and schema not in ('main', 'public', 'dbo') and dialect in ('postgresql', 'mssql'):
        qualified = f'"{schema}"."{table}"' if dialect == 'postgresql' else f"[{schema}].[{table}]"
    elif dialect in ('databricks', 'snowflake') and schema:
        # Snowflake/Databricks identifiers are case-insensitive; backtick-qualify.
        qualified = f"`{schema}`.`{table}`"
    else:
        qualified = f'"{table}"' if dialect in ('postgresql', 'sqlite') else f"[{table}]" if dialect == 'mssql' else f"`{table}`"

    if dialect in ('postgresql', 'mysql', 'sqlite', 'bigquery'):
        query = text(f"SELECT * FROM {qualified} LIMIT {limit}")
    elif dialect == 'mssql':
        query = text(f"SELECT TOP {limit} * FROM {qualified}")
    else:
        # Generic fallback
        query = text(f"SELECT * FROM {qualified} LIMIT {limit}")

    try:
        return pd.read_sql(query, engine)
    except Exception as e:
        logger.warning(f"Sample fetch failed for {qualified}: {e}")
        return pd.DataFrame()


def _clean_row(row):
    """Ensure all values are JSON-serializable."""
    cleaned = {}
    for k, v in row.items():
        if isinstance(v, (date, datetime)):
            cleaned[k] = v.isoformat()
        elif v is None:
            cleaned[k] = None
        elif isinstance(v, (str, int, float, bool)):
            cleaned[k] = v
        else:
            cleaned[k] = str(v)
    return cleaned


def profile_database(connection_string, schema=None):
    """Main entry point. Uses SQLAlchemy Inspector for dialect-agnostic schema reflection."""
    engine = create_engine(connection_string)
    return discover_connection(engine, schema=schema)