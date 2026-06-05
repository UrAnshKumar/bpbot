import sqlite3
import os
from datetime import datetime

DB_FILE = os.getenv("DATABASE_FILE", "study_bot.db")

def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # User Profile & Activity Data
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        discord_id INTEGER PRIMARY KEY,
        xp INTEGER DEFAULT 0,
        level INTEGER DEFAULT 1,
        voice_minutes INTEGER DEFAULT 0,
        message_count INTEGER DEFAULT 0,
        coins INTEGER DEFAULT 0,
        camera_minutes INTEGER DEFAULT 0,
        last_active TEXT
    )
    """)
    
    # Personal Todo List
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        discord_id INTEGER,
        task_text TEXT NOT NULL,
        status TEXT DEFAULT 'todo', -- 'todo' or 'done'
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(discord_id) REFERENCES users(discord_id) ON DELETE CASCADE
    )
    """)
    
    # Guild Configurations (Welcomer & Moderation)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS guild_config (
        guild_id INTEGER PRIMARY KEY,
        welcome_channel_id INTEGER,
        welcome_message TEXT,
        welcome_role_id INTEGER
    )
    """)
    
    # Self-assignable role configurations
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS self_roles (
        guild_id INTEGER,
        role_id INTEGER,
        emoji TEXT,
        label TEXT,
        PRIMARY KEY (guild_id, role_id)
    )
    """)
    
    # Shop items (Roles and items bought with coins)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS shop_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_name TEXT NOT NULL,
        item_type TEXT NOT NULL, -- 'color_role', 'gotchi_food'
        price INTEGER NOT NULL,
        role_id INTEGER
    )
    """)
    
    # User purchased items / inventory
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS inventory (
        discord_id INTEGER,
        item_id INTEGER,
        quantity INTEGER DEFAULT 1,
        PRIMARY KEY (discord_id, item_id),
        FOREIGN KEY(item_id) REFERENCES shop_items(id)
    )
    """)
    
    # Private rentable voice channels
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS private_rooms (
        channel_id INTEGER PRIMARY KEY,
        owner_id INTEGER NOT NULL,
        rent_expiry TEXT NOT NULL, -- Timestamp
        rent_rate INTEGER DEFAULT 50 -- Coins per hour
    )
    """)
    
    # LionGotchi Pet details
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS gotchi_pets (
        discord_id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        level INTEGER DEFAULT 1,
        xp INTEGER DEFAULT 0,
        hunger INTEGER DEFAULT 100, -- 0-100
        happiness INTEGER DEFAULT 100, -- 0-100
        health INTEGER DEFAULT 100, -- 0-100
        last_update TEXT NOT NULL,
        FOREIGN KEY(discord_id) REFERENCES users(discord_id) ON DELETE CASCADE
    )
    """)
    
    # Insert default shop items if shop_items is empty
    cursor.execute("SELECT COUNT(*) FROM shop_items")
    if cursor.fetchone()[0] == 0:
        default_items = [
            ('Red Scholar Role', 'color_role', 500, None),
            ('Blue Scholar Role', 'color_role', 500, None),
            ('Green Scholar Role', 'color_role', 500, None),
            ('Organic Meat (Gotchi Food)', 'gotchi_food', 100, None),
            ('Energy Drink (Gotchi Play)', 'gotchi_food', 100, None),
            ('Premium Shampoo (Gotchi Bath)', 'gotchi_food', 100, None)
        ]
        cursor.executemany("INSERT INTO shop_items (item_name, item_type, price, role_id) VALUES (?, ?, ?, ?)", default_items)
        
    conn.commit()
    conn.close()

# --- User Helpers ---
def get_user(discord_id):
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM users WHERE discord_id = ?", (discord_id,)).fetchone()
    conn.close()
    if not row:
        # Create user
        conn = get_db_connection()
        conn.execute("INSERT OR IGNORE INTO users (discord_id, last_active) VALUES (?, ?)", 
                     (discord_id, datetime.now().isoformat()))
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE discord_id = ?", (discord_id,)).fetchone()
        conn.close()
    return row

def update_user_activity(discord_id, xp_gain=0, voice_min=0, msg_count=0, camera_min=0, coin_gain=0):
    conn = get_db_connection()
    user = get_user(discord_id)
    new_xp = user['xp'] + xp_gain
    new_level = user['level']
    
    # Standard leveling formula: Level = sqrt(XP / 100) + 1
    # Or simplified: level up every 500 XP
    calculated_level = int(new_xp // 500) + 1
    level_up = False
    if calculated_level > new_level:
        new_level = calculated_level
        level_up = True
        
    conn.execute("""
        UPDATE users 
        SET xp = ?, level = ?, voice_minutes = voice_minutes + ?, 
            message_count = message_count + ?, camera_minutes = camera_minutes + ?, 
            coins = coins + ?, last_active = ?
        WHERE discord_id = ?
    """, (new_xp, new_level, voice_min, msg_count, camera_min, coin_gain, datetime.now().isoformat(), discord_id))
    conn.commit()
    conn.close()
    return level_up, new_level

def deduct_coins(discord_id, amount):
    conn = get_db_connection()
    user = get_user(discord_id)
    if user['coins'] < amount:
        conn.close()
        return False
    conn.execute("UPDATE users SET coins = coins - ? WHERE discord_id = ?", (amount, discord_id))
    conn.commit()
    conn.close()
    return True

def add_coins(discord_id, amount):
    conn = get_db_connection()
    get_user(discord_id) # ensure user exists
    conn.execute("UPDATE users SET coins = coins + ? WHERE discord_id = ?", (amount, discord_id))
    conn.commit()
    conn.close()

# --- Tasks (Todo) Helpers ---
def add_task(discord_id, text):
    conn = get_db_connection()
    get_user(discord_id)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO tasks (discord_id, task_text) VALUES (?, ?)", (discord_id, text))
    conn.commit()
    task_id = cursor.lastrowid
    conn.close()
    return task_id

def get_user_tasks(discord_id):
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM tasks WHERE discord_id = ? ORDER BY status DESC, id ASC", (discord_id,)).fetchall()
    conn.close()
    return rows

def complete_task(discord_id, task_id):
    conn = get_db_connection()
    # verify ownership
    task = conn.execute("SELECT * FROM tasks WHERE id = ? AND discord_id = ?", (task_id, discord_id)).fetchone()
    if not task:
        conn.close()
        return False
    
    if task['status'] == 'done':
        conn.close()
        return False # already complete
        
    conn.execute("UPDATE tasks SET status = 'done' WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()
    return True

def delete_task(discord_id, task_id):
    conn = get_db_connection()
    task = conn.execute("SELECT * FROM tasks WHERE id = ? AND discord_id = ?", (task_id, discord_id)).fetchone()
    if not task:
        conn.close()
        return False
    conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()
    return True

# --- Leaderboard Helpers ---
def get_leaderboard(stat_type="xp", limit=10):
    conn = get_db_connection()
    if stat_type == "xp":
        query = "SELECT discord_id, xp as score, level FROM users ORDER BY xp DESC LIMIT ?"
    elif stat_type == "voice":
        query = "SELECT discord_id, voice_minutes as score, level FROM users ORDER BY voice_minutes DESC LIMIT ?"
    elif stat_type == "coins":
        query = "SELECT discord_id, coins as score, level FROM users ORDER BY coins DESC LIMIT ?"
    else:
        query = "SELECT discord_id, xp as score, level FROM users ORDER BY xp DESC LIMIT ?"
        
    rows = conn.execute(query, (limit,)).fetchall()
    conn.close()
    return rows

# --- Shop Helpers ---
def get_shop_items():
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM shop_items").fetchall()
    conn.close()
    return rows

def add_shop_item(name, item_type, price, role_id=None):
    conn = get_db_connection()
    conn.execute("INSERT INTO shop_items (item_name, item_type, price, role_id) VALUES (?, ?, ?, ?)",
                 (name, item_type, price, role_id))
    conn.commit()
    conn.close()

def buy_item(discord_id, item_id):
    conn = get_db_connection()
    item = conn.execute("SELECT * FROM shop_items WHERE id = ?", (item_id,)).fetchone()
    if not item:
        conn.close()
        return False, "Item not found."
    
    user = get_user(discord_id)
    if user['coins'] < item['price']:
        conn.close()
        return False, f"Insufficient coins. You need {item['price']} coins but only have {user['coins']}."
        
    # Deduct coins
    conn.execute("UPDATE users SET coins = coins - ? WHERE discord_id = ?", (item['price'], discord_id))
    
    # Add to inventory
    inv = conn.execute("SELECT * FROM inventory WHERE discord_id = ? AND item_id = ?", (discord_id, item_id)).fetchone()
    if inv:
        conn.execute("UPDATE inventory SET quantity = quantity + 1 WHERE discord_id = ? AND item_id = ?", (discord_id, item_id))
    else:
        conn.execute("INSERT INTO inventory (discord_id, item_id, quantity) VALUES (?, ?, 1)", (discord_id, item_id))
        
    conn.commit()
    conn.close()
    return True, item

def get_inventory(discord_id):
    conn = get_db_connection()
    rows = conn.execute("""
        SELECT i.quantity, s.id, s.item_name, s.item_type, s.role_id 
        FROM inventory i
        JOIN shop_items s ON i.item_id = s.id
        WHERE i.discord_id = ?
    """, (discord_id,)).fetchall()
    conn.close()
    return rows

# --- Private Rooms Helpers ---
def add_private_room(channel_id, owner_id, duration_hours, rate=50):
    conn = get_db_connection()
    expiry = datetime.now().timestamp() + (duration_hours * 3600)
    conn.execute("INSERT OR REPLACE INTO private_rooms (channel_id, owner_id, rent_expiry, rent_rate) VALUES (?, ?, ?, ?)",
                 (channel_id, owner_id, str(expiry), rate))
    conn.commit()
    conn.close()
    return expiry

def get_private_room(channel_id):
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM private_rooms WHERE channel_id = ?", (channel_id,)).fetchone()
    conn.close()
    return row

def get_expired_rooms():
    conn = get_db_connection()
    now = datetime.now().timestamp()
    rows = conn.execute("SELECT * FROM private_rooms WHERE CAST(rent_expiry AS REAL) < ?", (now,)).fetchall()
    conn.close()
    return rows

def extend_private_room(channel_id, duration_hours):
    conn = get_db_connection()
    room = get_private_room(channel_id)
    if not room:
        conn.close()
        return False
    current_expiry = float(room['rent_expiry'])
    new_expiry = max(current_expiry, datetime.now().timestamp()) + (duration_hours * 3600)
    conn.execute("UPDATE private_rooms SET rent_expiry = ? WHERE channel_id = ?", (str(new_expiry), channel_id))
    conn.commit()
    conn.close()
    return new_expiry

def delete_private_room(channel_id):
    conn = get_db_connection()
    conn.execute("DELETE FROM private_rooms WHERE channel_id = ?", (channel_id,))
    conn.commit()
    conn.close()

# --- Gotchi Pet Helpers ---
def get_gotchi(discord_id):
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM gotchi_pets WHERE discord_id = ?", (discord_id,)).fetchone()
    conn.close()
    return row

def adopt_gotchi(discord_id, pet_name):
    conn = get_db_connection()
    get_user(discord_id)
    conn.execute("""
        INSERT OR REPLACE INTO gotchi_pets (discord_id, name, last_update) 
        VALUES (?, ?, ?)
    """, (discord_id, pet_name, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def update_gotchi_status(discord_id, hunger_delta=0, happiness_delta=0, health_delta=0, xp_gain=0):
    conn = get_db_connection()
    gotchi = get_gotchi(discord_id)
    if not gotchi:
        conn.close()
        return None
        
    new_hunger = max(0, min(100, gotchi['hunger'] + hunger_delta))
    new_happiness = max(0, min(100, gotchi['happiness'] + happiness_delta))
    new_health = max(0, min(100, gotchi['health'] + health_delta))
    new_xp = gotchi['xp'] + xp_gain
    new_level = gotchi['level']
    
    # Level gotchi up every 200 XP
    calc_lvl = int(new_xp // 200) + 1
    level_up = calc_lvl > new_level
    if level_up:
        new_level = calc_lvl
        
    conn.execute("""
        UPDATE gotchi_pets
        SET hunger = ?, happiness = ?, health = ?, xp = ?, level = ?, last_update = ?
        WHERE discord_id = ?
    """, (new_hunger, new_happiness, new_health, new_xp, new_level, datetime.now().isoformat(), discord_id))
    conn.commit()
    conn.close()
    return {
        'level_up': level_up,
        'level': new_level,
        'hunger': new_hunger,
        'happiness': new_happiness,
        'health': new_health
    }

# --- Guild Config Helpers ---
def get_guild_config(guild_id):
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM guild_config WHERE guild_id = ?", (guild_id,)).fetchone()
    conn.close()
    return row

def update_guild_config(guild_id, welcome_channel_id=None, welcome_message=None, welcome_role_id=None):
    conn = get_db_connection()
    config = get_guild_config(guild_id)
    if config:
        # Update existing
        q = "UPDATE guild_config SET "
        params = []
        updates = []
        if welcome_channel_id is not None:
            updates.append("welcome_channel_id = ?")
            params.append(welcome_channel_id)
        if welcome_message is not None:
            updates.append("welcome_message = ?")
            params.append(welcome_message)
        if welcome_role_id is not None:
            updates.append("welcome_role_id = ?")
            params.append(welcome_role_id)
        if updates:
            q += ", ".join(updates) + " WHERE guild_id = ?"
            params.append(guild_id)
            conn.execute(q, params)
    else:
        # Insert new
        conn.execute("""
            INSERT INTO guild_config (guild_id, welcome_channel_id, welcome_message, welcome_role_id)
            VALUES (?, ?, ?, ?)
        """, (guild_id, welcome_channel_id, welcome_message, welcome_role_id))
    conn.commit()
    conn.close()

# --- Self Role Configuration Helpers ---
def get_self_roles(guild_id):
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM self_roles WHERE guild_id = ?", (guild_id,)).fetchall()
    conn.close()
    return rows

def add_self_role(guild_id, role_id, emoji, label):
    conn = get_db_connection()
    conn.execute("INSERT OR REPLACE INTO self_roles (guild_id, role_id, emoji, label) VALUES (?, ?, ?, ?)",
                 (guild_id, role_id, emoji, label))
    conn.commit()
    conn.close()

def delete_self_role(guild_id, role_id):
    conn = get_db_connection()
    conn.execute("DELETE FROM self_roles WHERE guild_id = ? AND role_id = ?", (guild_id, role_id))
    conn.commit()
    conn.close()
