import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, date, timezone


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
            conn.executescript(
                """
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

            CREATE TABLE IF NOT EXISTS daily_counts (
                date TEXT NOT NULL,
                channel_id INTEGER NOT NULL,
                thread_id INTEGER,
                count INTEGER NOT NULL,
                PRIMARY KEY (date, channel_id, thread_id)
            );

            CREATE TABLE IF NOT EXISTS daily_user_events (
                date TEXT PRIMARY KEY,
                join_count INTEGER NOT NULL,
                leave_count INTEGER NOT NULL,
                ban_count INTEGER NOT NULL
            );
            """
            )
            conn.commit()

    @contextmanager
    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
        finally:
            conn.close()

    # Scheduled Bans Operations

    def add_scheduled_ban(
        self, unban_time: datetime, member_id: int, role_id: int
    ) -> int:
        """
        Adds (or replaces) a scheduled ban.

        • Any existing row for *member_id + role_id* is deleted first, so each user/role
          pair can appear only once in the table.
        • Returns the primary-key ID of the new row.
        """
        unban_str = self.sanitize_datetime(unban_time)

        with self.get_connection() as conn:
            cur = conn.cursor()

            # one-row-per-user/role policy
            cur.execute(
                "DELETE FROM scheduled_bans WHERE member_id = ? AND role_id = ?",
                (member_id, role_id),
            )

            # add the fresh ban
            cur.execute(
                "INSERT INTO scheduled_bans (unban_time, member_id, role_id) VALUES (?, ?, ?)",
                (unban_str, member_id, role_id),
            )
            conn.commit()
            return cur.lastrowid

    def remove_scheduled_ban(self, member_id: int, role_id: int) -> None:
        """Removes a scheduled ban entry for the given member and role."""
        with self.get_connection() as conn:
            conn.execute(
                "DELETE FROM scheduled_bans WHERE member_id = ? AND role_id = ?",
                (member_id, role_id),
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
                (current_str,),
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
                (member_id, roles_json),
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
                (member_id,),
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
            cur.execute(
                "SELECT channels FROM channel_categories WHERE category = ?",
                (category,),
            )
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
                (category, channels_json),
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

    # Daily Counts Operations

    def increment_daily_count(self, channel_id: int, thread_id: int | None) -> None:
        """
        Increments the message count for the given channel/thread for today's date.
        Uses an UPSERT to create or update a single row per (date, channel, thread).
        """
        date_str = datetime.now(timezone.utc).date().isoformat()
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO daily_counts (date, channel_id, thread_id, count)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(date, channel_id, thread_id)
                DO UPDATE SET count = count + 1;
                """,
                (date_str, channel_id, thread_id),
            )
            conn.commit()

    def get_daily_counts(self, target_date: date) -> list[tuple[int, int | None, int]]:
        """
        Retrieves the message counts per channel/thread for the given calendar date.
        Returns a list of tuples: (channel_id, thread_id, count).
        """
        date_str = target_date.isoformat()
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT channel_id, thread_id, count
                FROM daily_counts
                WHERE date = ?
                ORDER BY count DESC;
                """,
                (date_str,),
            )
            return cur.fetchall()

    # Daily User Events Operations

    def increment_daily_user_event(self, event: str) -> None:
        """
        Increments the join/leave/ban counter for today's date.
        event must be one of 'join', 'leave', 'ban'.
        """
        if event not in ("join", "leave", "ban"):
            raise ValueError(f"Unknown event type: {event}")
        date_str = datetime.now(timezone.utc).date().isoformat()
        col_map = {"join": "join_count", "leave": "leave_count", "ban": "ban_count"}
        join_val = 1 if event == "join" else 0
        leave_val = 1 if event == "leave" else 0
        ban_val = 1 if event == "ban" else 0
        col = col_map[event]
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                INSERT INTO daily_user_events (date, join_count, leave_count, ban_count)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET {col} = {col} + 1;
                """,
                (date_str, join_val, leave_val, ban_val),
            )
            conn.commit()

    def get_daily_user_events(self, target_date: date) -> dict[str, int]:
        """
        Retrieves the join/leave/ban counts for the given calendar date.
        Returns a dict with keys 'join', 'leave', 'ban' (0 if no row).
        """
        date_str = target_date.isoformat()
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT join_count, leave_count, ban_count FROM daily_user_events WHERE date = ?",
                (date_str,),
            )
            row = cur.fetchone()
            if row:
                return {"join": row[0], "leave": row[1], "ban": row[2]}
            return {"join": 0, "leave": 0, "ban": 0}
