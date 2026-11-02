"""Round-trip checks: canonical forms of the source rungs against the regenerated standard XML, xsd validation and
namespace checks of the regenerated XML."""

import xml.etree.ElementTree as ET

from . import omron_reader

IEC_NS = "{%s}" % omron_reader.NS


def regenerated_canonicals(xml_path):
    """{(program name, evaluationOrder): (status, reason, canonical or None)} of the regenerated XML."""
    out = {}
    for event in omron_reader.iter_rungs(xml_path):
        if event[0] != "rung":
            continue
        body, res = event[1], event[2]
        key = (body.pou, int(res.eval_order))
        if res.status == "converted":
            out[key] = (res.status, None, res.canonical())
        else:
            out[key] = (res.status, res.reason, res.detail)
    return out


def compare(forward, regenerated, mapping, limit=20):
    """forward: {(body, ordinal): canonical}; mapping: rungiec2xml report mapping. Return a result dict."""
    samples = []
    compared = missing = count = 0
    for m in mapping:
        key = (m["body"], m["ordinal"])
        fwd = forward.get(key)
        reg = regenerated.get((m["program"], m["evaluationOrder"]))
        if fwd is None or reg is None:
            missing += 1
            count += 1
            if len(samples) < limit:
                samples.append({"body": key[0], "ordinal": key[1], "problem": "missing",
                                "forward": fwd is not None, "regenerated": reg is not None})
            continue
        compared += 1
        if reg[0] != "converted" or reg[2] != fwd:
            count += 1
            if len(samples) < limit:
                samples.append({"body": key[0], "ordinal": key[1], "program": m["program"],
                                "evaluationOrder": m["evaluationOrder"], "regenerated_status": reg[0],
                                "regenerated_reason": reg[1], "forward": fwd, "regenerated": reg[2]})
    return {"compared": compared, "missing": missing, "mismatches": count, "samples": samples}


def namespace_checks(xml_path):
    """Count elements outside the IEC namespace, AddData elements and 'smcext' occurrences."""
    foreign = 0
    add_data = 0
    for _, el in ET.iterparse(xml_path, events=("end",)):
        if not el.tag.startswith(IEC_NS):
            foreign += 1
        elif el.tag == IEC_NS + "AddData":
            add_data += 1
        el.clear()
    with open(xml_path, encoding="utf-8") as f:
        smcext = sum(line.count("smcext") for line in f)
    return {"foreign_elements": foreign, "add_data": add_data, "smcext": smcext}


def validate_xsd(xml_path, xsd_path, limit=20):
    """Return (error count, first errors) or None when lxml is not installed."""
    try:
        from lxml import etree
    except ImportError:
        return None
    schema = etree.XMLSchema(etree.parse(xsd_path))
    doc = etree.parse(xml_path)
    ok = schema.validate(doc)
    errors = [] if ok else list(schema.error_log)
    return len(errors), ["%d: %s" % (e.line, e.message) for e in errors[:limit]]
