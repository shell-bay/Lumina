"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  LUMINA COMPILER v0.5.0 — safety_p5.py                                      ║
║  Phase 5: Module-aware safety checks, IMPORT_CALL validation                ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Optional, Dict, Set, List, Tuple

from compiler_p5 import (
    AITNode, LivenessInfo, StructDef, DiagnosticCollector,
    CORE_FUNCTION_MAP,
)


@dataclass
class SafetyViolation:
    level:      str
    message:    str
    hint:       str   = ""
    line_index: int   = -1
    code:       str   = "E000"


@dataclass
class SafetyReport:
    safe:       bool                     = True
    violations: List[SafetyViolation]    = field(default_factory=list)

    def add_error(self, msg, hint="", line=-1, code="E000"):
        self.safe = False
        self.violations.append(SafetyViolation("ERROR", msg, hint, line, code))

    def add_warning(self, msg, hint="", line=-1, code="W000"):
        self.violations.append(SafetyViolation("WARNING", msg, hint, line, code))

    def errors(self):
        return [v for v in self.violations if v.level == "ERROR"]

    def warnings(self):
        return [v for v in self.violations if v.level == "WARNING"]

    def to_dict(self) -> dict:
        return {
            "safe":     self.safe,
            "errors":   [{"code": v.code, "message": v.message, "hint": v.hint, "line": v.line_index}
                         for v in self.errors()],
            "warnings": [{"code": v.code, "message": v.message, "hint": v.hint, "line": v.line_index}
                         for v in self.warnings()],
        }


# ══════════════════════════════════════════════════════════════════════════════
# LIVENESS ANALYZER
# ══════════════════════════════════════════════════════════════════════════════

class LivenessAnalyzer:

    @staticmethod
    def _is_var(val: Optional[str]) -> bool:
        return bool(val and re.match(r'^[a-zA-Z_]\w*$', str(val)))

    @classmethod
    def _uses(cls, node: AITNode) -> Set[str]:
        used: Set[str] = set()
        def _add(v):
            if cls._is_var(v): used.add(v)
        intent = node.intent
        if intent == "PRINT":       _add(node.name)
        elif intent in ("FREE","BORROW","OWN"): _add(node.name)
        elif intent == "CALCULATE": _add(node.left); _add(node.right)
        elif intent == "IMPORT_CALL":
            for a in node.call_args: _add(a)
        elif node.is_mutation:      _add(node.name); _add(node.right)
        elif intent == "FIELD_SET": _add(node.name)
        elif intent == "FIELD_GET": _add(node.name)
        elif intent in ("IF_BLOCK","LOOP_BLOCK"):
            _add(node.cond_left); _add(node.cond_right)
            for child in node.body_nodes + node.else_nodes:
                used |= cls._uses(child)
        return used

    @classmethod
    def _defs(cls, node: AITNode) -> Set[str]:
        defined: Set[str] = set()
        intent = node.intent
        if intent in ("CREATE_VAR","STRUCT_NEW") and node.name:
            defined.add(node.name)
        elif intent in ("CALCULATE","IMPORT_CALL") and node.name:
            defined.add(node.name)
        elif node.is_mutation and node.name:
            defined.add(node.name)
        elif intent in ("IF_BLOCK","LOOP_BLOCK"):
            for child in node.body_nodes + node.else_nodes:
                defined |= cls._defs(child)
        return defined

    @classmethod
    def analyze(cls, nodes: List[AITNode]) -> None:
        n = len(nodes)
        if n == 0: return
        for node in nodes:
            node.liveness.used    = cls._uses(node)
            node.liveness.defined = cls._defs(node)
            node.liveness.live_before = set()
            node.liveness.live_after  = set()
        live_out: Set[str] = set()
        for i in range(n - 1, -1, -1):
            nd = nodes[i]
            nd.liveness.live_after  = set(live_out)
            nd.liveness.live_before = nd.liveness.used | (live_out - nd.liveness.defined)
            live_out = set(nd.liveness.live_before)


# ══════════════════════════════════════════════════════════════════════════════
# BORROW CHECKER  (Phase 5 — module aware)
# ══════════════════════════════════════════════════════════════════════════════

class BorrowChecker:

    def __init__(self, struct_registry: Optional[Dict[str, StructDef]] = None,
                 loaded_modules: Optional[Dict[str, str]] = None):
        self.defined_vars:    Set[str]               = set()
        self.freed_vars:      Set[str]               = set()
        self.heap_vars:       Set[str]               = set()
        self.owned_vars:      Dict[str, str]         = {}
        self.struct_vars:     Dict[str, StructDef]   = {}
        self.field_lifetimes: Dict[Tuple[str,str],bool] = {}
        self.struct_registry = struct_registry or {}
        # Phase 5: track which modules are imported (for IMPORT_CALL validation)
        self.loaded_modules: Dict[str, str] = loaded_modules or {}
        self.report = SafetyReport()

    def _err(self, code, msg, hint="", line=-1):
        self.report.add_error(msg, hint, line, code)

    def _warn(self, code, msg, hint="", line=-1):
        self.report.add_warning(msg, hint, line, code)

    def _check_ref(self, varname: str, ctx: str, line: int):
        if re.match(r'^-?\d+(?:\.\d+)?$', str(varname)): return
        if varname in self.freed_vars:
            code = "E007" if varname in self.heap_vars else "E006"
            kind = "Heap memory violation" if code == "E007" else "Use-after-free"
            self._err(code,
                f"⛔ {kind.upper()} — '{varname}' accessed after free in '{ctx}'",
                f"Fix: create a new variable with the value you need.", line)
        elif varname not in self.defined_vars:
            self._err("E005",
                f"⛔ SAFETY — '{varname}' used before creation in '{ctx}'",
                f"Fix: create a number called {varname} with value 0", line)

    def check_node(self, node: AITNode, line: int = -1):
        intent = node.intent

        if intent == "MODULE_USE":
            # Register module as loaded
            if node.module_name:
                self.loaded_modules[node.module_name] = node.module_path or "<builtin>"
            return

        elif intent == "IMPORT_CALL":
            # Phase 5: Verify the function exists and a module is loaded
            func = (node.call_fn or "").lower().strip()
            found_mod = False
            for mod in self.loaded_modules:
                if mod in CORE_FUNCTION_MAP:
                    for phrase, sym in CORE_FUNCTION_MAP[mod].items():
                        if phrase in func or func in phrase or sym == func:
                            found_mod = True
                            break
            if not found_mod and func and not func.startswith("__lum_"):
                self._warn("W006",
                    f"Function '{func}' used but no math/time/random module imported.",
                    f"Add:  use the math library  before this line.", line)
            # Check arguments exist
            for arg in (node.call_args or []):
                if re.match(r'^[a-zA-Z_]\w*$', arg):
                    self._check_ref(arg, f"call {func}", line)
            # Register result
            if node.name:
                self.defined_vars.add(node.name)
                self.owned_vars[node.name] = "main"
            return

        elif intent == "CREATE_VAR":
            if node.name in self.defined_vars:
                self._err("E009",
                    f"⛔ Duplicate definition of '{node.name}'",
                    f"Free '{node.name}' first, or choose a different name.", line)
            else:
                if node.name in self.freed_vars:
                    self._warn("W002", f"Re-creating '{node.name}' after free.", "", line)
                    self.freed_vars.discard(node.name)
                if node.name:
                    self.defined_vars.add(node.name)
                    self.owned_vars[node.name] = "main"
                    if node.is_heap or node.llvm_type == "i8*":
                        self.heap_vars.add(node.name)

        elif intent == "STRUCT_DEF":
            if node.struct_def:
                self.struct_registry[node.struct_def.name] = node.struct_def

        elif intent == "STRUCT_NEW":
            if node.struct_type and node.struct_type not in self.struct_registry:
                self._err("E005",
                    f"⛔ Struct type '{node.struct_type}' is not defined",
                    f"Define it first: define {node.struct_type} as a thing with ...", line)
            else:
                if node.name:
                    self.defined_vars.add(node.name)
                    self.owned_vars[node.name] = "main"
                    if node.struct_type:
                        sdef = self.struct_registry.get(node.struct_type)
                        if sdef:
                            self.struct_vars[node.name] = sdef

        elif intent == "FIELD_SET":
            if node.name: self._check_ref(node.name, "field set", line)

        elif intent == "FIELD_GET":
            if node.name: self._check_ref(node.name, "field get", line)

        elif intent == "CALCULATE":
            for ref in [node.left, node.right]:
                if ref: self._check_ref(ref, f"calculate {node.name}", line)
            if node.name:
                self.defined_vars.add(node.name)
                self.owned_vars[node.name] = "main"

        elif node.is_mutation:
            if node.name: self._check_ref(node.name, f"{node.intent}({node.name})", line)
            if node.right and re.match(r'^[a-zA-Z_]\w*$', str(node.right)):
                self._check_ref(node.right, f"{node.intent} delta", line)

        elif intent == "PRINT":
            if node.name: self._check_ref(node.name, "show/print", line)

        elif intent == "PRINT_LITERAL":
            pass

        elif intent == "BORROW":
            if node.name: self._check_ref(node.name, "borrow", line)

        elif intent == "OWN":
            if node.name and node.name in self.defined_vars:
                self.defined_vars.discard(node.name)
                self.freed_vars.add(node.name)

        elif intent == "FREE":
            if not node.name: return
            if node.name in self.freed_vars:
                self._err("E008",
                    f"⛔ Double-Free of '{node.name}'",
                    "Freeing a value twice is a critical memory error.", line)
            elif node.name not in self.defined_vars:
                self._err("E005",
                    f"⛔ Free of undefined '{node.name}'",
                    f"'{node.name}' was never created.", line)
            else:
                self.defined_vars.discard(node.name)
                self.freed_vars.add(node.name)
                self.owned_vars.pop(node.name, None)
                self.struct_vars.pop(node.name, None)

        elif intent == "IF_BLOCK":
            if node.cond_left: self._check_ref(node.cond_left, "if-condition", line)
            if node.cond_right and re.match(r'^[a-zA-Z_]\w*$', str(node.cond_right)):
                self._check_ref(node.cond_right, "if-condition-right", line)
            for child in node.body_nodes: self.check_node(child, line)
            for child in node.else_nodes: self.check_node(child, line)

        elif intent == "LOOP_BLOCK":
            if node.cond_left: self._check_ref(node.cond_left, "loop-condition", line)
            if node.cond_right and re.match(r'^[a-zA-Z_]\w*$', str(node.cond_right)):
                self._check_ref(node.cond_right, "loop-condition-right", line)
            for child in node.body_nodes: self.check_node(child, line)

    def check_dead_variables(self, nodes: List[AITNode]):
        all_used: Set[str] = set()
        for node in nodes: all_used |= node.liveness.used
        for node in nodes:
            if node.intent == "CREATE_VAR" and node.name:
                if node.name not in all_used:
                    self._warn("W001",
                        f"Variable '{node.name}' is created but never used.",
                        f"Consider removing it or add 'show {node.name}'")

    def check_program(self, nodes: List[AITNode]) -> SafetyReport:
        LivenessAnalyzer.analyze(nodes)
        self.check_dead_variables(nodes)
        for i, node in enumerate(nodes):
            self.check_node(node, line=node.source_line if node.source_line >= 0 else i)
        return self.report


def run_safety(nodes: List[AITNode],
               struct_registry: Optional[Dict] = None,
               loaded_modules: Optional[Dict] = None) -> SafetyReport:
    checker = BorrowChecker(struct_registry=struct_registry,
                            loaded_modules=loaded_modules)
    return checker.check_program(nodes)
