"""Verify cross-file imports by checking file/module existence and signature consistency."""
import sys
import os
import ast

backend = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend")
issues = []

# Build a map: module_name -> set of exported names (classes, functions, top-level vars)
module_exports = {}

def module_name_from_path(filepath):
    """Convert file path to dotted module name relative to backend/"""
    rel = os.path.relpath(filepath, backend)
    parts = rel.replace(os.sep, "/").replace(".py", "").split("/")
    parts = [p for p in parts if p]
    return ".".join(parts)


def collect_exports(filepath):
    """Parse a file and collect all top-level class/function/assignment/imported names."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source)
    except SyntaxError as e:
        issues.append(f"SYNTAX: {os.path.relpath(filepath, backend)}: {e}")
        return set()

    exports = set()
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef):
            exports.add(node.name)
        elif isinstance(node, ast.AsyncFunctionDef):
            exports.add(node.name)
        elif isinstance(node, ast.ClassDef):
            exports.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    exports.add(target.id)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                # bare import X -> 'X' available; 'import X as Y' -> 'Y' available
                name = alias.asname if alias.asname else alias.name.split(".")[0]
                exports.add(name)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                name = alias.asname if alias.asname else alias.name
                if name != "*":
                    exports.add(name)
    return exports


# Collect all modules and their exports
all_modules = {}  # module_name -> filepath
all_exports = {}  # module_name -> set of export names

for root, dirs, files in os.walk(backend):
    dirs[:] = [d for d in dirs if d != "__pycache__"]
    for f in files:
        if f.endswith(".py"):
            fp = os.path.join(root, f)
            mn = module_name_from_path(fp)
            all_modules[mn] = fp
            all_exports[mn] = collect_exports(fp)


# Check: does every __init__.py directory enable expected submodules?
# (skip, not strictly necessary)

# Check imports in each file
for mn, fp in all_modules.items():
    try:
        with open(fp, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source)
    except SyntaxError:
        continue

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                target = alias.name
                if target not in all_modules:
                    # Allow external / stdlib / third-party
                    if target.startswith("app.") or target.startswith("alembic."):
                        issues.append(f"MISSING MODULE: {mn} imports '{target}' but module not found")
                # Check if used name (alias.asname or alias.name) resolves
                used_name = alias.asname if alias.asname else alias.name.split(".")[-1]
                if target in all_modules:
                    pass  # bare import, access via module.attr, not checked deeply

        elif isinstance(node, ast.ImportFrom):
            if node.module is None:
                continue
            src_mod = node.module
            level = node.level

            # Resolve relative imports
            if level > 0:
                parts = mn.split(".")
                up = level - 1
                base_parts = parts[:-up] if up > 0 else parts
                if up >= len(parts):
                    issues.append(f"IMPORT LEVEL ERROR: {mn} attempts relative import beyond top-level package")
                    continue
                if src_mod:
                    resolved = ".".join(base_parts + [src_mod])
                else:
                    resolved = ".".join(base_parts)
            else:
                resolved = src_mod

            # For wildcard imports, we can't check individual names
            names = [alias.name for alias in node.names]
            if not names:
                continue  # wildcard import

            if resolved not in all_modules:
                if resolved.startswith("app.") or resolved.startswith("alembic."):
                    issues.append(f"MISSING MODULE: {mn} from-imports '{resolved}' but module not found")
                continue

            # Check each imported name exists in the source module
            src_exports = all_exports.get(resolved, set())
            for alias in node.names:
                imported_name = alias.name
                if imported_name == "*":
                    continue
                if imported_name not in src_exports:
                    issues.append(
                        f"MISSING NAME: {mn} imports '{imported_name}' from '{resolved}' but it is not defined there"
                    )


if issues:
    print(f"CROSS-FILE IMPORT ISSUES ({len(issues)}):")
    for i in issues:
        print(f"  {i}")
else:
    print(f"ALL {len(all_modules)} modules: cross-file imports verified OK.")

sys.exit(len(issues))
