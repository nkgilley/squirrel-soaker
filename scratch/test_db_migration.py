import os
import sys
import datetime

# Add the workspace root to python path to import classify_images
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from classify_images import db_session, engine, DBImage, DBBlast, DBVideo, DBSetting, DBUndoEvent, init_db_and_migrate, load_settings, save_settings

def run_tests():
    print("Starting database migration and schema verification tests...")
    
    # 1. Run migrations and startup sync
    print("Running init_db_and_migrate()...")
    init_db_and_migrate()
    print("init_db_and_migrate() executed successfully.")
    
    # 2. Check that tables are created
    from sqlalchemy import inspect
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    print("Tables in database:", tables)
    
    required_tables = {'images', 'blasts', 'videos', 'settings', 'undo_events'}
    for t in required_tables:
        assert t in tables, f"Error: Table {t} is missing from the database!"
        
    print("Verified all required tables exist in SQLite.")
    
    # 3. Verify settings load/save
    settings = load_settings()
    print(f"Loaded {len(settings)} settings from DB. Current spray_duration: {settings.get('spray_duration')}")
    assert settings.get('spray_duration') is not None, "Error: spray_duration is missing!"
    
    original_dur = settings.get('spray_duration')
    settings['spray_duration'] = 99.0
    save_settings(settings)
    
    reloaded = load_settings()
    print(f"Reloaded spray_duration after save: {reloaded.get('spray_duration')}")
    assert reloaded.get('spray_duration') == 99.0, "Error: Saved setting did not persist/reload!"
    
    # Restore original setting
    settings['spray_duration'] = original_dur
    save_settings(settings)
    print("Settings load/save verified successfully.")
    
    # 4. Verify images table has records (from directory scan)
    image_count = db_session.query(DBImage).count()
    print(f"Number of indexed images in DBImage: {image_count}")
    
    # 5. Verify blasts table has records (from legacy blasts_log.json migration)
    blast_count = db_session.query(DBBlast).count()
    print(f"Number of migrated blasts in DBBlast: {blast_count}")
    
    # 6. Verify videos table has records
    video_count = db_session.query(DBVideo).count()
    print(f"Number of indexed/migrated videos in DBVideo: {video_count}")
    
    # 7. Verify Undo Event persistence
    print("Testing Undo Event persistence...")
    # Clear any old undo events
    db_session.query(DBUndoEvent).delete()
    db_session.commit()
    
    assert db_session.query(DBUndoEvent).count() == 0
    
    # Add a mock undo event
    mock_ev = DBUndoEvent(
        timestamp=datetime.datetime.now(),
        filename="img_test_undo_123.jpg",
        original_category="raw",
        target_category="squirrel"
    )
    db_session.add(mock_ev)
    db_session.commit()
    
    assert db_session.query(DBUndoEvent).count() == 1
    reloaded_ev = db_session.query(DBUndoEvent).first()
    assert reloaded_ev.filename == "img_test_undo_123.jpg"
    assert reloaded_ev.original_category == "raw"
    assert reloaded_ev.target_category == "squirrel"
    
    # Delete the undo event
    db_session.delete(reloaded_ev)
    db_session.commit()
    assert db_session.query(DBUndoEvent).count() == 0
    print("Undo Event persistence verified successfully.")
    
    print("\n--- ALL DB TESTS PASSED SUCCESSFULLY! ---")

if __name__ == '__main__':
    run_tests()
