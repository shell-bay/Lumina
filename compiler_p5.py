"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  LUMINA COMPILER v0.5.0 — compiler_p5.py                                    ║
║  Phase 5: Stealth Package Manager · Module System · Name Mangling            ║
║                                                                              ║
║  New in Phase 5 vs Phase 4:                                                  ║
║    • USE keyword: "use the math library"  → MODULE_USE AITNode              ║
║    • Module Resolver: searches /lib folder for <name>.lum                   ║
║    • Name mangling: user vars → __lum_<module>_<name>                       ║
║    • IMPORT_CALL node: "calculate sqrt of x" maps to core.ll symbol         ║
║    • All Phase 4 features retained (structs, loops, if/else)                ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations
import re
import sys
import os
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Set, Tuple, Any
from difflib import get_close_matches
from pathlib import Path

try:
    from lark import Lark, Transformer, Tree, Token, UnexpectedInput, GrammarError
    from lark.exceptions import UnexpectedToken, UnexpectedCharacters, VisitError
    LARK_AVAILABLE = True
except ImportError:
    LARK_AVAILABLE = False
    print("[Lumina] WARNING: lark not installed. Run: pip install lark")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — AIT NODE DATA STRUCTURES  (Phase 5 extended)
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class LivenessInfo:
    live_before: Set[str] = field(default_factory=set)
    live_after:  Set[str] = field(default_factory=set)
    defined:     Set[str] = field(default_factory=set)
    used:        Set[str] = field(default_factory=set)


@dataclass
class StructField:
    name:      str
    llvm_type: str
    index:     int


@dataclass
class StructDef:
    name:   str
    fields: List[StructField] = field(default_factory=list)

    def llvm_type_str(self) -> str:
        parts = ", ".join(f.llvm_type for f in self.fields)
        return "{ " + parts + " }"

    def field_index(self, fname: str) -> Optional[int]:
        for f in self.fields:
            if f.name == fname:
                return f.index
        return None

    def field_type(self, fname: str) -> Optional[str]:
        for f in self.fields:
            if f.name == fname:
                return f.llvm_type
        return None


@dataclass
class AITNode:
    """
    One node in the Abstract Intent Tree.

    Phase 5 additions:
        module_name   — name of the module being imported (MODULE_USE nodes)
        module_path   — resolved filesystem path to the .lum file
        mangle_prefix — e.g. "__lum_math_" prepended to all names in that module
        call_fn       — for IMPORT_CALL: the core.ll function symbol name
        call_args     — for IMPORT_CALL: list of argument variable names
    """
    intent:       str
    name:         Optional[str]       = None
    value:        Optional[str]       = None
    llvm_type:    Optional[str]       = None
    op:           Optional[str]       = None
    left:         Optional[str]       = None
    right:        Optional[str]       = None
    is_mutation:  bool                = False
    is_heap:      bool                = False
    # Control flow
    cond_left:    Optional[str]       = None
    cond_op:      Optional[str]       = None
    cond_right:   Optional[str]       = None
    body_nodes:   List["AITNode"]     = field(default_factory=list)
    else_nodes:   List["AITNode"]     = field(default_factory=list)
    # Phase 4: Object Brain
    struct_type:  Optional[str]       = None
    field_name:   Optional[str]       = None
    struct_def:   Optional[StructDef] = None
    # Phase 5: Module system
    module_name:  Optional[str]       = None
    module_path:  Optional[str]       = None
    mangle_prefix:Optional[str]       = None
    call_fn:      Optional[str]       = None
    call_args:    List[str]           = field(default_factory=list)
    # Diagnostics
    source_line:  int                 = -1
    source_text:  str                 = ""
    # Liveness
    liveness:     LivenessInfo        = field(default_factory=LivenessInfo)
    metadata:     dict                = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "intent":       self.intent,
            "name":         self.name,
            "value":        self.value,
            "llvm_type":    self.llvm_type,
            "op":           self.op,
            "left":         self.left,
            "right":        self.right,
            "is_mutation":  self.is_mutation,
            "is_heap":      self.is_heap,
            "cond_left":    self.cond_left,
            "cond_op":      self.cond_op,
            "cond_right":   self.cond_right,
            "struct_type":  self.struct_type,
            "field_name":   self.field_name,
            "module_name":  self.module_name,
            "module_path":  self.module_path,
            "mangle_prefix":self.mangle_prefix,
            "call_fn":      self.call_fn,
            "call_args":    self.call_args,
            "source_line":  self.source_line,
            "source_text":  self.source_text,
            "body_nodes":   [n.to_dict() for n in self.body_nodes],
            "else_nodes":   [n.to_dict() for n in self.else_nodes],
            "struct_def":   {
                "name":   self.struct_def.name,
                "fields": [{"name": f.name, "llvm_type": f.llvm_type, "index": f.index}
                           for f in self.struct_def.fields],
            } if self.struct_def else None,
            "liveness": {
                "live_before": sorted(self.liveness.live_before),
                "live_after":  sorted(self.liveness.live_after),
                "defined":     sorted(self.liveness.defined),
                "used":        sorted(self.liveness.used),
            },
            "metadata": self.metadata,
        }


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — TYPE HELPERS
# ══════════════════════════════════════════════════════════════════════════════

TYPE_KEYWORDS: Dict[str, str] = {
    "number":       "i32",
    "integer":      "i32",
    "whole number": "i32",
    "count":        "i32",
    "decimal":      "double",
    "float":        "double",
    "real number":  "double",
    "text":         "i8*",
    "word":         "i8*",
    "sentence":     "i8*",
    "message":      "i8*",
    "string":       "i8*",
    "truth":        "i1",
    "boolean":      "i1",
    "flag":         "i1",
}

OP_TABLE: Dict[str, Tuple[str, str]] = {
    "ADD":      ("add nsw",  "fadd"),
    "SUBTRACT": ("sub nsw",  "fsub"),
    "MULTIPLY": ("mul nsw",  "fmul"),
    "DIVIDE":   ("sdiv",     "fdiv"),
    "MODULO":   ("srem",     "frem"),
}

def widen_type(a: str, b: str) -> str:
    if "double" in (a, b): return "double"
    if "i8*"   in (a, b): return "i8*"
    return "i32"

def default_for_type(t: str) -> str:
    return {"i32": "0", "double": "0.0", "i8*": "null", "i1": "0"}.get(t, "0")

def infer_type(val: Optional[str]) -> str:
    if val is None: return "i32"
    s = str(val).strip()
    if s.startswith("'") or s.startswith('"'): return "i8*"
    if re.match(r'^-?\d+\.\d+$', s): return "double"
    if re.match(r'^-?\d+$',      s): return "i32"
    if s.lower() in ("true","false","yes","no"): return "i1"
    return "i32"


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — MODULE RESOLVER  (Phase 5 — NEW)
# Finds .lum files in /lib directory. Supports nested "use" chains.
# ══════════════════════════════════════════════════════════════════════════════

# Map English module names → standard library module names
STDLIB_ALIASES: Dict[str, str] = {
    "math":        "math",
    "mathematics": "math",
    "maths":       "math",
    "time":        "time",
    "clock":       "time",
    "io":          "io",
    "input output":"io",
    "physics":     "physics",
    "strings":     "strings",
    "string":      "strings",
    "text":        "strings",
    "random":      "random",
    "rand":        "random",
    "list":        "list",
    "array":       "list",
}

# Map module function English phrases → LLVM IR symbol names in core.ll
CORE_FUNCTION_MAP: Dict[str, Dict[str, str]] = {
    "math": {
        "square root":    "__lum_core_sqrt",
        "sqrt":           "__lum_core_sqrt",
        "absolute value": "__lum_core_abs",
        "absolute":       "__lum_core_abs",
        "abs":            "__lum_core_abs",
        "power":          "__lum_core_pow",
        "ceiling":        "__lum_core_ceil",
        "floor":          "__lum_core_floor",
        "round":          "__lum_core_round",
        "logarithm":      "__lum_core_log",
        "log":            "__lum_core_log",
        "sine":           "__lum_core_sin",
        "sin":            "__lum_core_sin",
        "cosine":         "__lum_core_cos",
        "cos":            "__lum_core_cos",
    },
    "time": {
        "current time":   "__lum_core_time",
        "timestamp":      "__lum_core_time",
        "elapsed time":   "__lum_core_time",
    },
    "random": {
        "random number":  "__lum_core_rand",
        "random":         "__lum_core_rand",
    },
}


class ModuleResolver:
    """
    Finds .lum module files in the /lib directory.
    Returns the path and the mangling prefix for that module.
    """

    def __init__(self, lib_dirs: Optional[List[str]] = None):
        # Default search order: ./lib, ~/.lumina/lib, script dir/lib
        default_dirs = [
            Path("lib"),
            Path.home() / ".lumina" / "lib",
            Path(__file__).parent / "lib",
        ]
        if lib_dirs:
            self._dirs = [Path(d) for d in lib_dirs] + default_dirs
        else:
            self._dirs = default_dirs

    def resolve(self, raw_name: str) -> Tuple[str, Optional[str]]:
        """
        Given the raw English name from 'use the X library',
        return (canonical_name, path_or_None).
        canonical_name is the normalised library name.
        path is None for built-in (core.ll) libraries.
        """
        name = raw_name.lower().strip()
        canonical = STDLIB_ALIASES.get(name, name)

        # Built-in stdlib (backed by core.ll — no .lum file needed)
        if canonical in CORE_FUNCTION_MAP:
            return canonical, None

        # Search lib dirs for a .lum file
        for d in self._dirs:
            p = d / f"{canonical}.lum"
            if p.exists():
                return canonical, str(p)

        # Not found — return canonical name with no path
        # (caller will warn the user)
        return canonical, None

    def mangle_prefix(self, module_name: str) -> str:
        return f"__lum_{module_name}_"


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — DIAGNOSTIC SYSTEM
# ══════════════════════════════════════════════════════════════════════════════

INTENT_MAP: Dict[str, List[str]] = {
    "CREATE_VAR":    ["create a", "make a", "define a", "declare a"],
    "CALCULATE":     ["calculate", "compute", "figure out", "work out"],
    "ADD_TO":        ["add", "increase", "increment", "raise", "boost"],
    "SUBTRACT_FROM": ["subtract", "decrease", "decrement", "reduce", "lower"],
    "MULTIPLY_BY":   ["multiply", "scale", "double", "triple"],
    "DIVIDE_BY":     ["divide", "halve"],
    "IF":            ["if", "when", "whenever"],
    "LOOP":          ["repeat while", "loop while", "keep doing while"],
    "STOP":          ["stop", "end", "halt", "break", "done"],
    "PRINT":         ["show", "print", "display", "output", "say", "log"],
    "FREE":          ["free", "release", "delete", "drop", "destroy"],
    "MODULE_USE":    ["use the", "import the", "load the", "bring in the", "use"],
    "IMPORT_CALL":   ["calculate", "compute", "get", "find"],
    "STRUCT_DEF":    ["define", "describe", "blueprint"],
    "STRUCT_NEW":    ["create a new", "make a new", "build a new"],
    "FIELD_SET":     ["set the", "change the", "update the"],
    "FIELD_GET":     ["get the", "read the", "show the"],
}

@dataclass
class LuminaDiagnostic:
    code:       str
    level:      str  # "ERROR" | "WARNING" | "HINT"
    message:    str
    line:       int  = -1
    source:     str  = ""
    suggestion: str  = ""
    hint:       str  = ""

    @staticmethod
    def suggest_intent(text: str) -> str:
        words = text.lower().split()[:4]
        snippet = " ".join(words)
        candidates = []
        for intent, phrases in INTENT_MAP.items():
            for ph in phrases:
                candidates.append(ph)
        matches = get_close_matches(snippet, candidates, n=1, cutoff=0.4)
        return f"Did you mean: '{matches[0]} ...'?" if matches else ""


class DiagnosticCollector:
    def __init__(self):
        self._diags: List[LuminaDiagnostic] = []

    def error(self, code, msg, line=-1, source="", suggestion="", hint=""):
        self._diags.append(LuminaDiagnostic(code,"ERROR",msg,line,source,suggestion,hint))

    def warning(self, code, msg, line=-1, source="", suggestion="", hint=""):
        self._diags.append(LuminaDiagnostic(code,"WARNING",msg,line,source,suggestion,hint))

    def hint(self, code, msg, line=-1, source="", suggestion="", hint=""):
        self._diags.append(LuminaDiagnostic(code,"HINT",msg,line,source,suggestion,hint))

    def has_errors(self) -> bool:
        return any(d.level == "ERROR" for d in self._diags)

    def all(self) -> List[LuminaDiagnostic]:
        return self._diags

    def print_all(self):
        icons = {"ERROR": "✖", "WARNING": "⚠", "HINT": "💡"}
        for d in self._diags:
            icon = icons.get(d.level, "?")
            loc  = f" (line {d.line})" if d.line > 0 else ""
            print(f"  {icon} [{d.code}]{loc}  {d.message}")
            if d.source:
                print(f"       › {d.source}")
            if d.suggestion:
                print(f"       {d.suggestion}")
            if d.hint:
                print(f"       Hint: {d.hint}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — LARK GRAMMAR  (Phase 5: +use_stmt, +import_call)
# ══════════════════════════════════════════════════════════════════════════════

LUMINA_GRAMMAR_P5 = r"""
    start: (stmt NEWLINE*)*

    stmt: use_stmt
        | import_call_stmt
        | create_stmt
        | calc_stmt
        | mutation_stmt
        | print_stmt
        | free_stmt
        | borrow_stmt
        | own_stmt
        | if_stmt
        | loop_stmt
        | struct_def_stmt
        | struct_new_stmt
        | field_set_stmt
        | field_get_stmt

    // ── Phase 5: Module import ───────────────────────────────────────────────
    use_stmt: USE_KW ARTICLE? WORD+ (LIBRARY_KW)?
    USE_KW:      /use\b/i
    LIBRARY_KW:  /library\b/i
    ARTICLE:     /the\b|a\b|an\b/i

    // ── Phase 5: Call imported function ─────────────────────────────────────
    import_call_stmt: CALC_KW IDENT AS_KW FUNC_PHRASE OF_KW operand
    FUNC_PHRASE: /square root\b|sqrt\b|absolute value\b|absolute\b|abs\b|power\b|ceiling\b|floor\b|round\b|logarithm\b|log\b|sine\b|sin\b|cosine\b|cos\b|random number\b|random\b|current time\b|timestamp\b/i
    OF_KW:       /of\b/i

    // ── Create variable ───────────────────────────────────────────────────────
    create_stmt: CREATE_KW TYPE_KW CALLED_KW IDENT WITH_KW VALUE_KW value_expr
               | CREATE_KW TYPE_KW CALLED_KW IDENT AS_KW QUOTED_STRING
    CREATE_KW:  /create a\b|make a\b|define a\b|declare a\b|give me a\b|let there be a\b/i
    CALLED_KW:  /called\b|named\b/i
    WITH_KW:    /with\b/i
    VALUE_KW:   /value\b/i
    AS_KW:      /as\b/i
    TYPE_KW:    /number\b|integer\b|decimal\b|float\b|real number\b|text\b|word\b|sentence\b|truth\b|boolean\b|flag\b|count\b|whole number\b/i

    value_expr: NUMBER
              | SIGNED_FLOAT
              | IDENT

    // ── Calculate ─────────────────────────────────────────────────────────────
    calc_stmt: CALC_KW IDENT AS_KW operand MATH_OP operand
    CALC_KW:   /calculate\b|compute\b|figure out\b|work out\b|find\b|determine\b|evaluate\b|solve\b/i
    MATH_OP:   /plus\b|added to\b|minus\b|times\b|multiplied by\b|divided by\b|over\b|mod\b|modulo\b|remainder of\b/i

    operand: NUMBER | SIGNED_FLOAT | IDENT

    // ── Mutation ──────────────────────────────────────────────────────────────
    mutation_stmt: INCR_KW IDENT BY_KW operand
                 | ADD_KW operand TO_KW IDENT
                 | DECR_KW IDENT BY_KW operand
                 | DOUBLE_KW IDENT
                 | TRIPLE_KW IDENT
                 | HALVE_KW IDENT
    INCR_KW:   /increase\b|increment\b|bump up\b|raise\b|boost\b/i
    ADD_KW:    /add\b/i
    DECR_KW:   /decrease\b|decrement\b|reduce\b|lower\b|subtract\b/i
    BY_KW:     /by\b/i
    TO_KW:     /to\b/i
    DOUBLE_KW: /double\b/i
    TRIPLE_KW: /triple\b/i
    HALVE_KW:  /halve\b/i

    // ── Print ─────────────────────────────────────────────────────────────────
    print_stmt: PRINT_KW IDENT
              | PRINT_KW QUOTED_STRING
    PRINT_KW:  /show me\b|show\b|print\b|display\b|output\b|say\b|log\b|reveal\b|tell me\b/i

    // ── Free ─────────────────────────────────────────────────────────────────
    free_stmt: FREE_KW IDENT
    FREE_KW:   /free\b|release\b|delete\b|drop\b|destroy\b/i

    // ── Borrow / Own ─────────────────────────────────────────────────────────
    borrow_stmt: BORROW_KW IDENT
    own_stmt:    OWN_KW IDENT
    BORROW_KW:  /borrow\b|reference\b|look at\b/i
    OWN_KW:     /own\b|take ownership of\b|move\b/i

    // ── If block ──────────────────────────────────────────────────────────────
    if_stmt: IF_KW operand COMP_OP operand THEN_KW stmt (OTHERWISE_KW stmt)?
    IF_KW:       /if\b|when\b|whenever\b/i
    THEN_KW:     /then\b/i
    OTHERWISE_KW:/otherwise\b|else\b/i
    COMP_OP:     /is greater than\b|is less than\b|equals\b|is equal to\b|is at least\b|is at most\b|is not equal to\b|is\b/i

    // ── Loop block ────────────────────────────────────────────────────────────
    loop_stmt: LOOP_HDR_KW operand COMP_OP operand NEWLINE_BLOCK STOP_KW
    LOOP_HDR_KW: /repeat while\b|loop while\b|while\b|keep doing while\b|keep going while\b/i
    STOP_KW:     /stop\b|end loop\b|end\b|halt\b|break\b|done\b/i
    NEWLINE_BLOCK: /(\n[^\n]+)+/

    // ── Struct definition ────────────────────────────────────────────────────
    struct_def_stmt: DEFINE_KW IDENT AS_KW THING_KW WITH_KW field_list
    DEFINE_KW:   /define\b|describe\b|blueprint\b/i
    THING_KW:    /a thing\b|an object\b|a type\b|a struct\b/i
    field_list:  field_item (AND_KW field_item)*
    field_item:  IDENT LPAREN TYPE_KW RPAREN
    AND_KW:      /and\b/i
    LPAREN:      "("
    RPAREN:      ")"

    struct_new_stmt: STRUCT_NEW_KW IDENT CALLED_KW IDENT
    STRUCT_NEW_KW: /create a new\b|make a new\b|build a new\b/i

    field_set_stmt: FIELD_SET_KW IDENT OF_KW IDENT TO_KW operand
    FIELD_SET_KW:  /set the\b|change the\b|update the\b/i

    field_get_stmt: FIELD_GET_KW IDENT OF_KW IDENT
    FIELD_GET_KW:  /get the\b|read the\b|show the\b/i

    // ── Terminals ─────────────────────────────────────────────────────────────
    IDENT:         /[a-zA-Z_][a-zA-Z0-9_]*/
    WORD:          /[a-zA-Z_][a-zA-Z0-9_]*/
    NUMBER:        /-?\d+/
    SIGNED_FLOAT:  /-?\d+\.\d+/
    QUOTED_STRING: /\'[^\']*\'|\"[^\"]*\"/
    NEWLINE:       /\r?\n/

    %ignore /\s+/
    %ignore /#[^\n]*/
"""


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — LARK TRANSFORMER
# ══════════════════════════════════════════════════════════════════════════════

_COMP_MAP = {
    "is greater than":   "GT",
    "greater than":      "GT",
    "is less than":      "LT",
    "less than":         "LT",
    "equals":            "EQ",
    "is equal to":       "EQ",
    "is":                "EQ",
    "is at least":       "GTE",
    "at least":          "GTE",
    "is at most":        "LTE",
    "at most":           "LTE",
    "is not equal to":   "NEQ",
    "not equal to":      "NEQ",
}

_MATH_MAP = {
    "plus": "ADD", "added to": "ADD",
    "minus": "SUBTRACT",
    "times": "MULTIPLY", "multiplied by": "MULTIPLY",
    "divided by": "DIVIDE", "over": "DIVIDE",
    "mod": "MODULO", "modulo": "MODULO", "remainder of": "MODULO",
}

def _resolve_comp(s: str) -> str:
    s = s.lower().strip()
    return _COMP_MAP.get(s, "EQ")

def _resolve_math(s: str) -> str:
    s = s.lower().strip()
    return _MATH_MAP.get(s, "ADD")

def _resolve_type(s: str) -> str:
    s = s.lower().strip()
    return TYPE_KEYWORDS.get(s, "i32")


class LuminaTransformerP5(Transformer if LARK_AVAILABLE else object):

    def start(self, items):
        flat = []
        for it in items:
            if isinstance(it, list):
                flat.extend(it)
            elif isinstance(it, AITNode):
                flat.append(it)
        return flat

    def stmt(self, items):
        for it in items:
            if it is not None:
                return it
        return None

    # ── use_stmt ────────────────────────────────────────────────────────────
    def use_stmt(self, items):
        # Collect all WORD tokens (ignore USE_KW, ARTICLE, LIBRARY_KW)
        words = []
        for it in items:
            if isinstance(it, Token) and it.type == "WORD":
                words.append(str(it).lower())
        name = " ".join(words) if words else ""
        if not name:
            # Fallback: grab any token that isn't the keyword
            for it in items:
                if isinstance(it, Token) and it.type not in ("USE_KW","ARTICLE","LIBRARY_KW"):
                    name = str(it).lower()
                    break
        return AITNode(
            intent="MODULE_USE",
            module_name=name,
            source_text=" ".join(str(i) for i in items if isinstance(i, Token)),
        )

    # ── import_call_stmt ────────────────────────────────────────────────────
    def import_call_stmt(self, items):
        result_name = None
        func_phrase = None
        arg_name    = None
        for it in items:
            if isinstance(it, Token):
                if it.type == "IDENT" and result_name is None:
                    result_name = str(it).lower()
                elif it.type == "FUNC_PHRASE":
                    func_phrase = str(it).lower().strip()
                elif it.type == "IDENT" and result_name is not None:
                    arg_name = str(it).lower()
            elif isinstance(it, AITNode):
                arg_name = it.name or it.value
        return AITNode(
            intent="IMPORT_CALL",
            name=result_name,
            call_fn=func_phrase,
            call_args=[arg_name] if arg_name else [],
            llvm_type="double",
        )

    # ── create_stmt ─────────────────────────────────────────────────────────
    def create_stmt(self, items):
        type_kw = val = name = None
        is_heap = False
        for it in items:
            if isinstance(it, Token):
                if it.type == "TYPE_KW":
                    type_kw = str(it).lower()
                elif it.type == "IDENT":
                    name = str(it).lower()
                elif it.type == "QUOTED_STRING":
                    val = str(it)[1:-1]  # strip quotes
                    is_heap = True
            elif isinstance(it, AITNode) and it.intent == "__value__":
                val = it.value
        llvm_t = _resolve_type(type_kw or "") if type_kw else infer_type(val)
        return AITNode(
            intent="CREATE_VAR", name=name, value=val,
            llvm_type=llvm_t, is_heap=is_heap,
        )

    def value_expr(self, items):
        tok = items[0]
        return AITNode(intent="__value__", value=str(tok))

    # ── calc_stmt ────────────────────────────────────────────────────────────
    def calc_stmt(self, items):
        name = left = right = op_str = None
        operands = []
        for it in items:
            if isinstance(it, Token):
                if it.type == "IDENT" and name is None:
                    name = str(it).lower()
                elif it.type == "MATH_OP":
                    op_str = str(it).lower().strip()
            elif isinstance(it, AITNode) and it.intent == "__operand__":
                operands.append(it.value or it.name)
        if len(operands) >= 2:
            left, right = operands[0], operands[1]
        op = _resolve_math(op_str or "plus")
        ltype = infer_type(left)
        rtype = infer_type(right)
        result_type = widen_type(ltype, rtype)
        return AITNode(
            intent="CALCULATE", name=name, op=op,
            left=left, right=right, llvm_type=result_type,
        )

    def operand(self, items):
        tok = items[0]
        if isinstance(tok, Token):
            v = str(tok)
            if tok.type == "IDENT":
                return AITNode(intent="__operand__", name=v.lower(), value=v.lower())
            return AITNode(intent="__operand__", value=v)
        return AITNode(intent="__operand__", value=str(tok))

    # ── mutation_stmt ────────────────────────────────────────────────────────
    def mutation_stmt(self, items):
        tokens = [it for it in items if isinstance(it, Token)]
        operand_nodes = [it for it in items if isinstance(it, AITNode) and it.intent == "__operand__"]

        first_kw = tokens[0] if tokens else None
        if first_kw is None:
            return None
        kw_type = first_kw.type

        if kw_type == "DOUBLE_KW":
            target = operand_nodes[0].value if operand_nodes else (tokens[1].lower() if len(tokens)>1 else None)
            return AITNode(intent="MULTIPLY_BY", name=target, left=target, right="2",
                           op="MULTIPLY", is_mutation=True)
        if kw_type == "TRIPLE_KW":
            target = operand_nodes[0].value if operand_nodes else (str(tokens[1]).lower() if len(tokens)>1 else None)
            return AITNode(intent="MULTIPLY_BY", name=target, left=target, right="3",
                           op="MULTIPLY", is_mutation=True)
        if kw_type == "HALVE_KW":
            target = operand_nodes[0].value if operand_nodes else (str(tokens[1]).lower() if len(tokens)>1 else None)
            return AITNode(intent="DIVIDE_BY", name=target, left=target, right="2",
                           op="DIVIDE", is_mutation=True)

        # INCR/DECR/ADD forms
        ident_toks = [t for t in tokens if t.type == "IDENT"]
        delta_nodes= [n for n in operand_nodes]
        delta      = delta_nodes[0].value if delta_nodes else "1"
        target     = str(ident_toks[-1]).lower() if ident_toks else None

        if kw_type in ("INCR_KW", "ADD_KW"):
            return AITNode(intent="ADD_TO", name=target, left=target, right=delta,
                           op="ADD", is_mutation=True)
        else:
            return AITNode(intent="SUBTRACT_FROM", name=target, left=target, right=delta,
                           op="SUBTRACT", is_mutation=True)

    # ── print_stmt ──────────────────────────────────────────────────────────
    def print_stmt(self, items):
        for it in items:
            if isinstance(it, Token):
                if it.type == "IDENT":
                    return AITNode(intent="PRINT", name=str(it).lower())
                if it.type == "QUOTED_STRING":
                    return AITNode(intent="PRINT_LITERAL",
                                   value=str(it)[1:-1],
                                   name=str(it)[1:-1])
        return None

    # ── free / borrow / own ─────────────────────────────────────────────────
    def free_stmt(self, items):
        ident = next((str(t).lower() for t in items if isinstance(t, Token) and t.type == "IDENT"), None)
        return AITNode(intent="FREE", name=ident)

    def borrow_stmt(self, items):
        ident = next((str(t).lower() for t in items if isinstance(t, Token) and t.type == "IDENT"), None)
        return AITNode(intent="BORROW", name=ident)

    def own_stmt(self, items):
        ident = next((str(t).lower() for t in items if isinstance(t, Token) and t.type == "IDENT"), None)
        return AITNode(intent="OWN", name=ident)

    # ── if_stmt ──────────────────────────────────────────────────────────────
    def if_stmt(self, items):
        operands   = [it for it in items if isinstance(it, AITNode) and it.intent == "__operand__"]
        stmts      = [it for it in items if isinstance(it, AITNode) and it.intent not in ("__operand__",)]
        comp_op    = next((str(t) for t in items if isinstance(t, Token) and t.type == "COMP_OP"), "is")
        cond_left  = operands[0].value if len(operands) > 0 else None
        cond_right = operands[1].value if len(operands) > 1 else "0"
        body_n     = stmts[:1] if stmts else []
        else_n     = stmts[1:2] if len(stmts) > 1 else []
        return AITNode(
            intent="IF_BLOCK",
            cond_left=cond_left, cond_op=_resolve_comp(comp_op), cond_right=cond_right,
            body_nodes=body_n, else_nodes=else_n,
        )

    # ── loop_stmt ────────────────────────────────────────────────────────────
    def loop_stmt(self, items):
        operands = [it for it in items if isinstance(it, AITNode) and it.intent == "__operand__"]
        comp_op  = next((str(t) for t in items if isinstance(t, Token) and t.type == "COMP_OP"), "is")
        block    = next((str(t) for t in items if isinstance(t, Token) and t.type == "NEWLINE_BLOCK"), "")
        body_nodes = []
        for bline in block.strip().split("\n"):
            bline = bline.strip()
            if bline:
                n = RegexFallbackParser().try_parse(bline, -1)
                if n:
                    body_nodes.append(n)
        return AITNode(
            intent="LOOP_BLOCK",
            cond_left=operands[0].value if operands else None,
            cond_op=_resolve_comp(comp_op),
            cond_right=operands[1].value if len(operands) > 1 else "0",
            body_nodes=body_nodes,
        )

    # ── struct statements ────────────────────────────────────────────────────
    def struct_def_stmt(self, items):
        ident_toks = [t for t in items if isinstance(t, Token) and t.type == "IDENT"]
        name = str(ident_toks[0]).lower() if ident_toks else "Unknown"
        fields_node = next((it for it in items if isinstance(it, list)), [])
        sfields = []
        for i, (fname, ftype) in enumerate(fields_node):
            sfields.append(StructField(name=fname, llvm_type=ftype, index=i))
        sdef = StructDef(name=name, fields=sfields)
        return AITNode(intent="STRUCT_DEF", name=name, struct_def=sdef)

    def field_list(self, items):
        return [it for it in items if isinstance(it, tuple)]

    def field_item(self, items):
        fname = str(next(t for t in items if isinstance(t, Token) and t.type == "IDENT")).lower()
        ftype_kw = str(next(t for t in items if isinstance(t, Token) and t.type == "TYPE_KW")).lower()
        return (fname, _resolve_type(ftype_kw))

    def struct_new_stmt(self, items):
        ident_toks = [str(t).lower() for t in items if isinstance(t, Token) and t.type == "IDENT"]
        type_name  = ident_toks[0] if ident_toks else None
        var_name   = ident_toks[1] if len(ident_toks) > 1 else None
        return AITNode(intent="STRUCT_NEW", name=var_name, struct_type=type_name)

    def field_set_stmt(self, items):
        ident_toks   = [str(t).lower() for t in items if isinstance(t, Token) and t.type == "IDENT"]
        operand_node = next((it for it in items if isinstance(it, AITNode) and it.intent == "__operand__"), None)
        field_name   = ident_toks[0] if ident_toks else None
        struct_var   = ident_toks[1] if len(ident_toks) > 1 else None
        value        = operand_node.value if operand_node else None
        return AITNode(intent="FIELD_SET", name=struct_var, field_name=field_name, value=value)

    def field_get_stmt(self, items):
        ident_toks = [str(t).lower() for t in items if isinstance(t, Token) and t.type == "IDENT"]
        field_name = ident_toks[0] if ident_toks else None
        struct_var = ident_toks[1] if len(ident_toks) > 1 else None
        return AITNode(intent="FIELD_GET", name=struct_var, field_name=field_name)

    # ── terminal pass-through ────────────────────────────────────────────────
    def NEWLINE(self, tok): return None
    def NEWLINE_BLOCK(self, tok): return tok


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 7 — REGEX FALLBACK PARSER  (handles lines Lark can't parse)
# ══════════════════════════════════════════════════════════════════════════════

class RegexFallbackParser:
    """
    Regex-based fallback for common patterns.
    Same as Phase 4, now also handles USE and IMPORT_CALL.
    """

    _CREATE_RE = re.compile(
        r'^(?:create a|make a|define a|declare a|give me a|let there be a)\s+'
        r'(?P<type>number|integer|decimal|float|real number|text|word|sentence|truth|boolean|flag|count)'
        r'\s+(?:called|named)\s+(?P<name>\w+)'
        r'(?:\s+with\s+value\s+(?P<val>\S+)|\s+as\s+(?P<str>[\'"].*[\'"]))?',
        re.IGNORECASE
    )
    _PRINT_RE = re.compile(
        r'^(?:show me|show|print|display|output|say|log|reveal|tell me)\s+(?P<name>\w+)',
        re.IGNORECASE
    )
    _PRINT_LIT_RE = re.compile(
        r'^(?:show me|show|print|display|output|say|log|reveal|tell me)\s+[\'"](?P<text>[^\'"]+)[\'"]',
        re.IGNORECASE
    )
    _CALC_RE = re.compile(
        r'^(?:calculate|compute|figure out|work out|find|determine|evaluate|solve)\s+'
        r'(?P<name>\w+)\s+as\s+(?P<left>\w+|\d+(?:\.\d+)?)\s+'
        r'(?P<op>plus|added to|minus|times|multiplied by|divided by|over|mod|modulo|remainder of)\s+'
        r'(?P<right>\w+|\d+(?:\.\d+)?)',
        re.IGNORECASE
    )
    _INCR_RE = re.compile(
        r'^(?:increase|increment|bump up|raise|boost|add to)\s+(?P<target>\w+)\s+by\s+(?P<delta>\w+|\d+(?:\.\d+)?)',
        re.IGNORECASE
    )
    _ADD_TO_RE = re.compile(
        r'^add\s+(?P<delta>\w+|\d+(?:\.\d+)?)\s+to\s+(?P<target>\w+)',
        re.IGNORECASE
    )
    _DECR_RE = re.compile(
        r'^(?:decrease|decrement|reduce|lower|subtract)\s+(?P<target>\w+)\s+by\s+(?P<delta>\w+|\d+(?:\.\d+)?)',
        re.IGNORECASE
    )
    _FREE_RE   = re.compile(r'^(?:free|release|delete|drop|destroy)\s+(?P<name>\w+)', re.IGNORECASE)
    _DOUBLE_RE = re.compile(r'^double\s+(?P<name>\w+)', re.IGNORECASE)
    _TRIPLE_RE = re.compile(r'^triple\s+(?P<name>\w+)', re.IGNORECASE)
    _HALVE_RE  = re.compile(r'^halve\s+(?P<name>\w+)', re.IGNORECASE)

    # Phase 5 patterns
    _USE_RE = re.compile(
        r'^(?:use|import|load|bring in|include|activate)\s+(?:the\s+)?(?P<name>[a-z ]+?)(?:\s+library)?$',
        re.IGNORECASE
    )
    _IMPORT_CALL_RE = re.compile(
        r'^(?:calculate|compute|find|get)\s+(?P<result>\w+)\s+as\s+'
        r'(?P<func>square root|sqrt|absolute value|absolute|abs|power|ceiling|floor|round|log(?:arithm)?|sin(?:e)?|cos(?:ine)?|random(?: number)?|current time|timestamp)\s+'
        r'of\s+(?P<arg>\w+|\d+(?:\.\d+)?)',
        re.IGNORECASE
    )
    _IF_INLINE_RE = re.compile(
        r'^if\s+(?P<left>\w+)\s+(?P<comp>is greater than|is less than|equals|is equal to|is at least|is at most|is)\s+(?P<right>\w+|\d+(?:\.\d+)?)\s+then\s+(?P<body>.+?)(?:\s+otherwise\s+(?P<else>.+))?$',
        re.IGNORECASE
    )

    _LOOP_HDR_RE = re.compile(
        r'^(?:repeat while|loop while|while|keep doing while|keep going while)\s+'
        r'(?P<left>\w+|\d+(?:\.\d+)?)\s+'
        r'(?P<comp>is greater than|is less than|equals|is equal to|is at least|is at most|is)\s+'
        r'(?P<right>\w+|\d+(?:\.\d+)?)',
        re.IGNORECASE
    )
    _STOP_RE = re.compile(r'^(?:stop|end loop|end|halt|break|done)\s*$', re.IGNORECASE)

    def try_parse(self, line: str, lineno: int) -> Optional[AITNode]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            return None

        # Handle multi-line loop blocks (passed as one string with \n)
        if "\n" in stripped:
            lines = [l.strip() for l in stripped.split("\n") if l.strip() and not l.strip().startswith("#")]
            if lines:
                loop_node = self._try_loop_from_lines(lines, lineno)
                if loop_node:
                    return loop_node

        # USE
        m = self._USE_RE.match(stripped)
        if m:
            return AITNode(intent="MODULE_USE", module_name=m.group("name").lower().strip(),
                           source_line=lineno, source_text=stripped)

        # IMPORT_CALL
        m = self._IMPORT_CALL_RE.match(stripped)
        if m:
            return AITNode(intent="IMPORT_CALL",
                           name=m.group("result").lower(),
                           call_fn=m.group("func").lower().strip(),
                           call_args=[m.group("arg").lower()],
                           llvm_type="double",
                           source_line=lineno, source_text=stripped)

        # CREATE
        m = self._CREATE_RE.match(stripped)
        if m:
            type_str = m.group("type").lower()
            val = m.group("val") or (m.group("str")[1:-1] if m.group("str") else None)
            is_heap = bool(m.group("str"))
            lt = _resolve_type(type_str)
            return AITNode(intent="CREATE_VAR", name=m.group("name").lower(),
                           value=val, llvm_type=lt, is_heap=is_heap,
                           source_line=lineno, source_text=stripped)

        # PRINT LITERAL
        m = self._PRINT_LIT_RE.match(stripped)
        if m:
            return AITNode(intent="PRINT_LITERAL", name=m.group("text"), value=m.group("text"),
                           source_line=lineno, source_text=stripped)

        # PRINT
        m = self._PRINT_RE.match(stripped)
        if m:
            return AITNode(intent="PRINT", name=m.group("name").lower(),
                           source_line=lineno, source_text=stripped)

        # CALCULATE
        m = self._CALC_RE.match(stripped)
        if m:
            op = _resolve_math(m.group("op").lower())
            lt = infer_type(m.group("left"))
            rt = infer_type(m.group("right"))
            return AITNode(intent="CALCULATE",
                           name=m.group("name").lower(),
                           op=op,
                           left=m.group("left").lower(),
                           right=m.group("right").lower(),
                           llvm_type=widen_type(lt, rt),
                           source_line=lineno, source_text=stripped)

        # ADD TO
        m = self._ADD_TO_RE.match(stripped)
        if m:
            return AITNode(intent="ADD_TO", name=m.group("target").lower(),
                           left=m.group("target").lower(), right=m.group("delta"),
                           op="ADD", is_mutation=True, source_line=lineno, source_text=stripped)

        # INCR
        m = self._INCR_RE.match(stripped)
        if m:
            return AITNode(intent="ADD_TO", name=m.group("target").lower(),
                           left=m.group("target").lower(), right=m.group("delta"),
                           op="ADD", is_mutation=True, source_line=lineno, source_text=stripped)

        # DECR
        m = self._DECR_RE.match(stripped)
        if m:
            return AITNode(intent="SUBTRACT_FROM", name=m.group("target").lower(),
                           left=m.group("target").lower(), right=m.group("delta"),
                           op="SUBTRACT", is_mutation=True, source_line=lineno, source_text=stripped)

        # DOUBLE/TRIPLE/HALVE
        m = self._DOUBLE_RE.match(stripped)
        if m:
            t = m.group("name").lower()
            return AITNode(intent="MULTIPLY_BY", name=t, left=t, right="2",
                           op="MULTIPLY", is_mutation=True, source_line=lineno, source_text=stripped)
        m = self._TRIPLE_RE.match(stripped)
        if m:
            t = m.group("name").lower()
            return AITNode(intent="MULTIPLY_BY", name=t, left=t, right="3",
                           op="MULTIPLY", is_mutation=True, source_line=lineno, source_text=stripped)
        m = self._HALVE_RE.match(stripped)
        if m:
            t = m.group("name").lower()
            return AITNode(intent="DIVIDE_BY", name=t, left=t, right="2",
                           op="DIVIDE", is_mutation=True, source_line=lineno, source_text=stripped)

        # FREE
        m = self._FREE_RE.match(stripped)
        if m:
            return AITNode(intent="FREE", name=m.group("name").lower(),
                           source_line=lineno, source_text=stripped)

        # IF inline
        m = self._IF_INLINE_RE.match(stripped)
        if m:
            body_node = self.try_parse(m.group("body"), lineno)
            else_node = self.try_parse(m.group("else"), lineno) if m.group("else") else None
            return AITNode(
                intent="IF_BLOCK",
                cond_left=m.group("left").lower(),
                cond_op=_resolve_comp(m.group("comp")),
                cond_right=m.group("right").lower(),
                body_nodes=[body_node] if body_node else [],
                else_nodes=[else_node] if else_node else [],
                source_line=lineno, source_text=stripped,
            )

        return None

    def _try_loop_from_lines(self, lines: list, lineno: int) -> Optional[AITNode]:
        """Parse a multi-line loop block from a list of stripped lines."""
        header = lines[0]
        m = self._LOOP_HDR_RE.match(header)
        if not m:
            return None
        body_nodes = []
        for bl in lines[1:]:
            if self._STOP_RE.match(bl):
                break
            child = self.try_parse(bl, lineno)
            if child:
                body_nodes.append(child)
        return AITNode(
            intent="LOOP_BLOCK",
            cond_left=m.group("left").lower(),
            cond_op=_resolve_comp(m.group("comp")),
            cond_right=m.group("right").lower(),
            body_nodes=body_nodes,
            source_line=lineno,
            source_text=header,
        )


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 8 — LUMINA FRONT-END  (Phase 5 — with Module Resolver)
# ══════════════════════════════════════════════════════════════════════════════

class LuminaFrontEndP5:
    """
    Phase 5 front-end: Lark → fallback → module resolution → name mangling.
    """

    def __init__(self, lib_dirs: Optional[List[str]] = None, strict: bool = False):
        self._strict   = strict
        self._fallback = RegexFallbackParser()
        self._diags    = DiagnosticCollector()
        self._resolver = ModuleResolver(lib_dirs)
        self._structs: Dict[str, StructDef] = {}
        # Track which modules are loaded (module_name → canonical name)
        self._loaded_modules: Dict[str, str] = {}
        # Track external function symbols that codegen must declare
        self._external_symbols: List[Tuple[str, str, List[str]]] = []
        # Name mangling active prefix (set when inside a module parse)
        self._mangle_ctx: Optional[str] = None

        if LARK_AVAILABLE:
            try:
                self._lark = Lark(
                    LUMINA_GRAMMAR_P5,
                    parser="earley",
                    propagate_positions=True,
                    ambiguity="resolve",
                )
            except Exception as e:
                print(f"[Lumina] Grammar error: {e}")
                self._lark = None
        else:
            self._lark = None

    @property
    def diagnostics(self) -> DiagnosticCollector:
        return self._diags

    @property
    def struct_registry(self) -> Dict[str, StructDef]:
        return self._structs

    @property
    def loaded_modules(self) -> Dict[str, str]:
        return self._loaded_modules

    @property
    def external_symbols(self) -> List[Tuple[str, str, List[str]]]:
        """Returns list of (symbol_name, return_type, arg_types)."""
        return self._external_symbols

    def parse_program(self, source: str) -> List[AITNode]:
        self._diags      = DiagnosticCollector()
        raw_lines        = source.split("\n")
        processed, _     = self._preprocess_blocks(raw_lines)

        nodes: List[AITNode] = []
        for (chunk_text, orig_line) in processed:
            parsed = self._parse_chunk(chunk_text, orig_line, raw_lines)
            if parsed is None:
                continue
            if isinstance(parsed, list):
                for n in parsed:
                    self._handle_node(n, nodes)
            else:
                self._handle_node(parsed, nodes)

        # Register struct definitions
        for n in nodes:
            if n.intent == "STRUCT_DEF" and n.struct_def:
                self._structs[n.struct_def.name] = n.struct_def

        return nodes

    def _handle_node(self, node: AITNode, nodes: List[AITNode]):
        """Post-process each node: resolve MODULE_USE, mangle IMPORT_CALL."""
        if node.intent == "MODULE_USE":
            canonical, path = self._resolver.resolve(node.module_name or "")
            node.module_name  = canonical
            node.module_path  = path
            node.mangle_prefix = self._resolver.mangle_prefix(canonical)
            self._loaded_modules[canonical] = path or "<builtin>"

            # Register external symbols for this module
            if canonical in CORE_FUNCTION_MAP:
                for phrase, sym in CORE_FUNCTION_MAP[canonical].items():
                    self._external_symbols.append((sym, "double", ["double"]))

            if path:
                # Load and parse the .lum module file
                try:
                    module_src = Path(path).read_text()
                    sub_fe = LuminaFrontEndP5(strict=self._strict)
                    sub_nodes = sub_fe.parse_program(module_src)
                    # Mangle all names in sub-nodes
                    prefix = node.mangle_prefix or ""
                    self._mangle_nodes(sub_nodes, prefix)
                    node.body_nodes = sub_nodes
                except Exception as e:
                    self._diags.warning("W005",
                        f"Could not load module '{canonical}' from {path}: {e}",
                        line=node.source_line)
            elif canonical not in CORE_FUNCTION_MAP:
                self._diags.warning("W004",
                    f"Module '{canonical}' not found in /lib or stdlib.",
                    hint=f"Create lib/{canonical}.lum or run: lumina install {canonical}",
                    line=node.source_line)

        elif node.intent == "IMPORT_CALL":
            # Resolve the English function phrase to a core symbol
            func_phrase = (node.call_fn or "").lower().strip()
            resolved_sym = None
            for mod_name in self._loaded_modules:
                if mod_name in CORE_FUNCTION_MAP:
                    for phrase, sym in CORE_FUNCTION_MAP[mod_name].items():
                        if phrase in func_phrase or func_phrase in phrase:
                            resolved_sym = sym
                            break
                if resolved_sym:
                    break
            if resolved_sym:
                node.call_fn = resolved_sym
            else:
                self._diags.warning("W006",
                    f"Function '{func_phrase}' not found. Did you 'use the math library' first?",
                    line=node.source_line)

        nodes.append(node)

    @staticmethod
    def _mangle_nodes(nodes: List[AITNode], prefix: str):
        """Prepend prefix to all variable names in a list of nodes."""
        def mangle(s: Optional[str]) -> Optional[str]:
            if s is None: return None
            if re.match(r'^[a-zA-Z_]\w*$', s) and not s.startswith("__"):
                return prefix + s
            return s

        for n in nodes:
            n.name       = mangle(n.name)
            n.left       = mangle(n.left)
            n.right      = mangle(n.right)
            n.cond_left  = mangle(n.cond_left)
            n.cond_right = mangle(n.cond_right)
            n.call_args  = [mangle(a) or a for a in n.call_args]
            if n.body_nodes: LuminaFrontEndP5._mangle_nodes(n.body_nodes, prefix)
            if n.else_nodes: LuminaFrontEndP5._mangle_nodes(n.else_nodes, prefix)

    def _preprocess_blocks(self, lines: List[str]) -> Tuple[List[Tuple[str,int]], dict]:
        result: List[Tuple[str,int]] = []
        i = 0
        _LOOP_HDR = re.compile(
            r'^(?:repeat while|loop while|while|keep doing while|keep going while)\s+',
            re.IGNORECASE)
        _STOP = re.compile(r'^(?:stop|end loop|end|halt|break|done)\s*$', re.IGNORECASE)

        while i < len(lines):
            raw  = lines[i]
            line = raw.strip()
            i   += 1
            if not line or line.startswith("#"):
                continue
            if _LOOP_HDR.match(line):
                orig_line  = i
                body_parts = [line]
                while i < len(lines):
                    braw  = lines[i]
                    bline = braw.strip()
                    i    += 1
                    if not bline or bline.startswith("#"):
                        continue
                    if _STOP.match(bline):
                        body_parts.append(bline)
                        break
                    body_parts.append(bline)
                result.append(("\n".join(body_parts), orig_line))
            else:
                result.append((line, i))
        return result, {}

    def _parse_chunk(self, text: str, lineno: int, all_lines: List[str]) -> Optional[AITNode]:
        # Attempt Lark
        if self._lark:
            try:
                tree  = self._lark.parse(text)
                nodes = LuminaTransformerP5().transform(tree)
                if isinstance(nodes, list) and nodes:
                    return nodes[0] if len(nodes) == 1 else nodes
                if isinstance(nodes, AITNode):
                    return nodes
            except Exception:
                pass

        # Regex fallback
        if not self._strict:
            node = self._fallback.try_parse(text, lineno)
            if node:
                return node

        # Rich diagnostic
        suggestion = LuminaDiagnostic.suggest_intent(text)
        self._diags.error("E001", "Unrecognised sentence",
                          line=lineno, source=text[:80],
                          suggestion=suggestion,
                          hint="Try: create a number called x with value 5  |  show x  |  free x")
        return None
