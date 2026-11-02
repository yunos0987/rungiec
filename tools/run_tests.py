#!/usr/bin/env python3
"""Test runner for RUNGIEC: lint, then jiepp expansion, jiecc --run and verdict for each test.

Usage (from the repository root):
    python tools/run_tests.py [--only GLOB] [--no-lint] [--lint-only] [--lint-selftest]
                              [--jiepp PATH] [--jiecc PATH] [--jiecunit DIR]
                              [--tests-dir DIR] [--out-dir DIR] [--examples-dir DIR]

Test name prefixes: t = pass, e = preprocessing error, r = residual macro text,
f = jiecc-stage error (compile or run; the output must match the pattern).
e: the pattern is matched against the jiepp diagnostic lines (error: PPnn / warning: PPnn) only.
Examples (examples/*.iec) hold only user-written code and are not run directly. Each one is one function block
named after its file (examples/<stem>.iec defines 'function_block <stem>', no other function block and no
program). Each example is checked by a tests/x_<name>.iec file that includes it (the runner adds examples/ to the
include path); an example that no tests/x_*.iec includes fails the run. x = example check (must be
'@expect pass'). Before running an x_ test the runner checks the examples it includes (the rules above) and that
the test declares an instance of each of their function blocks ('<name>: <stem>;'). Without --only, the runner
also converts every example with 'jiecc examples/<stem>.iec -I <root> -t <target> -o <xml>' for omron, keyence,
codesys and mitsubishi (MAX_PARALLEL_JIECC at a time) and fails on a non-zero exit, an error or warning line, or an XML without
<FunctionBlock name="<stem>">. For an example that uses JMP() or LABEL(), the conversions for omron, keyence and
mitsubishi must instead fail with only the PP91 of rungiec.iec ('RUNGIEC JMP to <label> is not supported for the jiecc
targets omron, keyence and mitsubishi', and the same for LABEL) and write no XML, and the XML for codesys must have
'jmp <label>;' and no 'goto <label>;'. Each failed check is counted as a failure in the RESULT totals (an XML line
reports the passed conversions).

Without --only, the runner also converts every example in the LD mode (docs/TESTING.md "LD モードの変換の検査") with
'jiecc examples/<stem>.iec -I <root> -D RUNGIEC_LD -P -nC -t <target> -o <xml>' for each of XML_TARGETS, in the same
pool as the conversions above, and checks: (1) exit code 0 and no error or warning line; (2) the XML parses with
xml.etree; (3) the bodies are LD only (one <BodyContent xsi:type="LD">, LD_BODY_COUNTS for keyence), there is
<FunctionBlock name="<stem>"> and no ST of the work variables (RungiecAcc); (4) the first LD body equals the first LD
body of the expected file tests/expanded/<stem>.ld.xml (the omron XML, tracked by Git; the body is compared as
xml.etree serializes it, the ContentHeader with its creationDateTime is not), and the other targets give the body of
omron. On a difference or a missing expected file the runner writes the omron XML to
tests/expanded/<stem>.ld.xml.new (ignored by Git); review it and rename it to <stem>.ld.xml. (5) With lxml and the
environment variable RUNGIEC_IEC10_XSD naming the IEC 61131-10 XSD, each XML is validated against the XSD, except for
the examples that use ST_EXEC (smcext:InlineST is not in the XSD); otherwise one 'XSD NOT RUN' line is printed and
the check is not counted. (6) Round trip: tools/xml2rungiec.py converts the omron XML back to a RUNGIEC fragment; the
fragment (wrapped as tools/ld_xml_roundtrip.py does) and the example are expanded in the ST mode with jiepp and the
ST after the init statements of BEGIN_RUNGIEC() up to the end of the POU must be the same without comments and
with whitespace collapsed (and must have the work variables, so that two empty parts do not pass). The examples that
xml2rungiec cannot convert fully are listed with the reason in LD_ROUNDTRIP_SKIP; a listed example that converts fully
or an unlisted one that does not fails. A jiepp without the LD built-ins (__xmlattr and others) makes every LD
conversion fail with the error of rungiec.iec. 'LD XML' and 'LD ROUNDTRIP' lines report the passed checks.

t and x tests are jiecunit tests ('@expect pass'; '@expect pass' is accepted only for them). jiecunit is the git
submodule vendor/jiecunit (--jiecunit overrides the directory; a missing jiecunit.iec is an environment error:
run 'git submodule update --init'). Each t/x file includes tests/support/t_jiecunit.iec, has one
DEFINE_CONFIGURATION(... _JIEC_TESTNAMES_LIST_(<stem>) ...) and one TEST_M(<stem>) ... END_TEST_M whose body starts with
T_BEGIN(). The '(* @cycles N *)' header is required with N >= 1. The runner passes -D T_CYCLES=N from it and runs jiecc --run --max-cycles N+2: scans
1..N run the test, scan N+1 ends it (EOT() in T_BEGIN()). Before running, the source (comments removed) must have
at least one EXPECT_ / ASSERT_, exactly one T_BEGIN(), no 'program __main__' (jiecc would silently not run it under
the configuration), and no EOT( / SKIP_TEST( of its own. The run passes when the output has no error line,
exactly one '[========] 1 tests ran.', '[ PASSED ] 1 tests.', '[     OK ] <stem>', and no '[ FAILED ]' or
'[  ERROR ]' ('[ PASSED ] 0 tests.' is printed even when the test fails, so it is not used).
jiecc --run predefines _JIER, _OMRON and the other target macros that jiecunit tests in {#if}; the standalone jiepp
with -Werror=undef would report PP54 on them. At start the runner runs 'jiecc --run -E -dM' on an empty file once
and passes the result to jiepp with -include for the t/x tests (it follows the jiecc version; a failure is an
environment error). e/f/r tests do not use jiecunit and run as before.

Without --only and --no-lint, the runner first runs the lint (tools/lint_rungiec.py, rules LN0..LN25 in
docs/DESIGN.md). Its findings do not stop the tests; the exit code is 1 if the lint or a test fails.
--lint-only runs the lint alone and --lint-selftest runs its self-test alone (neither needs jiepp or jiecc).
An 'ir-audit: [ERROR]' line in the jiecc output fails an '@expect pass' test; an f-test can expect it with
'(* @expect run-error ir-audit: .ERROR. *)'.

Tools: tools/jiepp and tools/jiecc under the repository root by default, or the same names with .exe
(tools/jiepp.exe and tools/jiecc.exe on Windows; not tracked by Git);
--jiepp / --jiecc or the environment variables RUNGIEC_JIEPP / RUNGIEC_JIECC override them. The PATH is not used.
jiepp runs with -Werror=undef: an identifier that is not a macro in a {#if} condition or an __assert condition
(PP54) is a preprocessing error.

The runner removes the repository root from the paths in the jiepp and jiecc output (diagnostics show
'tests/e041_wire_variable_node.iec:16.0: ...') before it writes .pp.txt / .run.txt and matches '@expect' patterns.

Exit codes: 0 all passed, 1 failure, 2 environment error (missing binary, missing jiecunit, predefine failure).
"""
import argparse
import concurrent.futures
import fnmatch
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS_DIR)
sys.path.insert(0, TOOLS_DIR)

import ld_xml_roundtrip  # noqa: E402
import lint_rungiec  # noqa: E402
import xml2rungiec  # noqa: E402
from ld_xml import compare as ld_compare  # noqa: E402
from ld_xml.omron_reader import NS as IEC10_NS, XSI_TYPE  # noqa: E402

# Without a suffix so that Linux finds tools/jiepp and Windows finds tools/jiepp.exe (resolve_exe adds .exe).
DEFAULT_JIEPP = os.path.join(ROOT, "tools", "jiepp")
DEFAULT_JIECC = os.path.join(ROOT, "tools", "jiecc")
DEFAULT_JIECUNIT = os.path.join(ROOT, "vendor", "jiecunit")
TIMEOUT_SEC = 120
# Number of jiecc runs at the same time in the example conversions. More than 4 concurrent jiepp/jiecc processes
# are not allowed on the development machines (the antivirus software can take the OS down), and each jiecc
# starts one jiepp of its own, so 2 jiecc runs make at most 4 processes.
MAX_PARALLEL_JIECC = 2

# A call of a public macro (lint_rungiec.PUBLIC_NAMES, which lint rule LN7 keeps equal to the public names that
# rungiec_st.iec and rungiec_ld.iec define) left in the
# expanded ST is a residual.
RESIDUAL_RE = re.compile(
    r"\b(_RUNGIEC_\w+|T_\w+|"
    + "|".join(sorted(lint_rungiec.PUBLIC_NAMES, key=len, reverse=True))
    + r")\s*\("
)
# Any internal _RUNGIEC_ name left in the expanded ST is a residual, with or without a parenthesis.
# {#ignore PP91} leaves dispatch names such as _RUNGIEC_SERIES_0 or _RUNGIEC_SERIES_33.
INTERNAL_ANY_RE = re.compile(r"\b_RUNGIEC_\w+")
# A jiepp diagnostic line. An e-test pattern is matched only against these lines: on PP91 jiepp may also print
# the expanded text, and the @expect comment of the test itself would match its own pattern.
DIAG_RE = re.compile(r"\b(?:error|warning): PP\d+")
FAIL_PATTERNS = ("error:", "InterpreterFault", "Traceback", "ir-audit: [ERROR]")
# jiecunit summary lines (gtest style).
JU_RAN = "[========] 1 tests ran."
JU_PASSED = "[ PASSED ] 1 tests."
JU_BAD = ("[ FAILED ]", "[  ERROR ]")
INCLUDE_RE = re.compile(r"\{#include\s+['\"]([^'\"]+)['\"]\s*\}")
# jiecc -t targets that each example is converted to (IEC 61131-10 XML for the vendor tools).
XML_TARGETS = ("omron", "keyence", "codesys", "mitsubishi")
# The ST jump keyword that JMP() expands to for a target (rungiec_st.iec selects it with the target macros of jiecc);
# a target not listed gets goto. An example that uses JMP() must have exactly this keyword in its XML (for the
# targets that are not in NO_JUMP_TARGETS).
JUMP_KEYWORDS = {"codesys": "jmp"}
# The jiecc -t targets for which JMP() and LABEL() are the preprocessing error PP91 (_RUNGIEC_JUMP_OK of
# rungiec_st.iec, checked by rungiec.iec).
# The conversion of an example that uses them must fail with only these diagnostics and write no XML.
NO_JUMP_TARGETS = ("omron", "keyence", "mitsubishi")
NO_JUMP_RE = re.compile(r"error: PP91: 'RUNGIEC (?:JMP to|LABEL) \w+ is not supported for the jiecc targets omron, "
                        r"keyence and mitsubishi'")
# LD mode (docs/DESIGN.md D43): -D RUNGIEC_LD selects the LD mode of rungiec.iec; -P and -nC keep the line markers and
# the comments of jiepp out of the ld body, where they would break the XML.
LD_JIECC_ARGS = ("-D", "RUNGIEC_LD", "-P", "-nC")
# The target whose LD XML is the expected file tests/expanded/<stem>.ld.xml and the input of the round trip.
LD_EXPECTED_TARGET = "omron"
# The number of LD bodies in the XML of an example for a target, when it is not 1: jiecc -t keyence appends an LD body
# with the END and ENDH rungs (append-epilogue-pou-body). Only the first LD body is compared (docs/DESIGN.md X42 and
# "テストの仕組み").
LD_BODY_COUNTS = {"keyence": 2}
# The environment variable with the path of the IEC 61131-10 XSD for the XSD check of the LD XML (lxml is needed).
XSD_ENV = "RUNGIEC_IEC10_XSD"
# The examples whose omron LD XML tools/xml2rungiec.py does not convert back to a full fragment, with the reason
# (measured with the LD XML of the examples; the reason codes are those of xml2rungiec.json). The runner fails when a
# listed example converts fully (remove it here) or an unlisted one does not.
LD_ROUNDTRIP_SKIP = {
    "bridge_circuit": "general-net: the bridge is not a series-parallel network, and xml2rungiec converts only "
                      "series-parallel rungs",
    "conditional_return": "jump-body: xml2rungiec does not convert the Return object and skips the body whole",
    "parts_counter": "general-net: xml2rungiec reads the InlineST of ST_EXEC as an element with the same net on "
                     "both sides and skips those rungs",
}
# The ST of the DSL part of a POU in the ST mode: from after the init statements that BEGIN_RUNGIEC() writes
# (_RUNGIEC_INIT of rungiec_st.iec, whitespace between the tokens free) to the first end_function_block / end_program
# / end_function. RungiecSp := 0; alone is not a mark (JMP writes it too); a body statement such as end_if does not
# end the part.
ST_INIT_RE = re.compile(r"\s*".join(re.escape(token) for token in re.findall(
    r"\w+|:=|;", "RungiecEn := true; RungiecIn := true; RungiecAcc := true; RungiecVal := true; RungiecTmp := false; "
                 "RungiecRail := true; RungiecSp := 0;")))
ST_POU_END_RE = re.compile(r"\bend_(?:function_block|program|function)\b", re.IGNORECASE)


def write_text(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


# The repository root as jiepp and jiecc print it in a diagnostic ('C:/.../rungiec/tests/e041_....iec:16.0: error: ...'):
# either separator, and on Windows any letter case. relative_paths() removes it so that the stored outputs and the
# @expect matching do not depend on where the repository is cloned.
ROOT_PREFIX_RE = re.compile(
    r"[/\\]".join(re.escape(part) for part in re.split(r"[/\\]", ROOT.rstrip("/\\"))) + r"[/\\]",
    flags=re.IGNORECASE if os.name == "nt" else 0,
)


def relative_paths(text):
    """Return text with every path under the repository root written relative to the root."""
    return ROOT_PREFIX_RE.sub("", text)


def run_cmd(args):
    proc = subprocess.run(
        args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=TIMEOUT_SEC
    )
    out = relative_paths(proc.stdout.decode("utf-8", errors="replace").replace("\r\n", "\n"))
    err = relative_paths(proc.stderr.decode("utf-8", errors="replace").replace("\r\n", "\n"))
    return proc.returncode, out, err


def resolve_exe(path):
    """Return an existing executable path (accepting a missing .exe suffix) or None."""
    if os.path.isfile(path):
        return path
    if os.path.isfile(path + ".exe"):
        return path + ".exe"
    return None


def parse_header(text):
    expect = re.search(r"\(\*\s*@expect\s+(\S+)(?:[ \t]+(.*?))?\s*\*\)", text)
    cycles = re.search(r"\(\*\s*@cycles\s+(\d+)\s*\*\)", text)
    kind = expect.group(1) if expect else None
    pattern = (expect.group(2) or "") if expect else ""
    return kind, pattern, int(cycles.group(1)) if cycles else 1


def strip_comments(text):
    return re.sub(r"\(\*.*?\*\)", "", text, flags=re.DOTALL)


def is_jiecunit_test(name):
    return name.startswith("t") or name.startswith("x_")


def check_jiecunit_source(text, name):
    """Static checks on a t/x test source. Return None or a failure reason string."""
    code = strip_comments(text)
    if not re.search(r"\b(?:EXPECT|ASSERT)_\w+\s*\(", code):
        return "no EXPECT_ / ASSERT_ assertion"
    if re.search(r"\bprogram\s+__main__\b", code, flags=re.IGNORECASE):
        return "'program __main__' is left (it would not run under the jiecunit configuration)"
    own = re.search(r"\b(EOT|SKIP_TEST)\s*\(", code)
    if own:
        return "hand-written %s( (the test ends in T_BEGIN() after the @cycles scans)" % own.group(1)
    tests = re.findall(r"\bTEST_M\s*\(\s*(\w+)\s*\)", code)
    if tests != [name]:
        return "TEST_M names %s (expected exactly [%s])" % (tests, name)
    lists = re.findall(r"\b_JIEC_TESTNAMES_LIST_\s*\(\s*([^()]*?)\s*\)", code)
    if lists != [name]:
        return "_JIEC_TESTNAMES_LIST_ entries %s (expected exactly [%s])" % (lists, name)
    begins = len(re.findall(r"\bT_BEGIN\s*\(\s*\)", code))
    if begins != 1:
        return "T_BEGIN() appears %d times (expected 1)" % begins
    return None


def example_includes(text, examples_dir):
    """Return the names of the files in examples_dir that text includes, in order."""
    return [n for n in INCLUDE_RE.findall(strip_comments(text)) if os.path.isfile(os.path.join(examples_dir, n))]


def check_example_source(text, stem):
    """Static checks on an example. Return None or a failure reason string."""
    code = strip_comments(text)
    blocks = re.findall(r"^[ \t]*function_block[ \t]+(\w+)", code, flags=re.IGNORECASE | re.MULTILINE)
    if blocks != [stem]:
        return ("function blocks %s (expected exactly [%s]: an example is one function block named after its "
                "file)" % (blocks, stem))
    programs = re.findall(r"^[ \t]*program[ \t]+(\w+)", code, flags=re.IGNORECASE | re.MULTILINE)
    if programs:
        return "program POUs %s (an example has no program)" % programs
    return None


def check_example_check(text, examples_dir):
    """Static checks on an x_ test: it includes at least one example, each included example passes
    check_example_source, and the test declares an instance of each example function block (without one the
    example is never called and the test checks nothing of it).
    Return None or a failure reason string."""
    code = strip_comments(text)
    examples = example_includes(text, examples_dir)
    if not examples:
        return "includes no example from examples/"
    for n in examples:
        stem = n[:-4]
        with open(os.path.join(examples_dir, n), encoding="utf-8") as f:
            reason = check_example_source(f.read(), stem)
        if reason:
            return "examples/%s: %s" % (n, reason)
        if not re.search(r"\b\w+\s*:\s*%s\s*;" % re.escape(stem), code, flags=re.IGNORECASE):
            return ("no instance of examples/%s (expected '<name>: %s;' in a var block of the test)" % (n, stem))
    return None


def generate_predefines(jiecc, path):
    """Write the macros that jiecc --run predefines (jiecc --run -E -dM on an empty file) to path.

    Return None or an error message."""
    empty = os.path.join(os.path.dirname(path), "empty.iec")
    write_text(empty, "")
    try:
        rc, out, err = run_cmd([jiecc, "--run", "-E", "-dM", empty, "--max-cycles", "1"])
    except subprocess.TimeoutExpired:
        return "timeout"
    if rc != 0 or "{#define _JIER " not in out:
        return "rc=%d: %s" % (rc, (err + out).strip()[:300])
    write_text(path, out)
    return None


def cleanup(out_dir, name):
    for ext in (".st", ".run.txt", ".pp.txt"):
        path = os.path.join(out_dir, name + ext)
        if os.path.exists(path):
            os.remove(path)


def run_one(path, name, jiepp, jiecc, out_dir, example=False, jiecunit=None, examples=None):
    """Run one test. Return None on success or a failure reason string.

    jiecunit is (jiecunit_dir, predefine_file) for t/x tests and None for e/f/r tests.
    examples is the examples directory and is used when example is true (x_ tests)."""
    with open(path, encoding="utf-8", newline="") as f:
        header_text = f.read()
    kind, pattern, cycles = parse_header(header_text)
    if kind is None:
        return "missing @expect header"
    if example and kind != "pass":
        return "an example check must be '@expect pass'"
    if (jiecunit is not None) != (kind == "pass"):
        return "'@expect pass' is for t*/x_* tests only, and a t*/x_* test must be '@expect pass'"
    if jiecunit is not None:
        # parse_header defaults a missing @cycles to 1 (kept for e/f/r); a t/x test must state it, and with N < 1
        # T_BEGIN() would end the test on the first scan before any assertion ran.
        if not re.search(r"\(\*\s*@cycles\s+\d+\s*\*\)", header_text):
            return "missing '(* @cycles N *)' header"
        if cycles < 1:
            return "'@cycles' must be >= 1 (got %d)" % cycles
        reason = check_jiecunit_source(header_text.replace("\r\n", "\n"), name)
        if reason:
            return reason
    if example:
        reason = check_example_check(header_text.replace("\r\n", "\n"), examples)
        if reason:
            return reason
    cleanup(out_dir, name)
    st_path = os.path.join(out_dir, name + ".st")
    pp_args = [jiepp, "-P", "-Werror=undef"]
    if jiecunit is not None:
        pp_args += ["-include", jiecunit[1]]
    pp_args += ["-I", ROOT, "-I", os.path.join(ROOT, "tests", "support"), "-I", os.path.join(ROOT, "examples")]
    if jiecunit is not None:
        pp_args += ["-I", jiecunit[0], "-I", os.path.join(jiecunit[0], "sys")]
    pp_args += ["-D", "T_CYCLES=%d" % cycles, path, "-o", st_path]
    rc, out, err = run_cmd(pp_args)
    if kind == "pp-error":
        write_text(os.path.join(out_dir, name + ".pp.txt"), err + out)
        if rc == 0:
            return "expected preprocessing error but jiepp succeeded"
        diag = [ln for ln in (err + out).split("\n") if DIAG_RE.search(ln)]
        if not any(re.search(pattern, ln) for ln in diag):
            return "jiepp diagnostics do not match /%s/: %s" % (pattern, " | ".join(diag)[:300])
        if os.path.exists(st_path):
            return "jiepp failed but left an output file"
        return None
    if rc != 0:
        return "jiepp failed (rc=%d): %s" % (rc, (err + out).strip()[:300])
    with open(st_path, encoding="utf-8", newline="") as f:
        st_text = f.read().replace("\r\n", "\n")
    if "{#" in st_text:
        return "expanded ST still contains '{#'"
    residual = RESIDUAL_RE.search(strip_comments(st_text)) or INTERNAL_ANY_RE.search(strip_comments(st_text))
    if kind == "residual":
        if not re.search(pattern, strip_comments(st_text)):
            return "expected residual /%s/ not found" % pattern
        return None
    if residual:
        return "unexpanded macro call remains: %s" % residual.group(0)
    if kind == "pass":
        # Scans 1..cycles run the test and scan cycles+1 ends it; +2 leaves one spare scan so that a test that
        # does not end is reported by the missing summary lines rather than cut at the last test scan.
        rc, out, err = run_cmd([jiecc, "--run", st_path, "--max-cycles", str(cycles + 2)])
    else:
        rc, out, err = run_cmd([jiecc, "--run", st_path, "--max-cycles", str(cycles), "--verbose"])
    text = out + err
    write_text(os.path.join(out_dir, name + ".run.txt"), text)
    if kind == "run-error":
        if not re.search(pattern, text):
            return "run output does not match /%s/" % pattern
        return None
    if kind != "pass":
        return "unknown @expect kind: %s" % kind
    # jiecunit appends the elapsed time to some lines ('[     OK ] t001 (0us)'); drop it before comparing.
    lines = [re.sub(r" \(\d+(?:\.\d+)?\s*[a-z]+\)$", "", ln.rstrip()) for ln in text.split("\n")]
    bad =[ln for ln in lines if ln.startswith(JU_BAD)]
    if bad:
        # The jiecunit failure detail: 'Expected ...', 'Actual ...', '<stem>:<line>@<cycle> <message>'.
        detail = [ln.strip() for ln in lines if re.match(r"\s*(Expected|Actual|Which is)\b", ln)
                  or re.match(r"\s*%s:\d+" % re.escape(name), ln)]
        return "jiecunit: %s" % " | ".join(bad + detail)[:600]
    for pat in FAIL_PATTERNS:
        if re.search(re.escape(pat), text, flags=re.IGNORECASE if pat == "error:" else 0):
            return "output contains '%s'" % pat
    ran = lines.count(JU_RAN)
    if ran != 1:
        return "'%s' appears %d times (expected 1)" % (JU_RAN, ran)
    if JU_PASSED not in lines:
        return "'%s' not found" % JU_PASSED
    if ("[     OK ] %s" % name) not in lines:
        return "'[     OK ] %s' not found" % name
    if rc != 0:
        return "jiecc rc=%d" % rc
    return None


def convert_example(jiecc, path, stem, target, xml_path):
    """Convert one example to IEC 61131-10 XML for target with the command a user types
    (jiecc examples/<stem>.iec -I . -t <target> -o <xml>). Return None or a failure reason string.
    For an example that uses JMP() or LABEL() and a target of NO_JUMP_TARGETS the conversion must fail with the
    PP91 of rungiec.iec (NO_JUMP_RE) and write no XML."""
    with open(path, encoding="utf-8") as f:
        code = strip_comments(f.read())
    uses_jmp = re.search(r"\bJMP\s*\(", code)
    try:
        rc, out, err = run_cmd([jiecc, path, "-I", ROOT, "-t", target, "-o", xml_path])
    except subprocess.TimeoutExpired:
        return "timeout"
    text = (err + out).strip()
    if target in NO_JUMP_TARGETS and (uses_jmp or re.search(r"\bLABEL\s*\(", code)):
        diag = [ln for ln in text.split("\n") if DIAG_RE.search(ln)]
        if rc == 0 or not diag or not all(NO_JUMP_RE.search(ln) for ln in diag):
            return ("the example uses JMP() or LABEL(), so -t %s must fail with only the PP91 'RUNGIEC JMP to ... is "
                    "not supported ...' (rc=%d): %s" % (target, rc, text[:300]))
        if os.path.isfile(xml_path):
            return "jiecc failed but wrote an XML"
        return None
    if rc != 0 or re.search(r"error|warning", text, flags=re.IGNORECASE):
        return "jiecc rc=%d: %s" % (rc, text[:300])
    if not os.path.isfile(xml_path):
        return "no XML written"
    with open(xml_path, encoding="utf-8") as f:
        xml = f.read()
    if not re.search(r"<FunctionBlock\s[^>]*\bname=\"%s\"" % re.escape(stem), xml):
        return "the XML has no <FunctionBlock name=\"%s\">" % stem
    if uses_jmp:
        want = JUMP_KEYWORDS.get(target, "goto")
        other = ({"goto"} | set(JUMP_KEYWORDS.values())) - {want}
        if not re.search(r"\b%s\s+\w+\s*;" % want, xml):
            return "the example uses JMP() but the XML has no '%s <label>;' for -t %s" % (want, target)
        for word in sorted(other):
            if re.search(r"\b%s\s+\w+\s*;" % word, xml):
                return "the XML has '%s <label>;' for -t %s (expected only %s)" % (word, target, want)
    return None


def uses_st_exec(path):
    with open(path, encoding="utf-8") as f:
        return re.search(r"\bST_EXEC\s*\(", strip_comments(f.read())) is not None


def xsd_path():
    """Return (path of the XSD, None) when the XSD check of the LD XML can run, else (None, reason)."""
    path = os.environ.get(XSD_ENV)
    if not path:
        return None, "%s is not set" % XSD_ENV
    if not os.path.isfile(path):
        return None, "%s=%s is not a file" % (XSD_ENV, path)
    try:
        import lxml.etree  # noqa: F401
    except ImportError:
        return None, "lxml is not installed"
    return path, None


def body_text(body):
    """The first LD body as xml.etree serializes it (without the text that follows the element)."""
    tail, body.tail = body.tail, None
    try:
        return ET.tostring(body, encoding="unicode")
    finally:
        body.tail = tail


def ld_bodies(xml_path):
    """Return the <BodyContent> elements of an XML file (ET.ParseError when it does not parse)."""
    return list(ET.parse(xml_path).getroot().iter("{%s}BodyContent" % IEC10_NS))


def first_difference(got, want):
    """Return a short description of where the text got first differs from the text want."""
    i = 0
    while i < min(len(got), len(want)) and got[i] == want[i]:
        i += 1
    return "at character %d: got %r, expected %r" % (i, got[max(0, i - 40):i + 60], want[max(0, i - 40):i + 60])


def convert_example_ld(jiecc, path, stem, target, xml_path, xsd):
    """Convert one example in the LD mode for target and check the items 1, 2, 3 and 5 of docs/TESTING.md
    "LD モードの変換の検査" (xsd is None when the XSD check does not run for this example).
    Return (None or a failure reason string, the first LD body as text or None)."""
    try:
        rc, out, err = run_cmd([jiecc, path, "-I", ROOT] + list(LD_JIECC_ARGS) + ["-t", target, "-o", xml_path])
    except subprocess.TimeoutExpired:
        return "timeout", None
    text = (err + out).strip()
    if rc != 0 or re.search(r"error|warning", text, flags=re.IGNORECASE):
        return "jiecc rc=%d: %s" % (rc, text[:300]), None
    if not os.path.isfile(xml_path):
        return "no XML written", None
    try:
        root = ET.parse(xml_path).getroot()
    except ET.ParseError as exc:
        return "the XML does not parse: %s" % exc, None
    bodies = list(root.iter("{%s}BodyContent" % IEC10_NS))
    types = [b.get(XSI_TYPE) for b in bodies]
    want = LD_BODY_COUNTS.get(target, 1)
    if types != ["LD"] * want:
        return "the XML has the bodies %s (expected %d LD and no other)" % (types, want), None
    if not any(fb.get("name") == stem for fb in root.iter("{%s}FunctionBlock" % IEC10_NS)):
        return "the XML has no <FunctionBlock name=\"%s\">" % stem, None
    with open(xml_path, encoding="utf-8") as f:
        if "RungiecAcc" in f.read():
            return "the XML has the ST of the work variables (RungiecAcc)", None
    if xsd:
        errors = ld_compare.validate_xsd(xml_path, xsd, limit=3)
        if errors and errors[0]:
            return "XSD: %d errors: %s" % (errors[0], " | ".join(errors[1])[:300]), None
    return None, body_text(bodies[0])


def expand_st_block(jiepp, src, out_path, predef):
    """Expand src in the ST mode (jiepp -P -nC -Werror=undef) and return (the ST of the DSL part (ST_INIT_RE to
    ST_POU_END_RE) without comments and with whitespace collapsed, None) or (None, a failure reason string)."""
    args = [jiepp, "-P", "-nC", "-Werror=undef"]
    if predef:
        args += ["-include", predef]
    rc, out, err = run_cmd(args + ["-I", ROOT, src, "-o", out_path])
    if rc != 0 or not os.path.isfile(out_path):
        return None, "jiepp %s rc=%d: %s" % (os.path.basename(src), rc, (err + out).strip()[:300])
    with open(out_path, encoding="utf-8") as f:
        text = re.sub(r"\(\*.*?\*\)", " ", f.read(), flags=re.DOTALL)
    name = os.path.basename(src)
    inits = list(ST_INIT_RE.finditer(text))
    if len(inits) != 1:
        return None, "the ST of %s has %d init statement sequences of BEGIN_RUNGIEC() (expected 1)" % (name, len(inits))
    end = ST_POU_END_RE.search(text, inits[0].end())
    if end is None:
        return None, "the ST of %s has no end_function_block, end_program or end_function after BEGIN_RUNGIEC()" % name
    part = re.sub(r"\s+", " ", text[inits[0].end():end.start()]).strip()
    # An empty part on both sides would compare equal; the DSL part always has the work variables.
    if "RungiecAcc" not in part:
        return None, "the ST of %s after BEGIN_RUNGIEC() has no RungiecAcc (empty or not a network)" % name
    return part, None


def roundtrip_fragment(xml_path, work):
    """Convert the LD XML back with tools/xml2rungiec.py into work. Return (fragment path or None, None) when the
    conversion is full (one fragment, every rung converted or an empty rung of a label) or (None, the reason)."""
    code, report, _ = xml2rungiec.convert(xml_path, work)
    frag_dir = os.path.join(work, "rungiec")
    frags = sorted(n for n in os.listdir(frag_dir) if n.endswith(".iec")) if os.path.isdir(frag_dir) else []
    lost = sorted({"rung %s: %s" % (it["ordinal"], it["reason"]) for it in report.get("items", [])
                   if it["status"] != "empty"})
    if code != 0 or len(frags) != 1 or lost:
        return None, "rc=%d, %d fragments, not converted: %s" % (code, len(frags), ", ".join(lost) or "none")
    return os.path.join(frag_dir, frags[0]), None


def roundtrip_compare(jiepp, path, frag, work, predef):
    """Expand the fragment (wrapped as tools/ld_xml_roundtrip.py does) and the example in the ST mode and compare
    their ST. Return None or a failure reason string."""
    with open(frag, encoding="utf-8") as f:
        wrapped = os.path.join(work, "fragment.iec")
        write_text(wrapped, ld_xml_roundtrip.wrap(f.read()))
    try:
        got, reason = expand_st_block(jiepp, wrapped, os.path.join(work, "fragment.st"), predef)
        if reason:
            return reason
        want, reason = expand_st_block(jiepp, path, os.path.join(work, "example.st"), predef)
    except subprocess.TimeoutExpired:
        return "timeout"
    if reason:
        return reason
    if got != want:
        return "the ST of the fragment differs from the ST of the example %s" % first_difference(got, want)
    return None


def check_ld_expected(stem, omron_xml, body, out_dir):
    """Compare the first LD body of the omron XML with the expected file tests/expanded/<stem>.ld.xml.
    On a difference write the omron XML to <stem>.ld.xml.new, else remove a stale one. Return None or a reason."""
    expected = os.path.join(out_dir, stem + ".ld.xml")
    new = expected + ".new"
    rel = os.path.relpath(expected, ROOT).replace(os.sep, "/")
    reason = None
    if not os.path.isfile(expected):
        reason = "no expected file %s" % rel
    else:
        try:
            bodies = [b for b in ld_bodies(expected) if b.get(XSI_TYPE) == "LD"]
        except ET.ParseError as exc:
            bodies, reason = [], "the expected file %s does not parse: %s" % (rel, exc)
        if not reason and not bodies:
            reason = "the expected file %s has no LD body" % rel
        elif not reason and body_text(bodies[0]) != body:
            reason = "the LD body differs from %s %s" % (rel, first_difference(body, body_text(bodies[0])))
    if reason:
        shutil.copyfile(omron_xml, new)
        return "%s; the omron LD XML is written to %s.new, review it and rename it" % (reason, rel)
    if os.path.exists(new):
        os.remove(new)
    return None


def convert_examples(jiecc, jiepp, examples_dir, tmp, out_dir, predef):
    """Convert every example for each of XML_TARGETS in the ST mode and in the LD mode, check the LD XML and its
    round trip, all in one pool of MAX_PARALLEL_JIECC workers. Print a FAIL line per failure and the summary
    lines, and return the number of failures."""
    stems = sorted(n[:-4] for n in os.listdir(examples_dir) if n.endswith(".iec"))
    paths = {s: os.path.join(examples_dir, s + ".iec") for s in stems}
    xsd, xsd_reason = xsd_path()
    st_exec = {s for s in stems if uses_st_exec(paths[s])}
    jobs = [(s, target, mode) for mode in ("st", "ld") for s in stems for target in XML_TARGETS]

    def ld_xml(stem, target):
        return os.path.join(tmp, "%s.%s.ld.xml" % (stem, target))

    def run_job(job):
        stem, target, mode = job
        if mode == "st":
            return convert_example(jiecc, paths[stem], stem, target, os.path.join(tmp, "%s.%s.xml" % (stem, target)))
        return convert_example_ld(jiecc, paths[stem], stem, target, ld_xml(stem, target),
                                  None if stem in st_exec else xsd)

    failed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(MAX_PARALLEL_JIECC, os.cpu_count() or 1)) as pool:
        results = dict(zip(jobs, pool.map(run_job, jobs)))
        st_failed = 0
        for stem in stems:
            for target in XML_TARGETS:
                reason = results[(stem, target, "st")]
                if reason is not None:
                    st_failed += 1
                    print("FAIL examples/%s.iec -t %s: %s" % (stem, target, reason))
        failed += st_failed
        print("XML: %d of %d example conversions passed (%s)" % (len(stems) * len(XML_TARGETS) - st_failed,
                                                                 len(stems) * len(XML_TARGETS), ", ".join(XML_TARGETS)))

        # LD XML: the per-file checks of the jobs, then the expected file and the body of omron.
        ld_failed = 0
        for stem in stems:
            reason, omron_body = results[(stem, LD_EXPECTED_TARGET, "ld")]
            for target in XML_TARGETS:
                reason, body = results[(stem, target, "ld")]
                if reason is None and target == LD_EXPECTED_TARGET:
                    reason = check_ld_expected(stem, ld_xml(stem, target), body, out_dir)
                elif reason is None and omron_body is not None and body != omron_body:
                    reason = "the LD body differs from the body of -t %s %s" % (
                        LD_EXPECTED_TARGET, first_difference(body, omron_body))
                elif reason is None and omron_body is None:
                    reason = "the LD body is not compared because the -t %s conversion failed" % LD_EXPECTED_TARGET
                if reason is not None:
                    ld_failed += 1
                    print("FAIL examples/%s.iec -t %s (LD): %s" % (stem, target, reason))
        failed += ld_failed
        print("LD XML: %d of %d example conversions passed (%s)" % (len(stems) * len(XML_TARGETS) - ld_failed,
                                                                    len(stems) * len(XML_TARGETS),
                                                                    ", ".join(XML_TARGETS)))
        if xsd:
            for stem in sorted(st_exec):
                print("XSD SKIP examples/%s.iec: it uses ST_EXEC (smcext:InlineST is not in the XSD)" % stem)
            print("XSD: the LD XML of %d examples checked with %s" % (len(stems) - len(st_exec), xsd))
        else:
            print("XSD NOT RUN (%s): the LD XML is not checked against the XSD" % xsd_reason)

        # Round trip of the omron LD XML. xml2rungiec runs here one example at a time; the jiepp runs go to the pool.
        rt_failed = rt_skipped = 0
        rt_jobs = []
        for stem in stems:
            work = os.path.join(tmp, "roundtrip", stem)
            os.makedirs(work, exist_ok=True)
            reason = None
            if results[(stem, LD_EXPECTED_TARGET, "ld")][0] is not None:
                reason = "the -t %s LD conversion failed" % LD_EXPECTED_TARGET
            else:
                frag, partial = roundtrip_fragment(ld_xml(stem, LD_EXPECTED_TARGET), work)
                if stem in LD_ROUNDTRIP_SKIP:
                    if frag is not None:
                        reason = "xml2rungiec converts it fully; remove it from LD_ROUNDTRIP_SKIP"
                    else:
                        rt_skipped += 1
                        continue
                elif frag is None:
                    reason = ("xml2rungiec does not convert it fully (%s); list it in LD_ROUNDTRIP_SKIP with the "
                              "reason or fix the conversion" % partial)
                else:
                    rt_jobs.append((stem, frag, work))
                    continue
            rt_failed += 1
            print("FAIL examples/%s.iec (LD ROUNDTRIP): %s" % (stem, reason))
        for (stem, _, _), reason in zip(rt_jobs, pool.map(
                lambda job: roundtrip_compare(jiepp, paths[job[0]], job[1], job[2], predef), rt_jobs)):
            if reason is not None:
                rt_failed += 1
                print("FAIL examples/%s.iec (LD ROUNDTRIP): %s" % (stem, reason))
        failed += rt_failed
        print("LD ROUNDTRIP: %d of %d examples passed, %d in LD_ROUNDTRIP_SKIP" % (
            len(stems) - rt_skipped - rt_failed, len(stems) - rt_skipped, rt_skipped))
    return failed


def run_all(names, tests_dir, jiepp, jiecc, out_dir, jiecunit, examples=None):
    """Run the tests in names, print PASS / FAIL lines and return (passed, failed)."""
    passed = failed = 0
    for name in names:
        try:
            reason = run_one(os.path.join(tests_dir, name + ".iec"), name, jiepp, jiecc, out_dir,
                             name.startswith("x_"), jiecunit if is_jiecunit_test(name) else None, examples)
        except subprocess.TimeoutExpired:
            reason = "timeout"
        if reason is None:
            passed += 1
            print("PASS %s" % name)
        else:
            failed += 1
            print("FAIL %s: %s" % (name, reason))
    return passed, failed


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--jiepp", default=os.environ.get("RUNGIEC_JIEPP", DEFAULT_JIEPP))
    parser.add_argument("--jiecc", default=os.environ.get("RUNGIEC_JIECC", DEFAULT_JIECC))
    parser.add_argument("--jiecunit", default=DEFAULT_JIECUNIT, help="jiecunit directory (default: vendor/jiecunit)")
    parser.add_argument("--tests-dir", default=os.path.join(ROOT, "tests"))
    parser.add_argument("--out-dir", default=os.path.join(ROOT, "tests", "expanded"))
    parser.add_argument("--examples-dir", default=os.path.join(ROOT, "examples"))
    parser.add_argument("--only", default=None)
    parser.add_argument("--no-lint", action="store_true", help="do not run the lint before the tests")
    parser.add_argument("--lint-only", action="store_true", help="run only the lint")
    parser.add_argument("--lint-selftest", action="store_true", help="run only the self-test of the lint")
    args = parser.parse_args()

    os.chdir(ROOT)
    tests_dir = os.path.abspath(args.tests_dir)
    out_dir = os.path.abspath(args.out_dir)
    examples_dir = os.path.abspath(args.examples_dir)

    if args.lint_selftest:
        return lint_rungiec.selftest()
    if args.lint_only:
        return lint_rungiec.run_lint(ROOT, tests_dir, examples_dir)

    jiepp = resolve_exe(args.jiepp)
    jiecc = resolve_exe(args.jiecc)
    print("jiepp: %s" % (jiepp or args.jiepp))
    print("jiecc: %s" % (jiecc or args.jiecc))
    if jiepp is None:
        print("ERROR: jiepp not found: %s" % args.jiepp)
        return 2
    if jiecc is None:
        print("ERROR: jiecc not found: %s" % args.jiecc)
        return 2
    jiepp = os.path.abspath(jiepp)
    jiecc = os.path.abspath(jiecc)

    # The lint looks at every file, so it is skipped with --only (like the XML conversions).
    lint_failed = False
    if not args.no_lint and not args.only:
        lint_failed = lint_rungiec.run_lint(ROOT, tests_dir, examples_dir) != 0

    names = sorted(n[:-4] for n in os.listdir(tests_dir) if n.endswith(".iec"))
    if args.only:
        names = [n for n in names if fnmatch.fnmatch(n, args.only)]
    if names:
        os.makedirs(out_dir, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="rungiec-tests-") as tmp:
        jiecunit = None
        if any(is_jiecunit_test(n) for n in names):
            jiecunit_dir = os.path.abspath(args.jiecunit)
            print("jiecunit: %s" % jiecunit_dir)
            if not os.path.isfile(os.path.join(jiecunit_dir, "jiecunit.iec")):
                print("ERROR: jiecunit not found: %s (run 'git submodule update --init' in the repository root)"
                      % os.path.join(jiecunit_dir, "jiecunit.iec"))
                return 2
            predef = os.path.join(tmp, "jiecc_predefines.iec")
            error = generate_predefines(jiecc, predef)
            if error:
                print("ERROR: 'jiecc --run -E -dM' failed to list the predefined macros: %s" % error)
                return 2
            jiecunit = (jiecunit_dir, predef)
        passed, failed = run_all(names, tests_dir, jiepp, jiecc, out_dir, jiecunit, examples_dir)
        if not args.only and os.path.isdir(examples_dir):
            checks = "".join(
                open(os.path.join(tests_dir, n), encoding="utf-8").read()
                for n in os.listdir(tests_dir) if n.startswith("x_") and n.endswith(".iec")
            )
            for n in sorted(os.listdir(examples_dir)):
                if n.endswith(".iec") and ("'%s'" % n) not in checks:
                    failed += 1
                    print("FAIL examples/%s: no tests/x_*.iec includes this example" % n)
            os.makedirs(out_dir, exist_ok=True)
            failed += convert_examples(jiecc, jiepp, examples_dir, tmp, out_dir, jiecunit[1] if jiecunit else None)
    print("RESULT: %d passed, %d failed, %d total" % (passed, failed, passed + failed))
    if lint_failed:
        print("RESULT: lint failed (see the LINT lines above)")
    return 1 if failed or lint_failed else 0


if __name__ == "__main__":
    sys.exit(main())
