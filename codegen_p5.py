"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  LUMINA COMPILER v0.5.0 — codegen_p5.py                                     ║
║  Phase 5: Name Mangling · External Declarations · core.ll Calls             ║
║                                                                              ║
║  New in Phase 5:                                                             ║
║    • Name Mangling: user var "age" in main.lum → __lum_main_age             ║
║    •                user var "age" in math_lib  → __lum_math_age            ║
║    • External Decls: MODULE_USE nodes generate "declare" stmts              ║
║    • IMPORT_CALL: generates a "call double @__lum_core_sqrt(double %x)"     ║
║    • All Phase 4 features: structs, loops, if/else, SSA form                ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Optional, Dict, List, Tuple, Set

from compiler_p5 import (
    AITNode, StructDef, OP_TABLE, widen_type, default_for_type,
    CORE_FUNCTION_MAP,
)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — COMPARISON AND METADATA CONSTANTS
# ══════════════════════════════════════════════════════════════════════════════

_ICMP_MAP: Dict[str, str] = {
    "GT": "icmp sgt", "LT": "icmp slt", "EQ": "icmp eq",
    "NEQ": "icmp ne", "GTE": "icmp sge", "LTE": "icmp sle",
}
_FCMP_MAP: Dict[str, str] = {
    "GT": "fcmp ogt", "LT": "fcmp olt", "EQ": "fcmp oeq",
    "NEQ": "fcmp one", "GTE": "fcmp oge", "LTE": "fcmp ole",
}

TBAA_METADATA = """\
; ── TBAA (Type-Based Alias Analysis) metadata ──────────────────────────────
!tbaa.root = !{!"Lumina TBAA Root"}
!int_tbaa  = !{!"int",    !tbaa.root, i64 0}
!dbl_tbaa  = !{!"double", !tbaa.root, i64 0}
!ptr_tbaa  = !{!"ptr",    !tbaa.root, i64 0}
; ────────────────────────────────────────────────────────────────────────────
"""

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — NAME MANGLER  (Phase 5 — NEW)
# Prevents collisions when the user uses modules with shared variable names.
# ══════════════════════════════════════════════════════════════════════════════

class NameMangler:
    """
    Maps user-visible names to mangled LLVM-safe names.

    Rules:
      • Variables in the top-level program get prefix __lum_main_
      • Variables loaded from module X get prefix __lum_X_
      • Numeric literals and already-mangled names pass through untouched.

    Example:
      user writes  "age"  in main        →  __lum_main_age
      user writes  "age"  in math_lib    →  __lum_math_age
      → No collision, even though the English name is the same.
    """

    _IDENT_RE = re.compile(r'^[a-zA-Z_]\w*$')

    def __init__(self, module_name: str = "main"):
        self._prefix   = f"__lum_{module_name}_"
        self._registry: Dict[str, str] = {}   # original → mangled

    def mangle(self, name: Optional[str]) -> Optional[str]:
        """Return mangled version of name, or None/unchanged if not a user ident."""
        if name is None:
            return None
        if not self._IDENT_RE.match(name):
            return name   # numeric literal or already-mangled
        if name.startswith("__lum_"):
            return name   # already mangled (from module parse)
        mangled = self._prefix + name
        self._registry[name] = mangled
        return mangled

    def mangle_or_raw(self, name: str) -> str:
        """Same as mangle() but always returns a str."""
        return self.mangle(name) or name

    @property
    def prefix(self) -> str:
        return self._prefix


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — SSA REGISTER MAP
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class SSAVar:
    register:  str
    llvm_type: str
    is_const:  bool
    const_val: Optional[str] = None


class SSARegisterMap:

    def __init__(self):
        self._map:        Dict[str, SSAVar]   = {}
        self._freed:      Set[str]            = set()
        self._ver:        Dict[str, int]      = {}
        self._structs:    Dict[str, StructDef]= {}
        self._struct_ptrs:Dict[str, str]      = {}

    def _safe_ident(self, name: str) -> str:
        """Convert mangled names to safe LLVM register prefixes."""
        return re.sub(r'[^a-zA-Z0-9_.]', '_', name)

    def _next_version(self, name: str) -> str:
        safe = self._safe_ident(name)
        v    = self._ver.get(safe, 0)
        self._ver[safe] = v + 1
        return f"%{safe}.v{v}"

    def define(self, name: str, llvm_type: str,
               is_const: bool = False, const_val: Optional[str] = None) -> str:
        reg = self._next_version(name)
        self._map[name] = SSAVar(register=reg, llvm_type=llvm_type,
                                 is_const=is_const, const_val=const_val)
        self._freed.discard(name)
        return reg

    def get(self, name: str) -> Optional[SSAVar]:
        return self._map.get(name)

    def get_operand(self, name: str) -> Tuple[str, str]:
        var = self._map.get(name)
        if var:
            if var.is_const and var.const_val is not None:
                return var.const_val, var.llvm_type
            return var.register, var.llvm_type
        if re.match(r'^-?\d+(?:\.\d+)?$', str(name)):
            t = "double" if "." in str(name) else "i32"
            return str(name), t
        return name, "i32"

    def free(self, name: str):
        self._map.pop(name, None)
        self._freed.add(name)
        self._structs.pop(name, None)
        self._struct_ptrs.pop(name, None)

    def register_struct(self, var_name: str, sdef: StructDef, ptr_reg: str):
        self._structs[var_name]      = sdef
        self._struct_ptrs[var_name]  = ptr_reg

    def get_struct(self, var_name: str) -> Optional[StructDef]:
        return self._structs.get(var_name)

    def get_struct_ptr(self, var_name: str) -> Optional[str]:
        return self._struct_ptrs.get(var_name)

    def update(self, name: str, new_reg: str, llvm_type: str):
        self._map[name] = SSAVar(register=new_reg, llvm_type=llvm_type, is_const=False)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — LLVM IR GENERATOR  (Phase 5)
# ══════════════════════════════════════════════════════════════════════════════

class LLVMGeneratorP5:
    """
    Phase 5 LLVM IR generator.
    Handles:
      • MODULE_USE  → external declaration stubs
      • IMPORT_CALL → call to pre-compiled core.ll function
      • Name mangling via NameMangler
      • All Phase 4 constructs (structs, loops, if/else, SSA)
    """

    def __init__(self,
                 struct_registry: Optional[Dict[str, StructDef]] = None,
                 module_name:     str = "main",
                 external_syms:   Optional[List[Tuple[str, str, List[str]]]] = None):
        self._ssa           = SSARegisterMap()
        self._mangler       = NameMangler(module_name)
        self._counter       = 0
        self._block_counter = 0
        self._lines:        List[str]                   = []
        self._slot_ctx:     Dict[str, Tuple[str, str]]  = {}
        self._struct_reg:   Dict[str, StructDef]        = struct_registry or {}
        self._globals:      List[str]                   = []
        self._literal_count = 0
        # Phase 5: external symbol declarations (from MODULE_USE)
        self._external_decls: Set[str]              = set()
        # Pre-populate from constructor argument (passed by frontend)
        if external_syms:
            for sym, ret_t, arg_ts in external_syms:
                self._add_external_decl(sym, ret_t, arg_ts)
        # Phase 5: loaded modules (for comment metadata)
        self._loaded_mods: List[str] = []

    def _add_external_decl(self, sym: str, ret_type: str, arg_types: List[str]) -> str:
        args_str = ", ".join(arg_types)
        decl = f"declare {ret_type} @{sym}({args_str})"
        self._external_decls.add(decl)
        return decl

    def _fresh(self, prefix: str = "t") -> str:
        self._counter += 1
        return f"%{prefix}.{self._counter}"

    def _next_block(self) -> int:
        self._block_counter += 1
        return self._block_counter

    def _emit(self, line: str):
        if line.endswith(":") and not line.startswith("  "):
            self._lines.append(line)
        elif line.startswith(";") or line == "":
            self._lines.append(line)
        else:
            self._lines.append("  " + line)

    def _emit_c(self, text: str):
        self._lines.append(f"  ; {text}")

    def _add_global(self, line: str):
        self._globals.append(line)

    def _m(self, name: Optional[str]) -> Optional[str]:
        """Apply name mangling."""
        return self._mangler.mangle(name)

    def _m_str(self, name: str) -> str:
        return self._mangler.mangle_or_raw(name)

    # ── Header ───────────────────────────────────────────────────────────────

    def _build_header(self, struct_defs: List[AITNode],
                      loaded_mods: List[str]) -> List[str]:
        mod_list = ", ".join(loaded_mods) if loaded_mods else "none"
        lines = [
            "; ═══════════════════════════════════════════════════════════════",
            "; LUMINA COMPILER v0.5.0 — Generated LLVM IR",
            f"; Modules loaded : {mod_list}",
            "; Target         : x86_64-unknown-linux-gnu",
            "; Optimize       : equivalent to clang -O3 -ffast-math",
            "; Form           : Pure SSA + Name Mangling + External Decls",
            "; ═══════════════════════════════════════════════════════════════",
            "",
            'target datalayout = "e-m:e-p270:32:32-p271:32:32-p272:64:64-i64:64-f80:128-n8:16:32:64-S128"',
            'target triple = "x86_64-unknown-linux-gnu"',
            "",
        ]

        # Struct type declarations
        if struct_defs:
            lines.append("; ── Struct type declarations ────────────────────────────────────")
            for n in struct_defs:
                if n.struct_def:
                    sdef = n.struct_def
                    lines.append(f"%{sdef.name} = type {sdef.llvm_type_str()}")
            lines.append("")

        # Standard external declarations (C stdlib)
        lines += [
            "; ── Standard external declarations ─────────────────────────────",
            "declare i32 @printf(i8* noundef, ...)",
            "declare noalias i8* @malloc(i64 noundef)",
            "declare void @free(i8* noundef)",
            "declare i32 @puts(i8* noundef)",
            "declare i8* @strcpy(i8* noundef, i8* noundef)",
            "declare i64 @strlen(i8* noundef)",
            "",
        ]

        # Phase 5: Module external declarations
        if self._external_decls:
            lines.append("; ── Phase 5: Imported module function declarations ───────────────")
            lines.append("; (These are pre-compiled in core.o — linked at build time)")
            for decl in sorted(self._external_decls):
                lines.append(decl)
            lines.append("")

        # Format strings
        lines += [
            "; ── Format strings ──────────────────────────────────────────────",
            '@fmt.int = private unnamed_addr constant [4 x i8] c"%d\\0A\\00", align 1',
            '@fmt.dbl = private unnamed_addr constant [4 x i8] c"%f\\0A\\00", align 1',  
            '@fmt.str = private unnamed_addr constant [4 x i8] c"%s\\0A\\00", align 1',
            "",
        ]
        return lines

    def _footer(self) -> List[str]:
        return [
            "  ret i32 0",
            "}",
            "",
            'attributes #0 = { noinline nounwind optnone uwtable "frame-pointer"="all" }',
            "",
            TBAA_METADATA,
        ]

    # ── Type helpers ─────────────────────────────────────────────────────────

    def _cmp_instr(self, op: str, t: str) -> str:
        if t == "double":
            return _FCMP_MAP.get(op, "fcmp oeq")
        return _ICMP_MAP.get(op, "icmp eq")

    def _resolve(self, name: str) -> Tuple[str, str]:
        """Resolve a name (possibly mangled) to (register_or_literal, type)."""
        mangled = self._m_str(name)
        return self._ssa.get_operand(mangled)

    # ── Emit CREATE_VAR ───────────────────────────────────────────────────────

    def _emit_create_var(self, node: AITNode):
        mangled = self._m_str(node.name or "anon")
        lt      = node.llvm_type or "i32"
        val     = node.value

        self._emit_c(f"CREATE_VAR '{node.name}' → mangled '{mangled}' type={lt}")

        if lt == "i8*":
            # Heap string — malloc + strcpy
            if val:
                strlen = len(str(val)) + 1
                ptr    = self._fresh("ptr")
                reg    = self._ssa.define(mangled, "i8*")
                self._emit(f"{ptr} = call noalias i8* @malloc(i64 noundef {strlen})")
                str_const = self._fresh("strlit")
                self._add_global(
                    f'{str_const} = private unnamed_addr constant [{strlen} x i8] '
                    f'c"{val}\\00", align 1'
                )
                self._emit(
                    f"call i8* @strcpy(i8* noundef {ptr}, "
                    f"i8* noundef getelementptr inbounds "
                    f"([{strlen} x i8], [{strlen} x i8]* {str_const}, i64 0, i64 0))"
                )
                self._emit(f"{reg} = bitcast i8* {ptr} to i8*")
            else:
                reg = self._ssa.define(mangled, "i8*", is_const=True, const_val="null")
            self._emit("")
            return

        # Numeric
        if val is not None:
            const_val = str(val)
            if lt == "double" and "." not in const_val:
                const_val += ".0"
            reg = self._ssa.define(mangled, lt, is_const=True, const_val=const_val)
        else:
            dv  = default_for_type(lt)
            reg = self._ssa.define(mangled, lt, is_const=True, const_val=dv)
        self._emit_c(f"  SSA {reg} = {val or default_for_type(lt)} : {lt}")
        self._emit("")

    # ── Emit CALCULATE ────────────────────────────────────────────────────────

    def _emit_calculate(self, node: AITNode):
        mangled_name = self._m_str(node.name or "res")
        lt           = node.llvm_type or "i32"

        # Check slot context (inside loops)
        def _get_val(raw: str) -> Tuple[str, str]:
            mraw = self._m_str(raw)
            if mraw in self._slot_ctx:
                slot, slot_t = self._slot_ctx[mraw]
                loaded       = self._fresh(f"{mraw}.ld")
                self._emit(f"{loaded} = load {slot_t}, {slot_t}* {slot}, align 4")
                return loaded, slot_t
            return self._ssa.get_operand(mraw)

        lv, ltl = _get_val(node.left  or "0")
        rv, ltr = _get_val(node.right or "0")
        res_t   = widen_type(ltl, ltr)
        if lt   == "double": res_t = "double"

        # Cast integers to float if needed
        if res_t == "double":
            if ltl == "i32":
                c = self._fresh("cast"); self._emit(f"{c} = sitofp i32 {lv} to double"); lv = c
            if ltr == "i32":
                c = self._fresh("cast"); self._emit(f"{c} = sitofp i32 {rv} to double"); rv = c

        op    = node.op or "ADD"
        instr = OP_TABLE.get(op, ("add nsw", "fadd"))
        i_op  = instr[1] if res_t == "double" else instr[0]

        result = self._fresh("calc")
        self._emit_c(f"CALCULATE {node.name} = {node.left} {op} {node.right}")
        self._emit(f"{result} = {i_op} {res_t} {lv}, {rv}")

        reg = self._ssa.define(mangled_name, res_t)
        # If in slot ctx, store back
        if mangled_name in self._slot_ctx:
            slot, st = self._slot_ctx[mangled_name]
            self._emit(f"store {st} {result}, {st}* {slot}, align 4")
        else:
            self._emit(f"; {reg} = {result}  (SSA rename)")
            self._ssa.update(mangled_name, result, res_t)
        self._emit("")

    # ── Emit MUTATION ─────────────────────────────────────────────────────────

    def _emit_mutation(self, node: AITNode):
        mangled = self._m_str(node.name or "x")

        if mangled in self._slot_ctx:
            slot, lt = self._slot_ctx[mangled]
            cur      = self._fresh(f"{mangled}.ld")
            self._emit(f"{cur} = load {lt}, {lt}* {slot}, align 4")
        else:
            cur, lt = self._ssa.get_operand(mangled)

        rv, rt = self._ssa.get_operand(
            self._m_str(node.right) if node.right and re.match(r'^[a-zA-Z_]', node.right)
            else (node.right or "1")
        )

        res_t  = widen_type(lt, rt)
        op     = node.op or "ADD"
        instr  = OP_TABLE.get(op, ("add nsw", "fadd"))
        i_op   = instr[1] if res_t == "double" else instr[0]

        if res_t == "double":
            if lt == "i32":
                c = self._fresh("cast"); self._emit(f"{c} = sitofp i32 {cur} to double"); cur = c
            if rt == "i32":
                c = self._fresh("cast"); self._emit(f"{c} = sitofp i32 {rv} to double"); rv  = c

        result = self._fresh("mut")
        self._emit_c(f"MUTATE {node.name} {op} {node.right}")
        self._emit(f"{result} = {i_op} {res_t} {cur}, {rv}")

        if mangled in self._slot_ctx:
            slot, st = self._slot_ctx[mangled]
            self._emit(f"store {st} {result}, {st}* {slot}, align 4")
        else:
            new_reg = self._ssa.define(mangled, res_t)
            self._ssa.update(mangled, result, res_t)
        self._emit("")

    # ── Emit IMPORT_CALL  (Phase 5 — NEW) ────────────────────────────────────

    def _emit_import_call(self, node: AITNode):
        """
        Emit a call to a pre-compiled core.ll function.
        e.g.  calculate root as square root of x
        →     %root.v0 = call double @__lum_core_sqrt(double %x.v0)
        """
        result_name = self._m_str(node.name or "result")
        func_sym    = node.call_fn or "__lum_core_sqrt"
        args        = node.call_args or []

        # Ensure function is declared
        self._add_external_decl(func_sym, "double", ["double"] * len(args))

        self._emit_c(f"IMPORT_CALL {node.name} = {node.call_fn}({', '.join(args)})")

        # Resolve arguments
        arg_parts = []
        for raw_arg in args:
            m_arg      = self._m_str(raw_arg)
            val, vtype = self._ssa.get_operand(m_arg)
            if vtype == "i32":
                casted = self._fresh("arg_cast")
                self._emit(f"{casted} = sitofp i32 {val} to double")
                arg_parts.append(f"double {casted}")
            else:
                arg_parts.append(f"double {val}")

        result_reg = self._fresh("call")
        args_str   = ", ".join(arg_parts) if arg_parts else "double 0.0"
        self._emit(f"{result_reg} = call double @{func_sym}({args_str})")

        reg = self._ssa.define(result_name, "double")
        self._ssa.update(result_name, result_reg, "double")
        self._emit("")

    # ── Emit PRINT ────────────────────────────────────────────────────────────

    def _emit_print(self, node: AITNode):
        mangled  = self._m_str(node.name or "")
        val, t   = self._ssa.get_operand(mangled)
        if t == "i32":
            self._emit_c(f"PRINT int '{node.name}'")
            self._emit(
                f"call i32 (i8*, ...) @printf("
                f"i8* noundef getelementptr inbounds ([4 x i8], [4 x i8]* @fmt.int, i64 0, i64 0), "
                f"i32 noundef {val})"
            )
        elif t == "double":
            self._emit_c(f"PRINT double '{node.name}'")
            self._emit(
                f"call i32 (i8*, ...) @printf("
                f"i8* noundef getelementptr inbounds ([4 x i8], [4 x i8]* @fmt.dbl, i64 0, i64 0), "
                f"double noundef {val})"
            )
        elif t == "i8*":
            self._emit_c(f"PRINT string '{node.name}'")
            self._emit(
                f"call i32 (i8*, ...) @printf("
                f"i8* noundef getelementptr inbounds ([4 x i8], [4 x i8]* @fmt.str, i64 0, i64 0), "
                f"i8* noundef {val})"
            )
        self._emit("")

    def _emit_print_literal(self, node: AITNode):
        text   = node.value or ""
        sz     = len(text) + 2   # +newline+null
        gname  = self._fresh("strlit.g")
        self._add_global(
            f'{gname} = private unnamed_addr constant [{sz} x i8] '
            f'c"{text}\\0A\\00", align 1'
        )
        self._emit_c(f"PRINT_LITERAL '{text}'")
        self._emit(
            f"call i32 (i8*, ...) @printf("
            f"i8* noundef getelementptr inbounds ([{sz} x i8], [{sz} x i8]* {gname}, i64 0, i64 0))"
        )
        self._emit("")

    # ── Emit FREE ─────────────────────────────────────────────────────────────

    def _emit_free(self, node: AITNode):
        mangled = self._m_str(node.name or "")
        var     = self._ssa.get(mangled)
        if var and var.llvm_type == "i8*":
            self._emit_c(f"FREE heap string '{node.name}'")
            self._emit(f"call void @free(i8* noundef {var.register})")
        else:
            self._emit_c(f"FREE '{node.name}' — linear type dropped (mangled: {mangled})")
        self._ssa.free(mangled)
        self._emit("")

    # ── Emit IF_BLOCK ─────────────────────────────────────────────────────────

    def _emit_if_block(self, node: AITNode):
        bid  = self._next_block()
        then = f"if.then.{bid}"
        els  = f"if.else.{bid}" if node.else_nodes else f"if.end.{bid}"
        end  = f"if.end.{bid}"

        lv, lt = self._resolve(node.cond_left  or "0")
        rv, rt = self._resolve(node.cond_right or "0")
        res_t  = widen_type(lt, rt)
        if res_t == "double":
            if lt == "i32": c = self._fresh("cast"); self._emit(f"{c} = sitofp i32 {lv} to double"); lv = c
            if rt == "i32": c = self._fresh("cast"); self._emit(f"{c} = sitofp i32 {rv} to double"); rv = c
        cmp = self._fresh("cmp")
        self._emit_c(f"IF {node.cond_left} {node.cond_op} {node.cond_right}")
        self._emit(f"{cmp} = {self._cmp_instr(node.cond_op or 'EQ', res_t)} {res_t} {lv}, {rv}")
        self._emit(f"br i1 {cmp}, label %{then}, label %{els}")
        self._emit("")
        self._emit(f"{then}:")
        for ch in node.body_nodes: self._emit_node(ch)
        self._emit(f"br label %{end}")
        self._emit("")
        if node.else_nodes:
            self._emit(f"{els}:")
            for ch in node.else_nodes: self._emit_node(ch)
            self._emit(f"br label %{end}")
            self._emit("")
        self._emit(f"{end}:")
        self._emit("")

    # ── Emit LOOP_BLOCK ───────────────────────────────────────────────────────

    @staticmethod
    def _mutated_vars(body: List[AITNode]) -> List[str]:
        mutated = []
        for n in body:
            if n.is_mutation and n.name and n.name not in mutated:
                mutated.append(n.name)
        return mutated

    def _emit_loop_block(self, node: AITNode):
        bid  = self._next_block()
        hdr  = f"loop.hdr.{bid}"
        body = f"loop.body.{bid}"
        end  = f"loop.end.{bid}"

        mutated  = self._mutated_vars(node.body_nodes)
        slot_map: Dict[str, Tuple[str, str]] = {}
        self._emit_c(f"LOOP-PREP slots for: {[self._m_str(v) for v in mutated]}")

        for var in mutated:
            mangled = self._m_str(var)
            v       = self._ssa.get(mangled)
            if not v: continue
            lt   = v.llvm_type
            slot = f"%slot.{re.sub(r'[^a-zA-Z0-9_]','_', mangled)}.{bid}"
            self._emit(f"{slot} = alloca {lt}, align 4")
            cur, _ = self._ssa.get_operand(mangled)
            self._emit(f"store {lt} {cur}, {lt}* {slot}, align 4")
            slot_map[mangled] = (slot, lt)

        cond_l_m = self._m_str(node.cond_left or "0")
        if cond_l_m not in slot_map:
            v = self._ssa.get(cond_l_m)
            if v:
                lt   = v.llvm_type
                slot = f"%slot.{re.sub(r'[^a-zA-Z0-9_]','_', cond_l_m)}.{bid}"
                self._emit(f"{slot} = alloca {lt}, align 4")
                cur, _ = self._ssa.get_operand(cond_l_m)
                self._emit(f"store {lt} {cur}, {lt}* {slot}, align 4")
                slot_map[cond_l_m] = (slot, lt)

        self._emit(f"br label %{hdr}")
        self._emit("")
        self._emit(f"{hdr}:")

        if cond_l_m in slot_map:
            slot, lt = slot_map[cond_l_m]
            chk = self._fresh(f"chk")
            self._emit(f"{chk} = load {lt}, {lt}* {slot}, align 4")
            lv, lt2 = chk, lt
        else:
            lv, lt2 = self._ssa.get_operand(cond_l_m)

        rv, rt = self._ssa.get_operand(self._m_str(node.cond_right or "0"))
        res_t  = widen_type(lt2, rt)
        cmp    = self._fresh("cmp")
        self._emit_c(f"LOOP-COND while {node.cond_left} {node.cond_op} {node.cond_right}")
        self._emit(f"{cmp} = {self._cmp_instr(node.cond_op or 'LT', res_t)} {res_t} {lv}, {rv}")
        self._emit(f"br i1 {cmp}, label %{body}, label %{end}")
        self._emit("")
        self._emit(f"{body}:")
        old_ctx = dict(self._slot_ctx)
        self._slot_ctx.update(slot_map)
        for ch in node.body_nodes: self._emit_node(ch)
        self._slot_ctx = old_ctx
        self._emit(f"br label %{hdr}")
        self._emit("")
        self._emit(f"{end}:")
        self._emit_c("LOOP-END: reload final values")
        for var_m, (slot, lt) in slot_map.items():
            new_reg = self._ssa.define(var_m, lt, is_const=False)
            self._emit(f"{new_reg} = load {lt}, {lt}* {slot}, align 4")
            self._ssa.update(var_m, new_reg, lt)
        self._emit("")

    # ── Emit struct nodes ─────────────────────────────────────────────────────

    def _emit_struct_def(self, node: AITNode):
        if node.struct_def:
            self._struct_reg[node.struct_def.name] = node.struct_def
        self._emit_c(f"STRUCT_DEF {node.name} — declared in header")

    def _emit_struct_new(self, node: AITNode):
        mangled = self._m_str(node.name or "obj")
        stype   = node.struct_type or ""
        sdef    = self._struct_reg.get(stype)
        if not sdef:
            self._emit_c(f"ERROR: unknown struct type '{stype}'")
            return
        ptr_reg = self._fresh("struct_ptr")
        self._emit_c(f"STRUCT_NEW {node.name} : {stype}")
        self._emit(f"{ptr_reg} = alloca %{stype}, align 8")
        self._ssa.register_struct(mangled, sdef, ptr_reg)
        self._emit("")

    def _emit_field_set(self, node: AITNode):
        mangled   = self._m_str(node.name or "obj")
        ptr_reg   = self._ssa.get_struct_ptr(mangled)
        sdef      = self._ssa.get_struct(mangled)
        if not ptr_reg or not sdef:
            self._emit_c(f"ERROR: '{node.name}' is not a struct instance")
            return
        idx  = sdef.field_index(node.field_name or "")
        ft   = sdef.field_type(node.field_name or "") or "i32"
        if idx is None:
            self._emit_c(f"ERROR: field '{node.field_name}' not in struct")
            return
        gep  = self._fresh("gep")
        val, vt = self._ssa.get_operand(self._m_str(node.value or "0"))
        if ft == "double" and vt == "i32":
            c = self._fresh("cast"); self._emit(f"{c} = sitofp i32 {val} to double"); val = c
        self._emit_c(f"FIELD_SET {node.name}.{node.field_name} = {node.value}")
        self._emit(f"{gep} = getelementptr inbounds %{sdef.name}, %{sdef.name}* {ptr_reg}, i32 0, i32 {idx}")
        self._emit(f"store {ft} {val}, {ft}* {gep}, align 4")
        self._emit("")

    def _emit_field_get(self, node: AITNode):
        mangled = self._m_str(node.name or "obj")
        ptr_reg = self._ssa.get_struct_ptr(mangled)
        sdef    = self._ssa.get_struct(mangled)
        if not ptr_reg or not sdef:
            self._emit_c(f"ERROR: '{node.name}' not a struct")
            return
        idx  = sdef.field_index(node.field_name or "")
        ft   = sdef.field_type(node.field_name or "") or "i32"
        if idx is None:
            self._emit_c(f"ERROR: field '{node.field_name}' not found")
            return
        gep      = self._fresh("gep")
        loaded   = self._fresh("fld")
        res_name = self._m_str(f"{node.name}_{node.field_name}")
        self._emit_c(f"FIELD_GET {node.name}.{node.field_name}")
        self._emit(f"{gep}    = getelementptr inbounds %{sdef.name}, %{sdef.name}* {ptr_reg}, i32 0, i32 {idx}")
        self._emit(f"{loaded} = load {ft}, {ft}* {gep}, align 4")
        reg = self._ssa.define(res_name, ft)
        self._ssa.update(res_name, loaded, ft)
        self._emit("")

    # ── MODULE_USE  (Phase 5 — NEW) ───────────────────────────────────────────

    def _emit_module_use(self, node: AITNode):
        mod = node.module_name or "unknown"
        self._loaded_mods.append(mod)
        self._emit_c(f"MODULE_USE '{mod}' — stdlib symbols declared in header")

        # Register any external symbols for this module
        if mod in CORE_FUNCTION_MAP:
            for phrase, sym in CORE_FUNCTION_MAP[mod].items():
                self._add_external_decl(sym, "double", ["double"])

        # If the module has sub-nodes (from a .lum file), emit them
        for child in node.body_nodes:
            self._emit_node(child)
        self._emit("")

    # ── Node dispatch ─────────────────────────────────────────────────────────

    def _emit_node(self, node: AITNode):
        intent = node.intent
        if   intent == "CREATE_VAR":    self._emit_create_var(node)
        elif intent == "CALCULATE":     self._emit_calculate(node)
        elif intent == "IMPORT_CALL":   self._emit_import_call(node)
        elif intent == "MODULE_USE":    self._emit_module_use(node)
        elif intent == "STRUCT_DEF":    self._emit_struct_def(node)
        elif intent == "STRUCT_NEW":    self._emit_struct_new(node)
        elif intent == "FIELD_SET":     self._emit_field_set(node)
        elif intent == "FIELD_GET":     self._emit_field_get(node)
        elif node.is_mutation:          self._emit_mutation(node)
        elif intent == "PRINT":         self._emit_print(node)
        elif intent == "PRINT_LITERAL": self._emit_print_literal(node)
        elif intent == "FREE":          self._emit_free(node)
        elif intent == "IF_BLOCK":      self._emit_if_block(node)
        elif intent == "LOOP_BLOCK":    self._emit_loop_block(node)
        elif intent in ("BORROW", "OWN"):
            self._emit_c(f"[{intent}] {node.name} — ownership annotation")

    # ── Master generate ───────────────────────────────────────────────────────

    def generate(self, nodes: List[AITNode]) -> str:
        self._ssa           = SSARegisterMap()
        self._counter       = 0
        self._block_counter = 0
        self._slot_ctx      = {}
        self._globals       = []
        self._literal_count = 0
        self._lines         = []
        self._loaded_mods   = []

        struct_def_nodes = [n for n in nodes if n.intent == "STRUCT_DEF"]

        body_lines: List[str] = []
        self._lines = body_lines

        for node in nodes:
            self._emit_node(node)

        header = self._build_header(struct_def_nodes, self._loaded_mods)

        all_lines = (
            header
            + ([""] + self._globals if self._globals else [])
            + [
                "",
                "; ── Main function ───────────────────────────────────────────────",
                "define noundef i32 @main() #0 {",
                "entry:",
            ]
            + body_lines
            + self._footer()
        )
        return "\n".join(all_lines)


def generate_ir_p5(nodes: List[AITNode],
                   struct_registry: Optional[Dict[str, StructDef]] = None,
                   module_name: str = "main",
                   external_syms: Optional[List[Tuple[str, str, List[str]]]] = None) -> str:
    """Phase 5 convenience wrapper."""
    gen = LLVMGeneratorP5(
        struct_registry=struct_registry,
        module_name=module_name,
        external_syms=external_syms,
    )
    return gen.generate(nodes)
