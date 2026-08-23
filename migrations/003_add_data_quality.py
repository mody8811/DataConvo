"""Migration: Create the data-quality monitoring tables for Anomaly Studio.

Idempotent — safe to run multiple times. Creates `data_quality_monitor` and
`data_quality_incident` only if they do not already exist, and adds the
`notification_channels` column (alert destinations) to existing monitors.
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

    if table_exists(cursor, 'data_quality_monitor'):
        print('  [skip] data_quality_monitor table already exists')
    else:
        cursor.execute('''
            CREATE TABLE data_quality_monitor (
                id VARCHAR(36) PRIMARY KEY,
                user_id INTEGER NOT NULL,
                table_name VARCHAR(255) NOT NULL,
                check_type VARCHAR(30) NOT NULL,
                parameters TEXT NOT NULL DEFAULT '{}',
                notification_channels TEXT,
                schedule_cron VARCHAR(64),
                frequency VARCHAR(30),
                severity VARCHAR(20) DEFAULT 'warning' NOT NULL,
                is_active BOOLEAN DEFAULT 1 NOT NULL,
                created_at DATETIME,
                updated_at DATETIME
            )
        ''')
        print('  [ok]   created data_quality_monitor table')

    if table_exists(cursor, 'data_quality_incident'):
        print('  [skip] data_quality_incident table already exists')
    else:
        cursor.execute('''
            CREATE TABLE data_quality_incident (
                id VARCHAR(36) PRIMARY KEY,
                monitor_id VARCHAR(36) NOT NULL,
                status VARCHAR(20) DEFAULT 'failing' NOT NULL,
                error_message TEXT NOT NULL DEFAULT '',
                failed_rows_count INTEGER,
                triggered_at DATETIME,
                created_at DATETIME
            )
        ''')
        print('  [ok]   created data_quality_incident table')

    # Indexes for fast tenant + incident lookups
    cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='ix_data_quality_monitor_user_id'")
    if cursor.fetchone():
        print('  [skip] index ix_data_quality_monitor_user_id already exists')
    else:
        cursor.execute('CREATE INDEX ix_data_quality_monitor_user_id ON data_quality_monitor (user_id)')
        print('  [ok]   created index ix_data_quality_monitor_user_id')

    cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='ix_data_quality_incident_monitor_id'")
    if cursor.fetchone():
        print('  [skip] index ix_data_quality_incident_monitor_id already exists')
    else:
        cursor.execute('CREATE INDEX ix_data_quality_incident_monitor_id ON data_quality_incident (monitor_id)')
        print('  [ok]   created index ix_data_quality_incident_monitor_id')

    # Add notification_channels to data_quality_monitor (alert destinations)
    print('Adding notification_channels to "data_quality_monitor" table:')
    cursor.execute('PRAGMA table_info(data_quality_monitor)')
    dq_cols = {row[1] for row in cursor.fetchall()}
    if 'notification_channels' in dq_cols:
        print('  [skip] data_quality_monitor.notification_channels already exists')
    else:
        cursor.execute('ALTER TABLE data_quality_monitor ADD COLUMN notification_channels TEXT')
        print('  [ok]   added data_quality_monitor.notification_channels')

    conn.commit()
    conn.close()
    print('\nMigration complete.')


if __name__ == '__main__':
    main()