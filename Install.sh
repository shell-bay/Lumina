#!/bin/bash

# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  LUMINA COMPILER v0.5.0 — POWER INSTALLER                                ║
# ║  Phase 5: Stealth Binary, Module System, and Core Library Setup          ║
# ╚══════════════════════════════════════════════════════════════════════════╝

set -e # Exit on error

# --- Colors for UI ---
C_RESET="\033[0m"
C_BOLD="\033[1m"
C_GREEN="\033[32m"
C_RED="\033[31m"
C_CYAN="\033[36m"
C_YELLOW="\033[33m"

msg() { echo -e "${C_CYAN}${C_BOLD}>>${C_RESET} ${C_BOLD}$1${C_RESET}"; }
ok()  { echo -e "${C_GREEN}  [OK]${C_RESET} $1"; }
err() { echo -e "${C_RED}  [ERROR]${C_RESET} $1"; exit 1; }
warn() { echo -e "${C_YELLOW}  [WARN]${C_RESET} $1"; }

# ── STEP 1: Environment Check ────────────────────────────────────────────────

echo -e "\n${C_BOLD}Starting Lumina v0.5.0 Installation...${C_RESET}\n"

# Check Python
if ! command -v python3 &> /dev/null; then
    err "Python 3 is required but not found."
fi
PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
msg "Checking Python version... Found $PY_VER"

# Check Clang/LLVM
if ! command -v clang &> /dev/null; then
    err "Clang (LLVM) is required to build Lumina binaries."
fi
ok "Clang found."

# ── STEP 2: Dependency Installation ──────────────────────────────────────────

msg "Installing Python dependencies (Lark, LLVMlite, Nuitka)..."
python3 -m pip install --upgrade pip --quiet
python3 -m pip install lark llvmlite nuitka --quiet
ok "Dependencies installed."

# ── STEP 3: Pre-compile Standard Library (core.ll) ───────────────────────────

msg "Building Lumina Core Standard Library..."
if [ -f "core.ll" ]; then
    clang -c core.ll -o core.o
    ok "core.o generated from core.ll"
elif [ -f "core.ll.txt" ]; then
    # Handle the .txt suffix if it exists in the environment
    clang -c core.ll.txt -o core.o
    ok "core.o generated from core.ll.txt"
else
    warn "core.ll not found! Math functions will be unavailable until built."
fi

# ── STEP 4: Setup Directory Structure ────────────────────────────────────────

msg "Initializing Lumina environment..."
mkdir -p lib
mkdir -p outputs
ok "Directories created (/lib, /outputs)."

# ── STEP 5: Freeze Binary (The Stealth Pass) ─────────────────────────────────

msg "Running Stealth Freeze (Nuitka)..."
# We target freeze_compiler.py which orchestrates the Nuitka build
if [ -f "freeze_compiler.py" ]; then
    python3 freeze_compiler.py --name lumina
    
    # Check if the binary was produced
    if [ -f "lumina" ]; then
        chmod +x lumina
        ok "Stealth binary 'lumina' is ready."
    elif [ -f "dist/lumina" ]; then
        mv dist/lumina .
        chmod +x lumina
        ok "Stealth binary 'lumina' is ready."
    else
        warn "Nuitka build did not produce a binary in the expected spot."
        warn "Falling back to interpreted mode (lumina_repl_p5.py)."
    fi
else
    err "freeze_compiler.py missing. Cannot generate stealth binary."
fi

# ── STEP 6: Final Verification ───────────────────────────────────────────────

msg "Verifying installation..."
if [ -f "./lumina" ]; then
    echo -e "\n${C_GREEN}${C_BOLD}SUCCESS!${C_RESET}"
    echo -e "Lumina Phase 5 is installed as a standalone machine-code binary."
    echo -e "Usage:"
    echo -e "  ./lumina repl           # Start interactive shell"
    echo -e "  ./lumina build app.lum  # Compile English to Binary"
    echo -e "  ./lumina install math   # Use Lumina Package Manager (lpm)"
else
    warn "Installation finished with warnings. Stealth binary not found."
    echo -e "You can still run the compiler using: python3 lumina_repl_p5.py"
fi

echo -e "\n${C_BOLD}Happy Coding! 🚀${C_RESET}\n"
