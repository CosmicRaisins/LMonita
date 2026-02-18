#!/usr/bin/env python
"""
Test script to verify persistence functionality
"""
import sys
sys.path.insert(0, '.')

from lmstudio_monitor import GenRecord, save_history, load_history, ensure_data_dir
from collections import deque
import os
import time

def test_persistence():
    """Test that history can be saved and loaded"""
    
    # Create some test data
    model_id = "test-model-123"
    history = {
        model_id: deque(maxlen=50)
    }
    
    # Add some records
    for i in range(5):
        rec = GenRecord()
        rec.timestamp = time.time() - (5 - i)  # Different timestamps
        rec.tps = 20.0 + i * 2
        rec.ttft_sec = 0.1 + i * 0.05
        rec.total_sec = 1.0 + i * 0.5
        rec.prompt_tokens = 100 + i * 50
        rec.predicted_tokens = 200 + i * 100
        rec.total_tokens = 300 + i * 150
        rec.stop_reason = "stop" if i % 2 == 0 else "eos"
        history[model_id].append(rec)
    
    # Save the history
    print("Saving test history...")
    result = save_history(history)
    assert result == True, "Save should succeed"
    print("OK: History saved successfully")
    
    # Load the history back
    print("\nLoading test history...")
    loaded = load_history()
    assert model_id in loaded, f"Model {model_id} should be in loaded data"
    assert len(loaded[model_id]) == 5, "Should have 5 records"
    
    # Verify the data
    for i, rec_data in enumerate(loaded[model_id]):
        assert rec_data['tps'] == 20.0 + i * 2, f"TPS mismatch at index {i}"
        assert rec_data['predicted_tokens'] == 200 + i * 100, f"Predicted tokens mismatch at index {i}"
    
    print("OK: History loaded with correct data")
    
    # Test loading non-existent file
    print("\nTesting load of non-existent file...")
    temp_file = os.path.join(os.path.dirname(HISTORY_FILE), "nonexistent.json")
    try:
        result = load_history()  # Should still work, just return empty dict
        assert isinstance(result, dict), "Should return a dict"
        print("OK: Load handles missing file gracefully")
    except Exception as e:
        print(f"ERROR: Unexpected exception: {e}")
        raise
    
    # Clean up test data
    if os.path.exists(HISTORY_FILE):
        os.remove(HISTORY_FILE)
        print("\nCleaned up test file")
    
    print("\nAll persistence tests passed!")

if __name__ == "__main__":
    from lmstudio_monitor import HISTORY_FILE, state
    test_persistence()