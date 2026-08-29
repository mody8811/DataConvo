from langchain_community.utilities import SQLDatabase
from sqlalchemy import create_engine

class DatabaseManager:
    @staticmethod
    def get_sql_database(connection_string, schema=None):
        """Create engine + LangChain SQLDatabase.

        Forward schema so flavour-specific introspection (e.g. Snowflake
        `SHOW TABLES IN SCHEMA "DB"."SCHEMA"`) uses the user-selected
        schema instead of "None".
        """
        engine = create_engine(connection_string)
        if schema:
            return SQLDatabase(engine, schema=str(schema))
        return SQLDatabase(engine)
