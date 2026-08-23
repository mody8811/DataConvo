"""Migration: Add workspace_id column to table_permission (legacy schema fix).

The legacy `table_permission` table was created as:
    (id, user_id, role, allowed_tables, updated_at)

It is MISSING the `workspace_id` column that the SQLAlchemy model
(app/models.py TablePermission) and every RBAC query reference. Without this
column, saving/reading workspace-scoped permissions raises
"OperationalError: no such column: table_permission.workspace_id",
which is exactly why members are blocked from the chat.

This script:
  1. Adds `workspace_id INTEGER` if missing.
  2. Backfills workspace_id from user.workspace_id for legacy per-user rows.
  3. Deletes legacy global rows (both user_id and workspace_id NULL).
  4. Backfills a workspace-wide member row for every workspace with members.

Idempotent — safe to run multiple times.
"""
import sqlite3
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'instance', 'dataconvo.db')


def table_exists(cursor, name):
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return cursor.fetchone() is not None


def column_exists(cursor, table, column):
    cursor.execute(f'PRAGMA table_info({table})')
    return any(row[1] == column for row in cursor.fetchall())


def main():
    if not os.path.exists(DB_PATH):
        print(f'Database not found: {DB_PATH}')
        sys.exit(1)

    print(f'Migrating: {DB_PATH}')
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    if not table_exists(cursor, 'table_permission'):
        print('  [skip] table_permission table does not exist.')
        conn.close()
        return

    # 1) Add workspace_id column if missing.
    if column_exists(cursor, 'table_permission', 'workspace_id'):
        print('  [skip] table_permission.workspace_id already exists')
    else:
        cursor.execute('ALTER TABLE table_permission ADD COLUMN workspace_id INTEGER')
        print('  [ok]   added table_permission.workspace_id column')

    # 2) Backfill workspace_id from user.workspace_id for legacy per-user rows.
    cursor.execute(
        "UPDATE table_permission "
        "SET workspace_id = (SELECT u.workspace_id FROM user u WHERE u.id = table_permission.user_id) "
        "WHERE workspace_id IS NULL AND user_id IS NOT NULL "
        "AND EXISTS (SELECT 1 FROM user u WHERE u.id = table_permission.user_id AND u.workspace_id IS NOT NULL)"
    )
    print(f'  [ok]   backfilled workspace_id on {cursor.rowcount} legacy per-user row(s)')

    # 3) Delete legacy global rows (both NULL) - the leak source.
    cursor.execute(
        "DELETE FROM table_permission WHERE workspace_id IS NULL AND user_id IS NULL"
    )
    print(f'  [ok]   deleted {cursor.rowcount} legacy global row(s)')

    # 3b) Delete orphaned per-user rows that still have no workspace.
    cursor.execute(
        "DELETE FROM table_permission WHERE workspace_id IS NULL AND user_id IS NOT NULL"
    )
    print(f'  [ok]   deleted {cursor.rowcount} orphaned per-user row(s)')

    # 4) Backfill workspace-wide member row for every workspace that has a
    #    published semantic layer (so the admin's member-tables flow works
    #    even before any member joins), and for workspaces with members.
    cursor.execute(
        "SELECT DISTINCT u.workspace_id FROM user u "
        "WHERE (u.role='member' AND u.workspace_id IS NOT NULL) "
        "   OR u.workspace_id IN (SELECT p.user_id FROM published_model p)"
    )
    ws_ids = [row[0] for row in cursor.fetchall()]
    created = 0
    for ws_id in ws_ids:
        cursor.execute(
            "SELECT id FROM table_permission WHERE workspace_id=? AND role='member' AND user_id IS NULL",
            (ws_id,),
        )
        if cursor.fetchone() is None:
            cursor.execute(
                "INSERT INTO table_permission (workspace_id, user_id, role, allowed_tables, updated_at) "
                "VALUES (?, NULL, 'member', '[]', datetime('now'))",
                (ws_id,),
            )
            created += 1
            print(f'  [ok]   created workspace-wide member row for workspace_id={ws_id}')

    conn.commit()
    conn.close()
    print(f'Migration complete. ({created} workspace row(s) created.)')


if __name__ == '__main__':
    main()