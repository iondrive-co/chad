#!/usr/bin/env python3
"""Cross-file symbol reference index for the Chad codebase.

Indexes Python and TypeScript/TSX symbols (functions, classes, imports) and
shows all files that define or reference a given symbol. Replaces slow
Agent/Explore calls for dependency mapping.

Usage:
    python scripts/symbol_index.py SessionManager        # find all references
    python scripts/symbol_index.py --define TaskExecutor  # find definitions only
    python scripts/symbol_index.py --callers verify       # find files that import/call
    python scripts/symbol_index.py --impact src/chad/util/providers.py  # show impact of changing a file
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
UI_DIR = ROOT / "ui" / "src"
CLIENT_DIR = ROOT / "client" / "src"
TEST_DIR = ROOT / "tests"


@dataclass
class SymbolRef:
    file: str  # Relative to ROOT
    line: int
    kind: str  # "def", "class", "import", "use"
    context: str  # The line of code


@dataclass
class SymbolIndex:
    definitions: dict[str, list[SymbolRef]] = field(
        default_factory=lambda: defaultdict(list))
    references: dict[str, list[SymbolRef]] = field(
        default_factory=lambda: defaultdict(list))
    file_exports: dict[str, set[str]] = field(
        default_factory=lambda: defaultdict(set))

    def all_symbols(self) -> set[str]:
        return set(self.definitions.keys()) | set(self.references.keys())


def _index_python_file(path: Path, rel: str, index: SymbolIndex) -> None:
    """Index a Python file using AST parsing."""
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, ValueError):
        return

    lines = source.splitlines()

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
            name = node.name
            ctx = lines[node.lineno - 1].strip() if node.lineno <= len(lines) else ""
            ref = SymbolRef(file=rel, line=node.lineno, kind="def", context=ctx)
            index.definitions[name].append(ref)
            index.file_exports[rel].add(name)

        elif isinstance(node, ast.ClassDef):
            name = node.name
            ctx = lines[node.lineno - 1].strip() if node.lineno <= len(lines) else ""
            ref = SymbolRef(file=rel, line=node.lineno, kind="class", context=ctx)
            index.definitions[name].append(ref)
            index.file_exports[rel].add(name)

        elif isinstance(node, ast.ImportFrom):
            if node.names:
                for alias in node.names:
                    name = alias.name
                    if name == "*":
                        continue
                    lineno = node.lineno
                    ctx = lines[lineno - 1].strip() if lineno <= len(lines) else ""
                    ref = SymbolRef(file=rel, line=lineno, kind="import", context=ctx)
                    index.references[name].append(ref)

        elif isinstance(node, ast.Import):
            for alias in node.names:
                # For "import foo.bar", index "bar" as the short name
                name = alias.asname or alias.name.split(".")[-1]
                lineno = node.lineno
                ctx = lines[lineno - 1].strip() if lineno <= len(lines) else ""
                ref = SymbolRef(file=rel, line=lineno, kind="import", context=ctx)
                index.references[name].append(ref)


def _index_ts_file(path: Path, rel: str, index: SymbolIndex) -> None:
    """Index a TypeScript/TSX file using regex (no full TS parser needed)."""
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return

    lines = source.splitlines()

    for i, line in enumerate(lines, 1):
        stripped = line.strip()

        # Function/const definitions
        for m in re.finditer(
            r'(?:export\s+)?(?:function|const|let|var)\s+(\w+)', stripped
        ):
            name = m.group(1)
            # Skip common noise words
            if name in ("if", "for", "while", "return", "true", "false", "null"):
                continue
            index.definitions[name].append(
                SymbolRef(file=rel, line=i, kind="def", context=stripped))
            index.file_exports[rel].add(name)

        # Interface/type/class definitions
        for m in re.finditer(
            r'(?:export\s+)?(?:interface|type|class|enum)\s+(\w+)', stripped
        ):
            name = m.group(1)
            index.definitions[name].append(
                SymbolRef(file=rel, line=i, kind="class", context=stripped))
            index.file_exports[rel].add(name)

        # Imports
        for m in re.finditer(
            r'import\s+\{([^}]+)\}\s+from', stripped
        ):
            for name in m.group(1).split(","):
                name = name.strip().split(" as ")[0].strip()
                if name:
                    index.references[name].append(
                        SymbolRef(file=rel, line=i, kind="import",
                                  context=stripped))

        # Default imports
        for m in re.finditer(
            r'import\s+(\w+)\s+from', stripped
        ):
            name = m.group(1)
            if name not in ("type", "from"):
                index.references[name].append(
                    SymbolRef(file=rel, line=i, kind="import",
                              context=stripped))


def build_index() -> SymbolIndex:
    """Build a symbol index for the entire codebase."""
    index = SymbolIndex()

    # Index Python files
    for base_dir in [SRC_DIR, TEST_DIR]:
        if not base_dir.exists():
            continue
        for path in base_dir.rglob("*.py"):
            if "__pycache__" in str(path):
                continue
            rel = str(path.relative_to(ROOT))
            _index_python_file(path, rel, index)

    # Index TypeScript files
    for base_dir in [UI_DIR, CLIENT_DIR]:
        if not base_dir.exists():
            continue
        for ext in ("*.ts", "*.tsx"):
            for path in base_dir.rglob(ext):
                if "node_modules" in str(path):
                    continue
                rel = str(path.relative_to(ROOT))
                _index_ts_file(path, rel, index)

    return index


def find_symbol(index: SymbolIndex, name: str,
                mode: str = "all") -> list[SymbolRef]:
    """Find references to a symbol.

    Args:
        name: Symbol name (case-sensitive)
        mode: "all", "define", or "callers"
    """
    results = []
    if mode in ("all", "define"):
        results.extend(index.definitions.get(name, []))
    if mode in ("all", "callers"):
        results.extend(index.references.get(name, []))
    return results


def find_impact(index: SymbolIndex, file_path: str) -> dict[str, list[SymbolRef]]:
    """Find all files that would be affected by changing a given file.

    Returns a dict of symbol_name → list of files that reference it.
    """
    # Normalize path
    rel = str(Path(file_path).relative_to(ROOT)) if Path(file_path).is_absolute() else file_path
    rel = rel.replace("\\", "/")

    exported = index.file_exports.get(rel, set())
    impact: dict[str, list[SymbolRef]] = {}

    for symbol in exported:
        refs = index.references.get(symbol, [])
        # Exclude self-references
        external = [r for r in refs if r.file != rel]
        if external:
            impact[symbol] = external

    return impact


def _format_ref(ref: SymbolRef) -> str:
    return f"  {ref.file}:{ref.line}  [{ref.kind}]  {ref.context}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("symbol", nargs="?", help="Symbol name to search for")
    parser.add_argument("--define", action="store_true",
                        help="Show only definitions")
    parser.add_argument("--callers", action="store_true",
                        help="Show only files that import/reference the symbol")
    parser.add_argument("--impact", metavar="FILE",
                        help="Show all files affected by changes to FILE")
    parser.add_argument("--fuzzy", action="store_true",
                        help="Case-insensitive substring match")
    args = parser.parse_args()

    if not args.symbol and not args.impact:
        parser.error("Provide a symbol name or --impact FILE")

    index = build_index()

    if args.impact:
        impact = find_impact(index, args.impact)
        if not impact:
            print(f"No external references found for exports of {args.impact}")
            sys.exit(0)

        total_files = set()
        for symbol, refs in sorted(impact.items()):
            print(f"\n{symbol}:")
            for ref in refs:
                print(_format_ref(ref))
                total_files.add(ref.file)

        print(f"\n--- {len(total_files)} files affected, "
              f"{len(impact)} exported symbols referenced ---")
        sys.exit(0)

    symbol = args.symbol
    mode = "define" if args.define else ("callers" if args.callers else "all")

    if args.fuzzy:
        # Find all matching symbol names
        pattern = symbol.lower()
        matches = [s for s in index.all_symbols() if pattern in s.lower()]
        if not matches:
            print(f"No symbols matching '{symbol}'")
            sys.exit(1)
        for name in sorted(matches):
            refs = find_symbol(index, name, mode)
            if refs:
                print(f"\n{name} ({len(refs)} references):")
                for ref in refs:
                    print(_format_ref(ref))
    else:
        refs = find_symbol(index, symbol, mode)
        if not refs:
            print(f"No references found for '{symbol}'")
            # Suggest fuzzy matches
            pattern = symbol.lower()
            close = [s for s in index.all_symbols()
                     if pattern in s.lower()][:10]
            if close:
                print(f"Did you mean: {', '.join(sorted(close))}")
            sys.exit(1)

        print(f"\n{symbol} ({len(refs)} references):\n")
        for ref in refs:
            print(_format_ref(ref))


if __name__ == "__main__":
    main()
