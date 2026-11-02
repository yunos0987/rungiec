"""Rung model shared by both directions: elements, the rung graph, its series-parallel (SP) tree and the
canonical form used by the round-trip check.

A rung graph has one node per power net (S = left rail, T = right rail) and one edge per powered element.
SP reduction merges parallel edges into P and in-1/out-1 nodes into S; it succeeds when one edge S -> T is left.
Tree: ("L", Element) | ("S", [tree...]) | ("P", [tree...]).
"""

import json

from . import st_text

CONTACT_KINDS = ("NO", "NC", "P", "N", "NP", "NN")
CONTACT_MACROS = {
    "NO": "CONTACT", "NC": "NOT_CONTACT", "P": "RISE_CONTACT", "N": "FALL_CONTACT",
    "NP": "NOT_RISE_CONTACT", "NN": "NOT_FALL_CONTACT",
}
COIL_MACROS = {
    "COIL": "COIL", "NOT": "NOT_COIL", "SET": "SET_COIL", "RESET": "RESET_COIL",
    "RISE": "RISE_COIL", "FALL": "FALL_COIL",
}
CMP_MACROS = {"EQ": "EQ_CONTACT", "NE": "NE_CONTACT", "GT": "GT_CONTACT", "GE": "GE_CONTACT",
              "LT": "LT_CONTACT", "LE": "LE_CONTACT"}
CMP_OPERATORS = {"EQ": "=", "NE": "<>", "GT": ">", "GE": ">=", "LT": "<", "LE": "<="}
EDGE_CONTACTS = ("P", "N", "NP", "NN")
EDGE_COILS = ("RISE", "FALL")


class Arg(object):
    """A call argument: pin := value (input or in-out) or pin => value (output)."""
    __slots__ = ("pin", "direction", "value", "order")

    def __init__(self, pin, direction, value, order=0):
        self.pin, self.direction, self.value, self.order = pin, direction, value, order

    def text(self):
        return "%s %s %s" % (self.pin, self.direction, self.value)

    def canon(self):
        return "%s%s%s" % (st_text.canon(self.pin), self.direction, st_text.canon(self.value))


class Element(object):
    """A leaf of the rung.

    kind: contact (sub = CONTACT_KINDS), cmp (sub = CMP_MACROS key, ops = [a, b]), callcontact (name = function),
    coil (sub = COIL_MACROS key), fb_in (name = instance, fbtype, pin, power_out: FB_IN / FBT_IN without, FB_BOX /
    FBT_BOX with the output pin that carries the power on), fb_eno, fn_eno (dst),
    fb_scan, st_exec (text), mc (mcno), mcr (mcno), jump (name = target label; no power output).
    """
    __slots__ = ("kind", "sub", "name", "ops", "args", "pin", "dst", "power_out", "fbtype", "text", "mcno",
                 "order")

    def __init__(self, kind, order=0, **kw):
        self.kind = kind
        self.order = order
        self.sub = kw.get("sub")
        self.name = kw.get("name")
        self.ops = kw.get("ops") or []
        self.args = kw.get("args") or []
        self.pin = kw.get("pin")
        self.dst = kw.get("dst")
        self.power_out = kw.get("power_out")
        self.fbtype = kw.get("fbtype")
        self.text = kw.get("text")
        self.mcno = kw.get("mcno")

    def is_coil(self):
        return self.kind == "coil"

    def is_edge(self):
        return (self.kind == "contact" and self.sub in EDGE_CONTACTS) or (self.kind == "coil" and self.sub in EDGE_COILS)

    def signature(self):
        c = st_text.canon
        args = sorted(a.canon() for a in self.args)
        k = self.kind
        if k in ("contact", "coil"):
            return [k, self.sub, c(self.name)]
        if k == "cmp":
            return [k, self.sub, c(self.ops[0]), c(self.ops[1])]
        if k == "callcontact":
            return [k, c(self.name), args]
        if k == "fb_in":
            return [k, c(self.name), c(self.fbtype), c(self.pin), args, c(self.power_out or "")]
        if k in ("fb_eno", "fb_scan"):
            return [k, c(self.name), c(self.fbtype), args]
        if k == "fn_eno":
            return [k, c(self.name), args, c(self.dst or "")]
        if k == "st_exec":
            return [k, c(self.text)]
        if k in ("mc", "mcr"):
            return [k, c(self.mcno or "")]
        if k == "jump":
            return [k, self.name]
        raise ValueError("unknown element kind %s" % k)


def leaf(e):
    return ("L", e)


def min_order(tree):
    if tree[0] == "L":
        return tree[1].order
    return min(min_order(t) for t in tree[1])


def flatten(tree):
    """Flatten S in S and P in P, unwrap single-child S/P. P children ordered by document order (stable)."""
    if tree[0] == "L":
        return tree
    kind = tree[0]
    kids = []
    for t in tree[1]:
        t = flatten(t)
        if t[0] == kind:
            kids.extend(t[1])
        else:
            kids.append(t)
    if len(kids) == 1:
        return kids[0]
    if kind == "P":
        kids = sorted(kids, key=min_order)
    return (kind, kids)


class GeneralNet(Exception):
    pass


def sp_reduce(edges, s, t):
    """edges: [(u, v, tree)]. Return the SP tree of the edge S -> T, or raise GeneralNet."""
    edges = [list(e) for e in edges]
    if not edges:
        raise GeneralNet("no powered element")
    for u, v, _ in edges:
        if u == v:
            raise GeneralNet("element with the same net on both sides")
    changed = True
    while changed:
        changed = False
        # (a) parallel edges
        groups = {}
        for e in edges:
            groups.setdefault((e[0], e[1]), []).append(e)
        if any(len(g) > 1 for g in groups.values()):
            merged = []
            seen = set()
            for e in edges:
                key = (e[0], e[1])
                if key in seen:
                    continue
                seen.add(key)
                g = groups[key]
                if len(g) == 1:
                    merged.append(g[0])
                else:
                    merged.append([key[0], key[1], ("P", [x[2] for x in g])])
            edges = merged
            changed = True
        # (b) series nodes
        indeg, outdeg = {}, {}
        for u, v, _ in edges:
            outdeg[u] = outdeg.get(u, 0) + 1
            indeg[v] = indeg.get(v, 0) + 1
        for node in list(indeg):
            if node in (s, t) or indeg.get(node) != 1 or outdeg.get(node) != 1:
                continue
            e_in = next(e for e in edges if e[1] == node)
            e_out = next(e for e in edges if e[0] == node)
            if e_in is e_out:
                continue
            edges = [e for e in edges if e is not e_in and e is not e_out]
            edges.append([e_in[0], e_out[1], ("S", [e_in[2], e_out[2]])])
            changed = True
            break
    if len(edges) != 1 or edges[0][0] != s or edges[0][1] != t:
        raise GeneralNet("not a series-parallel network (%d edges left)" % len(edges))
    return flatten(edges[0][2])


def canon_tree(tree):
    """Nested lists for json: leaves are signatures, P children sorted by their canonical string."""
    if tree is None:
        return None
    tree = flatten(tree)
    if tree[0] == "L":
        return tree[1].signature()
    kids = [canon_tree(k) for k in tree[1]]
    if tree[0] == "P":
        kids = sorted(kids, key=lambda k: json.dumps(k, sort_keys=True))
    return [tree[0], kids]


def canonical(tree, unpowered, comments, label=None):
    """The canonical string of a rung: SP tree (jump targets included), multiset of unpowered elements, comment,
    rung label (exact text, None when the rung has none)."""
    unp = sorted(json.dumps(e.signature()) for e in unpowered)
    return json.dumps({"tree": canon_tree(tree), "unpowered": unp, "comment": st_text.canon_comment(comments),
                       "label": label or None}, sort_keys=True, ensure_ascii=False)


def leaves(tree):
    if tree is None:
        return []
    if tree[0] == "L":
        return [tree[1]]
    out = []
    for t in tree[1]:
        out.extend(leaves(t))
    return out


class UnionFind(object):
    def __init__(self):
        self.parent = {}

    def find(self, x):
        parent = self.parent
        parent.setdefault(x, x)
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            # keep the special nodes as roots
            if rb in ("S", "T"):
                ra, rb = rb, ra
            self.parent[rb] = ra
        return ra
