"""Migration/Cleanup: Purge legacy table_permission rows that are NOT workspace-scoped.

Legacy rows with `workspace_id IS NULL` caused cross-tenant permission leakage
(the old get_member_allowed_tables fell back to them). With strict tenant
isolation now in place, these rows:

  * user_id IS NOT NULL  -> per-user legacy rows (deprecated but kept for
                            backward-compat reads when no workspace_id passed).
  * user_id IS NULL AND workspace_id IS NULL -> GLOBAL rows. These are deleted
                            so a member can never inherit another workspace's
                            (or a global) table list.

Migration also backfills every member user who has a permission row to ensure
their workspace has a (workspace_id, role='member') row so they can actually
access the chat after the strict-isolation change.

This script is idempotent and safe to run multiple times.
"""
import sqlite3
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'instance', 'dataconvo.db')


def table_exists(cursor, name):
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return cursor.fetchone() is not None


def main():
    if not os.path.exists(DB_PATH):
        print(f'Database not found: {DB_PATH}')
        sys.exit(1)

    print(f'Migrating: {DB_PATH}')
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    if not table_exists(cursor, 'table_permission'):
        print('  [skip] table_permission table does not exist — nothing to do.')
        conn.close()
        return

    # 1) Delete legacy GLOBAL rows (user_id IS NULL AND workspace_id IS NULL).
    #    These are the leak source — a member in ANY workspace could inherit them.
    cursor.execute(
        "DELETE FROM table_permission WHERE workspace_id IS NULL AND user_id IS NULL"
    )
    deleted_global = cursor.rowcount
    print(f'  [ok]   deleted {deleted_global} legacy global table_permission row(s)')

    # 2) Clean up orphaned per-user rows pointing at users who are no longer
    #    members of any workspace (workspace_id IS NULL on the user) — these
    #    can never be resolved under strict isolation anyway.
    cursor.execute(
        "DELETE FROM table_permission "
        "WHERE workspace_id IS NULL AND user_id IS NOT NULL "
        "AND user_id NOT IN (SELECT id FROM user WHERE workspace_id IS NOT NULL)"
    )
    deleted_orphan = cursor.rowcount
    print(f'  [ok]   deleted {deleted_orphan} orphaned per-user table_permission row(s)')

    # 3) Backfill: for every user assigned to a workspace with role='member',
    #    ensure a (workspace_id, role='member') permission row exists so they
    #    can access the chat immediately after the strict-isolation change.
    cursor.execute(
        "SELECT DISTINCT u.workspace_id FROM user u "
        "WHERE u.role='member' AND u.workspace_id IS NOT NULL"
    )
    ws_ids = [row[0] for row in cursor.fetchall()]
    for ws_id in ws_ids:
        cursor.execute(
            "SELECT id FROM table_permission WHERE workspace_id=? AND role='member'",
            (ws_id,),
        )
        if cursor.fetchone() is None:
            cursor.execute(
                "INSERT INTO table_permission (workspace_id, user_id, role, allowed_tables, updated_at) "
                "VALUES (?, NULL, 'member', '[]', datetime('now'))",
                (ws_id,),
            )
            print(f'  [ok]   created empty workspace-scoped member permission row for workspace_id={ws_id}')

    conn.commit()
    conn.close()
    print('\nMigration complete.')


if __name__ == '__main__':
    main()