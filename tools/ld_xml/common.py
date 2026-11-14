"""Constants and small helpers shared by the LD XML converter, its command line tools and the test runner."""

import re

NS = "www.iec.ch/public/TC65SC65BWG7TF10"  # the IEC 61131-10 XML namespace
MC_MAX = 15  # RUNGIEC_MC_MAX default, also the MC nesting depth of Omron (MCNo 0..14)


def safe_name(text):
    """text with every character other than A-Z, a-z, 0-9 and _ replaced by _ ('_' for an empty text)."""
    return re.sub(r"[^A-Za-z0-9_]", "_", text or "") or "_"


def write_text(path, text):
    """Write text to path in UTF-8 with LF line ends."""
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
