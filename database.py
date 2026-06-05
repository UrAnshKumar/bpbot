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
