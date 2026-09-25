"""
One-time migration: the 'user' table was created before password_hash
became nullable and before the approval_token/password_set_token columns
existed. SQLite can't ALTER a column's NOT NULL constraint directly, so
this renames the old table, lets SQLModel create a fresh correctly-
shaped one from the current model, copies existing rows over (new
columns come through as NULL, which is correct -- an existing account
isn't "pending"), then drops the old table.

Run once: docker compose exec backend python3 /app/migrate_user_table.py
"""
import sqlite3

from app.core import db

DB_PATH = "/app/data/rofehai.db"

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

cursor.execute("SELECT COUNT(*) FROM user")
existing_count = cursor.fetchone()[0]
print(f"Existing accounts to preserve: {existing_count}")

cursor.execute("ALTER TABLE user RENAME TO user_old")
conn.commit()
print("Renamed old table to user_old")
conn.close()

# Let SQLModel create a fresh 'user' table matching the current model exactly.
db.init_db()
print("Created fresh user table with correct schema")

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()
cursor.execute(
    "INSERT INTO user (id, username, password_hash, display_name, created_at, is_active) "
    "SELECT id, username, password_hash, display_name, created_at, is_active FROM user_old"
)
conn.commit()
cursor.execute("SELECT COUNT(*) FROM user")
new_count = cursor.fetchone()[0]
print(f"Copied {new_count} accounts into the new table")

if new_count == existing_count:
    cursor.execute("DROP TABLE user_old")
    conn.commit()
    print("Migration successful -- old table dropped")
else:
    print("MISMATCH -- old table NOT dropped, investigate before proceeding")

conn.close()
