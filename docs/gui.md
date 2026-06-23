# tiddl-gui User Guide

## Overview

`tiddl-gui` is a PySide6 desktop application for downloading music from Tidal.
It wraps the `tiddl` CLI core library with a graphical interface.

## Quick Start

```bash
pip install -e ".[gui]"
tiddl-gui
```

## Workflow

### 1. Login

Menu **Auth > Login** or toolbar **Login** button. Your browser opens with a
verification URL — complete the Tidal login there.

### 2. Add Resources

Three tabs in the left panel:

- **URL Input**: Paste Tidal links or `type/id` shorthand, click Parse, Add to Queue
- **Search**: Enter query, select types, click Search. Double-click to add instantly
- **Favorites**: Load your Tidal favorites, filter by type, add to queue

### 3. Album Preview

Adding an album opens a preview dialog showing cover art, artist, track list
with checkboxes. Uncheck tracks you don't want before downloading.

### 4. Download

Select quality on the right panel and click **Start Download**.
Each track gets its own progress bar.

### 5. Settings

**File > Settings** (Ctrl+,) opens a 6-tab config editor.

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| Ctrl+, | Settings |
| Ctrl+Q | Quit |
| Ctrl+Enter | Search |

## Error Messages

Every error shows: what happened → technical detail → how to fix.
The **Copy Details** button copies all three for reporting.
