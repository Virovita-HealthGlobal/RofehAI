"""
One-time migration: adds question, answer, and actor_name columns to
the existing agentcalllog table, so the audit trail can store full
conversation transcripts. All three are nullable -- existing rows get
NULL, which is correct (we never had that data before). SQLite
supports ALTER TABLE ADD COLUMN directly, no rename/copy dance needed
since we're not changing any existing column's constraints.

Run once: docker compose exec backend python3 /app/migrate_agent_log_table.py
"""
import sqlite3

DB_PATH = "/app/data/rofehai.db"

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

cursor.execute("PRAGMA table_info(agentcalllog)")
existing_cols = {row[1] for row in cursor.fetchall()}
print(f"Existing columns: {sorted(existing_cols)}")

for col in ("question", "answer", "actor_name"):
    if col not in existing_cols:
        cursor.execute(f"ALTER TABLE agentcalllog ADD COLUMN {col} TEXT")
        print(f"Added column: {col}")
    else:
        print(f"Column already exists, skipped: {col}")

conn.commit()
conn.close()
print("Migration complete")
