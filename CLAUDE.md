# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**visualizeLOB** is a Limit Order Book (LOB) dynamic visualization tool. It replays and animates how buy/sell order book snapshots change over time, designed for Chinese A-share market data.

## Environment Setup & Commands

This project uses `uv` for Python package management (Python 3.12).

```powershell
uv sync                  # Install all dependencies from uv.lock
jupyter lab demo.ipynb   # Run the demo notebook
```

There are no build steps, test suite, or linter configured.

## Architecture

Everything lives in a single module: [`visualize_lob.py`](visualize_lob.py). The four components in dependency order:

### 1. `InternalOrderBook` (class)
Internal simulation engine used **only** for toy data generation. Maintains bid/ask dicts with price-time priority matching. Not part of the public API.

### 2. `generate_toy_data()` (function)
Generates synthetic LOB data: 101 order book snapshots + 100 trigger events, saved as `toy_data/orderbook.parquet` and `toy_data/triggerInfo.parquet`. Only records frames where the Top-10 snapshot actually changed.

### 3. `LOBDataLoader` (class)
Reads parquet files and filters by stock code, time range, or index range. Key methods: `get_frame(pos)` and `get_trigger(pos)`.

### 4. `LOBVisualizer` (class)
Rendering layer on top of `LOBDataLoader`:
- `plot_single_frame(pos)` — stacked bar chart showing base volume + delta with dividing lines
- `plot_animation(...)` — Plotly animation with play/pause/slider controls and `triggerType` labels

Animation uses a two-phase approach: **change phase** (delta visualization with color intensity) → **shift phase** (x-axis repositioning when best prices move).

## Data Schemas

**orderbook.parquet**: columns `code` (int), `adjIndex` (int, monotonic but may have gaps), `time`/`serverTime` (datetime), `bidPx1-10`/`bidVlm1-10`, `askPx1-10`/`askVlm1-10`.

**triggerInfo.parquet**: columns `code` (int), `adjIndex` (int), `triggerType` (str: `"order"` or `"cancel"`).

`(code, adjIndex)` is the composite key linking the two tables.

## Key Conventions

- All comments in the codebase are in Chinese.
- Color scheme: blue = bids, red = asks; darker shade = volume increase, lighter = decrease.
- `adjIndex` is monotonic but not contiguous — gaps are valid and expected.
- The demo workflow is in [`demo.ipynb`](demo.ipynb): generate data → load → static frame → animation.
