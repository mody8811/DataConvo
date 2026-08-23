from langchain_community.utilities import SQLDatabase
from sqlalchemy import create_engine

class DatabaseManager:
    @staticmethod
    def get_sql_database(connection_string):
        engine = create_engine(connection_string)
        return SQLDatabase(engine)
