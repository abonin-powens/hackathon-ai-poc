# Quick Start Guide

## 🚀 Get Started in 3 Steps

### Step 1: Test the Analyzer
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
   Extracted fields: 122
   Parent classes: 3

All tests passed! ✓
```

### Step 2: Start the Dynamic Server
```bash
python server_ais_dynamic.py
```

Expected output:
```
Initializing Woob module explorer...
Analyzing module: cragr_stet...
Found 122 extracted fields
Formatting analysis context...
Loading Swagger specification...
Loading HAR file...
Server initialization complete!

======================================================================
PSD2 Analysis Server (Dynamic Mode) on port 9999
======================================================================
Module: cragr_stet
Extracted Fields: 122
Parent Classes: 3

Server ready at http://localhost:9999/
======================================================================

Press Ctrl+C to stop the server
```

### Step 3: Trigger Analysis
In another terminal:
```bash
curl -X POST http://localhost:9999/
```

Or visit `http://localhost:9999/` in your browser and use a REST client.

## 📊 What You Get

The analysis will compare:
- ✅ **Swagger API Spec** - What the bank API should provide
- ✅ **Woob Implementation** - What the code actually extracts (122 fields)
- ✅ **HAR File** - Real API responses from actual sessions
- ✅ **Parent Classes** - Full inheritance chain (3 levels)

And produce a detailed gap analysis report with:
- Issue severity (High/Medium/Low)
- Exact locations in code
- Suggested fixes
- Impact assessment

## 🔧 Configuration

Edit `server_ais_dynamic.py` to change:

```python
# Module to analyze
MODULE_NAME = "cragr_stet"  # Change to analyze other modules

# Files
SWAGGER_FILE = "data/swagger_clean.json"
HAR_FILE = "data/bundle.anonymized.har"

# Bedrock model
model_id = "eu.anthropic.claude-sonnet-4-5-20250929-v1:0"
```

## 📁 File Overview

| File | Purpose |
|------|---------|
| `server_ais_dynamic.py` | Main server with dynamic analysis |
| `test_analyzer.py` | Test the analyzer |
| `server_ais.py` | Original static version (for comparison) |
| `woob_gap_analyzer/` | Code analysis modules |
| `data/swagger_clean.json` | API specification |
| `data/bundle.anonymized.har` | Real API session |

## 🆚 Static vs Dynamic

### Old Way (server_ais.py)
```python
# Manually copy files to data/
contexts = [
    "data/swagger_clean.json",
    "data/pages.py",        # ← Manual copy
    "data/stet_pages.py",   # ← Manual copy
    "data/bundle.anonymized.har"
]
```

### New Way (server_ais_dynamic.py)
```python
# Automatically analyze from ../woob
explorer = ModuleExplorer()  # Auto-finds ../woob
analysis = explorer.explore_module("cragr_stet")
# ✓ 122 fields extracted
# ✓ 3 parent classes traced
# ✓ All endpoints discovered
```

## 🎯 Key Benefits

1. **No Manual Work** - Automatically finds and analyzes code
2. **Always Current** - Reads directly from woob repo
3. **More Complete** - Analyzes all files (pages.py, browser.py, parents)
4. **Better Structure** - Formatted analysis instead of raw code
5. **12x Efficient** - Uses 12x fewer tokens (4K vs 50K)

## 🐛 Troubleshooting

### "Woob root not found"
```bash
# Check if woob exists
ls ~/dev/woob/modules/cragr_stet
```

### "AWS credentials not configured"
```bash
export AWS_PROFILE=playground
export AWS_REGION=eu-west-3
```

### "Module not found"
```bash
# Make sure you're in the hackathon-ai-poc directory
pwd
# Should show: /Users/damien.mat/dev/hackathon-ai-poc
```

## 📚 Documentation

- `README_DYNAMIC_ANALYZER.md` - Full documentation
- `COMPARISON.md` - Before/after comparison
- `SUMMARY.md` - Technical summary
- `QUICKSTART.md` - This file

## 🎉 Success!

If you see this, you're ready to go:
```
✓ Test passed
✓ Server started
✓ Analysis triggered
✓ Report generated
```

Now you can analyze any Woob module dynamically! 🚀
