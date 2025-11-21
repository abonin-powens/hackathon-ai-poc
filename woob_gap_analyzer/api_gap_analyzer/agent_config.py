"""Strand Agent configuration for Woob codebase exploration."""

import logging
from pathlib import Path
from typing import Optional

from strands import Agent
from strands.tools import tool

logger = logging.getLogger(__name__)

WOOB_EXPLORER_PROMPT = """You are an expert Woob codebase analyzer. Your mission is to explore Woob modules and understand how they extract data from bank APIs.

## Your Capabilities
You have access to tools to:
1. List directory contents to understand module structure
2. Read Python files to analyze code
3. Search for specific patterns (imports, class definitions, method calls)
4. Get file information and metadata

## Your Analysis Process
When analyzing a Woob module:

1. **Explore Structure**: List the module directory to understand what files exist
2. **Find Entry Points**: Identify main implementation files (usually pages.py, module.py)
3. **Trace Inheritance**: Find parent classes and trace the full inheritance chain
4. **Extract Fields**: Identify all obj_* methods and Dict() filters that extract data
5. **Map Transformations**: For each field, understand how it's transformed
6. **Report Findings**: Provide structured analysis of all extracted fields

## Key Patterns to Look For
- `obj_*` methods and attributes (field extraction)
- `Dict("path/to/field")` filters (JSON path extraction)
- `class ClassName(ParentClass):` (inheritance)
- `from X import Y` (dependencies)
- `@method` decorators (special extraction methods)

## Output Format
Provide your findings as a JSON structure with:
- module_path: The module being analyzed
- files: List of files in the module
- classes: Class definitions and their bases
- extracted_fields: All fields being extracted with their locations
- parent_classes: Inheritance chain
- transformations: Any data transformations applied
"""


class WoobCodebaseExplorer:
    """Strand Agent for exploring Woob codebase and understanding implementations."""

    def __init__(self, woob_root: Optional[str] = None):
        """Initialize the explorer with Strands Agent.

        Args:
            woob_root: Root path of Woob codebase (default: ../woob relative to this file)
        """
        if woob_root is None:
            # Default to ../woob relative to the hackathon-ai-poc directory
            current_file = Path(__file__).resolve()
            hackathon_root = current_file.parent.parent.parent.parent
            woob_root = hackathon_root.parent / "woob"
        self.woob_root = Path(woob_root)
        self.explored_files = {}  # Cache for explored files

        # Initialize Strands Agent with Woob exploration tools
        self.agent = Agent(
            system_prompt=WOOB_EXPLORER_PROMPT,
            tools=[
                self.list_directory,
                self.read_file_tool,
                self.search_in_file,
                self.get_file_info,
            ],
        )
        logger.info("Strands Agent initialized for Woob exploration")

    def _read_file_internal(self, path: str) -> str:
        """Internal method to read a file from the Woob codebase.

        Args:
            path: Relative path to file (e.g., 'modules/cragr_stet/pages.py')

        Returns:
            File contents as string

        Raises:
            ValueError: If path is outside woob root or file doesn't exist
        """
        file_path = (self.woob_root / path).resolve()

        # Security check: ensure path is within woob_root
        try:
            file_path.relative_to(self.woob_root.resolve())
        except ValueError:
            raise ValueError(f"Path {path} is outside Woob codebase")

        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        if not file_path.is_file():
            raise ValueError(f"Path is not a file: {path}")

        # Cache the file
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
            self.explored_files[path] = content
            logger.debug(f"Read file: {path} ({len(content)} bytes)")
            return content

    @tool
    def read_file_tool(self, path: str) -> str:
        """Read a file from the Woob codebase.

        This tool allows the agent to read Python files to analyze code structure,
        extract field definitions, and understand data transformations.

        Args:
            path: Relative path to file (e.g., 'modules/cragr_stet/pages.py')

        Returns:
            File contents as string
        """
        return self._read_file_internal(path)

    @tool
    def list_directory(self, path: str = ".") -> list[str]:
        """List contents of a directory in the Woob codebase.

        Args:
            path: Relative path to directory (default: root)

        Returns:
            List of file and directory names

        Raises:
            ValueError: If path is outside woob root or not a directory
        """
        dir_path = (self.woob_root / path).resolve()

        # Security check
        try:
            dir_path.relative_to(self.woob_root.resolve())
        except ValueError:
            raise ValueError(f"Path {path} is outside Woob codebase")

        if not dir_path.exists():
            raise FileNotFoundError(f"Directory not found: {path}")

        if not dir_path.is_dir():
            raise ValueError(f"Path is not a directory: {path}")

        items = []
        for item in sorted(dir_path.iterdir()):
            if item.name.startswith("."):
                continue
            if item.is_dir():
                items.append(f"{item.name}/")
            else:
                items.append(item.name)

        logger.debug(f"Listed directory: {path} ({len(items)} items)")
        return items

    @tool
    def search_in_file(self, path: str, pattern: str) -> list[dict]:
        """Search for a pattern in a file.

        This tool allows the agent to find specific patterns in code files,
        such as class definitions, imports, or method calls.

        Args:
            path: Relative path to file
            pattern: String pattern to search for (case-insensitive)

        Returns:
            List of matches with line numbers and context

        Raises:
            ValueError: If path is invalid
        """
        content = self._read_file_internal(path)
        matches = []

        for line_num, line in enumerate(content.split("\n"), 1):
            if pattern.lower() in line.lower():
                matches.append(
                    {
                        "line": line_num,
                        "content": line.strip(),
                    }
                )

        logger.debug(f"Found {len(matches)} matches for '{pattern}' in {path}")
        return matches

    @tool
    def get_file_info(self, path: str) -> dict:
        """Get information about a file.

        Args:
            path: Relative path to file

        Returns:
            Dictionary with file info (size, type, etc.)

        Raises:
            ValueError: If path is invalid
        """
        file_path = (self.woob_root / path).resolve()

        # Security check
        try:
            file_path.relative_to(self.woob_root.resolve())
        except ValueError:
            raise ValueError(f"Path {path} is outside Woob codebase")

        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        stat = file_path.stat()
        return {
            "path": path,
            "size": stat.st_size,
            "is_file": file_path.is_file(),
            "is_dir": file_path.is_dir(),
            "suffix": file_path.suffix,
        }

    def explore_module(self, module_path: str) -> dict:
        """Explore a Woob module autonomously using Strands Agent.

        The agent will:
        1. List the module directory structure
        2. Read and analyze main implementation files
        3. Trace inheritance chains to parent classes
        4. Extract all field definitions and transformations
        5. Provide structured analysis of data extraction patterns

        Args:
            module_path: Path to module (e.g., 'modules/cragr_stet')

        Returns:
            Dictionary with analysis results from the agent
        """
        logger.info(f"Starting autonomous exploration of {module_path}")

        prompt = f"""Analyze the Woob module at '{module_path}' and provide a comprehensive understanding of how it extracts data from bank APIs.

## Step-by-Step Analysis

1. **Explore Module Structure**
   - List all files in the module directory
   - Identify the main implementation files (pages.py, module.py, etc.)

2. **Analyze Main Implementation**
   - Read pages.py and identify all classes
   - Find all obj_* methods and attributes (these extract fields)
   - Find all Dict() filter calls (these specify JSON paths)
   - Note any @method decorators or special extraction patterns

3. **Trace Inheritance Chain**
   - For each class, identify its parent classes
   - Search for parent class imports (look for "from woob_modules" or "from woob.capabilities")
   - Read parent class files to understand inherited extraction methods
   - Continue tracing up the inheritance chain

4. **Extract Field Mapping**
   - For each obj_* method/attribute, identify:
     * The field name (what it's called in Woob)
     * The extraction method (Dict path, custom logic, etc.)
     * The source file and line number
     * Any transformations applied (CleanDecimal, Date, Format, etc.)

5. **Identify Transformations**
   - Look for filter chains like: Dict("path") | CleanDecimal()
   - Note any custom transformation logic
   - Identify conditional field extraction

## Output Format
Provide your findings as a structured JSON response with:
{{
  "module_path": "{module_path}",
  "files": ["list of files in module"],
  "classes": [
    {{
      "name": "ClassName",
      "bases": ["ParentClass"],
      "file": "path/to/file.py"
    }}
  ],
  "extracted_fields": [
    {{
      "field_name": "obj_id",
      "extraction_method": "Dict('accountId/other/cardNumber')",
      "transformations": [],
      "file": "modules/cragr_stet/pages.py",
      "line": 123,
      "description": "Extracts account ID from card number"
    }}
  ],
  "parent_classes": [
    {{
      "child": "AccountsPage",
      "parent": "_AccountsPage",
      "module": "woob_modules.stet.pages"
    }}
  ],
  "summary": "Overall analysis summary"
}}

Start by listing the module directory, then proceed with detailed analysis."""

        logger.info(f"Sending exploration prompt to Strands Agent for {module_path}")
        response = self.agent.run(prompt)
        logger.info("Autonomous exploration complete")

        # Parse agent response
        analysis_result = {
            "module": module_path,
            "agent_response": response,
            "explored_files": list(self.explored_files.keys()),
            "cached_file_count": len(self.explored_files),
        }

        logger.debug(f"Explored {len(self.explored_files)} files during analysis")
        return analysis_result

    def get_cached_files(self) -> dict:
        """Get all cached file contents.

        Returns:
            Dictionary mapping file paths to contents
        """
        return self.explored_files.copy()
