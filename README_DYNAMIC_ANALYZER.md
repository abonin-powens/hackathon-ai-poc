# Dynamic Woob Gap Analyzer

This project integrates dynamic code analysis of Woob modules with Bedrock AI to identify gaps between PSD2 API specifications and Woob implementations.

## Overview

Instead of manually providing code files, the system now:
1. **Dynamically analyzes** the Woob codebase from `../woob`
2. **Traces inheritance chains** to understand parent class implementations
3. **Extracts all fields** being parsed from API responses
4. **Formats the analysis** for LLM consumption
5. **Compares** against Swagger specs and HAR files

## Directory Structure

```
~/dev/
├── hackathon-ai-poc/          # This project
│   ├── server_ais.py          # Original static file server
│   ├── server_ais_dynamic.py  # NEW: Dynamic analyzer server
│   ├── test_analyzer.py       # Test script
│   ├── woob_gap_analyzer/     # Analyzer modules
│   │   ├── compare_scraping.py
│   │   └── api_gap_analyzer/
│   │       ├── code_analyzer.py
│   │       ├── context_formatter.py
│   │       ├── explorer.py
│   │       └── ...
│   └── data/
│       ├── swagger_clean.json
│       └── bundle.anonymized.har
└── woob/                      # Woob codebase (sibling directory)
    └── modules/
        └── cragr_stet/
            ├── browser.py
            ├── pages.py
            └── ...
```

## Setup

### Prerequisites

1. Python 3.8+
2. AWS credentials configured (for Bedrock)
3. Woob repository cloned at `~/dev/woob`

### Install Dependencies

```bash
pip install boto3
```

## Usage

### 1. Test the Analyzer

First, verify the analyzer can find and analyze the Woob codebase:

```bash
python test_analyzer.py
```

Expected output:
```
======================================================================
Testing Woob Gap Analyzer
======================================================================

1. Initializing ModuleExplorer...
   ✓ Explorer initialized
   Woob root: /Users/damien.mat/dev/woob
   Woob root exists: True

2. Analyzing cragr_stet module...
   ✓ Module analyzed successfully
   Main file: modules/cragr_stet/pages.py
   Extracted fields: 122
   Parent classes: 3
   ...

All tests passed! ✓
```

### 2. Run the Dynamic Analysis Server

Start the server with dynamic code analysis:

```bash
python server_ais_dynamic.py
```

The server will:
- Analyze the `cragr_stet` module at startup
- Extract all fields and parent classes
- Load Swagger and HAR files
- Start HTTP server on port 9999

### 3. Trigger Analysis

Send a POST request to trigger the analysis:

```bash
curl -X POST http://localhost:9999/
```

Or use the web interface (if you have one).

## What Changed

### Original Approach (`server_ais.py`)

- Manually loaded static files:
  - `data/pages.py` - cragr_stet pages
  - `data/stet_pages.py` - parent class pages
  - `data/swagger_clean.json` - API spec
  - `data/bundle.anonymized.har` - HAR file

### New Approach (`server_ais_dynamic.py`)

- **Dynamically analyzes** the Woob module:
  - Finds `modules/cragr_stet/pages.py` in `../woob`
  - Traces parent classes automatically
  - Extracts all `obj_*` methods and `Dict()` filters
  - Analyzes browser.py for endpoint implementations
  - Formats everything for LLM consumption

- **Benefits**:
  - Always up-to-date with latest code
  - No need to manually copy files
  - Understands full inheritance chain
  - Provides structured field mapping

## Configuration

Edit `server_ais_dynamic.py` to change:

```python
# Module to analyze
MODULE_NAME = "cragr_stet"

# Static files
SWAGGER_FILE = "data/swagger_clean.json"
HAR_FILE = "data/bundle.anonymized.har"

# Bedrock model
model_id = "eu.anthropic.claude-sonnet-4-5-20250929-v1:0"
```

## Analysis Output

The analyzer provides:

### 1. Extracted Fields
All fields being extracted from API responses:
- Field name (e.g., `obj_id`, `obj_balance`)
- Extraction method (e.g., `Dict("accountId")`)
- Source file and line number
- Parent class if inherited

### 2. Parent Classes
Full inheritance chain:
- `woob_modules.stet.pages.AccountsPage`
- `woob_modules.stet.pages.TransactionsPage`
- `woob_modules.stet.pages.PaymentRequestPage`

### 3. Browser Endpoints
URL patterns and page classes:
- `accounts` → `/accounts` (AccountsPage)
- `transactions` → `/accounts/{account_id}/transactions` (TransactionsPage)

### 4. Formatted Context
Structured markdown for LLM:
```markdown
# Woob Implementation Analysis

## Module: cragr_stet

### Overview
- Total fields extracted: 122
- Parent classes analyzed: 3

### Extracted Fields
...
```

## Troubleshooting

### "Woob root not found"

Make sure the Woob repository is at `~/dev/woob`:
```bash
ls ~/dev/woob/modules/cragr_stet
```

### "Module not found: strands"

The `agent_config.py` file has been removed from dependencies. If you see this error, make sure you're using the updated `explorer.py`.

### "AWS credentials not configured"

Set up AWS credentials:
```bash
export AWS_PROFILE=playground
export AWS_REGION=eu-west-3
```

Or configure in `~/.aws/credentials`.

## Next Steps

1. **Add more modules**: Change `MODULE_NAME` to analyze other modules
2. **Customize prompts**: Edit `system_prompt` in `server_ais_dynamic.py`
3. **Export reports**: Save analysis results to markdown files
4. **Compare versions**: Analyze multiple module versions

## Files Modified

- `woob_gap_analyzer/api_gap_analyzer/agent_config.py` - Auto-detect `../woob`
- `woob_gap_analyzer/api_gap_analyzer/explorer.py` - Removed strands dependency, auto-detect `../woob`
- `woob_gap_analyzer/api_gap_analyzer/code_analyzer.py` - Auto-detect `../woob`

## Files Created

- `server_ais_dynamic.py` - New dynamic analysis server
- `test_analyzer.py` - Test script for analyzer
- `README_DYNAMIC_ANALYZER.md` - This file
