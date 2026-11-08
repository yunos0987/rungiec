#!/usr/bin/env python3
"""Lint for the RUNGIEC library, its tests and its examples (rules LN0..LN25, see docs/DESIGN.md "lint の規則").

Usage:
    python tools/lint_rungiec.py              lint the repository
    python tools/lint_rungiec.py --selftest   check that every rule fires on a bad fragment and stays quiet on a good one

Each finding is printed as 'LINT LNxx path:line: message'. The last line is 'LINT: OK' (exit code 0) or
'LINT: NG (N findings)' (exit code 1).

The rules check only what jiepp, jiecc, the run-time traps of rungiec_st.iec, 'gen_arity_table.py --check' and the
static checks of tools/run_tests.py do not stop. A file that contains an error on purpose (a test) carries
'(* @lint-allow LNxx[, LNyy] *)': the listed rules are not reported for that file. LN0 (the lint itself:
an unparsable define, an unknown rule in @lint-allow, a @lint-allow that suppresses nothing) cannot be
suppressed.

Files and rules:
    rungiec.iec and rungiec_st.iec       LN0 to LN9 (the library: the entry file and the ST mode body)
    rungiec_ld.iec                       LN7 (the set of public names that it defines; no other rule)
    tests/support/*.iec                  LN0, LN1, LN9
    tests/*.iec                          LN0, LN9 to LN19, LN21 to LN25
    examples/*.iec                       LN0, LN9 to LN18, LN21 to LN25
    docs/DESIGN.md and docs/EXAMPLES.md  LN20 (the test map and the example tables)
"""
import argparse
import bisect
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The library: the entry file defines the common part and includes a mode body at its end; only the bodies
# define the public macros. The entry file and the ST body are linted as the library (LN1 to LN9); of the LD
# body LN7 reads only the names that it defines.
ENTRY_FILE = "rungiec.iec"
ST_FILE = "rungiec_st.iec"
LD_FILE = "rungiec_ld.iec"
LIBRARY_FILES = (ENTRY_FILE, ST_FILE)
# The settings that only the LD body defines (besides the public names).
LD_SETTINGS = frozenset(("RUNGIEC_LD_FANIN_MAX",))
# A define line of the LD body (LN7 reads only the names).
LD_DEFINE_RE = re.compile(r"^\{#\s*define\s+(\w+)", re.MULTILINE)

# The function-like public macros (LN7 checks that rungiec_st.iec and rungiec_ld.iec each define exactly these).
PUBLIC_NAMES = frozenset((
    "RUNGIEC_VAR", "RUNGIEC_VAR_TEMP", "RUNGIEC_VAR_NOEDGE", "BEGIN_RUNGIEC", "END_RUNGIEC",
    "RUNG", "SERIES", "PARALLEL", "BEGIN_NET", "WIRE", "END_NET",
    "CONTACT", "NOT_CONTACT", "RISE_CONTACT", "FALL_CONTACT", "NOT_RISE_CONTACT", "NOT_FALL_CONTACT",
    "EQ_CONTACT", "NE_CONTACT", "GT_CONTACT", "GE_CONTACT", "LT_CONTACT", "LE_CONTACT",
    "COIL", "NOT_COIL", "SET_COIL", "RESET_COIL", "RISE_COIL", "FALL_COIL",
    "FB_SCAN", "FB_IN", "FB_BOX", "FB_EXEC", "FB_ENO", "FBT_SCAN", "FBT_IN", "FBT_BOX", "FBT_EXEC", "FBT_ENO",
    "FUN_EXEC", "FUN_EXEC_RET", "FUN_ENO", "FUN_ENO_RET", "ST_EXEC", "BEGIN_MC", "END_MC", "RETURN", "JMP", "LABEL",
))
# The object-like public macros: settings and version.
CONFIG_NAMES = frozenset((
    "RUNGIEC_NEST_MAX", "RUNGIEC_EDGE_MAX", "RUNGIEC_ARITY_MAX", "RUNGIEC_NODE_MAX", "RUNGIEC_MC_MAX",
    "RUNGIEC_VERSION_MAJOR", "RUNGIEC_VERSION_MINOR", "RUNGIEC_VERSION_REVISION", "RUNGIEC_VERSION",
))
STATEMENT_MACROS = frozenset(("RUNG", "BEGIN_MC", "END_MC", "LABEL", "BEGIN_NET", "WIRE", "END_NET"))
DECL_MACROS = frozenset(("RUNGIEC_VAR", "RUNGIEC_VAR_TEMP", "RUNGIEC_VAR_NOEDGE", "BEGIN_RUNGIEC", "END_RUNGIEC"))
TOP_LEVEL_MACROS = STATEMENT_MACROS | DECL_MACROS
COIL_NAMES = frozenset(("COIL", "NOT_COIL", "SET_COIL", "RESET_COIL", "RISE_COIL", "FALL_COIL"))
WORK_VARS = frozenset((
    "RungiecEn", "RungiecIn", "RungiecAcc", "RungiecVal", "RungiecTmp", "RungiecRail", "RungiecSp",
    "RungiecStIn", "RungiecStAcc", "RungiecPrev", "RungiecNode", "RungiecI", "RungiecMcSt",
))
COUNTERS = frozenset((
    "rungiec_edge", "rungiec_net", "rungiec_node", "rungiec_mc", "rungiec_open", "rungiec_seg", "rungiec_pou",
    "rungiec_exit",
))
# The per-label counters of JMP and LABEL: the prefix is pasted to the label with _RUNGIEC_CAT_X and the pasted name
# reaches __counter_peek / __counter_set as a parameter of the define (LN5).
COUNTER_PREFIXES = frozenset(("rungiec_label_", "rungiec_jump_"))
# The edge contacts and transition coils: each call site keeps a previous value in RungiecPrev (LN23).
EDGE_NAMES = frozenset((
    "RISE_CONTACT", "FALL_CONTACT", "NOT_RISE_CONTACT", "NOT_FALL_CONTACT", "RISE_COIL", "FALL_COIL",
))
RULES = {
    "LN0": "the lint itself: unparsable define, unknown or unused @lint-allow",
    "LN1": "comment or directive in a define body, '$' in a library file",
    "LN2": "parameter used twice in the ST of a define body",
    "LN3": "operand of @@ that must go through _RUNGIEC_CAT_X",
    "LN4": "and/or assignment that is not one of the allowed shapes",
    "LN5": "work variable or preprocessor counter name",
    "LN6": "element or combinator body shape",
    "LN7": "macro naming and the set of public names",
    "LN8": "preprocessor condition and message",
    "LN9": "IEC keyword not in lower case",
    "LN10": "top-level call that is not a statement or declaration macro",
    "LN11": "';' after a top-level macro",
    "LN12": "PARALLEL mixing coils and non-coils",
    "LN13": "RETURN() or JMP() not the last child of a top-level RUNG or WIRE, or a WIRE after the WIRE with it",
    "LN14": "child that is not exactly one call",
    "LN15": "RUNGIEC_VAR() or RUNGIEC_VAR_TEMP() in a FUNCTION or METHOD",
    "LN16": "order of RUNGIEC_VAR*(), BEGIN_RUNGIEC(), END_RUNGIEC() and the DSL calls in a POU, and an st or end "
            "directive in a POU that uses the DSL",
    "LN17": "DSL macro, Rungiec name, return, jump, label or directive in ST_EXEC",
    "LN18": "comment inside DSL macro arguments",
    "LN19": "test file name, @expect kind and @cycles",
    "LN20": "test map and example tables of the documents",
    "LN21": "BEGIN_MC/END_MC or BEGIN_NET/END_NET split across branches",
    "LN22": "label of JMP/LABEL: not one name, a Rungiec name, or spelled in another letter case",
    "LN23": "edge contact or transition coil between LABEL(L) and a later JMP(L) (a backward-jump loop)",
    "LN24": "statement of the user before RUNGIEC_VAR*() or after END_RUNGIEC() in a POU that uses the DSL (one POU "
            "is in one language)",
    "LN25": "positional argument of a box call (the LD has named arguments only)",
}

# LN25: the number of leading name arguments of each box (instance or function, the type of FBT_*, the pins of
# *_IN and *_BOX, the dst of *_RET); the arguments after them are the arguments of the call.
BOX_NAME_ARGS = {
    "FB_SCAN": 1, "FB_IN": 2, "FB_BOX": 3, "FB_EXEC": 1, "FB_ENO": 1,
    "FBT_SCAN": 2, "FBT_IN": 3, "FBT_BOX": 4, "FBT_EXEC": 2, "FBT_ENO": 2,
    "FUN_EXEC": 1, "FUN_EXEC_RET": 2, "FUN_ENO": 1, "FUN_ENO_RET": 2,
}

# LN4: the only assignments (whitespace removed) whose right-hand side may use and/or in a define body.
AND_OR_ALLOWED = frozenset((
    "RungiecAcc:=RungiecAccorRungiecVal",
    "RungiecVal:=RungiecEnandRungiecTmp",
    "RungiecVal:=RungiecEnandnotRungiecTmp",
    "RungiecVal:=RungiecEnandRungiecTmpandnotRungiecPrev[n]",
    "RungiecVal:=RungiecEnandnotRungiecTmpandRungiecPrev[n]",
    "RungiecVal:=RungiecEnandnot(RungiecTmpandnotRungiecPrev[n])",
    "RungiecVal:=RungiecEnandnot(notRungiecTmpandRungiecPrev[n])",
    "x:=RungiecEnandnotRungiecPrev[n]",
    "x:=notRungiecEnandRungiecPrev[n]",
    "x:=RungiecRailandnotRungiecEn",
    "RungiecVal:=RungiecRailandinst.out_pin",
    "RungiecNode[to_node]:=RungiecNode[to_node]orRungiecVal",
))
# LN6: the combinators have one exact body each.
COMBINATOR_BODIES = {
    "_RUNGIEC_S": "RungiecEn := RungiecAcc; _RUNGIEC_CHILD(a); RungiecAcc := RungiecVal;",
    "_RUNGIEC_P": "RungiecEn := RungiecIn; _RUNGIEC_CHILD(a); RungiecAcc := RungiecAcc or RungiecVal;",
}
# LN9: IEC 61131-3 keywords (compared in lower case).
KEYWORDS = frozenset((
    "if", "then", "elsif", "else", "end_if", "case", "of", "end_case", "for", "to", "by", "do", "end_for",
    "while", "end_while", "repeat", "until", "end_repeat", "exit", "continue", "return",
    "and", "or", "xor", "not", "mod", "true", "false",
    "var", "var_input", "var_output", "var_in_out", "var_temp", "var_global", "var_external", "var_access",
    "end_var", "constant", "retain", "non_retain", "at",
    "program", "end_program", "function", "end_function", "function_block", "end_function_block",
    "method", "end_method", "configuration", "end_configuration", "resource", "end_resource",
    "task", "with", "type", "end_type", "struct", "end_struct", "array",
    "bool", "byte", "word", "dword", "lword", "sint", "int", "dint", "lint", "usint", "uint", "udint", "ulint",
    "real", "lreal", "time", "ltime", "date", "time_of_day", "date_and_time", "string", "wstring",
))

TEST_NAME_RE = re.compile(r"^[tefr]\d{3}_[a-z0-9]+(?:_[a-z0-9]+)*\.iec$")
TEST_PREFIX_EXPECT = {"t": "pass", "x": "pass", "e": "pp-error", "f": "run-error", "r": "residual"}
DEFINE_RE = re.compile(r"\{#define\s+([A-Za-z_]\w*)(\(([^()]*)\))?(?:\s+(.*?))?\s*\}\Z", re.DOTALL)
ALLOW_RE = re.compile(r"\(\*\s*@lint-allow\b(.*?)\*\)", re.DOTALL)
ASSIGN_START_RE = re.compile(r"[A-Za-z_]\w*(?:\[[^\]]*\])?\s*:=")
CALL_RE = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
CHECK_CALL_RE = re.compile(r"\b(?:__assert|__counter_set|_RUNGIEC_\w*_CHECKS?|_RUNGIEC_\w*_DONE)\s*\(")
LEADING_CHECK_RE = re.compile(
    r"(?:__assert|__counter_set|_RUNGIEC_EDGE_CHECK|_RUNGIEC_ARITY_CHECK|_RUNGIEC_\w+_CHECKS)\s*\(")
RUNGIEC_WORD_RE = re.compile(r"\brungiec[a-z]\w*", re.IGNORECASE)
COUNTER_USE_RE = re.compile(r"\b__counter(?:_peek|_set)?\s*\(\s*(\w+)")
PASTE_CALL_RE = re.compile(r"\b_RUNGIEC_CAT3?_X\s*\(")
PASTED_COUNTER_RE = re.compile(r"\b_RUNGIEC_CAT_X\s*\(\s*(rungiec_\w*)\s*,")
BARE_LOGIC_RE = re.compile(r"(?<![\\\w])(and|or|not)(?![\\\w])", re.IGNORECASE)
ESCAPED_LOGIC_RE = re.compile(r"\\(and|or|not)\\", re.IGNORECASE)
C_LOGIC = {"and": "&&", "or": "||", "not": "!"}


class Finding(object):
    __slots__ = ("rule", "label", "line", "message")

    def __init__(self, rule, label, line, message):
        self.rule, self.label, self.line, self.message = rule, label, line, message

    def text(self):
        return "LINT %s %s:%d: %s" % (self.rule, self.label, self.line, self.message)


class Source(object):
    """One .iec file: the text, line lookup and three masked copies of the same length.

    code   comments, strings and directives blanked (for finding calls and statements)
    nostr  only the contents of strings blanked (for finding comments and '{#' inside macro arguments)
    kw     comments, strings, directives except define bodies, and the define heads blanked (LN9)
    Blanking keeps the line breaks, so an offset in a mask is the same offset in the text.
    """

    def __init__(self, label, text):
        self.label = label
        self.text = text
        self.starts = [0] + [m.end() for m in re.finditer(r"\n", text)]
        self.directives = []  # (start, end): end is the index after the closing brace, None if unterminated
        self._scan()

    def line(self, pos):
        return bisect.bisect_right(self.starts, pos)

    def _scan(self):
        text = self.text
        n = len(text)
        code, nostr, kw = list(text), list(text), list(text)

        def blank(buf, a, b):
            for k in range(a, b):
                if buf[k] != "\n":
                    buf[k] = " "

        i = 0
        define_end = -1
        while i < n:
            c = text[i]
            if c == "(" and text.startswith("(*", i):
                j = text.find("*)", i + 2)
                j = n if j < 0 else j + 2
                blank(code, i, j)
                blank(kw, i, j)
                i = j
            elif c == "/" and text.startswith("//", i):
                j = text.find("\n", i)
                j = n if j < 0 else j
                blank(code, i, j)
                blank(kw, i, j)
                i = j
            elif c in "'\"":
                j = i + 1
                while j < n and text[j] != c and text[j] != "\n":
                    j += 2 if text[j] == "$" else 1
                j = min(j, n - 1) + 1
                blank(code, i, j)
                blank(kw, i, j)
                blank(nostr, i + 1, j - 1)
                i = j
            elif c == "{" and text.startswith("{#", i) and i > define_end:
                end = self._directive_end(i)
                stop = n if end is None else end
                head = re.match(r"\{#\s*define\s+[A-Za-z_]\w*(?:\([^()]*\))?", text[i:stop])
                self.directives.append((i, end))
                blank(code, i, stop)
                if head and end is not None:
                    # A define body stays visible in kw (and its comments and strings are scanned as usual).
                    blank(kw, i, i + head.end())
                    blank(kw, end - 1, end)
                    define_end = end - 1
                    i += head.end()
                else:
                    blank(kw, i, stop)
                    i = stop
            else:
                i += 1
        self.code = "".join(code)
        self.nostr = "".join(nostr)
        self.kw = "".join(kw)

    def _directive_end(self, start):
        """Return the index after the brace that closes the directive at start, or None."""
        depth = 0
        text = self.text
        for pos in range(start, len(text)):
            char = text[pos]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return pos + 1
        return None

    def defines(self):
        """Return [(line, name, params or None, body, start)] for the parsable defines and report the others."""
        result, bad = [], []
        for start, end in self.directives:
            if not re.match(r"\{#\s*define\b", self.text[start:start + 12]):
                continue
            if end is None:
                bad.append((self.line(start), "unterminated {#define} directive"))
                continue
            raw = self.text[start:end]
            found = DEFINE_RE.match(re.sub(r"\s*\n\s*", " ", raw))
            if not found:
                bad.append((self.line(start), "cannot parse this {#define} directive: %s"
                            % raw[:60].replace("\n", " ")))
                continue
            name, has_paren, params, body = found.groups()
            plist = None
            if has_paren:
                plist = [p.strip() for p in params.split(",") if p.strip()]
            result.append((self.line(start), name, plist, (body or "").strip(), start))
        return result, bad


def match_paren(text, open_pos):
    """text[open_pos] is '('. Return the index of the matching ')' or None (text is a mask without strings)."""
    depth = 0
    for pos in range(open_pos, len(text)):
        char = text[pos]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return pos
    return None


def split_args(text, open_pos, close_pos):
    """Return [(start, end)] of the arguments between text[open_pos] = '(' and text[close_pos] = ')'.

    Commas inside () and [] do not separate arguments (as in jiepp)."""
    args = []
    depth = 0
    start = open_pos + 1
    for pos in range(open_pos + 1, close_pos):
        char = text[pos]
        if char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        elif char == "," and depth == 0:
            args.append((start, pos))
            start = pos + 1
    args.append((start, close_pos))
    return args


def blank_calls(body, regex):
    """Return body with every call matched by regex (name and parenthesized arguments) replaced by spaces."""
    out = body
    for match in regex.finditer(body):
        close = match_paren(body, match.end() - 1)
        if close is None:
            continue
        out = out[:match.start()] + " " * (close + 1 - match.start()) + out[close + 1:]
    return out


def strip_leading_checks(body):
    """Remove the checks (expanding to nothing) at the start of a define body."""
    body = body.strip()
    while True:
        match = LEADING_CHECK_RE.match(body)
        if not match:
            return body
        close = match_paren(body, match.end() - 1)
        if close is None:
            return body
        body = body[close + 1:].strip()


# ---------------------------------------------------------------------------------------------------------
# Library rules (LN1 to LN9 on rungiec.iec and rungiec_st.iec; LN1 and LN9 on the helper files)
# ---------------------------------------------------------------------------------------------------------

def lint_library(src, defines, add, helper=False):
    """Apply LN1 to LN8 (helper: LN1 only) to src. defines is Source.defines()[0]."""
    match = re.search(r"\$", src.text)
    if match:
        add("LN1", src.line(match.start()), "'$' is not allowed in a library file")
    for line, name, params, body, _ in defines:
        for token, what in (("{#", "a directive '{#'"), ("//", "a comment '//'"), ("(*", "a comment '(*'")):
            if token in body:
                add("LN1", line, "define body of %s contains %s" % (name, what))
    if helper:
        return
    for line, name, params, body, _ in defines:
        lint_define(name, params, body, lambda rule, msg: add(rule, line, msg))
    lint_counters_and_vars(src, defines, add)
    lint_names(defines, add)
    lint_conditions(src, defines, add)


def lint_define(name, params, body, add):
    """LN2, LN3, LN4 and LN6 on one define of a library file."""
    st_part = blank_calls(body, CHECK_CALL_RE)
    # LN2: a parameter is used once in the ST part (checks are not counted, and neither is a use pasted into a name
    # with _RUNGIEC_CAT_X / _RUNGIEC_CAT3_X, such as the counter names of JMP and LABEL: it is not evaluated in ST).
    for param in params or ():
        word = "__VA_ARGS__" if param == "..." else param
        if not re.match(r"^[A-Za-z_]\w*$", word):
            continue
        if word == "n" and re.match(r"^_RUNGIEC_\w+_I$", name):
            continue
        if name == "WIRE" and word in ("from_node", "to_node"):
            continue
        # The instance of FB_BOX / FBT_BOX is called and read (inst.out_pin); it is a name, evaluated once each.
        if name in ("FB_BOX", "FBT_BOX") and word == "inst":
            continue
        count = len(re.findall(r"(?<!\w)%s(?!\w)" % re.escape(word), blank_calls(st_part, PASTE_CALL_RE)))
        if count > 1:
            add("LN2", "parameter %s of %s is used %d times in the ST of the body" % (word, name, count))
    # LN3: __VA_ARGC__ and the counters are not expanded as operands of @@.
    if re.search(r"@@\s*(__VA_ARGC__|__COUNTER__|__counter(_peek)?\s*\()", body) or re.search(
            r"(__VA_ARGC__|__COUNTER__|__counter(_peek)?\s*\([^()]*\))\s*@@", body):
        add("LN3", "%s pastes __VA_ARGC__, __COUNTER__ or a counter call with @@ (use _RUNGIEC_CAT_X)" % name)
    # LN4: and/or appear only in the allowed assignments.
    starts = list(ASSIGN_START_RE.finditer(st_part))
    for idx, match in enumerate(starts):
        stop = starts[idx + 1].start() if idx + 1 < len(starts) else len(st_part)
        rhs = st_part[match.end():stop]
        rhs = re.split(r";|\b(?:then|do|else|end_if|end_for)\b", rhs)[0]
        if re.search(r"\b(and|or|xor)\b", rhs):
            norm = re.sub(r"\s+", "", match.group(0) + rhs)
            if norm not in AND_OR_ALLOWED:
                add("LN4", "%s has an and/or assignment that is not an allowed shape: %s" % (name, norm))
    # LN6: the shape of the elements and the combinators.
    if name in COMBINATOR_BODIES:
        if params != ["a"] or body != COMBINATOR_BODIES[name]:
            add("LN6", "body of %s must be exactly: %s" % (name, COMBINATOR_BODIES[name]))
        return
    # _RUNGIEC_<X>_I is the body of the element X, or of the statement macro X (LABEL) that is not an element.
    impl = re.match(r"^_RUNGIEC_(\w+)_I$", name)
    is_element = (name in PUBLIC_NAMES and name not in STATEMENT_MACROS and name not in DECL_MACROS) or \
        name == "_RUNGIEC_POP" or (impl is not None and impl.group(1) not in STATEMENT_MACROS)
    if not is_element:
        return
    core = strip_leading_checks(body)
    if ":=" not in core:
        return  # a pure wrapper (it only calls other macros)
    if core.endswith(";"):
        add("LN6", "body of %s ends with ';' (the parent adds it)" % name)
    elif not ASSIGN_START_RE.match(core):
        add("LN6", "body of %s does not start with an assignment" % name)
    elif not ASSIGN_START_RE.match(core.rsplit(";", 1)[-1].strip()):
        add("LN6", "body of %s does not end with an assignment" % name)


def lint_counters_and_vars(src, defines, add):
    """LN5: work variable names and preprocessor counter names. A counter argument may also be a parameter of the
    define (the caller passes a per-label counter name pasted from one of COUNTER_PREFIXES)."""
    for match in RUNGIEC_WORD_RE.finditer(src.text):
        if match.group(0) not in WORK_VARS:
            add("LN5", src.line(match.start()), "%s is not one of the 13 work variables" % match.group(0))
    ends = dict(src.directives)

    def params_at(pos):
        for _, _, params, _, start in defines:
            if start <= pos < (ends.get(start) or start):
                return params or ()
        return ()

    for match in COUNTER_USE_RE.finditer(src.text):
        name = match.group(1)
        if name not in COUNTERS and name not in params_at(match.start()):
            add("LN5", src.line(match.start()), "counter %s is not one of the %d counters (%s)"
                % (name, len(COUNTERS), ", ".join(sorted(COUNTERS))))
    for match in PASTED_COUNTER_RE.finditer(src.text):
        if match.group(1) not in COUNTER_PREFIXES:
            add("LN5", src.line(match.start()), "pasted counter prefix %s is not one of %s"
                % (match.group(1), ", ".join(sorted(COUNTER_PREFIXES))))


def lint_names(defines, add):
    """LN7: rules 12 to 14 of AGENTS.md (prefixes, trailing '_', BEGIN_/END_ pairs)."""
    names = {}
    for line, name, params, _, _ in defines:
        names.setdefault(name, (line, params))
        if name.startswith("_"):
            if not name.startswith("_RUNGIEC_"):
                add("LN7", line, "internal macro %s must start with _RUNGIEC_" % name)
        elif name not in PUBLIC_NAMES and name not in CONFIG_NAMES:
            add("LN7", line, "%s is not a public name (PUBLIC_NAMES / CONFIG_NAMES of the lint); "
                             "start an internal macro with _RUNGIEC_" % name)
        elif name in PUBLIC_NAMES and params is None:
            add("LN7", line, "public macro %s must be function-like" % name)
        elif name in CONFIG_NAMES and params is not None:
            add("LN7", line, "setting %s must be object-like" % name)
        if name.endswith("_"):
            add("LN7", line, "%s ends with '_' (reserved for public macros that are not normally used)" % name)
        if not name.startswith("_") and re.search(r"_(BEGIN|END)(_|$)", name):
            add("LN7", line, "%s: put BEGIN_ / END_ at the start of the name" % name)
    for name, (line, _) in sorted(names.items()):
        for mine, other in (("BEGIN_", "END_"), ("END_", "BEGIN_")):
            if name.startswith(mine) and not name.startswith("_") and other + name[len(mine):] not in names:
                add("LN7", line, "%s has no %s" % (name, other + name[len(mine):]))


def lint_public_set(texts, add):
    """LN7 on the library as a whole and LN5 on the work variables. texts: {file name: text} for ENTRY_FILE,
    ST_FILE and LD_FILE; add(rule, label, line, message).

    LN7: rungiec_st.iec defines every public name and rungiec.iec none; rungiec.iec defines every config name
    and rungiec_st.iec none; the names that rungiec_ld.iec defines without the _RUNGIEC_ prefix are the public
    names and LD_SETTINGS (a public macro left out of the LD body would keep no definition in the LD mode).
    LN5: rungiec_st.iec declares the 13 work variables in _RUNGIEC_WORK_VARS / _RUNGIEC_EDGE_VARS."""
    entry = Source(ENTRY_FILE, texts[ENTRY_FILE].replace("\r\n", "\n")).defines()[0]
    st = Source(ST_FILE, texts[ST_FILE].replace("\r\n", "\n")).defines()[0]
    entry_names = {name for _, name, _, _, _ in entry}
    st_names = {name for _, name, _, _, _ in st}
    for line, name, _, _, _ in entry:
        if name in PUBLIC_NAMES:
            add("LN7", ENTRY_FILE, line, "public name %s is defined in %s; only the mode bodies define public names"
                % (name, ENTRY_FILE))
    for name in sorted(PUBLIC_NAMES - st_names):
        add("LN7", ST_FILE, 1, "public name %s is not defined in %s" % (name, ST_FILE))
    for name in sorted(CONFIG_NAMES - entry_names):
        add("LN7", ENTRY_FILE, 1, "setting %s is not defined in %s" % (name, ENTRY_FILE))
    for line, name, _, _, _ in st:
        if name in CONFIG_NAMES:
            add("LN7", ST_FILE, line, "setting %s is defined in %s; define it in %s" % (name, ST_FILE, ENTRY_FILE))
    ld_text = texts[LD_FILE].replace("\r\n", "\n")
    ld_public = {}
    for match in LD_DEFINE_RE.finditer(ld_text):
        if not match.group(1).startswith("_RUNGIEC_"):
            ld_public.setdefault(match.group(1), ld_text.count("\n", 0, match.start()) + 1)
    for name in sorted((PUBLIC_NAMES | LD_SETTINGS) - set(ld_public)):
        add("LN7", LD_FILE, 1, "public name %s is not defined in %s" % (name, LD_FILE))
    for name in sorted(set(ld_public) - PUBLIC_NAMES - LD_SETTINGS):
        add("LN7", LD_FILE, ld_public[name], "%s defines %s, which is not a public name (PUBLIC_NAMES) or an LD "
                                             "setting (LD_SETTINGS)" % (LD_FILE, name))
    declared = set()
    found = False
    for _, name, _, body, _ in st:
        if name in ("_RUNGIEC_WORK_VARS", "_RUNGIEC_EDGE_VARS"):
            found = True
            declared.update(re.findall(r"\b(Rungiec\w*)\s*:", body))
    if not found:
        add("LN5", ST_FILE, 1, "_RUNGIEC_WORK_VARS / _RUNGIEC_EDGE_VARS are not defined in %s" % ST_FILE)
        return
    for name in sorted(WORK_VARS - declared):
        add("LN5", ST_FILE, 1, "work variable %s is not declared in _RUNGIEC_WORK_VARS / _RUNGIEC_EDGE_VARS" % name)
    for name in sorted(declared - WORK_VARS):
        add("LN5", ST_FILE, 1, "%s is declared but is not one of the 13 work variables" % name)


def logic_issue(cond):
    """The LN8 note for a bare and / or / not or a backslash form \\and\\ / \\or\\ / \\not\\ in cond, or None."""
    bare = BARE_LOGIC_RE.search(cond)
    if bare:
        return "uses a bare '%s' (write %s)" % (bare.group(1), C_LOGIC[bare.group(1).lower()])
    escaped = ESCAPED_LOGIC_RE.search(cond)
    if escaped:
        return "uses '%s' (write %s)" % (escaped.group(0), C_LOGIC[escaped.group(1).lower()])
    return None


def lint_conditions(src, defines, add):
    """LN8: preprocessor conditions use && / || / !, messages start with 'RUNGIEC ', no {#ignore}."""
    for line, name, _, body, _ in defines:
        for match in re.finditer(r"\b__assert\s*\(", body):
            close = match_paren(body, match.end() - 1)
            if close is None:
                continue
            args = split_args(body, match.end() - 1, close)
            cond = body[args[0][0]:args[0][1]]
            message = body[args[1][0]:close].strip() if len(args) > 1 else ""
            issue = logic_issue(cond)
            if issue:
                add("LN8", line, "__assert condition in %s %s" % (name, issue))
            if not message.startswith("RUNGIEC "):
                add("LN8", line, "__assert message in %s does not start with 'RUNGIEC '" % name)
    for start, end in src.directives:
        raw = src.text[start:end if end else len(src.text)]
        head = re.match(r"\{#\s*(\w+)\s*(.*?)\}?\Z", raw, re.DOTALL)
        if not head:
            continue
        kind, rest = head.group(1), head.group(2)
        if kind in ("if", "elif"):
            issue = logic_issue(rest)
            if issue:
                add("LN8", src.line(start), "{#%s} %s" % (kind, issue))
        elif kind == "error" and not rest.startswith("RUNGIEC "):
            add("LN8", src.line(start), "{#error} message does not start with 'RUNGIEC '")
        elif kind == "ignore":
            add("LN8", src.line(start), "a library file must not use {#ignore} (it silences the checks of the user's "
                                        "file)")


def lint_keywords(src, add):
    """LN9: IEC keywords in lower case (comments, strings and directive lines other than define bodies are skipped)."""
    for match in re.finditer(r"(?<![\w#])([A-Za-z_]\w*)", src.kw):
        word = match.group(1)
        lower = word.lower()
        if lower not in KEYWORDS or word == lower:
            continue
        if word in PUBLIC_NAMES and re.match(r"\s*\(", src.kw[match.end():]):
            continue  # a macro call such as RETURN()
        if src.kw[match.end():match.end() + 1] == "#":
            if lower not in ("bool", "byte", "word", "dword", "lword", "sint", "int", "dint", "lint", "usint",
                             "uint", "udint", "ulint", "real", "lreal", "time", "ltime", "date", "string"):
                continue
        add("LN9", src.line(match.start()), "keyword %s must be lower case (%s)" % (word, lower))


# ---------------------------------------------------------------------------------------------------------
# User code rules (LN10 to LN18 and LN21 to LN25 on tests and examples)
# ---------------------------------------------------------------------------------------------------------

class Call(object):
    __slots__ = ("name", "start", "open", "close", "parent", "args")

    def __init__(self, name, start, open_pos, close):
        self.name, self.start, self.open, self.close = name, start, open_pos, close
        self.parent = None
        self.args = None


def dsl_calls(src):
    """Return the calls of public macros in src (code mask), each with its innermost enclosing DSL call."""
    code = src.code
    calls = []
    for match in CALL_RE.finditer(code):
        if match.group(1) not in PUBLIC_NAMES:
            continue
        close = match_paren(code, match.end() - 1)
        if close is None:
            continue
        call = Call(match.group(1), match.start(), match.end() - 1, close)
        call.args = split_args(code, call.open, close)
        calls.append(call)
    stack = []
    for call in calls:
        while stack and stack[-1].close < call.start:
            stack.pop()
        call.parent = stack[-1] if stack else None
        stack.append(call)
    return calls


# The st and end directives (LN16): in the code mask, and in the comment form that jiecc also reads (searched in
# the text with the string contents blanked, where comments are still visible).
DIRECTIVE_RE = re.compile(r"\{(st|end)\}", re.IGNORECASE)
COMMENT_DIRECTIVE_RE = re.compile(r"\(\*\s*\{(st|end)\}\s*\*\)", re.IGNORECASE)


def st_blocks(code, top=()):
    """Return [(start, end, terminated)] of the DSL part of each POU: start is after BEGIN_RUNGIEC(), end is at
    END_RUNGIEC(), or at the next BEGIN_RUNGIEC() / EOF (not terminated)."""
    marks = [(c.start, c.close + 1, c.name == "BEGIN_RUNGIEC") for c in top
             if c.name in ("BEGIN_RUNGIEC", "END_RUNGIEC")]
    marks.sort()
    blocks = []
    open_at = None
    for start, end, opens in marks:
        if opens:
            if open_at is not None:
                blocks.append((open_at, start, False))
            open_at = end
        elif open_at is not None:
            blocks.append((open_at, start, True))
            open_at = None
    if open_at is not None:
        blocks.append((open_at, len(code), False))
    return blocks


def is_one_call(text):
    """True if text (code mask) is exactly one call NAME(...)."""
    text = text.strip()
    match = re.match(r"[A-Za-z_]\w*\s*\(", text)
    if not match:
        return False
    close = match_paren(text, match.end() - 1)
    return close is not None and close == len(text) - 1


def is_call_sequence(text):
    """True if text (code mask) is two or more calls separated by whitespace only (a forgotten comma)."""
    text = text.strip()
    count = 0
    while text:
        match = re.match(r"[A-Za-z_]\w*\s*\(", text)
        if not match:
            return False
        close = match_paren(text, match.end() - 1)
        if close is None:
            return False
        text = text[close + 1:].strip()
        count += 1
    return count >= 2


ST_LABEL_TOKEN_RE = re.compile(r"\b(case|end_case)\b|\b([A-Za-z_]\w*)\s*:(?!=)", re.IGNORECASE)


def st_labels(code):
    """Return [(offset, name)] of the ST labels 'name:' in code (a code mask). The selectors of a case statement
    ('Red:' between case and end_case) are not labels and are skipped."""
    found = []
    depth = 0
    for match in ST_LABEL_TOKEN_RE.finditer(code):
        word = (match.group(1) or "").lower()
        if word == "case":
            depth += 1
        elif word == "end_case":
            depth = max(0, depth - 1)
        elif depth == 0:
            found.append((match.start(2), match.group(2)))
    return found


def named_argument(text):
    """True if text (code mask of one argument) has ':=' or '=>' outside () and [] (a named argument)."""
    depth = 0
    for pos, char in enumerate(text):
        if char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        elif depth == 0 and text.startswith((":=", "=>"), pos):
            return True
    return False


POU_TOKEN_RE = re.compile(
    r"\b(?:(end_function_block|end_function|end_program|end_method|END_TEST_M)\b"
    r"|(function_block|function|program|method)\b|(TEST_M)\s*\()", re.IGNORECASE)


def pou_spans(code):
    """Return [(start, end)] of the POUs in code (a code mask): function_block / function / program / method (or
    TEST_M(...) of jiecunit) to the matching end_* (or END_TEST_M). A method inside a function block is a span of
    its own inside the span of the function block."""
    spans = []
    stack = []
    for match in POU_TOKEN_RE.finditer(code):
        if match.group(1):
            if stack:
                spans.append((stack.pop(), match.end()))
        else:
            stack.append(match.start())
    return spans


def dsl_pous(src, top):
    """Return (spans, dsl): spans are the POU spans of src (the whole text if it has no POU), dsl maps each span of
    a POU that uses the DSL (a top-level RUNGIEC_VAR*(), BEGIN_RUNGIEC() or END_RUNGIEC() in the innermost POU) to
    its top-level DSL calls."""
    spans = pou_spans(src.code) or [(0, len(src.code))]

    def pou_of(pos):
        inside = [span for span in spans if span[0] <= pos < span[1]]
        return min(inside, key=lambda span: span[1] - span[0]) if inside else None

    owners = {pou_of(call.start) for call in top if call.name in DECL_MACROS} - {None}
    dsl = {span: [call for call in top if pou_of(call.start) == span] for span in owners}
    return spans, dsl


# LN24: what a POU that uses the DSL may have before RUNGIEC_VAR*() and after END_RUNGIEC() besides comments: var
# blocks (with their qualifiers), pragmas, using, nested POUs (function / method from pou_spans, action /
# transition here), the POU head and its end_* (or TEST_M(...) and END_TEST_M). The st and end directives are
# blanked as pragmas: LN16 reports them.
ALLOWED_OUTSIDE_RE = re.compile(
    r"\bvar(?:_[A-Za-z_]+)?\b.*?\bend_var\b"
    r"|\b(action|transition)\b.*?\bend_\1\b"
    r"|\busing\b[^;]*;"
    r"|\{[^{}]*\}"
    r"|\b(?:end_function_block|end_function|end_program|end_method|END_TEST_M)\b",
    re.IGNORECASE | re.DOTALL)


def blank_text(text, a, b):
    return text[:a] + re.sub(r"[^\n]", " ", text[a:b]) + text[b:]


def lint_user_st(src, top, add):
    """LN24: in a POU that uses the DSL, no statement of the user before RUNGIEC_VAR*() (the first top-level
    declaration macro) or after the last END_RUNGIEC() (one POU is in one language: in the LD mode such a statement
    would be an ST body next to the ld body)."""
    code = src.code
    spans, dsl = dsl_pous(src, top)
    has_pou = bool(pou_spans(code))
    for span, calls in sorted(dsl.items()):
        start, end = span
        head = re.match(r"\s*TEST_M\s*\(", code[start:end], re.IGNORECASE)
        if not has_pou:
            head_end = start
        elif head:
            close = match_paren(code, start + head.end() - 1)
            head_end = end if close is None else close + 1
        else:
            newline = code.find("\n", start, end)
            head_end = end if newline < 0 else newline
        decls = [c for c in calls if c.name in DECL_MACROS]
        ends = [c for c in calls if c.name == "END_RUNGIEC"]
        regions = [("before RUNGIEC_VAR*()", head_end, decls[0].start)]
        if ends:
            regions.append(("after END_RUNGIEC()", ends[-1].close + 1, end))
        for where, a, b in regions:
            text = code
            for other in spans:
                if other != span and a <= other[0] and other[1] <= b:
                    text = blank_text(text, other[0], other[1])
            for call in calls:
                if a <= call.start and call.close < b:
                    text = blank_text(text, call.start, call.close + 1)
            region = ALLOWED_OUTSIDE_RE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text[a:b])
            left = re.search(r"[^\s;]", region)
            if left:
                add("LN24", src.line(a + left.start()),
                    "statement of the user %s in a POU that uses the DSL (one POU is in one language): write it in "
                    "ST_EXEC(...) between BEGIN_RUNGIEC() and END_RUNGIEC(), or in a POU without the DSL" % where)


def lint_user(src, add):
    """Apply LN10 to LN18 and LN21 to LN25 to a test or an example."""
    code = src.code
    calls = dsl_calls(src)
    top = [c for c in calls if c.parent is None]

    def inside_st_exec(call):
        parent = call.parent
        while parent is not None:
            if parent.name == "ST_EXEC":
                return True
            parent = parent.parent
        return False

    for call in top:
        line = src.line(call.start)
        if call.name not in TOP_LEVEL_MACROS:
            add("LN10", line, "%s(...) at the top level: a network is RUNG(...) or BEGIN_NET() ... END_NET()"
                % call.name)
        if re.match(r"\s*;", code[call.close + 1:]):
            add("LN11", line, "%s(...) ends its own statement; remove the ';' after it" % call.name)
        span = src.nostr[call.open + 1:call.close]
        comment = re.search(r"\(\*|//", span)
        if comment:
            add("LN18", src.line(call.open + 1 + comment.start()),
                "comment inside the arguments of %s(...) (write it before the call)" % call.name)

    for call in calls:
        line = src.line(call.start)
        args = call.args
        if call.name == "PARALLEL":
            heads = []
            for a, b in args:
                head = re.match(r"\s*([A-Za-z_]\w*)\s*\(", code[a:b])
                heads.append(head.group(1) if head else "")
            coils = [h for h in heads if h in COIL_NAMES]
            if coils and len(coils) != len(heads):
                add("LN12", line, "PARALLEL mixes a coil (%s) with non-coil children; "
                                  "write SERIES(PARALLEL(...), %s(...)) or RUNG(PARALLEL(...), %s(...))"
                    % (coils[0], coils[0], coils[0]))
        if call.name in ("RETURN", "JMP") and not inside_st_exec(call):
            parent = call.parent
            ok = parent is not None and parent.parent is None and parent.name in ("RUNG", "WIRE")
            if ok:
                last_a, last_b = parent.args[-1]
                kids = parent.args[2:] if parent.name == "WIRE" else parent.args
                ok = bool(kids) and last_a <= call.start and call.close < last_b and \
                    code[last_a:last_b].strip() == code[call.start:call.close + 1]
            if not ok:
                add("LN13", line, "%s() must be the last child of a top-level RUNG or WIRE" % call.name)
            elif parent.name == "WIRE":
                # A jump or return leaves the network at once, so a later WIRE of it would not run on that scan.
                later = [c for c in top if c.start > parent.close and c.name in ("WIRE", "BEGIN_NET", "END_NET")]
                if later and later[0].name == "WIRE":
                    add("LN13", line, "the WIRE with %s() must be the last WIRE before END_NET() (the WIRE on line "
                                      "%d would not run when it jumps or returns)"
                        % (call.name, src.line(later[0].start)))
        if call.name in ("RUNG", "SERIES", "PARALLEL", "BEGIN_MC", "WIRE"):
            kids = args[2:] if call.name == "WIRE" else args
            for a, b in kids:
                child = code[a:b]
                if not child.strip() or is_one_call(child) or is_call_sequence(child):
                    continue
                add("LN14", src.line(a + len(child) - len(child.lstrip())),
                    "child of %s is not exactly one element call: %s" % (call.name, " ".join(child.split())[:60]))
        if call.name in BOX_NAME_ARGS and not inside_st_exec(call):
            for a, b in args[BOX_NAME_ARGS[call.name]:]:
                arg = code[a:b]
                if arg.strip() and not named_argument(arg):
                    add("LN25", src.line(a + len(arg) - len(arg.lstrip())),
                        "positional argument %s of %s(...): write name := value or name => variable (the LD has "
                        "no positional argument)" % (" ".join(arg.split())[:40], call.name))
        if call.name == "ST_EXEC":
            body_code = code[call.open + 1:call.close]
            base = call.open + 1
            for match in CALL_RE.finditer(body_code):
                if match.group(1) in PUBLIC_NAMES:
                    add("LN17", src.line(base + match.start()),
                        "DSL macro %s(...) inside ST_EXEC (write it as a child of the network)" % match.group(1))
            for match in RUNGIEC_WORD_RE.finditer(body_code):
                add("LN17", src.line(base + match.start()), "work variable name %s inside ST_EXEC" % match.group(0))
            for match in re.finditer(r"\breturn\b", body_code, re.IGNORECASE):
                add("LN17", src.line(base + match.start()), "return inside ST_EXEC (use RETURN())")
            for match in re.finditer(r"\b(goto|jmp)\b(?!\s*\()", body_code, re.IGNORECASE):
                add("LN17", src.line(base + match.start()), "%s inside ST_EXEC (use JMP() and LABEL())"
                    % match.group(1))
            for pos, name in st_labels(body_code):
                add("LN17", src.line(base + pos), "label %s: inside ST_EXEC (use LABEL())" % name)
            directive = src.nostr.find("{#", call.open + 1, call.close)
            if directive >= 0:
                add("LN17", src.line(directive), "directive inside ST_EXEC")

    for match in re.finditer(r"(?ims)^[ \t]*(function|method)[ \t]+\w+.*?^[ \t]*end_(function|method)\b", code):
        for call in top:
            if call.name in ("RUNGIEC_VAR", "RUNGIEC_VAR_TEMP") and match.start() <= call.start < match.end():
                add("LN15", src.line(call.start), "%s() in a %s: use RUNGIEC_VAR_NOEDGE() (a FUNCTION or METHOD "
                                                  "resets RungiecPrev on every call)" % (call.name, match.group(1)))

    blocks = st_blocks(code, top)
    lint_order(src, top, add)
    lint_user_st(src, top, add)
    lint_pairs(src, top, blocks, add)
    lint_jumps(src, calls, blocks, add)


def block_of(blocks, pos):
    for block in blocks:
        if block[0] <= pos < block[1]:
            return block
    return None


def lint_order(src, top, add):
    """LN16: in a POU that uses the DSL, RUNGIEC_VAR*() is followed by BEGIN_RUNGIEC() with only comments between,
    BEGIN_RUNGIEC() and END_RUNGIEC() pair up, the other DSL calls are between them, and the user writes no st or
    end directive anywhere in the POU (in the code or in the comment form that jiecc also reads; the statements go
    directly in the POU body). A statement of the user before RUNGIEC_VAR*() or after END_RUNGIEC() is checked by
    LN24 (lint_user_st)."""
    code = src.code
    _, dsl = dsl_pous(src, top)
    for start, end in sorted(dsl):
        found = [(m.start(), m.group(0)) for m in DIRECTIVE_RE.finditer(code, start, end)]
        found += [(m.start(), m.group(0)) for m in COMMENT_DIRECTIVE_RE.finditer(src.nostr, start, end)]
        for pos, written in sorted(found):
            add("LN16", src.line(pos), "%s in a POU that uses the DSL: write the statements directly in the POU body, "
                                       "without the st and end directives" % written)

    dsl_open = None  # the BEGIN_RUNGIEC() without its END_RUNGIEC() yet
    var_call = None  # the RUNGIEC_VAR*() without its BEGIN_RUNGIEC() yet
    outside_reported = False  # a DSL call outside BEGIN_RUNGIEC() and END_RUNGIEC() was reported since the last one

    def not_followed(call):
        add("LN16", src.line(call.start), "%s() must be followed by BEGIN_RUNGIEC() (only comments between)"
            % call.name)

    for event in top:
        pos = event.start
        line = src.line(pos)
        if event.name.startswith("RUNGIEC_VAR"):
            if dsl_open is not None:
                add("LN16", line, "%s() must be written before BEGIN_RUNGIEC(), after the last var block of the user"
                    % event.name)
            var_call = event
            continue
        if event.name == "BEGIN_RUNGIEC":
            # A directive between them is reported above, not again here.
            if var_call is not None and re.sub(r"[\s;]", "", DIRECTIVE_RE.sub("", code[var_call.close + 1:pos])):
                not_followed(var_call)
            var_call = None
            if dsl_open is not None:
                add("LN16", line, "BEGIN_RUNGIEC() before the END_RUNGIEC() of the BEGIN_RUNGIEC() on line %d"
                    % src.line(dsl_open.start))
            dsl_open = event
            outside_reported = False
            continue
        if var_call is not None:
            not_followed(var_call)
            var_call = None
        if event.name == "END_RUNGIEC":
            if dsl_open is None:
                add("LN16", line, "END_RUNGIEC() without a BEGIN_RUNGIEC() before it")
            dsl_open = None
            outside_reported = False
        elif dsl_open is None and not outside_reported:
            outside_reported = True
            add("LN16", line, "%s(...) is not between BEGIN_RUNGIEC() and END_RUNGIEC()" % event.name)
    if var_call is not None:
        not_followed(var_call)
    if dsl_open is not None:
        add("LN16", src.line(dsl_open.start), "BEGIN_RUNGIEC() has no END_RUNGIEC()")


PAIR_TOKEN_RE = re.compile(
    r"\b(if|elsif|else|end_if|case|end_case|for|end_for|while|end_while|repeat|end_repeat)\b|:(?!=)",
    re.IGNORECASE)
PAIR_OPEN = {"if": "end_if", "case": "end_case", "for": "end_for", "while": "end_while", "repeat": "end_repeat"}


def lint_pairs(src, top, blocks, add):
    """LN21: BEGIN_MC / END_MC and BEGIN_NET / END_NET in the same branch of the user's if / case / loops."""
    code = src.code
    for a, b, terminated in blocks:
        if not terminated:
            continue
        inside = [c for c in top if a <= c.start < b]
        if not any(c.name in ("BEGIN_MC", "END_MC", "BEGIN_NET", "END_NET") for c in inside):
            continue
        events = [(c.start, c) for c in inside]
        spans = [(c.start, c.close) for c in inside]
        for match in PAIR_TOKEN_RE.finditer(code, a, b):
            pos = match.start()
            if any(s <= pos <= e for s, e in spans):
                continue
            events.append((pos, (match.group(1) or ":").lower()))
        events.sort(key=lambda item: item[0])
        stack = []  # [kind, uid, branch]
        uid = 0
        mc_stack = []
        net = None
        for pos, event in events:
            if isinstance(event, Call):
                context = tuple((u, br) for _, u, br in stack)
                if event.name == "BEGIN_MC":
                    mc_stack.append((context, pos))
                elif event.name == "END_MC" and mc_stack:
                    opened, at = mc_stack.pop()
                    if opened != context:
                        add("LN21", src.line(pos), "END_MC() and its BEGIN_MC (line %d) are in different branches "
                                                   "of an if / case / loop" % src.line(at))
                elif event.name == "BEGIN_NET":
                    net = (context, pos)
                elif event.name == "END_NET" and net is not None:
                    if net[0] != context:
                        add("LN21", src.line(pos), "END_NET() and its BEGIN_NET() (line %d) are in different "
                                                   "branches of an if / case / loop" % src.line(net[1]))
                    net = None
                continue
            if event in PAIR_OPEN:
                uid += 1
                stack.append([event, uid, 0])
            elif event.startswith("end_"):
                if stack:
                    stack.pop()
            elif stack and (event in ("elsif", "else") and stack[-1][0] in ("if", "case")
                            or event == ":" and stack[-1][0] == "case"):
                stack[-1][2] += 1


def lint_jumps(src, calls, blocks, add):
    """LN22: the label of JMP / LABEL is one name, not a Rungiec name, and spelled in one letter case within the
    DSL part of a POU (BEGIN_RUNGIEC() to END_RUNGIEC(); jiecc labels ignore the case, the preprocessor counters of
    the checks do not).
    LN23: no edge contact or transition coil between LABEL(L) and a later JMP(L) of the same DSL part (the loop
    runs it several times in one scan)."""
    code = src.code
    spellings = {}
    labels = {}
    jumps = []
    for call in calls:
        if call.name not in ("JMP", "LABEL"):
            continue
        name = code[call.open + 1:call.close].strip()
        line = src.line(call.start)
        if not re.match(r"^[A-Za-z_]\w*$", name):
            add("LN22", line, "the label of %s(...) must be one name: %s" % (call.name, " ".join(name.split())[:40]))
            continue
        if RUNGIEC_WORD_RE.match(name) or name.lower().startswith("rungiec_"):
            add("LN22", line, "label %s starts with Rungiec (reserved for RUNGIEC)" % name)
        block = block_of(blocks, call.start)
        key = (block, name.lower())
        first = spellings.setdefault(key, (name, line))
        if first[0] != name:
            add("LN22", line, "label %s is spelled %s on line %d (jiecc ignores the case, the checks of JMP and "
                              "LABEL do not)" % (name, first[0], first[1]))
        if call.name == "LABEL":
            labels.setdefault(key, call)
        else:
            jumps.append((key, call))
    reported = set()
    for key, jump in jumps:
        label = labels.get(key)
        if label is None or label.start > jump.start:
            continue
        name = code[label.open + 1:label.close].strip()
        for call in calls:
            if call.name in EDGE_NAMES and label.close < call.start < jump.start and call.start not in reported:
                reported.add(call.start)
                add("LN23", src.line(call.start), "%s(...) is between LABEL(%s) (line %d) and JMP(%s) (line %d): a "
                                                  "backward jump runs it more than once in a scan"
                    % (call.name, name, src.line(label.start), name, src.line(jump.start)))


# ---------------------------------------------------------------------------------------------------------
# Tests and documents (LN19, LN20)
# ---------------------------------------------------------------------------------------------------------

def lint_test_header(src, base, examples, add):
    """LN19: the file name, the @expect kind for the name prefix, and the @cycles header."""
    stem = base[:-4]
    if base.startswith("x_"):
        if stem[2:] not in examples:
            add("LN19", 1, "%s: an example check is named x_<example> after a file in examples/" % base)
    elif not TEST_NAME_RE.match(base):
        add("LN19", 1, "%s: a test is named [tefr]NNN_<snake_case>.iec or x_<example>.iec" % base)
    expect = re.search(r"\(\*\s*@expect\s+(\S+)", src.text)
    want = TEST_PREFIX_EXPECT.get(base[:1])
    if not expect:
        add("LN19", 1, "missing '(* @expect ... *)' header")
    elif want and expect.group(1) != want:
        add("LN19", src.line(expect.start()), "@expect %s does not match the name prefix '%s' (expected %s)"
            % (expect.group(1), base[:1], want))
    if not re.search(r"\(\*\s*@cycles\s+\d+\s*\*\)", src.text):
        add("LN19", 1, "missing '(* @cycles N *)' header")


def section(text, heading):
    """Return (offset, text) of the Markdown section that starts at the line 'heading' (to the next heading of the
    same or a higher level), or (None, '')."""
    match = re.search(r"^%s[ \t]*$" % re.escape(heading), text, re.MULTILINE)
    if not match:
        return None, ""
    level = len(heading) - len(heading.lstrip("#"))
    rest = re.search(r"^#{1,%d} " % level, text[match.end():], re.MULTILINE)
    end = match.end() + rest.start() if rest else len(text)
    return match.start(), text[match.start():end]


def lint_docs(design, examples_doc, tests, examples, add_design, add_examples_doc):
    """LN20: every t/e/f/r test is in the test map of DESIGN.md and the count line matches; every example is in
    the example tables of DESIGN.md and EXAMPLES.md."""

    def line_of(text, pos):
        return text.count("\n", 0, pos) + 1

    off, test_map = section(design, "### 一覧")
    if off is None:
        add_design(1, "the test map ('### 一覧') is not found")
    else:
        listed = set(re.findall(r"^\| `([a-z]\d{3}_\w+)` \|", test_map, re.MULTILINE))
        for name in sorted(t for t in tests if not t.startswith("x_")):
            if name not in listed:
                add_design(line_of(design, off), "test %s is not in the test map" % name)
    count = re.search(r"テストは (\d+) 本（t (\d+)、e (\d+)、f (\d+)、r (\d+)）。"
                      r"例題の確認 x (\d+) 本を加えて、ランナーの合計は (\d+) 本", design)
    kinds = {k: sum(1 for t in tests if t.startswith(k) and not t.startswith("x_")) for k in "tefr"}
    xs = sum(1 for t in tests if t.startswith("x_"))
    want = (sum(kinds.values()), kinds["t"], kinds["e"], kinds["f"], kinds["r"], xs, len(tests))
    if not count:
        add_design(1, "the count line of the test map ('tests/ にあるテストは N 本（t ...）') is not found")
    elif tuple(int(g) for g in count.groups()) != want:
        add_design(line_of(design, count.start()),
                   "the count line says %s but the tests are %d (t %d, e %d, f %d, r %d), x %d, total %d"
                   % (count.group(0), want[0], want[1], want[2], want[3], want[4], want[5], want[6]))
    off, table = section(design, "### 例題（`examples/`）")
    listed = set(re.findall(r"^\| `(\w+)` \|", table, re.MULTILINE))
    for name in sorted(examples):
        if name not in listed:
            add_design(line_of(design, off or 0), "example %s is not in the example table" % name)
    off, table = section(examples_doc, "## 例題")
    listed = set(re.findall(r"^\| `examples/(\w+)\.iec` \|", table, re.MULTILINE))
    for name in sorted(examples):
        if name not in listed:
            add_examples_doc(line_of(examples_doc, off or 0), "example %s is not in the example table" % name)


# ---------------------------------------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------------------------------------

def lint_file(label, text, role, examples=frozenset()):
    """Lint one .iec file. role: 'library' (rungiec.iec or rungiec_st.iec), 'helper', 'test' or 'example'.
    examples: the example names (for the x_ test names). The checks of the library as a whole (the public names
    and the work variables) are lint_public_set, called by lint_repo.

    Return the findings after @lint-allow, with the LN0 findings of the file, ordered by line."""
    src = Source(label, text.replace("\r\n", "\n"))
    found = []

    def add(rule, line, message):
        found.append(Finding(rule, label, line, message))

    defines, bad = src.defines()
    lint0 = [Finding("LN0", label, line, message) for line, message in bad]
    if role in ("library", "helper"):
        lint_library(src, defines, add, helper=(role == "helper"))
    lint_keywords(src, add)
    if role in ("test", "example"):
        lint_user(src, add)
    if role == "test":
        lint_test_header(src, os.path.basename(label), examples, add)

    allowed = {}
    for match in ALLOW_RE.finditer(src.text):
        for rule in re.split(r"[\s,]+", match.group(1).strip()):
            if not rule:
                continue
            if rule not in RULES or rule == "LN0":
                lint0.append(Finding("LN0", label, src.line(match.start()),
                                     "@lint-allow names an unknown or unsuppressible rule %s" % rule))
            else:
                allowed.setdefault(rule, src.line(match.start()))
    kept = [f for f in found if f.rule not in allowed]
    used = {f.rule for f in found}
    for rule, line in sorted(allowed.items()):
        if rule not in used:
            lint0.append(Finding("LN0", label, line, "@lint-allow %s suppresses nothing; remove it" % rule))
    return sorted(lint0 + kept, key=lambda f: f.line)


def read(path):
    with open(path, encoding="utf-8", newline="") as f:
        return f.read().replace("\r\n", "\n")


def lint_repo(root, tests_dir, examples_dir=None):
    """Return the findings for the repository (sorted by file and line)."""
    if examples_dir is None:
        examples_dir = os.path.join(root, "examples")

    def rel(path):
        return os.path.relpath(path, root).replace("\\", "/")

    findings = []
    texts = {name: read(os.path.join(root, name)) for name in (ENTRY_FILE, ST_FILE, LD_FILE)}
    for name in LIBRARY_FILES:
        findings += lint_file(name, texts[name], "library")
    lint_public_set(texts, lambda rule, label, line, msg: findings.append(Finding(rule, label, line, msg)))
    support = os.path.join(tests_dir, "support")
    if os.path.isdir(support):
        for name in sorted(os.listdir(support)):
            if name.endswith(".iec"):
                path = os.path.join(support, name)
                findings += lint_file(rel(path), read(path), "helper")
    ex_names = sorted(n for n in os.listdir(examples_dir) if n.endswith(".iec")) if os.path.isdir(examples_dir) else []
    examples = {n[:-4] for n in ex_names}
    for name in ex_names:
        path = os.path.join(examples_dir, name)
        findings += lint_file(rel(path), read(path), "example")
    tests = []
    for name in sorted(os.listdir(tests_dir)):
        if name.endswith(".iec"):
            tests.append(name[:-4])
            path = os.path.join(tests_dir, name)
            findings += lint_file(rel(path), read(path), "test", examples)

    design_path = os.path.join(root, "docs", "DESIGN.md")
    examples_doc_path = os.path.join(root, "docs", "EXAMPLES.md")
    lint_docs(read(design_path), read(examples_doc_path), tests, examples,
              lambda line, msg: findings.append(Finding("LN20", rel(design_path), line, msg)),
              lambda line, msg: findings.append(Finding("LN20", rel(examples_doc_path), line, msg)))
    return findings


def run_lint(root, tests_dir, examples_dir=None):
    """Print the findings and the summary line. Return the process exit code (0 or 1)."""
    findings = lint_repo(root, tests_dir, examples_dir)
    for finding in findings:
        print(finding.text())
    if findings:
        print("LINT: NG (%d findings)" % len(findings))
        return 1
    print("LINT: OK")
    return 0


# ---------------------------------------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------------------------------------

def user_pou(body, header="(* @expect pass *)\n(* @cycles 1 *)\n", head="program p\nvar A, B, Y: bool; end_var\n",
             decl="RUNGIEC_VAR()\n"):
    """A small POU for the self-test: header, POU head, declarations, BEGIN_RUNGIEC(), body, END_RUNGIEC()."""
    return "%s%s%sBEGIN_RUNGIEC()\n%s\nEND_RUNGIEC()\nend_program\n" % (header, head, decl, body)


GOOD_LIB = (
    "{#define _RUNGIEC_WORK_VARS() RungiecEn: bool; RungiecIn: bool; RungiecAcc: bool; RungiecVal: bool; "
    "RungiecTmp: bool; RungiecRail: bool; RungiecSp: dint; RungiecStIn: array[1..2] of bool; "
    "RungiecStAcc: array[1..2] of bool; RungiecNode: array[0..2] of bool; RungiecI: dint; "
    "RungiecMcSt: array[1..2] of bool;}\n"
    "{#define _RUNGIEC_EDGE_VARS() RungiecPrev: array[1..2] of bool;}\n"
)
# A library that passes the whole-library checks: the entry file with the settings, the ST body with the work
# variables and the public names, the LD body with the public names and its setting.
GOOD_ENTRY = "".join("{#define %s 1}\n" % name for name in sorted(CONFIG_NAMES))
GOOD_ST = GOOD_LIB + "".join("{#define %s() a := 1}\n" % name for name in sorted(PUBLIC_NAMES))
GOOD_LD = ("{#define RUNGIEC_LD_FANIN_MAX 16}\n{#define _RUNGIEC_JUMP_OK 1}\n"
           + "".join("{#define %s() a}\n" % name for name in sorted(PUBLIC_NAMES)))


def libset(entry=GOOD_ENTRY, st=GOOD_ST, ld=GOOD_LD):
    """The texts of a "libset" self-test case (lint_public_set)."""
    return {ENTRY_FILE: entry, ST_FILE: st, LD_FILE: ld}


GOOD_DOCS = {
    "design": "## テスト対応表\n\n`tests/` にあるテストは 1 本（t 1、e 0、f 0、r 0）。"
              "例題の確認 x 1 本を加えて、ランナーの合計は 2 本。\n\n"
              "### 一覧\n\n| テスト | 内容 |\n|---|---|\n| `t001_a` | a |\n\n"
              "### 例題（`examples/`）\n\n| 例題 | 内容 |\n|---|---|\n| `ex` | e |\n",
    "examples_doc": "## 例題\n\n| ファイル | 内容 |\n|---|---|\n| `examples/ex.iec` | e |\n",
    "tests": ["t001_a", "x_ex"],
    "examples": ["ex"],
}

# (case name, kind, label, fragment, rule, must_fire, substring of the message or "")
# kind: "lib" = a fragment of a library file (LN1 to LN9), "libset" = the whole-library checks of LN5 / LN7
# (fragment = the dict of libset()), "helper" = a helper file, "test" = a test file (label = its path), "example" = an example, "docs" = LN20
# (fragment = dict like GOOD_DOCS). must_fire False means the fragment is valid for that rule.
SELFTEST_CASES = [
    ("LN0 unparsable define", "lib", "rungiec.iec", "{#define }\n", "LN0", True, "cannot parse"),
    ("LN0 unterminated define", "lib", "rungiec.iec", "{#define _RUNGIEC_X() a := 1;\n", "LN0", True, "unterminated"),
    ("LN0 unknown rule in @lint-allow", "test", "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A), COIL(Y))", header="(* @expect pass *)\n(* @cycles 1 *)\n(* @lint-allow LN99 *)\n"),
     "LN0", True, "unknown"),
    ("LN0 @lint-allow that suppresses nothing", "test", "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A), COIL(Y))", header="(* @expect pass *)\n(* @cycles 1 *)\n(* @lint-allow LN12 *)\n"),
     "LN0", True, "suppresses nothing"),
    ("LN0 quiet: @lint-allow that suppresses a finding", "test", "tests/t001_a.iec",
     user_pou("SERIES(CONTACT(A), COIL(Y))", header="(* @expect pass *)\n(* @cycles 1 *)\n(* @lint-allow LN10 *)\n"),
     "LN0", False, ""),
    ("LN1 directive in a define body", "lib", "rungiec.iec", "{#define _RUNGIEC_X() {#define Y 1}}\n", "LN1", True, "{#"),
    ("LN1 comment in a define body", "helper", "tests/support/h.iec", "{#define T_X() a := 1; (* c *)}\n", "LN1", True,
     "(*"),
    ("LN1 '$' in the file", "lib", "rungiec.iec", "(* $ *)\n{#define _RUNGIEC_X() a := 1}\n", "LN1", True, "'$'"),
    ("LN1 '$' of a directive in BEGIN_RUNGIEC", "lib", "rungiec_st.iec",
     "{#define BEGIN_RUNGIEC() _RUNGIEC_START() ${st$} _RUNGIEC_INIT()}\n", "LN1", True, "'$'"),
    ("LN1 quiet: comment outside the body", "lib", "rungiec.iec", "(* note *)\n{#define _RUNGIEC_X() a := 1}\n", "LN1",
     False, ""),
    ("LN2 operand used twice", "lib", "rungiec.iec", "{#define CONTACT(x) RungiecTmp := (x); RungiecVal := (x)}\n", "LN2",
     True, "used 2 times"),
    ("LN2 quiet: n of _RUNGIEC_*_I, a second use in __assert", "lib", "rungiec.iec",
     "{#define _RUNGIEC_RISE_I(x, n) __assert(x = x, RUNGIEC m) RungiecTmp := (x); RungiecVal := RungiecEn and RungiecTmp "
     "and not RungiecPrev[n]; RungiecPrev[n] := RungiecTmp}\n", "LN2", False, ""),
    ("LN2 label used twice in the ST", "lib", "rungiec.iec",
     "{#define _RUNGIEC_LABEL_I(label, lc) label: RungiecVal := label}\n", "LN2", True, "label of _RUNGIEC_LABEL_I"),
    ("LN2 label used twice in the ST after a shared check", "lib", "rungiec.iec",
     "{#define _RUNGIEC_LABEL_I(label, lc, jc) _RUNGIEC_LABEL_CHECKS(label, lc, jc) label: RungiecVal := label}\n",
     "LN2", True, "label of _RUNGIEC_LABEL_I"),
    ("LN2 quiet: uses in the shared checks and counter updates", "lib", "rungiec.iec",
     "{#define _RUNGIEC_LABEL_I(label, lc, jc) _RUNGIEC_LABEL_CHECKS(label, lc, jc) label: RungiecVal := true}\n"
     "{#define WIRE(from_node, to_node, ...) _RUNGIEC_WIRE_CHECKS(from_node, to_node, _RUNGIEC_COUNT(__VA_ARGS__)) "
     "_RUNGIEC_SERIES_ANY(__VA_ARGS__); _RUNGIEC_WIRE_DONE(to_node)}\n", "LN2", False, ""),
    ("LN2 quiet: a label pasted into counter names", "lib", "rungiec.iec",
     "{#define JMP(label) _RUNGIEC_JMP_I(label, _RUNGIEC_CAT_X(rungiec_label_, label), "
     "_RUNGIEC_CAT_X(rungiec_jump_, label))}\n", "LN2", False, ""),
    ("LN2 quiet: the instance of FB_BOX called and read", "lib", "rungiec.iec",
     "{#define FB_BOX(inst, in_pin, out_pin, ...) RungiecTmp := RungiecEn; inst(in_pin := RungiecTmp __VA_OPT__(,) "
     "__VA_ARGS__); RungiecVal := RungiecRail and inst.out_pin}\n", "LN2", False, ""),
    ("LN2 instance used twice in FB_SCAN", "lib", "rungiec.iec",
     "{#define FB_SCAN(inst, ...) RungiecTmp := RungiecEn; inst(__VA_ARGS__); RungiecVal := inst.Q}\n", "LN2", True,
     "parameter inst of FB_SCAN"),
    ("LN3 @@ with __VA_ARGC__", "lib", "rungiec.iec",
     "{#define SERIES(...) _RUNGIEC_SERIES_@@__VA_ARGC__(__VA_ARGS__)}\n", "LN3", True, ""),
    ("LN3 @@ with a counter call", "lib", "rungiec.iec", "{#define _RUNGIEC_X(a) a@@__counter(rungiec_edge)}\n", "LN3",
     True, ""),
    ("LN3 quiet: through _RUNGIEC_CAT_X", "lib", "rungiec.iec",
     "{#define _RUNGIEC_CAT(a, b) a@@b}\n"
     "{#define SERIES(...) _RUNGIEC_CAT_X(_RUNGIEC_SERIES_, __VA_ARGC__)(__VA_ARGS__)}\n", "LN3", False, ""),
    ("LN4 contact folded with and", "lib", "rungiec.iec", "{#define COIL(x) x := RungiecEn and RungiecIn; RungiecVal := "
     "RungiecEn}\n", "LN4", True, "x:=RungiecEnandRungiecIn"),
    ("LN4 quiet: the allowed shapes", "lib", "rungiec.iec",
     "{#define NOT_COIL(x) x := RungiecRail and not RungiecEn; RungiecVal := RungiecEn}\n"
     "{#define _RUNGIEC_P(a) RungiecEn := RungiecIn; _RUNGIEC_CHILD(a); RungiecAcc := RungiecAcc or RungiecVal;}\n"
     "{#define _RUNGIEC_FALL_COIL_I(x, n) x := not RungiecEn and RungiecPrev[n]; RungiecPrev[n] := RungiecEn; "
     "RungiecVal := RungiecEn}\n", "LN4", False, ""),
    ("LN4 quiet: the output of FBT_BOX under the rail", "lib", "rungiec.iec",
     "{#define FBT_BOX(inst, type, in_pin, out_pin, ...) RungiecTmp := RungiecEn; inst(in_pin := RungiecTmp "
     "__VA_OPT__(,) __VA_ARGS__); RungiecVal := RungiecRail and inst.out_pin}\n", "LN4", False, ""),
    ("LN4 quiet: contacts with their input power", "lib", "rungiec.iec",
     "{#define NOT_CONTACT(x) RungiecTmp := (x); RungiecVal := RungiecEn and not RungiecTmp}\n"
     "{#define GE_CONTACT(a, b) RungiecTmp := ((a) >= (b)); RungiecVal := RungiecEn and RungiecTmp}\n"
     "{#define _RUNGIEC_NOT_FALL_I(x, n) RungiecTmp := (x); RungiecVal := RungiecEn and not (not RungiecTmp and "
     "RungiecPrev[n]); RungiecPrev[n] := RungiecTmp}\n", "LN4", False, ""),
    ("LN4 series folded with and", "lib", "rungiec.iec",
     "{#define _RUNGIEC_X(a) RungiecEn := RungiecAcc; _RUNGIEC_CHILD(a); RungiecAcc := RungiecAcc and RungiecVal;}\n",
     "LN4", True, "RungiecAcc:=RungiecAccandRungiecVal"),
    ("LN4 contact without its input power", "lib", "rungiec.iec",
     "{#define NOT_CONTACT(x) RungiecTmp := (x); RungiecVal := not RungiecTmp and RungiecTmp}\n", "LN4", True,
     "RungiecVal:=notRungiecTmpandRungiecTmp"),
    ("LN4 box output folded with the power", "lib", "rungiec.iec",
     "{#define FB_BOX(inst, in_pin, out_pin, ...) RungiecTmp := RungiecEn; inst(in_pin := RungiecTmp __VA_OPT__(,) "
     "__VA_ARGS__); RungiecVal := RungiecEn and inst.out_pin}\n", "LN4", True, "RungiecVal:=RungiecEnandinst.out_pin"),
    ("LN5 unknown work variable", "lib", "rungiec.iec", "{#define _RUNGIEC_X() RungiecId := true}\n", "LN5", True,
     "RungiecId"),
    ("LN5 work variable in the wrong case", "lib", "rungiec.iec", "{#define _RUNGIEC_X() RungiecSP := 0}\n", "LN5", True,
     "RungiecSP"),
    ("LN5 unknown counter", "lib", "rungiec.iec", "{#define _RUNGIEC_X() __counter(rungiec_x)}\n", "LN5", True,
     "rungiec_x"),
    ("LN5 counter that is not a parameter of the define", "lib", "rungiec.iec",
     "{#define _RUNGIEC_JMP_I(label, lc) __counter_set(jc, __counter_peek(rungiec_seg))}\n", "LN5", True, "counter jc"),
    ("LN5 unknown pasted counter prefix", "lib", "rungiec.iec",
     "{#define JMP(label) _RUNGIEC_JMP_I(label, _RUNGIEC_CAT_X(rungiec_lbl_, label))}\n", "LN5", True, "rungiec_lbl_"),
    ("LN5 quiet: per-label counters passed as parameters", "lib", "rungiec.iec",
     "{#define JMP(label) _RUNGIEC_JMP_I(label, _RUNGIEC_CAT_X(rungiec_label_, label), "
     "_RUNGIEC_CAT_X(rungiec_jump_, label))}\n"
     "{#define _RUNGIEC_JMP_I(label, lc, jc) __assert(__counter_peek(lc) < __counter_peek(rungiec_pou), RUNGIEC m) "
     "__counter_set(jc, __counter_peek(rungiec_seg))}\n", "LN5", False, ""),
    ("LN5 declaration differs from the 13 names", "libset", "rungiec_st.iec",
     libset(st=GOOD_ST.replace(" RungiecI: dint;", "")), "LN5", True, "RungiecI is not declared"),
    ("LN5 quiet: known names and counters", "lib", "rungiec.iec",
     "{#define _RUNGIEC_X() RungiecMcSt[__counter_peek(rungiec_mc)] := RungiecRail}\n", "LN5", False, ""),
    ("LN5 quiet: the 13 declared names", "libset", "rungiec_st.iec", libset(), "LN5", False, ""),
    ("LN6 element ending with ';'", "lib", "rungiec.iec", "{#define COIL(x) x := RungiecEn; RungiecVal := RungiecEn;}\n",
     "LN6", True, "ends with ';'"),
    ("LN6 element starting with if", "lib", "rungiec.iec",
     "{#define SET_COIL(x) if RungiecEn then x := true; end_if; RungiecVal := RungiecEn}\n", "LN6", True,
     "does not start"),
    ("LN6 element ending with end_if", "lib", "rungiec.iec",
     "{#define _RUNGIEC_X_I(x, n) RungiecTmp := RungiecEn; if RungiecTmp then x := true; end_if}\n", "LN6", True,
     "does not end"),
    ("LN6 combinator differs", "lib", "rungiec.iec",
     "{#define _RUNGIEC_S(a) RungiecEn := RungiecAcc; _RUNGIEC_CHILD(a) RungiecAcc := RungiecVal;}\n", "LN6",
     True, "must be exactly"),
    ("LN6 series combinator with the old and", "lib", "rungiec.iec",
     "{#define _RUNGIEC_S(a) RungiecEn := RungiecAcc; _RUNGIEC_CHILD(a); RungiecAcc := RungiecAcc and RungiecVal;}\n",
     "LN6", True, "must be exactly"),
    ("LN6 quiet: checks first, a wrapper, a statement macro", "lib", "rungiec.iec",
     "{#define ST_EXEC(...) __assert(__VA_ARGC__ >= 1, RUNGIEC m) RungiecTmp := RungiecEn; if RungiecTmp then "
     "__nosplit(__VA_ARGS__); end_if; RungiecVal := RungiecTmp}\n"
     "{#define RISE_COIL(x) _RUNGIEC_RISE_COIL_I(x, __counter(rungiec_edge))}\n"
     "{#define END_NET() RungiecEn := RungiecRail;}\n", "LN6", False, ""),
    ("LN6 quiet: a shared check first (RETURN)", "lib", "rungiec.iec",
     "{#define RETURN() _RUNGIEC_RETURN_CHECKS() RungiecTmp := RungiecEn; if RungiecTmp then return; end_if; "
     "RungiecVal := RungiecTmp}\n", "LN6", False, ""),
    ("LN6 element starting with if after a shared check", "lib", "rungiec.iec",
     "{#define RETURN() _RUNGIEC_RETURN_CHECKS() if RungiecEn then return; end_if; RungiecVal := RungiecEn}\n", "LN6",
     True, "does not start with an assignment"),
    ("LN6 quiet: the body of a statement macro (_RUNGIEC_LABEL_I)", "lib", "rungiec.iec",
     "{#define _RUNGIEC_LABEL_I(label, lc, jc) label: RungiecStIn[1 - RungiecSp] := RungiecIn;}\n", "LN6", False, ""),
    ("LN6 element body (_RUNGIEC_JMP_I) ending with ';'", "lib", "rungiec.iec",
     "{#define _RUNGIEC_JMP_I(label, lc, jc) RungiecTmp := RungiecEn; if RungiecTmp then goto label; end_if; "
     "RungiecVal := RungiecTmp;}\n", "LN6", True, "ends with ';'"),
    ("LN7 internal name without _RUNGIEC_", "lib", "rungiec.iec", "{#define _X() a := 1}\n", "LN7", True,
     "must start with _RUNGIEC_"),
    ("LN7 unknown public name", "lib", "rungiec.iec", "{#define MY_COIL(x) x := RungiecEn}\n", "LN7", True,
     "not a public name"),
    ("LN7 trailing '_'", "lib", "rungiec.iec", "{#define _RUNGIEC_X_() a := 1}\n", "LN7", True, "ends with '_'"),
    ("LN7 BEGIN_ at the end and no pair", "lib", "rungiec.iec",
     "{#define BEGIN_MC(...) a := 1}\n{#define RUNGIEC_BEGIN() a := 1}\n", "LN7", True, "has no END_MC"),
    ("LN7 public name missing from rungiec_st.iec", "libset", "rungiec_st.iec", libset(st=GOOD_LIB), "LN7", True,
     "RUNG is not defined in rungiec_st.iec"),
    ("LN7 public name in the entry file rungiec.iec", "libset", "rungiec.iec",
     libset(entry=GOOD_ENTRY + "{#define END_RUNGIEC() a}\n"), "LN7", True,
     "END_RUNGIEC is defined in rungiec.iec"),
    ("LN7 setting missing from rungiec.iec", "libset", "rungiec.iec",
     libset(entry=GOOD_ENTRY.replace("{#define RUNGIEC_NODE_MAX 1}\n", "")), "LN7", True,
     "RUNGIEC_NODE_MAX is not defined in rungiec.iec"),
    ("LN7 setting in rungiec_st.iec", "libset", "rungiec_st.iec",
     libset(st=GOOD_ST + "{#define RUNGIEC_MC_MAX 15}\n"), "LN7", True, "RUNGIEC_MC_MAX is defined in rungiec_st.iec"),
    ("LN7 public name missing from rungiec_ld.iec", "libset", "rungiec_ld.iec",
     libset(ld=GOOD_LD.replace("{#define END_RUNGIEC() a}\n", "")), "LN7", True,
     "END_RUNGIEC is not defined in rungiec_ld.iec"),
    ("LN7 unknown name in rungiec_ld.iec", "libset", "rungiec_ld.iec",
     libset(ld=GOOD_LD + "{#define MY_COIL(x) a}\n"), "LN7", True, "defines MY_COIL"),
    ("LN7 quiet: the entry file, the ST body and the LD body", "libset", "rungiec.iec", libset(), "LN7", False, ""),
    ("LN7 quiet: internal and paired public names", "lib", "rungiec.iec",
     "{#define _RUNGIEC_POP() a := 1}\n{#define BEGIN_NET() a := 1;}\n{#define END_NET() a := 1;}\n"
     "{#define RUNGIEC_MC_MAX 15}\n", "LN7", False, ""),
    ("LN8 bare or in {#if}", "lib", "rungiec.iec", "{#if RUNGIEC_NEST_MAX < 1 or RUNGIEC_NEST_MAX > 9}\n{#endif}\n", "LN8",
     True, "bare 'or'"),
    ("LN8 bare and in __assert", "lib", "rungiec.iec",
     "{#define _RUNGIEC_X(n) __assert(n >= 1 and n <= 2, RUNGIEC bad n)}\n", "LN8", True, "bare 'and'"),
    ("LN8 message without RUNGIEC", "lib", "rungiec.iec", "{#define _RUNGIEC_X(n) __assert(n >= 1, bad n)}\n", "LN8",
     True, "does not start"),
    ("LN8 {#ignore}", "lib", "rungiec.iec", "{#ignore PP91}\n", "LN8", True, "{#ignore}"),
    ("LN8 backslash and in __assert", "lib", "rungiec.iec",
     "{#define _RUNGIEC_X(n) __assert(n >= 1 \\and\\ n <= 2, RUNGIEC bad n)}\n", "LN8", True, "'\\and\\' (write &&)"),
    ("LN8 backslash not in {#if}", "lib", "rungiec.iec", "{#if \\not\\ __has_builtin(__assert)}\n{#endif}\n", "LN8",
     True, "'\\not\\' (write !)"),
    ("LN8 quiet: C logical operators and RUNGIEC messages", "lib", "rungiec.iec",
     "{#if !__has_builtin(__assert) || RUNGIEC_NEST_MAX < 1}\n{#error RUNGIEC needs a jiepp}\n{#endif}\n"
     "{#define _RUNGIEC_X(n) __assert(n >= 1 && n <= 2, RUNGIEC bad n, see the manual)}\n", "LN8", False, ""),
    ("LN9 upper-case keyword in a test", "test", "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A), COIL(Y))\nIF A THEN B := TRUE; END_IF;"), "LN9", True, "IF"),
    ("LN9 upper-case keyword in a define body", "lib", "rungiec.iec", "{#define _RUNGIEC_X() a := NOT b}\n", "LN9", True,
     "NOT"),
    ("LN9 quiet: comments, strings, directives, RETURN(), T#", "test", "tests/t001_a.iec",
     user_pou("(* IF THEN *)\nRUNG(CONTACT(A), RETURN())\nS := 'IF';\nTm := T#1s;\n{#if TRUE}\n{#endif}"),
     "LN9", False, ""),
    ("LN10 bare SERIES at the top level", "test", "tests/t001_a.iec", user_pou("SERIES(CONTACT(A), COIL(Y))"), "LN10",
     True, "SERIES"),
    ("LN10 bare element at the top level", "example", "examples/ex.iec", user_pou("COIL(Y)", header=""), "LN10",
     True, "COIL"),
    ("LN10 quiet: networks, MC and the declarations", "test", "tests/t001_a.iec",
     user_pou("BEGIN_MC(CONTACT(A))\nRUNG(SERIES(CONTACT(A), COIL(Y)))\nEND_MC()\nBEGIN_NET()\n"
              "WIRE(0, 1, CONTACT(A))\nWIRE(1, 2, COIL(Y))\nEND_NET()"), "LN10", False, ""),
    ("LN10 quiet: LABEL between networks", "test", "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A), JMP(Skip))\nRUNG(COIL(Y))\nLABEL(Skip)"), "LN10", False, ""),
    ("LN11 ';' after RUNG", "test", "tests/t001_a.iec", user_pou("RUNG(CONTACT(A), COIL(Y));"), "LN11", True, "RUNG"),
    ("LN11 ';' after LABEL", "test", "tests/t001_a.iec", user_pou("RUNG(CONTACT(A), JMP(Skip))\nLABEL(Skip);"), "LN11",
     True, "LABEL"),
    ("LN11 quiet: no ';', ';' inside ST_EXEC", "test", "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A), ST_EXEC(B := true;), COIL(Y))"), "LN11", False, ""),
    ("LN12 coil mixed with contacts", "test", "tests/t001_a.iec",
     user_pou("RUNG(PARALLEL(CONTACT(A), CONTACT(B), COIL(Y)))"), "LN12", True, "mixes a coil"),
    ("LN12 quiet: coil-only PARALLEL and SERIES branches", "test", "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A), PARALLEL(COIL(Y), NOT_COIL(B)))\n"
              "RUNG(PARALLEL(SERIES(CONTACT(A), COIL(Y)), SERIES(CONTACT(B), COIL(B))))"), "LN12", False, ""),
    ("LN13 RETURN in the middle", "test", "tests/t001_a.iec", user_pou("RUNG(CONTACT(A), RETURN(), COIL(Y))"), "LN13",
     True, "last child"),
    ("LN13 RETURN in a PARALLEL branch", "test", "tests/t001_a.iec",
     user_pou("RUNG(PARALLEL(SERIES(CONTACT(A), RETURN()), CONTACT(B)), COIL(Y))"), "LN13", True, "last child"),
    ("LN13 quiet: last child of RUNG and WIRE", "test", "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A), RETURN())\nRUNG(RETURN())\nBEGIN_NET()\nWIRE(0, 1, CONTACT(A), RETURN())\nEND_NET()"),
     "LN13", False, ""),
    ("LN13 JMP in the middle", "test", "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A), JMP(Skip), COIL(Y))\nLABEL(Skip)"), "LN13", True, "JMP() must be"),
    ("LN13 JMP in BEGIN_MC", "test", "tests/t001_a.iec",
     user_pou("BEGIN_MC(CONTACT(A), JMP(Skip))\nEND_MC()\nLABEL(Skip)"), "LN13", True, "JMP() must be"),
    ("LN13 quiet: JMP last in RUNG and WIRE", "test", "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A), JMP(Skip))\nBEGIN_NET()\nWIRE(0, 1, CONTACT(B), JMP(Skip))\nEND_NET()\nLABEL(Skip)"),
     "LN13", False, ""),
    ("LN13 WIRE after the WIRE with JMP", "test", "tests/t001_a.iec",
     user_pou("BEGIN_NET()\nWIRE(0, 1, CONTACT(A))\nWIRE(1, 2, CONTACT(B), JMP(Skip))\nWIRE(1, 3, COIL(Y))\nEND_NET()\n"
              "LABEL(Skip)"), "LN13", True, "last WIRE before END_NET"),
    ("LN13 WIRE after the WIRE with RETURN", "test", "tests/t001_a.iec",
     user_pou("BEGIN_NET()\nWIRE(0, 1, CONTACT(A), RETURN())\nWIRE(0, 2, COIL(Y))\nEND_NET()"), "LN13", True,
     "last WIRE before END_NET"),
    ("LN13 quiet: the WIRE with JMP or RETURN last, then a new network", "test", "tests/t001_a.iec",
     user_pou("BEGIN_NET()\nWIRE(0, 1, CONTACT(A))\nWIRE(1, 2, COIL(Y))\nWIRE(1, 3, CONTACT(B), JMP(Skip))\nEND_NET()\n"
              "BEGIN_NET()\nWIRE(0, 1, CONTACT(A), RETURN())\nEND_NET()\nBEGIN_NET()\nWIRE(0, 1, COIL(Y))\nEND_NET()\n"
              "LABEL(Skip)"), "LN13", False, ""),
    ("LN14 operator instead of a comma", "test", "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A) and B, COIL(Y))"), "LN14", True, "not exactly one"),
    ("LN14 bare variable as a child", "test", "tests/t001_a.iec", user_pou("RUNG(SERIES(A, CONTACT(B)), COIL(Y))"),
     "LN14", True, "not exactly one"),
    ("LN14 quiet: calls, an empty child, a missing comma", "test", "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A), COIL(Y))\nRUNG(CONTACT(A), )\nRUNG(CONTACT(A) CONTACT(B), COIL(Y))\n"
              "BEGIN_NET()\nWIRE(0, 1, CONTACT(A))\nEND_NET()"), "LN14", False, ""),
    ("LN15 RUNGIEC_VAR in a FUNCTION", "test", "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A), COIL(F))", head="function F : bool\nvar_input A: bool; end_var\n")
     .replace("end_program", "end_function"), "LN15", True, "RUNGIEC_VAR"),
    ("LN15 quiet: RUNGIEC_VAR_NOEDGE in a FUNCTION, RUNGIEC_VAR in a FUNCTION_BLOCK", "test", "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A), COIL(F))", head="function F : bool\nvar_input A: bool; end_var\n",
              decl="RUNGIEC_VAR_NOEDGE()\n").replace("end_program", "end_function")
     + user_pou("RUNG(CONTACT(A), COIL(Y))", header="", head="function_block Fb\nvar A, Y: bool; end_var\n")
     .replace("end_program", "end_function_block"), "LN15", False, ""),
    ("LN16 RUNGIEC_VAR after BEGIN_RUNGIEC", "test", "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A), COIL(Y))", decl="").replace("BEGIN_RUNGIEC()\n", "BEGIN_RUNGIEC()\nRUNGIEC_VAR()\n"),
     "LN16", True, "written before BEGIN_RUNGIEC()"),
    ("LN16 statement between RUNGIEC_VAR and BEGIN_RUNGIEC", "test", "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A), COIL(Y))").replace("RUNGIEC_VAR()\n", "RUNGIEC_VAR()\nB := A;\n"), "LN16", True,
     "followed by BEGIN_RUNGIEC()"),
    ("LN16 {st} of the user after RUNGIEC_VAR", "test", "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A), COIL(Y))").replace("RUNGIEC_VAR()\n", "RUNGIEC_VAR()\n{st}\n"), "LN16", True,
     "{st} in a POU that uses the DSL"),
    ("LN16 {st} of the user after BEGIN_RUNGIEC", "test", "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A), COIL(Y))").replace("BEGIN_RUNGIEC()\n", "BEGIN_RUNGIEC()\n{st}\n"), "LN16", True,
     "{st} in a POU that uses the DSL"),
    ("LN16 {end} of the user before END_RUNGIEC", "test", "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A), COIL(Y))").replace("END_RUNGIEC()\n", "{end}\nEND_RUNGIEC()\n"), "LN16", True,
     "{end} in a POU that uses the DSL"),
    ("LN16 {end} of the user after END_RUNGIEC", "test", "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A), COIL(Y))").replace("END_RUNGIEC()\n", "END_RUNGIEC()\n{end}\n"), "LN16", True,
     "{end} in a POU that uses the DSL"),
    ("LN16 {st} of the user before RUNGIEC_VAR", "test", "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A), COIL(Y))", decl="{st}\nRUNGIEC_VAR()\n"), "LN16", True,
     "{st} in a POU that uses the DSL"),
    ("LN16 comment form (*{st}*) and (* {end} *) of the user", "test", "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A), COIL(Y))\n(* {end} *)").replace("BEGIN_RUNGIEC()\n", "BEGIN_RUNGIEC()\n(*{st}*)\n"),
     "LN16", True, "(*{st}*) in a POU that uses the DSL"),
    ("LN16 BEGIN_RUNGIEC twice", "test", "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A), COIL(Y))").replace("BEGIN_RUNGIEC()\n", "BEGIN_RUNGIEC()\nBEGIN_RUNGIEC()\n"), "LN16",
     True, "before the END_RUNGIEC()"),
    ("LN16 END_RUNGIEC missing", "test", "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A), COIL(Y))").replace("END_RUNGIEC()\n", ""), "LN16", True, "has no END_RUNGIEC()"),
    ("LN16 BEGIN_RUNGIEC missing", "test", "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A), COIL(Y))").replace("BEGIN_RUNGIEC()\n", ""), "LN16", True, "not between"),
    ("LN16 END_RUNGIEC without BEGIN_RUNGIEC", "test", "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A), COIL(Y))").replace("BEGIN_RUNGIEC()\n", ""), "LN16", True,
     "END_RUNGIEC() without a BEGIN_RUNGIEC()"),
    ("LN16 quiet: the POU layout, comments after RUNGIEC_VAR, a comment that names the st directive, a POU without "
     "the DSL", "test", "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A), COIL(Y))\nB := A;")
     .replace("RUNGIEC_VAR()\n", "RUNGIEC_VAR()\n(* the ladder of the POU, without an st directive *)\n")
     + "function_block q\nvar A: bool; end_var\nA := true;\nend_function_block\n", "LN16", False, ""),
    ("LN17 DSL macro in ST_EXEC", "test", "tests/t001_a.iec", user_pou("RUNG(CONTACT(A), ST_EXEC(RUNG(COIL(Y))))"),
     "LN17", True, "DSL macro RUNG"),
    ("LN17 Rungiec name, return and a directive in ST_EXEC", "test", "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A), ST_EXEC(rungiecval := true; return; {#if 1} B := A; {#endif}))"), "LN17", True,
     "directive"),
    ("LN17 return in ST_EXEC", "test", "tests/t001_a.iec", user_pou("RUNG(ST_EXEC(if A then return; end_if;))"), "LN17",
     True, "return"),
    ("LN17 quiet: statements with commas and strings", "test", "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A), ST_EXEC(case N of 1, 2: S := 'RUNG(x) return'; end_case; M := Fn(1, 2);))"), "LN17",
     False, ""),
    ("LN17 goto in ST_EXEC", "test", "tests/t001_a.iec",
     user_pou("RUNG(ST_EXEC(if A then goto Skip; end_if;))\nLABEL(Skip)"), "LN17", True, "goto inside"),
    ("LN17 label in ST_EXEC", "test", "tests/t001_a.iec", user_pou("RUNG(ST_EXEC(Again: N := N + 1;))"), "LN17", True,
     "label Again"),
    ("LN17 quiet: case selectors, jmp in a string", "test", "tests/t001_a.iec",
     user_pou("RUNG(ST_EXEC(case C of Red: S := 'jmp L'; Green, Blue: S := 'goto'; end_case; N := 1;))"), "LN17",
     False, ""),
    ("LN18 comment between children", "test", "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A), (* note *) COIL(Y))"), "LN18", True, "comment inside"),
    ("LN18 comment inside ST_EXEC", "test", "tests/t001_a.iec",
     user_pou("RUNG(ST_EXEC(B := A; // note\n))"), "LN18", True, "comment inside"),
    ("LN18 quiet: comment before the call, '//' in a string", "test", "tests/t001_a.iec",
     user_pou("(* |--| A |--( Y )--| *)\nRUNG(CONTACT(A), ST_EXEC(S := 'a//b (*';), COIL(Y))"), "LN18", False, ""),
    ("LN19 bad test name", "test", "tests/T1_a.iec", user_pou("RUNG(CONTACT(A), COIL(Y))"), "LN19", True,
     "a test is named"),
    ("LN19 two-digit test number", "test", "tests/t01_a.iec", user_pou("RUNG(CONTACT(A), COIL(Y))"), "LN19", True,
     "a test is named"),
    ("LN19 quiet: three-digit test number", "test", "tests/t100_a.iec", user_pou("RUNG(CONTACT(A), COIL(Y))"), "LN19",
     False, ""),
    ("LN19 x_ name without an example", "test", "tests/x_none.iec", user_pou("RUNG(CONTACT(A), COIL(Y))"), "LN19", True,
     "after a file in examples"),
    ("LN19 @expect kind for the prefix", "test", "tests/e001_a.iec", user_pou("RUNG(CONTACT(A), COIL(Y))"), "LN19", True,
     "does not match"),
    ("LN19 missing @cycles", "test", "tests/f001_a.iec",
     user_pou("RUNG(CONTACT(A), COIL(Y))", header="(* @expect run-error X *)\n"), "LN19", True, "@cycles"),
    ("LN19 quiet: t and x_ tests", "test", "tests/x_ex.iec", user_pou("RUNG(CONTACT(A), COIL(Y))"), "LN19", False, ""),
    ("LN20 test missing from the test map", "docs", "docs/DESIGN.md",
     dict(GOOD_DOCS, tests=["t001_a", "t002_b", "x_ex"]), "LN20", True, "t002_b is not in the test map"),
    ("LN20 count line differs", "docs", "docs/DESIGN.md",
     dict(GOOD_DOCS, design=GOOD_DOCS["design"].replace("t 1、", "t 2、")), "LN20", True, "count line"),
    ("LN20 example missing from EXAMPLES", "docs", "docs/EXAMPLES.md", dict(GOOD_DOCS, examples_doc="## 例題\n"),
     "LN20", True, "example ex"),
    ("LN20 quiet: documents that match", "docs", "docs/DESIGN.md", GOOD_DOCS, "LN20", False, ""),
    ("LN21 END_MC in an if branch", "test", "tests/t001_a.iec",
     user_pou("BEGIN_MC(CONTACT(A))\nRUNG(COIL(Y))\nif B then\nEND_MC()\nend_if;"), "LN21", True, "END_MC()"),
    ("LN21 BEGIN_NET and END_NET in different case labels", "test", "tests/t001_a.iec",
     user_pou("case N of\n1: BEGIN_NET()\nWIRE(0, 1, COIL(Y))\n2: END_NET()\nend_case;"), "LN21", True, "END_NET()"),
    ("LN21 quiet: pairs in one branch and nested MC", "test", "tests/t001_a.iec",
     user_pou("if B then\nBEGIN_MC(CONTACT(A))\nBEGIN_MC(CONTACT(B))\nRUNG(COIL(Y))\nEND_MC()\nEND_MC()\nelse\n"
              "BEGIN_NET()\nWIRE(0, 1, COIL(Y))\nEND_NET()\nend_if;"), "LN21", False, ""),
    ("LN22 label in another letter case", "test", "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A), JMP(Skip))\nRUNG(COIL(Y))\nLABEL(SKIP)"), "LN22", True, "is spelled Skip"),
    ("LN22 Rungiec label", "test", "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A), JMP(RungiecSkip))\nLABEL(RungiecSkip)"), "LN22", True, "starts with Rungiec"),
    ("LN22 label that is not one name", "test", "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A), JMP(L1 + 1))\nLABEL(L1)"), "LN22", True, "must be one name"),
    ("LN22 quiet: one spelling per POU, another spelling in another POU", "test", "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A), JMP(Skip))\nRUNG(COIL(Y))\nLABEL(Skip)")
     + user_pou("RUNG(CONTACT(A), JMP(SKIP))\nLABEL(SKIP)", header=""), "LN22", False, ""),
    ("LN23 edge contact in a backward-jump loop", "test", "tests/t001_a.iec",
     user_pou("LABEL(Again)\nRUNG(RISE_CONTACT(A), COIL(Y))\nRUNG(CONTACT(B), JMP(Again))"), "LN23", True,
     "RISE_CONTACT(...) is between LABEL(Again)"),
    ("LN23 transition coil in a backward-jump loop", "test", "tests/t001_a.iec",
     user_pou("LABEL(Again)\nRUNG(CONTACT(A), FALL_COIL(Y))\nRUNG(CONTACT(B), JMP(Again))"), "LN23", True,
     "FALL_COIL"),
    ("LN23 quiet: edge contacts outside the loop and after a forward jump", "test", "tests/t001_a.iec",
     user_pou("RUNG(RISE_CONTACT(A), COIL(Y))\nLABEL(Again)\nRUNG(CONTACT(B), JMP(Again))\n"
              "RUNG(CONTACT(A), JMP(Skip))\nRUNG(FALL_CONTACT(B), COIL(Y))\nLABEL(Skip)"), "LN23", False, ""),
    ("LN24 statement of the user after END_RUNGIEC", "test", "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A), COIL(Y))").replace("END_RUNGIEC()\n", "END_RUNGIEC()\nY := A;\n"),
     "LN24", True, "after END_RUNGIEC()"),
    ("LN24 statement of the user before RUNGIEC_VAR", "test", "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A), COIL(Y))", decl="B := A;\nRUNGIEC_VAR()\n"), "LN24", True,
     "before RUNGIEC_VAR*()"),
    ("LN24 statement of the user after END_RUNGIEC in a method with the DSL", "test", "tests/t001_a.iec",
     "(* @expect pass *)\n(* @cycles 1 *)\nfunction_block q\nvar A, Y: bool; end_var\nmethod m\n"
     "RUNGIEC_VAR_NOEDGE()\nBEGIN_RUNGIEC()\nRUNG(CONTACT(A), COIL(Y))\nEND_RUNGIEC()\nA := false;\nend_method\n"
     "end_function_block\n", "LN24", True, "after END_RUNGIEC()"),
    ("LN24 statement of the user before RUNGIEC_VAR in a TEST_M", "test", "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A), COIL(Y))", head="TEST_M(t001_a)\nvar A, B, Y: bool; end_var\nB := A;\n")
     .replace("end_program", "END_TEST_M"), "LN24", True, "before RUNGIEC_VAR*()"),
    ("LN24 quiet: statements in a function block and a method without the DSL", "test", "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A), COIL(Y))")
     + "function_block q\nvar A: bool; end_var\nmethod m\nA := false;\nend_method\nA := true;\nend_function_block\n",
     "LN24", False, ""),
    ("LN24 quiet: one POU with the DSL", "test", "tests/t001_a.iec", user_pou("RUNG(CONTACT(A), COIL(Y))"), "LN24",
     False, ""),
    ("LN24 quiet: declarations, a pragma, comments, using, a nested function and an action around the DSL", "test",
     "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A), COIL(Y))", head="program p\nusing Lib;\nvar constant K: int := 1; end_var\n"
              "{attribute 'hide'}\nvar_temp T: bool; end_var\n(* before *)\n")
     .replace("END_RUNGIEC()\n", "END_RUNGIEC()\n(* after *)\nvar_temp U: bool; end_var\n"
              "function f : dint\nf := 7;\nend_function\naction act:\nA := true;\nend_action\n"), "LN24", False, ""),
    ("LN24 quiet: a TEST_M with the DSL", "test", "tests/t001_a.iec",
     user_pou("T_BEGIN()\nRUNG(CONTACT(A), COIL(Y))", head="TEST_M(t001_a)\nvar A, B, Y: bool; end_var\n")
     .replace("end_program", "END_TEST_M"), "LN24", False, ""),
    ("LN25 positional arguments of FUN_EXEC_RET", "test", "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A), FUN_EXEC_RET(R, Add, 3, 4))"), "LN25", True, "positional argument 3 of FUN_EXEC_RET"),
    ("LN25 positional argument of FB_EXEC", "test", "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A), FB_EXEC(Tm, Start))"), "LN25", True, "positional argument Start of FB_EXEC"),
    ("LN25 ':=' only inside the parentheses of an argument", "test", "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A), FUN_EXEC_RET(R, F, max(IN1 := A, IN2 := B)))"), "LN25", True,
     "positional argument max"),
    ("LN25 comparison as an argument", "example", "examples/ex.iec",
     user_pou("RUNG(CONTACT(A), FUN_EXEC(F, A >= B), FB_ENO(Fb0, C <> D))", header=""), "LN25", True,
     "positional argument C <> D of FB_ENO"),
    ("LN25 positional argument of FB_BOX", "test", "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A), FB_BOX(Tm, IN, Q, T#2s), COIL(Y))"), "LN25", True,
     "positional argument T#2s of FB_BOX"),
    ("LN25 positional argument of FBT_BOX", "test", "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A), FBT_BOX(Tm, TON, IN, Q, T#2s), COIL(Y))"), "LN25", True,
     "positional argument T#2s of FBT_BOX"),
    ("LN25 positional argument of FBT_SCAN", "test", "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A), FBT_SCAN(Tm, TON, S))"), "LN25", True, "positional argument S of FBT_SCAN"),
    ("LN25 quiet: the pins of FB_BOX", "test", "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A), FB_BOX(Tm, IN, Q, PT := T#2s), COIL(Y))"), "LN25", False, ""),
    ("LN25 quiet: the type and pins of FBT_BOX", "test", "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A), FBT_BOX(Tm, TON, IN, Q, PT := T#2s, ET => E), COIL(Y))"), "LN25", False, ""),
    ("LN25 quiet: the type of FBT_SCAN, FBT_IN, FBT_EXEC and FBT_ENO", "test", "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A), FBT_SCAN(Tm, TON, IN := S), FBT_IN(Tm, TON, IN), FBT_EXEC(Fb1, MyFb), "
              "FBT_ENO(Fb0, MyFb, ok_in := B))"), "LN25", False, ""),
    ("LN25 quiet: named arguments of FB_SCAN", "test", "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A), FB_SCAN(Tm, IN := S, PT := T#2s))"), "LN25", False, ""),
    ("LN25 quiet: the pin of FB_IN", "test", "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A), FB_IN(Tm, IN, PT := T#2s))"), "LN25", False, ""),
    ("LN25 quiet: input and output arguments of FUN_ENO_RET", "test", "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A), FUN_ENO_RET(R1, EnoFn, ok_in := Good, ENO => E))"), "LN25", False, ""),
    ("LN25 quiet: no argument, comparisons in named arguments", "test", "tests/t001_a.iec",
     user_pou("RUNG(CONTACT(A), FB_EXEC(Fb1), FUN_EXEC(F, a := B >= C, b := D <= E, c := X[1] = Y))"), "LN25",
     False, ""),
]


def run_selftest_case(kind, label, fragment):
    if kind == "docs":
        found = []
        lint_docs(fragment["design"], fragment["examples_doc"], fragment["tests"], set(fragment["examples"]),
                  lambda line, msg: found.append(Finding("LN20", "docs/DESIGN.md", line, msg)),
                  lambda line, msg: found.append(Finding("LN20", "docs/EXAMPLES.md", line, msg)))
        return found
    if kind == "libset":
        found = []
        lint_public_set(fragment, lambda rule, where, line, msg: found.append(Finding(rule, where, line, msg)))
        return found
    role = {"lib": "library", "helper": "helper", "test": "test", "example": "example"}[kind]
    # A "lib" fragment is not the whole library: the whole-library checks are the "libset" cases.
    return lint_file(label, fragment, role, {"ex"})


def selftest():
    """Run SELFTEST_CASES; every rule needs a firing case and a quiet case. Return the exit code."""
    ok_count = 0
    for name, kind, label, fragment, rule, must_fire, contains in SELFTEST_CASES:
        found = run_selftest_case(kind, label, fragment)
        hit = any(f.rule == rule and contains in f.message for f in found)
        good = hit == must_fire
        if must_fire:
            verdict = "fired" if hit else "NOT FIRED"
        else:
            verdict = "quiet" if not hit else "FIRED UNEXPECTEDLY: " + "; ".join(
                f.message for f in found if f.rule == rule)[:200]
        print("SELFTEST %s: %s" % (name, verdict))
        if good:
            ok_count += 1
    total = len(SELFTEST_CASES)
    firing = sum(1 for case in SELFTEST_CASES if case[5])
    missing = []
    for rule in sorted(RULES, key=lambda r: int(r[2:])):
        fire = sum(1 for case in SELFTEST_CASES if case[4] == rule and case[5])
        quiet = sum(1 for case in SELFTEST_CASES if case[4] == rule and not case[5])
        if not fire or not quiet:
            missing.append("%s (%d firing, %d quiet)" % (rule, fire, quiet))
    if missing:
        print("SELFTEST: rules without a firing and a quiet case: %s" % ", ".join(missing))
    print("SELFTEST: %d/%d cases as expected (%d firing, %d quiet), %d rules covered"
          % (ok_count, total, firing, total - firing, len(RULES) - len(missing)))
    return 0 if ok_count == total and not missing else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--tests-dir", default=os.path.join(ROOT, "tests"))
    parser.add_argument("--examples-dir", default=os.path.join(ROOT, "examples"))
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    return run_lint(ROOT, os.path.abspath(args.tests_dir), os.path.abspath(args.examples_dir))


if __name__ == "__main__":
    sys.exit(main())
