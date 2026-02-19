#!/usr/bin/env python3
"""
Test script for theme functionality in LM Studio Monitor
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lmstudio_monitor import ThemeManager, THEMES, AppState

def test_theme_manager():
    """Test the ThemeManager class"""
    print("Testing ThemeManager...")
    
    # Test initialization
    tm = ThemeManager()
    assert tm.current_theme == "light", f"Expected 'light', got '{tm.current_theme}'"
    assert len(tm.themes) == 2, f"Expected 2 themes, got {len(tm.themes)}"
    print("✓ ThemeManager initialization works")
    
    # Test set_theme
    assert tm.set_theme("dark"), "Failed to set dark theme"
    assert tm.current_theme == "dark", f"Expected 'dark', got '{tm.current_theme}'"
    print("✓ set_theme('dark') works")
    
    # Test invalid theme
    assert not tm.set_theme("invalid"), "Should return False for invalid theme"
    assert tm.current_theme == "dark", f"Theme should remain 'dark', got '{tm.current_theme}'"
    print("✓ set_theme rejects invalid themes")
    
    # Test cycle_theme
    tm.set_theme("light")
    tm.cycle_theme()
    assert tm.current_theme == "dark", f"Expected 'dark' after cycle, got '{tm.current_theme}'"
    print("✓ cycle_theme works (light -> dark)")
    
    tm.cycle_theme()
    assert tm.current_theme == "light", f"Expected 'light' after second cycle, got '{tm.current_theme}'"
    print("✓ cycle_theme works (dark -> light)")
    
    # Test get_colors
    colors = tm.get_colors()
    assert isinstance(colors, dict), "get_colors should return a dict"
    assert "BG" in colors, "Colors dict should contain 'BG'"
    assert "TEXT" in colors, "Colors dict should contain 'TEXT'"
    print("✓ get_colors returns valid color dictionary")
    
    # Test theme definitions
    print("\nTheme definitions:")
    for name, colors in THEMES.items():
        print(f"  {name}: BG={colors['BG']}, TEXT={colors['TEXT']}")
    print("✓ All themes properly defined")

def test_app_state_with_theme():
    """Test AppState includes ThemeManager"""
    print("\nTesting AppState with ThemeManager...")
    
    state = AppState()
    assert hasattr(state, 'theme_manager'), "AppState should have theme_manager attribute"
    assert isinstance(state.theme_manager, ThemeManager), "theme_manager should be a ThemeManager instance"
    print("✓ AppState includes ThemeManager")

if __name__ == "__main__":
    try:
        test_theme_manager()
        test_app_state_with_theme()
        print("\n" + "="*50)
        print("All theme tests passed! ✓")
        print("="*50)
    except AssertionError as e:
        print(f"\n✗ Test failed: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
