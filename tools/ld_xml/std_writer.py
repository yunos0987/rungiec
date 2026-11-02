"""Rung tree -> standard IEC 61131-10 LD XML (no vendor extension, no AddData, no positions).

The power flows from the left rail through the elements; a parallel branch takes the same input connections and
its outputs are merged by the next element (several Connection elements). A terminal element's power output goes
to the right rail, except FB_IN / FBT_IN, whose power is passed through (the next element reads the input net).
FB_BOX / FBT_BOX pass the power on from their output pin.
"""

from xml.sax.saxutils import escape, quoteattr

from . import model

NS = "www.iec.ch/public/TC65SC65BWG7TF10"
XSI = "http://www.w3.org/2001/XMLSchema-instance"


def attr(name, value):
    return " %s=%s" % (name, quoteattr(value))


class RungWriter(object):
    def __init__(self):
        self.next_id = 0
        self.objs = []

    def new_id(self):
        self.next_id += 1
        return self.next_id

    def cp_in(self, ins, indent):
        if not ins:
            return "%s<ConnectionPointIn/>" % indent
        conns = "".join('<Connection refConnectionPointOutId="%d"/>' % i for i in ins)
        return "%s<ConnectionPointIn>%s</ConnectionPointIn>" % (indent, conns)

    def data_source(self, value):
        oid = self.new_id()
        self.objs.append('<FbdObject xsi:type="DataSource"%s><ConnectionPointOut connectionPointOutId="%d"/>'
                         '</FbdObject>' % (attr("identifier", value), oid))
        return oid

    def data_sink(self, value, ref):
        self.objs.append('<FbdObject xsi:type="DataSink"%s><ConnectionPointIn><Connection '
                         'refConnectionPointOutId="%d"/></ConnectionPointIn></FbdObject>' % (attr("identifier", value), ref))

    def block(self, type_name, instance, power_in, args, power_outs, ret_dst=None):
        """power_in: (pin, [ins]) or None. power_outs: [pin] -> returns {pin: id}."""
        inputs = []
        outputs = []
        sinks = []
        if power_in is not None:
            pin, ins = power_in
            conns = "".join('<Connection refConnectionPointOutId="%d"/>' % i for i in ins)
            inputs.append('<InputVariable%s><ConnectionPointIn>%s</ConnectionPointIn></InputVariable>'
                          % (attr("parameterName", pin), conns))
        for a in args:
            if a.direction == ":=":
                sid = self.data_source(a.value)
                inputs.append('<InputVariable%s><ConnectionPointIn><Connection refConnectionPointOutId="%d"/>'
                              '</ConnectionPointIn></InputVariable>' % (attr("parameterName", a.pin), sid))
        ids = {}
        for pin in power_outs:
            ids[pin] = self.new_id()
            outputs.append('<OutputVariable%s><ConnectionPointOut connectionPointOutId="%d"/></OutputVariable>'
                           % (attr("parameterName", pin), ids[pin]))
        if ret_dst:
            rid = self.new_id()
            outputs.append('<OutputVariable parameterName=""><ConnectionPointOut connectionPointOutId="%d"/>'
                           '</OutputVariable>' % rid)
            sinks.append((ret_dst, rid))
        for a in args:
            if a.direction == "=>":
                oid = self.new_id()
                outputs.append('<OutputVariable%s><ConnectionPointOut connectionPointOutId="%d"/></OutputVariable>'
                               % (attr("parameterName", a.pin), oid))
                sinks.append((a.value, oid))
        head = '<FbdObject xsi:type="Block"%s%s>' % (attr("typeName", type_name),
                                                    attr("instanceName", instance) if instance else "")
        body = ""
        if inputs:
            body += "<InputVariables>%s</InputVariables>" % "".join(inputs)
        if outputs:
            body += "<OutputVariables>%s</OutputVariables>" % "".join(outputs)
        self.objs.append(head + body + "</FbdObject>")
        for value, ref in sinks:
            self.data_sink(value, ref)
        return ids

    def element(self, e, ins):
        """Write one element; return [(out id, may go to the right rail)]."""
        k = e.kind
        if k in ("contact", "coil"):
            oid = self.new_id()
            a = ""
            if k == "contact":
                if e.sub in ("NC", "NP", "NN"):
                    a += ' negated="true"'
                if e.sub in ("P", "NP"):
                    a += ' edge="rising"'
                elif e.sub in ("N", "NN"):
                    a += ' edge="falling"'
                xtype = "Contact"
            else:
                a += {"COIL": "", "NOT": ' negated="true"', "SET": ' latch="set"', "RESET": ' latch="reset"',
                      "RISE": ' edge="rising"', "FALL": ' edge="falling"'}[e.sub]
                xtype = "Coil"
            self.objs.append('<LdObject xsi:type="%s"%s%s>%s<ConnectionPointOut connectionPointOutId="%d"/>'
                             '</LdObject>' % (xtype, a, attr("operand", e.name), self.cp_in(ins, ""), oid))
            return [(oid, True)]
        if k == "cmp":
            oid = self.new_id()
            self.objs.append('<LdObject xsi:type="CompareContact"%s%s%s>%s<ConnectionPointOut '
                             'connectionPointOutId="%d"/></LdObject>' % (
                                 attr("compareOperator", model.CMP_OPERATORS[e.sub]), attr("operand1", e.ops[0]),
                                 attr("operand2", e.ops[1]), self.cp_in(ins, ""), oid))
            return [(oid, True)]
        if k == "callcontact":
            ids = self.block(e.name, None, ("EN", ins), e.args, [""])
            return [(ids[""], True)]
        if k == "fn_eno":
            ids = self.block(e.name, None, ("EN", ins), e.args, ["ENO"], ret_dst=e.dst)
            return [(ids["ENO"], True)]
        if k == "fb_eno":
            ids = self.block(e.fbtype, e.name, ("EN", ins), e.args, ["ENO"])
            return [(ids["ENO"], True)]
        if k == "fb_in":
            if e.power_out:
                ids = self.block(e.fbtype, e.name, (e.pin, ins), e.args, [e.power_out])
                return [(ids[e.power_out], True)]
            self.block(e.fbtype, e.name, (e.pin, ins), e.args, [])
            return [(i, False) for i in ins]
        if k == "fb_scan":
            self.block(e.fbtype, e.name, None, e.args, [])
            return []
        if k in ("mc", "mcr"):
            # The Omron form: MC (power input In) / MCR (power input EN), the number as a constant input MCNo.
            ids = self.block(k.upper(), None, ("In" if k == "mc" else "EN", ins),
                             [model.Arg("MCNo", ":=", e.mcno, 1)], ["ENO"])
            return [(ids["ENO"], True)]
        if k == "jump":
            # Jump has a power input only: the rung does not reach the right rail through it.
            self.objs.append('<FbdObject xsi:type="Jump"%s>%s</FbdObject>' % (
                attr("targetNetworkLabel", e.name), self.cp_in(ins, "")))
            return []
        raise ValueError("no standard form for %s" % k)

    def emit(self, tree, ins):
        if tree[0] == "L":
            return self.element(tree[1], ins)
        if tree[0] == "S":
            outs = [(i, True) for i in ins]
            for t in tree[1]:
                outs = self.emit(t, [i for i, _ in outs])
            return outs
        outs = []
        for t in tree[1]:
            outs.extend(self.emit(t, ins))
        return outs

    def rung(self, tree, unpowered, comments, eval_order, label=None):
        parts = []
        for c in comments:
            parts.append('<CommonObject xsi:type="Comment"><Content xsi:type="SimpleText">%s</Content>'
                         '</CommonObject>' % escape(c))
        left = self.new_id()
        for e in unpowered:
            self.element(e, [])
        outs = self.emit(tree, [left]) if tree is not None else []
        rail = []
        seen = set()
        for i, ok in outs:
            if ok and i not in seen:
                seen.add(i)
                rail.append(i)
        parts.append('<LdObject xsi:type="LeftPowerRail"><ConnectionPointOut connectionPointOutId="%d"/></LdObject>'
                     % left)
        parts.extend(self.objs)
        parts.append('<LdObject xsi:type="RightPowerRail">%s</LdObject>' % "".join(
            '<ConnectionPointIn><Connection refConnectionPointOutId="%d"/></ConnectionPointIn>' % i for i in rail))
        return '<Rung%s evaluationOrder="%d">%s</Rung>' % (attr("label", label) if label else "", eval_order,
                                                           "".join(parts))


def write_rung(tree, unpowered, comments, eval_order, label=None):
    """One Rung. label: the Rung@label attribute (the target of Jump objects). A label without elements (the label
    of a rung that was not converted, or a LABEL at the end of a body) is a rung with only the two rails."""
    return RungWriter().rung(tree, unpowered, comments, eval_order, label)


def project_head(name, creation):
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<Project xmlns="%s" xmlns:xsi="%s" schemaVersion="1">\n'
            '<FileHeader companyName="rungiec" productName="rungiec2xml" productVersion="0.1"/>\n'
            '<ContentHeader%s%s/>\n<Types>\n<GlobalNamespace>\n' % (NS, XSI, attr("name", name),
                                                                   attr("creationDateTime", creation)))


def project_tail():
    return "</GlobalNamespace>\n</Types>\n<Instances/>\n</Project>\n"


def program(name, rungs):
    return '<Program%s>\n<MainBody>\n<BodyContent xsi:type="LD">\n%s</BodyContent>\n</MainBody>\n</Program>\n' % (
        attr("name", name), "".join(r + "\n" for r in rungs))
