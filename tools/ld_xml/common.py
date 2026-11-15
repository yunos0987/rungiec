"""Constants and small helpers shared by the LD XML converter, its command line tools and the test runner."""

import os
import re

NS = "www.iec.ch/public/TC65SC65BWG7TF10"  # the IEC 61131-10 XML namespace
MC_MAX = 15  # RUNGIEC_MC_MAX default, also the MC nesting depth of Omron (MCNo 0..14)
# The IEC 61131-10 XSD for the XSD checks: the environment variable XSD_ENV names it; without the variable,
# tools/<XSD_NAME> is used when it exists (README.md "ツールの入手": downloaded by each user, not tracked by Git).
XSD_ENV = "RUNGIEC_IEC10_XSD"
XSD_NAME = "IEC61131_10_Ed1_0.xsd"
XSD_DEFAULT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), XSD_NAME)


def find_xsd():
    """(path of the XSD, None), or (None, reason) when there is none. Whether lxml is installed is not checked."""
    path = os.environ.get(XSD_ENV)
    if path:
        if os.path.isfile(path):
            return path, None
        return None, "%s=%s is not a file" % (XSD_ENV, path)
    if os.path.isfile(XSD_DEFAULT):
        return XSD_DEFAULT, None
    return None, "%s is not set and tools/%s does not exist" % (XSD_ENV, XSD_NAME)


def safe_name(text):
    """text with every character other than A-Z, a-z, 0-9 and _ replaced by _ ('_' for an empty text)."""
    return re.sub(r"[^A-Za-z0-9_]", "_", text or "") or "_"


def write_text(path, text):
    """Write text to path in UTF-8 with LF line ends."""
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
