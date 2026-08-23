"""Migration: Create the dashboard_widget table for pinned BI visualizations.

Idempotent — safe to run multiple times. Creates the table only if it does not
already exist, preserving any existing data.
"""
import sqlite3
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'instance', 'dataconvo.db')


def table_exists(cursor, table):
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cursor.fetchone() is not None


def main():
    if not os.path.exists(DB_PATH):
        print(f'Database not found: {DB_PATH}')
        sys.exit(1)

    print(f'Migrating: {DB_PATH}')
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    if table_exists(cursor, 'dashboard_widget'):
        print('  [skip] dashboard_widget table already exists')
    else:
        cursor.execute('''
            CREATE TABLE dashboard_widget (
                id VARCHAR(36) PRIMARY KEY,
                user_id INTEGER NOT NULL,
                title VARCHAR(200) NOT NULL,
                sql_query TEXT NOT NULL,
                chart_config TEXT NOT NULL DEFAULT '{}',
                result_metadata TEXT NOT NULL DEFAULT '{}',
                created_at DATETIME,
                updated_at DATETIME
            )
        ''')
        print('  [ok]   created dashboard_widget table')

    # Add connection_string to published_model for background execution context
    print('Adding connection_string to "published_model" table:')
    cursor.execute('PRAGMA table_info(published_model)')
    pm_cols = {row[1] for row in cursor.fetchall()}
    if 'connection_string' in pm_cols:
        print('  [skip] published_model.connection_string already exists')
    else:
        cursor.execute('ALTER TABLE published_model ADD COLUMN connection_string TEXT')
        print('  [ok]   added published_model.connection_string')

    # Ensure an index on user_id for fast per-user lookups
    cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='ix_dashboard_widget_user_id'")
    if cursor.fetchone():
        print('  [skip] index ix_dashboard_widget_user_id already exists')
    else:
        cursor.execute('CREATE INDEX ix_dashboard_widget_user_id ON dashboard_widget (user_id)')
        print('  [ok]   created index ix_dashboard_widget_user_id')

    conn.commit()
    conn.close()
    print('\nMigration complete.')


if __name__ == '__main__':
    main()