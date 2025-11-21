import ast
import importlib
import inspect
import sys
import re
import subprocess
# ---------------------------------------------------------
# Parse a Python module into AST
# ---------------------------------------------------------
def parse_module(module_path):
    with open(module_path, "r") as f:
        src = f.read()
    tree = ast.parse(src)
    return tree, src
# ---------------------------------------------------------
# Collect imports, top-level assignments, functions, classes
# ---------------------------------------------------------
def collect_top_level(tree):
    imports, assignments, functions, classes = [], [], [], []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.append(node)
        elif isinstance(node, ast.ClassDef):
            classes.append(node)
        elif isinstance(node, ast.FunctionDef):
            functions.append(node)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            assignments.append(node)
        else:
            # Include other nodes if needed
            assignments.append(node)
    return imports, assignments, functions, classes
# ---------------------------------------------------------
# Build alias -> real class mapping from imports
# ---------------------------------------------------------
def get_alias_mapping(tree):
    mapping = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            module = node.module
            for alias in node.names:
                name = alias.name
                asname = alias.asname or alias.name
                mapping[asname] = f"{module}.{name}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name
                asname = alias.asname or name
                mapping[asname] = name
    return mapping
# ---------------------------------------------------------
# Get imports needed for external class
# ---------------------------------------------------------
def get_external_imports(full_class_path):
    module_name, _ = full_class_path.rsplit(".", 1)
    try:
        module = importlib.import_module(module_name)
        file_path = inspect.getsourcefile(module)
    except Exception:
        return []
    if not file_path:
        return []
    try:
        with open(file_path, "r") as f:
            tree = ast.parse(f.read())
    except Exception:
        return []
    imports = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.append(ast.unparse(node))
    return imports
# ---------------------------------------------------------
# Get external class source and rename to alias
# ---------------------------------------------------------
def get_external_class_source(full_class_path, alias_name):
    module_name, class_name = full_class_path.rsplit(".", 1)
    module = importlib.import_module(module_name)
    cls = getattr(module, class_name)
    src = inspect.getsource(cls)
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            node.name = alias_name
    return ast.unparse(tree.body[0])
# ---------------------------------------------------------
# Flatten module into single standalone file
# ---------------------------------------------------------
def flatten_module(module_path):
    tree, _ = parse_module(module_path)
    imports, assignments, functions, classes = collect_top_level(tree)
    alias_mapping = get_alias_mapping(tree)
    lines = []
    # Determine which aliases will be flattened
    flattened_aliases = set()
    for cls in classes:
        for base in cls.bases:
            if isinstance(base, ast.Name) and base.id in alias_mapping:
                flattened_aliases.add(base.id)
    # Remove imports corresponding to flattened classes
    new_imports = []
    for node in imports:
        if isinstance(node, ast.ImportFrom):
            new_names = [alias for alias in node.names if (alias.asname or alias.name) not in flattened_aliases]
            if new_names:
                node.names = new_names
                new_imports.append(node)
        elif isinstance(node, ast.Import):
            new_names = [alias for alias in node.names if (alias.asname or alias.name) not in flattened_aliases]
            if new_names:
                node.names = new_names
                new_imports.append(node)
    imports = new_imports
    # Write original imports
    written_imports = set()
    if imports:
        for node in imports:
            code = ast.unparse(node)
            lines.append(code + "\n")
            written_imports.add(code)
        lines.append("\n")
    # Write top-level variables/constants
    if assignments:
        for node in assignments:
            lines.append(ast.unparse(node) + "\n\n")
    # Write top-level functions
    if functions:
        for node in functions:
            lines.append(ast.unparse(node) + "\n\n")
    # Write external base classes
    written_aliases = set()
    for cls in classes:
        for base in cls.bases:
            if isinstance(base, ast.Name):
                alias_name = base.id
                if alias_name in alias_mapping and alias_name not in written_aliases:
                    full_class_path = alias_mapping[alias_name]
                    # Write necessary imports for the external class
                    ext_imports = get_external_imports(full_class_path)
                    for imp in ext_imports:
                        if imp not in written_imports:
                            lines.append(imp + "\n")
                            written_imports.add(imp)
                    if ext_imports:
                        lines.append("\n")
                    # Write the class itself under alias name
                    try:
                        src = get_external_class_source(full_class_path, alias_name)
                        lines.append(src + "\n\n")
                        written_aliases.add(alias_name)
                    except Exception as e:
                        print(f"Warning: could not fetch source for {full_class_path}: {e}")
    # Write original classes
    for cls in classes:
        lines.append(ast.unparse(cls) + "\n\n")

    return lines
    # Save to output file

    #with open(output_file, "w") as f:
    #    f.writelines(lines)
    #print(f":coche_trait_plein: Flattened module written to: {output_file}")
    ## ---------------------------------------------------------
    ## Run Ruff formatter and check
    ## ---------------------------------------------------------
    #subprocess.run(["ruff", "format", output_file])
    #subprocess.run(["ruff", "check", "--fix", output_file, "--ignore", "F811,F821,E402"])
# ---------------------------------------------------------
# CLI
# ---------------------------------------------------------
if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python flatten_module_final.py <module_path.py>")
        sys.exit(1)
    module_path = sys.argv[1]
    output_file = re.sub(r"\.py$", "_flat.py", sys.argv[1])
    flatten_module(module_path, output_file)
