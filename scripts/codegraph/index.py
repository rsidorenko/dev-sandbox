#!/usr/bin/env python3
"""Repository code graph indexer for TelegramBotVPN.

Builds a tracked JSON code graph at .codegraph/graph.json containing files,
symbols, imports, exports, dependencies, and relationships. Designed for use
by Claude Code and developers to navigate the codebase efficiently.

Commands:
    build              Rebuild .codegraph/graph.json and manifest.json
    check              Verify graph is current (exit 1 if stale/missing)
    summary            Print compact project summary
    search "<query>"   Search by path, symbol, import, export, summary
    file "<path>"      Show metadata for one file
    related "<path>"   Show neighboring files/symbols (default depth 1)
    changed            Show files whose hashes differ from graph.json
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GRAPH_DIR = REPO_ROOT / ".codegraph"
GRAPH_FILE = GRAPH_DIR / "graph.json"
MANIFEST_FILE = GRAPH_DIR / "manifest.json"

SCHEMA_VERSION = "1.0"
MAX_FILE_SIZE = 200_000  # bytes — skip files larger than this

# Directories to always skip
SKIP_DIRS = {
    ".git", ".claude", ".codegraph",
    "node_modules", "vendor", "dist", "build", "out", "coverage",
    ".next", ".nuxt", "target", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".turbo", ".cache", "tmp", "temp", "logs",
    ".vscode", ".idea", "worktrees",
}

# File patterns to skip
SKIP_PATTERNS = {
    ".log", ".sqlite", ".db", ".pem", ".key", ".crt", ".p12",
    ".pyc", ".pyo", ".egg-info",
}

# Files to always skip (exact names)
SKIP_FILES = {
    ".env", ".env.local", ".env.production",
    "image.png",
}

# Prefixes that indicate secret files
SECRET_PREFIXES = (".env",)


def _should_skip(path: Path) -> bool:
    """Return True if this path should be excluded from indexing."""
    parts = path.parts
    for part in parts:
        if part in SKIP_DIRS:
            return True
    name = path.name
    if name in SKIP_FILES:
        return True
    if any(name.startswith(p) and p != "." for p in SECRET_PREFIXES):
        if name.startswith(".env"):
            return True
    ext = path.suffix.lower()
    if ext in SKIP_PATTERNS:
        return True
    try:
        if path.stat().st_size > MAX_FILE_SIZE:
            return True
    except OSError:
        return True
    return False


# ---------------------------------------------------------------------------
# File hashing
# ---------------------------------------------------------------------------

def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            content = f.read()
        content = content.replace(b"\r\n", b"\n")
        h.update(content)
    except OSError:
        return ""
    return h.hexdigest()[:16]


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

LANG_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".json": "json",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".toml": "toml",
    ".md": "markdown",
    ".html": "html",
    ".css": "css",
    ".scss": "scss",
    ".sql": "sql",
    ".sh": "shell",
    ".bash": "shell",
    ".dockerfile": "dockerfile",
    ".conf": "config",
    ".cfg": "config",
    ".ini": "config",
    ".txt": "text",
    ".csv": "csv",
}


def _detect_language(path: Path) -> str:
    if path.name == "Dockerfile":
        return "dockerfile"
    if path.name.endswith(".dockerignore"):
        return "dockerignore"
    if path.name.endswith(".gitignore"):
        return "gitignore"
    return LANG_MAP.get(path.suffix.lower(), "unknown")


# ---------------------------------------------------------------------------
# Python AST parser
# ---------------------------------------------------------------------------

def _parse_python(path: Path) -> dict[str, Any]:
    """Parse a Python file using the ast module."""
    result: dict[str, Any] = {
        "imports": [],
        "exports": [],
        "symbols": [],
        "depends_on": [],
    }
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return result

    lines = source.splitlines()

    # Collect module-level __all__ for exports
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    if isinstance(node.value, ast.List):
                        result["exports"] = sorted({
                            elt.value
                            for elt in node.value.elts
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                        })

    imported_modules = set()

    for node in ast.iter_child_nodes(tree):
        # Imports
        if isinstance(node, ast.Import):
            for alias in node.names:
                mod = alias.name.split(".")[0]
                imported_modules.add(mod)
                result["imports"].append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                mod = node.module.split(".")[0]
                imported_modules.add(mod)
                for alias in (node.names or []):
                    result["imports"].append(
                        f"{node.module}.{alias.name}" if alias.name != "*" else f"{node.module}.*"
                    )

        # Functions
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            decorators = _decorators(node)
            kind = "function"
            if any("route" in d.lower() or "app." in d.lower() for d in decorators):
                kind = "route"
            result["symbols"].append({
                "name": node.name,
                "kind": kind,
                "line_start": node.lineno,
                "line_end": node.end_lineno or node.lineno,
                "decorators": decorators,
                "async": isinstance(node, ast.AsyncFunctionDef),
            })

        # Classes
        elif isinstance(node, ast.ClassDef):
            decorators = _decorators(node)
            methods = []
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    method_decorators = _decorators(item)
                    mkind = "method"
                    if any(d in ("@staticmethod", "staticmethod") for d in method_decorators):
                        mkind = "staticmethod"
                    elif any(d in ("@classmethod", "classmethod") for d in method_decorators):
                        mkind = "classmethod"
                    methods.append({
                        "name": item.name,
                        "kind": mkind,
                        "line_start": item.lineno,
                        "line_end": item.end_lineno or item.lineno,
                        "async": isinstance(item, ast.AsyncFunctionDef),
                    })
                    result["symbols"].append({
                        "name": f"{node.name}.{item.name}",
                        "kind": "method",
                        "line_start": item.lineno,
                        "line_end": item.end_lineno or item.lineno,
                    })
            bases = []
            for base in node.bases:
                if isinstance(base, ast.Name):
                    bases.append(base.id)
                elif isinstance(base, ast.Attribute):
                    bases.append(ast.dump(base))
            result["symbols"].append({
                "name": node.name,
                "kind": "class",
                "line_start": node.lineno,
                "line_end": node.end_lineno or node.lineno,
                "decorators": decorators,
                "bases": bases,
                "methods": sorted(methods, key=lambda m: m["line_start"]),
            })

    # If no explicit __all__, treat top-level public names as exports
    if not result["exports"]:
        exports = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not node.name.startswith("_"):
                    exports.append(node.name)
            elif isinstance(node, ast.ClassDef):
                if not node.name.startswith("_"):
                    exports.append(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and not target.id.startswith("_"):
                        exports.append(target.id)
        result["exports"] = sorted(exports)

    result["imports"] = sorted(set(result["imports"]))
    result["depends_on"] = sorted(imported_modules)

    return result


def _decorators(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> list[str]:
    result = []
    for dec in node.decorator_list:
        if isinstance(dec, ast.Name):
            result.append(dec.id)
        elif isinstance(dec, ast.Attribute):
            result.append(f"{ast.dump(dec.value)}.{dec.attr}" if isinstance(dec.value, ast.Name) else dec.attr)
        elif isinstance(dec, ast.Call):
            if isinstance(dec.func, ast.Name):
                result.append(dec.func.id)
            elif isinstance(dec.func, ast.Attribute):
                result.append(dec.func.attr)
    return result


# ---------------------------------------------------------------------------
# JavaScript / TypeScript conservative parser
# ---------------------------------------------------------------------------

_JS_IMPORT_RE = re.compile(
    r"""(?:import\s+.*?from\s+['"]([^'"]+)['"]|"""
    r"""require\s*\(\s*['"]([^'"]+)['"]\s*\))""",
    re.MULTILINE,
)
_JS_EXPORT_RE = re.compile(
    r"""export\s+(?:default\s+)?(?:function|class|const|let|var|async\s+function)\s+(\w+)""",
    re.MULTILINE,
)
_JS_FUNCTION_RE = re.compile(
    r"""(?:export\s+(?:default\s+)?)?(?:async\s+)?function\s+(\w+)\s*\(""",
    re.MULTILINE,
)
_JS_CLASS_RE = re.compile(
    r"""(?:export\s+(?:default\s+)?)?class\s+(\w+)""",
    re.MULTILINE,
)
_JS_COMPONENT_RE = re.compile(
    r"""(?:export\s+(?:default\s+)?)?function\s+([A-Z]\w+)\s*[\(\<]""",
    re.MULTILINE,
)
_JS_ROUTE_RE = re.compile(
    r"""(?:app|router)\.(?:get|post|put|delete|patch|use|all)\s*\(\s*['"]([^'"]+)""",
    re.MULTILINE,
)


def _parse_js_ts(path: Path) -> dict[str, Any]:
    """Conservative JS/TS parsing with regex."""
    result: dict[str, Any] = {
        "imports": [],
        "exports": [],
        "symbols": [],
        "depends_on": [],
    }
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return result

    imports = set()
    for m in _JS_IMPORT_RE.finditer(source):
        imp = m.group(1) or m.group(2)
        if imp:
            imports.add(imp)
    result["imports"] = sorted(imports)

    exports = set()
    for m in _JS_EXPORT_RE.finditer(source):
        exports.add(m.group(1))
    result["exports"] = sorted(exports)

    symbols = []

    # React components
    for m in _JS_COMPONENT_RE.finditer(source):
        name = m.group(1)
        symbols.append({"name": name, "kind": "component"})

    # Functions
    for m in _JS_FUNCTION_RE.finditer(source):
        name = m.group(1)
        if not name[0].isupper():
            symbols.append({"name": name, "kind": "function"})

    # Classes
    for m in _JS_CLASS_RE.finditer(source):
        symbols.append({"name": m.group(1), "kind": "class"})

    # Routes
    for m in _JS_ROUTE_RE.finditer(source):
        symbols.append({"name": m.group(1), "kind": "route"})

    result["symbols"] = sorted(symbols, key=lambda s: s["name"])

    depends_on = set()
    for imp in imports:
        if not imp.startswith("."):
            depends_on.add(imp.split("/")[0])
    result["depends_on"] = sorted(depends_on)

    return result


# ---------------------------------------------------------------------------
# SQL parser (minimal)
# ---------------------------------------------------------------------------

_SQL_TABLE_RE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)", re.IGNORECASE)
_SQL_INDEX_RE = re.compile(r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)", re.IGNORECASE)
_SQL_ALTER_RE = re.compile(r"ALTER\s+TABLE\s+(\w+)", re.IGNORECASE)


def _parse_sql(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "imports": [],
        "exports": [],
        "symbols": [],
        "depends_on": [],
    }
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return result

    for m in _SQL_TABLE_RE.finditer(source):
        result["symbols"].append({"name": m.group(1), "kind": "table"})
    for m in _SQL_INDEX_RE.finditer(source):
        result["symbols"].append({"name": m.group(1), "kind": "index"})
    for m in _SQL_ALTER_RE.finditer(source):
        result["symbols"].append({"name": f"ALTER {m.group(1)}", "kind": "migration"})

    result["exports"] = sorted({s["name"] for s in result["symbols"]})
    return result


# ---------------------------------------------------------------------------
# Generic fallback parser
# ---------------------------------------------------------------------------

def _parse_generic(path: Path) -> dict[str, Any]:
    return {
        "imports": [],
        "exports": [],
        "symbols": [],
        "depends_on": [],
    }


# ---------------------------------------------------------------------------
# File summary generation
# ---------------------------------------------------------------------------

def _generate_summary(path: Path, language: str, parsed: dict[str, Any]) -> str:
    """Generate a compact one-line summary for a file."""
    parts = path.parts

    # Detect file role by path
    rel = str(path.relative_to(REPO_ROOT)).replace("\\", "/")

    if "test" in rel and language == "python":
        return f"Test: {path.name}"
    if "migration" in rel:
        return f"Migration: {path.name}"
    if language == "yaml" and ".github" in rel:
        return f"CI/CD workflow: {path.name}"
    if language == "yaml" and "docker-compose" in path.name:
        return f"Docker compose: {path.name}"
    if language == "toml":
        return f"Project config: {path.name}"
    if language == "dockerfile":
        return f"Docker image: {path.name}"

    symbols = parsed.get("symbols", [])
    exports = parsed.get("exports", [])
    imports = parsed.get("imports", [])

    sym_names = [s["name"] for s in symbols[:5]]
    exp_names = exports[:5]

    if language == "python":
        classes = [s["name"] for s in symbols if s["kind"] == "class"]
        funcs = [s["name"] for s in symbols if s["kind"] in ("function", "route")]
        if classes:
            return f"Python module: classes {', '.join(classes[:3])}"
        if funcs:
            return f"Python module: functions {', '.join(funcs[:4])}"
        return f"Python module ({path.name})"

    if language in ("javascript", "typescript"):
        components = [s["name"] for s in symbols if s["kind"] == "component"]
        if components:
            return f"React component: {', '.join(components)}"
        return f"{'TS' if language == 'typescript' else 'JS'} module ({path.name})"

    return f"{language} file: {path.name}"


# ---------------------------------------------------------------------------
# Test file matching
# ---------------------------------------------------------------------------

def _find_related_tests(source_rel: str, all_files: dict[str, Any]) -> list[str]:
    """Heuristic: find test files related to a source file."""
    stem = Path(source_rel).stem
    candidates = []
    for fpath, fdata in all_files.items():
        fname = Path(fpath).name
        if fname.startswith("test_") and stem in fname:
            candidates.append(fpath)
        elif fname.startswith("test_") and stem.replace("_", "") in fname.replace("_", ""):
            candidates.append(fpath)
    return sorted(set(candidates))[:10]


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def build_graph() -> dict[str, Any]:
    """Walk the repo and build the complete code graph."""
    files: dict[str, Any] = {}
    symbols_index: dict[str, list[dict]] = defaultdict(list)
    edges: list[dict[str, str]] = []

    # Collect all files first
    all_paths: list[Path] = []
    for root, dirs, filenames in os.walk(REPO_ROOT):
        # Prune skipped directories in-place
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".env")]
        root_path = Path(root)
        for fname in sorted(filenames):
            fpath = root_path / fname
            if _should_skip(fpath):
                continue
            all_paths.append(fpath)

    # Parse each file
    for fpath in sorted(all_paths):
        rel = str(fpath.relative_to(REPO_ROOT)).replace("\\", "/")
        language = _detect_language(fpath)

        if language == "python":
            parsed = _parse_python(fpath)
        elif language in ("javascript", "typescript"):
            parsed = _parse_js_ts(fpath)
        elif language == "sql":
            parsed = _parse_sql(fpath)
        else:
            parsed = _parse_generic(fpath)

        summary = _generate_summary(fpath, language, parsed)
        file_hash = _file_hash(fpath)
        file_size = fpath.stat().st_size if fpath.exists() else 0

        file_entry: dict[str, Any] = {
            "language": language,
            "hash": file_hash,
            "size": file_size,
            "summary": summary,
        }
        if parsed.get("imports"):
            file_entry["imports"] = parsed["imports"]
        if parsed.get("exports"):
            file_entry["exports"] = parsed["exports"]
        if parsed.get("symbols"):
            file_entry["symbols"] = parsed["symbols"]
        if parsed.get("depends_on"):
            file_entry["depends_on"] = parsed["depends_on"]

        files[rel] = file_entry

        # Index symbols
        for sym in parsed.get("symbols", []):
            sym_entry = {
                "file": rel,
                "kind": sym["kind"],
                "line_start": sym.get("line_start", 0),
                "line_end": sym.get("line_end", 0),
            }
            symbols_index[sym["name"]].append(sym_entry)

    # Build edges (import relationships)
    for rel, fdata in files.items():
        for imp in fdata.get("depends_on", []):
            # Find files that might be the import target
            for other_rel, other_data in files.items():
                if other_rel == rel:
                    continue
                other_lang = other_data.get("language", "")
                if other_lang != "python":
                    continue
                # Check if import matches a Python module path
                other_parts = Path(other_rel).parts
                if other_parts[-1].replace(".py", "") == imp:
                    edges.append({"from": rel, "to": other_rel, "kind": "imports"})

    # Add test relationship edges
    for rel, fdata in files.items():
        lang = fdata.get("language", "")
        if lang not in ("python",):
            continue
        related_tests = _find_related_tests(rel, files)
        if related_tests:
            fdata["related_tests"] = related_tests
            for test_rel in related_tests:
                edges.append({"from": test_rel, "to": rel, "kind": "tests"})

    # Detect languages present
    languages = sorted({f["language"] for f in files.values() if f["language"] != "unknown"})

    graph = {
        "schema_version": SCHEMA_VERSION,
        "project": {
            "name": "TelegramBotVPN",
            "root_marker": "CLAUDE.md",
            "languages": languages,
        },
        "files": dict(sorted(files.items())),
        "symbols": dict(sorted(symbols_index.items())),
        "edges": sorted(edges, key=lambda e: (e["from"], e["to"], e["kind"])),
    }

    return graph


# ---------------------------------------------------------------------------
# Manifest builder
# ---------------------------------------------------------------------------

def build_manifest(graph: dict[str, Any]) -> dict[str, Any]:
    """Build manifest.json metadata."""
    return {
        "schema_version": SCHEMA_VERSION,
        "project": graph["project"],
        "stats": {
            "files": len(graph["files"]),
            "symbols": len(graph["symbols"]),
            "edges": len(graph["edges"]),
        },
        "languages": graph["project"]["languages"],
    }


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

def cmd_build() -> None:
    """Build or rebuild the code graph."""
    GRAPH_DIR.mkdir(parents=True, exist_ok=True)

    print("Building code graph...")
    graph = build_graph()
    manifest = build_manifest(graph)

    with open(GRAPH_FILE, "w", encoding="utf-8") as f:
        json.dump(graph, f, indent=2, sort_keys=True, ensure_ascii=False)

    with open(MANIFEST_FILE, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, sort_keys=True, ensure_ascii=False)

    stats = manifest["stats"]
    print(f"  Files indexed:   {stats['files']}")
    print(f"  Symbols indexed: {stats['symbols']}")
    print(f"  Edges indexed:   {stats['edges']}")
    print(f"  Languages:       {', '.join(manifest['languages'])}")
    print(f"  Graph written:   {GRAPH_FILE.relative_to(REPO_ROOT)}")


def cmd_check() -> None:
    """Check if the graph is current. Exit 1 if stale or missing."""
    if not GRAPH_FILE.exists():
        print("FAIL: .codegraph/graph.json not found. Run: python scripts/codegraph/index.py build")
        sys.exit(1)

    try:
        with open(GRAPH_FILE, "r", encoding="utf-8") as f:
            graph = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"FAIL: Cannot read graph.json: {e}")
        sys.exit(1)

    changed = _detect_changed_files(graph)
    if changed:
        print(f"FAIL: {len(changed)} file(s) changed since last build:")
        for f in changed[:20]:
            print(f"  {f}")
        print("Run: python scripts/codegraph/index.py build")
        sys.exit(1)

    print("OK: code graph is current.")


def cmd_summary() -> None:
    """Print a compact project summary."""
    if not GRAPH_FILE.exists():
        print("Graph not found. Run: python scripts/codegraph/index.py build")
        sys.exit(1)

    with open(GRAPH_FILE, "r", encoding="utf-8") as f:
        graph = json.load(f)

    proj = graph.get("project", {})
    files = graph.get("files", {})
    symbols = graph.get("symbols", {})
    edges = graph.get("edges", [])

    print(f"Project: {proj.get('name', 'unknown')}")
    print(f"Languages: {', '.join(proj.get('languages', []))}")

    # File type breakdown
    lang_counts: dict[str, int] = defaultdict(int)
    for fdata in files.values():
        lang_counts[fdata.get("language", "unknown")] += 1
    for lang, count in sorted(lang_counts.items(), key=lambda x: -x[1]):
        print(f"  {lang}: {count} files")

    print(f"Total files: {len(files)}")
    print(f"Total symbols: {len(symbols)}")
    print(f"Total edges: {len(edges)}")

    # Symbol kinds
    kind_counts: dict[str, int] = defaultdict(int)
    for sym_list in symbols.values():
        for sym in sym_list:
            kind_counts[sym.get("kind", "unknown")] += 1
    if kind_counts:
        print("Symbol breakdown:")
        for kind, count in sorted(kind_counts.items(), key=lambda x: -x[1]):
            print(f"  {kind}: {count}")


def cmd_search(query: str) -> None:
    """Search by path, symbol, import, export, summary, or route."""
    if not GRAPH_FILE.exists():
        print("Graph not found. Run: python scripts/codegraph/index.py build")
        sys.exit(1)

    with open(GRAPH_FILE, "r", encoding="utf-8") as f:
        graph = json.load(f)

    q = query.lower()
    results: list[dict[str, Any]] = []

    for rel, fdata in graph.get("files", {}).items():
        score = 0
        reasons = []

        # Path match
        if q in rel.lower():
            score += 10
            reasons.append("path")

        # Summary match
        if q in fdata.get("summary", "").lower():
            score += 5
            reasons.append("summary")

        # Symbol match
        for sym in fdata.get("symbols", []):
            if q in sym["name"].lower():
                score += 8
                reasons.append(f"symbol:{sym['name']}")
            if q in sym.get("kind", ""):
                score += 3
                reasons.append(f"kind:{sym['kind']}")

        # Import match
        for imp in fdata.get("imports", []):
            if q in imp.lower():
                score += 4
                reasons.append(f"import:{imp}")

        # Export match
        for exp in fdata.get("exports", []):
            if q in exp.lower():
                score += 6
                reasons.append(f"export:{exp}")

        if score > 0:
            results.append({
                "file": rel,
                "score": score,
                "reasons": sorted(set(reasons))[:5],
                "language": fdata.get("language", ""),
                "summary": fdata.get("summary", ""),
            })

    results.sort(key=lambda r: -r["score"])

    if not results:
        print(f"No results for: {query}")
        return

    print(f"Search results for '{query}' ({len(results)} matches):")
    for r in results[:30]:
        reasons_str = ", ".join(r["reasons"])
        print(f"  {r['file']} [{r['language']}] ({reasons_str})")
        if r["summary"]:
            print(f"    {r['summary']}")


def cmd_file(filepath: str) -> None:
    """Show metadata for one file."""
    if not GRAPH_FILE.exists():
        print("Graph not found. Run: python scripts/codegraph/index.py build")
        sys.exit(1)

    with open(GRAPH_FILE, "r", encoding="utf-8") as f:
        graph = json.load(f)

    # Normalize path
    filepath = filepath.replace("\\", "/")
    if filepath.startswith("./"):
        filepath = filepath[2:]

    fdata = graph.get("files", {}).get(filepath)
    if not fdata:
        # Try matching end of path
        candidates = [k for k in graph["files"] if k.endswith(filepath)]
        if len(candidates) == 1:
            filepath = candidates[0]
            fdata = graph["files"][filepath]
        elif len(candidates) > 1:
            print(f"Ambiguous path. Matches: {', '.join(candidates)}")
            return
        else:
            print(f"File not found in graph: {filepath}")
            return

    print(f"File: {filepath}")
    print(f"  Language: {fdata.get('language', 'unknown')}")
    print(f"  Hash: {fdata.get('hash', 'unknown')}")
    print(f"  Size: {fdata.get('size', 0)} bytes")
    print(f"  Summary: {fdata.get('summary', 'N/A')}")

    if fdata.get("imports"):
        print(f"  Imports ({len(fdata['imports'])}):")
        for imp in fdata["imports"][:20]:
            print(f"    {imp}")
        if len(fdata["imports"]) > 20:
            print(f"    ... and {len(fdata['imports']) - 20} more")

    if fdata.get("exports"):
        print(f"  Exports ({len(fdata['exports'])}):")
        for exp in fdata["exports"][:20]:
            print(f"    {exp}")

    if fdata.get("symbols"):
        print(f"  Symbols ({len(fdata['symbols'])}):")
        for sym in fdata["symbols"][:30]:
            line_range = f"L{sym.get('line_start', '?')}-{sym.get('line_end', '?')}"
            print(f"    {sym['name']} ({sym['kind']}) {line_range}")

    if fdata.get("related_tests"):
        print(f"  Related tests:")
        for t in fdata["related_tests"]:
            print(f"    {t}")


def cmd_related(target: str, depth: int = 1) -> None:
    """Show neighboring files and symbols."""
    if not GRAPH_FILE.exists():
        print("Graph not found. Run: python scripts/codegraph/index.py build")
        sys.exit(1)

    with open(GRAPH_FILE, "r", encoding="utf-8") as f:
        graph = json.load(f)

    target = target.replace("\\", "/")
    if target.startswith("./"):
        target = target[2:]

    # Find the file
    if target not in graph.get("files", {}):
        candidates = [k for k in graph["files"] if k.endswith(target)]
        if len(candidates) == 1:
            target = candidates[0]
        elif not candidates:
            # Try as symbol name
            sym_entries = graph.get("symbols", {}).get(target, [])
            if sym_entries:
                print(f"Symbol '{target}' found in:")
                for entry in sym_entries:
                    print(f"  {entry['file']} ({entry['kind']}) L{entry['line_start']}-{entry['line_end']}")
                return
            print(f"Not found: {target}")
            return
        else:
            print(f"Ambiguous. Matches: {', '.join(candidates)}")
            return

    fdata = graph["files"][target]
    print(f"Related to: {target}")
    print(f"  Summary: {fdata.get('summary', 'N/A')}")

    # Direct dependencies from edges
    direct_related = set()
    for edge in graph.get("edges", []):
        if edge["from"] == target:
            direct_related.add((edge["to"], edge["kind"]))
        elif edge["to"] == target:
            direct_related.add((edge["from"], edge["kind"]))

    if direct_related:
        print(f"  Direct relations ({len(direct_related)}):")
        for rel_path, kind in sorted(direct_related):
            rel_summary = graph["files"].get(rel_path, {}).get("summary", "")
            print(f"    {rel_path} [{kind}] — {rel_summary}")

    # Imports-based relations
    imports = fdata.get("depends_on", [])
    if imports:
        print(f"  Depends on modules: {', '.join(imports[:20])}")

    # Related tests
    related_tests = fdata.get("related_tests", [])
    if related_tests:
        print(f"  Related tests ({len(related_tests)}):")
        for t in related_tests:
            print(f"    {t}")

    # Files that depend on this one
    dependents = set()
    for other_rel, other_data in graph["files"].items():
        if other_rel == target:
            continue
        for imp in other_data.get("imports", []):
            stem = Path(target).stem
            if stem and stem in imp:
                dependents.add(other_rel)

    if dependents:
        print(f"  Used by ({len(dependents)}):")
        for dep in sorted(dependents)[:20]:
            print(f"    {dep}")


def cmd_changed() -> None:
    """Show files whose hashes differ from graph.json."""
    if not GRAPH_FILE.exists():
        print("Graph not found. Run: python scripts/codegraph/index.py build")
        sys.exit(1)

    with open(GRAPH_FILE, "r", encoding="utf-8") as f:
        graph = json.load(f)

    changed = _detect_changed_files(graph)
    if not changed:
        print("No changes detected. Graph is current.")
        return

    print(f"Changed files ({len(changed)}):")
    for f in changed:
        print(f"  {f}")


def _detect_changed_files(graph: dict[str, Any]) -> list[str]:
    """Compare current file hashes against the graph."""
    changed = []
    files = graph.get("files", {})

    for rel, fdata in files.items():
        fpath = REPO_ROOT / rel
        if not fpath.exists():
            changed.append(f"{rel} (deleted)")
            continue
        current_hash = _file_hash(fpath)
        if current_hash != fdata.get("hash", ""):
            changed.append(rel)

    # Also detect new files (not in graph but exist)
    for root, dirs, filenames in os.walk(REPO_ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".env")]
        root_path = Path(root)
        for fname in sorted(filenames):
            fpath = root_path / fname
            if _should_skip(fpath):
                continue
            rel = str(fpath.relative_to(REPO_ROOT)).replace("\\", "/")
            if rel not in files:
                changed.append(f"{rel} (new)")

    return sorted(changed)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

USAGE = """\
Usage: python scripts/codegraph/index.py <command> [args]

Commands:
  build                    Build or rebuild the code graph
  check                    Verify graph is current (for CI)
  summary                  Print project summary
  search "<query>"         Search files and symbols
  file "<path>"            Show metadata for a file
  related "<path>" [--depth N]  Show related files/symbols
  changed                  Show files changed since last build
"""


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(USAGE)
        sys.exit(0)

    command = args[0]

    if command == "build":
        cmd_build()
    elif command == "check":
        cmd_check()
    elif command == "summary":
        cmd_summary()
    elif command == "search":
        if len(args) < 2:
            print("Usage: python scripts/codegraph/index.py search \"<query>\"")
            sys.exit(1)
        cmd_search(args[1])
    elif command == "file":
        if len(args) < 2:
            print("Usage: python scripts/codegraph/index.py file \"<path>\"")
            sys.exit(1)
        cmd_file(args[1])
    elif command == "related":
        if len(args) < 2:
            print('Usage: python scripts/codegraph/index.py related "<path>" [--depth N]')
            sys.exit(1)
        depth = 1
        for i, a in enumerate(args[2:], 2):
            if a == "--depth" and i + 1 < len(args):
                try:
                    depth = int(args[i + 1])
                except ValueError:
                    pass
        cmd_related(args[1], depth=depth)
    elif command == "changed":
        cmd_changed()
    else:
        print(f"Unknown command: {command}")
        print(USAGE)
        sys.exit(1)


if __name__ == "__main__":
    main()
