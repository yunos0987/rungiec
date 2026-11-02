"""Checks and normalization of operands and ST text for RUNGIEC output.

An operand (contact operand, argument value, instance name ...) and the text of an inline ST element are checked
so that the RUNGIEC text expands with jiepp and passes tools/lint_rungiec.py:

- keywords (lint_rungiec.KEYWORDS) are lowercased outside strings and comments (LN9),
- no comment, directive brace, '$', unbalanced bracket or top-level comma in an operand,
- no name that jiepp would expand (public macros, settings, internal names) or that clashes with the work variables,
- no non-ASCII character outside strings and comments.

Each check returns a list of issues (reason, detail); the caller picks the first reason by precedence.
"""

import os
import re

from . import TOOLS_DIR  # noqa: F401  (puts tools/ on sys.path)
import lint_rungiec

KEYWORDS = lint_rungiec.KEYWORDS
IDENT_RE = re.compile(r"(?<![\w#])([A-Za-z_]\w*)")
WORD_RE = re.compile(r"[A-Za-z_]\w*")
PLAIN_IDENT_RE = re.compile(r"[A-Za-z_]\w*\Z")
# An instance name: a variable path (members and subscripts).
VAR_PATH_RE = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*|\[[^\[\]]*\])*\Z")


def _macro_names():
    """The macro names defined by the library of the tree that holds lint_rungiec (jiepp would expand them): the
    entry file rungiec.iec and both mode bodies (rungiec_st.iec, rungiec_ld.iec). A file that cannot be read
    raises OSError (import fails) instead of falling back to the public names."""
    names = set(lint_rungiec.PUBLIC_NAMES) | set(lint_rungiec.CONFIG_NAMES)
    for name in (lint_rungiec.ENTRY_FILE, lint_rungiec.ST_FILE, lint_rungiec.LD_FILE):
        src = lint_rungiec.Source(name, lint_rungiec.read(os.path.join(lint_rungiec.ROOT, name)))
        defines, _ = src.defines()
        names.update(define[1] for define in defines)
    return frozenset(names)


MACRO_NAMES = _macro_names()


class ScanError(Exception):
    pass


def segments(text):
    """Split ST text into [(kind, text)] with kind 'code', 'str' or 'comment'. Raise ScanError when a string or a
    comment is not terminated."""
    out = []
    n = len(text)
    i = 0
    start = 0

    def flush(end):
        if end > start:
            out.append(("code", text[start:end]))

    while i < n:
        c = text[i]
        if c in "'\"":
            flush(i)
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == "$" else 1
            if j >= n:
                raise ScanError("unterminated string")
            out.append(("str", text[i:j + 1]))
            i = start = j + 1
        elif text.startswith("(*", i):
            flush(i)
            j = text.find("*)", i + 2)
            if j < 0:
                raise ScanError("unterminated comment")
            out.append(("comment", text[i:j + 2]))
            i = start = j + 2
        elif text.startswith("/*", i):
            flush(i)
            j = text.find("*/", i + 2)
            if j < 0:
                raise ScanError("unterminated comment")
            out.append(("comment", text[i:j + 2]))
            i = start = j + 2
        elif text.startswith("//", i):
            flush(i)
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append(("comment", text[i:j]))
            i = start = j
        else:
            i += 1
    flush(n)
    return out


def comment_body(comment):
    if comment.startswith("//"):
        return comment[2:].strip()
    return comment[2:-2].strip()


def lower_keywords(code):
    return IDENT_RE.sub(lambda m: m.group(1).lower() if m.group(1).lower() in KEYWORDS else m.group(1), code)


def _balanced(code):
    stack = []
    pairs = {")": "(", "]": "["}
    for c in code:
        if c in "([":
            stack.append(c)
        elif c in ")]":
            if not stack or stack[-1] != pairs[c]:
                return False
            stack.pop()
    return not stack


def _top_level_comma(code):
    depth = 0
    for c in code:
        if c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        elif c == "," and depth == 0:
            return True
    return False


def name_issue(word):
    """Return a detail string if the identifier clashes with RUNGIEC or jiepp names, else None."""
    low = word.lower()
    if word in MACRO_NAMES:
        return "name %s is a RUNGIEC macro" % word
    if low.startswith("rungiec") or low.startswith("_rungiec"):
        return "name %s is reserved by RUNGIEC" % word
    if word.startswith("__") or low.startswith("_jiepp"):
        return "name %s is reserved by jiepp" % word
    return None


def _non_ascii(s):
    return any(ord(c) > 127 for c in s)


def _code_checks(code_parts, issues, what):
    code = " ".join(code_parts)
    if _non_ascii(code):
        issues.append(("non-ascii", "non-ASCII character in %s" % what))
    for m in WORD_RE.finditer(code):
        detail = name_issue(m.group(0))
        if detail:
            issues.append(("name-clash", detail))
    return code


def normalize_code(text):
    """Lowercase keywords and collapse whitespace outside strings and comments (comments are kept as is)."""
    parts = []
    for kind, s in segments(text):
        if kind == "code":
            parts.append(lower_keywords(re.sub(r"\s+", " ", s)))
        else:
            parts.append(s)
    return "".join(parts).strip()


def check_operand(text, what="operand"):
    """Return (normalized text, issues). The text is an expression (no comments, one argument)."""
    issues = []
    if text is None or not text.strip():
        return "", [("operand", "empty %s" % what)]
    if "{" in text or "}" in text:
        issues.append(("operand", "brace in %s %r" % (what, text[:40])))
    if "$" in text:
        issues.append(("operand", "'$' in %s %r" % (what, text[:40])))
    if ";" in text:
        issues.append(("operand", "';' in %s %r" % (what, text[:40])))
    try:
        segs = segments(text)
    except ScanError as exc:
        return text, issues + [("operand", "%s in %s %r" % (exc, what, text[:40]))]
    if any(kind == "comment" for kind, _ in segs) or "*)" in text:
        issues.append(("operand", "comment in %s %r" % (what, text[:40])))
    _code_checks([s for kind, s in segs if kind == "code"], issues, what)
    masked = "".join(s if kind == "code" else " " for kind, s in segs)
    if not _balanced(masked):
        issues.append(("operand", "unbalanced brackets in %s %r" % (what, text[:40])))
    elif _top_level_comma(masked):
        issues.append(("operand", "top-level comma in %s %r" % (what, text[:40])))
    return normalize_code(text), issues


def check_name(text, what, path=False):
    """A function name, pin name or instance name: an identifier (or a variable path), not a keyword."""
    norm, issues = check_operand(text, what)
    regex = VAR_PATH_RE if path else PLAIN_IDENT_RE
    if not issues and not regex.match(norm):
        issues.append(("operand", "%s %r is not an identifier" % (what, text[:40])))
    elif not issues and not path and norm.lower() in KEYWORDS:
        issues.append(("operand", "%s %r is a keyword" % (what, text[:40])))
    return norm, issues


RETURN_RE = re.compile(r"\breturn\b", re.IGNORECASE)


def code_has_return(code):
    """True if the code text (strings and comments already removed) has the word return."""
    return RETURN_RE.search(code) is not None


def has_return(text):
    """True if the ST text has the word return in its code (not in a string or a comment). The same test as the
    st-text issue of check_st. A text that cannot be scanned (unterminated string or comment) is searched as a whole:
    it is skipped by check_st anyway, and matching more only skips more."""
    try:
        segs = segments((text or "").replace("\r\n", "\n"))
    except ScanError:
        return code_has_return(text or "")
    return code_has_return(" ".join(s for kind, s in segs if kind == "code"))


def check_st(text):
    """Inline ST text -> (normalized one-line statements, [comment texts], issues)."""
    issues = []
    comments = []
    if text is None:
        text = ""
    try:
        segs = segments(text.replace("\r\n", "\n"))
    except ScanError as exc:
        return "", [], [("st-text", str(exc))]
    code_parts = []
    for kind, s in segs:
        if kind == "comment":
            body = comment_body(s)
            if body:
                comments.append(body)
        elif kind == "code":
            code_parts.append(s)
    masked = "".join(s if kind == "code" else (" " if kind == "comment" else "''") for kind, s in segs)
    full = "".join(s for kind, s in segs if kind != "comment")
    if "{" in full or "}" in full:
        issues.append(("st-text", "brace in the ST text"))
    if "$" in full:
        issues.append(("st-text", "'$' in the ST text"))
    if not _balanced(masked):
        issues.append(("st-text", "unbalanced brackets in the ST text"))
    code = " ".join(code_parts)
    if _non_ascii(code):
        issues.append(("non-ascii", "non-ASCII character in the ST text"))
    if code_has_return(code):
        issues.append(("st-text", "return in the ST text"))
    for m in re.finditer(r"\b([A-Za-z_]\w*)\s*\(", code):
        if m.group(1) in lint_rungiec.PUBLIC_NAMES:
            issues.append(("st-text", "DSL macro %s in the ST text" % m.group(1)))
    for m in WORD_RE.finditer(code):
        word = m.group(0)
        low = word.lower()
        if low.startswith("rungiec") or low.startswith("_rungiec"):
            issues.append(("st-text", "Rungiec name %s in the ST text" % word))
        elif word in lint_rungiec.PUBLIC_NAMES and re.match(r"\s*\(", code[m.end():]):
            pass  # a DSL macro call: st-text above
        elif low in KEYWORDS:
            pass  # return: st-text above
        elif name_issue(word):
            issues.append(("name-clash", name_issue(word)))
    # One line: collapse whitespace outside strings.
    parts = []
    for kind, s in segs:
        if kind == "str":
            parts.append(s)
        elif kind == "comment":
            parts.append(" ")
        else:
            parts.append(lower_keywords(re.sub(r"\s+", " ", s)))
    norm = _collapse_outside_strings(parts, segs)
    if not norm.strip().strip(";").strip():
        issues.append(("st-text", "no statements in the ST text"))
    return norm, comments, issues


def _collapse_outside_strings(parts, segs):
    out = []
    for (kind, _), s in zip(segs, parts):
        if kind == "str":
            out.append(s)
        else:
            out.append(re.sub(r"\s+", " ", s))
    text = "".join(out)
    # Collapse runs of spaces that came from adjacent code pieces (outside strings only).
    result = []
    for kind, s in segments(text):
        result.append(s if kind == "str" else re.sub(r" {2,}", " ", s))
    return "".join(result).strip()


def canon(text):
    """Canonical form of an operand or ST text for comparison: keywords lowercased, whitespace removed outside
    strings, comments dropped."""
    if text is None:
        return ""
    try:
        segs = segments(text)
    except ScanError:
        return re.sub(r"\s+", "", text)
    out = []
    for kind, s in segs:
        if kind == "str":
            out.append(s)
        elif kind == "code":
            out.append(lower_keywords(re.sub(r"\s+", "", s)))
    return "".join(out)


def comment_text(text):
    """A comment for RUNGIEC output: one line, no comment delimiters, no directive braces."""
    t = re.sub(r"\s+", " ", text or "").strip()
    t = t.replace("(*", "( *").replace("*)", "* )").replace("{", "{ ")
    if t.startswith("@"):
        t = "@ " + t[1:]  # not a fragment marker or a lint directive
    return t


def canon_comment(texts):
    """Canonical form of the comments of a rung (whitespace removed, delimiters undone)."""
    joined = "".join(re.sub(r"\s+", "", t or "") for t in texts)
    return joined
