import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

class Database:
    def __init__(self, db_path: str | None = None):
        if not db_path:
            with open("config/bot.json", "r") as f:
                config = json.load(f)
                db_path = config["database_path"]
        self.db_path = db_path
        self.init_schema()

    @staticmethod
    def sanitize_datetime(dt: datetime) -> str:
        """Convert a datetime to a naive ISO-formatted string."""
        return dt.astimezone(timezone.utc).replace(tzinfo=None).isoformat()

    def init_schema(self) -> None:
        """Initialize the database schema if not already present.

        All datetime values are stored in UTC as naive ISO-formatted strings.
        """
        with self.get_connection() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS scheduled_bans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                unban_time TEXT,   -- ISO-formatted UTC datetime string (naive)
                member_id INTEGER,
                role_id INTEGER
            );

            CREATE TABLE IF NOT EXISTS member_last_roles (
                member_id INTEGER PRIMARY KEY,
                last_roles TEXT    -- JSON array of role IDs
            );
            
            CREATE TABLE IF NOT EXISTS channel_categories (
            category TEXT PRIMARY KEY,
            channels TEXT    -- JSON-encoded list of channel IDs
            );
            """)
            conn.commit()

    @contextmanager
    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
        finally:
            conn.close()

    # Scheduled Bans Operations

    def add_scheduled_ban(self, unban_time: datetime, member_id: int, role_id: int) -> int:
        """
        Inserts a new scheduled ban into the database.
        Returns the ID of the newly inserted row.

        The unban_time is converted to UTC and stored as a naive ISO-formatted string.
        """
        # Convert unban_time to UTC, remove tzinfo, and store as ISO string.
        unban_str = unban_time.astimezone(timezone.utc).replace(tzinfo=None).isoformat()
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO scheduled_bans (unban_time, member_id, role_id) VALUES (?, ?, ?)",
                (unban_str, member_id, role_id)
            )
            conn.commit()
            return cur.lastrowid

    def remove_scheduled_ban(self, member_id: int, role_id: int) -> None:
        """Removes a scheduled ban entry for the given member and role."""
        with self.get_connection() as conn:
            conn.execute(
                "DELETE FROM scheduled_bans WHERE member_id = ? AND role_id = ?",
                (member_id, role_id)
            )
            conn.commit()

    def get_due_scheduled_bans(self, current_time: datetime) -> list[tuple]:
        """
        Returns a list of all scheduled bans that are due (i.e. unban_time <= current_time).
        Each row is a tuple: (id, unban_time, member_id, role_id).

        The current_time is converted to a naive UTC datetime string for comparison.
        """
        current_str = self.sanitize_datetime(current_time)
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, unban_time, member_id, role_id FROM scheduled_bans WHERE unban_time <= ?",
                (current_str,)
            )
            return cur.fetchall()

    def get_active_scheduled_bans(self, current_time: datetime) -> list[tuple]:
        """
        Returns a list of active (future) scheduled bans.
        Each row is a tuple: (id, unban_time, member_id, role_id).

        The current_time is converted to a naive UTC datetime string for comparison.
        """
        current_str = self.sanitize_datetime(current_time)
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, unban_time, member_id, role_id FROM scheduled_bans WHERE unban_time > ?",
                (current_str,),
            )
            return cur.fetchall()

    def delete_scheduled_ban(self, ban_id: int) -> None:
        """Deletes a scheduled ban by its primary key."""
        with self.get_connection() as conn:
            conn.execute("DELETE FROM scheduled_bans WHERE id = ?", (ban_id,))
            conn.commit()

    # Member Last Roles Operations

    def update_member_last_roles(self, member_id: int, roles: list) -> None:
        """
        Updates (or inserts) the last roles for a given member.
        Roles should be provided as a list of integers.
        """
        roles_json = json.dumps(roles)
        with self.get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO member_last_roles (member_id, last_roles) VALUES (?, ?)",
                (member_id, roles_json)
            )
            conn.commit()

    def get_member_last_roles(self, member_id: int) -> list:
        """
        Retrieves the last roles for a member as a Python list.
        Returns an empty list if no data is found.
        """
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT last_roles FROM member_last_roles WHERE member_id = ?",
                (member_id,)
            )
            row = cur.fetchone()
            if row and row[0]:
                return json.loads(row[0])
            return []

    def delete_member_last_roles(self, member_id: int) -> None:
        """Deletes the stored roles for a member from the database."""
        with self.get_connection() as conn:
            conn.execute(
                "DELETE FROM member_last_roles WHERE member_id = ?", (member_id,)
            )
            conn.commit()

    # Channel Categories Operations

    def get_channel_category(self, category: str) -> list:
        """
        Returns the list of channel IDs (as integers) for the given category.
        If the category doesn't exist, returns an empty list.
        """
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT channels FROM channel_categories WHERE category = ?", (category,))
            row = cur.fetchone()
            if row and row[0]:
                return json.loads(row[0])
            return []

    def update_channel_category(self, category: str, channels: list) -> None:
        """
        Inserts or replaces the channel list for the given category.
        The channels are stored as a JSON-encoded string.
        """
        channels_json = json.dumps(channels)
        with self.get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO channel_categories (category, channels) VALUES (?, ?)",
                (category, channels_json)
            )
            conn.commit()

    def toggle_channel_category(self, category: str, channel_id: int) -> bool:
        """
        Toggles the presence of channel_id in the specified category.
        Returns True if channel_id was added (i.e. now ignored),
        and False if it was removed.
        """
        channels = self.get_channel_category(category)
        if channel_id in channels:
            channels.remove(channel_id)
            added = False
        else:
            channels.append(channel_id)
            added = True
        self.update_channel_category(category, channels)
        return added