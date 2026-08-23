import sqlite3
import hashlib
import json
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'sql_cache.db')

def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sql_cache (
            prompt_hash TEXT PRIMARY KEY,
            generated_sql TEXT NOT NULL,
            related_questions TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    return conn

def normalize_prompt(prompt: str) -> str:
    return prompt.strip().lower()

def hash_prompt(prompt: str) -> str:
    return hashlib.sha256(normalize_prompt(prompt).encode('utf-8')).hexdigest()

def get_cached_sql(prompt: str):
    prompt_hash = hash_prompt(prompt)
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT generated_sql, related_questions FROM sql_cache WHERE prompt_hash = ?",
            (prompt_hash,)
        ).fetchone()
        if row:
            return {
                "generated_sql": row[0],
                "related_questions": json.loads(row[1])
            }
        return None
    finally:
        conn.close()

def set_cached_sql(prompt: str, generated_sql: str, related_questions: list):
    prompt_hash = hash_prompt(prompt)
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO sql_cache (prompt_hash, generated_sql, related_questions, created_at) VALUES (?, ?, ?, ?)",
            (prompt_hash, generated_sql, json.dumps(related_questions), datetime.utcnow().isoformat())
        )
        conn.commit()
    finally:
        conn.close()

def clear_cache():
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM sql_cache")
        conn.commit()
    finally:
        conn.close()
