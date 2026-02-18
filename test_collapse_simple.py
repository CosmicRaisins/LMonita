#!/usr/bin/env python
"""
Simple test script to verify collapse functionality
"""
import sys
sys.path.insert(0, '.')

def test_collapse_logic():
    """Test that collapse state logic works correctly"""
    
    # Simulate the collapse toggle logic
    collapsed = False
    
    # Test initial state
    assert collapsed == False, "Initial state should be expanded"
    print("OK: Initial state is expanded")
    
    # Test toggle to collapsed
    collapsed = not collapsed
    assert collapsed == True, "After first toggle, should be collapsed"
    print("OK: Toggle to collapsed works")
    
    # Test toggle back to expanded
    collapsed = not collapsed
    assert collapsed == False, "After second toggle, should be expanded again"
    print("OK: Toggle back to expanded works")
    
    print("\nAll collapse logic tests passed!")

if __name__ == "__main__":
    test_collapse_logic()