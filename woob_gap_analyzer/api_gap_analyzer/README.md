# Woob API Gap Analyzer

A comprehensive tool for analyzing the gap between Bank API specifications (PSD2 Swagger) and Woob module implementations. Uses AWS Bedrock with Claude AI to identify discrepancies, missing fields, type mismatches, and other issues.

## Overview

The Woob API Gap Analyzer compares:
- **API Specification**: What the Bank API theoretically provides (Swagger/OpenAPI JSON)
- **Woob Implementation**: What the Woob module actually extracts from the API

The tool generates a detailed markdown report identifying all gaps, with severity levels, code locations, and suggested fixes.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│ 1. Swagger Spec (JSON)                                  │
│    ↓                                                    │
│    SwaggerParser.parse_swagger()                        │
└─────────────────────────────────────────────────────────┘
                     ↓
┌─────────────────────────────────────────────────────────┐
│ 2. Woob Module (Python)                                 │
│    ↓                                                    │
│    ModuleExplorer.explore_module()                      │
│    - Analyzes code structure                            │
│    - Traces inheritance chains                          │
│    - Identifies extraction patterns                     │
└─────────────────────────────────────────────────────────┘
                     ↓
┌─────────────────────────────────────────────────────────┐
│ 3. Woob Analysis (Dict)                                 │
│    ↓                                                    │
│    ContextFormatter.format_comparison_context()         │
│    - Converts to markdown                               │
│    - Combines with Swagger spec                         │
└─────────────────────────────────────────────────────────┘
                     ↓
┌─────────────────────────────────────────────────────────┐
│ 4. Formatted Context (Markdown)                         │
│    ↓                                                    │
│    BedrockAnalyzer.analyze_gap()                        │
│    - Sends to AWS Bedrock                               │
│    - Uses Claude AI for analysis                        │
└─────────────────────────────────────────────────────────┘
                     ↓
┌─────────────────────────────────────────────────────────┐
│ 5. Analysis Results (Issues, gaps, recommendations)     │
│    ↓                                                    │
│    ReportGenerator.format_report_with_summary()         │
│    - Creates markdown report                            │
│    - Adds statistics and metadata                       │
└─────────────────────────────────────────────────────────┘
                     ↓
┌─────────────────────────────────────────────────────────┐
│ 6. Markdown Report (File or stdout)                     │
└─────────────────────────────────────────────────────────┘
```

## Installation

### Prerequisites

- Python 3.13+
- AWS account with Bedrock access (via JumpCloud SSO)
- AWS CLI configured with SSO profile

### Setup

1. **Create virtual environment** (Python 3.13):
   ```bash
   python3.13 -m venv .venv-agents
   source .venv-agents/bin/activate
   ```

2. **Install dependencies**:
   ```bash
   uv pip install strands-agents boto3 pyyaml
   ```

3. **Configure AWS**:
   ```bash
   # Ensure ~/.aws/credentials has:
   [playground-hackathon]
   sso_start_url = https://d-80672338a9.awsapps.com/start
   sso_region = eu-west-3
   sso_account_id = 966836753662
   sso_role_name = DevelopperAccess
   
   # Authenticate
   aws sso login --profile sso
   ```

4. **Set environment variables** (optional):
   ```bash
   export AWS_PROFILE=playground-hackathon
   export AWS_REGION=eu-west-3
   ```

## Usage

### Quick Start

The simplest way to run the analyzer:

```bash
python woob/dev_tools/compare_scraping.py --module cragr_stet
```

This automatically:
- Finds `modules/cragr_stet/api-spec/swagger.json`
- Saves report to `modules/cragr_stet/api-spec/gap_analysis_cragr_stet.md`
- Displays execution time summary

### Command-Line Options

- `--module MODULE` (required): Woob module name (e.g., `cragr_stet`)
- `--swagger SWAGGER` (optional): Path to Swagger/OpenAPI JSON file (auto-detected from `modules/{module}/api-spec/swagger.json`)
- `--output OUTPUT` (optional): Output file path (default: `modules/{module}/api-spec/gap_analysis_{module}.md`)
- `--capability CAPABILITY` (optional): Capability type (default: `Account`)
- `--model MODEL` (optional): Bedrock model ID (default: Claude Haiku)
- `-v, --verbose`: Enable verbose logging

### Examples

**Auto-detect everything (recommended)**:

```bash
python woob/dev_tools/compare_scraping.py --module cragr_stet
```

**Specify custom Swagger path**:

```bash
python woob/dev_tools/compare_scraping.py \
  --module cragr_stet \
  --swagger path/to/custom-swagger.json
```

**Specify custom output path**:

```bash
python woob/dev_tools/compare_scraping.py \
  --module cragr_stet \
  --output custom_report.md
```

**Verbose output with timing**:

```bash
python woob/dev_tools/compare_scraping.py --module cragr_stet -v
```

**Use different Bedrock model**:

```bash
python woob/dev_tools/compare_scraping.py \
  --module cragr_stet \
  --model eu.anthropic.claude-sonnet-4-5-20250929-v1:0
```

### File Organization

The tool expects this structure:

```
modules/
├── cragr_stet/
│   ├── api-spec/
│   │   ├── swagger.json              (standardized name)
│   │   └── gap_analysis_cragr_stet.md (generated report)
│   ├── pages.py
│   ├── browser.py
│   └── ...
```

To set up a new module:

```bash
mkdir -p modules/your_module/api-spec
cp your_swagger.json modules/your_module/api-spec/swagger.json
python woob/dev_tools/compare_scraping.py --module your_module
```

## Execution Time Summary

After each analysis, the tool displays a timing breakdown:

```
======================================================================
EXECUTION TIME SUMMARY
======================================================================
Step 1 - Load Swagger spec:        0.0s
Step 2 - Explore Woob module:      0.3s
Step 3 - Format context:           0.0s
Step 4 - Bedrock analysis:         2.0m
Step 5 - Generate report:          0.0s
Step 6 - Save report:              0.0s
----------------------------------------------------------------------
TOTAL TIME:                        2.0m
======================================================================
```

**Typical Performance**:
- Swagger parsing: < 0.1s
- Module exploration: 0.2-0.5s
- Context formatting: < 0.1s
- Bedrock analysis: 1-3 minutes (depends on API latency)
- Report generation: < 0.1s
- **Total**: 1-3 minutes

The Bedrock analysis step is typically the longest due to LLM processing time.

## Report Format

The generated report includes:

### Header
- Module name
- API name
- Generation timestamp
- Tool information

### Summary
- Total issues found
- Issues by severity (High, Medium, Low)
- Whether recommendations are provided

### Issues
Each issue includes:
- **Issue Number**: Unique identifier
- **Severity**: High/Medium/Low
- **Location**: File name and line number
- **Problem**: Clear explanation of the issue
- **API Specification Says**: Relevant excerpt from Swagger spec
- **Woob Implementation Does**: Relevant code snippet
- **Impact**: Consequences of the issue
- **Suggested Fix**: Corrected code or steps to fix

### Footer
- Usage instructions
- Next steps checklist
- How to apply fixes

## Key Features

### Comprehensive Analysis

- **Dual-file exploration**: Analyzes both `pages.py` (data extraction) and `browser.py` (endpoint definitions)
- **Inheritance tracing**: Recursively traces parent classes to find all inherited implementations
- **URL endpoint mapping**: Extracts endpoint definitions and maps them to page classes
- **Field extraction**: Identifies all `obj_*` methods and `Dict()` filters with their extraction paths
- **Browser implementation**: Captures endpoint URLs and their corresponding page handlers

### Intelligent Context Formatting

The tool formats analysis data specifically for LLM processing:
- Swagger spec converted to readable markdown
- Woob analysis includes extraction methods with actual Dict paths
- Parent browser implementations with endpoint definitions
- Field mappings showing source files and extraction logic

### AI-Powered Analysis

Uses AWS Bedrock with Claude AI to:
- Identify API specification gaps
- Detect data type mismatches
- Find missing implementations
- Suggest fixes with code examples
- Provide severity levels (High/Medium/Low)

## Components

### swagger_parser.py

Parses Swagger/OpenAPI specifications and extracts:
- AIS (Account Information Service) endpoints
- Response schemas
- Field definitions with types

### explorer.py

Analyzes Woob modules to understand:
- Class hierarchies and inheritance
- Data extraction patterns
- Field mappings
- Parent class implementations
- Browser endpoint definitions

### code_analyzer.py

Performs static code analysis:
- Extracts imports (including multi-line)
- Finds class definitions
- Identifies `Dict()` filters
- Locates `obj_*` methods and attributes
- Extracts URL endpoint definitions

### context_formatter.py
Formats analysis data for LLM:
- Converts Swagger spec to readable format
- Formats Woob analysis with field listings
- Combines both into comparison context

### context_formatter.py

Formats analysis data for LLM:
- Converts Swagger spec to readable format
- Formats Woob analysis with field listings
- Combines both into comparison context
- Includes parent browser implementations
- Shows endpoint URL mappings

### bedrock_client.py

Manages AWS Bedrock integration:
- AWS SSO authentication
- Sends analysis requests to Claude
- Handles responses and token usage
- Manages token counting

### agent_config.py

Strands Agent configuration for autonomous exploration:
- Initializes agent with Woob-specific tools
- Provides file reading and directory listing
- Enables pattern searching in code
- Supports future autonomous analysis

### system_prompt.py

Provides comprehensive analysis instructions:
- PSD2 API analysis guidelines
- Issue reporting format
- Key areas to check
- Example issue reports

### report_generator.py

Generates markdown reports:
- Formats Bedrock responses
- Adds metadata and statistics
- Saves to file or stdout

## AWS Setup Details

### JumpCloud SSO Authentication

1. **Open JumpCloud Console**: https://console.jumpcloud.com/userconsole#/
2. **Select AWS Account**: Find "playground" account with "DevelopperAccess" role
3. **Click DevelopperAccess**: Opens AWS Console
4. **Configure AWS CLI**:
   ```bash
   # Add to ~/.aws/credentials
   [sso]
   sso_start_url = https://d-80672338a9.awsapps.com/start
   sso_region = eu-west-3
   
   [playground-hackathon]
   sso_start_url = https://d-80672338a9.awsapps.com/start
   sso_region = eu-west-3
   sso_account_id = 966836753662
   sso_role_name = DevelopperAccess
   ```

5. **Authenticate**:
   ```bash
   aws sso login --profile sso
   ```

6. **Verify Access**:
   ```bash
   aws bedrock list-foundation-models \
     --profile playground-hackathon \
     --region eu-west-3 | grep modelId
   ```

## Troubleshooting

### AWS Authentication Errors

**Error**: "Failed to initialize AWS Bedrock client"

**Solution**:
1. Run `aws sso login --profile sso`
2. Verify profile in `~/.aws/credentials`
3. Check AWS_PROFILE and AWS_REGION environment variables

### Module Not Found

**Error**: "Module not found: cragr_stet"

**Solution**:
1. Verify module exists in `modules/cragr_stet/`
2. Check module name spelling
3. Ensure you're running from workspace root

### Swagger Spec Errors

**Error**: "Invalid JSON in Swagger spec"

**Solution**:
1. Validate JSON: `python -m json.tool swagger.json`
2. Check file path is correct
3. Ensure file is readable

## Performance

- **Analysis Time**: 1-3 minutes (mostly Bedrock LLM processing)
- **Token Usage**: ~40,000 input tokens, ~7,000 output tokens per analysis
- **Report Size**: 20-40 KB depending on issues found
- **Module Exploration**: < 1 second for typical modules
- **Swagger Parsing**: < 0.1 seconds

## What's New (v1.0 - Production Ready)

### Enhanced Analysis
- ✅ Dual-file exploration (pages.py + browser.py)
- ✅ Recursive parent class analysis
- ✅ URL endpoint extraction and mapping
- ✅ Comprehensive field extraction with sources

### Improved User Experience
- ✅ Auto-detection of Swagger specs
- ✅ Standardized folder structure (`api-spec/`)
- ✅ Execution time tracking with breakdown
- ✅ Better error messages and logging

### Production Features
- ✅ Strands Agent integration (ready for autonomous analysis)
- ✅ Comprehensive documentation
- ✅ Tested with real Woob modules
- ✅ AWS SSO authentication support

## Limitations

- Currently supports AIS (Account Information Service) endpoints
- Requires AWS Bedrock access
- Analyzes Python-based Woob modules only
- Swagger/OpenAPI format required

## Future Enhancements

- Support for PIS (Payment Initiation Service) endpoints
- Support for other API formats (GraphQL, REST)
- Batch analysis of multiple modules
- Integration with CI/CD pipelines
- Custom analysis templates
- Autonomous analysis via Strands Agent

## Contributing

To extend the analyzer:

1. **Add new analysis components**: Create new modules in `api_gap_analyzer/`
2. **Enhance system prompt**: Update `system_prompt.py` with new analysis guidelines
3. **Add new report sections**: Extend `report_generator.py`
4. **Improve context formatting**: Update `context_formatter.py`

## License

Part of the Woob project. See main repository for license details.

## Support

For issues or questions:
1. Check troubleshooting section above
2. Review AWS setup requirements
3. Verify Swagger spec format
4. Check module structure
