"""Render a compiled part as a drawing sheet for the visual critic.

Layout like the input drawings, all orthographic views at one scale:
    FRONT (x right, z up)   | SIDE (y right, z up)
    TOP   (x right, y up)   | ISO (shaded)
Visible edges solid, hidden edges dashed (OCC hidden-line removal). Labels: overall sizes, feature ids, hole
diameters and slot sizes, so the critic can name the feature it means.
"""
import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.collections import LineCollection, PolyCollection  # noqa: E402
from OCP.BRepAdaptor import BRepAdaptor_Curve  # noqa: E402
from OCP.BRepLib import BRepLib  # noqa: E402
from OCP.GCPnts import GCPnts_QuasiUniformDeflection  # noqa: E402
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt  # noqa: E402
from OCP.HLRAlgo import HLRAlgo_Projector  # noqa: E402
from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape  # noqa: E402
from OCP.TopAbs import TopAbs_EDGE  # noqa: E402
from OCP.TopExp import TopExp_Explorer  # noqa: E402
from OCP.TopoDS import TopoDS  # noqa: E402

# view -> (direction towards the viewer, image-right direction); image-up = towards x right
VIEWS = {"front": ((0, -1, 0), (1, 0, 0)), "top": ((0, 0, 1), (1, 0, 0)), "side": ((1, 0, 0), (0, 1, 0)),
         "iso": ((1, -1, 1), (1, 1, 0))}
AXES_2D = {"front": (0, 2), "top": (0, 1), "side": (1, 2)}
INK, HIDDEN, LABEL, DIM = "#1b2a41", "#8a94a6", "#b3261e", "#2f5d8a"


def _frame(view):
    n, x = (np.array(v, float) for v in VIEWS[view])
    n, x = n / np.linalg.norm(n), x / np.linalg.norm(x)
    return n, x, np.cross(n, x)


def _segments(compound):
    """Polylines (lists of 2D points) of every edge in an HLR result compound."""
    out = []
    if compound.IsNull():
        return out
    BRepLib.BuildCurves3d_s(compound, 1e-6)
    exp = TopExp_Explorer(compound, TopAbs_EDGE)
    while exp.More():
        curve = BRepAdaptor_Curve(TopoDS.Edge_s(exp.Current()))
        pts = GCPnts_QuasiUniformDeflection(curve, 0.02, curve.FirstParameter(), curve.LastParameter())
        if pts.IsDone() and pts.NbPoints() > 1:
            out.append([(pts.Value(i).X(), pts.Value(i).Y()) for i in range(1, pts.NbPoints() + 1)])
        exp.Next()
    return out


def hlr_lines(solid, view):
    """(visible, hidden) polylines of the solid projected in a view, in the view's 2D frame (mm)."""
    n, x, _ = _frame(view)
    algo = HLRBRep_Algo()
    algo.Add(solid.wrapped)
    algo.Projector(HLRAlgo_Projector(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(*n), gp_Dir(*x))))
    algo.Update()
    algo.Hide()
    shapes = HLRBRep_HLRToShape(algo)
    visible = _segments(shapes.VCompound()) + _segments(shapes.OutLineVCompound()) + \
        _segments(shapes.Rg1LineVCompound())
    hidden = _segments(shapes.HCompound()) + _segments(shapes.OutLineHCompound())
    return visible, hidden


def _fmt(v):
    return f"{v:.3g}" if abs(v - round(v)) > 1e-6 else str(int(round(v)))


def _dim(ax, p0, p1, text, offset, horizontal):
    """A dimension line between p0 and p1 (2D), shifted by `offset` perpendicular to it."""
    if horizontal:
        y = p0[1] + offset
        ax.annotate("", (p0[0], y), (p1[0], y), arrowprops=dict(arrowstyle="<->", color=DIM, lw=0.8))
        ax.text((p0[0] + p1[0]) / 2, y, text, color=DIM, fontsize=8, ha="center",
                va="top" if offset < 0 else "bottom", bbox=dict(fc="white", ec="none", pad=0.5))
    else:
        x = p0[0] + offset
        ax.annotate("", (x, p0[1]), (x, p1[1]), arrowprops=dict(arrowstyle="<->", color=DIM, lw=0.8))
        ax.text(x, (p0[1] + p1[1]) / 2, text, color=DIM, fontsize=8, rotation=90, va="center",
                ha="right" if offset < 0 else "left", bbox=dict(fc="white", ec="none", pad=0.5))


def _labels(result, view):
    """[(u, v, text, mark)] for features seen end-on in this view."""
    i, j = AXES_2D[view]
    normal = np.zeros(3)
    normal[3 - i - j] = 1
    out = []
    sources = {g["source"] for g in result.geometry.values() if "source" in g}
    for fid, g in result.geometry.items():
        t = g["type"]
        if fid in sources:
            continue                     # labelled by its pattern
        axis = np.asarray(g.get("axis") or [0, 0, 0], float)
        end_on = abs(abs(axis @ normal) - 1) < 1e-6
        if t in ("hole", "linear_pattern", "circular_pattern") and end_on and ("diameter" in g or "length" in g):
            insts = g["instances"] if t == "hole" else result.geometry[g["source"]]["instances"] + g["instances"]
            n = len(insts)
            text = f"{fid}  " + (f"{n}x " if t != "hole" else "") + f"Ø{_fmt(g.get('diameter', 0))}"
            if "length" in g:
                text = f"{fid}  " + (f"{n}x " if t != "hole" else "") + f"slot {_fmt(g['length'])}x{_fmt(g['width'])}"
            c = insts[0]["center"]
            out.append((c[i], c[j], text, [(p["center"][i], p["center"][j]) for p in insts]))
        elif t == "slot" and end_on and "length" in g:
            c = g["instances"][0]["center"]
            out.append((c[i], c[j], f"{fid}  slot {_fmt(g['length'])}x{_fmt(g['width'])}", [(c[i], c[j])]))
        elif t in ("extrude", "revolve") and g.get("operation") == "add" and view == "front":
            lo, hi = np.array(g["bbox"][0]), np.array(g["bbox"][1])
            c = (lo + hi) / 2
            out.append((c[i], c[j], fid, []))
        elif t == "extrude" and "diameter" in g and end_on:
            c = g["instances"][0]["center"]
            out.append((c[i], c[j], f"{fid}  Ø{_fmt(g['diameter'])}", [(c[i], c[j])]))
    return out


def _draw_ortho(ax, result, view, lo, hi):
    visible, hidden = hlr_lines(result.solid, view)
    ax.add_collection(LineCollection(hidden, colors=HIDDEN, linewidths=0.7, linestyles=(0, (3, 2))))
    ax.add_collection(LineCollection(visible, colors=INK, linewidths=1.2))
    i, j = AXES_2D[view]
    size = hi - lo
    pad = 0.18 * max(size[i], size[j]) + 6
    ax.set_xlim(lo[i] - pad, hi[i] + pad)
    ax.set_ylim(lo[j] - pad, hi[j] + pad)
    _dim(ax, (lo[i], lo[j]), (hi[i], lo[j]), _fmt(size[i]), -0.45 * pad, True)
    _dim(ax, (lo[i], lo[j]), (lo[i], hi[j]), _fmt(size[j]), -0.45 * pad, False)
    for k, (u, v, text, marks) in enumerate(_labels(result, view)):
        for mu, mv in marks:
            ax.plot([mu], [mv], marker="+", color=LABEL, ms=6, mew=0.8)
        if marks:
            ax.annotate(text, (u, v), (u + 0.08 * pad + 2, v + (0.25 + 0.12 * (k % 3)) * pad), color=LABEL, fontsize=7,
                        arrowprops=dict(arrowstyle="-", color=LABEL, lw=0.6), bbox=dict(fc="white", ec="none", pad=0.3))
        else:
            ax.text(u, v, text, color=LABEL, fontsize=7, ha="center", va="center", alpha=0.8)
    ax.set_title(view.upper(), fontsize=10, color=INK, fontweight="bold", y=-0.02, va="top")


def _draw_iso(ax, result):
    n, x, y = _frame("iso")
    verts, tris = result.solid.tessellate(0.05, 0.2)
    v = np.array([p.toTuple() for p in verts])
    t = np.array(tris)
    p3 = v[t]
    normals = np.cross(p3[:, 1] - p3[:, 0], p3[:, 2] - p3[:, 0])
    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)
    facing = normals @ n > 1e-6
    light = np.array([0.4, -0.5, 0.75])
    light /= np.linalg.norm(light)
    shade = 0.45 + 0.5 * np.clip(normals @ light, 0, 1)
    depth = (p3 @ n).mean(axis=1)
    order = np.argsort(depth)
    order = order[facing[order]]
    polys = [np.c_[p3[k] @ x, p3[k] @ y] for k in order]
    colors = [(0.55 * s, 0.65 * s, 0.78 * s) for s in shade[order]]
    ax.add_collection(PolyCollection(polys, facecolors=colors, edgecolors=colors, linewidths=0.3))
    visible, _ = hlr_lines(result.solid, "iso")
    ax.add_collection(LineCollection(visible, colors=INK, linewidths=0.6))
    allp = np.concatenate([np.c_[v @ x, v @ y]])
    lo2, hi2 = allp.min(axis=0), allp.max(axis=0)
    pad = 0.08 * (hi2 - lo2).max()
    ax.set_xlim(lo2[0] - pad, hi2[0] + pad)
    ax.set_ylim(lo2[1] - pad, hi2[1] + pad)
    ax.set_title("ISO", fontsize=10, color=INK, fontweight="bold", y=-0.02, va="top")


def render_sheet(result, title=None, dpi=110):
    """PNG bytes of the drawing sheet for a compiled Result."""
    lo, hi = (np.array(result.metrics["bbox"][k]) for k in ("min", "max"))
    sx, sy, sz = np.maximum(hi - lo, 1.0)
    fig = plt.figure(figsize=(10, 8.5), dpi=dpi)
    gs = fig.add_gridspec(2, 2, width_ratios=[sx * 1.4 + 12, sy * 1.4 + 12], height_ratios=[sz * 1.4 + 12, sy * 1.4 + 12],
                          wspace=0.05, hspace=0.12)
    for view, cell in (("front", gs[0, 0]), ("side", gs[0, 1]), ("top", gs[1, 0])):
        ax = fig.add_subplot(cell)
        _draw_ortho(ax, result, view, lo, hi)
        ax.set_aspect("equal")
        ax.axis("off")
    ax = fig.add_subplot(gs[1, 1])
    _draw_iso(ax, result)
    ax.set_aspect("equal")
    ax.axis("off")
    fig.suptitle(title or f"{result.spec.name}: rendered from the spec (all sizes in mm)", fontsize=11, color=INK)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor="white")
    plt.close(fig)
    return buf.getvalue()
