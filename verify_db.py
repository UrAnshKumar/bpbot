import os
import sys

# Ensure study_bot.db is used and delete previous test database to run in clean slate
test_db = "test_study_bot.db"
os.environ["DATABASE_FILE"] = test_db
if os.path.exists(test_db):
    os.remove(test_db)

try:
    import database
    print("Database module imported successfully.")
    
    # 1. Initialize DB
    database.init_db()
    print("Database initialized successfully.")
    
    # 2. Test User Activity and Leveling
    user_id = 123456789
    user = database.get_user(user_id)
    assert user['discord_id'] == user_id, "User discord ID mismatch!"
    assert user['xp'] == 0, "Default XP should be 0"
    print("Default user creation verified.")
    
    # Update activity (gain 600 XP, which should trigger a level up to Level 2)
    lvl_up, new_lvl = database.update_user_activity(user_id, xp_gain=600, voice_min=10, msg_count=5, coin_gain=50)
    assert lvl_up is True, "Level up should be True!"
    assert new_lvl == 2, f"Level should be 2, got {new_lvl}"
    
    # Fetch updated user
    updated_user = database.get_user(user_id)
    assert updated_user['xp'] == 600
    assert updated_user['level'] == 2
    assert updated_user['coins'] == 50
    assert updated_user['voice_minutes'] == 10
    assert updated_user['message_count'] == 5
    print("User activity tracking and leveling calculations verified.")
    
    # 3. Test Tasks (Todo List)
    task_id = database.add_task(user_id, "Solve math equations")
    tasks = database.get_user_tasks(user_id)
    assert len(tasks) == 1, "Should have 1 task!"
    assert tasks[0]['task_text'] == "Solve math equations", "Task text mismatch!"
    assert tasks[0]['status'] == "todo", "Task status should be todo"
    print("Task creation verified.")
    
    # Complete task
    success = database.complete_task(user_id, task_id)
    assert success is True, "Task completion failed!"
    tasks = database.get_user_tasks(user_id)
    assert tasks[0]['status'] == "done", "Task status should be done"
    print("Task completion verified.")
    
    # Delete task
    success = database.delete_task(user_id, task_id)
    assert success is True, "Task deletion failed!"
    tasks = database.get_user_tasks(user_id)
    assert len(tasks) == 0, "Should have 0 tasks!"
    print("Task deletion verified.")
    
    # 4. Test Gotchi Pets
    database.adopt_gotchi(user_id, "Simba")
    gotchi = database.get_gotchi(user_id)
    assert gotchi['name'] == "Simba", "Gotchi name mismatch"
    assert gotchi['level'] == 1, "Default gotchi level should be 1"
    assert gotchi['hunger'] == 100
    print("Gotchi adoption verified.")
    
    # Interact with Gotchi (feed it)
    status = database.update_gotchi_status(user_id, hunger_delta=-20, happiness_delta=10, xp_gain=150)
    assert status['hunger'] == 80, f"Hunger should be 80, got {status['hunger']}"
    assert status['happiness'] == 100, f"Happiness should be capped at 100, got {status['happiness']}"
    assert status['level'] == 1
    
    # Gain more XP to level up Gotchi
    status = database.update_gotchi_status(user_id, xp_gain=100)
    assert status['level_up'] is True
    assert status['level'] == 2
    print("Gotchi interactions and leveling verified.")
    
    # 5. Test Shop and Economy
    shop = database.get_shop_items()
    assert len(shop) > 0, "Shop items should not be empty!"
    
    # Deduct coins
    assert database.deduct_coins(user_id, 30) is True, "Should be able to deduct 30 coins"
    assert database.get_user(user_id)['coins'] == 20, "Should have 20 coins left"
    assert database.deduct_coins(user_id, 100) is False, "Should not be able to deduct 100 coins (insufficient)"
    print("Deduct coins verified.")
    
    # Clean up test database
    if os.path.exists(test_db):
        os.remove(test_db)
        
    print("\nAll database checks passed successfully! :)")
    
except Exception as e:
    print(f"\nDatabase verification failed: {e}")
    if os.path.exists(test_db):
        os.remove(test_db)
    sys.exit(1)
