# -*- coding: utf-8 -*-
"""Minimal s-expression reader for KiCad files.

Pure Python, no FreeCAD imports, so it can be unit tested standalone.

``parse(text)`` returns nested lists of ``str``; e.g.::

    (footprint "X" (fp_line (start 0 0) (end 1 1)))

becomes::

    ['footprint', 'X', ['fp_line', ['start', '0', '0'], ['end', '1', '1']]]
"""

__all__ = ["SExprError", "parse", "child", "children", "value", "num", "nums"]


class SExprError(Exception):
    """Raised when the input is not a well formed s-expression."""


_DELIMS = set('()" \t\r\n')


def _tokenize(src):
    tokens = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c == "(" or c == ")":
            tokens.append(c)
            i += 1
        elif c == '"':
            # Copy unescaped runs in one slice; long strings (embedded
            # footprint sources, board-solid mesh data) stay linear-time.
            j = i + 1
            buf = []
            while True:
                quote = src.find('"', j)
                if quote < 0:
                    raise SExprError("Unterminated string literal")
                slash = src.find("\\", j, quote)
                if slash < 0:
                    buf.append(src[j:quote])
                    j = quote
                    break
                buf.append(src[j:slash])
                if slash + 1 >= n:
                    raise SExprError("Unterminated string literal")
                esc = src[slash + 1]
                buf.append({"n": "\n", "r": "\r", "t": "\t"}.get(esc, esc))
                j = slash + 2
            tokens.append(("atom", "".join(buf)))
            i = j + 1
        elif c.isspace():
            i += 1
        elif c == "#" or (c == ";" and src[i:i + 2] == ";;"):
            while i < n and src[i] != "\n":
                i += 1
        else:
            j = i
            while j < n and src[j] not in _DELIMS:
                j += 1
            tokens.append(("atom", src[i:j]))
            i = j
    return tokens


def parse(src):
    """Parse the first top level s-expression found in *src*."""
    tokens = _tokenize(src)
    pos = 0

    # skip a possible BOM / stray atoms before the first '('
    while pos < len(tokens) and tokens[pos] != "(":
        pos += 1
    if pos >= len(tokens):
        raise SExprError("No s-expression found in input")

    stack = []
    current = None
    for tok in tokens[pos:]:
        if tok == "(":
            new = []
            if current is not None:
                current.append(new)
                stack.append(current)
            current = new
        elif tok == ")":
            if current is None:
                raise SExprError("Unexpected ')'")
            if stack:
                current = stack.pop()
            else:
                return current
        else:
            if current is None:
                raise SExprError("Atom outside of any list")
            current.append(tok[1])
    raise SExprError("Unbalanced parentheses")


# ---------------------------------------------------------------------------
# small helpers used all over the footprint parser
# ---------------------------------------------------------------------------
def child(node, name):
    """First direct sub-list of *node* whose head is *name* (or None)."""
    if not isinstance(node, list):
        return None
    for c in node:
        if isinstance(c, list) and c and c[0] == name:
            return c
    return None


def children(node, name):
    """All direct sub-lists of *node* whose head is *name*."""
    if not isinstance(node, list):
        return []
    return [c for c in node if isinstance(c, list) and c and c[0] == name]


def value(node, name, default=None, index=1):
    """``(name v)`` -> ``v``."""
    c = child(node, name)
    if c is None or len(c) <= index:
        return default
    v = c[index]
    return v if isinstance(v, str) else default


def num(v, default=None):
    """Tolerant float conversion."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def nums(node, start=1):
    """Every numeric atom of *node* starting at *start*."""
    out = []
    if not isinstance(node, list):
        return out
    for v in node[start:]:
        if isinstance(v, str):
            f = num(v)
            if f is not None:
                out.append(f)
    return out


def has_flag(node, name):
    """True for both the legacy bare flag and the new ``(name yes)`` form."""
    if not isinstance(node, list):
        return False
    if name in node:
        return True
    c = child(node, name)
    return bool(c) and len(c) > 1 and c[1] in ("yes", "true")
