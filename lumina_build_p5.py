"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  LUMINA BUILD ORCHESTRATOR v0.5.0 — lumina_build_p5.py                      ║
║                                                                              ║
║  Phase 5 changes vs Phase 3/4:                                               ║
║    • Links core.o (pre-compiled standard library) automatically              ║
║    • Links any .lum module files found in /lib (as .ll IR files)            ║
║    • Uses Phase 5 compiler (compiler_p5.py + codegen_p5.py)                 ║
║    • Accepts --lib-dir to specify custom module directory                    ║
║    • Name mangling is applied automatically                                  ║
║                                                                              ║
║  Build Pipeline:                                                             ║
║    Step 1: English (.lum) → LLVM IR (.ll)  [compiler_p5 + codegen_p5]       ║
║    Step 2: Compile lumina_ghost.c → ghost.o                                 ║
║    Step 3: Compile core.ll → core.o  (if not already compiled)              ║
║    Step 4: Compile any lib/*.ll module files → module.o files               ║
║    Step 5: Link: .ll + ghost.o + core.o + module.o(s) → final binary       ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations
import os
import sys
import json
import shutil
import tempfile
import argparse
import textwrap
import subprocess
from pathlib import Path
from typing import List, Optional, Dict


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
# SECTION 2 — TOOL CHECKS
# ══════════════════════════════════════════════════════════════════════════════

REQUIRED_TOOLS = {
    "clang":   "Install LLVM/clang: https://releases.llvm.org/",
    "python3": "Python 3.8+ required",
}

def check_tools(verbose: bool = True) -> bool:
    all_ok = True
    if verbose: print("\n  Checking build tools:")
    for tool, hint in REQUIRED_TOOLS.items():
        found = shutil.which(tool)
        if found:
            if verbose: ok(f"{tool:10s} → {C.DIM}{found}{C.RESET}")
        else:
            all_ok = False
            if verbose:
                err(f"{tool:10s} → NOT FOUND")
                print(f"            {C.DIM}{hint}{C.RESET}")
    return all_ok

def get_clang_version() -> str:
    try:
        r = subprocess.run(["clang","--version"], capture_output=True, text=True, timeout=5)
        return r.stdout.split("\n")[0].strip()
    except Exception:
        return "unknown"


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — COMMAND RUNNER
# ══════════════════════════════════════════════════════════════════════════════

class LuminaBuildError(Exception):
    pass

def run_cmd(cmd: list, label: str, capture: bool = False) -> subprocess.CompletedProcess:
    print(f"    {C.DIM}$ {' '.join(str(c) for c in cmd)}{C.RESET}")
    result = subprocess.run(cmd, capture_output=capture, text=True)
    if result.returncode != 0:
        err(f"{label} failed (exit {result.returncode})")
        if capture and result.stderr:
            print(textwrap.indent(result.stderr.strip(), "    "))
        raise LuminaBuildError(f"{label} failed")
    return result


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — LUMINA IR GENERATOR  (calls Phase 5 compiler)
# ══════════════════════════════════════════════════════════════════════════════

def compile_lum_to_ir(source_path: str,
                      lib_dir: Optional[str] = None,
                      module_name: str = "main") -> str:
    """
    Parse a .lum source file using Phase 5 compiler pipeline.
    Returns the generated LLVM IR as a string.
    """
    source_text = Path(source_path).read_text(encoding="utf-8")

    from compiler_p5 import LuminaFrontEndP5
    from codegen_p5  import LLVMGeneratorP5
    from safety_p5   import run_safety

    lib_dirs = [lib_dir] if lib_dir else None
    fe       = LuminaFrontEndP5(lib_dirs=lib_dirs)
    nodes    = fe.parse_program(source_text)

    # Print diagnostics
    diags = fe.diagnostics
    diags.print_all()
    if diags.has_errors():
        raise LuminaBuildError("Parse errors — fix your English code and try again.")

    # Safety check
    safety = run_safety(nodes,
                        struct_registry=fe.struct_registry,
                        loaded_modules=fe.loaded_modules)

    if not safety.safe:
        print("\n  ⛔  Safety violations detected:")
        for e in safety.errors():
            print(f"     [{e.code}] {e.message}")
            if e.hint: print(f"            → {e.hint}")
        raise LuminaBuildError("Safety check failed — fix violations above.")

    for w in safety.warnings():
        warn(f"[{w.code}] {w.message}")

    # Code generation
    gen = LLVMGeneratorP5(
        struct_registry=fe.struct_registry,
        module_name=module_name,
        external_syms=fe.external_symbols,
    )
    ir = gen.generate(nodes)
    return ir


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — FIND OBJECT FILES
# Phase 5: automatically find core.o and any module .o files to link.
# ══════════════════════════════════════════════════════════════════════════════

def _find_script_dir() -> Path:
    """Find the directory where lumina_build_p5.py lives."""
    return Path(__file__).parent if not getattr(sys, 'frozen', False) else Path(sys.executable).parent


def find_core_object(work_dir: Path) -> Optional[Path]:
    """
    Look for core.o in these locations (in order):
      1. Same directory as this script
      2. dist/ directory (if frozen binary)
      3. Build it from core.ll on the fly
    """
    script_dir = _find_script_dir()

    for candidate in [
        script_dir / "core.o",
        script_dir / "dist" / "core.o",
        Path("core.o"),
        Path("dist") / "core.o",
    ]:
        if candidate.exists():
            return candidate

    # Try to build core.o from core.ll
    core_ll = script_dir / "core.ll"
    if not core_ll.exists():
        core_ll = Path("core.ll")
    if core_ll.exists():
        core_o = work_dir / "core.o"
        try:
            r = subprocess.run(
                ["clang", "-O3", "-c", str(core_ll), "-o", str(core_o), "-lm"],
                capture_output=True, text=True, timeout=30
            )
            if r.returncode == 0:
                ok(f"Built core.o from core.ll → {core_o}")
                return core_o
        except Exception:
            pass

    warn("core.o not found — math/time functions require 'use the math library' first.")
    return None


def find_module_objects(lib_dir: Optional[str], work_dir: Path,
                        loaded_modules: Optional[Dict[str, str]] = None) -> List[Path]:
    """
    For each module that has a .lum or .ll source, compile it to a .o object.
    Returns list of .o paths to link.
    """
    objects: List[Path] = []
    if not loaded_modules:
        return objects

    ldir = Path(lib_dir or "lib")
    for mod_name, mod_path in loaded_modules.items():
        if not mod_path or mod_path == "<builtin>":
            continue  # built-in modules are in core.o

        p = Path(mod_path)
        if not p.exists():
            warn(f"Module file not found: {p}")
            continue

        if p.suffix == ".ll":
            # Compile .ll → .o
            out_o = work_dir / f"{mod_name}.o"
            try:
                r = subprocess.run(
                    ["clang", "-O3", "-c", str(p), "-o", str(out_o)],
                    capture_output=True, text=True
                )
                if r.returncode == 0:
                    objects.append(out_o)
                    ok(f"Compiled {p.name} → {out_o.name}")
                else:
                    warn(f"Failed to compile {p.name}: {r.stderr[:80]}")
            except Exception as e:
                warn(f"Could not compile {p.name}: {e}")

        elif p.suffix == ".lum":
            # Compile .lum → .ll → .o
            try:
                mod_ir = compile_lum_to_ir(str(p), lib_dir=str(ldir), module_name=mod_name)
                ll_path = work_dir / f"{mod_name}.ll"
                ll_path.write_text(mod_ir)
                out_o   = work_dir / f"{mod_name}.o"
                r = subprocess.run(
                    ["clang", "-O3", "-c", str(ll_path), "-o", str(out_o)],
                    capture_output=True, text=True
                )
                if r.returncode == 0:
                    objects.append(out_o)
                    ok(f"Compiled module '{mod_name}' (.lum) → {out_o.name}")
                else:
                    warn(f"Failed to compile module '{mod_name}': {r.stderr[:80]}")
            except Exception as e:
                warn(f"Could not compile module '{mod_name}': {e}")

    return objects


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — MAIN BUILD FUNCTION
# ══════════════════════════════════════════════════════════════════════════════

def build(
    source_lum:  str,
    output_exe:  str  = "LuminaApp",
    opt_level:   str  = "O3",
    ghost_c:     str  = "lumina_ghost.c",
    lib_dir:     Optional[str] = None,
    keep_ir:     bool = True,
    verbose:     bool = True,
) -> str:
    """
    Full Phase 5 build pipeline.
    Returns path to final executable.
    Raises LuminaBuildError on failure.
    """
    banner(f"LUMINA BUILD SYSTEM v0.5.0  —  {Path(source_lum).name}")

    # ── Tool check ──────────────────────────────────────────────────────────
    if not shutil.which("clang"):
        raise LuminaBuildError("clang not found. Install LLVM: https://releases.llvm.org/")

    # ── Working directory ───────────────────────────────────────────────────
    work_dir  = Path(tempfile.mkdtemp(prefix="lumina_build_"))
    ll_path   = work_dir / "lumina_output.ll"
    ghost_obj = work_dir / "lumina_ghost.o"
    exe_path  = Path(output_exe)

    try:
        # ════════════════════════════════════════════════════════════════════
        # STEP 1 — English → LLVM IR  (Phase 5 pipeline)
        # ════════════════════════════════════════════════════════════════════
        step(1, "English → LLVM IR  (Phase 5 compiler)")

        # Import Phase 5 front-end to know which modules were loaded
        from compiler_p5 import LuminaFrontEndP5
        source_text = Path(source_lum).read_text(encoding="utf-8")
        fe          = LuminaFrontEndP5(lib_dirs=[lib_dir] if lib_dir else None)
        nodes       = fe.parse_program(source_text)

        # Diagnostics
        fe.diagnostics.print_all()
        if fe.diagnostics.has_errors():
            raise LuminaBuildError("Parse errors — fix your code.")

        # Safety
        from safety_p5 import run_safety
        safety = run_safety(nodes,
                            struct_registry=fe.struct_registry,
                            loaded_modules=fe.loaded_modules)
        if not safety.safe:
            for v in safety.errors():
                err(f"[{v.code}] {v.message}")
                if v.hint: print(f"         → {v.hint}")
            raise LuminaBuildError("Safety check failed.")
        for w in safety.warnings():
            warn(f"[{w.code}] {w.message}")

        # Code generation
        from codegen_p5 import LLVMGeneratorP5
        gen = LLVMGeneratorP5(
            struct_registry=fe.struct_registry,
            module_name="main",
            external_syms=fe.external_symbols,
        )
        ir = gen.generate(nodes)
        ll_path.write_text(ir)
        ok(f"LLVM IR generated → {ll_path}")

        if verbose:
            print(f"\n{C.DIM}  ── IR preview (first 30 lines) ───────────────────────")
            lines = ir.split("\n")
            for i, ln in enumerate(lines[:30]):
                print("  " + ln)
            if len(lines) > 30:
                print(f"  ... ({len(lines) - 30} more lines)")
            print(f"  ──────────────────────────────────────────────────{C.RESET}\n")

        # ════════════════════════════════════════════════════════════════════
        # STEP 2 — Compile lumina_ghost.c → ghost.o
        # ════════════════════════════════════════════════════════════════════
        step(2, "Compile Ghost Linker  (lumina_ghost.c → ghost.o)")

        script_dir  = _find_script_dir()
        ghost_path  = Path(ghost_c)
        if not ghost_path.exists():
            ghost_path = script_dir / ghost_c
        if not ghost_path.exists():
            raise LuminaBuildError(
                f"lumina_ghost.c not found.\n"
                f"Generate it: python lumina_lexer.py --ghost-c > lumina_ghost.c"
            )

        python_include = subprocess.run(
            [sys.executable, "-c",
             "import sysconfig; print(sysconfig.get_path('include'))"],
            capture_output=True, text=True
        ).stdout.strip()

        run_cmd(
            ["clang", "-c", f"-{opt_level}", f"-I{python_include}",
             str(ghost_path), "-o", str(ghost_obj)],
            label="Clang ghost.c → ghost.o"
        )
        ok(f"Ghost object → {ghost_obj}")

        # ════════════════════════════════════════════════════════════════════
        # STEP 3 — Find / build core.o  (Phase 5 standard library)
        # ════════════════════════════════════════════════════════════════════
        step(3, "Locate / build core.o  (Phase 5 stdlib)")
        core_o = find_core_object(work_dir)
        if core_o:
            ok(f"core.o ready → {core_o}")
        else:
            warn("Proceeding without core.o — stdlib math functions unavailable")

        # ════════════════════════════════════════════════════════════════════
        # STEP 4 — Compile any .lum module files
        # ════════════════════════════════════════════════════════════════════
        if fe.loaded_modules:
            step(4, "Compile imported modules")
            module_objects = find_module_objects(
                lib_dir=lib_dir, work_dir=work_dir,
                loaded_modules={k: v for k, v in fe.loaded_modules.items() if v != "<builtin>"}
            )
        else:
            module_objects = []
            step(4, "No user modules to compile")
            ok("(no 'use the X library' statements found — skipping)")

        # ════════════════════════════════════════════════════════════════════
        # STEP 5 — Link everything → final binary
        # ════════════════════════════════════════════════════════════════════
        step(5, f"Link → {exe_path.name}")

        link_cmd = [
            "clang", f"-{opt_level}",
            str(ll_path),
            str(ghost_obj),
        ]
        if core_o:
            link_cmd.append(str(core_o))
        for mo in module_objects:
            link_cmd.append(str(mo))

        link_cmd += ["-o", str(exe_path), "-lm"]

        # Python link flags
        python_ldflags = subprocess.run(
            [sys.executable, "-c",
             "import sysconfig; cfg = sysconfig.get_config_vars(); "
             "print('-L' + cfg.get('LIBDIR','') + ' ' + cfg.get('BLDLIBRARY',''))"],
            capture_output=True, text=True
        ).stdout.strip()
        if python_ldflags.strip():
            link_cmd += python_ldflags.split()
        if sys.platform == "darwin":
            link_cmd += ["-framework", "Python"]

        run_cmd(link_cmd, "Clang link")
        ok(f"Executable → {exe_path.resolve()}")

        # ── Keep .ll ────────────────────────────────────────────────────────
        if keep_ir:
            ir_dest = Path(str(exe_path) + ".ll")
            shutil.copy(str(ll_path), str(ir_dest))
            ok(f"IR saved → {ir_dest.resolve()}")

        # ── Summary ──────────────────────────────────────────────────────────
        linked_items = ["main.ll", "ghost.o"]
        if core_o:         linked_items.append("core.o")
        if module_objects: linked_items += [mo.name for mo in module_objects]

        print(f"\n{'═'*66}")
        print(f"  {C.OK}{C.BOLD}BUILD SUCCESSFUL ✔{C.RESET}")
        print(f"  Linked: {', '.join(linked_items)}")
        print(f"  Run your app:  ./{exe_path.name}")
        print(f"{'═'*66}\n")

        return str(exe_path.resolve())

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 7 — DEMO SOURCE
# ══════════════════════════════════════════════════════════════════════════════

DEMO_LUM_P5 = """\
# ═══════════════════════════════════════════════════════════
# LUMINA v0.5.0 — Phase 5 Demo
# This program uses the math module and name mangling.
# ═══════════════════════════════════════════════════════════

# Import the math standard library
use the math library

# Variables
create a decimal called radius with value 5.0
create a decimal called pi with value 3.14159

# Use the pre-compiled math function (no re-parsing overhead!)
calculate area as pi times radius

# Loop example
create a number called count with value 1
create a number called limit with value 5

Repeat while count is less than limit
  increase count by 1
  show count
Stop

# Show results
show area
show radius

# Free heap values would go here if we used text variables
"""


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 8 — CLI
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        prog="lumina_build_p5",
        description="Lumina Build Orchestrator v0.5.0",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
        Examples:
          python lumina_build_p5.py                       # demo build
          python lumina_build_p5.py hello.lum             # build your file
          python lumina_build_p5.py hello.lum -o App      # custom output
          python lumina_build_p5.py --check               # check tools
          python lumina_build_p5.py --generate-demo       # write demo.lum
        """)
    )
    parser.add_argument("source", nargs="?", default=None)
    parser.add_argument("-o","--output",       default="LuminaApp")
    parser.add_argument("--opt","-O",          default="O3",
                        choices=["O0","O1","O2","O3","Os","Oz"])
    parser.add_argument("--ghost-c",           default="lumina_ghost.c")
    parser.add_argument("--lib-dir",           default=None)
    parser.add_argument("--no-keep-ir",        action="store_true")
    parser.add_argument("--check",             action="store_true")
    parser.add_argument("--generate-demo",     action="store_true")
    parser.add_argument("--quiet","-q",        action="store_true")
    args = parser.parse_args()

    if args.check:
        banner("Tool Check")
        print(f"  Clang: {get_clang_version()}")
        all_ok = check_tools(verbose=True)
        sys.exit(0 if all_ok else 1)

    if args.generate_demo:
        with open("demo_p5.lum","w") as f: f.write(DEMO_LUM_P5)
        ok("Wrote demo_p5.lum")
        sys.exit(0)

    if not check_tools(verbose=False):
        err("Missing required build tools. Run with --check for details.")
        sys.exit(1)

    if args.source is None:
        import tempfile as _tf
        tmp = _tf.NamedTemporaryFile(mode="w", suffix=".lum", delete=False, prefix="lumina_demo_")
        tmp.write(DEMO_LUM_P5)
        tmp.close()
        source_path = tmp.name
        print(f"\n  {C.DIM}No source — building built-in Phase 5 demo{C.RESET}")
    else:
        source_path = args.source
        if not Path(source_path).exists():
            err(f"Source file not found: {source_path}")
            sys.exit(1)

    try:
        exe = build(
            source_lum = source_path,
            output_exe = args.output,
            opt_level  = args.opt,
            ghost_c    = args.ghost_c,
            lib_dir    = args.lib_dir,
            keep_ir    = not args.no_keep_ir,
            verbose    = not args.quiet,
        )
        print(f"  {C.BOLD}Your Lumina app:{C.RESET}  ./{Path(exe).name}\n")
    except LuminaBuildError as e:
        err(f"BUILD FAILED: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n  Build cancelled.")
        sys.exit(130)


if __name__ == "__main__":
    main()
