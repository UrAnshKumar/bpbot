import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "welcome_bot.db")

def init_db():
    """Initializes the SQLite database and creates the necessary tables."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS welcomer_settings (
            guild_id INTEGER PRIMARY KEY,
            channel_id INTEGER NOT NULL,
            normal_text TEXT,
            embed_title TEXT,
            embed_description TEXT,
            embed_author_name TEXT,
            embed_author_icon TEXT,
            embed_thumbnail TEXT,
            embed_banner TEXT,
            embed_footer TEXT,
            embed_color TEXT
        )
    """)
    # Check if dm_welcome column exists, if not add it
    cursor.execute("PRAGMA table_info(welcomer_settings)")
    columns = [col[1] for col in cursor.fetchall()]
    if "dm_welcome" not in columns:
        cursor.execute("ALTER TABLE welcomer_settings ADD COLUMN dm_welcome INTEGER DEFAULT 0")
        
    # Create moderator_roles table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS moderator_roles (
            guild_id INTEGER,
            role_id INTEGER,
            PRIMARY KEY (guild_id, role_id)
        )
    """)
    
    # Create mod_logs table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS mod_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER,
            user_id INTEGER,
            user_name TEXT,
            moderator_id INTEGER,
            moderator_name TEXT,
            action TEXT,
            reason TEXT,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Create automod_settings table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS automod_settings (
            guild_id INTEGER PRIMARY KEY,
            enabled INTEGER DEFAULT 0,
            vulgar_filter INTEGER DEFAULT 1,
            caps_filter INTEGER DEFAULT 1,
            spam_filter INTEGER DEFAULT 1
        )
    """)
    
    # Create user_warnings table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_warnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER,
            user_id INTEGER,
            reason TEXT,
            moderator_id INTEGER,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def save_welcome_settings(
    guild_id: int,
    channel_id: int,
    normal_text: str,
    embed_title: str,
    embed_description: str,
    embed_author_name: str,
    embed_author_icon: str,
    embed_thumbnail: str,
    embed_banner: str,
    embed_footer: str,
    embed_color: str
):
    """Saves or updates welcome configuration settings for a guild."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Preserve existing dm_welcome value if present
    cursor.execute("SELECT dm_welcome FROM welcomer_settings WHERE guild_id = ?", (guild_id,))
    row = cursor.fetchone()
    dm_welcome = row[0] if row else 0

    cursor.execute("""
        INSERT INTO welcomer_settings (
            guild_id, channel_id, normal_text, embed_title, embed_description,
            embed_author_name, embed_author_icon, embed_thumbnail, embed_banner,
            embed_footer, embed_color, dm_welcome
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET
            channel_id=excluded.channel_id,
            normal_text=excluded.normal_text,
            embed_title=excluded.embed_title,
            embed_description=excluded.embed_description,
            embed_author_name=excluded.embed_author_name,
            embed_author_icon=excluded.embed_author_icon,
            embed_thumbnail=excluded.embed_thumbnail,
            embed_banner=excluded.embed_banner,
            embed_footer=excluded.embed_footer,
            embed_color=excluded.embed_color
    """, (
        guild_id, channel_id, normal_text, embed_title, embed_description,
        embed_author_name, embed_author_icon, embed_thumbnail, embed_banner,
        embed_footer, embed_color, dm_welcome
    ))
    conn.commit()
    conn.close()

def update_dm_welcome(guild_id: int, enabled: bool) -> bool:
    """Updates the dm_welcome setting for a guild. Returns False if guild welcome is not setup yet."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM welcomer_settings WHERE guild_id = ?", (guild_id,))
    exists = cursor.fetchone()
    if not exists:
        conn.close()
        return False
    
    cursor.execute(
        "UPDATE welcomer_settings SET dm_welcome = ? WHERE guild_id = ?",
        (1 if enabled else 0, guild_id)
    )
    conn.commit()
    conn.close()
    return True

def get_welcome_settings(guild_id: int) -> dict:
    """Retrieves the welcome configuration settings for a guild."""
    conn = sqlite3.connect(DB_PATH)
    # Configure connection to return rows as dictionaries
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM welcomer_settings WHERE guild_id = ?", (guild_id,))
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return dict(row)
    return {}

# Moderator Role Helpers
def add_mod_role(guild_id: int, role_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO moderator_roles (guild_id, role_id) VALUES (?, ?)",
        (guild_id, role_id)
    )
    conn.commit()
    conn.close()

def remove_mod_role(guild_id: int, role_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM moderator_roles WHERE guild_id = ? AND role_id = ?",
        (guild_id, role_id)
    )
    conn.commit()
    conn.close()

def get_mod_roles(guild_id: int) -> list:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT role_id FROM moderator_roles WHERE guild_id = ?", (guild_id,))
    rows = cursor.fetchall()
    conn.close()
    return [row[0] for row in rows]

# Automod Helpers
def get_automod_settings(guild_id: int) -> dict:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM automod_settings WHERE guild_id = ?", (guild_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return dict(row)
    return {"guild_id": guild_id, "enabled": 0, "vulgar_filter": 1, "caps_filter": 1, "spam_filter": 1}

def save_automod_settings(guild_id: int, enabled: bool, vulgar: bool, caps: bool, spam: bool):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO automod_settings (guild_id, enabled, vulgar_filter, caps_filter, spam_filter)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET
            enabled=excluded.enabled,
            vulgar_filter=excluded.vulgar_filter,
            caps_filter=excluded.caps_filter,
            spam_filter=excluded.spam_filter
    """, (guild_id, 1 if enabled else 0, 1 if vulgar else 0, 1 if caps else 0, 1 if spam else 0))
    conn.commit()
    conn.close()

# Mod Logs Helpers
def add_mod_log(guild_id: int, user_id: int, user_name: str, moderator_id: int, moderator_name: str, action: str, reason: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO mod_logs (guild_id, user_id, user_name, moderator_id, moderator_name, action, reason)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (guild_id, user_id, user_name, moderator_id, moderator_name, action, reason))
    conn.commit()
    conn.close()

def get_mod_logs(guild_id: int, limit: int = 10) -> list:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM mod_logs WHERE guild_id = ? ORDER BY id DESC LIMIT ?",
        (guild_id, limit)
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

# Warning Helpers
def add_warning(guild_id: int, user_id: int, reason: str, moderator_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO user_warnings (guild_id, user_id, reason, moderator_id)
        VALUES (?, ?, ?, ?)
    """, (guild_id, user_id, reason, moderator_id))
    conn.commit()
    conn.close()

def get_warnings(guild_id: int, user_id: int) -> list:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM user_warnings WHERE guild_id = ? AND user_id = ? ORDER BY id DESC",
        (guild_id, user_id)
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def clear_warnings(guild_id: int, user_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM user_warnings WHERE guild_id = ? AND user_id = ?",
        (guild_id, user_id)
    )
    conn.commit()
    conn.close()
