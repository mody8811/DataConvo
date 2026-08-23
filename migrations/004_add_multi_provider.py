"""Migration: Add universal multi-provider BYOK metadata to the `user` table.

Adds `llm_provider`, `llm_base_url`, and `llm_model_id` columns so users can
configure OpenAI, Anthropic, OpenRouter, Google Gemini, or any custom
OpenAI-compatible endpoint (Cline-style universal BYOK).
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

    print('Adding multi-provider BYOK columns to "user" table:')
    add_column(cursor, 'user', 'llm_provider', "VARCHAR(30) NOT NULL DEFAULT 'openai'")
    add_column(cursor, 'user', 'llm_base_url', 'VARCHAR(500)')
    add_column(cursor, 'user', 'llm_model_id', 'VARCHAR(200)')

    # Backfill existing rows: llm_provider='openai' where NULL/empty
    cursor.execute("UPDATE user SET llm_provider = 'openai' WHERE llm_provider IS NULL OR llm_provider = ''")

    conn.commit()
    conn.close()
    print('\nMigration complete.')


if __name__ == '__main__':
    main()