
🌟 Lumina
Natural Language. Native Performance.
Lumina is a high-performance systems programming language that bridges the gap between human thought and machine execution. It allows developers to write code in plain English while leveraging the power of LLVM to produce optimized, native machine binaries that rival C++ and Rust in speed.
</div>
✨ Key Features (Phase 5 Release)
English-First Syntax: No brackets, no semi-colons. Define logic using natural sentences.
LLVM Backend: Compiles directly to LLVM IR, enabling O3-level optimizations.
Memory Safety: A professional Borrow Checker prevents Use-After-Free and Double-Free errors at compile time.
LPM (Lumina Package Manager): A built-in module system to install and share libraries (use the math library).
Object Brain: High-level struct definitions with low-level memory control.
Stealth Binary: The compiler is self-contained via Nuitka. No Python dependencies are visible to the end-user.
🚀 Installation Guide
You can easily install Lumina and compile the stealth binary using the provided install script.
Prerequisites
Python 3.10+
Clang / LLVM (Required for linking the generated IR)
1. Clone the Repository:
  git clone [https://github.com/shell-   bay/Lumina.git].           (https://github.com/shell-   bay/Lumina.git)
cd Lumina
2. Run the Power Installer
The install.sh script will install required Python packages (lark, llvmlite, nuitka), pre-compile the core.ll standard library, and freeze the compiler into a single machine-code executable named lumina.
 chmod +x install.sh
./install.sh
3. Verify Installation
./lumina version
📦 Package Management (LPM)
Lumina comes with LPM, making code reuse effortless.
# Download a module to your local ./lib folder
./lumina install physics    

# Show all installed modules
./lumina list               

# Search the central registry
./lumina search strings     
📘 The Lumina Developer Guide
How to speak to the machine in English.
1. Variables (Creating and Setting)
You must define the type (number for integers, decimal3. Output
To print a variable to the console, ask Lumina to show it.
for floats, text for strings, truth for booleans).


create a number called age with value 25

create a decimal called pi with value 3.14
create a text called username as "Alice"

Modifying Variables:
set age to 26
increase age by 5
decrease age by 1
2. Arithmetic
Lumina evaluates the right side and stores it in the variable on the left.

create a decimal called radius with value 10.0
calculate base_area as 3.14 times radius times radius
calculate volume as base_area times 5.0

3. Output
To print a variable to the console, ask Lumina to show it.

show volume
show "Hello World"

4. Conditional Logic (If / Else)
Comparisons: is greater than, is less than, is equal to, is not equal to.
create a number called speed with value 120
If speed is greater than 100 then show "Speeding!" otherwise show "Safe speed"

5. Loops (Repeating Actions)
Loops use a Repeat while block and must be closed with a Stop statement.

create a number called speed with value 120
If speed is greater than 100 then show "Speeding!" otherwise show "Safe speed"
6. Object Brain (Structs)
Lumina allows you to create complex data structures called "Things."

define Player as a thing with health (number) and speed (decimal)

create a new Player called hero
set the health of hero to 100
set the speed of hero to 5.5

get the health of hero

7. Modules and the Standard Library (Phase 5)
To use code from another file, ask Lumina to use it.
use the math library

create a decimal called x with value 144.0
calculate my_root as square root of x
show my_root
(Available Core Math Functions: square root, absolute value, ceiling, floor, round, log, sin, cos, tan, random number, current time)

8. Memory Management (Safety)
Lumina safely manages Heap allocations (like Strings and Structs), but you can explicitly free them. The Borrow Checker will physically prevent you from running code if you try to use a variable after freeing it.
create a text called secret as "password"
free secret

show secret  # <-- The compiler will throw an E007 (Use-After-Free) Error!
🛠 Building Your Program
Once you have written your .lum file (e.g., hello.lum), compile it into a native executable:
./lumina build hello.lum -o my_app
./my_app

You can also test ideas instantly using the interactive REPL:
./lumina repl
📊 Performance Benchmarks

Task Python (v3.12) Lumina (v0.5.0) C++ (Clang)
Math (Recursive) 1.2s 0.02s 0.018s
Memory Management GC (Heavy) Manual/Safe (Zero Cost) Manual (Risky)
Syntax High-level Natural English Rigid
📄 License
Lumina is released under the MIT License. Built with ❤️ for the future of readable systems programming.
