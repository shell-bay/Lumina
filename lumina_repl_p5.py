"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  LUMINA REPL v0.5.0 — lumina_repl_p5.py                                     ║
║  Interactive English programming shell with Phase 5 module support          ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations
import sys
import json
from typing import List

from compiler_p5 import LuminaFrontEndP5, AITNode
from codegen_p5  import LLVMGeneratorP5
from safety_p5   import run_safety

REPL_BANNER = """
╔══════════════════════════════════════════════════════════════════════╗
║   🌟  LUMINA v0.5.0  — Phase 5: Package Manager + Stealth Binary    ║
╠══════════════════════════════════════════════════════════════════════╣
║  Commands:                                                           ║
║    run   → compile & show generated LLVM IR                         ║
║    clear → reset program buffer                                      ║
║    list  → show current program lines                               ║
║    mods  → show loaded modules                                      ║
║    quit  → exit                                                      ║
╠══════════════════════════════════════════════════════════════════════╣
║  Phase 5 Examples:                                                   ║
║    use the math library                                              ║
║    create a decimal called x with value 16.0                        ║
║    calculate root as square root of x                               ║
║    show root                                                         ║
║    use the physics library                                           ║
╚══════════════════════════════════════════════════════════════════════╝
"""

_class_LuminaEncoder = type("_LuminaEncoder", (json.JSONEncoder,), {
    "default": lambda self, obj: sorted(obj) if isinstance(obj, set) else super().default(obj)
})


def run_repl():
    print(REPL_BANNER)
    program:  List[str] = []
    fe        = LuminaFrontEndP5()

    while True:
        try:
            line = input("lumina❯ ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\nGoodbye! Keep building Lumina 🚀")
            break

        cmd = line.lower()

        if cmd == "quit":
            print("Goodbye! Keep building Lumina 🚀")
            break

        elif cmd == "run":
            if not program:
                print("  (Nothing to compile yet)")
                continue
            src    = "\n".join(program)
            fe     = LuminaFrontEndP5()
            nodes  = fe.parse_program(src)

            fe.diagnostics.print_all()
            if fe.diagnostics.has_errors():
                print("  ✖  Parse errors — fix them and run again.")
                continue

            safety = run_safety(nodes, fe.struct_registry, fe.loaded_modules)
            if not safety.safe:
                print("\n  ⛔  Safety violations:")
                for e in safety.errors():
                    print(f"     [{e.code}] {e.message}")
                    if e.hint: print(f"            → {e.hint}")
                print("\n  IR generation BLOCKED — fix violations above.")
                continue

            gen = LLVMGeneratorP5(
                struct_registry=fe.struct_registry,
                module_name="main",
                external_syms=fe.external_symbols,
            )
            ir = gen.generate(nodes)

            print("\n" + "═"*66)
            print("  LUMINA v0.5.0 — Generated LLVM IR")
            print("═"*66)
            print(ir)
            print("═"*66)

            # Write IR to output
            try:
                out_path = "/mnt/user-data/outputs/lumina_output.ll"
                with open(out_path, "w") as f:
                    f.write(ir)
                print(f"\n  📄  IR saved → {out_path}")
            except Exception:
                pass

            program = []
            fe      = LuminaFrontEndP5()

        elif cmd == "clear":
            program = []
            fe      = LuminaFrontEndP5()
            print("  (Buffer cleared)")

        elif cmd == "list":
            if program:
                print("\n  ── Current program ──")
                for i, ln in enumerate(program, 1):
                    print(f"   {i:3d}: {ln}")
            else:
                print("  (Empty)")

        elif cmd == "mods":
            if fe.loaded_modules:
                print("\n  ── Loaded modules ──")
                for mod, path in fe.loaded_modules.items():
                    print(f"   {mod:15s} → {path}")
            else:
                print("  (No modules loaded yet — try: use the math library)")

        elif line:
            program.append(line)
            print(f"  📝  Line {len(program)} added")


if __name__ == "__main__":
    run_repl()
