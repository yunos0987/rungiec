"""LD XML <-> RUNGIEC converter (v1).

xml2rungiec: Omron Sysmac Studio (or standard IEC 61131-10) LD XML -> RUNGIEC fragments.
rungiec2xml: RUNGIEC fragments -> standard IEC 61131-10 LD XML.
Both directions share the rung graph and canonical form of model.py.
"""

import os
import sys

sys.dont_write_bytecode = True

TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)
