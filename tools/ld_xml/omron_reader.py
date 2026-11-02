"""Read LD rungs of an IEC 61131-10 XML file (Omron Sysmac Studio export or standard) into rung graphs.

iterparse only: each Rung element is converted and cleared. The reader yields one RungResult per Rung of an LD
body; the result is either an SP tree (with the unpowered elements and the comment) or a skip reason.
"""

import re
import xml.etree.ElementTree as ET

from . import model, st_text
from .model import Arg, Element

NS = "www.iec.ch/public/TC65SC65BWG7TF10"
SMC = "https://www.ia.omron.com/Smc"
XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"
POU_TAGS = ("Program", "FunctionBlock", "Function")
LD_BODY_TYPES = ("smcext:LdSection", "LD")

# Skip reasons in precedence order (the first that applies wins). unknown-object is unknown-object:<type>.
# The label reasons after mc-structure are given per body by to_rungiec.check_labels and the jump reasons by
# to_rungiec.check_jumps (after the rungs are converted), except jump-position, which the conversion of the rung gives. mc-body, loop-body and jump-body are
# given by to_rungiec.convert_body to the remaining converted rungs when it skips a whole body (broken MC structure,
# a FOR / NEXT / BREAK rung, a Jump / Return that is not converted); it checks them in this order.
PRECEDENCE = (
    "empty", "loop", "unknown-object", "block-wire", "multi-power-pin", "pin-modifier",
    "non-ascii", "name-clash", "operand", "st-text", "st-unpowered", "unpowered-function", "compare-arity",
    "coil-modifier", "inout-mismatch", "general-net", "nest-too-deep", "mc-structure",
    "jump-position", "label-name", "label-duplicate", "jump-target-missing", "jump-across-mc", "jump-loop-edge",
    "mc-body", "loop-body", "jump-body",
)
LOOP_BLOCKS = ("FOR", "NEXT", "BREAK")
JUMP_BLOCKS = ("RETURN", "JMP")  # blocks (typeName, any letter case) that decide whether later rungs run, like Jump
CMP_TYPES = {"=": "EQ", "<>": "NE", "<": "LT", "<=": "LE", ">": "GT", ">=": "GE",
             "EQ": "EQ", "NE": "NE", "LT": "LT", "LE": "LE", "GT": "GT", "GE": "GE"}
STD_CMP = {"=": "EQ", "<>": "NE", "<": "LT", "<=": "LE", ">": "GT", ">=": "GE"}


def local(tag):
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def reason_key(reason):
    base = reason.split(":", 1)[0]
    try:
        return PRECEDENCE.index(base)
    except ValueError:
        return len(PRECEDENCE)


def pick(issues):
    """The first issue by precedence (stable for equal reasons)."""
    if not issues:
        return None
    return min(enumerate(issues), key=lambda p: (reason_key(p[1][0]), p[0]))[1]


class BodyInfo(object):
    __slots__ = ("ordinal", "pou", "pou_kind", "section", "btype", "is_ld", "rungs")

    def __init__(self, ordinal, pou, pou_kind, section, btype):
        self.ordinal, self.pou, self.pou_kind, self.section, self.btype = ordinal, pou, pou_kind, section, btype
        self.is_ld = btype in LD_BODY_TYPES
        self.rungs = 0


class RungResult(object):
    __slots__ = ("body", "ordinal", "eval_order", "status", "reason", "detail", "tree", "unpowered", "comments",
                 "has_mc", "has_mcr", "has_jump", "has_loop", "instances", "edge_count", "label", "jump", "emit_label")

    def __init__(self, body, ordinal, eval_order):
        self.body, self.ordinal, self.eval_order = body, ordinal, eval_order
        self.status = "converted"
        self.reason = self.detail = None
        self.tree = None
        self.unpowered = []
        self.comments = []
        self.has_mc = self.has_mcr = False
        self.has_jump = False   # the Rung has a Jump or Return element directly, or an InlineST with a return: it
                                # decides whether later rungs run
        self.has_loop = False   # the Rung has a FOR / NEXT / BREAK block directly: it repeats or leaves rungs
        self.instances = {}
        self.edge_count = 0
        self.label = None       # Rung@label as written (None when absent or empty)
        self.jump = None        # target label of the Jump object of a converted rung
        self.emit_label = None  # the label written as LABEL(...) before the rung (set by to_rungiec.check_labels)

    def skip(self, reason, detail):
        self.status = "skipped"
        self.reason, self.detail = reason, detail
        self.tree = None

    def canonical(self):
        return model.canonical(self.tree, self.unpowered, self.comments, self.label)


class Point(object):
    """A connection point of an object. side: 'in' or 'out'."""
    __slots__ = ("obj", "side", "pin", "group", "refs", "out_id", "order", "negated", "edge")

    def __init__(self, obj, side, pin, group, refs=None, out_id=None, order=None, negated=False, edge=None):
        self.obj, self.side, self.pin, self.group = obj, side, pin, group
        self.refs, self.out_id, self.order, self.negated, self.edge = refs or [], out_id, order, negated, edge


class Obj(object):
    __slots__ = ("index", "kind", "attrs", "points", "text")

    def __init__(self, index, kind, attrs):
        self.index, self.kind, self.attrs = index, kind, attrs
        self.points = []
        self.text = None


def _order(cp):
    for o in cp.iter("{%s}ConnectionPointInOrder" % SMC):
        return int(o.get("order"))
    for o in cp.iter("{%s}ConnectionPointOutOrder" % SMC):
        return int(o.get("order"))
    return None


def _refs(cp):
    return [c.get("refConnectionPointOutId") for c in cp
            if local(c.tag) in ("Connection", "FeedbackConnection")]


def parse_objects(rung, issues):
    objs = []
    comments = []
    for index, ch in enumerate(rung):
        group = local(ch.tag)
        xtype = ch.get(XSI_TYPE) or ""
        if group in ("RelPosition", "Size", "Documentation", "AddData"):
            continue
        if group == "CommonObject":
            if xtype == "Comment":
                content = ch.find("{%s}Content" % NS)
                comments.append("".join(content.itertext()) if content is not None else "")
            else:
                issues.append(("unknown-object:%s" % xtype, "%s object" % xtype))
            continue
        attrs = {k: v for k, v in ch.attrib.items() if k != XSI_TYPE}
        obj = Obj(index, xtype, attrs)
        objs.append(obj)
        if xtype == "Block":
            for grp in ch:
                gname = local(grp.tag)
                if gname not in ("InputVariables", "InOutVariables", "OutputVariables"):
                    continue
                kind = {"InputVariables": "in", "InOutVariables": "inout", "OutputVariables": "out"}[gname]
                for var in grp:
                    pin = var.get("parameterName")
                    negated = var.get("negated") == "true"
                    edge = var.get("edge")
                    for cp in var:
                        cpl = local(cp.tag)
                        if cpl == "ConnectionPointIn":
                            obj.points.append(Point(obj, "in", pin, kind, refs=_refs(cp), order=_order(cp),
                                                    negated=negated, edge=edge))
                        elif cpl == "ConnectionPointOut":
                            obj.points.append(Point(obj, "out", pin, kind, out_id=cp.get("connectionPointOutId"),
                                                    order=_order(cp), negated=negated))
                    if kind == "out" and not any(p.pin == pin and p.side == "out" for p in obj.points
                                                 if p.obj is obj):
                        obj.points.append(Point(obj, "out", pin, kind, negated=negated))
        else:
            for cp in ch:
                cpl = local(cp.tag)
                if cpl == "ConnectionPointIn":
                    obj.points.append(Point(obj, "in", None, "power", refs=_refs(cp)))
                elif cpl == "ConnectionPointOut":
                    obj.points.append(Point(obj, "out", None, "power", out_id=cp.get("connectionPointOutId")))
                elif cpl == "ST" and cp.tag == "{%s}ST" % SMC:
                    obj.text = _st_body_text(cp)
    return objs, comments


def _st_body_text(cp):
    """Text of an smcext:ST element: the body in its ST child, or its own text."""
    inner = cp.find("{%s}ST" % NS)
    return (inner.text if inner is not None else cp.text) or ""


def inline_st_text(elem):
    """ST body text of an smcext:InlineST element (not its Documentation or other children)."""
    for cp in elem:
        if local(cp.tag) == "ST" and cp.tag == "{%s}ST" % SMC:
            return _st_body_text(cp)
    return ""


class Ctx(object):
    """Per-rung net analysis."""

    def __init__(self, objs, issues):
        self.objs = objs
        self.issues = issues
        self.uf = model.UnionFind()
        self.producer = {}
        for obj in objs:
            for p in obj.points:
                if p.side == "out" and p.out_id is not None:
                    key = "o" + p.out_id
                    if key in self.producer:
                        raise ValueError("duplicate connectionPointOutId %s" % p.out_id)
                    self.producer[key] = p
                    self.uf.find(key)
        self.uf.find("S")
        self.uf.find("T")
        for obj in objs:
            for p in obj.points:
                if p.side == "in":
                    for r in p.refs:
                        if "o" + r not in self.producer:
                            issues.append(("general-net", "connection to unknown point %s" % r))
                    keys = ["o" + r for r in p.refs if "o" + r in self.producer]
                    for k in keys[1:]:
                        self.uf.union(keys[0], k)
                    if obj.kind == "RightPowerRail":
                        for k in keys:
                            self.uf.union("T", k)
                if p.side == "out" and obj.kind == "LeftPowerRail" and p.out_id is not None:
                    self.uf.union("S", "o" + p.out_id)
        self.consumers = {}
        for obj in objs:
            for p in obj.points:
                if p.side == "in" and p.refs:
                    net = self.net_in(p)
                    if net is not None:
                        self.consumers.setdefault(net, []).append(p)

    def net_in(self, p):
        for r in p.refs:
            if "o" + r in self.producer:
                return self.uf.find("o" + r)
        return None

    def net_out(self, p):
        if p.out_id is None:
            return None
        return self.uf.find("o" + p.out_id)

    def producers_of(self, net):
        return [p for k, p in self.producer.items() if self.uf.find(k) == net]


def _is_function(obj):
    return obj.kind == "Block" and obj.attrs.get("instanceName") is None


def _pins(obj, side):
    return [p for p in obj.points if p.side == side]


def classify(ctx):
    """Return (power nets, data nets). Adds block-wire issues for nets that mix power and data or that carry data
    from one block to another."""
    power, data = set(), set()
    for obj in ctx.objs:
        for p in obj.points:
            net = ctx.net_in(p) if p.side == "in" else ctx.net_out(p)
            if net is None:
                continue
            k = obj.kind
            if k in ("LeftPowerRail", "RightPowerRail", "Contact", "Coil", "CompareContact", "smcext:InlineST",
                     "Jump", "Return"):
                power.add(net)
            elif k == "DataSource" or k == "DataSink":
                data.add(net)
            elif k == "Block":
                if p.pin in ("EN", "ENO") and p.group in ("in", "out"):
                    power.add(net)
                elif (obj.attrs.get("typeName") or "").upper() == "MC" and p.pin == "In":
                    power.add(net)
    power.add(ctx.uf.find("S"))
    power.add(ctx.uf.find("T"))
    # The '' output of a function with EN and without ENO is its power output (comparison, TestABit ...).
    for obj in ctx.objs:
        if not _is_function(obj):
            continue
        pins_in = {p.pin for p in _pins(obj, "in")}
        pins_out = {p.pin for p in _pins(obj, "out")}
        if "EN" in pins_in and "ENO" not in pins_out:
            for p in _pins(obj, "out"):
                if p.pin == "":
                    net = ctx.net_out(p)
                    if net is not None and net not in power and net not in data:
                        power.add(net)
    for net in power & data:
        ctx.issues.append(("block-wire", "power and data on one net"))
        break
    for obj in ctx.objs:
        if obj.kind != "Block":
            continue
        for p in _pins(obj, "out"):
            net = ctx.net_out(p)
            if net is None or net in power:
                continue
            for c in ctx.consumers.get(net, []):
                if c.obj.kind == "Block":
                    ctx.issues.append(("block-wire", "%s.%s feeds %s.%s" % (
                        obj.attrs.get("typeName"), p.pin, c.obj.attrs.get("typeName"), c.pin)))
                    break
    return power, data


def _source_value(ctx, p, issues, what):
    """The DataSource identifier connected to an input point, or None if unconnected."""
    net = ctx.net_in(p)
    if net is None:
        return None
    prods = ctx.producers_of(net)
    if len(prods) != 1 or prods[0].obj.kind != "DataSource":
        issues.append(("block-wire", "%s is not fed by a variable or literal" % what))
        return None
    if not (prods[0].obj.attrs.get("identifier") or "").strip():
        return None  # an empty variable box: the input is not assigned
    norm, iss = st_text.check_operand(prods[0].obj.attrs.get("identifier"), what)
    issues.extend(iss)
    return norm


def _sink_values(ctx, p, issues, what):
    """The non-empty DataSink identifiers fed by an output point."""
    net = ctx.net_out(p)
    if net is None:
        return []
    vals = []
    for c in ctx.consumers.get(net, []):
        if c.obj.kind == "DataSink":
            ident = c.obj.attrs.get("identifier") or ""
            if ident.strip():
                norm, iss = st_text.check_operand(ident, what)
                issues.extend(iss)
                vals.append(norm)
        else:
            issues.append(("block-wire", "%s feeds a %s" % (what, c.obj.kind)))
    return vals


def block_args(ctx, obj, power_pins, issues):
    """Data arguments of a block: (args, return-value sinks)."""
    tn = obj.attrs.get("typeName")
    ins, outs = [], []
    ret = []
    inout_out = {}
    for p in obj.points:
        if p.group == "inout" and p.side == "out":
            inout_out[p.pin] = p
    for idx, p in enumerate(obj.points):
        if (p.pin, p.side) in power_pins:
            continue
        if p.negated or (p.edge not in (None, "none")):
            issues.append(("pin-modifier", "negated or edge pin %s.%s" % (tn, p.pin)))
        what = "argument %s of %s" % (p.pin, tn)
        if p.side == "in":
            pin_norm, iss = st_text.check_name(p.pin, "pin name")
            issues.extend(iss)
            val = _source_value(ctx, p, issues, what)
            if val is None:
                continue
            if p.group == "inout" and p.pin in inout_out:
                sinks = _sink_values(ctx, inout_out[p.pin], issues, what)
                for s in sinks:
                    if st_text.canon(s) != st_text.canon(val):
                        issues.append(("inout-mismatch", "in-out %s.%s: %s in, %s out" % (tn, p.pin, val, s)))
            ins.append(Arg(pin_norm, ":=", val, p.order if p.order is not None else 1000 + idx))
        else:
            if p.group == "inout":
                continue
            sinks = _sink_values(ctx, p, issues, what)
            if len(sinks) > 1:
                issues.append(("block-wire", "output %s.%s feeds several variables" % (tn, p.pin)))
                continue
            if not sinks:
                continue
            if p.pin == "":
                ret.append(sinks[0])
                continue
            pin_norm, iss = st_text.check_name(p.pin, "pin name")
            issues.extend(iss)
            outs.append(Arg(pin_norm, "=>", sinks[0], p.order if p.order is not None else 1000 + idx))
    ins.sort(key=lambda a: a.order)
    outs.sort(key=lambda a: a.order)
    return ins + outs, ret


def build_rung(objs, comments, res, issues):
    """Fill res.tree / res.unpowered from the objects, or add issues."""
    kinds = [o.kind for o in objs]
    if all(k in ("LeftPowerRail", "RightPowerRail") for k in kinds):
        issues.append(("empty", "no elements"))
        return
    jumps = [o for o in objs if o.kind == "Jump"]
    if len(jumps) > 1:
        issues.append(("jump-position", "%d jumps in one rung" % len(jumps)))
    for o in objs:
        if o.kind == "Jump":
            continue  # becomes an element below
        if o.kind == "Block" and (o.attrs.get("typeName") or "").upper() in LOOP_BLOCKS:
            issues.append(("loop", "%s block" % o.attrs.get("typeName")))
        elif o.kind == "Block" and (o.attrs.get("typeName") or "").upper() in JUMP_BLOCKS:
            # RETURN / JMP blocks are never converted (has_jump makes the body skip whole).
            issues.append(("unknown-object:Block", "%s block" % o.attrs.get("typeName")))
        elif o.kind not in ("LeftPowerRail", "RightPowerRail", "Contact", "Coil", "CompareContact",
                            "smcext:InlineST", "DataSource", "DataSink", "Block"):
            issues.append(("unknown-object:%s" % o.kind, "%s object" % o.kind))
    if issues and reason_key(pick(issues)[0]) <= PRECEDENCE.index("unknown-object"):
        return  # these reasons win over anything the net analysis could find
    ctx = Ctx(objs, issues)
    power, _ = classify(ctx)
    S, T = ctx.uf.find("S"), ctx.uf.find("T")

    def node(net):
        if net is None:
            return None
        if net == T:
            return "T"
        if net == S:
            return "S"
        if net not in ctx.consumers:
            return "T"
        return net

    edges = []
    unpowered = []

    def add_edge(elem, p_in, p_out):
        u = node(ctx.net_in(p_in)) if p_in is not None else None
        v = node(ctx.net_out(p_out)) if p_out is not None else "T"
        if u is None:
            issues.append(("general-net", "unconnected power input of %s" % elem.kind))
            return
        if v is None:
            v = "T"
        edges.append((u, v, model.leaf(elem)))

    for o in objs:
        k = o.kind
        p_in = next((p for p in o.points if p.side == "in"), None)
        p_out = next((p for p in o.points if p.side == "out"), None)
        if k == "Contact":
            name, iss = st_text.check_operand(o.attrs.get("operand"), "contact operand")
            issues.extend(iss)
            neg = o.attrs.get("negated") == "true"
            edge = o.attrs.get("edge") or "none"
            sub = {(False, "none"): "NO", (True, "none"): "NC", (False, "rising"): "P", (False, "falling"): "N",
                   (True, "rising"): "NP", (True, "falling"): "NN"}.get((neg, edge))
            if sub is None:
                issues.append(("unknown-object:Contact", "contact edge %s" % edge))
                continue
            add_edge(Element("contact", o.index, sub=sub, name=name), p_in, p_out)
        elif k == "CompareContact":
            op = STD_CMP.get(o.attrs.get("compareOperator"))
            a, iss1 = st_text.check_operand(o.attrs.get("operand1"), "comparison operand")
            b, iss2 = st_text.check_operand(o.attrs.get("operand2"), "comparison operand")
            issues.extend(iss1 + iss2)
            if op is None:
                issues.append(("unknown-object:CompareContact", "operator %s" % o.attrs.get("compareOperator")))
                continue
            add_edge(Element("cmp", o.index, sub=op, ops=[a, b]), p_in, p_out)
        elif k == "Coil":
            name, iss = st_text.check_operand(o.attrs.get("operand"), "coil operand")
            issues.extend(iss)
            mods = []
            if o.attrs.get("negated") == "true":
                mods.append("NOT")
            latch = o.attrs.get("latch") or "none"
            if latch == "set":
                mods.append("SET")
            elif latch == "reset":
                mods.append("RESET")
            edge = o.attrs.get("edge") or "none"
            if edge == "rising":
                mods.append("RISE")
            elif edge == "falling":
                mods.append("FALL")
            if len(mods) > 1:
                issues.append(("coil-modifier", "coil with %s" % "+".join(mods)))
                continue
            add_edge(Element("coil", o.index, sub=mods[0] if mods else "COIL", name=name), p_in, p_out)
        elif k == "smcext:InlineST":
            text, st_comments, iss = st_text.check_st(o.text)
            issues.extend(iss)
            comments.extend(st_comments)
            if p_in is None or ctx.net_in(p_in) is None:
                issues.append(("st-unpowered", "inline ST without power input"))
                continue
            add_edge(Element("st_exec", o.index, text=text), p_in, p_out)
        elif k == "Jump":
            target = o.attrs.get("targetNetworkLabel") or ""
            add_edge(Element("jump", o.index, name=target), p_in, None)
            res.jump = target
        elif k == "Block":
            block(ctx, o, power, issues, add_edge, unpowered, res)
    if pick(issues):
        return
    res.unpowered = unpowered
    if not edges and unpowered:
        res.tree = None
        return
    if S == T:
        issues.append(("general-net", "left rail wired to the right rail"))
        return
    try:
        res.tree = model.sp_reduce(edges, "S", "T")
    except model.GeneralNet as exc:
        issues.append(("general-net", str(exc)))


def block(ctx, o, power, issues, add_edge, unpowered, res):
    tn = o.attrs.get("typeName") or ""
    inst = o.attrs.get("instanceName")
    pins_in = [p for p in o.points if p.side == "in"]
    pins_out = [p for p in o.points if p.side == "out"]
    p_in = [p for p in pins_in if ctx.net_in(p) in power]
    p_out = [p for p in pins_out if p.group != "inout" and ctx.net_out(p) in power]
    if len(p_in) > 1 or len(p_out) > 1:
        issues.append(("multi-power-pin", "%s with power on %s" % (tn, ", ".join(
            [p.pin for p in p_in] + ["=>" + p.pin for p in p_out]))))
        return
    pin_in = p_in[0] if p_in else None
    pin_out = p_out[0] if p_out else None
    for p in (pin_in, pin_out):
        if p is not None and (p.negated or p.edge not in (None, "none")):
            issues.append(("pin-modifier", "negated or edge power pin %s.%s" % (tn, p.pin)))
    power_pins = set()
    if pin_in is not None:
        power_pins.add((pin_in.pin, "in"))
    if pin_out is not None:
        power_pins.add((pin_out.pin, "out"))
    has_eno = any(p.pin == "ENO" for p in pins_out)
    if has_eno:
        power_pins.add(("ENO", "out"))
    if inst is None:
        if tn.upper() in ("MC", "MCR"):
            tn = tn.upper()
            want = "In" if tn == "MC" else "EN"
            if pin_in is None or pin_in.pin != want:
                issues.append(("mc-structure", "%s without power on %s" % (tn, want)))
                return
            args, _ = block_args(ctx, o, power_pins, issues)
            mcno = next((a.value for a in args if a.pin == "MCNo"), None)
            add_edge(Element(tn.lower(), o.index, mcno=mcno), pin_in, pin_out)
            return
        fn, iss = st_text.check_name(tn, "function name")
        if tn not in CMP_TYPES:
            issues.extend(iss)
        if pin_in is None:
            issues.append(("unpowered-function", "function %s without power" % tn))
            return
        if pin_in.pin != "EN":
            issues.append(("unpowered-function", "function %s with power on %s, not EN" % (tn, pin_in.pin)))
            return
        args, ret = block_args(ctx, o, power_pins, issues)
        if tn in CMP_TYPES:
            data_in = [a for a in args if a.direction == ":="]
            if len(data_in) != 2 or len(args) != 2:
                issues.append(("compare-arity", "comparison %s with %d inputs" % (tn, len(data_in))))
                return
            if pin_out is not None and pin_out.pin != "":
                issues.append(("unknown-object:Block", "comparison %s with power on %s" % (tn, pin_out.pin)))
                return
            add_edge(Element("cmp", o.index, sub=CMP_TYPES[tn], ops=[data_in[0].value, data_in[1].value]),
                     pin_in, pin_out)
            return
        if pin_out is not None and pin_out.pin == "ENO" or (pin_out is None and has_eno):
            if len(ret) > 1:
                issues.append(("block-wire", "return value of %s feeds several variables" % tn))
                return
            add_edge(Element("fn_eno", o.index, name=fn, args=args, dst=ret[0] if ret else None), pin_in, pin_out)
            return
        if pin_out is not None and pin_out.pin == "":
            if any(a.direction == "=>" for a in args):
                issues.append(("unknown-object:Block", "function %s powers the rung and has output arguments" % tn))
                return
            add_edge(Element("callcontact", o.index, name=fn, args=args), pin_in, pin_out)
            return
        if pin_out is None and not has_eno:
            if len(ret) > 1:
                issues.append(("block-wire", "return value of %s feeds several variables" % tn))
                return
            add_edge(Element("fn_eno", o.index, name=fn, args=args, dst=ret[0] if ret else None), pin_in, None)
            return
        issues.append(("unknown-object:Block", "function %s with power on output %s" % (tn, pin_out.pin)))
        return
    # Function block instance.
    name, iss = st_text.check_name(inst, "instance name", path=True)
    issues.extend(iss)
    fbtype, iss = st_text.check_name(tn, "function block type")
    issues.extend(iss)
    args, ret = block_args(ctx, o, power_pins, issues)
    if ret:
        issues.append(("unknown-object:Block", "function block %s with a return value" % tn))
        return
    res.instances[name] = fbtype
    if pin_in is None:
        if pin_out is not None:
            issues.append(("general-net", "function block %s powers the rung without a power input" % tn))
            return
        unpowered.append(Element("fb_scan", o.index, name=name, fbtype=fbtype, args=args))
        return
    if pin_in.pin == "EN":
        if pin_out is not None and pin_out.pin != "ENO":
            issues.append(("unknown-object:Block", "function block %s with EN and power on %s" % (tn, pin_out.pin)))
            return
        add_edge(Element("fb_eno", o.index, name=name, fbtype=fbtype, args=args), pin_in, pin_out)
        return
    pin_norm, iss = st_text.check_name(pin_in.pin, "pin name")
    issues.extend(iss)
    out_norm = None
    if pin_out is not None:
        out_norm, iss = st_text.check_name(pin_out.pin, "pin name")
        issues.extend(iss)
    add_edge(Element("fb_in", o.index, name=name, fbtype=fbtype, pin=pin_norm, args=args, power_out=out_norm),
             pin_in, pin_out)


def parse_rung(el, body, ordinal):
    res = RungResult(body, ordinal, el.get("evaluationOrder"))
    issues = []
    try:
        # Judged from the XML children, so that no early return of build_rung can hide them.
        for ch in el:
            xtype = ch.get(XSI_TYPE) or ""
            if xtype in ("Jump", "Return"):
                res.has_jump = True
            elif xtype == "Block" and (ch.get("typeName") or "").upper() in JUMP_BLOCKS:
                res.has_jump = True
            elif xtype == "smcext:InlineST":
                # A return in the code of the ST text (not in a comment or a string) ends the POU, so the later
                # rungs do not always run.
                if st_text.has_return(inline_st_text(ch)):
                    res.has_jump = True
            elif xtype == "Block" and (ch.get("typeName") or "").upper() in LOOP_BLOCKS:
                res.has_loop = True
            elif xtype == "Block" and (ch.get("typeName") or "").upper() == "MC":
                res.has_mc = True
            elif xtype == "Block" and (ch.get("typeName") or "").upper() == "MCR":
                res.has_mcr = True
        objs, comments = parse_objects(el, issues)
        res.comments = comments
        res.label = el.get("label") or None
        build_rung(objs, comments, res, issues)
    except Exception as exc:  # one rung must not stop the conversion
        res.status = "error"
        res.reason = "error"
        res.detail = "%s: %s" % (type(exc).__name__, exc)
        res.tree = None
        return res
    first = pick(issues)
    if first is not None:
        if first[0] == "empty":
            res.status = "empty"
            res.reason, res.detail = "empty", first[1]
            res.tree = None
        else:
            res.skip(first[0], first[1])
    else:
        res.edge_count = sum(1 for e in model.leaves(res.tree) if e.is_edge())
    return res


def iter_rungs(path):
    """Yield ('body', BodyInfo) at the start of every body and ('rung', BodyInfo, RungResult) for every Rung of
    an LD body. Raises ET.ParseError on a malformed file."""
    pou_stack = []
    body = None
    body_count = 0
    depth_rung = 0
    rung_ordinal = 0
    for event, el in ET.iterparse(path, events=("start", "end")):
        tag = local(el.tag)
        if event == "start":
            if tag in POU_TAGS and el.tag.startswith("{%s}" % NS):
                pou_stack.append((el.get("name"), tag))
            elif tag == "BodyContent":
                body_count += 1
                pou, kind = pou_stack[-1] if pou_stack else ("", "")
                section = el.get("name") or ("main" if el.get(XSI_TYPE) == "LD" else "")
                body = BodyInfo(body_count, pou, kind, section, el.get(XSI_TYPE) or "")
                rung_ordinal = 0
                yield ("body", body)
            elif tag == "Rung":
                depth_rung += 1
            continue
        if tag == "Rung":
            depth_rung -= 1
            if body is not None and body.is_ld:
                rung_ordinal += 1
                body.rungs += 1
                yield ("rung", body, parse_rung(el, body, rung_ordinal))
            el.clear()
        elif tag in POU_TAGS and el.tag.startswith("{%s}" % NS):
            pou_stack.pop()
            el.clear()
        elif tag == "BodyContent":
            body = None
            el.clear()
        elif depth_rung == 0:
            el.clear()
