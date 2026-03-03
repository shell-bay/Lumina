"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  LUMINA FREEZE COMPILER — freeze_compiler.py                                 ║
║  Phase 5: Stealth Binary — Hides Python Engine via Nuitka                    ║
║                                                                              ║
║  What this does:                                                             ║
║    1. Takes the entire Lumina Python compiler suite                          ║
║    2. Uses Nuitka to translate it to C++ → machine code                     ║
║    3. Produces a single binary: lumina  (or lumina.exe on Windows)           ║
║                                                                              ║
║  After running this:                                                         ║
║    ./lumina build hello.lum     ← users run this                            ║
║    ./lumina repl                ← interactive mode                           ║
║    ./lumina install math        ← install modules                           ║
║    (No Python, no Lark visible in 'ps aux' — complete stealth)              ║
║                                                                              ║
║  Usage:                                                                      ║
║    python freeze_compiler.py                   # build lumina binary        ║
║    python freeze_compiler.py --check           # check Nuitka is installed  ║
║    python freeze_compiler.py --no-nuitka       # fallback: PyInstaller      ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations
import os
import sys
import shutil
import subprocess
import argparse
import textwrap
from pathlib import Path

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — COLOUR OUTPUT
# ══════════════════════════════════════════════════════════════════════════════

class C:
    _on   = sys.stdout.isatty()
    OK    = "\033[92m" if _on else ""
    WARN  = "\033[93m" if _on else ""
    ERR   = "\033[91m" if _on else ""
    BOLD  = "\033[1m"  if _on else ""
    DIM   = "\033[2m"  if _on else ""
    RESET = "\033[0m"  if _on else ""

def ok(msg):    print(f"  {C.OK}✔{C.RESET}  {msg}")
def warn(msg):  print(f"  {C.WARN}⚠{C.RESET}  {msg}")
def err(msg):   print(f"  {C.ERR}✖{C.RESET}  {msg}")
def step(n, m): print(f"\n{C.BOLD}── Step {n}: {m}{C.RESET}")
def banner(m):
    line = "═" * (len(m) + 4)
    print(f"\n{C.BOLD}{line}\n  {m}\n{line}{C.RESET}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — THE LUMINA CLI ENTRY POINT
# This is the main() that gets compiled into the 'lumina' binary.
# We write it to lumina_cli.py and freeze THAT.
# ══════════════════════════════════════════════════════════════════════════════

LUMINA_CLI_SOURCE = '''\
"""
LUMINA CLI — lumina_cli.py
This is the entry point that becomes the 'lumina' binary after Nuitka freeze.
Usage:
  lumina build hello.lum        # compile an English program
  lumina build hello.lum -o App # custom output name
  lumina repl                   # interactive REPL
  lumina install math           # install a module
  lumina remove math            # remove a module
  lumina list                   # list installed modules
  lumina search physics         # search registry
  lumina check                  # check build tools
  lumina version                # print version
"""

from __future__ import annotations
import sys
import os
import argparse
import textwrap
from pathlib import Path


# ── Version ───────────────────────────────────────────────────────────────────
LUMINA_VERSION = "0.5.0"
LUMINA_BANNER  = f"""
╔══════════════════════════════════════════════════════════════╗
║   🌟  LUMINA  v{LUMINA_VERSION}  — The English Programming Language      ║
║   Build fast. Write plain English. Stay safe.               ║
╠══════════════════════════════════════════════════════════════╣
║  lumina build  hello.lum    ← compile your program          ║
║  lumina install math        ← install a module              ║
║  lumina repl                ← interactive shell             ║
╚══════════════════════════════════════════════════════════════╝
"""


def cmd_build(args):
    """Compile a .lum file → native binary."""
    from lumina_build_p5 import build, check_tools, LuminaBuildError
    if not check_tools(verbose=False):
        print("[Lumina] Missing tools. Run: lumina check")
        sys.exit(1)
    try:
        exe = build(
            source_lum  = args.source,
            output_exe  = args.output or "LuminaApp",
            opt_level   = args.opt,
            keep_ir     = not args.no_ir,
            verbose     = not args.quiet,
        )
        print(f"\\n  Your Lumina app is ready:  ./{Path(exe).name}\\n")
    except LuminaBuildError as e:
        print(f"\\n  BUILD FAILED: {e}")
        sys.exit(1)


def cmd_repl(args):
    """Start interactive English REPL."""
    from lumina_repl_p5 import run_repl
    run_repl()


def cmd_install(args):
    """Install a module via LPM."""
    from lpm import LuminaPackageManager
    lpm = LuminaPackageManager(lib_dir=args.lib_dir or "lib")
    ok, msg = lpm.install(args.name)
    print(f"  {'✔' if ok else '✖'}  {msg}")
    sys.exit(0 if ok else 1)


def cmd_remove(args):
    """Remove an installed module."""
    from lpm import LuminaPackageManager
    lpm = LuminaPackageManager(lib_dir=args.lib_dir or "lib")
    ok, msg = lpm.remove(args.name)
    print(f"  {'✔' if ok else '✖'}  {msg}")
    sys.exit(0 if ok else 1)


def cmd_list(args):
    """List installed / available modules."""
    from lpm import LuminaPackageManager
    lpm = LuminaPackageManager(lib_dir=args.lib_dir or "lib")
    pkgs = lpm.list_available() if args.available else lpm.list_installed()
    label = "Available" if args.available else "Installed"
    print(f"\\n  {label} modules ({len(pkgs)}):")
    for p in pkgs:
        tag = "[builtin]" if p.get("builtin") else "[.lum]"
        print(f"    {tag:10s}  {p.get(\'name\',\'?\')}  v{p.get(\'version\',\'?\')}  — {p.get(\'description\',\'\')[:55]}")


def cmd_search(args):
    """Search the module registry."""
    from lpm import LuminaPackageManager
    lpm = LuminaPackageManager()
    results = lpm.search(args.query)
    if not results:
        print(f"  No results for \\'{args.query}\\'.")
    for p in results:
        print(f"  {p.get(\'name\'):15s} v{p.get(\'version\',\'?\')} — {p.get(\'description\',\'\')}")


def cmd_check(args):
    """Check that all required build tools are installed."""
    from lumina_build_p5 import check_tools, get_clang_version
    print(f"  Clang: {get_clang_version()}")
    all_ok = check_tools(verbose=True)
    sys.exit(0 if all_ok else 1)


def cmd_version(args):
    print(f"  Lumina v{LUMINA_VERSION}")


# ── Argument parser ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="lumina",
        description="Lumina — The English Programming Language",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
        Examples:
          lumina build hello.lum
          lumina build hello.lum -o MyApp --opt O3
          lumina repl
          lumina install math
          lumina list --available
          lumina check
        """)
    )

    if len(sys.argv) == 1:
        print(LUMINA_BANNER)
        parser.print_help()
        sys.exit(0)

    sub = parser.add_subparsers(dest="command", required=True)

    # build
    pb = sub.add_parser("build", help="Compile a .lum file")
    pb.add_argument("source",           help="Path to .lum file")
    pb.add_argument("-o","--output",    default=None, help="Output binary name")
    pb.add_argument("--opt",            default="O3", choices=["O0","O1","O2","O3","Os"])
    pb.add_argument("--no-ir",          action="store_true", help="Don\'t save .ll IR file")
    pb.add_argument("--quiet","-q",     action="store_true")

    # repl
    pr = sub.add_parser("repl", help="Interactive English REPL")

    # install / remove / list / search
    pi = sub.add_parser("install", help="Install a module")
    pi.add_argument("name")
    pi.add_argument("--lib-dir", default="lib")

    prm = sub.add_parser("remove", help="Remove a module")
    prm.add_argument("name")
    prm.add_argument("--lib-dir", default="lib")

    pl = sub.add_parser("list", help="List modules")
    pl.add_argument("--lib-dir",   default="lib")
    pl.add_argument("--available", action="store_true")

    ps = sub.add_parser("search", help="Search module registry")
    ps.add_argument("query")

    sub.add_parser("check",   help="Check build tools")
    sub.add_parser("version", help="Print version")

    args = parser.parse_args()
    dispatch = {
        "build":   cmd_build,
        "repl":    cmd_repl,
        "install": cmd_install,
        "remove":  cmd_remove,
        "list":    cmd_list,
        "search":  cmd_search,
        "check":   cmd_check,
        "version": cmd_version,
    }
    fn = dispatch.get(args.command)
    if fn:
        fn(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
'''


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — COMPILER MODULES LIST
# Every .py file that must be included in the frozen binary.
# ══════════════════════════════════════════════════════════════════════════════

COMPILER_MODULES = [
    "compiler_p5.py",
    "codegen_p5.py",
    "safety_p5.py",
    "lumina_build_p5.py",
    "lumina_repl_p5.py",
    "lpm.py",
    "lumina_ghost.c",   # not frozen — compiled separately by build system
]

DATA_FILES = [
    "core.ll",
    "core.o",           # pre-compiled core library (built by build.sh)
]


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — NUITKA FREEZE LOGIC
# ══════════════════════════════════════════════════════════════════════════════

def check_nuitka() -> bool:
    """Return True if Nuitka is importable."""
    return shutil.which("nuitka") is not None or shutil.which("nuitka3") is not None

def check_pyinstaller() -> bool:
    return shutil.which("pyinstaller") is not None


def run_cmd(cmd: list, label: str) -> bool:
    print(f"    {C.DIM}$ {' '.join(str(c) for c in cmd)}{C.RESET}")
    result = subprocess.run(cmd, capture_output=False, text=True)
    if result.returncode != 0:
        err(f"{label} failed (exit {result.returncode})")
        return False
    return True


def write_cli_script(dest_dir: Path) -> Path:
    """Write the lumina_cli.py entry point to disk."""
    path = dest_dir / "lumina_cli.py"
    path.write_text(LUMINA_CLI_SOURCE)
    ok(f"Entry point written → {path}")
    return path


def freeze_with_nuitka(entry_script: Path, output_dir: Path,
                       output_name: str = "lumina",
                       lto: bool = True) -> bool:
    """
    Run Nuitka to compile lumina_cli.py → standalone binary.

    Key Nuitka flags used:
      --standalone     : bundle all imports, no Python needed on target
      --onefile        : pack everything into a single executable
      --lto=yes        : Link-Time Optimisation (faster binary)
      --python-flag=no_site    : don't load site.py (smaller, faster startup)
      --assume-yes-for-downloads : auto-download Nuitka dependencies
      --output-dir     : where to put the result
    """
    nuitka_cmd = shutil.which("nuitka") or shutil.which("nuitka3") or "nuitka"

    cmd = [
        sys.executable, "-m", "nuitka",
        "--standalone",
        "--onefile",
        f"--output-dir={output_dir}",
        f"--output-filename={output_name}",
        "--python-flag=no_site",
        "--assume-yes-for-downloads",
        "--follow-imports",
        "--include-package=lark",
        "--include-package=lpm",
        "--include-package=compiler_p5",
        "--include-package=codegen_p5",
        "--include-package=safety_p5",
        "--include-package=lumina_build_p5",
        "--include-package=lumina_repl_p5",
    ]

    if lto:
        cmd.append("--lto=yes")

    # Platform-specific optimisations
    if sys.platform == "linux":
        cmd += ["--linux-onefile-compression=zstd"]
    elif sys.platform == "darwin":
        cmd += ["--macos-create-app-bundle"]

    cmd.append(str(entry_script))
    return run_cmd(cmd, "Nuitka compilation")


def freeze_with_pyinstaller(entry_script: Path, output_dir: Path,
                            output_name: str = "lumina") -> bool:
    """
    Fallback: use PyInstaller if Nuitka is not available.
    Produces a ~50MB onefile executable.
    Note: PyInstaller does NOT convert Python to C++.
    The binary still contains a compressed Python interpreter.
    """
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--name", output_name,
        "--distpath", str(output_dir),
        "--clean",
        "--hidden-import", "lark",
        "--hidden-import", "lark.grammars",
        "--hidden-import", "lark.parsers",
        str(entry_script),
    ]
    return run_cmd(cmd, "PyInstaller compilation")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — BUILD CORE.O  (pre-compile core.ll → core.o)
# ══════════════════════════════════════════════════════════════════════════════

def build_core_object(core_ll: Path, output_dir: Path) -> Optional[Path]:
    """
    Compile core.ll → core.o using llc + clang.
    This is the Phase 5 performance boost:
    standard math functions are pre-compiled and linked without re-parsing.
    """
    if not core_ll.exists():
        warn(f"core.ll not found at {core_ll} — skipping core.o build")
        return None

    core_o = output_dir / "core.o"
    clang  = shutil.which("clang")
    if not clang:
        warn("clang not found — skipping core.o. Math module will still work via JIT.")
        return None

    cmd = [
        "clang", "-O3", "-c",
        str(core_ll),
        "-o", str(core_o),
        "-lm",
    ]
    print(f"    {C.DIM}$ {' '.join(cmd)}{C.RESET}")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        warn(f"core.o build failed: {r.stderr.strip()}")
        return None
    ok(f"core.o built → {core_o}")
    return core_o


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        prog="freeze_compiler",
        description="Lumina Freeze Compiler — build the standalone 'lumina' binary",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
        Examples:
          python freeze_compiler.py              # build lumina binary (uses Nuitka)
          python freeze_compiler.py --no-nuitka  # fallback to PyInstaller
          python freeze_compiler.py --check      # check tools only
          python freeze_compiler.py --core-only  # only build core.o
        """)
    )
    parser.add_argument("--check",      action="store_true", help="Check tools and exit")
    parser.add_argument("--no-nuitka",  action="store_true", help="Use PyInstaller instead of Nuitka")
    parser.add_argument("--core-only",  action="store_true", help="Only build core.o from core.ll")
    parser.add_argument("--output-dir", default="dist",      help="Output directory (default: dist)")
    parser.add_argument("--name",       default="lumina",    help="Binary name (default: lumina)")
    parser.add_argument("--no-lto",     action="store_true", help="Disable LTO (faster build, slower binary)")
    args = parser.parse_args()

    banner("LUMINA FREEZE COMPILER v0.5.0")

    # ── Tool check ─────────────────────────────────────────────────────────────
    step("✦", "Checking available tools")
    has_nuitka = check_nuitka()
    has_pyi    = check_pyinstaller()
    has_clang  = bool(shutil.which("clang"))

    ok(f"Nuitka        : {'✔ found' if has_nuitka else '✖ not found'}")
    ok(f"PyInstaller   : {'✔ found' if has_pyi    else '✖ not found'}")
    ok(f"Clang         : {'✔ found' if has_clang  else '✖ not found'}")
    ok(f"Python        : {sys.version.split()[0]}")

    if args.check:
        print()
        if not has_nuitka:
            print("  Install Nuitka:       pip install nuitka")
        if not has_pyi:
            print("  Install PyInstaller:  pip install pyinstaller")
        sys.exit(0 if (has_nuitka or has_pyi) else 1)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    here       = Path(__file__).parent

    # ── Step 1: Build core.o ──────────────────────────────────────────────────
    step(1, "Pre-compile core.ll → core.o")
    core_ll = here / "core.ll"
    core_o  = build_core_object(core_ll, here)

    if args.core_only:
        print(f"\n  core.o build {'complete' if core_o else 'skipped'}.")
        sys.exit(0 if core_o else 1)

    # ── Step 2: Write lumina_cli.py ───────────────────────────────────────────
    step(2, "Write CLI entry point (lumina_cli.py)")
    cli_script = write_cli_script(here)

    # ── Step 3: Freeze ────────────────────────────────────────────────────────
    step(3, "Freeze compiler → standalone binary")

    use_nuitka = has_nuitka and not args.no_nuitka
    use_pyi    = has_pyi    and (args.no_nuitka or not has_nuitka)

    if use_nuitka:
        ok(f"Using Nuitka (C++ backend) → maximum performance")
        success = freeze_with_nuitka(cli_script, output_dir,
                                     output_name=args.name,
                                     lto=not args.no_lto)
    elif use_pyi:
        warn("Nuitka not available — using PyInstaller (Python still inside binary)")
        ok("Binary will still hide 'python' from process tree")
        success = freeze_with_pyinstaller(cli_script, output_dir, output_name=args.name)
    else:
        err("Neither Nuitka nor PyInstaller found.")
        print("  Install either:   pip install nuitka   OR   pip install pyinstaller")
        sys.exit(1)

    if not success:
        err("Freeze step failed. Check output above.")
        sys.exit(1)

    # ── Step 4: Bundle core.o with binary ─────────────────────────────────────
    step(4, "Bundle core.o alongside binary")
    if core_o and core_o.exists():
        dest_core = output_dir / "core.o"
        if str(core_o) != str(dest_core):
            shutil.copy(str(core_o), str(dest_core))
        ok(f"core.o bundled → {dest_core}")
    else:
        warn("core.o not available — math functions will use JIT fallback")

    # ── Summary ───────────────────────────────────────────────────────────────
    binary_name = args.name + (".exe" if sys.platform == "win32" else "")
    binary_path = output_dir / binary_name
    print(f"\n{'═'*62}")
    print(f"  ✅  BUILD COMPLETE")
    print(f"  Binary : {binary_path}")
    if binary_path.exists():
        size_mb = binary_path.stat().st_size / (1024 * 1024)
        print(f"  Size   : {size_mb:.1f} MB")
    print(f"")
    print(f"  Users run:  ./{binary_name} build hello.lum")
    print(f"              ./{binary_name} install math")
    print(f"              ./{binary_name} repl")
    print(f"  Python/Lark are completely hidden from the user.")
    print(f"{'═'*62}\n")


if __name__ == "__main__":
    main()
