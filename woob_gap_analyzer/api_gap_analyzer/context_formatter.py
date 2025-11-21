"""Format analysis context for LLM consumption."""

import json
from typing import Any, Dict


class ContextFormatter:
    """Format Woob explorer and Swagger data for LLM analysis."""

    @staticmethod
    def format_woob_analysis(explorer_result: Dict[str, Any]) -> str:
        """Format Woob explorer results into readable context.

        Args:
            explorer_result: Result from ModuleExplorer.explore_module()

        Returns:
            Formatted Woob analysis as string
        """
        module = explorer_result["module"]
        extracted_fields = explorer_result["extracted_fields"]
        field_mapping = explorer_result["field_mapping"]
        parent_analysis = explorer_result["parent_analysis"]

        output = f"""# Woob Implementation Analysis

## Module: {module}

### Overview
- Total fields extracted: {len(extracted_fields)}
- Parent classes analyzed: {len(parent_analysis)}

### Extracted Fields

The following fields are being extracted from the API responses:

"""

        # Group fields by source
        main_fields = [
            (name, info) for name, info in field_mapping.items() if info.get("source") == "main"
        ]
        parent_fields = [
            (name, info) for name, info in field_mapping.items() if info.get("source") == "parent"
        ]

        if main_fields:
            output += "#### From Main Module (cragr_stet):\n"
            for field_name, info in sorted(main_fields):
                field_type = info.get("type", "unknown")
                output += f"- `{field_name}` ({field_type})\n"
                if info.get("body"):
                    body = info.get("body", "").strip()
                    if body and body != "N/A":
                        output += f"  - Extraction: {body[:100]}\n"
                if info.get("path"):
                    output += f"  - Path: {info.get('path')}\n"
                output += f"  - Source: {info.get('file', 'unknown')}\n"
            output += "\n"

        if parent_fields:
            output += "#### From Parent Classes:\n"
            for field_name, info in sorted(parent_fields):
                field_type = info.get("type", "unknown")
                parent = info.get("parent", "unknown")
                output += f"- `{field_name}` ({field_type}) from {parent}\n"
                # Show extraction method if available
                if info.get("body"):
                    body = info.get("body", "").strip()
                    if body and body != "N/A":
                        output += f"  - Extraction: {body[:100]}\n"
                if info.get("path"):
                    output += f"  - Path: {info.get('path')}\n"
            output += "\n"

        # Add browser implementation details
        if explorer_result["main_analysis"].get("browser_classes"):
            output += "### Browser Implementation (Endpoint Definitions)\n\n"
            browser_file = explorer_result["main_analysis"].get("browser_file", "browser.py")
            output += f"File: {browser_file}\n\n"

            browser_classes = explorer_result["main_analysis"].get("browser_classes", [])
            if browser_classes:
                output += "#### Classes:\n"
                for cls in browser_classes:
                    output += f"- `{cls['name']}` (bases: {', '.join(cls['bases'])})\n"
                output += "\n"

            browser_methods = explorer_result["main_analysis"].get("browser_methods", [])
            if browser_methods:
                output += "#### Methods:\n"
                for method in browser_methods[:10]:  # Show first 10 methods
                    output += f"- `{method['name']}`\n"
                if len(browser_methods) > 10:
                    output += f"- ... and {len(browser_methods) - 10} more methods\n"
                output += "\n"

        # Add URL endpoint mappings from parent browser classes
        if explorer_result.get("parent_analysis"):
            endpoints_found = False
            for parent_key, parent_data in explorer_result["parent_analysis"].items():
                if parent_data.get("browser_analysis"):
                    browser_analysis = parent_data["browser_analysis"]
                    url_endpoints = browser_analysis.get("url_endpoints", [])
                    if url_endpoints and not endpoints_found:
                        output += "### API Endpoint Implementations (from Parent Browser)\n\n"
                        endpoints_found = True

                    if url_endpoints:
                        output += f"#### From {parent_key}:\n"
                        for endpoint in url_endpoints:
                            output += f"- `{endpoint['name']}` → `{endpoint['pattern']}` (Page: {endpoint['page_class']})\n"
                        output += "\n"

        # Add parent class details
        if parent_analysis:
            output += "### Parent Classes\n\n"
            for parent_key, parent_data in sorted(parent_analysis.items()):
                output += f"#### {parent_key}\n"
                output += f"- Pages File: {parent_data['file']}\n"
                analysis = parent_data["analysis"]
                output += f"- Classes: {len(analysis['classes'])}\n"
                output += f"- obj_* methods/attributes: {len(analysis['obj_methods'])}\n"
                output += f"- Dict filters: {len(analysis['dict_filters'])}\n"

                # Add browser implementation details if available
                if parent_data.get("browser_analysis"):
                    browser_analysis = parent_data["browser_analysis"]
                    output += f"- Browser File: {parent_data['browser_file']}\n"
                    output += f"- Browser Classes: {len(browser_analysis['classes'])}\n"
                    output += f"- Browser Methods: {len(browser_analysis['obj_methods'])}\n"

                    # List URL endpoint definitions
                    if browser_analysis.get("dict_filters"):
                        output += "- Endpoint URLs:\n"
                        for url_filter in browser_analysis["dict_filters"][:5]:
                            output += f"  - {url_filter['context'][:80]}\n"
                        if len(browser_analysis["dict_filters"]) > 5:
                            output += (
                                f"  - ... and {len(browser_analysis['dict_filters']) - 5} more\n"
                            )

                output += "\n"

        return output

    @staticmethod
    def format_swagger_spec(swagger_content: str, max_lines: int = 100) -> str:
        """Format Swagger spec for LLM (truncated for token efficiency).

        Args:
            swagger_content: Raw Swagger JSON content
            max_lines: Maximum lines to include

        Returns:
            Formatted Swagger spec
        """
        try:
            # Parse JSON to validate and pretty-print
            spec = json.loads(swagger_content)

            # Extract key information
            output = f"""# Bank API Specification (Swagger/OpenAPI)

## Metadata
- Title: {spec.get("info", {}).get("title", "Unknown")}
- Version: {spec.get("info", {}).get("version", "Unknown")}
- Description: {spec.get("info", {}).get("description", "N/A")[:200]}...

## Endpoints

"""

            # List all endpoints
            paths = spec.get("paths", {})
            for path, path_item in sorted(paths.items()):
                for method, operation in path_item.items():
                    if method.startswith("x-"):
                        continue
                    if method not in {"get", "post", "put", "delete", "patch"}:
                        continue

                    summary = operation.get("summary", "")
                    output += f"### {method.upper()} {path}\n"
                    if summary:
                        output += f"- Summary: {summary}\n"
                    output += f"- Operation ID: {operation.get('operationId', 'N/A')}\n"

                    # Response schemas
                    responses = operation.get("responses", {})
                    if responses:
                        output += "- Response codes: "
                        output += ", ".join(sorted(responses.keys()))
                        output += "\n"

                    output += "\n"

            # Add schemas section
            output += "## Response Schemas\n\n"
            schemas = spec.get("components", {}).get("schemas", {})
            for schema_name, schema_def in sorted(schemas.items()):
                properties = schema_def.get("properties", {})
                if properties:
                    output += f"### {schema_name}\n"
                    output += "Properties:\n"
                    for prop_name, prop_def in sorted(properties.items()):
                        prop_type = prop_def.get("type", "unknown")
                        required = (
                            "required"
                            if prop_name in schema_def.get("required", [])
                            else "optional"
                        )
                        output += f"- `{prop_name}` ({prop_type}) - {required}\n"
                    output += "\n"

            return output

        except json.JSONDecodeError as e:
            return f"Error parsing Swagger spec: {e}\n\nRaw content (first 1000 chars):\n{swagger_content[:1000]}"

    @staticmethod
    def format_comparison_context(swagger_content: str, woob_analysis: Dict[str, Any]) -> str:
        """Format complete context for LLM analysis.

        Args:
            swagger_content: Raw Swagger JSON
            woob_analysis: Woob explorer results

        Returns:
            Complete formatted context
        """
        context = "# Gap Analysis Context\n\n"
        context += ContextFormatter.format_swagger_spec(swagger_content)
        context += "\n---\n\n"
        context += ContextFormatter.format_woob_analysis(woob_analysis)

        return context
