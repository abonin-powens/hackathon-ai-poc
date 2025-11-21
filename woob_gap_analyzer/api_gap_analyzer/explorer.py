"""Orchestrator for exploring Woob modules and understanding implementations."""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from api_gap_analyzer.code_analyzer import CodeAnalyzer

logger = logging.getLogger(__name__)


class ModuleExplorer:
    """Explore a Woob module to understand its implementation."""

    def __init__(self, woob_root: Optional[str] = None):
        """Initialize the explorer.

        Args:
            woob_root: Root path of Woob codebase (default: ../woob relative to this file)
        """
        if woob_root is None:
            # Default to ../woob relative to the hackathon-ai-poc directory
            current_file = Path(__file__).resolve()
            hackathon_root = current_file.parent.parent.parent
            woob_root = str(hackathon_root.parent / "woob")
        self.woob_root = Path(woob_root)
        self.code_analyzer = CodeAnalyzer(woob_root)
        self.analysis_cache = {}

    def explore_module(self, module_name: str) -> Dict[str, Any]:
        """Explore a Woob module to understand its implementation.

        Args:
            module_name: Module name (e.g., 'cragr_stet')

        Returns:
            Dictionary with complete analysis
        """
        if module_name in self.analysis_cache:
            logger.debug(f"Using cached analysis for {module_name}")
            return self.analysis_cache[module_name]

        logger.info(f"Starting exploration of module: {module_name}")

        module_path = f"modules/{module_name}"

        # Step 1: Analyze the main pages.py file
        logger.info("Step 1: Analyzing main implementation file")
        pages_path = f"{module_path}/pages.py"
        main_analysis = self.code_analyzer.analyze_extraction_patterns(pages_path)

        # Step 1b: Analyze browser.py for endpoint implementations
        logger.info("Step 1b: Analyzing browser implementation file")
        browser_path = f"{module_path}/browser.py"
        browser_analysis = self.code_analyzer.analyze_extraction_patterns(browser_path)
        # Merge browser analysis with main analysis
        main_analysis["browser_file"] = browser_path
        main_analysis["browser_classes"] = browser_analysis.get("classes", [])
        main_analysis["browser_methods"] = browser_analysis.get("obj_methods", [])

        # Step 2: Trace parent classes
        logger.info("Step 2: Tracing parent classes")
        parent_classes = self._trace_parent_classes(pages_path, main_analysis)

        # Step 3: Analyze parent implementations
        logger.info("Step 3: Analyzing parent implementations")
        parent_analysis = self._analyze_parents(parent_classes)

        # Step 4: Build field mapping
        logger.info("Step 4: Building field mapping")
        field_mapping = self._build_field_mapping(main_analysis, parent_analysis)

        # Step 5: Compile results
        result = {
            "module": module_name,
            "main_file": pages_path,
            "main_analysis": main_analysis,
            "parent_classes": parent_classes,
            "parent_analysis": parent_analysis,
            "field_mapping": field_mapping,
            "extracted_fields": self._extract_all_fields(field_mapping),
        }

        self.analysis_cache[module_name] = result
        logger.info(f"Exploration complete for {module_name}")

        return result

    def _trace_parent_classes(
        self, file_path: str, analysis: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Trace parent classes for all classes in a file.

        Args:
            file_path: Path to Python file
            analysis: Analysis result from code_analyzer

        Returns:
            List of parent class information
        """
        parent_classes = []

        for cls in analysis["classes"]:
            for base in cls["bases"]:
                # Find the import for this base
                for imp in analysis["imports"]:
                    if imp["alias"] == base:
                        parent_classes.append(
                            {
                                "child_class": cls["name"],
                                "parent_class": imp["name"],
                                "parent_module": imp["module"],
                                "import_type": imp["type"],
                            }
                        )
                        break

        logger.debug(f"Found {len(parent_classes)} parent class relationships")
        return parent_classes

    def _analyze_parents(self, parent_classes: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Analyze parent class implementations recursively.

        Args:
            parent_classes: List of parent class information

        Returns:
            Dictionary with parent analysis
        """
        parent_analysis = {}
        visited = set()

        def analyze_parent_recursive(parent_info: Dict[str, Any], depth: int = 0) -> None:
            """Recursively analyze parent classes."""
            module_name = parent_info["parent_module"]
            parent_class = parent_info["parent_class"]
            key = f"{module_name}.{parent_class}"

            if key in visited:
                return

            visited.add(key)

            # Try to find the parent class file (check both pages.py and browser.py)
            parent_file = self._resolve_parent_file(module_name, parent_class)

            if parent_file:
                logger.debug(f"{'  ' * depth}Analyzing parent: {parent_class} in {parent_file}")
                analysis = self.code_analyzer.analyze_extraction_patterns(parent_file)

                # Also analyze browser.py if we found pages.py
                browser_analysis = None
                if parent_file.endswith("pages.py"):
                    browser_file = parent_file.replace("pages.py", "browser.py")
                    try:
                        browser_analysis = self.code_analyzer.analyze_extraction_patterns(
                            browser_file
                        )
                        logger.debug(f"{'  ' * depth}Also analyzing browser: {browser_file}")
                    except (FileNotFoundError, ValueError):
                        pass

                parent_analysis[key] = {
                    "file": parent_file,
                    "analysis": analysis,
                    "browser_file": browser_file if browser_analysis else None,
                    "browser_analysis": browser_analysis,
                    "depth": depth,
                }

                # Recursively analyze this parent's parents
                grandparent_classes = self._trace_parent_classes(parent_file, analysis)
                for grandparent in grandparent_classes:
                    analyze_parent_recursive(grandparent, depth + 1)

        # Start recursive analysis for each parent
        for parent_info in parent_classes:
            analyze_parent_recursive(parent_info)

        return parent_analysis

    def _resolve_parent_file(self, module_name: str, class_name: str) -> Optional[str]:
        """Resolve the file path for a parent class.

        Args:
            module_name: Module name (e.g., 'woob_modules.stet.pages')
            class_name: Class name

        Returns:
            File path or None if not found
        """
        # Handle woob_modules prefix - convert to modules/
        if module_name.startswith("woob_modules."):
            module_name = module_name.replace("woob_modules.", "")

        # Convert dot notation to path
        parts = module_name.split(".")
        possible_paths = [
            f"modules/{'/'.join(parts)}.py",
            f"modules/{'/'.join(parts[:-1])}/pages.py",
        ]

        for path in possible_paths:
            full_path = self.woob_root / path
            if full_path.exists() and full_path.is_file():
                return path

        return None

    def _build_field_mapping(
        self, main_analysis: Dict[str, Any], parent_analysis: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Build a mapping of extracted fields from main and parent classes.

        Args:
            main_analysis: Analysis of main file
            parent_analysis: Analysis of parent files

        Returns:
            Dictionary mapping field names to their extraction details
        """
        field_mapping = {}

        # Process obj_* methods from main file (highest priority)
        for method in main_analysis["obj_methods"]:
            field_name = method["field"]
            field_mapping[field_name] = {
                "source": "main",
                "method": method["name"],
                "line": method["line"],
                "body": method["body"],
                "dict_filters": self._extract_dict_filters_from_body(method["body"]),
                "file": "main",
            }

        # Process Dict filters from main file
        for filt in main_analysis["dict_filters"]:
            if filt["path"] not in field_mapping:
                field_mapping[filt["path"]] = {
                    "source": "dict_filter",
                    "path": filt["path"],
                    "line": filt["line"],
                    "context": filt["context"],
                    "file": "main",
                }

        # Process parent classes (lower priority - don't override main)
        for parent_key, parent_data in parent_analysis.items():
            analysis = parent_data["analysis"]

            # Process obj_* methods from parent
            for method in analysis["obj_methods"]:
                field_name = method["field"]
                if field_name not in field_mapping:
                    field_mapping[field_name] = {
                        "source": "parent",
                        "parent": parent_key,
                        "method": method["name"],
                        "line": method["line"],
                        "body": method["body"],
                        "dict_filters": self._extract_dict_filters_from_body(method["body"]),
                        "file": parent_data["file"],
                    }

            # Process Dict filters from parent
            for filt in analysis["dict_filters"]:
                if filt["path"] not in field_mapping:
                    field_mapping[filt["path"]] = {
                        "source": "parent",
                        "parent": parent_key,
                        "path": filt["path"],
                        "line": filt["line"],
                        "context": filt["context"],
                        "file": parent_data["file"],
                    }

        return field_mapping

    def _extract_dict_filters_from_body(self, body: str) -> List[str]:
        """Extract Dict filter paths from method body.

        Args:
            body: Method body as string

        Returns:
            List of Dict filter paths
        """
        import re

        filters = []
        matches = re.finditer(r'Dict\s*\(\s*["\']([^"\']+)["\']', body)
        for match in matches:
            filters.append(match.group(1))

        return filters

    def _extract_all_fields(self, field_mapping: Dict[str, Any]) -> List[str]:
        """Extract all unique field names from field mapping.

        Args:
            field_mapping: Field mapping dictionary

        Returns:
            List of unique field names
        """
        return sorted(list(field_mapping.keys()))

    def get_extracted_fields_summary(self, module_name: str) -> Dict[str, Any]:
        """Get a summary of extracted fields for a module.

        Args:
            module_name: Module name

        Returns:
            Summary dictionary
        """
        analysis = self.explore_module(module_name)

        return {
            "module": module_name,
            "total_fields": len(analysis["extracted_fields"]),
            "fields": analysis["extracted_fields"],
            "obj_methods": [m["name"] for m in analysis["main_analysis"]["obj_methods"]],
            "dict_filters": [f["path"] for f in analysis["main_analysis"]["dict_filters"]],
            "parent_classes": analysis["parent_classes"],
        }
