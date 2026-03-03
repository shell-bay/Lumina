"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  LUMINA TEST SUITE v0.5.0 — test_phase5.py                                  ║
║  Validates all Phase 5 features automatically.                               ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import sys
import os

# Add current dir to path so we can import our modules
sys.path.insert(0, os.path.dirname(__file__))

from compiler_p5 import LuminaFrontEndP5, CORE_FUNCTION_MAP
from codegen_p5  import LLVMGeneratorP5
from safety_p5   import run_safety
from lpm         import LuminaPackageManager

PASS = "✔"
FAIL = "✖"
results = []

def test(name: str, fn):
    try:
        fn()
        results.append((PASS, name))
        print(f"  {PASS}  {name}")
    except Exception as e:
        results.append((FAIL, name))
        print(f"  {FAIL}  {name}")
        print(f"       Error: {e}")


def compile_and_get_ir(source: str) -> str:
    fe     = LuminaFrontEndP5(lib_dirs=["lib"])
    nodes  = fe.parse_program(source)
    safety = run_safety(nodes, fe.struct_registry, fe.loaded_modules)
    assert safety.safe, f"Safety failed: {[e.message for e in safety.errors()]}"
    gen = LLVMGeneratorP5(
        struct_registry=fe.struct_registry,
        module_name="main",
        external_syms=fe.external_symbols,
    )
    return gen.generate(nodes)


# ══════════════════════════════════════════════════════════════════════════════
# TEST 1: Basic variable creation and print
# ══════════════════════════════════════════════════════════════════════════════
def t1():
    ir = compile_and_get_ir("""
create a number called age with value 25
show age
""")
    assert "printf" in ir
    assert "__lum_main_age" in ir   # name mangling applied

test("Variable creation + name mangling (age → __lum_main_age)", t1)


# ══════════════════════════════════════════════════════════════════════════════
# TEST 2: Module USE keyword parsed
# ══════════════════════════════════════════════════════════════════════════════
def t2():
    fe    = LuminaFrontEndP5(lib_dirs=["lib"])
    nodes = fe.parse_program("use the math library")
    mod_use_nodes = [n for n in nodes if n.intent == "MODULE_USE"]
    assert len(mod_use_nodes) >= 1
    assert mod_use_nodes[0].module_name == "math"

test("USE keyword → MODULE_USE node with module_name='math'", t2)


# ══════════════════════════════════════════════════════════════════════════════
# TEST 3: External declarations emitted for math module
# ══════════════════════════════════════════════════════════════════════════════
def t3():
    ir = compile_and_get_ir("use the math library")
    assert "declare double @__lum_core_sqrt" in ir

test("use math → LLVM 'declare double @__lum_core_sqrt' in IR", t3)


# ══════════════════════════════════════════════════════════════════════════════
# TEST 4: IMPORT_CALL → call instruction in IR
# ══════════════════════════════════════════════════════════════════════════════
def t4():
    ir = compile_and_get_ir("""
use the math library
create a decimal called x with value 16.0
calculate root as square root of x
show root
""")
    assert "call double @__lum_core_sqrt" in ir

test("calculate square root of x → 'call double @__lum_core_sqrt' in IR", t4)


# ══════════════════════════════════════════════════════════════════════════════
# TEST 5: Name collision prevention
# ══════════════════════════════════════════════════════════════════════════════
def t5():
    ir = compile_and_get_ir("""
create a decimal called pi with value 3.14
""")
    assert "__lum_main_pi" in ir
    assert "pi.v0" not in ir.split("__lum_main_")[0]  # raw 'pi' not used

test("Name mangling: 'pi' → '__lum_main_pi' (no collision with math module)", t5)


# ══════════════════════════════════════════════════════════════════════════════
# TEST 6: While loop
# ══════════════════════════════════════════════════════════════════════════════
def t6():
    ir = compile_and_get_ir("""
create a number called count with value 0
create a number called limit with value 3
Repeat while count is less than limit
  increase count by 1
Stop
""")
    assert "loop.hdr" in ir
    assert "loop.body" in ir
    assert "loop.end"  in ir

test("While loop generates loop.hdr / loop.body / loop.end blocks", t6)


# ══════════════════════════════════════════════════════════════════════════════
# TEST 7: If/else block
# ══════════════════════════════════════════════════════════════════════════════
def t7():
    ir = compile_and_get_ir("""
create a number called score with value 75
If score is greater than 50 then show score otherwise show score
""")
    assert "if.then" in ir
    assert "if.else" in ir

test("If/else → if.then / if.else blocks in IR", t7)


# ══════════════════════════════════════════════════════════════════════════════
# TEST 8: Safety — use-after-free detection
# ══════════════════════════════════════════════════════════════════════════════
def t8():
    fe     = LuminaFrontEndP5()
    nodes  = fe.parse_program("""
create a number called x with value 10
free x
show x
""")
    safety = run_safety(nodes)
    assert not safety.safe
    codes  = [e.code for e in safety.errors()]
    assert "E006" in codes

test("Safety: use-after-free correctly blocked with E006", t8)


# ══════════════════════════════════════════════════════════════════════════════
# TEST 9: Safety — duplicate definition
# ══════════════════════════════════════════════════════════════════════════════
def t9():
    fe     = LuminaFrontEndP5()
    nodes  = fe.parse_program("""
create a number called age with value 10
create a number called age with value 20
""")
    safety = run_safety(nodes)
    assert not safety.safe
    assert any(e.code == "E009" for e in safety.errors())

test("Safety: duplicate variable definition blocked with E009", t9)


# ══════════════════════════════════════════════════════════════════════════════
# TEST 10: LPM — install a built-in module
# ══════════════════════════════════════════════════════════════════════════════
def t10():
    import tempfile, shutil
    tmp_dir = tempfile.mkdtemp()
    try:
        lpm    = LuminaPackageManager(lib_dir=tmp_dir)
        ok, msg = lpm.install("math")
        assert ok, f"Install failed: {msg}"
        assert lpm._manifest.is_installed("math")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

test("LPM: install 'math' → manifest updated, is_installed() = True", t10)


# ══════════════════════════════════════════════════════════════════════════════
# TEST 11: LPM — install community module writes .lum file
# ══════════════════════════════════════════════════════════════════════════════
def t11():
    import tempfile, shutil
    from pathlib import Path
    tmp_dir = tempfile.mkdtemp()
    try:
        lpm    = LuminaPackageManager(lib_dir=tmp_dir)
        ok, msg = lpm.install("physics")
        assert ok, f"Install failed: {msg}"
        assert (Path(tmp_dir) / "physics.lum").exists()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

test("LPM: install 'physics' → physics.lum written to lib/ directory", t11)


# ══════════════════════════════════════════════════════════════════════════════
# TEST 12: LPM — search works
# ══════════════════════════════════════════════════════════════════════════════
def t12():
    lpm     = LuminaPackageManager()
    results = lpm.search("math")
    names   = [r["name"] for r in results]
    assert "math" in names

test("LPM: search('math') returns registry entry", t12)


# ══════════════════════════════════════════════════════════════════════════════
# TEST 13: Struct definition and field access
# ══════════════════════════════════════════════════════════════════════════════
def t13():
    ir = compile_and_get_ir("""
define Point as a thing with x_val (decimal) and y_val (decimal)
create a new Point called my_point
set the x_val of my_point to 3.0
set the y_val of my_point to 4.0
get the x_val of my_point
""")
    assert "%point = type" in ir.lower()
    assert "getelementptr" in ir
    assert "alloca %" in ir

test("Struct (Object Brain): %Point = type, alloca, GEP all in IR", t13)


# ══════════════════════════════════════════════════════════════════════════════
# TEST 14: TBAA metadata in IR
# ══════════════════════════════════════════════════════════════════════════════
def t14():
    ir = compile_and_get_ir("create a number called z with value 99")
    assert "TBAA" in ir or "tbaa" in ir

test("TBAA strict-aliasing metadata present in generated IR", t14)


# ══════════════════════════════════════════════════════════════════════════════
# TEST 15: CORE_FUNCTION_MAP coverage
# ══════════════════════════════════════════════════════════════════════════════
def t15():
    math_fns = CORE_FUNCTION_MAP.get("math", {})
    required = ["square root", "absolute value", "ceiling", "floor", "round", "log"]
    for fn in required:
        assert fn in math_fns, f"Missing: {fn}"

test("CORE_FUNCTION_MAP has all 6 required math functions", t15)


# ══════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════════════════════
passed = sum(1 for r in results if r[0] == PASS)
failed = sum(1 for r in results if r[0] == FAIL)
total  = len(results)

print(f"\n{'═'*66}")
print(f"  LUMINA PHASE 5 TEST RESULTS")
print(f"  Passed: {passed}/{total}   Failed: {failed}/{total}")
print(f"{'═'*66}")

if failed > 0:
    print("\n  Failed tests:")
    for mark, name in results:
        if mark == FAIL:
            print(f"    ✖  {name}")
    sys.exit(1)
else:
    print(f"\n  🎉  All {total} tests passed! Phase 5 is ready.")
    sys.exit(0)
