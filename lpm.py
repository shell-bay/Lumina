"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  LUMINA PACKAGE MANAGER — lpm.py                                             ║
║  Version : 0.5.0                                                             ║
║                                                                              ║
║  The "Cargo for English" — install, list, and manage .lum modules.           ║
║                                                                              ║
║  CLI Usage:                                                                  ║
║    python lpm.py install math       # Download math.lum to ./lib/           ║
║    python lpm.py install physics    # Download physics.lum                  ║
║    python lpm.py list               # List installed modules                ║
║    python lpm.py search strings     # Search the registry                   ║
║    python lpm.py info math          # Show module description               ║
║    python lpm.py remove math        # Uninstall a module                    ║
║                                                                              ║
║  UI Integration:                                                             ║
║    from lpm import LuminaPackageManager                                      ║
║    lpm = LuminaPackageManager()                                              ║
║    ok, msg = lpm.install("physics")   # call from a UI button               ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations
import os
import sys
import json
import shutil
import hashlib
import argparse
import textwrap
from pathlib import Path
from typing import Optional, Dict, List, Tuple
from datetime import datetime

try:
    import urllib.request as _url
    URLLIB_OK = True
except ImportError:
    URLLIB_OK = False

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — SIMULATED PACKAGE REGISTRY
# In production, this would be a JSON file hosted at a URL.
# For Phase 5, the registry is built in — packages are shipped as strings.
# ══════════════════════════════════════════════════════════════════════════════

BUILTIN_REGISTRY: Dict[str, Dict] = {
    "math": {
        "version":     "1.0.0",
        "description": "Mathematical operations: sqrt, abs, pow, ceil, floor, round, log, sin, cos",
        "author":      "Lumina Core Team",
        "builtin":     True,   # backed by core.ll — no .lum file needed
        "source":      None,
    },
    "time": {
        "version":     "1.0.0",
        "description": "Time operations: get current time, measure elapsed time",
        "author":      "Lumina Core Team",
        "builtin":     True,
        "source":      None,
    },
    "random": {
        "version":     "1.0.0",
        "description": "Random number generation (seeded, normalised 0.0-1.0)",
        "author":      "Lumina Core Team",
        "builtin":     True,
        "source":      None,
    },
    "physics": {
        "version":     "0.1.0",
        "description": "Physics simulation helpers: velocity, acceleration, gravity",
        "author":      "Community",
        "builtin":     False,
        "source":      "__bundled__",   # content provided below
    },
    "strings": {
        "version":     "0.2.0",
        "description": "String utilities: length, reverse, concat patterns",
        "author":      "Community",
        "builtin":     False,
        "source":      "__bundled__",
    },
    "list": {
        "version":     "0.1.0",
        "description": "Simple list/array operations (Phase 6 preview)",
        "author":      "Lumina Core Team",
        "builtin":     False,
        "source":      "__bundled__",
    },
}

# Bundled .lum source for community modules
BUNDLED_SOURCES: Dict[str, str] = {
    "physics": """\
# ═══════════════════════════════════════════════════════
# Lumina Physics Library — physics.lum
# Usage:  use the physics library
# ═══════════════════════════════════════════════════════

# Constants
create a decimal called gravity with value 9.81
create a decimal called pi with value 3.14159

# velocity = distance / time
# In your program: calculate velocity as distance divided by time
""",
    "strings": """\
# ═══════════════════════════════════════════════════════
# Lumina Strings Library — strings.lum
# Provides: common text manipulation patterns
# ═══════════════════════════════════════════════════════

# String utility constants
create a number called max_length with value 1024
create a number called empty_length with value 0
""",
    "list": """\
# ═══════════════════════════════════════════════════════
# Lumina List Library — list.lum  (Phase 6 preview)
# Note: Full generic lists require Phase 6 heap arrays.
# ═══════════════════════════════════════════════════════

create a number called list_size with value 0
create a number called list_capacity with value 16
""",
}


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — COLOUR OUTPUT
# ══════════════════════════════════════════════════════════════════════════════

class C:
    _on   = sys.stdout.isatty()
    OK    = "\033[92m" if _on else ""
    WARN  = "\033[93m" if _on else ""
    ERR   = "\033[91m" if _on else ""
    BOLD  = "\033[1m"  if _on else ""
    DIM   = "\033[2m"  if _on else ""
    CYAN  = "\033[96m" if _on else ""
    RESET = "\033[0m"  if _on else ""

def _ok(msg):   print(f"  {C.OK}✔{C.RESET}  {msg}")
def _err(msg):  print(f"  {C.ERR}✖{C.RESET}  {msg}")
def _warn(msg): print(f"  {C.WARN}⚠{C.RESET}  {msg}")
def _info(msg): print(f"  {C.CYAN}ℹ{C.RESET}  {msg}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — PACKAGE MANIFEST  (tracks what's installed)
# ══════════════════════════════════════════════════════════════════════════════

class PackageManifest:
    """
    Reads/writes lumina_packages.json — tracks installed module metadata.
    """

    def __init__(self, lib_dir: Path):
        self._lib_dir  = lib_dir
        self._manifest_path = lib_dir / "lumina_packages.json"
        self._data: Dict[str, Dict] = self._load()

    def _load(self) -> Dict[str, Dict]:
        if self._manifest_path.exists():
            try:
                return json.loads(self._manifest_path.read_text())
            except Exception:
                return {}
        return {}

    def save(self):
        self._lib_dir.mkdir(parents=True, exist_ok=True)
        self._manifest_path.write_text(json.dumps(self._data, indent=2))

    def add(self, name: str, meta: Dict):
        self._data[name] = {**meta, "installed_at": datetime.utcnow().isoformat()}
        self.save()

    def remove(self, name: str):
        self._data.pop(name, None)
        self.save()

    def is_installed(self, name: str) -> bool:
        return name in self._data

    def all_installed(self) -> Dict[str, Dict]:
        return dict(self._data)

    def get(self, name: str) -> Optional[Dict]:
        return self._data.get(name)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — LUMINA PACKAGE MANAGER CLASS
# ══════════════════════════════════════════════════════════════════════════════

class LuminaPackageManager:
    """
    Main LPM class. All public methods return (success: bool, message: str)
    so they can be called from a UI button or the CLI.

    Example UI integration:
        lpm = LuminaPackageManager()
        ok, msg = lpm.install("physics")
        if ok:
            show_success_toast(msg)
        else:
            show_error_toast(msg)
    """

    def __init__(self,
                 lib_dir:      Optional[str] = None,
                 registry_url: Optional[str] = None):
        """
        lib_dir      : where .lum module files are stored (default: ./lib)
        registry_url : URL of remote registry JSON (None = use bundled registry)
        """
        self._lib_dir      = Path(lib_dir or "lib")
        self._registry_url = registry_url
        self._manifest     = PackageManifest(self._lib_dir)
        self._registry     = dict(BUILTIN_REGISTRY)

        # Try to load remote registry if URL given
        if registry_url and URLLIB_OK:
            self._fetch_remote_registry(registry_url)

    # ── Registry ──────────────────────────────────────────────────────────────

    def _fetch_remote_registry(self, url: str):
        try:
            with _url.urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read().decode())
                self._registry.update(data)
        except Exception:
            pass  # fall back to built-in registry silently

    def _lookup(self, name: str) -> Optional[Dict]:
        """Resolve aliases and look up in registry."""
        # Normalise
        n = name.lower().strip()
        aliases = {
            "mathematics": "math", "maths": "math",
            "clock":       "time",
            "rand":        "random",
            "string":      "strings", "text": "strings",
            "array":       "list",
        }
        n = aliases.get(n, n)
        return self._registry.get(n), n  # type: ignore

    # ── Install ────────────────────────────────────────────────────────────────

    def install(self, name: str) -> Tuple[bool, str]:
        """
        Install a module.
        - Built-in (core.ll) modules: just update the manifest.
        - Community .lum modules: write the source to lib/<name>.lum
        Returns (True, success_message) or (False, error_message).
        """
        entry, canonical = self._lookup(name)
        if entry is None:
            return False, (
                f"Module '{name}' not found in registry.\n"
                f"   Available: {', '.join(self._registry.keys())}\n"
                f"   Or create your own: lib/{name}.lum"
            )

        if self._manifest.is_installed(canonical):
            installed_ver = self._manifest.get(canonical or "").get("version", "?")
            return True, f"Module '{canonical}' v{installed_ver} is already installed."

        self._lib_dir.mkdir(parents=True, exist_ok=True)

        if entry.get("builtin"):
            # Built-in module — just register it (backed by core.ll)
            self._manifest.add(canonical, {
                "name":    canonical,
                "version": entry["version"],
                "builtin": True,
                "path":    "<builtin/core.ll>",
            })
            return True, (
                f"✔  Module '{canonical}' v{entry['version']} activated.\n"
                f"   (Built-in — powered by pre-compiled core.ll)\n"
                f"   Use it: write  'use the {canonical} library'  in your .lum file."
            )

        # Community module — write .lum source
        source_key = entry.get("source", "")
        if source_key == "__bundled__":
            source = BUNDLED_SOURCES.get(canonical)
        elif source_key and URLLIB_OK:
            # Try to fetch from a real URL
            try:
                with _url.urlopen(source_key, timeout=10) as r:
                    source = r.read().decode()
            except Exception as e:
                return False, f"Failed to download module '{canonical}': {e}"
        else:
            source = None

        if source is None:
            return False, f"No source available for module '{canonical}'."

        dest = self._lib_dir / f"{canonical}.lum"
        dest.write_text(source)

        self._manifest.add(canonical, {
            "name":    canonical,
            "version": entry["version"],
            "builtin": False,
            "path":    str(dest),
        })
        return True, (
            f"✔  Module '{canonical}' v{entry['version']} installed → {dest}\n"
            f"   Use it: write  'use the {canonical} library'  in your .lum file."
        )

    # ── Remove ─────────────────────────────────────────────────────────────────

    def remove(self, name: str) -> Tuple[bool, str]:
        _, canonical = self._lookup(name)
        if not self._manifest.is_installed(canonical):
            return False, f"Module '{canonical}' is not installed."
        meta = self._manifest.get(canonical) or {}
        path = meta.get("path", "")
        if path and path != "<builtin/core.ll>":
            p = Path(path)
            if p.exists():
                p.unlink()
        self._manifest.remove(canonical)
        return True, f"✔  Module '{canonical}' removed."

    # ── List ────────────────────────────────────────────────────────────────────

    def list_installed(self) -> List[Dict]:
        return [
            {"name": k, **v}
            for k, v in self._manifest.all_installed().items()
        ]

    def list_available(self) -> List[Dict]:
        return [
            {"name": k, **v}
            for k, v in self._registry.items()
        ]

    # ── Search ──────────────────────────────────────────────────────────────────

    def search(self, query: str) -> List[Dict]:
        q = query.lower()
        return [
            {"name": k, **v}
            for k, v in self._registry.items()
            if q in k.lower() or q in v.get("description", "").lower()
        ]

    # ── Info ────────────────────────────────────────────────────────────────────

    def info(self, name: str) -> Tuple[bool, Dict]:
        entry, canonical = self._lookup(name)
        if entry is None:
            return False, {}
        installed_meta = self._manifest.get(canonical) or {}
        return True, {**entry, "canonical_name": canonical, **installed_meta}


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — CLI
# ══════════════════════════════════════════════════════════════════════════════

def _print_banner():
    print(f"""
{C.BOLD}╔══════════════════════════════════════════════════════════════╗
║  🌟  LUMINA PACKAGE MANAGER (LPM) v0.5.0                     ║
║  The "Cargo for English" — install modules with one command  ║
╚══════════════════════════════════════════════════════════════╝{C.RESET}
""")


def main():
    parser = argparse.ArgumentParser(
        prog="lpm",
        description="Lumina Package Manager — install English modules",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
        Examples:
          python lpm.py install math         # install the math module
          python lpm.py install physics      # install physics module
          python lpm.py list                 # show installed modules
          python lpm.py search time          # search registry
          python lpm.py info math            # show module details
          python lpm.py remove physics       # uninstall a module
        """)
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # install
    p_install = sub.add_parser("install", help="Install a module")
    p_install.add_argument("name", help="Module name (e.g. math, physics)")
    p_install.add_argument("--lib-dir", default="lib", help="Library directory (default: ./lib)")

    # remove
    p_remove = sub.add_parser("remove", help="Remove an installed module")
    p_remove.add_argument("name")
    p_remove.add_argument("--lib-dir", default="lib")

    # list
    p_list = sub.add_parser("list", help="List installed modules")
    p_list.add_argument("--lib-dir", default="lib")
    p_list.add_argument("--available", action="store_true", help="Show all available modules")

    # search
    p_search = sub.add_parser("search", help="Search the module registry")
    p_search.add_argument("query", help="Search term")

    # info
    p_info = sub.add_parser("info", help="Show module details")
    p_info.add_argument("name")

    args = parser.parse_args()
    _print_banner()
    lpm  = LuminaPackageManager(lib_dir=getattr(args, "lib_dir", "lib"))

    if args.command == "install":
        ok, msg = lpm.install(args.name)
        if ok:  _ok(msg)
        else:   _err(msg)
        sys.exit(0 if ok else 1)

    elif args.command == "remove":
        ok, msg = lpm.remove(args.name)
        if ok:  _ok(msg)
        else:   _err(msg)
        sys.exit(0 if ok else 1)

    elif args.command == "list":
        if args.available:
            pkgs = lpm.list_available()
            print(f"  {C.BOLD}Available modules ({len(pkgs)}):{C.RESET}")
        else:
            pkgs = lpm.list_installed()
            print(f"  {C.BOLD}Installed modules ({len(pkgs)}):{C.RESET}")
        if not pkgs:
            _info("None. Run: python lpm.py install math")
        for p in pkgs:
            status = f"{C.OK}[builtin]{C.RESET}" if p.get("builtin") else f"{C.CYAN}[.lum]{C.RESET}"
            print(f"    {status} {C.BOLD}{p['name']}{C.RESET}  v{p.get('version','?')}"
                  f"  — {p.get('description','')[:60]}")

    elif args.command == "search":
        results = lpm.search(args.query)
        if not results:
            _info(f"No modules found matching '{args.query}'.")
        else:
            print(f"  {C.BOLD}Search results for '{args.query}' ({len(results)}):{C.RESET}")
            for p in results:
                print(f"    {C.BOLD}{p['name']}{C.RESET}  v{p.get('version','?')}  — {p.get('description','')}")

    elif args.command == "info":
        ok, meta = lpm.info(args.name)
        if not ok:
            _err(f"Module '{args.name}' not found.")
            sys.exit(1)
        print(f"  {C.BOLD}Module: {meta.get('canonical_name', args.name)}{C.RESET}")
        for key in ("version", "description", "author", "builtin", "path", "installed_at"):
            val = meta.get(key)
            if val is not None:
                print(f"    {key:15s}: {val}")


if __name__ == "__main__":
    main()
