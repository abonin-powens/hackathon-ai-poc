# Architecture Overview

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     HTTP Client (curl/browser)                   │
└────────────────────────────┬────────────────────────────────────┘
                             │ POST /
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                   server_ais_dynamic.py                          │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  Startup Phase                                           │   │
│  │  1. Initialize ModuleExplorer                            │   │
│  │  2. Analyze cragr_stet module                            │   │
│  │  3. Load Swagger & HAR files                             │   │
│  │  4. Format context for LLM                               │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  Request Phase                                           │   │
│  │  1. Receive POST request                                 │   │
│  │  2. Build conversation with:                             │   │
│  │     - Woob analysis (structured)                         │   │
│  │     - Swagger spec (JSON)                                │   │
│  │     - HAR file (JSON)                                    │   │
│  │  3. Send to Bedrock                                      │   │
│  │  4. Return gap analysis report                           │   │
│  └─────────────────────────────────────────────────────────┘   │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    AWS Bedrock (Claude)                          │
│                                                                   │
│  Analyzes:                                                       │
│  - API Specification (Swagger)                                   │
│  - Woob Implementation (Structured Analysis)                     │
│  - Real API Behavior (HAR)                                       │
│                                                                   │
│  Produces:                                                       │
│  - Gap Analysis Report                                           │
│  - Issue List (High/Medium/Low)                                  │
│  - Suggested Fixes                                               │
└─────────────────────────────────────────────────────────────────┘
```

## Code Analysis Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                    ModuleExplorer                                │
│                                                                   │
│  explore_module("cragr_stet")                                    │
│         │                                                         │
│         ▼                                                         │
│  ┌──────────────────────────────────────────────────────┐       │
│  │ Step 1: Analyze Main Files                           │       │
│  │  - modules/cragr_stet/pages.py                       │       │
│  │  - modules/cragr_stet/browser.py                     │       │
│  │  Extract: classes, obj_* methods, Dict() filters     │       │
│  └──────────────────────────────────────────────────────┘       │
│         │                                                         │
│         ▼                                                         │
│  ┌──────────────────────────────────────────────────────┐       │
│  │ Step 2: Trace Parent Classes                         │       │
│  │  - Find imports (from woob_modules.stet.pages)       │       │
│  │  - Resolve to modules/stet/pages.py                  │       │
│  │  - Build inheritance chain                           │       │
│  └──────────────────────────────────────────────────────┘       │
│         │                                                         │
│         ▼                                                         │
│  ┌──────────────────────────────────────────────────────┐       │
│  │ Step 3: Analyze Parent Files (Recursive)             │       │
│  │  - modules/stet/pages.py                             │       │
│  │  - modules/stet/browser.py                           │       │
│  │  - Continue up the chain...                          │       │
│  └──────────────────────────────────────────────────────┘       │
│         │                                                         │
│         ▼                                                         │
│  ┌──────────────────────────────────────────────────────┐       │
│  │ Step 4: Build Field Mapping                          │       │
│  │  - Map each field to its source                      │       │
│  │  - Track extraction methods                          │       │
│  │  - Identify transformations                          │       │
│  └──────────────────────────────────────────────────────┘       │
│         │                                                         │
│         ▼                                                         │
│  ┌──────────────────────────────────────────────────────┐       │
│  │ Result: Complete Analysis                            │       │
│  │  - 122 extracted fields                              │       │
│  │  - 3 parent classes                                  │       │
│  │  - All endpoints                                     │       │
│  │  - Full inheritance chain                            │       │
│  └──────────────────────────────────────────────────────┘       │
└─────────────────────────────────────────────────────────────────┘
```

## Directory Structure

```
~/dev/
├── hackathon-ai-poc/                    # This project
│   │
│   ├── server_ais_dynamic.py            # Main server
│   │   └── Uses: ModuleExplorer, ContextFormatter, Bedrock
│   │
│   ├── test_analyzer.py                 # Test script
│   │   └── Tests: ModuleExplorer, ContextFormatter
│   │
│   ├── woob_gap_analyzer/               # Analysis modules
│   │   ├── compare_scraping.py          # CLI tool (standalone)
│   │   └── api_gap_analyzer/
│   │       ├── explorer.py              # Orchestrates analysis
│   │       │   └── Uses: CodeAnalyzer
│   │       ├── code_analyzer.py         # Parses Python code
│   │       │   └── Extracts: classes, methods, imports
│   │       ├── context_formatter.py     # Formats for LLM
│   │       │   └── Produces: Markdown analysis
│   │       ├── swagger_parser.py        # Parses OpenAPI specs
│   │       ├── bedrock_client.py        # AWS Bedrock client
│   │       └── system_prompt.py         # LLM prompts
│   │
│   └── data/
│       ├── swagger_clean.json           # API specification
│       └── bundle.anonymized.har        # Real API session
│
└── woob/                                # Woob codebase (sibling)
    └── modules/
        ├── cragr_stet/                  # Target module
        │   ├── pages.py                 # Main implementation
        │   ├── browser.py               # Endpoint definitions
        │   └── module.py                # Module config
        └── stet/                        # Parent module
            ├── pages.py                 # Parent implementation
            └── browser.py               # Parent endpoints
```

## Data Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                         Input Sources                            │
└─────────────────────────────────────────────────────────────────┘
         │                    │                    │
         │                    │                    │
    ┌────▼────┐         ┌────▼────┐         ┌────▼────┐
    │  Woob   │         │ Swagger │         │   HAR   │
    │  Code   │         │  Spec   │         │  File   │
    │ (../woob)│        │ (JSON)  │         │ (JSON)  │
    └────┬────┘         └────┬────┘         └────┬────┘
         │                    │                    │
         ▼                    ▼                    ▼
    ┌────────────────────────────────────────────────┐
    │         ModuleExplorer                         │
    │  - Analyzes code structure                     │
    │  - Traces inheritance                          │
    │  - Extracts fields                             │
    └────────────────┬───────────────────────────────┘
                     │
                     ▼
    ┌────────────────────────────────────────────────┐
    │      ContextFormatter                          │
    │  - Formats as structured markdown              │
    │  - Groups by source (main/parent)              │
    │  - Shows extraction methods                    │
    └────────────────┬───────────────────────────────┘
                     │
                     ▼
    ┌────────────────────────────────────────────────┐
    │         Bedrock (Claude)                       │
    │  - Compares all sources                        │
    │  - Identifies discrepancies                    │
    │  - Generates gap report                        │
    └────────────────┬───────────────────────────────┘
                     │
                     ▼
    ┌────────────────────────────────────────────────┐
    │         Gap Analysis Report                    │
    │  - Issue list with severity                    │
    │  - Exact locations                             │
    │  - Suggested fixes                             │
    │  - Impact assessment                           │
    └────────────────────────────────────────────────┘
```

## Component Responsibilities

### ModuleExplorer
- **Input**: Module name (e.g., "cragr_stet")
- **Process**:
  1. Find module in ../woob
  2. Analyze pages.py and browser.py
  3. Trace parent classes recursively
  4. Extract all obj_* methods and Dict() filters
  5. Build field mapping
- **Output**: Structured analysis dictionary

### CodeAnalyzer
- **Input**: File path (e.g., "modules/cragr_stet/pages.py")
- **Process**:
  1. Parse Python code
  2. Extract imports, classes, methods
  3. Find obj_* patterns
  4. Find Dict() filters
  5. Extract URL endpoints
- **Output**: Code structure dictionary

### ContextFormatter
- **Input**: Analysis dictionary from ModuleExplorer
- **Process**:
  1. Format as markdown
  2. Group fields by source
  3. Show extraction methods
  4. List parent classes
  5. Show endpoints
- **Output**: Formatted markdown string

### Bedrock Client
- **Input**: 
  - Woob analysis (markdown)
  - Swagger spec (JSON)
  - HAR file (JSON)
  - System prompt
- **Process**:
  1. Build conversation
  2. Call AWS Bedrock API
  3. Parse response
- **Output**: Gap analysis report

## Key Design Decisions

### 1. Auto-detect ../woob
**Why**: No manual configuration needed
**How**: Calculate path relative to current file
```python
current_file = Path(__file__).resolve()
hackathon_root = current_file.parent.parent.parent
woob_root = hackathon_root.parent / "woob"
```

### 2. Recursive Parent Analysis
**Why**: Full understanding of inheritance
**How**: Trace imports, analyze parent files, repeat
```python
def _analyze_parents(parent_classes):
    for parent in parent_classes:
        analyze_parent_recursive(parent)
```

### 3. Structured Output
**Why**: Better LLM comprehension, fewer tokens
**How**: Format as markdown with sections
```markdown
## Extracted Fields
- field_name (type)
  - Extraction: Dict("path")
  - Source: file.py
```

### 4. Startup Analysis
**Why**: Fast response times
**How**: Analyze once at startup, cache results
```python
# At startup
woob_analysis = explorer.explore_module("cragr_stet")
# In request handler - use cached analysis
```

## Performance Characteristics

| Metric | Value |
|--------|-------|
| Startup Time | 2-3 seconds |
| Analysis Cache | In-memory |
| Token Reduction | 12x (50K → 4K) |
| Context Size | 16KB (structured) |
| Files Analyzed | 6+ (main + parents) |
| Fields Extracted | 122 |
| Parent Depth | 3 levels |

## Scalability

### Current
- Single module analysis
- In-memory caching
- Synchronous processing

### Future Enhancements
- Multi-module analysis
- Persistent caching (Redis/SQLite)
- Async processing
- Incremental updates
- Diff analysis (compare versions)
