# Collapse Feature Implementation

## Summary
Added a collapsible widget feature that allows users to collapse the TPS history and session stats sections, displaying only a summary of model info.

## Changes Made

### 1. Added Collapse State Variable
- Added `_collapsed` state variable in `MonitorWidget.__init__()` (line 429)
- Initialized to `False` (expanded by default)

### 2. Added Collapse Button
- Added collapse button to the title bar (lines 518-520)
- Uses "⊖" symbol when expanded, "⊕" when collapsed
- Button is positioned next to the pin button

### 3. Stored Widget References for Collapsing
- `self.model_card` - Model info card (line 543)
- `self.last_gen_section` - Last generation stats section (line 574)
- `self.tps_history_section` - TPS history section (line 601)
- `self.session_section` - Session totals section (line 623)

### 4. Implemented Toggle Functionality
- `_toggle_collapse()` method (lines 660-666):
  - Toggles the `_collapsed` state
  - Updates button icon and color
  - Calls `_update_collapsed_state()` to show/hide sections

- `_update_collapsed_state()` method (lines 668-679):
  - When collapsed: hides last generation, TPS history, and session sections
  - When expanded: shows all sections again
  - Uses `pack_forget()` and `pack()` to toggle visibility

## Usage
1. Click the "⊖" button in the title bar to collapse the widget
2. The widget will show only the model info card
3. Click the "⊕" button to expand the widget again
4. All sections (Last Generation, TPS History, Session) will be visible

## Testing
- Created `test_collapse_simple.py` to verify toggle logic works correctly
- Main application starts successfully with new feature
- No breaking changes to existing functionality

## Files Modified
- `lmstudio_monitor.py` - Main implementation file (renamed from lmstudio-monitor.py)
