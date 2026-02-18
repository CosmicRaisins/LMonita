# LM Studio Monitor

A passive floating desktop widget that monitors LM Studio in real-time, displaying per-generation statistics and TPS history.

## Features

### Real-Time Monitoring
- **Model Information**: Displays loaded model name, architecture, quantization, format, and context length
- **Generation Stats**: Shows tokens per second (TPS), time to first token (TTFT), generation time, and token counts
- **Queue Status**: Tracks queued prediction requests
- **Session Statistics**: Maintains totals for generations and tokens across your session

### Visual Elements
- **Sparkline Graph**: Visual history of TPS performance
- **Status Indicators**: Color-coded dots showing connection and generation status
- **Compact/Collapsed View**: Toggle between full details and a compact summary
- **Draggable Window**: Position the widget anywhere on your screen
- **Pin/Unpin**: Keep the window on top or let it stay behind other applications

### Persistence
- **Cross-Session History**: Generation history persists across application restarts
- **Model-Specific Tracking**: Each model maintains its own history, accessible even after switching models
- **Automatic Saving**: History is saved every 30 seconds in the background

## Installation

```bash
pip install requests
```

## Usage

Run the monitor with:

```bash
python lmstudio_monitor.py [url] [--debug]
```

- `url`: Optional LM Studio server URL (defaults to `http://localhost:1234`)
- `--debug`: Enable debug logging

## Data Sources

The monitor collects data from three sources:

1. **LM Studio API** (`/api/v0/models` or `/v1/models`): Model metadata including architecture, quantization, format, and context length
2. **LM Studio CLI** (`lms ps --json`): Generation status and queue depth
3. **LM Studio Log Stream** (`lms log stream --stats`): Post-generation statistics (TPS, TTFT, tokens, time)

## Controls

- **Drag the title bar**: Move the widget to any position on your screen
- **Pin button (📌)**: Toggle whether the window stays on top of other applications
- **Collapse button (▾/▸)**: Toggle between full view and compact summary
- **Close button (✕)**: Exit the application

## Persistence

Generation history is automatically saved to:
- **Windows**: `%USERPROFILE%\.lmstudio-monitor\history.json`
- **Linux/macOS**: `~/.lmstudio-monitor/history.json`

The file stores up to 50 generations per model, including:
- Timestamp
- Tokens per second (TPS)
- Time to first token (TTFT)
- Total generation time
- Token counts (prompt, predicted, total)
- Stop reason

## Testing

Run the test suite to verify functionality:

```bash
python test_persistence.py  # Test persistence feature
python test_collapse_simple.py  # Test collapse/expand functionality
python test_collapse.py  # Additional collapse tests
```

## Requirements

- Python 3.6+
- LM Studio installed and running
- `requests` library (install with `pip install requests`)

## License

This project is open source. See LICENSE for details.
