"""Unit tests of the LD XML <-> RUNGIEC converter on small hand-made rungs.

Run from the repository root: python -m unittest discover -s tools/ld_xml -p "test_*.py" -v
The xsd test needs lxml and the environment variable RUNGIEC_IEC10_XSD (path of IEC61131_10_Ed1_0.xsd).
"""

import sys

sys.dont_write_bytecode = True

import contextlib  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402
import unittest  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import rungiec2xml  # noqa: E402
import xml2rungiec  # noqa: E402
from ld_xml import ascii_art, compare, model, omron_reader, rungiec_reader, st_text, to_rungiec  # noqa: E402
from ld_xml.model import Arg, Element  # noqa: E402

HEAD = ('<?xml version="1.0"?>\n<Project xmlns:smcext="https://www.ia.omron.com/Smc" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" schemaVersion="1" '
        'xmlns="www.iec.ch/public/TC65SC65BWG7TF10">\n'
        '<FileHeader companyName="test" productName="test" productVersion="1"/>\n'
        '<ContentHeader name="test" creationDateTime="2026-01-01T00:00:00"/>\n<Types><GlobalNamespace>\n')
TAIL = '</GlobalNamespace></Types><Instances/></Project>\n'
ORDER = ('<AddData><Data name="x" handleUnknown="discard"><smcext:ConnectionPoint%sOrder order="%d"/></Data>'
         '</AddData>')


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


class RB(object):
    """Builder of one Omron-style rung."""

    def __init__(self):
        self.n = 0
        self.objs = []
        self.comments = []

    def id(self):
        self.n += 1
        return self.n

    @staticmethod
    def cin(refs, tag="ConnectionPointIn", order=None):
        refs = refs if isinstance(refs, (list, tuple)) else [refs]
        o = ORDER % ("In", order) if order else ""
        return "<%s>%s%s</%s>" % (tag, o, "".join('<Connection refConnectionPointOutId="%d"/>' % r for r in refs), tag)

    def left(self, n=1):
        ids = [self.id() for _ in range(n)]
        self.objs.append('<LdObject xsi:type="LeftPowerRail">%s</LdObject>' % "".join(
            '<ConnectionPointOut connectionPointOutId="%d"/>' % i for i in ids))
        return ids if n > 1 else ids[0]

    def right(self, *refs):
        self.objs.append('<LdObject xsi:type="RightPowerRail">%s</LdObject>' % "".join(self.cin(r) for r in refs))

    def contact(self, op, ins, neg=False, edge=None):
        oid = self.id()
        a = (' negated="true"' if neg else "") + (' edge="%s"' % edge if edge else "")
        self.objs.append('<LdObject xsi:type="Contact"%s operand="%s">%s<ConnectionPointOut connectionPointOutId="%d"/>'
                         '</LdObject>' % (a, esc(op), self.cin(ins), oid))
        return oid

    def coil(self, op, ins, neg=False, edge=None, latch=None):
        oid = self.id()
        a = (' negated="true"' if neg else "") + (' edge="%s"' % edge if edge else "") + \
            (' latch="%s"' % latch if latch else "")
        self.objs.append('<LdObject xsi:type="Coil"%s operand="%s">%s<ConnectionPointOut connectionPointOutId="%d"/>'
                         '</LdObject>' % (a, esc(op), self.cin(ins), oid))
        return oid

    def source(self, ident):
        oid = self.id()
        self.objs.append('<FbdObject xsi:type="DataSource" identifier="%s"><ConnectionPointOut '
                         'connectionPointOutId="%d"/></FbdObject>' % (esc(ident), oid))
        return oid

    def sink(self, ident, ref):
        self.objs.append('<FbdObject xsi:type="DataSink" identifier="%s">%s</FbdObject>' % (esc(ident), self.cin(ref)))

    def block(self, type_name, inst=None, inputs=(), inouts=(), outputs=()):
        """inputs: [(pin, ref or [refs] or a literal str)], inouts: [(pin, literal in, sink out)],
        outputs: [(pin, sink identifier or None)] -> {pin: out id}."""
        ids = {}
        ins_xml = []
        for k, (pin, ref) in enumerate(inputs, 1):
            if isinstance(ref, str):
                ref = self.source(ref)
            ins_xml.append('<InputVariable parameterName="%s">%s</InputVariable>' % (pin, self.cin(ref, order=k)))
        io_xml = []
        sinks = []
        for k, (pin, lit, out) in enumerate(inouts, len(inputs) + 1):
            src = self.source(lit)
            oid = self.id()
            io_xml.append('<InOutVariable parameterName="%s">%s<ConnectionPointOut connectionPointOutId="%d">%s'
                          '</ConnectionPointOut></InOutVariable>' % (pin, self.cin(src, order=k), oid,
                                                                     ORDER % ("Out", k)))
            sinks.append((out, oid))
        out_xml = []
        for k, (pin, sink) in enumerate(outputs, 1):
            oid = self.id()
            ids[pin] = oid
            out_xml.append('<OutputVariable parameterName="%s"><ConnectionPointOut connectionPointOutId="%d">%s'
                           '</ConnectionPointOut></OutputVariable>' % (pin, oid, ORDER % ("Out", k)))
            if sink is not None:
                sinks.append((sink, oid))
        self.objs.append('<FbdObject xsi:type="Block" typeName="%s"%s>%s%s%s</FbdObject>' % (
            esc(type_name), ' instanceName="%s"' % inst if inst else "",
            "<InputVariables>%s</InputVariables>" % "".join(ins_xml) if ins_xml else "",
            "<InOutVariables>%s</InOutVariables>" % "".join(io_xml) if io_xml else "",
            "<OutputVariables>%s</OutputVariables>" % "".join(out_xml) if out_xml else ""))
        for ident, ref in sinks:
            self.sink(ident, ref)
        return ids

    def inline_st(self, text, ins, doc=None):
        oid = self.id()
        self.objs.append('<LdObject xsi:type="smcext:InlineST">%s<smcext:ConnectionPointOut connectionPointOutId="%d"/>'
                         '%s<smcext:ST><ST>%s</ST></smcext:ST></LdObject>' % (
                             self.cin(ins, "smcext:ConnectionPointIn"), oid,
                             "<Documentation>%s</Documentation>" % esc(doc) if doc else "", esc(text)))
        return oid

    def jump(self, label, ins):
        self.objs.append('<FbdObject xsi:type="Jump" targetNetworkLabel="%s">%s</FbdObject>' % (label, self.cin(ins)))

    def comment(self, text):
        self.comments.append('<CommonObject xsi:type="Comment"><Content xsi:type="SimpleText">%s</Content>'
                             '</CommonObject>' % esc(text))

    def xml(self, order, label=None):
        return '<Rung evaluationOrder="%d"%s>%s%s</Rung>\n' % (
            order, ' label="%s"' % label if label else "", "".join(self.comments), "".join(self.objs))


def document(rungs, pou="P1", section="SEC_1"):
    body = "".join(r.xml(i) if isinstance(r, RB) else r(i) for i, r in enumerate(rungs, 1))
    return HEAD + ('<Program name="%s"><MainBody><BodyContent xsi:type="smcext:LdSection" name="%s" '
                   'evaluationOrder="1">\n%s</BodyContent></MainBody></Program>\n' % (pou, section, body)) + TAIL


def simple(op_in, op_out):
    r = RB()
    a = r.contact(op_in, r.left())
    r.right(r.coil(op_out, a))
    return r


def reasons(report):
    """The skip reasons of the items of a forward report, in order."""
    return [it["reason"] for it in report["items"]]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ldxml_test_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_forward(self, rungs, canon=False):
        path = os.path.join(self.tmp, "in.xml")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(document(rungs))
        out = os.path.join(self.tmp, "out")
        code, report, canonical = xml2rungiec.convert(path, out, keep_canonical=canon)
        frags = sorted(os.listdir(os.path.join(out, "rungiec"))) if os.path.isdir(os.path.join(out, "rungiec")) else []
        text = ""
        if frags:
            with open(os.path.join(out, "rungiec", frags[0]), encoding="utf-8") as f:
                text = f.read()
        self.out = out
        return code, report, text, canonical

    def statements(self, text):
        return [ln for ln in text.split("\n") if ln and not ln.startswith("(*")]

    def skipped_markers(self, text):
        """[(ordinal, reason)] of the '(* @skipped N reason *)' lines."""
        return [(int(m.group(1)), m.group(2)) for m in re.finditer(r"^\(\* @skipped (\d+) (\S+) \*\)$", text,
                                                                     re.MULTILINE)]

    def assert_body_skipped(self, report, text):
        """No rung converted, no fragment (no text, no file, no fragment name in the items, no converted index)."""
        self.assertEqual(report["counts"]["converted"], 0, report["items"])
        self.assertEqual(text, "")
        self.assertTrue(all(it["fragment"] is None for it in report["items"]), report["items"])
        self.assertEqual(report["converted_index"], [])
        self.assertEqual(os.listdir(os.path.join(self.out, "rungiec")), [])

    def convert_one(self, rung):
        code, report, text, _ = self.run_forward([rung])
        self.assertEqual(code, 0, report)
        self.assertEqual(report["counts"]["converted"], 1, report["items"])
        return self.statements(text)

    def round_trip(self, rungs):
        """Forward, reverse, read back: return (forward canonical, mapping, regenerated canonical, report)."""
        code, report, text, canon = self.run_forward(rungs, canon=True)
        self.assertEqual(code, 0)
        xml_path = os.path.join(self.tmp, "std.xml")
        rcode, rev = rungiec2xml.convert(os.path.join(self.out, "rungiec"), xml_path, "2026-01-01T00:00:00Z")
        regen = compare.regenerated_canonicals(xml_path)
        return canon, rev, regen, compare.compare(canon, regen, rev["mapping"]), text


class Forward(Base):
    def test_01_series(self):
        r = RB()
        a = r.contact("A", r.left())
        b = r.contact("B", a, neg=True)
        r.right(r.coil("Y", b))
        self.assertEqual(self.convert_one(r), ["RUNG(CONTACT(A), NOT_CONTACT(B), COIL(Y))"])

    def test_02_or_and_coil(self):
        r = RB()
        l1, l2 = r.left(2)
        a = r.contact("A", l1)
        b = r.contact("B", l2)
        c = r.contact("C", [a, b])
        r.right(r.coil("Y", c))
        self.assertEqual(self.convert_one(r), ["RUNG(PARALLEL(CONTACT(A), CONTACT(B)), CONTACT(C), COIL(Y))"])

    def test_03_several_outputs(self):
        r = RB()
        a = r.contact("A", r.left())
        b = r.contact("B", a)
        y1 = r.coil("Y1", b)
        c = r.contact("C", a)
        y2 = r.coil("Y2", c)
        r.right(y1, y2)
        self.assertEqual(self.convert_one(r), [
            "RUNG(CONTACT(A), PARALLEL(SERIES(CONTACT(B), COIL(Y1)), SERIES(CONTACT(C), COIL(Y2))))"])

    def test_04_ln12_wrap(self):
        r = RB()
        a = r.contact("A", r.left())
        y1 = r.coil("Y1", a)
        b = r.contact("B", a)
        y2 = r.coil("Y2", b)
        r.right(y1, y2)
        self.assertEqual(self.convert_one(r), [
            "RUNG(CONTACT(A), PARALLEL(SERIES(COIL(Y1)), SERIES(CONTACT(B), COIL(Y2))))"])

    def test_05_six_contacts(self):
        r = RB()
        p = r.contact("A", r.left())
        p = r.contact("B", p, neg=True)
        p = r.contact("C", p, edge="rising")
        p = r.contact("D", p, edge="falling")
        p = r.contact("E", p, neg=True, edge="rising")
        p = r.contact("F", p, neg=True, edge="falling")
        r.right(r.coil("Y", p))
        self.assertEqual(self.convert_one(r), [
            "RUNG(CONTACT(A), NOT_CONTACT(B), RISE_CONTACT(C), FALL_CONTACT(D), NOT_RISE_CONTACT(E), "
            "NOT_FALL_CONTACT(F), COIL(Y))"])

    def test_06_six_coils(self):
        r = RB()
        a = r.contact("A", r.left())
        outs = [r.coil("Y1", a), r.coil("Y2", a, neg=True), r.coil("Y3", a, latch="set"),
                r.coil("Y4", a, latch="reset"), r.coil("Y5", a, edge="rising"), r.coil("Y6", a, edge="falling")]
        r.right(*outs)
        self.assertEqual(self.convert_one(r), [
            "RUNG(CONTACT(A), PARALLEL(COIL(Y1), NOT_COIL(Y2), SET_COIL(Y3), RESET_COIL(Y4), RISE_COIL(Y5), "
            "FALL_COIL(Y6)))"])

    def test_06b_coil_modifier(self):
        r = RB()
        a = r.contact("A", r.left())
        r.right(r.coil("Y", a, neg=True, latch="set"))
        code, report, _, _ = self.run_forward([r])
        self.assertEqual(report["counts"]["skipped"], {"coil-modifier": 1})

    def test_07_comparison_and_call_contact(self):
        r = RB()
        a = r.contact("A", r.left())
        eq = r.block("=", inputs=[("EN", a), ("In1", "Count"), ("In2", "UINT#10")], outputs=[("", None)])
        t = r.block("TestABit", inputs=[("EN", eq[""]), ("In", "W"), ("Pos", "USINT#3")], outputs=[("", None)])
        r.right(r.coil("Y", t[""]))
        self.assertEqual(self.convert_one(r), [
            "RUNG(CONTACT(A), EQ_CONTACT(Count, uint#10), CONTACT(TestABit(In := W, Pos := usint#3)), COIL(Y))"])

    def test_07b_compare_arity(self):
        r = RB()
        a = r.contact("A", r.left())
        eq = r.block("<=", inputs=[("EN", a), ("In1", "X"), ("In2", "Y"), ("In3", "Z")], outputs=[("", None)])
        r.right(r.coil("Q", eq[""]))
        _, report, _, _ = self.run_forward([r])
        self.assertEqual(report["counts"]["skipped"], {"compare-arity": 1})

    def test_08_function_and_fb_eno(self):
        r = RB()
        a = r.contact("A", r.left())
        f1 = r.block("Calc", inputs=[("EN", a), ("In1", "X"), ("In2", "1")], outputs=[("ENO", None), ("", "R")])
        f2 = r.block("MOVE", inputs=[("EN", f1["ENO"]), ("In", "X")], outputs=[("ENO", None), ("Out", "Z")])
        f3 = r.block("Clear", inputs=[("EN", f2["ENO"])], inouts=[("InOut", "Buf", "Buf")],
                     outputs=[("ENO", None), ("", "")])
        fb = r.block("MyFb", inst="Fb1", inputs=[("EN", f3["ENO"]), ("Go", "TRUE")], outputs=[("ENO", None)])
        r.right(r.coil("Y", fb["ENO"]))
        self.assertEqual(self.convert_one(r), [
            "RUNG(CONTACT(A), FUN_ENO_RET(R, Calc, In1 := X, In2 := 1), FUN_ENO(MOVE, In := X, Out => Z), "
            "FUN_ENO(Clear, InOut := Buf), FBT_ENO(Fb1, MyFb, Go := true), COIL(Y))"])

    def test_09_fb_box_power_out_one_element(self):
        r = RB()
        a = r.contact("A", r.left())
        tm = r.block("TON", inst="T1", inputs=[("In", a), ("PT", "T#1s")], outputs=[("Q", None), ("ET", "")])
        r.right(r.coil("Y", tm["Q"]))
        canon, rev, regen, result, text = self.round_trip([r])
        self.assertEqual(self.statements(text), ["RUNG(CONTACT(A), FBT_BOX(T1, TON, In, Q, PT := T#1s), COIL(Y))"])
        self.assertIn("(*  |--| A |--[ T1:TON(PT := T#1s) In Q ]--( Y )--|  *)", text)
        self.assertIn("(* @instance T1: TON *)", text)
        self.assertEqual(rev["converted"], 1)
        self.assertEqual(result["mismatches"], 0)
        self.assertEqual(result["compared"], 1)
        self.assertIn('"Q"', list(canon.values())[0])

    def test_09b_fb_box_power_out_nested(self):
        r = RB()
        l1, l2 = r.left(2)
        a = r.contact("A", l1)
        b = r.contact("B", l2)
        tm = r.block("TON", inst="T1", inputs=[("In", a), ("PT", "T#1s")], outputs=[("Q", None)])
        c = r.contact("C", [tm["Q"], b])
        r.right(r.coil("Y", c))
        canon, rev, regen, result, text = self.round_trip([r])
        self.assertEqual(self.statements(text), [
            "RUNG(PARALLEL(SERIES(CONTACT(A), FBT_BOX(T1, TON, In, Q, PT := T#1s)), CONTACT(B)), CONTACT(C), COIL(Y))"])
        self.assertEqual(result["mismatches"], 0)
        self.assertEqual(result["compared"], 1)

    def test_09c_fb_box_at_the_right_rail(self):
        # The output pin wired to the right rail keeps the box: FBT_BOX, not FBT_IN.
        r = RB()
        a = r.contact("A", r.left())
        tm = r.block("TON", inst="T1", inputs=[("IN", a), ("PT", "T#1s")], outputs=[("Q", None)])
        r.right(tm["Q"])
        canon, rev, regen, result, text = self.round_trip([r])
        self.assertEqual(self.statements(text), ["RUNG(CONTACT(A), FBT_BOX(T1, TON, IN, Q, PT := T#1s))"])
        self.assertEqual(result["mismatches"], 0)

    def test_09d_fb_box_in_an_mc_condition(self):
        r = RB()
        a = r.contact("A", r.left())
        tm = r.block("TON", inst="T1", inputs=[("IN", a), ("PT", "T#1s")], outputs=[("Q", None)])
        mc = r.block("MC", inputs=[("In", tm["Q"]), ("MCNo", "0")], outputs=[("ENO", None)])
        r.right(mc["ENO"])
        m2 = RB()
        mcr = m2.block("MCR", inputs=[("EN", m2.left()), ("MCNo", "0")], outputs=[("ENO", None)])
        m2.right(mcr["ENO"])
        code, report, text, _ = self.run_forward([r, m2])
        self.assertEqual(report["counts"]["converted"], 2, report["items"])
        self.assertIn("BEGIN_MC(CONTACT(A), FBT_BOX(T1, TON, IN, Q, PT := T#1s))", self.statements(text))

    def test_09e_fbt_boxes(self):
        # Each function block box of a Block with a typeName comes back as the FBT_* form.
        r = RB()
        a = r.contact("A", r.left())
        r.block("R_TRIG", inst="Tr", inputs=[("CLK", "X")], outputs=[("Q", "Z")])
        r.block("TON", inst="T1", inputs=[("IN", a), ("PT", "T#1s")])
        fb = r.block("MyFb", inst="Fb0", inputs=[("EN", a), ("Go", "TRUE")], outputs=[("ENO", None)])
        r.right(r.coil("Y", fb["ENO"]))
        canon, rev, regen, result, text = self.round_trip([r])
        stmts = self.statements(text)
        self.assertEqual(len(stmts), 1, stmts)
        for call in ("FBT_SCAN(Tr, R_TRIG, CLK := X, Q => Z)", "FBT_IN(T1, TON, IN, PT := T#1s)",
                     "FBT_ENO(Fb0, MyFb, Go := true)"):
            self.assertIn(call, stmts[0])
        self.assertNotRegex(stmts[0], r"\bFB_(SCAN|IN|ENO)\(")
        self.assertEqual(result["mismatches"], 0)

    def test_09f_fb_box_without_type_from_the_instance_marker(self):
        # FB_BOX takes the type from the @instance marker: the Block gets typeName TON and its output Q powers the
        # coil, the same rung as FBT_BOX.
        r = RB()
        a = r.contact("A", r.left())
        tm = r.block("TON", inst="T1", inputs=[("IN", a), ("PT", "T#1s")], outputs=[("Q", None)])
        r.right(r.coil("Y", tm["Q"]))
        code, report, text, canon = self.run_forward([r], canon=True)
        frag_dir = os.path.join(self.out, "rungiec")
        frag = os.path.join(frag_dir, sorted(os.listdir(frag_dir))[0])
        untyped = text.replace("FBT_BOX(T1, TON, IN, Q, ", "FB_BOX(T1, IN, Q, ").replace("T1:TON(", "T1(")
        self.assertIn("RUNG(CONTACT(A), FB_BOX(T1, IN, Q, PT := T#1s), COIL(Y))", self.statements(untyped))
        with open(frag, "w", encoding="utf-8", newline="\n") as f:
            f.write(untyped)
        xml_path = os.path.join(self.tmp, "std.xml")
        rcode, rev = rungiec2xml.convert(frag_dir, xml_path, "2026-01-01T00:00:00Z")
        self.assertEqual(rcode, 0, rev)
        with open(xml_path, encoding="utf-8") as f:
            xml = f.read()
        self.assertIn('typeName="TON" instanceName="T1"', xml)
        out_id = re.search(r'<OutputVariable parameterName="Q"><ConnectionPointOut connectionPointOutId="(\d+)"',
                           xml).group(1)
        self.assertRegex(xml, r'xsi:type="Coil" operand="Y"><ConnectionPointIn><Connection '
                              r'refConnectionPointOutId="%s"/>' % out_id)
        result = compare.compare(canon, compare.regenerated_canonicals(xml_path), rev["mapping"])
        self.assertEqual(result["mismatches"], 0)
        self.assertEqual(result["compared"], 1)

    def test_10_fb_scan(self):
        r = RB()
        a = r.contact("A", r.left())
        r.block("FB_X", inst="F1", inputs=[("IN_01", "X")], outputs=[("OUT_01", "Z")])
        r.right(r.coil("Y", a))
        self.assertEqual(self.convert_one(r), ["RUNG(FBT_SCAN(F1, FB_X, IN_01 := X, OUT_01 => Z), CONTACT(A), COIL(Y))"])

    def test_11_inline_st(self):
        r = RB()
        a = r.contact("A", r.left())
        s = r.inline_st("// note\nX := X + 1;\nIF X > 10 THEN X := 0; (* wrap *) END_IF;", a)
        r.right(s)
        code, report, text, _ = self.run_forward([r])
        self.assertEqual(self.statements(text), ["RUNG(CONTACT(A), ST_EXEC(X := X + 1; if X > 10 then X := 0; end_if;))"])
        self.assertIn("(* note *)", text)
        self.assertIn("(* wrap *)", text)

    def test_11b_inline_st_rejected(self):
        bad = []
        for st in ("RETURN;", "RungiecEn := TRUE;", "COIL(Y);", "S := '$N';", "{#if X} Y := 1;"):
            r = RB()
            r.right(r.inline_st(st, r.contact("A", r.left())))
            bad.append(r)
        _, report, _, _ = self.run_forward(bad)
        self.assertEqual(report["counts"]["skipped"], {"st-text": 5})

    def test_12_mc_mcr(self):
        mc = RB()
        a = mc.contact("A", mc.left())
        no = mc.block("MC", inputs=[("In", a), ("MCNo", "0")], outputs=[("ENO", None)])
        mc.right(no["ENO"])
        mcr = RB()
        x = mcr.block("MCR", inputs=[("EN", mcr.left()), ("MCNo", "0")], outputs=[("ENO", None)])
        mcr.right(x["ENO"])
        code, report, text, _ = self.run_forward([mc, simple("B", "Y"), mcr])
        self.assertEqual(self.statements(text), ["BEGIN_MC(CONTACT(A))", "RUNG(CONTACT(B), COIL(Y))", "END_MC()"])

    def test_12b_mc_structure(self):
        mc = RB()
        a = mc.contact("A", mc.left())
        mc.right(mc.block("MC", inputs=[("In", a), ("MCNo", "0")], outputs=[("ENO", None)])["ENO"])
        mcr = RB()
        mcr.right(mcr.block("MCR", inputs=[("EN", mcr.left()), ("MCNo", "1")], outputs=[("ENO", None)])["ENO"])
        # A broken MC structure skips the whole body: the MC / MCR rungs as mc-structure, every other rung as mc-body,
        # and the body gets no fragment.
        # MCNo 0 is closed by MCNo 1.
        code, report, text, _ = self.run_forward([simple("Z", "W"), mc, simple("B", "Y"), mcr])
        self.assertEqual([it["reason"] for it in report["items"]], ["mc-body", "mc-structure", "mc-body", "mc-structure"])
        self.assert_body_skipped(report, text)
        # An MCR without MC.
        code, report, text, _ = self.run_forward([simple("Z", "W"), mcr, simple("B", "Y"), simple("C", "X")])
        self.assertEqual([it["reason"] for it in report["items"]], ["mc-body", "mc-structure", "mc-body", "mc-body"])
        self.assert_body_skipped(report, text)
        # An MC that is never closed.
        code, report, text, _ = self.run_forward([simple("Z", "W"), mc, simple("B", "Y")])
        self.assertEqual([it["reason"] for it in report["items"]], ["mc-body", "mc-structure", "mc-body"])
        self.assert_body_skipped(report, text)

    def test_12c_mc_rung_cut_short(self):
        # An MC rung that is not converted for another reason (here a FOR block, which returns early in build_rung)
        # still breaks the MC structure of the body: the other rungs are skipped as mc-body (the MC check runs
        # before the loop check).
        mc = RB()
        a = mc.contact("A", mc.left())
        mc.right(mc.block("MC", inputs=[("In", a), ("MCNo", "0")], outputs=[("ENO", None)])["ENO"])
        f = mc.block("FOR", inputs=[("EN", mc.left()), ("InitVal", "0"), ("EndVal", "9"), ("StepVal", "1")],
                     outputs=[("ENO", None), ("Index", "I")])
        mc.right(f["ENO"])
        code, report, text, _ = self.run_forward([simple("Z", "W"), mc, simple("B", "Y"), mcr_rung("0")])
        self.assertEqual([it["reason"] for it in report["items"]], ["mc-body", "loop", "mc-body", "mc-structure"])
        self.assert_body_skipped(report, text)

    def test_13_skip_reasons(self):
        label = simple("B", "Y")
        bad = simple("A;B", "Y")  # an operand that is not one name
        wire = RB()
        a = wire.contact("A", wire.left())
        add = wire.block("ADD", inputs=[("EN", a), ("In1", "X"), ("In2", "1")], outputs=[("ENO", None), ("", None)])
        mv = wire.block("MOVE", inputs=[("EN", add["ENO"]), ("In", add[""])], outputs=[("ENO", None), ("Out", "Z")])
        wire.right(mv["ENO"])
        bridge = RB()
        l1, l2 = bridge.left(2)
        a = bridge.contact("A", l1)
        b = bridge.contact("B", l2)
        e = bridge.contact("E", a)
        c = bridge.contact("C", a)
        d = bridge.contact("D", [b, e])
        bridge.right(bridge.coil("Y", [c, d]))
        rungs = [lambda i: label.xml(i, label="L1"), bad, wire, bridge]
        _, report, text, _ = self.run_forward(rungs)
        self.assertEqual(reasons(report), ["operand", "block-wire", "general-net"])
        self.assertEqual(self.statements(text), ["LABEL(L1)", "RUNG(CONTACT(B), COIL(Y))"])
        self.assertEqual(self.skipped_markers(text), [(2, "operand"), (3, "block-wire"), (4, "general-net")])

    def test_13b_skipped_marker(self):
        bad = simple("A;B", "Y")  # an operand that is not one name
        loop = wire_rung()
        empty = RB()
        empty.right(empty.left())
        code, report, text, _ = self.run_forward([simple("C", "Z"), bad, loop, empty, simple("D", "W")])
        self.assertEqual(code, 0, report)
        self.assertEqual(reasons(report), ["operand", "block-wire", "empty"])
        lines = text.split("\n")
        # The marker stands at the position of the rung, between the markers of its neighbours; no diagram.
        pos = lines.index("(* @skipped 2 operand *)")
        self.assertEqual(lines[pos + 1:pos + 4], ["(* @skipped 3 block-wire *)", "(* @skipped 4 empty *)", "(* @rung 5 *)"])
        self.assertEqual(lines[pos - 1], "RUNG(CONTACT(C), COIL(Z))")
        self.assertEqual(len(self.skipped_markers(text)), 3)
        self.assertNotIn("A;B", text)
        # The reader ignores the markers: no comment, no unit, no XML, the comparison is unaffected.
        frag = rungiec_reader.read_fragment(text, "f")
        self.assertEqual([u.ordinal for u in frag.units], [1, 5])
        self.assertEqual(frag.skipped, [(2, "operand"), (3, "block-wire"), (4, "empty")])
        self.assertTrue(all(not u.comments for u in frag.units))
        self.assertEqual(frag.loose, [])
        canon, rev, regen, result, _ = self.round_trip([simple("C", "Z"), bad, loop, empty, simple("D", "W")])
        self.assertEqual((rev["units"], rev["converted"], result["compared"], result["mismatches"]), (2, 2, 2, 0))

    def test_13c_skipped_marker_with_label(self):
        # '@skipped' comes first, then '@label' with the label; the reason code has no ':<detail>' part.
        loop = wire_rung()
        _, report, text, _ = self.run_forward([jump_rung("L1", "A"), labelled(loop, "L1"), simple("B", "Y")])
        self.assertIn("(* @skipped 2 block-wire *)\n(* @label 2 *)\n(*  L1:  *)\nLABEL(L1)\n", text)
        r = omron_reader.RungResult(None, 1, 1)
        r.skip("unknown-object:Foo(* x", "d")
        self.assertEqual(to_rungiec.skipped_reason(r), "unknown-object")
        r.skip("weird (* *) {x", "d")
        code = to_rungiec.skipped_reason(r)
        self.assertTrue("(*" not in code and "*)" not in code and " " not in code, code)

    def test_14_arity_split(self):
        r = RB()
        p = r.left()
        for k in range(16):
            p = r.contact("C%d" % k, p)
        r.right(r.coil("Y", p))
        stmt = self.convert_one(r)[0]
        self.assertEqual(stmt, "RUNG(%s, SERIES(CONTACT(C15), COIL(Y)))" % ", ".join(
            "CONTACT(C%d)" % k for k in range(15)))

    def test_15_counts_add_up(self):
        empty = RB()
        empty.right(empty.left())
        jump = RB()
        jump.jump("L1", jump.contact("A", jump.left()))
        rungs = [simple("A", "Y"), empty, jump, simple("B", "Z")]
        code, report, _, _ = self.run_forward(rungs)
        c = report["counts"]
        self.assertEqual(code, 0)
        self.assertEqual(report["total_rungs"], 4)
        self.assertEqual((c["converted"], c["empty"], c["error"], c["skipped"]), (0, 1, 0, {"jump-target-missing": 1, "jump-body": 2}))
        self.assertTrue(report["counts_add_up"])
        with open(os.path.join(self.out, "xml2rungiec.json"), encoding="utf-8") as f:
            data = json.load(f)
        self.assertIs(data["complete"], True)
        self.assertEqual(list(data)[-1], "complete")
        self.assertEqual([(i["ordinal"], i["status"]) for i in data["items"]], [(1, "skipped"), (2, "empty"), (3, "skipped"), (4, "skipped")])
        self.assertEqual(data["fragments"], 0)

    def test_16_truncated_xml(self):
        text = document([simple("A", "Y"), simple("B", "Z")])
        path = os.path.join(self.tmp, "cut.xml")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text[:len(text) * 2 // 3])
        out = os.path.join(self.tmp, "out")
        code, report, _ = xml2rungiec.convert(path, out)
        self.assertEqual(code, 2)
        with open(os.path.join(out, "xml2rungiec.json"), encoding="utf-8") as f:
            self.assertIs(json.load(f)["complete"], False)
        self.assertEqual(os.listdir(os.path.join(out, "rungiec")), [])

    @unittest.skipUnless(os.environ.get("RUNGIEC_IEC10_XSD"), "RUNGIEC_IEC10_XSD is not set")
    def test_17_xsd(self):
        try:
            import lxml  # noqa: F401
        except ImportError:
            self.skipTest("lxml is not installed")
        r = RB()
        a = r.contact("A", r.left())
        tm = r.block("TON", inst="T1", inputs=[("In", a), ("PT", "T#1s")], outputs=[("Q", None)])
        eq = r.block("=", inputs=[("EN", tm["Q"]), ("In1", "X"), ("In2", "1")], outputs=[("", None)])
        f = r.block("Calc", inputs=[("EN", eq[""]), ("In1", "X")], outputs=[("ENO", None), ("", "R")])
        r.right(r.coil("Y", f["ENO"]))
        empty = RB()
        empty.right(empty.left())
        canon, rev, regen, result, _ = self.round_trip([r, simple("B", "Z"), jump_rung("L1", "A"),
                                                        labelled(simple("C", "W"), "L0"), labelled(empty, "L1")])
        xml_path = os.path.join(self.tmp, "std.xml")
        xsd = os.environ["RUNGIEC_IEC10_XSD"]
        self.assertEqual(compare.validate_xsd(xml_path, xsd)[0], 0)
        self.assertEqual(compare.namespace_checks(xml_path), {"foreign_elements": 0, "add_data": 0, "smcext": 0})
        self.assertEqual(result["mismatches"], 0)
        # The validation is live: a Contact without operand is an error.
        with open(xml_path, encoding="utf-8") as f:
            broken = f.read().replace('operand="B"', "", 1)
        bad_path = os.path.join(self.tmp, "bad.xml")
        with open(bad_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(broken)
        self.assertGreater(compare.validate_xsd(bad_path, xsd)[0], 0)

    def test_18_comment_round_trip(self):
        r = simple("A", "Y")
        r.comment("start (* motor *) {x}")
        canon, rev, regen, result, text = self.round_trip([r])
        self.assertIn("(* start ( * motor * ) { x} *)", text)
        self.assertEqual(result["mismatches"], 0)
        self.assertEqual(result["compared"], 1)
        self.assertIn("start(*motor*){x}", list(canon.values())[0])

    def test_19_mismatch_is_detected(self):
        canon, rev, regen, result, _ = self.round_trip([simple("A", "Y")])
        key = list(canon)[0]
        canon[key] = canon[key].replace('"A"', '"B"')
        self.assertEqual(compare.compare(canon, regen, rev["mapping"])["mismatches"], 1)


def jump_rung(label, op=None, edge=None):
    """A rung that jumps to label: through a contact op, or unconditionally (op None)."""
    r = RB()
    p = r.left()
    if op is not None:
        p = r.contact(op, p, edge=edge)
    r.jump(label, p)
    return r


def labelled(rung, label):
    return lambda i: rung.xml(i, label=label)


def mc_rung(no="0"):
    r = RB()
    r.right(r.block("MC", inputs=[("In", r.contact("M", r.left())), ("MCNo", no)], outputs=[("ENO", None)])["ENO"])
    return r


def mcr_rung(no="0"):
    r = RB()
    r.right(r.block("MCR", inputs=[("EN", r.left()), ("MCNo", no)], outputs=[("ENO", None)])["ENO"])
    return r


def mc_rung_named(name, close=False):
    r = RB()
    if close:
        r.right(r.block(name, inputs=[("EN", r.left()), ("MCNo", "0")], outputs=[("ENO", None)])["ENO"])
    else:
        r.right(r.block(name, inputs=[("In", r.contact("M", r.left())), ("MCNo", "0")], outputs=[("ENO", None)])["ENO"])
    return r


def empty_rung():
    r = RB()
    r.right(r.left())
    return r


def for_rung():
    r = RB()
    f = r.block("FOR", inputs=[("EN", r.left()), ("InitVal", "0"), ("EndVal", "9"), ("StepVal", "1")],
                outputs=[("ENO", None), ("Index", "I")])
    r.right(f["ENO"])
    return r


def wire_rung():
    """A rung that is skipped as block-wire (a function block output wired to a variable of two sinks)."""
    r = RB()
    a = r.contact("A", r.left())
    add = r.block("ADD", inputs=[("EN", a), ("In1", "X"), ("In2", "1")], outputs=[("ENO", None), ("", None)])
    mv = r.block("MOVE", inputs=[("EN", add["ENO"]), ("In", add[""])], outputs=[("ENO", None), ("Out", "Z")])
    r.right(mv["ENO"])
    return r


def two_body_document(rungs_a, rungs_b):
    """A document with two LD bodies (programs P1 / SEC_1 and P2 / SEC_2)."""
    def body(rungs, pou, section):
        text = "".join(r.xml(i) if isinstance(r, RB) else r(i) for i, r in enumerate(rungs, 1))
        return ('<Program name="%s"><MainBody><BodyContent xsi:type="smcext:LdSection" name="%s" '
                'evaluationOrder="1">\n%s</BodyContent></MainBody></Program>\n' % (pou, section, text))
    return HEAD + body(rungs_a, "P1", "SEC_1") + body(rungs_b, "P2", "SEC_2") + TAIL


class Jumps(Base):
    def read_file(self, path):
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_20_forward_jump(self):
        rungs = [jump_rung("L1", "A"), simple("B", "Y"), labelled(simple("C", "Z"), "L1"), jump_rung("L1")]
        code, report, text, _ = self.run_forward(rungs)
        self.assertEqual(code, 0, report["items"])
        self.assertEqual(report["counts"]["converted"], 4)
        self.assertEqual(self.statements(text), ["RUNG(CONTACT(A), JMP(L1))", "RUNG(CONTACT(B), COIL(Y))",
                                                 "LABEL(L1)", "RUNG(CONTACT(C), COIL(Z))", "RUNG(JMP(L1))"])
        canon, rev, regen, result, _ = self.round_trip(rungs)
        self.assertEqual((result["compared"], result["mismatches"]), (4, 0))
        xml = self.read_file(os.path.join(self.tmp, "std.xml"))
        self.assertEqual(xml.count('<FbdObject xsi:type="Jump" targetNetworkLabel="L1">'), 2)
        self.assertEqual(xml.count('<Rung label="L1" '), 1)
        # The jump rungs do not reach the right rail.
        first = xml.split("<Rung ")[1]
        self.assertIn('<LdObject xsi:type="RightPowerRail"></LdObject>', first)
        # 16 contacts and a jump: the split keeps JMP the last child of the RUNG.
        long = RB()
        p = long.left()
        for k in range(16):
            p = long.contact("C%d" % k, p)
        long.jump("L1", p)
        rungs = [long, labelled(simple("B", "Y"), "L1")]
        _, report, text, _ = self.run_forward(rungs)
        self.assertEqual(self.statements(text)[0], "RUNG(%s, SERIES(CONTACT(C14), CONTACT(C15)), JMP(L1))" % ", ".join(
            "CONTACT(C%d)" % k for k in range(14)))
        canon, rev, regen, result, _ = self.round_trip(rungs)
        self.assertEqual((result["compared"], result["mismatches"]), (2, 0))

    def test_21_backward_jump(self):
        rungs = [labelled(simple("A", "Y"), "Again"), simple("B", "Z"), jump_rung("Again", "C")]
        code, report, text, _ = self.run_forward(rungs)
        self.assertEqual(self.statements(text), ["LABEL(Again)", "RUNG(CONTACT(A), COIL(Y))",
                                                 "RUNG(CONTACT(B), COIL(Z))", "RUNG(CONTACT(C), JMP(Again))"])
        canon, rev, regen, result, _ = self.round_trip(rungs)
        self.assertEqual((result["compared"], result["mismatches"]), (3, 0))
        # A backward jump over an edge contact (lint LN23) is skipped; the Jump is then not converted, so the whole
        # body is skipped (jump-body) and gets no fragment.
        rungs = [simple("P", "Q"), labelled(simple("A", "Y"), "Again"), jump_rung("Again", "C", edge="rising")]
        _, report, text, _ = self.run_forward(rungs)
        self.assertEqual(report["counts"]["skipped"], {"jump-loop-edge": 1, "jump-body": 2})
        self.assert_body_skipped(report, text)
        # A forward jump over the same edge contact is converted.
        rise = RB()
        rise.right(rise.coil("W", rise.contact("D", rise.left(), edge="rising")))
        rungs = [jump_rung("Fwd", "C"), labelled(rise, "Fwd"), simple("A", "Y")]
        _, report, _, _ = self.run_forward(rungs)
        self.assertEqual(report["counts"]["converted"], 3, report["items"])

    def test_22_label_on_empty_and_skipped_rung(self):
        empty = RB()
        empty.right(empty.left())
        loop = wire_rung()
        rungs = [jump_rung("L1", "A"), jump_rung("L2", "B"), labelled(empty, "L1"), labelled(loop, "L2"),
                 simple("C", "Y")]
        code, report, text, _ = self.run_forward(rungs)
        self.assertEqual(code, 0)
        c = report["counts"]
        self.assertEqual((c["converted"], c["empty"], c["skipped"]), (3, 1, {"block-wire": 1}))
        self.assertIn("(* @skipped 3 empty *)\n(* @label 3 *)\n(*  L1:  *)\nLABEL(L1)\n"
                      "(* @skipped 4 block-wire *)\n(* @label 4 *)\n(*  L2:  *)\nLABEL(L2)\n", text)
        canon, rev, regen, result, _ = self.round_trip(rungs)
        self.assertEqual((result["compared"], result["mismatches"]), (3, 0))
        self.assertEqual((rev["units"], rev["converted"], rev["label_only_rungs"]), (3, 3, 2))
        xml = self.read_file(os.path.join(self.tmp, "std.xml"))
        self.assertIn('<Rung label="L1" evaluationOrder="3"><LdObject xsi:type="LeftPowerRail">', xml)
        self.assertIn('<Rung label="L2" evaluationOrder="4">', xml)

    def test_23_jump_skip_reasons(self):
        cases = [
            ([jump_rung("L9", "A"), labelled(simple("B", "Y"), "L1")], "jump-target-missing"),
            ([jump_rung("l1", "A"), labelled(simple("B", "Y"), "L1")], "label-name"),
            ([jump_rung("Rungiec1", "A"), labelled(simple("B", "Y"), "Rungiec1")], "label-name"),
            ([jump_rung("L1", "A"), mc_rung(), labelled(simple("B", "Y"), "L1"), mcr_rung()], "jump-across-mc"),
            ([labelled(simple("B", "Y"), "L1"), mc_rung(), jump_rung("L1", "A"), mcr_rung()], "jump-across-mc"),
        ]
        for rungs, reason in cases:
            _, report, _, _ = self.run_forward(rungs)
            jump_items = [it for it in report["items"] if ("jump to" in (it["detail"] or "") or
                          it["reason"].startswith("jump")) and it["reason"] != "jump-body"]
            self.assertEqual([it["reason"] for it in jump_items], [reason], report["items"])
        # Jump inside MC with the label in the same region: converted.
        rungs = [mc_rung(), jump_rung("L1", "A"), simple("C", "Z"), labelled(simple("B", "Y"), "L1"), mcr_rung()]
        _, report, text, _ = self.run_forward(rungs)
        self.assertEqual(report["counts"]["converted"], 5, report["items"])
        # Two rungs with one label (letter case ignored): both labelled rungs and the jump are skipped.
        rungs = [jump_rung("L1", "A"), labelled(simple("B", "Y"), "L1"), labelled(simple("C", "Z"), "l1")]
        _, report, text, _ = self.run_forward(rungs)
        self.assertEqual(report["counts"]["skipped"], {"label-duplicate": 3})
        # A jump in a branch, two jumps in a rung.
        r = RB()
        a = r.contact("A", r.left())
        r.right(r.coil("Y", a))
        r.jump("L1", a)
        two = RB()
        p = two.left()
        two.jump("L1", two.contact("A", p))
        two.jump("L1", two.contact("B", p))
        _, report, _, _ = self.run_forward([r, two, labelled(simple("B", "Y"), "L1")])
        self.assertEqual(report["counts"]["skipped"], {"jump-position": 2, "jump-body": 1})

    def test_23b_dropped_jump_skips_the_body(self):
        # The jump to L9 is dropped, so the whole body is skipped (jump-body), also the rungs that would convert; the
        # other reasons stay.
        rungs = [simple("A", "Y"), jump_rung("L9", "B"), labelled(simple("C", "Z"), "L1"), empty_rung(), wire_rung()]
        _, report, text, _ = self.run_forward(rungs)
        self.assertEqual(reasons(report), ["jump-body", "jump-target-missing", "jump-body", "empty", "block-wire"])
        self.assert_body_skipped(report, text)

    def test_23c_jump_rung_cut_short_skips_the_body(self):
        # A Jump / Return rung that build_rung gives up on early (the early return must not hide the jump) still
        # makes the body skipped.
        jump_wire = wire_rung()
        a = jump_wire.contact("A", jump_wire.left())
        jump_wire.jump("L1", a)
        common = jump_rung("L1", "A")
        ret = jump_rung("L1", "A")
        cases = [
            (jump_wire.xml, "block-wire"),
            (lambda i: common.xml(i).replace("</Rung>", '<CommonObject xsi:type="Foo"/></Rung>'),
             "unknown-object:Foo"),
            (lambda i: ret.xml(i).replace('xsi:type="Jump" targetNetworkLabel="L1"', 'xsi:type="Return"'),
             "unknown-object:Return"),
        ]
        for rung, reason in cases:
            _, report, text, _ = self.run_forward([simple("B", "Y"), rung, labelled(simple("C", "Z"), "L1"),
                                                   simple("D", "X")])
            self.assertEqual(reasons(report), ["jump-body", reason, "jump-body", "jump-body"], reason)
            self.assert_body_skipped(report, text)

    def test_23d_label_failure_alone_keeps_the_body(self):
        # Two rungs with one label and no jump: only those two rungs are skipped, the body stays.
        rungs = [simple("A", "Y"), labelled(simple("B", "Z"), "L1"), labelled(simple("C", "X"), "l1"),
                 simple("D", "W")]
        _, report, text, _ = self.run_forward(rungs)
        self.assertEqual(reasons(report), ["label-duplicate", "label-duplicate"])
        self.assertEqual(report["counts"]["converted"], 2)
        self.assertEqual(self.statements(text), ["RUNG(CONTACT(A), COIL(Y))", "RUNG(CONTACT(D), COIL(W))"])
        self.assertEqual(self.skipped_markers(text), [(2, "label-duplicate"), (3, "label-duplicate")])

    def test_23e_other_body_unaffected(self):
        # A dropped jump skips its own body only: the other body of the document converts fully and has a fragment.
        path = os.path.join(self.tmp, "two.xml")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(two_body_document([simple("A", "Y"), jump_rung("L9", "B"), simple("C", "Z")],
                                      [simple("D", "X"), simple("E", "W")]))
        out = os.path.join(self.tmp, "out2")
        code, report, _ = xml2rungiec.convert(path, out)
        self.assertEqual(code, 0, report)
        self.assertEqual([(it["body"], it["reason"], it["fragment"]) for it in report["items"]],
                         [(1, "jump-body", None), (1, "jump-target-missing", None), (1, "jump-body", None)])
        self.assertEqual(report["counts"]["converted"], 2)
        self.assertEqual(report["fragments"], 1)
        self.assertEqual({c["body"] for c in report["converted_index"]}, {2})
        names = os.listdir(os.path.join(out, "rungiec"))
        self.assertEqual(len(names), 1)
        with open(os.path.join(out, "rungiec", names[0]), encoding="utf-8") as f:
            text = f.read()
        self.assertEqual(self.statements(text), ["RUNG(CONTACT(D), COIL(X))", "RUNG(CONTACT(E), COIL(W))"])
        self.assertEqual(self.skipped_markers(text), [])

    def test_24_changed_label_or_target_is_mismatch(self):
        rungs = [jump_rung("L1", "A"), labelled(simple("B", "Y"), "L1")]
        canon, rev, regen, result, _ = self.round_trip(rungs)
        self.assertEqual((result["compared"], result["mismatches"]), (2, 0))
        xml_path = os.path.join(self.tmp, "std.xml")
        xml = self.read_file(xml_path)
        for old, new in (('label="L1"', 'label="L2"'), ('targetNetworkLabel="L1"', 'targetNetworkLabel="L2"')):
            changed = os.path.join(self.tmp, "changed.xml")
            with open(changed, "w", encoding="utf-8", newline="\n") as f:
                f.write(xml.replace(old, new))
            res = compare.compare(canon, compare.regenerated_canonicals(changed), rev["mapping"])
            self.assertEqual(res["mismatches"], 1, old)


def mc_open_rung():
    """An MC rung without a condition: the left rail feeds the MC block."""
    r = RB()
    r.right(r.block("MC", inputs=[("In", r.left()), ("MCNo", "0")], outputs=[("ENO", None)])["ENO"])
    return r


class MasterControl(Base):
    """Reverse direction: BEGIN_MC / END_MC are written as the MC / MCR blocks of Omron."""

    def read_file(self, path):
        with open(path, encoding="utf-8") as f:
            return f.read()

    def reverse_text(self, text):
        frag_dir = os.path.join(self.tmp, "frag")
        os.makedirs(frag_dir, exist_ok=True)
        with open(os.path.join(frag_dir, "f.iec"), "w", encoding="utf-8", newline="\n") as f:
            f.write("(* @body 1 P1 SEC_1 *)\n" + text)
        xml_path = os.path.join(self.tmp, "rev.xml")
        rcode, rev = rungiec2xml.convert(frag_dir, xml_path, "2026-01-01T00:00:00Z")
        return rcode, rev, xml_path

    def test_30_outer_and_nested(self):
        def mc_with(no):
            r = RB()
            r.right(r.block("MC", inputs=[("In", r.contact("M", r.left())), ("MCNo", no)],
                            outputs=[("ENO", None)])["ENO"])
            return r
        rungs = [mc_with("0"), simple("A", "Y"), mc_with("1"), simple("B", "Z"), mcr_rung("1"), mcr_rung("0")]
        canon, rev, regen, result, text = self.round_trip(rungs)
        self.assertEqual(self.statements(text), [
            "BEGIN_MC(CONTACT(M))", "RUNG(CONTACT(A), COIL(Y))", "BEGIN_MC(CONTACT(M))",
            "RUNG(CONTACT(B), COIL(Z))", "END_MC()", "END_MC()"])
        self.assertEqual(rev["skipped"], {})
        self.assertEqual((result["compared"], result["mismatches"]), (6, 0))
        xml = self.read_file(os.path.join(self.tmp, "std.xml"))
        self.assertEqual(xml.count('typeName="MC"'), 2)
        self.assertEqual(xml.count('typeName="MCR"'), 2)
        self.assertEqual(xml.count('<FbdObject xsi:type="DataSource" identifier="1">'), 2)
        self.assertNotIn("smcext", xml)
        self.assertNotIn("AddData", xml)
        # The MC block: In from the condition, MCNo constant, ENO to the right rail.
        self.assertIn('<InputVariable parameterName="In">', xml)
        self.assertIn('<InputVariable parameterName="EN">', xml)
        self.assertIn('<OutputVariable parameterName="ENO">', xml)

    def test_31_mc_without_condition(self):
        rungs = [mc_open_rung(), simple("A", "Y"), mcr_rung("0")]
        code, report, text, canon = self.run_forward(rungs, canon=True)
        self.assertEqual(self.statements(text)[0], "BEGIN_MC(CONTACT(true))")
        canon, rev, regen, result, _ = self.round_trip(rungs)
        self.assertEqual((result["compared"], result["mismatches"]), (3, 0))
        xml = self.read_file(os.path.join(self.tmp, "std.xml"))
        self.assertNotIn('operand="true"', xml)

    def test_32_unbalanced(self):
        rcode, rev, _ = self.reverse_text("(* @rung 1 *)\nEND_MC()\n")
        self.assertEqual(rcode, 1)
        self.assertEqual(rev["skipped"], {"mc-unbalanced": 1})
        self.assertEqual(rev["converted"], 0)
        # A BEGIN_MC left open at the end of the body.
        rcode, rev, _ = self.reverse_text("(* @rung 1 *)\nBEGIN_MC(CONTACT(A))\n")
        self.assertEqual(rcode, 1)
        self.assertEqual(rev["skipped"], {"mc-unbalanced": 1})
        self.assertEqual(rev["converted"], 1)

    def test_33_depth_limit(self):
        text = "".join("(* @rung %d *)\nBEGIN_MC(CONTACT(A))\n" % k for k in range(1, 17))
        text += "".join("(* @rung %d *)\nEND_MC()\n" % k for k in range(17, 33))
        rcode, rev, xml_path = self.reverse_text(text)
        self.assertEqual(rcode, 1)
        self.assertEqual(rev["skipped"], {"mc-nesting-too-deep": 2})
        xml = self.read_file(xml_path)
        self.assertIn('<FbdObject xsi:type="DataSource" identifier="14">', xml)
        self.assertNotIn('identifier="15"', xml)

    def test_34_st_exec_is_a_failure(self):
        rcode, rev, _ = self.reverse_text("(* @rung 1 *)\nRUNG(CONTACT(A), ST_EXEC(X := 1;))\n")
        self.assertEqual(rcode, 1)
        self.assertEqual(rev["skipped"], {"st-exec-no-standard-form": 1})


def _c(name, sub="NO"):
    return model.leaf(Element("contact", 0, sub=sub, name=name))


def _y(name, sub="COIL"):
    return model.leaf(Element("coil", 0, sub=sub, name=name))


def _e(kind, **kw):
    return model.leaf(Element(kind, 0, **kw))


class MacroNames(unittest.TestCase):
    """st_text.MACRO_NAMES holds the names of all three library files."""

    def test_names_of_each_library_file(self):
        # Each name is defined in one file only: the entry file, the ST body and the LD body.
        for name in ("_RUNGIEC_IS_NODE_00", "_RUNGIEC_SERIES_1", "_RUNGIEC_LD_CHECK"):
            self.assertIn(name, st_text.MACRO_NAMES)


class AsciiArt(unittest.TestCase):
    """The diagram lines of ascii_art (exact text) and the column check on them."""

    def art(self, kids, rail=True):
        lines, masks = ascii_art.rung_drawing(kids)
        self.assertEqual(ascii_art.alignment_problems(lines, masks, rail), [])
        return lines

    def test_series(self):
        self.assertEqual(self.art([_c("A"), _c("B"), _y("Y")]), ["|--| A |--| B |--( Y )--|"])

    def test_parallel_unequal_branches(self):
        kids = [("P", [_c("Start"), _c("Run")]), _c("Stop", "NC"), _c("Overload", "NC"), _y("Run")]
        self.assertEqual(self.art(kids), [
            "|--+--| Start |--+--|/Stop |--|/Overload |--( Run )--|",
            "|  |             |                                   |",
            "|  +--| Run |----+                                   |"])

    def test_nested_parallel_in_series_in_parallel(self):
        kids = [("P", [_c("A"), ("S", [_c("B"), ("P", [_c("C"), _c("D")])]), _c("E")]), _y("Y")]
        self.assertEqual(self.art(kids), [
            "|--+--| A |---------------+--( Y )--|",
            "|  |                      |         |",
            "|  +--| B |--+--| C |--+--+         |",
            "|  |         |         |  |         |",
            "|  |         +--| D |--+  |         |",
            "|  |                      |         |",
            "|  +--| E |---------------+         |"])

    def test_parallel_coils_stack_on_the_right_rail(self):
        self.assertEqual(self.art([_c("A"), ("P", [_y("Y"), _y("Z", "SET"), _y("W", "NOT")])]), [
            "|--| A |--+--( Y )---|",
            "|         |          |",
            "|         +--(S Z )--|",
            "|         |          |",
            "|         +--(/ W )--|"])
        # a coil after a branch inside a branch
        kids = [_c("A"), ("P", [("S", [_c("B"), ("P", [_y("Y"), _y("Z")])]), _y("W")])]
        self.assertEqual(self.art(kids), [
            "|--| A |--+--| B |--+--( Y )--|",
            "|         |         |         |",
            "|         |         +--( Z )--|",
            "|         |                   |",
            "|         +--( W )------------|"])

    def test_merge_coil_is_not_stacked(self):
        self.assertEqual(self.art([("P", [_c("A"), _c("B")]), _y("Y")]), [
            "|--+--| A |--+--( Y )--|",
            "|  |         |         |",
            "|  +--| B |--+         |"])

    def test_contact_and_coil_kinds(self):
        kids = [_c("A", "NC"), _c("B", "P"), _c("C", "N"), _c("D", "NP"), _c("E", "NN"),
                _y("F"), _y("G", "NOT"), _y("H", "SET"), _y("I", "RESET"), _y("J", "RISE"), _y("K", "FALL")]
        self.assertEqual(self.art(kids), [
            "|--|/A |--|P B |--|N C |--|/P D |--|/N E |--( F )--(/ G )--(S H )--(R I )--(P J )--(N K )--|"])

    def test_comparison_and_call_contact(self):
        kids = [_e("cmp", sub="GE", ops=["Count", "3"]), _e("cmp", sub="NE", ops=["X", "Y"]),
                _e("callcontact", name="Chk", args=[Arg("a", ":=", "1"), Arg("b", ":=", "Z")]),
                _e("callcontact", name="Probe"), _y("Y")]
        self.assertEqual(self.art(kids), ["|--| Count >= 3 |--| X <> Y |--| Chk(a := 1, b := Z) |--| Probe |--( Y )--|"])

    def test_boxes(self):
        a = [Arg("IN", ":=", "S"), Arg("PT", ":=", "T#2s")]
        kids = [_e("fb_scan", name="Tm", args=a), _e("fb_scan", name="Tn"),
                _e("fb_in", name="T1", pin="IN", args=[Arg("PT", ":=", "T#1s")]),
                _e("fb_in", name="T2", pin="IN"), _e("fb_eno", name="Fb0"),
                _e("fn_eno", name="EnoFn"), _e("fn_eno", name="EnoFn", dst="R1", args=[Arg("x", ":=", "1")]),
                _e("st_exec", text="Count := Count + 1;")]
        self.assertEqual(self.art(kids), [
            "|--[ Tm(IN := S, PT := T#2s) ]--[ Tn ]--[ T1(PT := T#1s) IN ]--[ T2 IN ]--[ Fb0 ENO ]--"
            "[ EnoFn ENO ]--[ R1 := EnoFn(x := 1) ENO ]--[[ ST: Count := Count + 1; ]]--|"])
        p = [Arg("PT", ":=", "T#3s")]
        kids = [_e("fb_scan", name="Tm", fbtype="TON", args=[Arg("IN", ":=", "S")]),
                _e("fb_in", name="Tm", fbtype="TON", pin="IN"), _e("fb_eno", name="Fb0", fbtype="MyFb"),
                _e("fb_in", name="Tm", pin="IN", power_out="Q", args=p),
                _e("fb_in", name="Tm", fbtype="TON", pin="IN", power_out="Q", args=p)]
        self.assertEqual(self.art(kids), [
            "|--[ Tm:TON(IN := S) ]--[ Tm:TON IN ]--[ Fb0:MyFb ENO ]--[ Tm(PT := T#3s) IN Q ]--"
            "[ Tm:TON(PT := T#3s) IN Q ]--|"])

    def test_jump_and_return_have_no_right_rail(self):
        self.assertEqual(self.art([_e("cmp", sub="GE", ops=["Count", "3"]), _e("jump", name="Skip")], False),
                         ["|--| Count >= 3 |-->>Skip"])
        self.assertEqual(self.art([_e("jump", name="Done")], False), ["|-->>Done"])
        self.assertEqual(self.art([_c("A"), _e("return")], False), ["|--| A |--<RETURN>"])
        kids = [("P", [_c("A"), _c("B")]), _e("jump", name="L1")]
        self.assertEqual(self.art(kids, False), ["|--+--| A |--+-->>L1", "|  |         |", "|  +--| B |--+"])

    def test_mc(self):
        self.assertEqual(self.art([_c("A"), _c("B"), _e("mc")]), ["|--| A |--| B |--(MC)--|"])
        self.assertEqual(self.art([_e("mcr")]), ["|--(MCR)--|"])

    def test_label(self):
        self.assertEqual(ascii_art.label_art("Skip"), ["(*  Skip:  *)"])

    def test_comment_wrapper_and_is_art(self):
        lines = ascii_art.rung_art([_c("A"), _y("Y")])
        self.assertEqual(lines, ["(*  |--| A |--( Y )--|  *)"])
        self.assertTrue(ascii_art.is_art(lines[0][2:-2]))
        self.assertFalse(ascii_art.is_art(" a comment "))

    def test_comment_safety(self):
        kids = [_c("a(*b"), _e("st_exec", text="x := 1; (* c *) {st} {end} {#if}\n y := 2;"), _y("Y")]
        for line in ascii_art.rung_art(kids):
            inner = line[2:-2]
            self.assertNotIn("(*", inner)
            self.assertNotIn("*)", inner)
            self.assertNotIn("{st}", line)
            self.assertNotIn("{end}", line)
            self.assertNotIn("{#", line)
            self.assertNotIn("\n", line)

    def test_wide_characters_keep_the_columns(self):
        kids = [("P", [_c(u"スタート"), _c("B")]), _y("Y")]
        lines, masks = ascii_art.rung_drawing(kids)
        self.assertEqual(lines, [u"|--+--| スタート |--+--( Y )--|",
                                 u"|  |" + " " * 16 + u"|" + " " * 9 + u"|",
                                 u"|  +--| B |" + "-" * 9 + u"+" + " " * 9 + u"|"])
        self.assertEqual(ascii_art.alignment_problems(lines, masks), [])
        self.assertEqual(len(set(ascii_art.width(l) for l in lines)), 1)

    def test_alignment_check_finds_a_shifted_connector(self):
        lines, masks = ascii_art.rung_drawing([("P", [_c("A"), _c("B")]), _y("Y")])
        self.assertEqual(ascii_art.alignment_problems(lines, masks), [])
        lines[1] = lines[1][:4] + " " + lines[1][4:-1]
        masks[1] = lines[1]
        self.assertTrue(ascii_art.alignment_problems(lines, masks))

    def test_connectors_inside_a_symbol_are_ignored(self):
        for kids in ([_e("st_exec", text="a := b + c | d;"), _y("Y")],
                     [_e("cmp", sub="NE", ops=["a | b", "c + d"]), _y("Y")]):
            lines, masks = ascii_art.rung_drawing(kids)
            self.assertEqual(ascii_art.alignment_problems(lines, masks), [])

    def test_contact_bars_are_not_connectors_on_parallel_branches(self):
        # the contact bars of the second row of a branch sit under the contact bars of the first: with the text
        # alone '|' above and below would look like a connector column that has no '+'
        kids = [("P", [("S", [_c("A"), _c("B")]), ("S", [_c("C", "NC"), _c("D", "P")]), _c("E")]), _y("Y")]
        lines, masks = ascii_art.rung_drawing(kids)
        self.assertEqual(ascii_art.alignment_problems(lines, masks), [])
        # a contact bar in the connector column is told from a wire connector by the mask
        self.assertIn("x", masks[0])
        self.assertEqual(masks[0].count("+"), 2)
        self.assertEqual(lines[0].count("|"), 2 * 2 + 2)  # two contacts with two bars each, the two rails
        # a connector that does not touch another one is still found next to contacts
        lines[1] = lines[1][:-5] + "|" + lines[1][-4:]
        masks[1] = masks[1][:-5] + "|" + masks[1][-4:]
        self.assertTrue(ascii_art.alignment_problems(lines, masks))


class ArtInFragment(Base):
    def test_art_before_every_statement_and_round_trip(self):
        r = RB()
        p = r.left()
        a = r.contact("A", p)
        b = r.contact("B", p)
        r.right(r.coil("Y", a))
        r.right(r.coil("Z", b))
        lab = labelled(simple("C", "W"), "L1")
        code, report, text, _ = self.run_forward([r, lab])
        self.assertEqual(code, 0, report)
        lines = text.split("\n")
        for i, ln in enumerate(lines):
            if ln.startswith(("RUNG(", "LABEL(")):
                self.assertTrue(lines[i - 1].startswith("(*  ") and lines[i - 1].endswith("  *)"), ln)
        self.assertIn("(*  L1:  *)\nLABEL(L1)\n(*  |--| C |--( W )--|  *)\nRUNG(CONTACT(C), COIL(W))", text)
        canon, rev, regen, result, _ = self.round_trip([r, lab])
        self.assertEqual((result["compared"], result["mismatches"]), (2, 0))


def break_rung():
    r = RB()
    f = r.block("BREAK", inputs=[("EN", r.left())], outputs=[("ENO", None)])
    r.right(f["ENO"])
    return r


def return_st_rung(text, doc=None):
    r = RB()
    r.right(r.inline_st(text, r.contact("A", r.left()), doc))
    return r


def flow_block_rung(type_name):
    r = RB()
    r.right(r.block(type_name, inputs=[("EN", r.left())], outputs=[("ENO", None)])["ENO"])
    return r


def program_xml(name, rungs):
    text = "".join(r.xml(i) if isinstance(r, RB) else r(i) for i, r in enumerate(rungs, 1))
    return ('<Program name="%s"><MainBody><BodyContent xsi:type="smcext:LdSection" name="S" '
            'evaluationOrder="1">\n%s</BodyContent></MainBody></Program>\n' % (name, text))


class WholeBody(Base):
    """Loop blocks and an inline ST with a return skip the whole body (checks in the order MC, loop, jump)."""

    def test_30_loop_body(self):
        rungs = [simple("A", "Y"), for_rung(), simple("B", "Z"), empty_rung(), wire_rung(), simple("C", "X")]
        _, report, text, _ = self.run_forward(rungs)
        self.assertEqual(reasons(report), ["loop-body", "loop", "loop-body", "empty", "block-wire", "loop-body"])
        self.assert_body_skipped(report, text)

    def test_30b_break_alone(self):
        _, report, text, _ = self.run_forward([simple("A", "Y"), break_rung()])
        self.assertEqual(reasons(report), ["loop-body", "loop"])
        self.assert_body_skipped(report, text)

    def test_30c_other_body_unaffected(self):
        path = os.path.join(self.tmp, "in.xml")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(two_body_document([simple("A", "Y"), for_rung(), simple("B", "Z")], [simple("C", "X")]))
        out = os.path.join(self.tmp, "out")
        code, report, _ = xml2rungiec.convert(path, out)
        self.assertEqual(code, 0, report)
        self.assertEqual(report["counts"]["converted"], 1)
        self.assertEqual(report["counts"]["skipped"], {"loop": 1, "loop-body": 2})
        self.assertEqual(len(os.listdir(os.path.join(out, "rungiec"))), 1)
        self.assertEqual([c["body"] for c in report["converted_index"]], [2])

    def test_30d_mc_check_comes_first(self):
        # A broken MC structure wins over the loop: the converted rungs get mc-body, the loop rung keeps loop.
        _, report, text, _ = self.run_forward([simple("A", "Y"), mc_rung("0"), for_rung()])
        self.assertEqual(reasons(report), ["mc-body", "mc-structure", "loop"])
        self.assert_body_skipped(report, text)

    def test_31_inline_st_return(self):
        for st in ("RETURN;", "Return;", "IF X THEN return; END_IF;"):
            _, report, text, _ = self.run_forward([simple("A", "Y"), return_st_rung(st), simple("B", "Z")])
            self.assertEqual(reasons(report), ["jump-body", "st-text", "jump-body"], st)
            self.assert_body_skipped(report, text)

    def test_31b_inline_st_without_return(self):
        # 'returned' and 'return_x' are other words, and a return in a comment leaves a converted rung: the body
        # stays (the text match only matters when a rung is not converted).
        _, report, text, _ = self.run_forward([simple("A", "Y"), return_st_rung("returned := 1; return_x := 2;"),
                                               return_st_rung("// return" + chr(10) + "X := 1;"), simple("B", "Z")])
        self.assertEqual(report["counts"]["skipped"], {})
        self.assertEqual(report["counts"]["converted"], 4)
        self.assertEqual(len(self.statements(text)), 4)

    def test_31c_return_only_in_comment_or_string(self):
        # A return in a comment or a string is not code: the rungs skipped for another reason (the Rungiec name) do not
        # skip the body, the other rungs stay.
        rungs = [simple("A", "Y"), return_st_rung("// return" + chr(10) + "X := 1; Rungiec1 := 2;"),
                 return_st_rung("S := 'return'; Rungiec1 := 2;"), return_st_rung("(* return *) Rungiec1 := 2;"),
                 simple("B", "Z")]
        _, report, text, _ = self.run_forward(rungs)
        self.assertEqual(reasons(report), ["st-text", "st-text", "st-text"])
        self.assertEqual(report["counts"]["converted"], 2)
        self.assertEqual(len(self.statements(text)), 2)

    def test_31d_block_return_and_jmp(self):
        # A RETURN or JMP block (any letter case) decides which rungs run, like a Jump object: the body is skipped.
        for type_name in ("RETURN", "JMP", "Return"):
            block = RB()
            block.right(block.block(type_name, inputs=[("EN", block.contact("A", block.left()))],
                                    outputs=[("ENO", None)])["ENO"])
            _, report, text, _ = self.run_forward([simple("A", "Y"), block, simple("B", "Z")])
            got = reasons(report)
            self.assertEqual((got[0], got[2], len(got)), ("jump-body", "jump-body", 3), type_name)
            self.assert_body_skipped(report, text)

    def test_31e_documentation_does_not_hide_the_return(self):
        # The text before the ST body ('note') must not glue onto 'return;'.
        _, report, text, _ = self.run_forward([simple("A", "Y"), return_st_rung("return;", doc="note"),
                                               simple("B", "Z")])
        self.assertEqual(reasons(report), ["jump-body", "st-text", "jump-body"])
        self.assert_body_skipped(report, text)

    def test_31f_return_only_in_documentation_is_not_code(self):
        _, report, text, _ = self.run_forward([simple("A", "Y"), return_st_rung("X := 1;", doc="see return"),
                                               simple("B", "Z")])
        self.assertEqual(report["counts"]["skipped"], {})
        self.assertEqual(len(self.statements(text)), 3)

    def test_31g_flow_block_names_in_any_letter_case(self):
        # Lower and mixed case names are handled like upper case: no rung of a changed meaning is written.
        for type_name, first in (("for", "loop"), ("Next", "loop"), ("Break", "loop"),
                                 ("jmp", "unknown-object"), ("return", "unknown-object")):
            _, report, text, _ = self.run_forward([simple("A", "Y"), flow_block_rung(type_name), simple("B", "Z")])
            got = reasons(report)
            self.assertEqual(len(got), 3, (type_name, got))
            self.assertEqual(got[1].split(":")[0], first, (type_name, got))
            self.assertEqual((got[0], got[2]), (got[0], got[2]))
            self.assertEqual(self.statements(text), [], type_name)
            self.assert_body_skipped(report, text)

    def test_31h_mc_names_in_any_letter_case(self):
        # Mc / mcr become BEGIN_MC / END_MC like MC / MCR.
        _, report, text, _ = self.run_forward([simple("A", "Y"), mc_rung_named("Mc"), simple("B", "Z"),
                                               mc_rung_named("mcr", close=True)])
        self.assertEqual(report["counts"]["skipped"], {})
        self.assertIn("BEGIN_MC(", text)
        self.assertIn("END_MC()", text)
        self.assertNotIn("Mc(", text)

    def summary(self, report):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            xml2rungiec.print_summary(report)
        return [ln for ln in buf.getvalue().split("\n") if ln.startswith("  body skipped")]

    def test_32_summary_lists_every_body_without_fragment(self):
        path = os.path.join(self.tmp, "in.xml")
        bodies = [("P1", [simple("A", "Y"), for_rung()]),                # loop-body
                  ("P2", [simple("A", "Y"), jump_rung("L9", "B")]),      # jump-body
                  ("P3", [wire_rung(), empty_rung()]),                   # no-converted-rung
                  ("P4", [simple("A", "Y"), mc_rung("0")]),              # mc-body
                  ("P5", [simple("A", "Y"), wire_rung()])]               # has a fragment: not listed
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(HEAD + "".join(program_xml(n, b) for n, b in bodies) + TAIL)
        code, report, _ = xml2rungiec.convert(path, os.path.join(self.tmp, "out"))
        self.assertEqual(code, 0, report)
        self.assertEqual(self.summary(report), ["  body skipped (loop-body): P1 S", "  body skipped (jump-body): P2 S",
                                                "  body skipped (no-converted-rung): P3 S",
                                                "  body skipped (mc-body): P4 S"])

    def test_32b_summary_reason_without_a_converted_rung(self):
        # No rung of the body is converted, so no rung carries a *-body reason: the reason comes from the others.
        path = os.path.join(self.tmp, "in.xml")
        bodies = [("P1", [for_rung(), wire_rung()]),            # loop-body
                  ("P2", [mc_rung("0")]),                       # mc-body (the MC is not closed)
                  ("P3", [jump_rung("L9", "A")]),               # jump-body (no label L9)
                  ("P4", [wire_rung(), empty_rung()])]          # no-converted-rung
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(HEAD + "".join(program_xml(n, b) for n, b in bodies) + TAIL)
        code, report, _ = xml2rungiec.convert(path, os.path.join(self.tmp, "out"))
        self.assertEqual(code, 0, report)
        self.assertEqual(self.summary(report), ["  body skipped (loop-body): P1 S", "  body skipped (mc-body): P2 S",
                                                "  body skipped (jump-body): P3 S",
                                                "  body skipped (no-converted-rung): P4 S"])

    def test_33_roundtrip_reads_markers_with_rungiec_reader(self):
        import ld_xml_roundtrip
        self.assertIn("loop-body", ld_xml_roundtrip.BODY_SKIPS)
        rungs = [simple("C", "Z"), simple("A;B", "Y"), wire_rung(), empty_rung(), simple("D", "W")]
        _, report, text, _ = self.run_forward(rungs)
        found = ld_xml_roundtrip.read_skipped_markers(os.path.join(self.out, "rungiec"))
        name = report["items"][0]["fragment"]
        expected = {(name, it["ordinal"], it["reason"].split(":", 1)[0]) for it in report["items"]}
        self.assertEqual(len(expected), 3)
        self.assertEqual(set(found), expected)
        self.assertEqual(sum(found.values()), 3)

    def flow_check(self, rungs, claim_fragment=False):
        """Run the forward conversion on rungs and the independent flow check. claim_fragment: pretend that body 1
        has a fragment (a converter that wrongly wrote one)."""
        import ld_xml_roundtrip
        _, report, _, _ = self.run_forward(rungs)
        index = [{"body": 1}] if claim_fragment else report["converted_index"]
        return ld_xml_roundtrip.flow_elements_in_fragment_bodies(os.path.join(self.tmp, "in.xml"), report["items"], index)

    def test_34c_flow_check_sees_a_return_after_documentation(self):
        rungs = [simple("A", "Y"), return_st_rung("return;", doc="note")]
        self.assertEqual(self.flow_check(rungs)["offending"], 0)
        flow = self.flow_check(rungs, claim_fragment=True)
        self.assertEqual((flow["offending"], flow["first_offending"]), (1, [[1, 2, "inline_return"]]), flow)

    def test_34d_flow_check_ignores_return_only_in_documentation(self):
        flow = self.flow_check([simple("A", "Y"), return_st_rung("X := 1;", doc="see return")], claim_fragment=True)
        self.assertEqual(flow["offending"], 0, flow)

    def test_34_flow_check_passes_on_a_good_document(self):
        rungs = [simple("A", "Y"), jump_rung("L1", "A"), labelled(simple("C", "Z"), "L1"), mc_rung("0"),
                 return_st_rung("// return" + chr(10) + "X := 1;"), mcr_rung("0")]
        flow = self.flow_check(rungs)
        self.assertEqual((flow["jump_rungs"], flow["mc_rungs"], flow["flow_block_rungs"], flow["inline_return_rungs"],
                          flow["offending"]), (1, 2, 0, 0, 0), flow)

    def test_34b_flow_check_fails_on_a_flow_element_in_a_fragment_body(self):
        # The body has no fragment (loop-body, jump-body); claiming one makes every flow element an offender,
        # and a Jump that was converted stays fine.
        cases = [([simple("A", "Y"), for_rung()], "flow_block"),
                 ([simple("A", "Y"), return_st_rung("X := 1; return;")], "inline_return"),
                 ([simple("A", "Y"), jump_rung("L9", "B")], "jump")]
        for rungs, kind in cases:
            flow = self.flow_check(rungs)
            self.assertEqual(flow["offending"], 0, (kind, flow))
            flow = self.flow_check(rungs, claim_fragment=True)
            self.assertEqual((flow["offending"], flow["first_offending"]), (1, [[1, 2, kind]]), (kind, flow))


if __name__ == "__main__":
    unittest.main()
