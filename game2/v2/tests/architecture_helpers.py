"""Shared AST import resolution for V2 architecture regression tests."""
from __future__ import annotations

import ast
from pathlib import Path


def _module_name(path: Path, package_root: Path) -> str:
    relative = path.relative_to(package_root)
    parts = relative.with_suffix("").parts
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(("game2", "v2", *parts))


def imports_from_tree(tree: ast.AST, module_name: str,
                     package_name: str | None = None) -> set[str]:
    """Return absolute module paths for import statements in *tree*.

    Relative imports are resolved from the importing module's package, just as
    Python does. Imports using ``from package import name`` include both the
    package and the named child so boundary checks catch either form.
    """
    modules = set()
    package = package_name or module_name.rsplit(".", 1)[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level == 0:
            if node.module:
                modules.add(node.module)
                modules.update(f"{node.module}.{alias.name}" for alias in node.names
                               if alias.name != "*")
            continue

        package_parts = package.split(".")
        ascend = node.level - 1
        if ascend > len(package_parts):
            # Python would reject this import at runtime. Keep it visible to
            # architecture checks instead of silently treating it as local.
            modules.add("<invalid-relative-import>")
            continue
        base = ".".join(package_parts[:len(package_parts) - ascend])
        if node.module:
            modules.add(".".join(part for part in (base, node.module) if part))
        else:
            modules.add(base)
            modules.update(f"{base}.{alias.name}" for alias in node.names
                           if alias.name != "*")
    return modules


def absolute_imports(path: Path, package_root: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    module_name = _module_name(path, package_root)
    package_name = module_name if path.name == "__init__.py" else None
    return imports_from_tree(tree, module_name, package_name)
