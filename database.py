import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "sync_history.db")

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS synced_materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic_id TEXT NOT NULL,
            message_id INTEGER NOT NULL,
            material_type TEXT NOT NULL,
            title TEXT NOT NULL,
            subject_id INTEGER NOT NULL,
            fcis_material_id INTEGER,
            file_name TEXT,
            synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(topic_id, message_id)
        )
    """)
    return conn

def init_db():
    with get_connection() as conn:
        conn.commit()

def is_already_synced(topic_id: str, message_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 1 FROM synced_materials 
            WHERE topic_id = ? AND message_id = ?
        """, (str(topic_id), int(message_id)))
        return cursor.fetchone() is not None

def record_synced(topic_id: str, message_id: int, material_type: str, title: str, subject_id: int, fcis_material_id: int = None, file_name: str = None):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO synced_materials 
            (topic_id, message_id, material_type, title, subject_id, fcis_material_id, file_name, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (str(topic_id), int(message_id), material_type, title, subject_id, fcis_material_id, file_name, datetime.utcnow()))
        conn.commit()
