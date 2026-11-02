"""SP tree -> RUNGIEC text.

Rules: top level RUNG(...) only (no ';' after it), SERIES only inside; a PARALLEL that mixes coils and other
children gets each bare coil wrapped in SERIES (lint LN12); more than ARITY_MAX children are split associatively;
a nesting deeper than NEST_MAX is skipped. A function block whose output carries the power on becomes FBT_BOX
(FB_BOX without a type); the function block boxes are FBT_* when the Block has a typeName and FB_* otherwise.
MC / MCR rungs become BEGIN_MC(...) / END_MC().
A Jump becomes JMP(label), the last child of the RUNG; a rung label becomes LABEL(label) before the statements of
the rung, also when the rung itself is not converted (written after a '(* @label N *)' marker then).
Every rung that is not converted leaves '(* @skipped N <reason> *)' at its position (N = the ordinal of
'(* @rung N *)', reason = the skip reason code without its ':<detail>' suffix). A body with a rung whose loss could
change the meaning of other rungs has all rungs skipped and gets no fragment. The checks run in this order and each
rung keeps its first reason: broken MC structure (mc-structure for the MC / MCR rungs, mc-body for the others), a
rung with a FOR / NEXT / BREAK block (loop for it, loop-body for the others), a Jump / Return, or an InlineST with a
return, that is not converted (jump-body for the converted rungs).
The judgement is per body (one POU section, one convert_body call): the other sections of the same POU are not
looked at.
"""

import re

from . import ascii_art, model, st_text
from .model import Element

ARITY_MAX = 16  # RUNGIEC_ARITY_MAX default
NEST_MAX = 16   # RUNGIEC_NEST_MAX default
MC_MAX = 15     # RUNGIEC_MC_MAX default
BODY_SKIPS = ("mc-body", "loop-body", "jump-body")  # reasons of the rungs of a body skipped whole (convert_body)


class Skip(Exception):
    def __init__(self, reason, detail):
        Exception.__init__(self, "%s: %s" % (reason, detail))
        self.reason, self.detail = reason, detail


def _args(args):
    return ", ".join(a.text() for a in args)


def _call(name, parts):
    parts = [p for p in parts if p]
    return "%s(%s)" % (name, ", ".join(parts))


def _fb_call(box, e, pins):
    """FBT_<box>(inst, type, pins..., args...) when the element has a type, FB_<box>(inst, pins..., args...)
    otherwise."""
    if e.fbtype:
        return _call("FBT_" + box, [e.name, e.fbtype] + pins + [_args(e.args)])
    return _call("FB_" + box, [e.name] + pins + [_args(e.args)])


def elem_text(e):
    k = e.kind
    if k == "contact":
        return "%s(%s)" % (model.CONTACT_MACROS[e.sub], e.name)
    if k == "cmp":
        return "%s(%s, %s)" % (model.CMP_MACROS[e.sub], e.ops[0], e.ops[1])
    if k == "callcontact":
        return "CONTACT(%s(%s))" % (e.name, _args(e.args))
    if k == "coil":
        return "%s(%s)" % (model.COIL_MACROS[e.sub], e.name)
    if k == "fb_in":
        if e.power_out:
            return _fb_call("BOX", e, [e.pin, e.power_out])
        return _fb_call("IN", e, [e.pin])
    if k == "fb_eno":
        return _fb_call("ENO", e, [])
    if k == "fn_eno":
        if e.dst:
            return _call("FUN_ENO_RET", [e.dst, e.name, _args(e.args)])
        return _call("FUN_ENO", [e.name, _args(e.args)])
    if k == "fb_scan":
        return _fb_call("SCAN", e, [])
    if k == "st_exec":
        return "ST_EXEC(%s)" % e.text
    if k == "jump":
        return "JMP(%s)" % e.name
    raise Skip("mc-structure", "%s inside a network" % k)


def _is_bare_coil(tree):
    return tree[0] == "L" and tree[1].is_coil()


def render_items(kind, kids, limit=ARITY_MAX):
    """Render the children of a series ('S') or parallel ('P'). Return ([(text, depth)], ...) as a list where depth
    is the combinator depth of each item (0 for an element). At most limit items (the rest is split off)."""
    items = []
    if kind == "P":
        coils = [k for k in kids if _is_bare_coil(k)]
        wrap = bool(coils) and (len(coils) != len(kids) or len(kids) > ARITY_MAX)
    else:
        wrap = False
    for k in kids:
        text, depth = render_node(k)
        if wrap and _is_bare_coil(k):
            text, depth = "SERIES(%s)" % text, 1
        items.append((text, depth))
    return _split(kind, items, limit)


def _split(kind, items, limit=ARITY_MAX):
    if len(items) <= limit:
        return items
    head = items[:limit - 1]
    rest = _split(kind, items[limit - 1:])
    name = "SERIES" if kind == "S" else "PARALLEL"
    text = "%s(%s)" % (name, ", ".join(t for t, _ in rest))
    return head + [(text, 1 + max(d for _, d in rest))]


def render_node(tree):
    if tree[0] == "L":
        return elem_text(tree[1]), 0
    items = render_items(tree[0], tree[1])
    name = "SERIES" if tree[0] == "S" else "PARALLEL"
    return "%s(%s)" % (name, ", ".join(t for t, _ in items)), 1 + max(d for _, d in items)


def statement(name, kids, tail_elem=None):
    """RUNG / BEGIN_MC with the given child trees (in series), preceded by the lines of its ASCII-art diagram (one
    string, the lines joined by a newline; tail_elem is the MC coil that the diagram shows after the conditions of
    BEGIN_MC). A JMP stays the last child of the statement when the children are split (lint LN13)."""
    drawn = list(kids) + ([model.leaf(tail_elem)] if tail_elem is not None else [])
    tail = []
    if kids and kids[-1][0] == "L" and kids[-1][1].kind == "jump":
        kids, tail = kids[:-1], [(elem_text(kids[-1][1]), 0)]
    items = render_items("S", kids, ARITY_MAX - len(tail)) + tail
    depth = 1 + max(d for _, d in items)
    if depth > NEST_MAX:
        raise Skip("nest-too-deep", "nesting %d exceeds %d" % (depth, NEST_MAX))
    return "\n".join(ascii_art.rung_art(drawn) + ["%s(%s)" % (name, ", ".join(t for t, _ in items))])


def _top_children(tree):
    if tree is None:
        return []
    tree = model.flatten(tree)
    return list(tree[1]) if tree[0] == "S" else [tree]


def rung_statements(res):
    """The RUNGIEC statements of one converted rung (one statement). Raises Skip."""
    kids = _top_children(res.tree)
    if res.has_mc or res.has_mcr:
        return [mc_statement(res, kids)]
    for i, k in enumerate(kids):
        if any(e.kind == "jump" for e in model.leaves(k)) and (i != len(kids) - 1 or k[0] != "L"):
            raise Skip("jump-position", "the jump is not the last element of the top-level series")
    scans = [model.leaf(e) for e in sorted(res.unpowered, key=lambda x: x.order)]
    return [statement("RUNG", scans + kids)]


def mc_statement(res, kids):
    if res.unpowered:
        raise Skip("mc-structure", "MC/MCR rung with other function blocks")
    if not kids or kids[-1][0] != "L" or kids[-1][1].kind not in ("mc", "mcr"):
        raise Skip("mc-structure", "MC/MCR is not the last element of the top-level series")
    last = kids[-1][1]
    conds = kids[:-1]
    for t in conds:
        for e in model.leaves(t):
            if e.kind in ("mc", "mcr"):
                raise Skip("mc-structure", "several MC/MCR in one rung")
            if e.kind == "jump":
                raise Skip("jump-position", "a jump in an MC/MCR rung")
    if last.kind == "mcr":
        if conds:
            raise Skip("mc-structure", "MCR with a condition")
        return "\n".join(ascii_art.rung_art([model.leaf(last)]) + ["END_MC()"])
    if not conds:
        conds = [model.leaf(Element("contact", last.order, sub="NO", name="true"))]
    return statement("BEGIN_MC", conds, last)


def check_mc_body(results, statements):
    """MC/MCR pairing per body (LIFO, matching MCNo, depth <= MC_MAX). On failure every converted MC/MCR rung of
    the body is skipped as mc-structure and every other converted rung of the body as mc-body (the whole body is
    skipped). statements: {ordinal: [text]} of the converted rungs (updated in place)."""
    mc_rungs = [r for r in results if r.has_mc or r.has_mcr]
    if not mc_rungs:
        return None
    stack = []
    problem = None
    for r in mc_rungs:
        if r.status != "converted" or r.ordinal not in statements:
            problem = "MC/MCR rung %d is not converted" % r.ordinal
            break
        mc = next(e for e in model.leaves(r.tree) if e.kind in ("mc", "mcr"))
        no = st_text.canon(mc.mcno or "")
        if mc.kind == "mc":
            stack.append(no)
            if len(stack) > MC_MAX:
                problem = "MC nesting exceeds %d at rung %d" % (MC_MAX, r.ordinal)
                break
        else:
            if not stack or stack[-1] != no:
                problem = "MCR %s at rung %d does not close the innermost MC" % (no, r.ordinal)
                break
            stack.pop()
    if problem is None and stack:
        problem = "MC %s is not closed" % stack[-1]
    if problem is None:
        return None
    for r in mc_rungs:
        if r.status == "converted":
            r.skip("mc-structure", problem)
            statements.pop(r.ordinal, None)
    for r in results:
        _unconvert(r, statements, "mc-body", "the MC/MCR structure of the body fails: %s" % problem)
    return problem


LABEL_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
# Words that cannot be an ST label besides lint_rungiec.KEYWORDS (the jump keywords and the POU / block words).
LABEL_RESERVED = frozenset(("goto", "jmp", "var", "end_var", "program", "end_program", "function",
                            "end_function", "function_block", "end_function_block", "method", "end_method"))


def label_problem(name):
    """None if name can be the label of JMP / LABEL (one ASCII ST name, not a keyword, not a RUNGIEC or jiepp name;
    lint LN22), else the detail."""
    if not name or not LABEL_RE.match(name):
        return "label %r is not one name" % (name or "")[:40]
    if name.lower() in st_text.KEYWORDS or name.lower() in LABEL_RESERVED:
        return "label %s is a keyword" % name
    return st_text.name_issue(name)


def _unconvert(r, statements, reason, detail):
    if r.status == "converted":
        r.skip(reason, detail)
    statements.pop(r.ordinal, None)


def check_labels(results, statements):
    """Decide which rung labels are written as LABEL(...) (r.emit_label). A label that is not a valid name or that
    two rungs of the body carry (the ST labels ignore the letter case) is not written, and a converted rung that
    carries it is skipped (label-name / label-duplicate). Run before check_mc_body, which then sees the skipped MC
    rungs. Return {lowercase label: (reason, detail)} of the labels not written."""
    by_name = {}
    for r in results:
        r.emit_label = None
        if r.label:
            by_name.setdefault(r.label.lower(), []).append(r)
    bad = {}
    for low, rs in by_name.items():
        problem = label_problem(rs[0].label)
        if problem:
            bad[low] = ("label-name", problem)
        elif len(rs) > 1:
            bad[low] = ("label-duplicate", "label %s is on rungs %s" % (
                rs[0].label, ", ".join("%d (%s)" % (r.ordinal, r.label) for r in rs)))
    for r in results:
        if not r.label:
            continue
        if r.label.lower() in bad:
            _unconvert(r, statements, *bad[r.label.lower()])
        else:
            r.emit_label = r.label
    return bad


def check_jumps(results, statements, bad):
    """Skip the converted jump rungs whose JMP(L) RUNGIEC would reject or lint would report: the label is not written
    (bad, see check_labels) or not in the body, spelled in another letter case (LN22), in another MC segment
    (BEGIN_MC / END_MC between them: PP91), or a backward jump over edge contacts / transition coils (LN23).
    Run after check_mc_body (the segments are those of the written statements)."""
    seg = 0
    label_at = {}
    where = {}
    for idx, r in enumerate(results):
        if r.emit_label:
            label_at[r.emit_label.lower()] = (idx, seg, r.emit_label, r.ordinal)
        where[r.ordinal] = (idx, seg)
        if r.ordinal in statements and (r.has_mc or r.has_mcr):
            seg += 1
    for r in results:
        if r.ordinal not in statements or r.jump is None:
            continue
        target = r.jump
        low = target.lower()
        idx, jseg = where[r.ordinal]
        problem = None
        target_problem = label_problem(target)
        if target_problem:
            problem = ("label-name", "jump to " + target_problem)
        elif low in bad:
            problem = (bad[low][0], "jump to %s: %s" % (target, bad[low][1]))
        elif low not in label_at:
            problem = ("jump-target-missing", "no rung of this body has the label %s" % target)
        else:
            lidx, lseg, spelled, lord = label_at[low]
            if spelled != target:
                problem = ("label-name", "jump to %s, the label is spelled %s (rung %d)" % (target, spelled, lord))
            elif lseg != jseg:
                problem = ("jump-across-mc", "jump to %s (rung %d) across BEGIN_MC or END_MC" % (target, lord))
            elif lidx <= idx:
                edges = [x.ordinal for x in results[lidx:idx + 1] if x.ordinal in statements and x.edge_count]
                if edges:
                    problem = ("jump-loop-edge", "backward jump to %s (rung %d) over edge contacts or transition "
                                                 "coils in rungs %s" % (target, lord, ", ".join(map(str, edges))))
        if problem:
            _unconvert(r, statements, *problem)


def convert_body(results):
    """results: RungResults of one body in order. Return {ordinal: [statements]} for the converted rungs and
    update the status of the rungs skipped here. A body whose MC structure fails (check_mc_body), or that has a
    FOR / NEXT / BREAK rung (loop-body: the rungs between FOR and NEXT would run once instead of N times), or a
    Jump / Return rung that is not converted, has all its rungs skipped (mc-body, loop-body, jump-body) and returns
    no statements: such a rung decides which other rungs run, and the body cannot be written without it. The
    checks run in this order: MC structure, loop, jump."""
    statements = {}
    for r in results:
        if r.status != "converted":
            continue
        try:
            statements[r.ordinal] = rung_statements(r)
        except Skip as exc:
            r.skip(exc.reason, exc.detail)
    bad = check_labels(results, statements)
    if check_mc_body(results, statements):
        return statements
    if any(r.has_loop for r in results):
        for r in results:
            _unconvert(r, statements, "loop-body", "the body has a FOR / NEXT / BREAK block")
        return statements
    check_jumps(results, statements, bad)
    lost = next((r for r in results if r.has_jump and r.ordinal not in statements), None)
    if lost is not None:
        for r in results:
            _unconvert(r, statements, "jump-body", "rung %d has a Jump / Return that is not converted (%s)" % (
                lost.ordinal, lost.reason))
    return statements


JUMP_REASONS = ("jump-position", "jump-target-missing", "jump-across-mc", "jump-loop-edge")


def whole_body_reason(reasons):
    """The whole-body reason (one of BODY_SKIPS) of a body without a fragment, judged from the reason codes (without
    ':<detail>') of its rungs that are not converted, or None when no rung shows one. Same order as convert_body: MC
    structure, loop, jump. A Jump / Return rung skipped for a reason that is not a jump reason (block-wire ...) cannot
    be told from the reasons; its body shows jump-body only when a converted rung got jump-body."""
    reasons = set(reasons)
    if "mc-body" in reasons or "mc-structure" in reasons:
        return "mc-body"
    if "loop-body" in reasons or "loop" in reasons:
        return "loop-body"
    if "jump-body" in reasons or reasons.intersection(JUMP_REASONS):
        return "jump-body"
    return None


def skipped_reason(r):
    """The reason code of a rung that is not converted, as written in '(* @skipped N <reason> *)': the code without
    its ':<detail>' suffix (unknown-object:<type> carries an XML name), one word, safe in a comment."""
    code = (r.reason or "unknown").split(":", 1)[0]
    return st_text.comment_text(code).replace(" ", "-") or "unknown"


def fragment(body, source, results, statements):
    """Return (text, index) for one body: index = [(ordinal, first line, last line)]."""
    instances = {}
    for r in results:
        if r.ordinal in statements:
            instances.update(r.instances)
    lines = ["(* @rungiec-fragment 1 *)", "(* @source %s *)" % st_text.comment_text(source),
             "(* @body %d %s %s *)" % (body.ordinal, st_text.comment_text(body.pou),
                                      st_text.comment_text(body.section) or "-")]
    for name in sorted(instances):
        lines.append("(* @instance %s: %s *)" % (name, instances[name]))
    index = []
    for r in results:
        if r.ordinal not in statements:
            # Order of the lines of a rung that is not converted: the '@skipped' marker (always), then, when the rung
            # carries a label, the '@label' marker with the diagram of the label and LABEL(...). No diagram of the
            # rung itself.
            lines.append("(* @skipped %d %s *)" % (r.ordinal, skipped_reason(r)))
            if r.emit_label:
                # The rung is not converted, its label is kept so that the jumps to it stay valid.
                lines.append("(* @label %d *)" % r.ordinal)
                lines.extend(ascii_art.label_art(r.emit_label))
                lines.append("LABEL(%s)" % r.emit_label)
            continue
        first = len(lines) + 1
        lines.append("(* @rung %d *)" % r.ordinal)
        for c in r.comments:
            text = st_text.comment_text(c)
            if text:
                lines.append("(* %s *)" % text)
        if r.emit_label:
            lines.extend(ascii_art.label_art(r.emit_label))
            lines.append("LABEL(%s)" % r.emit_label)
        for st in statements[r.ordinal]:
            lines.extend(st.split("\n"))  # the diagram lines and the statement
        index.append((r.ordinal, first, len(lines)))
    return "\n".join(lines) + "\n", index
