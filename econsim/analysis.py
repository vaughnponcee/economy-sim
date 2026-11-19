"""Monte Carlo aggregation and problem detectors."""
from __future__ import annotations

from .model import simulate_player


def _sim(cfg: dict, seed: int, i: int) -> list[dict]:
    return simulate_player(cfg, i, seed)


def percentile(xs: list[float], p: float) -> float:
    """Linear-interpolated percentile, p in [0, 100]."""
    if not xs:
        return 0.0
    s = sorted(xs)
    k = (len(s) - 1) * p / 100
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def run(cfg: dict, seed: int = 0, players: int | None = None, workers: int = 1) -> dict:
    """Players are independent and seeded by (seed, player_id), so results
    are identical for any worker count."""
    n = players or cfg["players"]
    if workers > 1:
        from concurrent.futures import ProcessPoolExecutor
        from functools import partial

        with ProcessPoolExecutor(workers) as ex:
            runs = list(ex.map(partial(_sim, cfg, seed), range(n), chunksize=max(1, n // (workers * 4))))
    else:
        runs = [simulate_player(cfg, i, seed) for i in range(n)]
    days = []
    for d in range(cfg["days"]):
        snap = [r[d] for r in runs]
        gold = [s["gold"] for s in snap]
        lvl = [s["level"] for s in snap]
        earned = sum(s["earned"] for s in snap)
        sinks = sum(s["spent_sinks"] + s["spent_upgrades"] + s["lost_softcap"] for s in snap)
        days.append({
            "day": d + 1,
            "gold_p10": percentile(gold, 10), "gold_p50": percentile(gold, 50),
            "gold_p90": percentile(gold, 90),
            "level_p10": percentile(lvl, 10), "level_p50": percentile(lvl, 50),
            "level_p90": percentile(lvl, 90),
            "upgrades_p50": percentile([s["upgrades"] for s in snap], 50),
            "sink_ratio": sinks / earned if earned else 0.0,
            "idle_pct": 100 * sum(s["idle_s"] for s in snap)
                        / max(1e-9, sum(s["idle_s"] + s["active_s"] for s in snap)),
        })
    action_time: dict[str, float] = {}
    for r in runs:
        for k, v in r[-1]["action_time"].items():
            action_time[k] = action_time.get(k, 0.0) + v
    total = sum(action_time.values()) or 1.0
    result = {"players": n, "seed": seed, "days": days,
              "action_share": {k: v / total for k, v in sorted(action_time.items())}}
    result["issues"] = detect(cfg, result)
    return result


def detect(cfg: dict, res: dict) -> list[dict]:
    """Heuristics; thresholds are configurable under cfg['detectors']."""
    th = {"inflation_growth": 0.25, "sink_floor": 0.6, "stall_days": 3,
          "dominant_share": 0.8, "idle_pct": 30.0, "spread": 10.0}
    th.update(cfg.get("detectors", {}))
    issues = []
    days = res["days"]

    # Inflation: median wallet keeps compounding late in the run while sinks
    # remove too little of what is earned.
    tail = days[len(days) // 2:]
    growth = [b["gold_p50"] / a["gold_p50"] - 1 for a, b in zip(tail, tail[1:]) if a["gold_p50"] > 1]
    if growth:
        g = sum(growth) / len(growth)
        if g > th["inflation_growth"] and tail[-1]["sink_ratio"] < th["sink_floor"]:
            issues.append({"type": "inflation", "severity": "high",
                           "detail": f"median wallet grows {g:.0%}/day in 2nd half; sinks absorb "
                                     f"only {tail[-1]['sink_ratio']:.0%} of income"})

    # Progression stall: median level flat for N+ consecutive days before the end.
    run_len = best = 0
    start = None
    for a, b in zip(days, days[1:]):
        if b["level_p50"] <= a["level_p50"]:
            run_len += 1
            if run_len > best:
                best, start = run_len, b["day"] - run_len
        else:
            run_len = 0
    if best >= th["stall_days"]:
        issues.append({"type": "progression_stall", "severity": "medium",
                       "detail": f"median level flat for {best} days starting day {start}"})

    # Exploit / dominant strategy: one action eats nearly all play time.
    for name, share in res["action_share"].items():
        if share >= th["dominant_share"] and len(cfg["actions"]) > 1:
            issues.append({"type": "dominant_action", "severity": "high",
                           "detail": f"'{name}' takes {share:.0%} of active time; other actions are dead content"})

    # Exploit loop: an upgrade whose payback is shorter than its own action,
    # i.e. it pays for itself before you could even use the gold elsewhere.
    for u in cfg["upgrades"]:
        targets = [a for a in cfg["actions"] if u["target"] in ("*", a["name"])]
        for a in targets:
            per_s = (a["reward"][0] + a["reward"][1]) / 2 * u["bonus"] / a["duration_s"]
            if per_s > 0 and u["growth"] <= 1.0 + 1e-9:
                issues.append({"type": "exploit_loop", "severity": "high",
                               "detail": f"'{u['name']}' has flat cost and no max level: "
                                         f"income scales without bound"})
                break
            if per_s > 0 and u["base_cost"] / per_s < a["duration_s"]:
                issues.append({"type": "exploit_loop", "severity": "high",
                               "detail": f"'{u['name']}' pays back in {u['base_cost'] / per_s:.1f}s "
                                         f"(< one '{a['name']}')"})
                break

    if days[-1]["idle_pct"] > th["idle_pct"]:
        issues.append({"type": "cooldown_starvation", "severity": "medium",
                       "detail": f"players idle {days[-1]['idle_pct']:.0f}% of session time "
                                 "waiting on cooldowns"})

    last = days[-1]
    if last["gold_p10"] > 0 and last["gold_p90"] / last["gold_p10"] > th["spread"]:
        issues.append({"type": "wealth_spread", "severity": "low",
                       "detail": f"p90/p10 wallet ratio {last['gold_p90'] / last['gold_p10']:.1f}x "
                                 "on final day (luck dominates)"})
    return issues
