"""Code analysis module for understanding Woob implementations."""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class CodeAnalyzer:
    """Analyze Python code to extract structure and patterns."""

    def __init__(self, woob_root: Optional[str] = None):
        """Initialize the analyzer.

        Args:
            woob_root: Root path of Woob codebase (default: ../woob relative to this file)
        """
        if woob_root is None:
            # Default to ../woob relative to the hackathon-ai-poc directory
            current_file = Path(__file__).resolve()
            hackathon_root = current_file.parent.parent.parent
            import os
            home = os.path.expanduser("~")
            woob_root  / "dev" / "woob"
        self.woob_root = Path(woob_root)
        print(self.woob_root)
        self.imports_cache = {}
        self.classes_cache = {}

    def extract_imports(self, file_path: str) -> List[Dict[str, str]]:
        """Extract import statements from a Python file.

        Args:
            file_path: Path to Python file

        Returns:
            List of import dictionaries with 'type', 'module', 'name', 'alias'
        """
        if file_path in self.imports_cache:
            return self.imports_cache[file_path]

        full_path = self.woob_root / file_path
        if not full_path.exists():
            return []

        imports = []
        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Handle multi-line imports by normalizing them
        # Replace newlines inside parentheses with spaces
        normalized = re.sub(r"\(\s*\n\s*", "(", content)
        normalized = re.sub(r"\n\s*\)", ")", normalized)
        normalized = re.sub(r"\n\s*,", ",", normalized)

        # Match: from X import Y [as Z], W [as V], ... (with or without parentheses)
        from_imports = re.finditer(
            r"from\s+([\w.]+)\s+import\s+\((.*?)\)|from\s+([\w.]+)\s+import\s+([\w, ]+)",
            normalized,
            re.MULTILINE | re.DOTALL,
        )
        for match in from_imports:
            if match.group(1):  # Parenthesized import
                module = match.group(1)
                names_str = match.group(2)
            else:  # Regular import
                module = match.group(3)
                names_str = match.group(4)

            # Parse individual imports with optional aliases
            for item in names_str.split(","):
                item = item.strip()
                if " as " in item:
                    name, alias = item.split(" as ")
                    name = name.strip()
                    alias = alias.strip()
                else:
                    name = item
                    alias = item

                if name:
                    imports.append(
                        {
                            "type": "from",
                            "module": module,
                            "name": name,
                            "alias": alias,
                        }
                    )

        # Match: import X [as Y]
        direct_imports = re.finditer(r"^import\s+([\w.]+)(?:\s+as\s+(\w+))?", content, re.MULTILINE)
        for match in direct_imports:
            module = match.group(1)
            alias = match.group(2)

            imports.append(
                {
                    "type": "import",
                    "module": module,
                    "name": module.split(".")[-1],
                    "alias": alias or module.split(".")[-1],
                }
            )

        self.imports_cache[file_path] = imports
        logger.debug(f"Extracted {len(imports)} imports from {file_path}")
        return imports

    def extract_classes(self, file_path: str) -> List[Dict[str, Any]]:
        """Extract class definitions from a Python file.

        Args:
            file_path: Path to Python file

        Returns:
            List of class dictionaries with 'name', 'bases', 'line', 'methods'
        """
        if file_path in self.classes_cache:
            return self.classes_cache[file_path]

        full_path = self.woob_root / file_path
        if not full_path.exists():
            return []

        classes = []
        with open(full_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # Match: class ClassName(Base1, Base2):
        for line_num, line in enumerate(lines, 1):
            match = re.match(r"^class\s+(\w+)\s*\((.*?)\):", line)
            if match:
                class_name = match.group(1)
                bases_str = match.group(2)
                bases = [b.strip() for b in bases_str.split(",") if b.strip()]

                # Extract methods
                methods = self._extract_methods_for_class(lines, line_num)

                classes.append(
                    {
                        "name": class_name,
                        "bases": bases,
                        "line": line_num,
                        "methods": methods,
                    }
                )

        self.classes_cache[file_path] = classes
        logger.debug(f"Extracted {len(classes)} classes from {file_path}")
        return classes

    def _extract_methods_for_class(
        self, lines: List[str], class_start: int
    ) -> List[Dict[str, Any]]:
        """Extract methods from a class definition.

        Args:
            lines: All lines from the file
            class_start: Line number where class starts (1-indexed)

        Returns:
            List of method dictionaries
        """
        methods = []
        class_indent = len(lines[class_start - 1]) - len(lines[class_start - 1].lstrip())

        # Find the end of the class
        class_end = len(lines)
        for i in range(class_start, len(lines)):
            line = lines[i]
            if line.strip() and not line.startswith(" " * (class_indent + 1)):
                class_end = i
                break

        # Extract methods within the class
        for i in range(class_start, class_end):
            line = lines[i]
            match = re.match(r"^\s+def\s+(\w+)\s*\((.*?)\):", line)
            if match:
                method_name = match.group(1)
                params = match.group(2)

                methods.append(
                    {
                        "name": method_name,
                        "line": i + 1,
                        "params": params,
                    }
                )

        return methods

    def extract_dict_filters(self, file_path: str) -> List[Dict[str, Any]]:
        """Extract Dict() filter usage from a Python file.

        Dict filters are used in Woob to extract fields from JSON responses.

        Args:
            file_path: Path to Python file

        Returns:
            List of Dict filter usages with 'path', 'line', 'context'
        """
        full_path = self.woob_root / file_path
        if not full_path.exists():
            return []

        filters = []
        with open(full_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # Match: Dict("path/to/field")
        for line_num, line in enumerate(lines, 1):
            matches = re.finditer(r'Dict\s*\(\s*["\']([^"\']+)["\']', line)
            for match in matches:
                field_path = match.group(1)
                filters.append(
                    {
                        "path": field_path,
                        "line": line_num,
                        "context": line.strip(),
                    }
                )

        logger.debug(f"Found {len(filters)} Dict filters in {file_path}")
        return filters

    def extract_obj_methods(self, file_path: str) -> List[Dict[str, Any]]:
        """Extract obj_* methods and attributes from a Python file.

        obj_* methods and attributes define how fields are extracted and transformed.

        Args:
            file_path: Path to Python file

        Returns:
            List of obj_* method/attribute definitions
        """
        full_path = self.woob_root / file_path
        if not full_path.exists():
            return []

        methods = []
        with open(full_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        for line_num, line in enumerate(lines, 1):
            # Match: def obj_* methods
            match = re.match(r"^\s+def\s+(obj_\w+)\s*\((.*?)\):", line)
            if match:
                method_name = match.group(1)
                field_name = method_name[4:]  # Remove 'obj_' prefix

                # Extract the method body (next few lines)
                body_lines = []
                indent = len(line) - len(line.lstrip())
                for i in range(line_num, min(line_num + 20, len(lines))):
                    body_line = lines[i]
                    if body_line.strip() and not body_line.startswith(" " * (indent + 1)):
                        break
                    body_lines.append(body_line.rstrip())

                methods.append(
                    {
                        "name": method_name,
                        "field": field_name,
                        "line": line_num,
                        "body": "\n".join(body_lines),
                        "type": "method",
                    }
                )

            # Match: obj_* = ... (simple attribute assignment)
            attr_match = re.match(r"^\s+(obj_\w+)\s*=\s*(.+)$", line)
            if attr_match:
                attr_name = attr_match.group(1)
                field_name = attr_name[4:]  # Remove 'obj_' prefix
                attr_value = attr_match.group(2).strip()

                methods.append(
                    {
                        "name": attr_name,
                        "field": field_name,
                        "line": line_num,
                        "body": attr_value,
                        "type": "attribute",
                    }
                )

        logger.debug(f"Found {len(methods)} obj_* methods/attributes in {file_path}")
        return methods

    def trace_inheritance(
        self, file_path: str, class_name: str, visited: Optional[Set[str]] = None
    ) -> List[Dict[str, Any]]:
        """Trace the inheritance chain for a class.

        Args:
            file_path: Path to Python file
            class_name: Name of the class to trace
            visited: Set of already visited classes (to avoid cycles)

        Returns:
            List of classes in the inheritance chain
        """
        if visited is None:
            visited = set()

        if class_name in visited:
            return []

        visited.add(class_name)
        chain = []

        # Find the class in the file
        classes = self.extract_classes(file_path)
        for cls in classes:
            if cls["name"] == class_name:
                chain.append(
                    {
                        "name": class_name,
                        "file": file_path,
                        "bases": cls["bases"],
                        "line": cls["line"],
                    }
                )

                # Trace each base class
                for base in cls["bases"]:
                    # Try to find the base class in imports
                    imports = self.extract_imports(file_path)
                    for imp in imports:
                        if imp["alias"] == base:
                            # Resolve the module path
                            module_path = self._resolve_module_path(imp["module"])
                            if module_path:
                                base_chain = self.trace_inheritance(
                                    module_path, imp["name"], visited
                                )
                                chain.extend(base_chain)
                            break

                break

        return chain

    def _resolve_module_path(self, module_name: str) -> Optional[str]:
        """Resolve a module name to a file path.

        Args:
            module_name: Module name (e.g., 'woob.capabilities.bank')

        Returns:
            Relative file path or None if not found
        """
        # Handle relative imports
        if module_name.startswith("."):
            return None

        # Convert module name to path
        parts = module_name.split(".")
        path = self.woob_root / "/".join(parts)

        # Try as package
        if (path / "__init__.py").exists():
            return str(path / "__init__.py").replace(str(self.woob_root) + "/", "")

        # Try as module
        if (path.parent / f"{parts[-1]}.py").exists():
            return str(path.parent / f"{parts[-1]}.py").replace(str(self.woob_root) + "/", "")

        return None

    def extract_url_endpoints(self, file_path: str) -> List[Dict[str, Any]]:
        """Extract URL endpoint definitions from a browser file.

        URL endpoints are typically defined as: endpoint_name = URL(r"pattern", PageClass)

        Args:
            file_path: Path to Python file

        Returns:
            List of URL endpoint definitions with 'name', 'pattern', 'page_class', 'line'
        """
        full_path = self.woob_root / file_path
        if not full_path.exists():
            return []

        endpoints = []
        with open(full_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # Match: endpoint_name = URL(r"pattern", PageClass)
        for line_num, line in enumerate(lines, 1):
            match = re.search(r'(\w+)\s*=\s*URL\s*\(\s*r["\']([^"\']+)["\']', line)
            if match:
                endpoint_name = match.group(1)
                pattern = match.group(2)
                # Try to extract page class
                page_class_match = re.search(r",\s*(\w+)\s*\)", line)
                page_class = page_class_match.group(1) if page_class_match else "Unknown"

                endpoints.append(
                    {
                        "name": endpoint_name,
                        "pattern": pattern,
                        "page_class": page_class,
                        "line": line_num,
                        "context": line.strip(),
                    }
                )

        logger.debug(f"Found {len(endpoints)} URL endpoints in {file_path}")
        return endpoints

    def analyze_extraction_patterns(self, file_path: str) -> Dict[str, Any]:
        """Analyze all extraction patterns in a file.

        Args:
            file_path: Path to Python file

        Returns:
            Dictionary with all extraction patterns found
        """
        return {
            "file": file_path,
            "imports": self.extract_imports(file_path),
            "classes": self.extract_classes(file_path),
            "dict_filters": self.extract_dict_filters(file_path),
            "obj_methods": self.extract_obj_methods(file_path),
            "url_endpoints": self.extract_url_endpoints(file_path),
        }
