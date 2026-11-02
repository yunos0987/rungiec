#!/usr/bin/env python3
"""Round trip of an LD XML file through RUNGIEC: forward, reverse, xsd validation, comparison, rung count and
(with --jiepp) jiepp expansion and lint of every fragment.

usage: python tools/ld_xml_roundtrip.py IN.xml --out DIR --xsd XSD [--jiepp PATH] [--rungiec-root DIR]
                                         [--creation-date ISO]

Steps:
  1. xml2rungiec IN.xml -> DIR/rungiec/*.iec, DIR/xml2rungiec.json
  2. rungiec2xml DIR/rungiec -> DIR/standard.xml, DIR/standard.report.json
  3. xsd validation of DIR/standard.xml (lxml), no element outside the IEC namespace, no AddData, no 'smcext'
  4. canonical form of every forward-converted rung == canonical form of the regenerated rung read back
  5. an independent count of the Rung elements of IN.xml == total_rungs of step 1
     (and, with step 1: the '(* @skipped N <reason> *)' markers of the fragments, read by ld_xml.rungiec_reader ==
     the rungs not converted of the bodies that have a fragment, by fragment, ordinal and reason; reports the
     mc-body / loop-body / jump-body counts; a body with a rung of reason mc-body / loop-body / jump-body has no
     fragment and none of its rungs in converted_index)
  5b. an independent scan of IN.xml (no use of omron_reader's detection): in every body that has a fragment, a rung
     with a Jump / Return object or an MC / MCR block was converted, and no rung has a FOR / NEXT / BREAK / RETURN /
     JMP block or an InlineST with a return (forward_no_flow_element_in_fragment_body; counts in roundtrip.json)
  6. (--jiepp) each fragment wrapped in a PROGRAM, expanded with jiepp -Werror=undef and checked with lint_file
Writes DIR/roundtrip.json.

Exit codes: 0 = every check passed; 1 = a check failed (the failed checks are printed); 2 = the input could not be
read; 3 = the other checks passed but a check was not run (lxml missing: "xsd validation NOT RUN", or no --jiepp, or a --jiepp path that does not exist).
"""

import sys

sys.dont_write_bytecode = True

import argparse  # noqa: E402
import collections  # noqa: E402
import concurrent.futures  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import subprocess  # noqa: E402
import time  # noqa: E402
import xml.etree.ElementTree as ET  # noqa: E402

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)

import lint_rungiec  # noqa: E402
import rungiec2xml  # noqa: E402
import xml2rungiec  # noqa: E402
from ld_xml import compare, omron_reader, rungiec_reader, st_text  # noqa: E402
from ld_xml.omron_reader import LD_BODY_TYPES, XSI_TYPE  # noqa: E402
from ld_xml.to_rungiec import BODY_SKIPS  # noqa: E402  (reasons of a body skipped whole)

DIAG_RE = re.compile(r"\b(?:error|warning): PP\d+")
EDGE_RE = re.compile(r"\b(?:RISE_CONTACT|FALL_CONTACT|NOT_RISE_CONTACT|NOT_FALL_CONTACT|RISE_COIL|FALL_COIL)\s*\(")
RESIDUAL_RE = re.compile(r"\b_RUNGIEC_\w+|\b(?:%s)\s*\(" % "|".join(sorted(lint_rungiec.PUBLIC_NAMES)))
WRAP_HEAD = 6  # lines before the fragment in the wrapped file
FLOW_BLOCKS = ("FOR", "NEXT", "BREAK", "RETURN", "JMP")  # a rung with one of these is never in a fragment body


def read_skipped_markers(frag_dir):
    """Counter of (fragment file name, ordinal, reason) of the '@skipped' markers of the fragments in frag_dir, as
    read by rungiec_reader."""
    found = collections.Counter()
    for fname in sorted(os.listdir(frag_dir)):
        with open(os.path.join(frag_dir, fname), encoding="utf-8", newline="") as f:
            frag = rungiec_reader.read_fragment(f.read(), fname)
        for ordinal, reason in frag.skipped:
            found[(fname, ordinal, reason)] += 1
    return found


def count_rungs(path):
    """Independent count of the Rung elements (any namespace)."""
    n = 0
    for _, el in ET.iterparse(path, events=("end",)):
        if el.tag.rsplit("}", 1)[-1] == "Rung":
            n += 1
        el.clear()
    return n


def flow_elements_in_fragment_bodies(path, items, converted_index, first_n=5):
    """Independent scan of the input (iterparse, constant memory; it does not use omron_reader's detection): in
    every LD body that has a fragment (a body with an entry in converted_index), look at the direct children of each
    Rung. A Jump / Return object or an MC / MCR block is fine only if the rung was converted (not in items); a
    FOR / NEXT / BREAK / RETURN / JMP block or an InlineST with a return in its code is never fine. Bodies are keyed
    as in the json: the position of the BodyContent among all BodyContent elements. Return a dict with the counts
    of the rungs seen ("jump_rungs", "mc_rungs", "flow_block_rungs", "inline_return_rungs"), "offending" and the
    first first_n offending as [body, rung ordinal, kind]."""
    with_fragment = {c["body"] for c in converted_index}
    not_converted = {(it["body"], it["ordinal"]) for it in items}
    seen = collections.Counter()
    offending = 0
    first = []
    body = ordinal = depth = 0
    is_ld = False
    for event, el in ET.iterparse(path, events=("start", "end")):
        tag = el.tag.rsplit("}", 1)[-1]
        if event == "start":
            if tag == "BodyContent":
                body += 1
                ordinal = 0
                is_ld = el.get(XSI_TYPE) in LD_BODY_TYPES
            elif tag == "Rung":
                depth += 1
            continue
        if tag == "Rung":
            depth -= 1
            if is_ld:
                ordinal += 1
                if body in with_fragment:
                    kinds = set()
                    for ch in el:
                        xtype = ch.get(XSI_TYPE) or ""
                        type_name = (ch.get("typeName") or "").upper()
                        if xtype in ("Jump", "Return"):
                            kinds.add("jump")
                        elif xtype == "Block" and type_name in ("MC", "MCR"):
                            kinds.add("mc")
                        elif xtype == "Block" and type_name in FLOW_BLOCKS:
                            kinds.add("flow_block")
                        elif xtype == "smcext:InlineST" and st_text.has_return(_inline_st_code(ch)):
                            kinds.add("inline_return")
                    for kind in sorted(kinds):
                        seen[kind + "_rungs"] += 1
                        if kind in ("jump", "mc") and (body, ordinal) not in not_converted:
                            continue
                        offending += 1
                        if len(first) < first_n:
                            first.append([body, ordinal, kind])
            el.clear()
        elif tag == "BodyContent":
            is_ld = False
            el.clear()
        elif depth == 0:
            el.clear()
    result = {k + "_rungs": seen[k + "_rungs"] for k in ("jump", "mc", "flow_block", "inline_return")}
    result["offending"] = offending
    result["first_offending"] = first
    return result


def _inline_st_code(elem):
    """ST body text of an InlineST element; all its text when the ST element is not found (over-detects, safe)."""
    for cp in elem:
        if cp.tag.rsplit("}", 1)[-1] == "ST":
            inner = [c for c in cp if c.tag.rsplit("}", 1)[-1] == "ST"]
            return (inner[0].text if inner else cp.text) or ""
    return " ".join(elem.itertext())


def wrap(fragment_text):
    code = lint_rungiec.Source("f", fragment_text).code
    edges = len(EDGE_RE.findall(code))
    head = ("{#define RUNGIEC_EDGE_MAX %d}\n{#include 'rungiec.iec'}\nprogram p\nRUNGIEC_VAR()\nBEGIN_RUNGIEC()\n"
            % max(64, edges))
    return head + fragment_text + "END_RUNGIEC()\nend_program\n"


def strip_comments(text):
    return re.sub(r"\(\*.*?\*\)", " ", text, flags=re.DOTALL)


def check_fragment(jiepp, root, frag_path, work_dir, index):
    name = os.path.basename(frag_path)
    with open(frag_path, encoding="utf-8", newline="") as f:
        text = f.read()
    wrapped = wrap(text)
    in_path = os.path.join(work_dir, name)
    out_path = os.path.join(work_dir, name[:-4] + ".st")
    xml2rungiec.write_text(in_path, wrapped)
    if os.path.exists(out_path):
        os.remove(out_path)
    proc = subprocess.run([jiepp, "-P", "-Werror=undef", "-I", root, in_path, "-o", out_path],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600)
    output = (proc.stdout + proc.stderr).decode("utf-8", errors="replace").replace("\r\n", "\n")
    diags = []
    for line in output.split("\n"):
        if DIAG_RE.search(line):
            m = re.search(r":(\d+)(?:\.\d+)?: (?:error|warning)", line)
            rung = None
            if m:
                frag_line = int(m.group(1)) - WRAP_HEAD
                for ordinal, first, last in index:
                    if first <= frag_line <= last:
                        rung = ordinal
            diags.append({"line": line.strip()[:300], "rung": rung})
    result = {"fragment": name, "rc": proc.returncode, "diagnostics": diags, "directive_left": False,
              "residual": None, "output": os.path.exists(out_path), "lint": []}
    if os.path.exists(out_path):
        with open(out_path, encoding="utf-8", errors="replace") as f:
            st = f.read()
        result["directive_left"] = "{#" in st
        m = RESIDUAL_RE.search(strip_comments(st))
        result["residual"] = m.group(0) if m else None
    findings = lint_rungiec.lint_file(name, wrapped, "example")
    result["lint"] = [f.text() for f in findings]
    return result


def run_jiepp(jiepp, root, frag_dir, work_dir, report):
    os.makedirs(work_dir, exist_ok=True)
    index = {}
    for item in report["converted_index"]:
        index.setdefault(item["fragment"], []).append((item["ordinal"], item["first_line"], item["last_line"]))
    paths = sorted(os.path.join(frag_dir, n) for n in os.listdir(frag_dir) if n.endswith(".iec"))
    # More than 4 concurrent jiepp/jiecc processes are not allowed on the development machines (the antivirus
    # software can take the OS down).
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, os.cpu_count() or 1)) as pool:
        results = list(pool.map(lambda p: check_fragment(jiepp, root, p, work_dir,
                                                         index.get(os.path.basename(p), [])), paths))
    summary = {
        "fragments": len(results),
        "failed_runs": sum(1 for r in results if r["rc"] != 0 or not r["output"]),
        "diagnostic_lines": sum(len(r["diagnostics"]) for r in results),
        "directive_left": sum(1 for r in results if r["directive_left"]),
        "residual": sum(1 for r in results if r["residual"]),
        "lint_findings": sum(len(r["lint"]) for r in results),
        "problems": [r for r in results if r["rc"] != 0 or not r["output"] or r["diagnostics"]
                     or r["directive_left"] or r["residual"] or r["lint"]][:30],
    }
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description="LD XML -> RUNGIEC -> standard LD XML round trip")
    parser.add_argument("input")
    parser.add_argument("--out", required=True)
    parser.add_argument("--xsd", required=True)
    parser.add_argument("--jiepp", default=None)
    parser.add_argument("--rungiec-root", default=os.path.dirname(TOOLS))
    parser.add_argument("--creation-date", default="2000-01-01T00:00:00Z")
    args = parser.parse_args(argv)
    t0 = time.time()
    out = {"input": os.path.basename(args.input), "checks": {}, "elapsed_s": {}}
    checks = out["checks"]
    os.makedirs(args.out, exist_ok=True)

    t = time.time()
    code, fwd, canon = xml2rungiec.convert(args.input, args.out, keep_canonical=True)
    out["elapsed_s"]["forward"] = round(time.time() - t, 2)
    out["forward"] = {k: fwd.get(k) for k in ("total_rungs", "bodies", "counts", "fragments", "counts_add_up",
                                              "complete", "error")}
    if code == 2:
        out["checks"]["input_readable"] = False
        xml2rungiec.write_json(os.path.join(args.out, "roundtrip.json"), out)
        print("ERROR: cannot read %s: %s" % (args.input, fwd.get("error")))
        return 2
    counts = fwd["counts"]
    checks["forward_complete"] = fwd["complete"] is True
    checks["forward_errors_0"] = counts["error"] == 0
    checks["forward_counts_add_up"] = bool(fwd["counts_add_up"])
    checks["forward_reasons_known"] = all(r.split(":", 1)[0] in omron_reader.PRECEDENCE for r in counts["skipped"])
    # Every rung that is not converted (skipped, empty, error) in a body that has a fragment has one '@skipped'
    # marker with its ordinal and reason code in that fragment, and the fragments hold no other '@skipped' marker.
    expected = collections.Counter(
        (it["fragment"], it["ordinal"], it["reason"].split(":", 1)[0]) for it in fwd["items"] if it["fragment"])
    found = read_skipped_markers(os.path.join(args.out, "rungiec"))
    out["skipped_markers"] = {"markers": sum(found.values()), "expected": sum(expected.values()),
                              "items_without_fragment": sum(1 for it in fwd["items"] if not it["fragment"]),
                              "mc_body": counts["skipped"].get("mc-body", 0),
                              "loop_body": counts["skipped"].get("loop-body", 0),
                              "jump_body": counts["skipped"].get("jump-body", 0)}
    checks["forward_skip_markers_match"] = found == expected
    # A body skipped whole (mc-body / loop-body / jump-body) has no fragment and none of its rungs is in the converted index.
    skipped_bodies = {it["body"] for it in fwd["items"] if (it["reason"] or "").split(":", 1)[0] in BODY_SKIPS}
    checks["forward_body_skips_have_no_fragment"] = (
        all(it["fragment"] is None for it in fwd["items"] if (it["reason"] or "").split(":", 1)[0] in BODY_SKIPS)
        and not any(c["body"] in skipped_bodies for c in fwd["converted_index"]))

    t = time.time()
    flow = flow_elements_in_fragment_bodies(args.input, fwd["items"], fwd["converted_index"])
    out["elapsed_s"]["flow"] = round(time.time() - t, 2)
    out["flow_elements"] = flow
    checks["forward_no_flow_element_in_fragment_body"] = flow["offending"] == 0

    t = time.time()
    independent = count_rungs(args.input)
    out["elapsed_s"]["count"] = round(time.time() - t, 2)
    out["independent_rung_count"] = independent
    checks["independent_count_matches"] = independent == fwd["total_rungs"]

    t = time.time()
    xml_path = os.path.join(args.out, "standard.xml")
    rcode, rev = rungiec2xml.convert(os.path.join(args.out, "rungiec"), xml_path, args.creation_date)
    out["elapsed_s"]["reverse"] = round(time.time() - t, 2)
    out["reverse"] = {k: rev.get(k) for k in ("fragments", "units", "converted", "skipped")}
    # Every unit must be written. ST_EXEC has no standard form, so each ST_EXEC unit is a failure of this check;
    # reverse_skips_only_st_exec tells whether that is the only reason (False: some other unit cannot be read).
    st_exec_skipped = rev["skipped"].get(rungiec2xml.ST_EXEC_REASON, 0)
    out["reverse"]["st_exec_skipped"] = st_exec_skipped
    checks["reverse_skips_0"] = rcode == 0 and not rev["skipped"]
    checks["reverse_skips_only_st_exec"] = all(r == rungiec2xml.ST_EXEC_REASON for r in rev["skipped"])
    checks["reverse_counts_add_up"] = rev["converted"] + sum(rev["skipped"].values()) == counts["converted"] and \
        rev["units"] == counts["converted"]

    t = time.time()
    xsd = compare.validate_xsd(xml_path, args.xsd)
    if xsd is None:
        out["xsd"] = "NOT RUN (lxml missing)"
        checks["xsd_errors_0"] = None
        print("xsd validation NOT RUN")
    else:
        out["xsd"] = {"errors": xsd[0], "first_errors": xsd[1]}
        checks["xsd_errors_0"] = xsd[0] == 0
    ns = compare.namespace_checks(xml_path)
    out["namespace"] = ns
    checks["namespace_clean"] = ns["foreign_elements"] == 0 and ns["add_data"] == 0 and ns["smcext"] == 0
    out["elapsed_s"]["xsd"] = round(time.time() - t, 2)

    t = time.time()
    regen = compare.regenerated_canonicals(xml_path)
    cmp_result = compare.compare(canon, regen, rev["mapping"])
    out["compare"] = cmp_result
    checks["mismatches_0"] = cmp_result["mismatches"] == 0 and cmp_result["compared"] == rev["converted"]
    out["elapsed_s"]["compare"] = round(time.time() - t, 2)

    jiepp_missing = bool(args.jiepp) and not os.path.isfile(args.jiepp)
    if jiepp_missing:
        out["jiepp"] = "NOT RUN (--jiepp %s does not exist)" % args.jiepp
        checks["jiepp_clean"] = None
        checks["lint_clean"] = None
        print("jiepp check NOT RUN: --jiepp %s does not exist" % args.jiepp)
    elif args.jiepp:
        t = time.time()
        js = run_jiepp(os.path.abspath(args.jiepp), os.path.abspath(args.rungiec_root),
                       os.path.join(args.out, "rungiec"), os.path.join(args.out, "jiepp"), fwd)
        out["jiepp"] = js
        checks["jiepp_clean"] = js["failed_runs"] == 0 and js["diagnostic_lines"] == 0 and \
            js["directive_left"] == 0 and js["residual"] == 0
        checks["lint_clean"] = js["lint_findings"] == 0
        out["elapsed_s"]["jiepp_lint"] = round(time.time() - t, 2)
    else:
        out["jiepp"] = "NOT RUN (no --jiepp)"
        checks["jiepp_clean"] = None
        checks["lint_clean"] = None
        print("jiepp check NOT RUN")
    out["elapsed_s"]["total"] = round(time.time() - t0, 2)
    failed = [k for k, v in checks.items() if v is False]
    not_run = [k for k, v in checks.items() if v is None]
    out["failed"] = failed
    out["not_run"] = not_run
    xml2rungiec.write_json(os.path.join(args.out, "roundtrip.json"), out)

    print("input: %s  rungs: %d (independent count %d)" % (out["input"], fwd["total_rungs"], independent))
    print("forward: converted %d  empty %d  error %d  skipped %d" % (
        counts["converted"], counts["empty"], counts["error"], sum(counts["skipped"].values())))
    for reason, n in sorted(counts["skipped"].items(), key=lambda p: -p[1]):
        print("  forward skipped %-26s %d" % (reason, n))
    sm = out["skipped_markers"]
    print("skipped markers: %d (expected %d; %d items in bodies without a fragment)  mc-body %d  loop-body %d  jump-body %d" % (
        sm["markers"], sm["expected"], sm["items_without_fragment"], sm["mc_body"], sm["loop_body"], sm["jump_body"]))
    print("flow elements in bodies with a fragment: Jump/Return rungs %d  MC/MCR rungs %d  FOR/NEXT/BREAK/RETURN/JMP "
          "block rungs %d  InlineST return rungs %d  offending %d %s" % (
              flow["jump_rungs"], flow["mc_rungs"], flow["flow_block_rungs"], flow["inline_return_rungs"],
              flow["offending"], flow["first_offending"]))
    print("reverse: units %d  converted %d  skipped %s" % (rev["units"], rev["converted"], rev["skipped"]))
    if st_exec_skipped:
        print("reverse: ST_EXEC has no standard form: %d units skipped (counted as failures)" % st_exec_skipped)
    print("xsd: %s  namespace: %s" % (out["xsd"] if xsd is None else "%d errors" % xsd[0], ns))
    print("compare: compared %d  mismatches %d" % (cmp_result["compared"], cmp_result["mismatches"]))
    if args.jiepp and not jiepp_missing:
        print("jiepp: fragments %d  failed runs %d  diagnostic lines %d  '{#' left %d  residual %d  lint findings %d"
              % (js["fragments"], js["failed_runs"], js["diagnostic_lines"], js["directive_left"], js["residual"],
                 js["lint_findings"]))
    print("elapsed: %s" % out["elapsed_s"])
    if failed:
        print("FAILED checks: %s" % ", ".join(failed))
        return 1
    if not_run:
        print("NOT RUN checks: %s" % ", ".join(not_run))
        return 3
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
