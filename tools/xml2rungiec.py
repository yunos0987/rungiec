#!/usr/bin/env python3
"""Convert the LD bodies of an IEC 61131-10 XML file (Omron Sysmac Studio export or standard) to RUNGIEC fragments.

usage: python tools/xml2rungiec.py IN.xml --out DIR

Writes DIR/rungiec/NNNN_<pou>_<section>.iec (one per LD body with at least one converted rung) and
DIR/xml2rungiec.json (counts by status and skip reason, the list of rungs not converted, the index of the
converted rungs). Rungs that RUNGIEC v1 cannot express are skipped and listed, the others are converted.
In a fragment, each rung that is not converted leaves '(* @skipped N <reason> *)' at its position (N = the rung
ordinal of '(* @rung N *)'; "fragment" of its json item names the file, null when the body has no fragment).
A body with a broken MC structure, a FOR / NEXT / BREAK rung, or a Jump / Return (or an inline ST with a return) that
is not converted is skipped whole and gets no fragment (reasons mc-body / loop-body / jump-body). The summary lists
every body that has rungs listed but no fragment, with the whole-body reason or no-converted-rung.

Exit codes: 0 = done, every rung accounted for and no conversion error; 1 = conversion errors or counts that do
not add up; 2 = the input could not be read (the json then has "complete": false and no fragment is written).
"""

import sys

sys.dont_write_bytecode = True

import argparse  # noqa: E402
import glob  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import time  # noqa: E402
import xml.etree.ElementTree as ET  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ld_xml import omron_reader, to_rungiec  # noqa: E402


def safe_name(text):
    return re.sub(r"[^A-Za-z0-9_]", "_", text or "") or "_"


def fragment_name(body):
    return "%04d_%s_%s.iec" % (body.ordinal, safe_name(body.pou), safe_name(body.section))


def write_text(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def write_json(path, data):
    write_text(path, json.dumps(data, indent=1, ensure_ascii=False) + "\n")


def clear_outputs(out_dir):
    frag_dir = os.path.join(out_dir, "rungiec")
    for path in glob.glob(os.path.join(frag_dir, "*.iec")):
        os.remove(path)
    json_path = os.path.join(out_dir, "xml2rungiec.json")
    if os.path.exists(json_path):
        os.remove(json_path)


def convert(in_path, out_dir, keep_canonical=False):
    """Run the conversion. Return (exit code, report dict, canonical map {(body, ordinal): str} or None)."""
    t0 = time.time()
    clear_outputs(out_dir)
    os.makedirs(os.path.join(out_dir, "rungiec"), exist_ok=True)
    source = os.path.basename(in_path)
    report = {"input": source, "total_rungs": 0, "bodies": {}, "counts": {}, "items": [], "converted_index": []}
    counts = {"converted": 0, "empty": 0, "skipped": {}, "error": 0}
    fragments = []
    canon = {} if keep_canonical else None
    current = {"body": None, "rungs": []}

    def finish_body():
        body, results = current["body"], current["rungs"]
        if body is None or not body.is_ld:
            return
        statements = to_rungiec.convert_body(results)
        name = None  # the fragment of the body (each rung not converted has a '@skipped' marker there)
        if statements:
            text, index = to_rungiec.fragment(body, source, results, statements)
            name = fragment_name(body)
            fragments.append((name, text))
            for ordinal, first, last in index:
                report["converted_index"].append({"body": body.ordinal, "ordinal": ordinal, "fragment": name,
                                                  "first_line": first, "last_line": last})
        for r in results:
            if r.status == "converted":
                counts["converted"] += 1
                if canon is not None:
                    canon[(body.ordinal, r.ordinal)] = r.canonical()
            else:
                if r.status == "empty":
                    counts["empty"] += 1
                elif r.status == "error":
                    counts["error"] += 1
                else:
                    counts["skipped"][r.reason] = counts["skipped"].get(r.reason, 0) + 1
                report["items"].append({"body": body.ordinal, "pou": body.pou, "section": body.section,
                                        "ordinal": r.ordinal, "evaluationOrder": r.eval_order, "status": r.status,
                                        "reason": r.reason, "detail": r.detail, "fragment": name})
            r.tree = None
            r.unpowered = []

    try:
        for event in omron_reader.iter_rungs(in_path):
            if event[0] == "body":
                finish_body()
                body = event[1]
                current["body"], current["rungs"] = body, []
                report["bodies"][body.btype] = report["bodies"].get(body.btype, 0) + 1
            else:
                report["total_rungs"] += 1
                current["rungs"].append(event[2])
        finish_body()
    except (ET.ParseError, OSError) as exc:
        report["error"] = "%s: %s" % (type(exc).__name__, exc)
        report["counts"] = counts
        report["elapsed_s"] = round(time.time() - t0, 2)
        report["complete"] = False
        write_json(os.path.join(out_dir, "xml2rungiec.json"), report)
        return 2, report, None

    for name, text in fragments:
        write_text(os.path.join(out_dir, "rungiec", name), text)
    counts["skipped"] = dict(sorted(counts["skipped"].items()))
    report["counts"] = counts
    report["fragments"] = len(fragments)
    total = counts["converted"] + counts["empty"] + counts["error"] + sum(counts["skipped"].values())
    report["counts_add_up"] = total == report["total_rungs"]
    report["elapsed_s"] = round(time.time() - t0, 2)
    report["complete"] = True
    write_json(os.path.join(out_dir, "xml2rungiec.json"), report)
    code = 0 if counts["error"] == 0 and report["counts_add_up"] else 1
    return code, report, canon


def print_summary(report):
    c = report["counts"]
    print("input: %s" % report["input"])
    print("total rungs: %d" % report["total_rungs"])
    print("converted: %d  empty: %d  error: %d  skipped: %d" % (
        c.get("converted", 0), c.get("empty", 0), c.get("error", 0), sum(c.get("skipped", {}).values())))
    for reason, n in sorted(c.get("skipped", {}).items(), key=lambda p: -p[1]):
        print("  skipped %-28s %d" % (reason, n))
    bodies = {}  # body ordinal -> (pou, section, reason codes); only the bodies without a fragment
    for it in report["items"]:
        if it["fragment"] is not None:
            continue
        bodies.setdefault(it["body"], (it["pou"], it["section"], set()))[2].add((it.get("reason") or "").split(":", 1)[0])
    for pou, section, reasons in bodies.values():
        print("  body skipped (%s): %s %s" % (to_rungiec.whole_body_reason(reasons) or "no-converted-rung", pou, section))
    print("fragments: %d  elapsed: %ss  complete: %s" % (report.get("fragments", 0), report["elapsed_s"],
                                                      report["complete"]))


def main(argv=None):
    parser = argparse.ArgumentParser(description="IEC 61131-10 LD XML -> RUNGIEC fragments")
    parser.add_argument("input")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    code, report, _ = convert(args.input, args.out)
    print_summary(report)
    if code == 2:
        print("ERROR: %s" % report.get("error"))
    elif code == 1:
        print("ERROR: %d conversion errors, counts add up: %s" % (report["counts"]["error"], report["counts_add_up"]))
    return code


if __name__ == "__main__":
    sys.exit(main())
