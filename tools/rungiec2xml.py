#!/usr/bin/env python3
"""Convert RUNGIEC fragments written by xml2rungiec back to standard IEC 61131-10 LD XML.

usage: python tools/rungiec2xml.py DIR/rungiec --out FILE.xml [--creation-date ISO]

One Program <pou>_<section> per fragment, one Rung per '(* @rung N *)' unit. JMP(L) becomes a Jump object and LABEL(L) the label attribute of the Rung of its unit; the LABEL
after a '(* @label N *)' marker (the label of a rung that was not converted) becomes a Rung with the label and
the two rails only (counted as label_only_rungs, not as units). BEGIN_MC(cond...) becomes a rung with the
conditions and an MC block (inputs In and MCNo), END_MC() a rung with an MCR block (inputs EN and MCNo), the way
Omron Sysmac Studio writes master control; MCNo is the nesting depth of the region in the body (0 for the
outermost, up to 14). Rungs with ST_EXEC have no standard form: they are skipped and counted (the summary prints
"st-exec-no-standard-form") and the run ends with exit 1, like any other unit that cannot be written. Everything
skipped is listed in FILE.report.json with the mapping of every written rung to its source body and rung.

Exit codes: 0 = done, every unit written; 1 = a unit could not be written (ST_EXEC, an END_MC without a BEGIN_MC or
a BEGIN_MC left open at the end of the body: mc-unbalanced, nesting over 15, or any other unreadable form);
2 = the input directory could not be read.
"""

import sys

sys.dont_write_bytecode = True

import argparse  # noqa: E402
import datetime  # noqa: E402
import glob  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import time  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ld_xml import rungiec_reader, std_writer  # noqa: E402

ST_EXEC_REASON = "st-exec-no-standard-form"  # a known cause of skipped units; still a failure (exit 1)


def safe_name(text):
    return re.sub(r"[^A-Za-z0-9_]", "_", text or "") or "_"


def read(path):
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def write_text(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def convert(frag_dir, out_file, creation_date=None):
    """Return (exit code, report)."""
    t0 = time.time()
    report_path = (out_file[:-4] if out_file.endswith(".xml") else out_file) + ".report.json"
    for path in (out_file, report_path):
        if os.path.exists(path):
            os.remove(path)
    report = {"input": os.path.basename(os.path.normpath(frag_dir)), "fragments": 0, "units": 0, "converted": 0,
              "label_only_rungs": 0, "skipped": {}, "items": [], "mapping": []}
    if not os.path.isdir(frag_dir):
        report["error"] = "not a directory: %s" % frag_dir
        report["complete"] = False
        write_text(report_path, json.dumps(report, indent=1) + "\n")
        return 2, report
    if creation_date is None:
        creation_date = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    paths = sorted(glob.glob(os.path.join(frag_dir, "*.iec")))
    names = set()
    programs = []
    for path in paths:
        label = os.path.basename(path)
        frag = rungiec_reader.read_fragment(read(path), label)
        report["fragments"] += 1
        base = "%s_%s" % (safe_name(frag.pou), safe_name(frag.section))
        name = base
        k = 2
        while name in names:
            name = "%s_%d" % (base, k)
            k += 1
        names.add(name)
        rungs = []
        if frag.loose:
            report["items"].append({"fragment": label, "ordinal": None, "reason": "unsupported-form",
                                    "detail": "%d statements outside @rung markers" % len(frag.loose)})
            report["skipped"]["unsupported-form"] = report["skipped"].get("unsupported-form", 0) + 1
        for unit in frag.units:
            if unit.label_only:
                # The label of a source rung that was not converted: a rung with the label and the two rails only.
                try:
                    label_name = rungiec_reader.unit_label(unit)
                except rungiec_reader.Unsupported as exc:
                    report["label_errors"] = report.get("label_errors", 0) + 1
                    report["items"].append({"fragment": label, "body": frag.body, "ordinal": unit.ordinal,
                                            "reason": exc.reason, "detail": exc.detail})
                    continue
                rungs.append(std_writer.write_rung(None, [], [], len(rungs) + 1, label=label_name))
                report["label_only_rungs"] += 1
                continue
            report["units"] += 1
            try:
                tree, unpowered, rung_label = rungiec_reader.unit_rung(unit, frag)
                xml = std_writer.write_rung(tree, unpowered, unit.comments, len(rungs) + 1, label=rung_label)
            except rungiec_reader.Unsupported as exc:
                report["skipped"][exc.reason] = report["skipped"].get(exc.reason, 0) + 1
                report["items"].append({"fragment": label, "body": frag.body, "ordinal": unit.ordinal,
                                        "reason": exc.reason, "detail": exc.detail})
                if unit.statements and unit.statements[0].name == "LABEL":
                    # Keep the label of the skipped unit so that the Jump objects to it stay valid.
                    try:
                        label_name = rungiec_reader.label_arg(unit.statements[0])
                    except rungiec_reader.Unsupported:
                        continue
                    rungs.append(std_writer.write_rung(None, [], [], len(rungs) + 1, label=label_name))
                    report["label_only_rungs"] += 1
                continue
            rungs.append(xml)
            report["converted"] += 1
            report["mapping"].append({"program": name, "evaluationOrder": len(rungs), "body": frag.body,
                                      "ordinal": unit.ordinal, "fragment": label})
        if frag.mc_depth > 0:
            report["skipped"]["mc-unbalanced"] = report["skipped"].get("mc-unbalanced", 0) + 1
            report["items"].append({"fragment": label, "body": frag.body, "ordinal": None, "reason": "mc-unbalanced",
                                    "detail": "%d BEGIN_MC not closed by END_MC at the end of the body" % frag.mc_depth})
        programs.append(std_writer.program(name, rungs))
    text = std_writer.project_head("rungiec2xml", creation_date) + "".join(programs) + std_writer.project_tail()
    write_text(out_file, text)
    report["skipped"] = dict(sorted(report["skipped"].items()))
    bad = dict(report["skipped"])
    if report.get("label_errors"):
        bad["label-only unit"] = report["label_errors"]
    report["elapsed_s"] = round(time.time() - t0, 2)
    report["complete"] = True
    write_text(report_path, json.dumps(report, indent=1, ensure_ascii=False) + "\n")
    return (1 if bad else 0), report


def main(argv=None):
    parser = argparse.ArgumentParser(description="RUNGIEC fragments -> standard IEC 61131-10 LD XML")
    parser.add_argument("input", help="directory of .iec fragments (DIR/rungiec of xml2rungiec)")
    parser.add_argument("--out", required=True)
    parser.add_argument("--creation-date", default=None, help="creationDateTime, e.g. 2026-01-01T00:00:00Z")
    args = parser.parse_args(argv)
    code, report = convert(args.input, args.out, args.creation_date)
    print("fragments: %d  units: %d  converted: %d  label-only rungs: %d" % (
        report["fragments"], report["units"], report["converted"], report.get("label_only_rungs", 0)))
    for reason, n in report["skipped"].items():
        print("  skipped %-28s %d" % (reason, n))
    st_exec = report["skipped"].get(ST_EXEC_REASON, 0)
    if st_exec:
        print("ST_EXEC has no standard form: %d units skipped (counted as failures)" % st_exec)
    if code:
        print("ERROR: %s" % (report.get("error") or "units that could not be read (see the report)"))
    return code


if __name__ == "__main__":
    sys.exit(main())
