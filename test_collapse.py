#!/usr/bin/env python
"""
Test script to verify collapse functionality
"""
import sys
sys.path.insert(0, '.')

# Import the monitor module
import lmstudio_monitor
from lmstudio_monitor import MonitorWidget
import tkinter as tk

def test_collapse():
    """Test that collapse state works correctly"""
    root = tk.Tk()
    
    # Create widget
    widget = MonitorWidget.__new__(MonitorWidget)
    widget._collapsed = False
    
    # Test initial state
    assert widget._collapsed == False, "Initial state should be expanded"
    print("OK: Initial state is expanded")
    
    # Test toggle to collapsed
    widget._toggle_collapse()
    assert widget._collapsed == True, "After first toggle, should be collapsed"
    print("OK: Toggle to collapsed works")
    
    # Test toggle back to expanded
    widget._toggle_collapse()
    assert widget._collapsed == False, "After second toggle, should be expanded again"
    print("OK: Toggle back to expanded works")
    
    print("\nAll collapse tests passed!")

if __name__ == "__main__":
    test_collapse()