"""Migration: Add role/expires/email_sent columns to team_invite table."""
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

    print('Adding invite-details columns to "team_invite" table:')
    add_column(cursor, 'team_invite', 'role', "VARCHAR(20) NOT NULL DEFAULT 'member'")
    add_column(cursor, 'team_invite', 'expires_at', 'DATETIME')
    add_column(cursor, 'team_invite', 'accepted_by', 'INTEGER')
    add_column(cursor, 'team_invite', 'email_sent', 'BOOLEAN DEFAULT 0')

    conn.commit()
    conn.close()
    print('\nMigration complete.')


if __name__ == '__main__':
    main()