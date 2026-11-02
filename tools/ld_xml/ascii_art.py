"""ASCII-art ladder diagram of a RUNGIEC rung (the diagram rules of AGENTS.md, section "図の書き方").

The art is written as comment lines right before the RUNG(...) / BEGIN_MC(...) / END_MC() it shows, each line
'(*  <art>  *)', so that a rung of the XML can be compared with its diagram. rungiec_reader skips these lines
(is_art), the round trip does not see them.

A block is a rectangle of rows; row 0 is the power wire that enters at the left edge. The block records which rows
carry a wire out of the right edge (wires) and whether it is open (a coil at the end of the rung: nothing merges, the
rows end at the right rail by themselves). Layout:

    series      blocks joined by '--' on row 0
    parallel    '+--' branch '--+', the branches stacked, narrower ones padded with '-' to the widest, a row with only
                the '|' connectors between two branches; an open parallel (all branches end in a coil, last in the
                rung) has no merge: every coil row ends at the right rail
    rung        '|--' ... '--|' (the right rail on every row); a rung that ends in JMP / RETURN has no right rail

Comment safety: every text (operand, ST text) is made one line, '(*' becomes '( *', '*)' becomes '* )' and '{' becomes
'{ ' (jiecc reads '{st}', '{end}' and '{#' even inside a comment), the same as st_text.comment_text.

Columns are counted in display width (unicodedata.east_asian_width W / F = 2 columns) so that the '|' and '+' of the
rows stay under each other when an operand has full-width characters. Operand names are copied from the XML as they
are, so this is the one exception to the ASCII-only rule for diagrams (names written in Japanese are kept).

Symbol masks: a contact is drawn '| A |', so its bars look like the wire connector '|'. Every Block therefore carries
a mask per row (the row with each character of a symbol replaced by 'x'), built by the same operations as the row
itself, and alignment_problems reads the connectors from the masks, not from the text.
"""

import re
import unicodedata

from . import model

COMMENT_OPEN = "(*  "
COMMENT_CLOSE = "  *)"

CONTACT_FORMS = {"NO": " %s", "NC": "/%s", "P": "P %s", "N": "N %s", "NP": "/P %s", "NN": "/N %s"}
COIL_FORMS = {"COIL": " %s", "NOT": "/ %s", "SET": "S %s", "RESET": "R %s", "RISE": "P %s", "FALL": "N %s"}


def is_art(comment_inner):
    """True for the inner text of an art comment: two spaces after '(*' and two before '*)'. The other comments the
    converter writes are stripped (st_text.comment_text), so they never start with a space."""
    return comment_inner.startswith("  ") and comment_inner.endswith("  ")


def safe(text):
    """One line, no comment delimiters, no directive braces (see the module comment)."""
    t = re.sub(r"\s+", " ", text or "").strip()
    return t.replace("(*", "( *").replace("*)", "* )").replace("{", "{ ")


def width(s):
    """Display width of s in columns: full-width and wide characters take 2."""
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in s)


def pad(s, w, fill):
    return s + fill * (w - width(s))


def _args(args):
    return ", ".join("%s %s %s" % (safe(a.pin), a.direction, safe(a.value)) for a in args)


def _call(name, args):
    return "%s(%s)" % (safe(name), _args(args)) if args else safe(name)


def _fb_name(e):
    """The instance of a function block box, with ':type' when it has a type (FBT_*)."""
    return "%s:%s" % (e.name, e.fbtype) if e.fbtype else e.name


def symbol(e):
    """The one-row symbol of an element (AGENTS.md)."""
    k = e.kind
    if k == "contact":
        return "|%s |" % (CONTACT_FORMS[e.sub] % safe(e.name))
    if k == "cmp":
        return "| %s %s %s |" % (safe(e.ops[0]), model.CMP_OPERATORS[e.sub], safe(e.ops[1]))
    if k == "callcontact":
        return "| %s |" % _call(e.name, e.args)
    if k == "coil":
        return "(%s )" % (COIL_FORMS[e.sub] % safe(e.name))
    if k == "mc":
        return "(MC)"
    if k == "mcr":
        return "(MCR)"
    if k == "fb_scan":
        return "[ %s ]" % _call(_fb_name(e), e.args)
    if k == "fb_in":
        # the arguments go in '( )' after the name; the pin that gets the power follows the call, then the output
        # pin that carries the power on (FB_BOX / FBT_BOX)
        if e.power_out:
            return "[ %s %s %s ]" % (_call(_fb_name(e), e.args), safe(e.pin), safe(e.power_out))
        return "[ %s %s ]" % (_call(_fb_name(e), e.args), safe(e.pin))
    if k == "fb_eno":
        return "[ %s ENO ]" % _call(_fb_name(e), e.args)
    if k == "fn_eno":
        if e.dst:
            return "[ %s := %s ENO ]" % (safe(e.dst), _call(e.name, e.args))
        return "[ %s ENO ]" % _call(e.name, e.args)
    if k == "st_exec":
        return "[[ ST: %s ]]" % safe(e.text)
    if k == "jump":
        return ">>%s" % safe(e.name)
    if k == "return":
        return "<RETURN>"
    raise ValueError("no symbol for element kind %s" % k)


class Block(object):
    """rows: equal display width; masks: the rows with each character of a symbol replaced by 'x' (one character
    per character of the row); wires: rows with a wire out of the right edge; open: ends at the right rail
    (a coil at the end of the rung); rail: False for the JMP / RETURN symbol (no right rail)."""
    __slots__ = ("rows", "masks", "width", "wires", "open", "rail")

    def __init__(self, rows, masks, wires=(0,), open_=False, rail=True):
        self.rows = rows
        self.masks = masks
        self.width = width(rows[0])
        self.wires = frozenset(wires)
        self.open = open_
        self.rail = rail


def ends_open(tree):
    """True when every path of the tree ends in a coil (so it can stack on the right rail when it is last)."""
    if tree[0] == "L":
        return tree[1].is_coil() or tree[1].kind in ("mc", "mcr")
    if tree[0] == "S":
        return ends_open(tree[1][-1])
    return all(ends_open(t) for t in tree[1])


def _leaf(e, last):
    s = symbol(e)
    return Block([s], ["x" * len(s)], open_=last and (e.is_coil() or e.kind in ("mc", "mcr")), rail=e.kind not in ("jump", "return"))


def _series(blocks):
    height = max(len(b.rows) for b in blocks)
    rows = [""] * height
    masks = [""] * height
    for i, b in enumerate(blocks):
        gap = 2 if i else 0
        for r in range(height):
            if r == 0:
                rows[0] += "-" * gap + b.rows[0]
                masks[0] += "-" * gap + b.masks[0]
            elif r >= len(b.rows):
                rows[r] += " " * (b.width + gap)
                masks[r] += " " * (b.width + gap)
            else:
                rows[r] += " " * gap + b.rows[r]
                masks[r] += " " * gap + b.masks[r]
    last = blocks[-1]
    return Block(rows, masks, last.wires, last.open, last.rail)


def _parallel(branches):
    w = max(b.width for b in branches)
    is_open = all(b.open for b in branches)
    rows = []
    masks = []
    wires = set()
    n = len(branches)
    for i, b in enumerate(branches):
        more = i < n - 1
        for r, row in enumerate(b.rows):
            fill = "-" if r in b.wires else " "
            body = pad(row, w, fill)
            mbody = b.masks[r] + fill * (w - b.width)
            if r == 0:
                head = "+--"
            else:
                head = ("|" if more else " ") + "  "
            if is_open:
                tail = ""
                if r in b.wires:
                    wires.add(len(rows))
            else:
                tail = ("--" if r == 0 else "  ") + ("+" if r == 0 else ("|" if more else " "))
            rows.append(head + body + tail)
            masks.append(head + mbody + tail)
        if more:
            gap_row = "|" + " " * (w + 2) + ("" if is_open else " " * 2 + "|")
            rows.append(gap_row)
            masks.append(gap_row)
    if is_open:
        return Block(rows, masks, wires, True)
    return Block(rows, masks)


def _block(tree, last):
    if tree[0] == "L":
        return _leaf(tree[1], last)
    kids = tree[1]
    if tree[0] == "S":
        n = len(kids)
        return _series([_block(k, last and i == n - 1) for i, k in enumerate(kids)])
    return _parallel([_block(k, last and ends_open(tree)) for k in kids])


def rung_drawing(kids):
    """The rows of one rung drawn from the series children kids (trees; the same tree as the macro), without the
    comment delimiters, and their symbol masks (see the module comment). Return (lines, masks)."""
    block = _block(("S", list(kids)), True)
    lines = []
    masks = []
    for r in range(len(block.rows)):
        left = "|--" if r == 0 else "|  "
        right = ("--|" if r in block.wires else "  |") if block.rail else ""
        lines.append(left + block.rows[r] + right)
        masks.append(left + block.masks[r] + right)
    if not block.rail:
        # the rows are cut at the end; a mask has the same length as its line, so it is cut at the same place
        for r in range(len(lines)):
            n = len(lines[r].rstrip())
            lines[r] = lines[r][:n]
            masks[r] = masks[r][:n]
    return lines, masks


def rung_lines(kids):
    return rung_drawing(kids)[0]


def comment_lines(lines):
    """Wrap rows into the art comment lines. A last guard keeps any '(*' / '*)' that the pieces could form."""
    return [COMMENT_OPEN + l.replace("(*", "( *").replace("*)", "* )") + COMMENT_CLOSE for l in lines]


def rung_art(kids):
    return comment_lines(rung_lines(kids))


def label_art(label):
    return comment_lines(["%s:" % safe(label)])


def alignment_problems(lines, masks, rail=True):
    """Check the connectors of the rows of one rung (without comment delimiters) by column: every row starts with
    the left rail '|', with a right rail the rows end in '|' and have the same display width, a '+' touches a '|' or
    '+' above or below it, and a '|' has one above and one below. masks are the symbol masks of the lines (from
    rung_drawing): a '|' or '+' inside a symbol (a contact bar, an operand, an ST text) is not a connector.
    Return the list of problems (empty when fine)."""
    rows = masks
    probs = []
    w = width(lines[0]) if lines else 0
    for r, row in enumerate(rows):
        if not row.startswith("|"):
            probs.append("row %d does not start with the left rail" % r)
        if rail:
            if width(lines[r]) != w:
                probs.append("row %d has width %d, not %d" % (r, width(lines[r]), w))
            if not row.endswith("|"):
                probs.append("row %d does not end with the right rail" % r)
    # the display columns of the masked rows (a masked row has one 'x' per character, so its columns are the
    # character indexes; the width of a full-width character is added when the original row is longer)
    for r, row in enumerate(rows):
        cols = {}
        c = 0
        for i, ch in enumerate(lines[r]):
            cols[c] = i
            c += width(ch)
        for col, i in cols.items():
            ch = row[i]
            if ch not in "+|" or col == 0 or (rail and col == w - 1):
                continue
            up = rows[r - 1][_at(lines[r - 1], col)] if r and _at(lines[r - 1], col) is not None else " "
            down = rows[r + 1][_at(lines[r + 1], col)] if r + 1 < len(rows) and \
                _at(lines[r + 1], col) is not None else " "
            if ch == "+" and up not in "+|" and down not in "+|":
                probs.append("row %d column %d: '+' without a vertical neighbour" % (r, col))
            if ch == "|" and (up not in "+|" or down not in "+|"):
                probs.append("row %d column %d: '|' is not between '+' or '|'" % (r, col))
    return probs


def _at(line, col):
    """The character index of display column col in line (None when the line is shorter)."""
    c = 0
    for i, ch in enumerate(line):
        if c == col:
            return i
        c += width(ch)
    return None
