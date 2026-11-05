import sqlite3
import os
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(__file__), "line_assistant.db")

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    # ตารางกลุ่มงานที่ลงทะเบียน
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS registered_groups (
            group_id TEXT PRIMARY KEY,
            group_name TEXT,
            registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active INTEGER DEFAULT 1
        )
    """)
    # ตารางบันทึกข้อความแชท
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id TEXT,
            user_id TEXT,
            user_name TEXT,
            message_text TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (group_id) REFERENCES registered_groups (group_id)
        )
    """)
    conn.commit()
    conn.close()

def register_group(group_id: str, group_name: str = "กลุ่มงาน"):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO registered_groups (group_id, group_name, is_active)
        VALUES (?, ?, 1)
        ON CONFLICT(group_id) DO UPDATE SET is_active=1, group_name=excluded.group_name
    """, (group_id, group_name))
    conn.commit()
    conn.close()

def is_group_registered(group_id: str) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM registered_groups WHERE group_id = ? AND is_active = 1", (group_id,))
    row = cursor.fetchone()
    conn.close()
    return row is not None

def get_registered_groups():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT group_id, group_name, registered_at FROM registered_groups WHERE is_active = 1")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def save_message(group_id: str, user_id: str, user_name: str, message_text: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO messages (group_id, user_id, user_name, message_text)
        VALUES (?, ?, ?, ?)
    """, (group_id, user_id, user_name, message_text))
    conn.commit()
    conn.close()

def get_weekly_messages(group_id: str, days: int = 7):
    conn = get_connection()
    cursor = conn.cursor()
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("""
        SELECT user_name, message_text, created_at
        FROM messages
        WHERE group_id = ? AND created_at >= ?
        ORDER BY created_at ASC
    """, (group_id, since))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def count_weekly_messages(group_id: str, days: int = 7) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("""
        SELECT COUNT(*) as cnt
        FROM messages
        WHERE group_id = ? AND created_at >= ?
    """, (group_id, since))
    cnt = cursor.fetchone()["cnt"]
    conn.close()
    return cnt
