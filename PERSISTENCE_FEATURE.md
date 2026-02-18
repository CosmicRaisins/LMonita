# Persistence Feature Implementation

## Summary
Added persistence for model stats history across sessions and model swaps. The application now saves generation history to disk and loads it on startup.

## Features
- **Automatic saving**: History is saved every 30 seconds in the background
- **Cross-session persistence**: History persists when you close and reopen the monitor
- **Model swap support**: Each model maintains its own history, accessible even after switching models
- **Graceful degradation**: If persistence fails, the application continues to work normally
- **Data directory**: `~/.lmstudio-monitor/history.json` (platform-specific)

## Implementation Details

### 1. Configuration (lines 46-50)
```python
DATA_DIR = os.path.expanduser("~/.lmstudio-monitor")
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")
```

### 2. Core Persistence Functions

#### `ensure_data_dir()` (lines 415-420)
- Creates the data directory if it doesn't exist
- Handles errors gracefully with debug logging

#### `load_history()` (lines 422-432)
- Loads history from disk as a dictionary
- Returns empty dict if file doesn't exist or can't be loaded
- Validates JSON structure

#### `save_history(history)` (lines 434-451)
- Converts deque objects to lists for JSON serialization
- Saves all model histories with their generation records
- Includes all fields: timestamp, tps, ttft_sec, total_sec, tokens, stop_reason
- Returns True on success, False on failure

#### `load_history_into_state()` (lines 453-464)
- Loads persisted data into the global state
- Maintains chronological order by adding records to front of deque
- Only loads models that aren't already in memory
- Logs number of models loaded for debugging

### 3. Integration Points

#### Startup (lines 925-934)
```python
# Load persisted history on startup
load_history_into_state()

app = MonitorWidget()

# Start periodic save thread
threading.Thread(target=periodic_save_loop, daemon=True).start()
```

#### Periodic Save (lines 914-920)
```python
def periodic_save_loop():
    """Periodically save history to disk."""
    while True:
        time.sleep(30.0)  # Save every 30 seconds
        if state.model_history:
            save_history(state.model_history)
```

## Data Format

The history file (`history.json`) stores data in this format:

```json
{
  "model-id-1": [
    {
      "timestamp": 1234567890.123,
      "tps": 25.5,
      "ttft_sec": 0.15,
      "total_sec": 2.3,
      "prompt_tokens": 100,
      "predicted_tokens": 200,
      "total_tokens": 300,
      "stop_reason": "eos"
    },
    ...
  ],
  "model-id-2": [...]
}
```

## Testing

Created `test_persistence.py` which verifies:
1. History can be saved successfully
2. Saved data can be loaded correctly
3. Data integrity is maintained (all fields preserved)
4. Missing files are handled gracefully
5. Multiple models can be stored independently

Run tests with: `python test_persistence.py`

## Benefits

1. **No data loss**: History persists across application restarts
2. **Model switching**: Switch between models and their histories remain available
3. **Performance**: Minimal overhead (saves every 30 seconds)
4. **Reliability**: Errors don't affect main functionality
5. **User experience**: Seamless transition between sessions

## Files Modified
- `lmstudio_monitor.py` - Main implementation file

## Usage Notes
- The feature is automatic and requires no user intervention
- Data is stored locally on the user's machine
- No network transmission of historical data
- History is limited to 50 generations per model (configurable via MAX_HISTORY)
