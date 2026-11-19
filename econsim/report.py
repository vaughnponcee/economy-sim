"""Plain-text tables and ASCII charts."""
from __future__ import annotations

BARS = " ▁▂▃▄▅▆▇█"


def fmt_num(x: float) -> str:
    for unit, div in (("B", 1e9), ("M", 1e6), ("k", 1e3)):
        if abs(x) >= div:
            return f"{x / div:.1f}{unit}"
    return f"{x:.0f}" if abs(x) >= 10 else f"{x:.2f}"


def table(headers: list[str], rows: list[list], align: str | None = None) -> str:
    cells = [[str(h) for h in headers]] + [[str(c) for c in r] for r in rows]
    widths = [max(len(r[i]) for r in cells) for i in range(len(headers))]
    align = align or "l" + "r" * (len(headers) - 1)

    def line(r):
        return "  ".join(c.ljust(w) if a == "l" else c.rjust(w)
                         for c, w, a in zip(r, widths, align))

    sep = "  ".join("-" * w for w in widths)
    return "\n".join([line(cells[0]), sep] + [line(r) for r in cells[1:]])


def sparkline(values: list[float]) -> str:
    if not values:
        return ""
    lo, hi = min(values), max(values)
    span = hi - lo or 1.0
    return "".join(BARS[1 + int((v - lo) / span * (len(BARS) - 2))] for v in values)


def line_chart(series: dict[str, list[float]], height: int = 10, width: int | None = None) -> str:
    """Multi-series ASCII chart; each series drawn with its own glyph."""
    glyphs = "*o+x#"
    allv = [v for s in series.values() for v in s]
    if not allv:
        return ""
    lo, hi = min(allv), max(allv)
    span = hi - lo or 1.0
    n = max(len(s) for s in series.values())
    width = width or n
    grid = [[" "] * width for _ in range(height)]
    for gi, s in enumerate(series.values()):
        for i, v in enumerate(s):
            x = int(i * (width - 1) / max(1, n - 1))
            y = height - 1 - int((v - lo) / span * (height - 1))
            grid[y][x] = glyphs[gi % len(glyphs)]
    labels = [fmt_num(hi - span * r / (height - 1)) for r in range(height)]
    lw = max(len(lbl) for lbl in labels)
    rows = [f"{labels[r].rjust(lw)} |{''.join(grid[r])}" for r in range(height)]
    rows.append(" " * lw + " +" + "-" * width)
    rows.append(" " * lw + "  " + "day 1".ljust(width - 6) + f"day {n}")
    legend = "  ".join(f"{glyphs[i % len(glyphs)]} {k}" for i, k in enumerate(series))
    return "\n".join(rows + [" " * lw + "  " + legend])


def render(result: dict, every: int | None = None) -> str:
    days = result["days"]
    every = every or max(1, len(days) // 10)
    picked = [d for d in days if (d["day"] - 1) % every == 0 or d["day"] == len(days)]
    rows = [[d["day"], fmt_num(d["gold_p10"]), fmt_num(d["gold_p50"]), fmt_num(d["gold_p90"]),
             f"{d['level_p50']:.0f}", f"{d['upgrades_p50']:.0f}", f"{d['sink_ratio']:.0%}",
             f"{d['idle_pct']:.0f}%"] for d in picked]
    out = [f"players={result['players']} seed={result['seed']} days={len(days)}", "",
           table(["day", "gold p10", "gold p50", "gold p90", "lvl p50", "upg p50", "sinks", "idle"], rows),
           "",
           line_chart({"gold p50": [d["gold_p50"] for d in days],
                       "gold p90": [d["gold_p90"] for d in days]}, width=min(60, max(20, len(days) * 2))),
           "",
           "level p50  " + sparkline([d["level_p50"] for d in days]),
           "", "action share of active time:"]
    out += [f"  {k:16} {v:6.1%} {'#' * int(v * 40)}" for k, v in result["action_share"].items()]
    out += ["", "issues:"]
    if result["issues"]:
        out += [f"  [{i['severity'].upper():6}] {i['type']}: {i['detail']}" for i in result["issues"]]
    else:
        out.append("  none detected")
    return "\n".join(out)


def render_sweep(path: str, rows: list[tuple]) -> str:
    """rows: (value, result)."""
    body = []
    for value, r in rows:
        last = r["days"][-1]
        body.append([value, fmt_num(last["gold_p50"]), f"{last['level_p50']:.0f}",
                     f"{last['sink_ratio']:.0%}", f"{last['idle_pct']:.0f}%",
                     sparkline([d["gold_p50"] for d in r["days"]]),
                     ", ".join(sorted({i["type"] for i in r["issues"]})) or "-"])
    return f"sweep: {path}\n\n" + table(
        ["value", "gold p50", "lvl p50", "sinks", "idle", "gold trend", "issues"], body,
        align="rrrrrll")
