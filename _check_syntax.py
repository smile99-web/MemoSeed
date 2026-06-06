"""Check all .py files under backend/ for syntax errors."""
import sys
import os
import py_compile
import traceback

backend = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend")
errors = []
total = 0

for root, dirs, files in os.walk(backend):
    dirs[:] = [d for d in dirs if d != "__pycache__"]
    for f in files:
        if f.endswith(".py"):
            total += 1
            path = os.path.join(root, f)
            try:
                py_compile.compile(path, doraise=True)
            except py_compile.PyCompileError as e:
                errors.append(f"SYNTAX ERROR: {path}\n  {e}")

if errors:
    print(f"SYNTAX ERRORS found in {len(errors)} file(s):")
    for e in errors:
        print(e)
else:
    print(f"ALL {total} files PASS syntax check.")

sys.exit(len(errors))
