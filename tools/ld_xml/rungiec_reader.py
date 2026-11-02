"""RUNGIEC fragment (as written by xml2rungiec) -> units of rungs -> SP trees.

The fragment is read with tools/lint_rungiec.py (Source masks, dsl_calls, match_paren, split_args). A unit is the
statements under one '(* @rung N *)' marker: one RUNG, or one BEGIN_MC / END_MC, each optionally preceded by
LABEL(L) (the label of the rung). A '(* @label N *)' marker is
followed by one LABEL(L) only: the label of source rung N, which is not converted (a label-only unit).
A '(* @skipped N <reason> *)' marker only records a source rung that is not converted: it is kept in
Fragment.skipped, ends the current unit (statements after it, outside a @rung / @label unit, are loose), is never a
comment of a unit and produces no XML.
Only the subset that xml2rungiec generates is read; anything else is reported as unsupported.
"""

import re

from . import TOOLS_DIR  # noqa: F401
import lint_rungiec

from . import ascii_art, model
from .model import Arg, Element

MARKER_RE = re.compile(r"\(\*\s*@(rungiec-fragment|source|body|instance|rung|label|skipped)\b(.*?)\*\)", re.DOTALL)
COMMENT_RE = re.compile(r"\(\*(.*?)\*\)", re.DOTALL)
CONTACT_NAMES = {v: k for k, v in model.CONTACT_MACROS.items()}
COIL_NAMES = {v: k for k, v in model.COIL_MACROS.items()}
CMP_NAMES = {v: k for k, v in model.CMP_MACROS.items()}
# The function block boxes that xml2rungiec writes: (element kind, number of pin arguments after the instance, and
# after the type of FBT_*).
FB_BOXES = {
    "FB_SCAN": ("fb_scan", 0), "FB_IN": ("fb_in", 1), "FB_BOX": ("fb_in", 2), "FB_ENO": ("fb_eno", 0),
    "FBT_SCAN": ("fb_scan", 0), "FBT_IN": ("fb_in", 1), "FBT_BOX": ("fb_in", 2), "FBT_ENO": ("fb_eno", 0),
}


class Unsupported(Exception):
    def __init__(self, reason, detail):
        Exception.__init__(self, "%s: %s" % (reason, detail))
        self.reason, self.detail = reason, detail


class Node(object):
    """A parsed call: name, argument texts (original text) and argument nodes (for combinators)."""
    __slots__ = ("name", "args", "kids", "pos")

    def __init__(self, name, args, kids, pos):
        self.name, self.args, self.kids, self.pos = name, args, kids, pos


class Unit(object):
    __slots__ = ("ordinal", "comments", "statements", "line", "label_only")

    def __init__(self, ordinal, line, label_only=False):
        self.ordinal, self.line, self.label_only = ordinal, line, label_only
        self.comments = []
        self.statements = []


class Fragment(object):
    def __init__(self, label):
        self.label = label
        self.source = None
        self.body = None
        self.pou = None
        self.section = None
        self.instances = {}
        self.units = []
        self.skipped = []  # (ordinal, reason) of the '@skipped' markers
        self.loose = []  # statements outside any @rung marker
        self.mc_depth = 0  # open BEGIN_MC regions while the units are read in order (unit_rung updates it)


def parse_call(src, start):
    """Parse NAME( ... ) at code position start. Return Node."""
    code = src.code
    m = re.compile(r"\s*([A-Za-z_]\w*)\s*\(").match(code, start)
    if not m:
        raise Unsupported("unsupported-form", "not a call: %s" % code[start:start + 40].strip())
    open_pos = m.end() - 1
    close = lint_rungiec.match_paren(code, open_pos)
    if close is None:
        raise Unsupported("unsupported-form", "unbalanced call %s" % m.group(1))
    name = m.group(1)
    spans = lint_rungiec.split_args(code, open_pos, close)
    args = [src.text[a:b].strip() for a, b in spans]
    if len(args) == 1 and not args[0]:
        args = []
    kids = []
    if name in ("RUNG", "SERIES", "PARALLEL", "BEGIN_MC"):
        for a, b in spans:
            if code[a:b].strip():
                kids.append(parse_call(src, a)[0])
    return Node(name, args, kids, m.start(1)), close + 1


def read_fragment(text, label):
    src = lint_rungiec.Source(label, text.replace("\r\n", "\n"))
    frag = Fragment(label)
    calls = [c for c in lint_rungiec.dsl_calls(src) if c.parent is None]
    events = []
    for c in calls:
        events.append((c.start, "call", c))
    spans = [(c.start, c.close) for c in calls]

    def inside(pos):
        return any(a <= pos <= b for a, b in spans)

    for m in COMMENT_RE.finditer(src.text):
        if src.nostr[m.start():m.start() + 2] != "(*":
            continue  # not a comment (inside a string)
        if inside(m.start()):
            continue
        events.append((m.start(), "comment", m))
    events.sort(key=lambda e: e[0])
    unit = None
    for pos, kind, obj in events:
        if kind == "comment":
            mk = MARKER_RE.match(obj.group(0))
            if mk:
                what, rest = mk.group(1), mk.group(2).strip()
                if what == "source":
                    frag.source = rest
                elif what == "body":
                    parts = rest.split(None, 2)
                    frag.body = int(parts[0]) if parts and parts[0].isdigit() else None
                    frag.pou = parts[1] if len(parts) > 1 else ""
                    frag.section = parts[2] if len(parts) > 2 else ""
                elif what == "instance":
                    name, _, typ = rest.rpartition(":")
                    frag.instances[name.strip()] = typ.strip()
                elif what in ("rung", "label"):
                    unit = Unit(int(rest), src.line(pos), label_only=what == "label")
                    frag.units.append(unit)
                elif what == "skipped":
                    parts = rest.split(None, 1)
                    frag.skipped.append((int(parts[0]) if parts and parts[0].isdigit() else None,
                                         parts[1].strip() if len(parts) > 1 else ""))
                    unit = None
                continue
            if ascii_art.is_art(obj.group(1)):
                continue  # the diagram of the rung
            if unit is not None:
                unit.comments.append(obj.group(1).strip())
            continue
        node, _ = parse_call(src, obj.start)
        if unit is None:
            frag.loose.append(node)
        else:
            unit.statements.append(node)
    return frag


def _named_args(texts, what):
    args = []
    for i, t in enumerate(texts):
        m = re.match(r"\s*([A-Za-z_]\w*)\s*(:=|=>)(.*)\Z", t, re.DOTALL)
        if not m:
            raise Unsupported("unsupported-form", "positional argument %r of %s" % (t[:30], what))
        args.append(Arg(m.group(1), m.group(2), m.group(3).strip(), i))
    return args


def _split_call(text):
    """'Fn(a := 1, b := 2)' -> ('Fn', [arg texts]) or None."""
    m = re.match(r"\s*([A-Za-z_]\w*)\s*\(", text)
    if not m:
        return None
    src = lint_rungiec.Source("operand", text)
    close = lint_rungiec.match_paren(src.code, m.end() - 1)
    if close is None or src.code[close + 1:].strip():
        return None
    spans = lint_rungiec.split_args(src.code, m.end() - 1, close)
    args = [text[a:b].strip() for a, b in spans]
    if len(args) == 1 and not args[0]:
        args = []
    return m.group(1), args


def element(node, frag, order):
    n, a = node.name, node.args
    if n in CONTACT_NAMES:
        sub = CONTACT_NAMES[n]
        call = _split_call(a[0]) if sub == "NO" else None
        if call is not None and all(re.match(r"\s*[A-Za-z_]\w*\s*:=", t) for t in call[1]):
            return Element("callcontact", order, name=call[0], args=_named_args(call[1], n))
        return Element("contact", order, sub=sub, name=a[0])
    if n in CMP_NAMES:
        return Element("cmp", order, sub=CMP_NAMES[n], ops=[a[0], a[1]])
    if n in COIL_NAMES:
        return Element("coil", order, sub=COIL_NAMES[n], name=a[0])
    if n in FB_BOXES:
        # FB_<box>(inst, pins..., args...) takes the type from the @instance marker, FBT_<box>(inst, type, pins...,
        # args...) from its second argument.
        kind, npins = FB_BOXES[n]
        inst = a[0]
        if n.startswith("FBT_"):
            if len(a) < 2:
                raise Unsupported("unsupported-form", "%s without a type" % n)
            fbtype, rest = a[1], a[2:]
        else:
            if inst not in frag.instances:
                raise Unsupported("unknown-fb-type", "no @instance for %s" % inst)
            fbtype, rest = frag.instances[inst], a[1:]
        if len(rest) < npins:
            raise Unsupported("unsupported-form", "%s without its pins" % n)
        pins, args = rest[:npins], _named_args(rest[npins:], n)
        if kind == "fb_in":
            return Element("fb_in", order, name=inst, fbtype=fbtype, pin=pins[0],
                           power_out=pins[1] if npins == 2 else None, args=args)
        return Element(kind, order, name=inst, fbtype=fbtype, args=args)
    if n == "FUN_ENO":
        return Element("fn_eno", order, name=a[0], args=_named_args(a[1:], n))
    if n == "FUN_ENO_RET":
        return Element("fn_eno", order, name=a[1], dst=a[0], args=_named_args(a[2:], n))
    if n == "ST_EXEC":
        raise Unsupported("st-exec-no-standard-form", "ST_EXEC")
    if n == "JMP":
        if len(a) != 1:
            raise Unsupported("unsupported-form", "JMP with %d arguments" % len(a))
        return Element("jump", order, name=a[0])
    raise Unsupported("unsupported-form", "%s(...)" % n)


def tree_of(node, frag, unpowered, counter):
    counter[0] += 1
    if node.name in ("SERIES", "PARALLEL", "RUNG"):
        kids = []
        for k in node.kids:
            t = tree_of(k, frag, unpowered, counter)
            if t is not None:
                kids.append(t)
        if not kids:
            return None
        return ("P" if node.name == "PARALLEL" else "S", kids)
    e = element(node, frag, counter[0])
    if e.kind == "fb_scan":
        unpowered.append(e)
        return None
    return model.leaf(e)


def _contains(node, names):
    if node.name in names:
        return True
    return any(_contains(k, names) for k in node.kids)


def label_arg(node):
    """The label of a LABEL(L) node. Raises Unsupported."""
    if len(node.args) != 1 or not re.match(r"[A-Za-z_]\w*\Z", node.args[0]):
        raise Unsupported("unsupported-form", "LABEL(%s)" % ", ".join(node.args)[:40])
    return node.args[0]


def unit_label(unit):
    """The label of a label-only unit ('(* @label N *)' then LABEL(L)). Raises Unsupported."""
    if len(unit.statements) != 1 or unit.statements[0].name != "LABEL":
        raise Unsupported("unsupported-form", "a @label marker is not followed by exactly one LABEL")
    return label_arg(unit.statements[0])


MC_MAX = 15  # Omron MC nesting depth (to_rungiec.MC_MAX)


def mc_rung(node, frag):
    """(tree, unpowered) of a BEGIN_MC(cond...) / END_MC() unit, in the form Omron writes: the MC block (In = the
    power of the conditions, MCNo = the nesting depth, 0-based) or the MCR block (EN = the left rail). The depth is
    counted in frag.mc_depth; a BEGIN_MC counts even when it cannot be written, so that its END_MC stays paired.
    BEGIN_MC(CONTACT(true)) is the form xml2rungiec writes for an MC without condition: no contact is written."""
    if node.name == "END_MC":
        if node.args:
            raise Unsupported("unsupported-form", "END_MC with arguments")
        if frag.mc_depth <= 0:
            raise Unsupported("mc-unbalanced", "END_MC without BEGIN_MC")
        frag.mc_depth -= 1
        if frag.mc_depth >= MC_MAX:
            raise Unsupported("mc-nesting-too-deep", "END_MC of a BEGIN_MC deeper than %d" % MC_MAX)
        return model.leaf(Element("mcr", 1, mcno=str(frag.mc_depth))), []
    depth = frag.mc_depth
    frag.mc_depth += 1
    if depth >= MC_MAX:
        raise Unsupported("mc-nesting-too-deep", "BEGIN_MC nesting exceeds %d" % MC_MAX)
    if _contains(node, ("ST_EXEC",)):
        raise Unsupported("st-exec-no-standard-form", "ST_EXEC")
    unpowered = []
    counter = [0]
    kids = []
    for k in node.kids:
        t = tree_of(k, frag, unpowered, counter)
        if t is not None:
            kids.append(t)
    if len(kids) == 1 and kids[0][0] == "L" and kids[0][1].kind == "contact" and kids[0][1].sub == "NO" and             kids[0][1].name == "true":
        kids = []
    kids.append(model.leaf(Element("mc", counter[0] + 1, mcno=str(depth))))
    return model.flatten(("S", kids)), unpowered


def unit_rung(unit, frag):
    """Return (tree, unpowered, label) of one unit (label None when the unit has no LABEL). Raises Unsupported."""
    stmts = unit.statements
    label = None
    if stmts and stmts[0].name == "LABEL":
        label = label_arg(stmts[0])
        stmts = stmts[1:]
    if not stmts:
        raise Unsupported("unsupported-form", "no statement")
    if stmts[0].name in ("BEGIN_MC", "END_MC"):
        if len(stmts) != 1:
            raise Unsupported("unsupported-form", "%s with other statements in one unit" % stmts[0].name)
        tree, unpowered = mc_rung(stmts[0], frag)
        return tree, unpowered, label
    for s in stmts:
        if s.name in ("BEGIN_NET", "WIRE", "END_NET"):
            raise Unsupported("net-not-supported-v1", s.name)
        if s.name != "RUNG":
            raise Unsupported("unsupported-form", "top-level %s" % s.name)
        if _contains(s, ("ST_EXEC",)):
            raise Unsupported("st-exec-no-standard-form", "ST_EXEC")
    if len(stmts) != 1:
        raise Unsupported("unsupported-form", "several statements in one unit")
    unpowered = []
    counter = [0]
    series = []
    for k in stmts[0].kids:
        t = tree_of(k, frag, unpowered, counter)
        if t is not None:
            series.append(t)
    if not series:
        return None, unpowered, label
    return model.flatten(("S", series)), unpowered, label
