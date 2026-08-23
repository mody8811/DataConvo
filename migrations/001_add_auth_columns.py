"""Migration: Safely add auth/subscription columns to the existing `user` table.

Preserves all legacy data. Uses ALTER TABLE ADD COLUMN (idempotent) for each
new column so existing rows and indexes remain intact.
"""
import sqlite3
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'instance', 'dataconvo.db')


def column_exists(cursor, table, column):
    cursor.execute(f'PRAGMA table_info({table})')
    return any(row[1] == column for row in cursor.fetchall())


def add_column(cursor, table, column, ddl):
    if column_exists(cursor, table, column):
        print(f'  [skip] {table}.{column} already exists')
        return
    cursor.execute(f'ALTER TABLE {table} ADD COLUMN {column} {ddl}')
    print(f'  [ok]   added {table}.{column}')


def main():
    if not os.path.exists(DB_PATH):
        print(f'Database not found: {DB_PATH}')
        sys.exit(1)

    print(f'Migrating: {DB_PATH}')
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    print('Adding columns to "user" table:')
    add_column(cursor, 'user', 'password_hash', 'VARCHAR(256)')
    add_column(cursor, 'user', 'is_active', 'BOOLEAN DEFAULT 1')
    add_column(cursor, 'user', 'stripe_customer_id', 'VARCHAR(120)')
    add_column(cursor, 'user', 'subscription_tier', 'VARCHAR(20) DEFAULT \'free\'')
    add_column(cursor, 'user', 'created_at', 'DATETIME')

    # Backfill existing rows: is_active=1, subscription_tier='free' where NULL
    cursor.execute("UPDATE user SET is_active = 1 WHERE is_active IS NULL")
    cursor.execute("UPDATE user SET subscription_tier = 'free' WHERE subscription_tier IS NULL OR subscription_tier = ''")

    # Add user_id column to saved_query if not present (account isolation)
    print('Adding columns to "saved_query" table:')
    add_column(cursor, 'saved_query', 'user_id', 'INTEGER')

    # Create published_model table if not exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='published_model'")
    if cursor.fetchone():
        print('  [skip] published_model table already exists')
    else:
        cursor.execute('''
            CREATE TABLE published_model (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                model_json TEXT NOT NULL,
                db_type VARCHAR(20),
                created_at DATETIME,
                updated_at DATETIME
            )
        ''')
        print('  [ok]   created published_model table')

    conn.commit()
    conn.close()
    print('\nMigration complete.')


if __name__ == '__main__':
    main()