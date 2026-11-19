"""econsim CLI.

  python -m econsim run   configs/idle_miner.json [--players N] [--seed S] [--json out.json]
  python -m econsim sweep configs/idle_miner.json --param upgrades.sharper_pick.growth \
                          --values 1.05 1.15 1.3 1.6
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from . import analysis, config, report


def _num(s: str):
    try:
        return int(s)
    except ValueError:
        try:
            return float(s)
        except ValueError:
            return s


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="econsim")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "sweep"):
        p = sub.add_parser(name)
        p.add_argument("config")
        p.add_argument("--players", type=int)
        p.add_argument("--days", type=int)
        p.add_argument("--seed", type=int, default=0)
        p.add_argument("--json", help="write full results as JSON")
        p.add_argument("-j", "--workers", type=int, default=1, help="processes (results identical)")
        if name == "sweep":
            p.add_argument("--param", required=True, help="dotted path, e.g. upgrades.0.growth")
            p.add_argument("--values", nargs="+", required=True)
    a = ap.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # sparklines on Windows consoles
    cfg = config.load(a.config)
    if a.days:
        cfg["days"] = a.days
    t0 = time.perf_counter()
    if a.cmd == "run":
        res = analysis.run(cfg, a.seed, a.players, a.workers)
        print(report.render(res))
        payload = res
    else:
        rows = []
        for v in a.values:
            variant = config.set_path(cfg, a.param, _num(v))
            rows.append((v, analysis.run(variant, a.seed, a.players, a.workers)))
        print(report.render_sweep(a.param, rows))
        payload = {"param": a.param, "runs": [{"value": v, **r} for v, r in rows]}
    print(f"\n({time.perf_counter() - t0:.2f}s)", file=sys.stderr)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
