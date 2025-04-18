"""
Import CSV data into the new SQLite database.
This script, given csvs from the old version, handles:
 - The scheduled bans (dropping the guild_id)
 - Member last roles (only if non-empty)

Configuration is loaded from bot.json.
"""

import os
import csv
import sqlite3
import json
import sys


try:
    with open("config/bot.json", "r") as f:
        config = json.load(f)
except Exception as e:
    print(f"Error loading config/bot.json: {e}")
    sys.exit(1)

# Use the database_path from bot.json.
DB_PATH = config.get("database_path", "bot_data.db")

# The pickles and CSV exports are assumed to be in the data folder.
DATA_DIR = "data"

# Create the simplified schema.
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS member_last_roles (
    member_id INTEGER PRIMARY KEY,
    last_roles TEXT
);

CREATE TABLE IF NOT EXISTS scheduled_bans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    unban_time TEXT,
    member_id INTEGER,
    role_id INTEGER
);
"""

def import_member_last_roles(cursor):
    member_csv = os.path.join(DATA_DIR, 'member_data.csv')
    if not os.path.exists(member_csv):
        print("member_data.csv not found; skipping member roles import.")
        return
    with open(member_csv, newline='') as csvfile:
        reader = csv.DictReader(csvfile)
        count = 0
        for row in reader:
            last_roles = row['last_roles'].strip()
            # Import only if last_roles is non-empty and not just an empty JSON array.
            if last_roles and last_roles != "[]":
                member_id = int(row['member_id'])
                cursor.execute(
                    "INSERT OR REPLACE INTO member_last_roles (member_id, last_roles) VALUES (?, ?)",
                    (member_id, last_roles)
                )
                count += 1
        print(f"Imported last roles for {count} member(s).")

def import_scheduled_bans(cursor):
    bans_csv = os.path.join(DATA_DIR, 'scheduled_bans.csv')
    if not os.path.exists(bans_csv):
        print("scheduled_bans.csv not found; skipping bans import.")
        return
    with open(bans_csv, newline='') as csvfile:
        reader = csv.DictReader(csvfile)
        count = 0
        for row in reader:
            unban_time = row['unban_time']
            member_id = int(row['member_id'])
            role_id = int(row['role_id'])
            cursor.execute(
                "INSERT INTO scheduled_bans (unban_time, member_id, role_id) VALUES (?, ?, ?)",
                (unban_time, member_id, role_id)
            )
            count += 1
        print(f"Imported {count} scheduled ban(s).")

def main():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.executescript(SCHEMA_SQL)
    conn.commit()

    import_member_last_roles(cursor)
    import_scheduled_bans(cursor)

    conn.commit()
    conn.close()
    print("Data import completed successfully.")

if __name__ == '__main__':
    main()
