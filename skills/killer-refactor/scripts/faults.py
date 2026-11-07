#!/usr/bin/env python3
"""Plant small faults in source code, for killer-refactor's pin checks.

The same idea as killer-tests' mutation testing (its mutants.py), cut down to what a pin
check needs: a handful of one-token faults in the code a pinning test claims to cover. A
pin that still passes with every one of them planted doesn't pin anything.

Python is read with its own parser. The faults are comparisons (< to <=, == to !=),
arithmetic (+ to -, * to /), `and` to `or`, True to False, and whole numbers (0 to 1, 1 to
0, n to n + 1); only where the result still compiles, never inside strings, comments or
f-strings, and never in an `if __name__ == "__main__":` block. Other languages get the
same operators, found in text whose strings and comments are blanked out first, with
spaces either side of an operator so a pointer or a generic isn't taken for one.

Standard library only. refactor.py loads it by path. On its own:
  python faults.py <file> [<first line>-<last line>]   lists the faults it would plant
"""
from __future__ import annotations

import ast
import io
import re
import sys
import tokenize
import warnings
from pathlib import Path

PY = {".py", ".pyw"}
CLIKE = {".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".mts", ".cts", ".go", ".rs", ".java", ".cs", ".c", ".h",
         ".cc", ".cpp", ".cxx", ".hpp", ".kt", ".swift", ".scala", ".dart", ".php"}
MASK = "\x01"

PY_CMP = {"<": "<=", "<=": "<", ">": ">=", ">=": ">", "==": "!=", "!=": "=="}
PY_ARITH = {"+": "-", "-": "+", "*": "/", "/": "*"}
PY_AUG = {"+=": "-=", "-=": "+="}
MAIN_GUARD = re.compile(r"__name__ == ['\"]__main__['\"]|['\"]__main__['\"] == __name__")
CL_CMP = {"<": "<=", "<=": "<", ">": ">=", ">=": ">", "==": "!=", "!=": "==", "===": "!==", "!==": "==="}
CL_ARITH = {"+": "-", "-": "+", "*": "/", "/": "*", "+=": "-=", "-=": "+="}
SPACED = r"(?<=\s)({})(?=\s)"
CL_RULES = [
    ("comparison", re.compile(SPACED.format(r"===|!==|==|!=|<=|>=|<|>")), CL_CMP),
    ("boolean", re.compile(SPACED.format(r"&&|\|\|")), {"&&": "||", "||": "&&"}),
    ("boolean", re.compile(r"(?<![\w$.])(true|false)(?![\w$])"), {"true": "false", "false": "true"}),
    ("arithmetic", re.compile(SPACED.format(r"\+=|-=|\+|-|\*|/")), CL_ARITH),
    ("constant", re.compile(r"(?<![\w$.])(\d+)(?![\w.])"), None),
]
CL_SKIP = re.compile(r"\s*(import|package|using|use|#\s*include|require|export\s+\*)\b")
CL_FUNC = re.compile(r"(?:function\*?|func(?:\s*\([^)]*\))?|fn|def)\s+([A-Za-z_$][\w$]*)"
                     r"|([A-Za-z_$][\w$]*)\s*(?:=\s*(?:async\s*)?\([^)]*\)\s*=>|\([^;{]*\)[^;{]*\{\s*$)")
CL_NOT_FUNC = {"if", "for", "while", "switch", "catch", "return", "else", "do", "try", "with", "new", "typeof", "sizeof"}


def lang_of(path: str | Path) -> str | None:
    ext = Path(str(path)).suffix.lower()
    return "python" if ext in PY else "clike" if ext in CLIKE else None


def split_lines(text: str) -> list[str]:
    """Lines with their endings, split on \\n only (as Python's tokenizer counts them)."""
    parts = text.split("\n")
    return [p + "\n" for p in parts[:-1]] + ([parts[-1]] if parts[-1] else [])


def bare(line: str) -> str:
    return line[:-2] if line.endswith("\r\n") else line[:-1] if line.endswith("\n") else line


def _compiles(text: str) -> bool:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            compile(text, "<fault>", "exec", dont_inherit=True)
        return True
    except (SyntaxError, ValueError, OverflowError, RecursionError, MemoryError):
        return False


def _parse(text: str):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return ast.parse(text)
    except (SyntaxError, ValueError):
        return None


# ---------------------------------------------------------------- functions: where a fault is, by name

def functions(text: str, lang: str | None) -> list[dict]:
    """[{name, start, end}] for each function (methods as Class.method), first line to last."""
    if lang == "python":
        tree = _parse(text)
        if tree is None:
            return []
        out = []

        def visit(node, prefix):
            for ch in ast.iter_child_nodes(node):
                if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    start = min([ch.lineno] + [d.lineno for d in ch.decorator_list])
                    out.append({"name": prefix + ch.name, "start": start, "end": ch.end_lineno or ch.lineno})
                    visit(ch, prefix + ch.name + ".")
                elif isinstance(ch, ast.ClassDef):
                    visit(ch, prefix + ch.name + ".")
                else:
                    visit(ch, prefix)
        visit(tree, "")
        return out
    if lang == "clike":
        masked = mask_clike(text)
        lines = split_lines(masked)
        out = []
        for i, ln in enumerate(lines):
            m = CL_FUNC.search(bare(ln))
            name = m and (m.group(1) or m.group(2))
            if not name or name in CL_NOT_FUNC or re.match(r"\s*(if|for|while|switch|catch|else)\b", ln):
                continue
            end = _brace_end(lines, i)
            if end is not None:
                out.append({"name": name, "start": i + 1, "end": end + 1})
        return out
    return []


def _brace_end(lines: list[str], i: int) -> int | None:
    depth, opened = 0, False
    for j in range(i, min(len(lines), i + 2000)):
        for ch in lines[j]:
            if ch == "{":
                depth, opened = depth + 1, True
            elif ch == "}":
                depth -= 1
                if opened and depth == 0:
                    return j
        if not opened and j > i:
            return None
    return None


# ---------------------------------------------------------------- Python: the parser for meaning, tokens for positions

def _ccol(lines: list[str], row: int, bcol: int) -> int:
    """ast counts columns in UTF-8 bytes; tokenize and slicing count characters."""
    s = lines[row - 1]
    if s.isascii():
        return bcol
    return len(s.encode("utf-8", "surrogateescape")[:bcol].decode("utf-8", "surrogateescape"))


def _is_text(node) -> bool:
    return isinstance(node, ast.JoinedStr) or (isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)))


def py_faults(text: str) -> list[dict]:
    tree = _parse(text)
    if tree is None or re.search(r"\r(?!\n)", text):
        return []
    lines = split_lines(text)
    toks: dict[int, list] = {}
    try:
        for t in tokenize.generate_tokens(io.StringIO(text).readline):
            if t.type in (tokenize.OP, tokenize.NAME, tokenize.NUMBER):
                toks.setdefault(t.start[0], []).append(t)
    except (tokenize.TokenError, SyntaxError):
        return []
    parents = {ch: node for node in ast.walk(tree) for ch in ast.iter_child_nodes(node)}
    skip: set[int] = set()
    fspans = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            fspans.append(((node.lineno, _ccol(lines, node.lineno, node.col_offset)),
                           (node.end_lineno, _ccol(lines, node.end_lineno, node.end_col_offset))))
        if isinstance(node, ast.If) and MAIN_GUARD.fullmatch(ast.unparse(node.test)):
            skip.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))

    def pos(node, end=False):
        row = node.end_lineno if end else node.lineno
        return row, _ccol(lines, row, node.end_col_offset if end else node.col_offset)

    found = []

    def add(t, new, op):
        row = t.start[0]
        if row in skip or any(a <= t.start < b for a, b in fspans):
            return
        found.append((row, t.start[1], t.end[1], new, op, f"{t.string} → {new}"))

    def between(a, b, strings):
        for row in range(a[0], b[0] + 1):
            for t in toks.get(row, ()):
                if t.start >= a and t.end <= b and t.string in strings:
                    return t
        return None

    def at(p, strings=None, kind=None):
        return next((t for t in toks.get(p[0], ()) if t.start == p
                     and (strings is None or t.string in strings) and (kind is None or t.type == kind)), None)

    def top_constant(node) -> bool:
        p = parents.get(node)
        while isinstance(p, (ast.UnaryOp, ast.Tuple, ast.List)):
            p = parents.get(p)
        return isinstance(p, (ast.Assign, ast.AnnAssign)) and isinstance(parents.get(p), ast.Module)

    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            prev = node.left
            for nxt in node.comparators:
                t = between(pos(prev, True), pos(nxt), PY_CMP)
                if t:
                    add(t, PY_CMP[t.string], "comparison")
                prev = nxt
        elif isinstance(node, ast.BoolOp):
            for x, y in zip(node.values, node.values[1:]):
                t = between(pos(x, True), pos(y), {"and", "or"})
                if t:
                    add(t, "or" if t.string == "and" else "and", "boolean")
        elif isinstance(node, ast.BinOp) and type(node.op) in (ast.Add, ast.Sub, ast.Mult, ast.Div):
            if not (_is_text(node.left) or _is_text(node.right)):
                t = between(pos(node.left, True), pos(node.right), PY_ARITH)
                if t:
                    add(t, PY_ARITH[t.string], "arithmetic")
        elif isinstance(node, ast.AugAssign) and type(node.op) in (ast.Add, ast.Sub):
            t = between(pos(node.target, True), pos(node.value), PY_AUG)
            if t:
                add(t, PY_AUG[t.string], "arithmetic")
        elif isinstance(node, ast.Constant) and isinstance(node.value, bool):
            t = at(pos(node), {"True", "False"})
            if t:
                add(t, "False" if t.string == "True" else "True", "boolean")
        elif isinstance(node, ast.Constant) and type(node.value) is int and not top_constant(node):
            t = at(pos(node), kind=tokenize.NUMBER)
            if t and re.fullmatch(r"0|[1-9][0-9_]*", t.string):   # decimal only: hex and octal are masks
                n = int(t.string.replace("_", ""))
                add(t, "1" if n == 0 else "0" if n == 1 else str(n + 1), "constant")

    out, seen = [], set()
    for row, c0, c1, new, op, detail in sorted(found):
        line = bare(lines[row - 1])
        mutated = line[:c0] + new + line[c1:]
        if mutated == line or (row, mutated) in seen:
            continue
        seen.add((row, mutated))
        ending = lines[row - 1][len(line):]
        if not _compiles("".join(lines[:row - 1]) + mutated + ending + "".join(lines[row:])):
            continue
        out.append({"line": row, "col": c0, "op": op, "detail": detail, "original": line, "mutated": mutated})
    return out


# ---------------------------------------------------------------- other languages: blank strings and comments, then match

def mask_clike(text: str) -> str:
    """Strings and comments blanked to MASK (line breaks kept), so operators in them aren't seen."""
    chars, n, i = list(text), len(text), 0

    def blank(a, b):
        for k in range(a, min(b, n)):
            if chars[k] not in "\r\n":
                chars[k] = MASK
    while i < n:
        c = text[i]
        if text.startswith("//", i):
            k = text.find("\n", i)
            k = n if k < 0 else k
            blank(i, k)
            i = k
        elif text.startswith("/*", i):
            k = text.find("*/", i + 2)
            k = n if k < 0 else k + 2
            blank(i, k)
            i = k
        elif c in "\"'`":
            j = i + 1
            while j < n and text[j] != c and (c == "`" or text[j] != "\n"):
                j += 2 if text[j] == "\\" else 1
            blank(i, j + 1)
            i = j + 1
        else:
            i += 1
    return "".join(chars)


def clike_faults(text: str) -> list[dict]:
    lines, masked = split_lines(text), split_lines(mask_clike(text))
    out, seen = [], set()
    for idx, (line, mline) in enumerate(zip(lines, masked)):
        line, code = bare(line), bare(mline)
        if not code.strip(MASK + " \t") or CL_SKIP.match(code) or code.lstrip().startswith(("#", "@")):
            continue
        for op, rx, table in CL_RULES:
            for m in rx.finditer(code):
                tok = m.group(1)
                if table is None:
                    if len(tok) > 1 and tok.startswith("0"):
                        continue
                    k = int(tok)
                    new = "1" if k == 0 else "0" if k == 1 else str(k + 1)
                else:
                    new = table.get(tok)
                if new is None:
                    continue
                mutated = line[:m.start(1)] + new + line[m.end(1):]
                if mutated != line and (idx + 1, mutated) not in seen:
                    seen.add((idx + 1, mutated))
                    out.append({"line": idx + 1, "col": m.start(1), "op": op, "detail": f"{tok} → {new}",
                                "original": line, "mutated": mutated})
    return sorted(out, key=lambda f: (f["line"], f["col"]))


# ---------------------------------------------------------------- the interface refactor.py uses

def find(text: str, lang: str | None, lines: set[int] | None = None) -> list[dict]:
    """Every fault in text (only on the given line numbers, if any)."""
    found = py_faults(text) if lang == "python" else clike_faults(text) if lang == "clike" else []
    return [f for f in found if lines is None or f["line"] in lines]


def _spread(n: int) -> list[int]:
    """0 .. n-1 in an order that covers the range early: 0, n/2, then the quarters, and so on."""
    order, seen, step = [], set(), n
    while len(order) < n:
        for i in range(0, n, max(1, step)):
            if i not in seen:
                seen.add(i)
                order.append(i)
        step //= 2
    return order


def sample(found: list[dict], n: int) -> list[dict]:
    """Up to n faults, spread over the kinds and the lines, the same every time."""
    if n <= 0 or not found:
        return []
    kinds: dict[str, list[dict]] = {}
    for f in sorted(found, key=lambda f: (f["line"], f["col"])):
        kinds.setdefault(f["op"], []).append(f)
    queues = [[fs[i] for i in _spread(len(fs))] for fs in kinds.values()]
    out = []
    while len(out) < n and any(queues):
        for q in queues:
            if q and len(out) < n:
                out.append(q.pop(0))
    return out


def plant(text: str, fault: dict) -> str:
    """text with the fault in: its one line replaced, the line ending kept."""
    lines = split_lines(text)
    row = fault["line"] - 1
    if row >= len(lines) or bare(lines[row]) != fault["original"]:
        raise ValueError(f"line {fault['line']} isn't what the fault was made from")
    lines[row] = fault["mutated"] + lines[row][len(bare(lines[row])):]
    return "".join(lines)


def main(argv=None) -> None:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return
    path = Path(args[0])
    text = path.read_bytes().decode("utf-8", "surrogateescape")
    lines = None
    if len(args) > 1:
        a, _, b = args[1].partition("-")
        lines = set(range(int(a), int(b or a) + 1))
    found = find(text, lang_of(path), lines)
    for f in found:
        print(f"{path.name}:{f['line']} {f['op']}: {f['detail']}")
    print(f"{len(found)} faults")


if __name__ == "__main__":
    main()
