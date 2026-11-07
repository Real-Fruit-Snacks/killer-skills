# Refactoring craft

How to turn the user's refactor into a plan, how to write the steps' allowed interface changes, and how to record runs that catch what the tests miss.

## Cutting a refactor into steps

- **Small steps.** Each step is one change a reviewer could check in a few minutes and a refactorer can make without guessing. "Split utils.py" is four steps: move the string helpers, move the date helpers, move the file helpers, delete what's left. A step that fails verify is cheaper to redo when it's small.
- **One kind of change per step.** Move, rename, extract, inline, simplify, delete: never two in one step. A step that moves a function and also tidies it can't be checked as a move.
- **Move, then change.** Code moves as it is first, with the old names re-exported where callers expect them. Changing the moved code is the next step. Removing the re-exports, if the user wants that, is the last.
- **Keep names greppable.** Rename one thing per step, everywhere at once: code, tests, docs, config, strings that name it. A half-done rename is a broken interface.
- **Never a fix inside a refactor.** When a step would change what the code does, even to fix an obvious bug, it isn't a refactor. Pin the behaviour as it is, finish the refactor, and give the bug to the user (killer-bugs proves and fixes it). A probe that shows a difference is the same: record it with `decide`, keep the step out.
- **Order by risk.** Steps that only move code come first; they make later steps smaller. Deleting dead code comes after the moves, once it's clear nothing reaches it. Simplifying logic comes last, with pins in place.
- **Leave out what the user didn't ask for.** Formatting, renaming for taste and new abstractions are other people's refactors.

## The kinds, and what each step may change

Write the allowed changes with dotted names as `surface record` prints them (`module.Class.method`). Patterns use `*`.

| Kind | Typical steps | Allow |
|---|---|---|
| Split a module | Move one group of functions into a new module, re-exported from the old one | `add <newmodule>*` |
| Extract a function | Pull a block into a private helper (`_name`); the caller calls it | nothing (private names aren't in the interface); `add <module>.<name>` if it's public |
| Rename across the codebase | Rename one public name everywhere | `rename <module>.<old> <module>.<new>` |
| Move files | Move a module into a package, keeping an import shim at the old path if callers need it | `move <old.module> <new.module>`, or `add <new.module>*` with a shim |
| Remove dead code | Delete one unreachable function or branch at a time | `remove <module>.<name>` for a public one |
| Simplify tangled logic | One function per step: flatten nesting, split a long function into private helpers | nothing |
| Replace a deprecated API | One call site pattern per step, same arguments, same results | nothing, unless its signature is public |
| Modernise syntax | One kind of syntax per step (f-strings, dataclass, `pathlib`) | nothing; `change <name>` only where a signature has to change, like a default that becomes keyword-only |

With `--interface same`, only `add` rules are accepted: nothing that exists may change or disappear. A rename or move the user asked for needs `--interface planned`. With `--interface free` (internal code), differences are reported and never refused.

`rename` and `move` carry through: the script applies them to the code of test files (a step may change tests only by exactly that), pins, recorded runs' drivers and probes. It changes names only, found by Python's tokenizer (or, in C-like languages, outside strings and comments): the dotted name; a bare use of the name; `x.name` when the name is a method or attribute of a class, or `x` is its module; and `from module import name`, with the module too for a move when it's the only name imported. Strings, comments, data files and recorded output are never changed, so a string key or a message that changes with the rename shows up as a difference. When a test refers to a renamed thing some other way (a string, `from a import moved, other`), the step has to change it by hand, and `candidate add --allow-protected` records that as a decision for the user.

## Good recorded runs

A recorded run is a command whose stdout, stderr, exit code and written files must be identical before and after every step. It's the check that catches what the tests miss, so aim it at the code in scope:

- **Its command line,** with the options that reach the code in scope, on an input that takes every branch you can see: the boundaries (a value exactly at each `>=`), empty and odd input, and an error path or two (a bad argument, a missing file, a malformed line).
- **A driver script for a library:** a small Python (or Node.js) script you write in your own scratch space that imports the code in scope and prints the result of each public function on a few inputs. Give it with `--give`; the command reaches it as `../scratch/<name>`. Print `repr()` of results and of exceptions, so types and messages show. Start it with `sys.path.insert(0, os.getcwd())` (the script's own folder is the scratch folder, not the project).
- **What it writes:** a command that writes files has them compared too, in the project copy or the scratch folder.
- **Deterministic:** the same output on every run of the same code, in any copy. Times, durations, the copy's folder, UUIDs and object addresses (`<X object at 0x...>`) are masked already; anything else that varies (a seed, a hostname, a count of something on the machine) needs `--mask '<regex>'` or a driver that doesn't print it. A mask that swallows most of the output makes the run worthless.
- **Quick:** each recorded run is rerun for every step and for every probe pin. A few seconds each is plenty.

`--exact` turns the default masks off (the copy's folder is still masked). Use it when the code in scope formats times or durations itself: the default masks would hide a change to them. Line endings, spaces and blank lines are never masked.

## The checks in one place

| Check | Run by | Counts when |
|---|---|---|
| Baseline | `baseline` | the tests pass 3 of 3 on the original code in a fresh copy (tests that flip are left out, and listed) |
| Recorded run | `golden add`, then every `verify`, `probe` and the final check | the same stdout, stderr, exit code and files as on the original code |
| Interface | `surface record`, then every `verify` and the final check | the same entries, apart from the plan's allowed changes for the steps so far |
| Pin | `pin check` | it passes 3 of 3 on the original code and fails on at least one fault planted in the code it pins |
| Step | `verify` | test files changed only by planned renames, then all of the above, in one fresh copy |
| Probe | `probe` | the same output on the original code and with the steps applied, each side in its own fresh copy; 3 such probes per step (6 thorough) |
| Branch | `land` | all of the above again, on the committed branch, in a fresh copy |
