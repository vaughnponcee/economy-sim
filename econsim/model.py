"""Event-driven simulation of one player over many sessions.

Time advances action-by-action (not in fixed ticks), so cooldowns and
durations are exact and a 30-day run of 1,000 players stays fast.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field


@dataclass
class PlayerState:
    gold: float = 0.0
    xp: float = 0.0
    level: int = 1
    upgrades: dict = field(default_factory=dict)  # name -> level
    earned: float = 0.0
    spent_upgrades: float = 0.0
    spent_sinks: float = 0.0
    lost_softcap: float = 0.0
    active_s: float = 0.0
    idle_s: float = 0.0
    action_time: dict = field(default_factory=dict)  # name -> seconds
    # caches, refreshed only when an upgrade is bought (hot-path speedup)
    mult: dict = field(default_factory=dict)          # action -> multiplier
    min_cost: float = 0.0                             # cheapest available upgrade


def refresh_caches(cfg: dict, st: PlayerState) -> None:
    st.mult = {a["name"]: multiplier(cfg, st, a["name"]) for a in cfg["actions"]}
    costs = [upgrade_cost(u, st.upgrades.get(u["name"], 0)) for u in cfg["upgrades"]
             if st.upgrades.get(u["name"], 0) < u.get("max_level", 10**9)]
    st.min_cost = min(costs) if costs else math.inf


def upgrade_cost(u: dict, level: int) -> float:
    """Geometric cost curve: base * growth^level."""
    return u["base_cost"] * u["growth"] ** level


def level_threshold(cfg: dict, level: int) -> float:
    """XP needed to go from `level` to `level+1` (geometric)."""
    lv = cfg.get("leveling", {"base_xp": 100, "growth": 1.25})
    return lv["base_xp"] * lv["growth"] ** (level - 1)


def multiplier(cfg: dict, st: PlayerState, action: str) -> float:
    m = 1.0
    for u in cfg["upgrades"]:
        lvl = st.upgrades.get(u["name"], 0)
        if lvl and (u["target"] == "*" or u["target"] == action):
            m += u["bonus"] * lvl  # additive stacking within the multiplier
    return m


def soft_capped(cfg: dict, gold: float, gain: float) -> tuple[float, float]:
    """Income above the soft cap is scaled by `softcap.efficiency`; the hard
    cap clamps the wallet. Returns (new_gold, gold_lost_to_caps)."""
    cap = cfg.get("caps", {})
    soft, eff = cap.get("soft"), cap.get("efficiency", 0.5)
    hard = cap.get("hard")
    new = gold + gain
    if soft is not None and new > soft:
        below = max(0.0, soft - gold)
        new = gold + below + (gain - below) * eff
    if hard is not None and new > hard:
        new = hard
    return new, gold + gain - new


def expected_value(cfg: dict, st: PlayerState, a: dict) -> float:
    lo, hi = a["reward"]
    m = st.mult.get(a["name"])
    return (lo + hi) / 2 * (m if m is not None else multiplier(cfg, st, a["name"]))


def choose_action(cfg: dict, st: PlayerState, ready: dict, now: float):
    """Greedy policy: best expected gold per second of the action's own
    duration, among actions that are unlocked and off cooldown."""
    best, best_rate = None, -1.0
    for a in cfg["actions"]:
        if st.level < a.get("unlock_level", 1) or ready.get(a["name"], 0.0) > now:
            continue
        rate = expected_value(cfg, st, a) / a["duration_s"]
        if rate > best_rate:
            best, best_rate = a, rate
    return best


def buy_upgrades(cfg: dict, st: PlayerState) -> int:
    """Buy the affordable upgrade with the shortest payback, repeatedly.
    Payback = cost / (extra gold per second the upgrade adds to the action
    the player currently values most). Returns the number bought."""
    bought = 0
    reserve = cfg.get("policy", {}).get("reserve", 0.0)
    # A flat-cost, uncapped upgrade would otherwise be bought until gold runs
    # out, which can be millions of purchases. Cap per decision point and let
    # the detector report the loop.
    limit = cfg.get("policy", {}).get("max_buys_per_step", 50)
    if st.gold - reserve < st.min_cost:  # nothing affordable: the common case
        return 0
    while bought < limit:
        best, best_pb = None, math.inf
        for u in cfg["upgrades"]:
            lvl = st.upgrades.get(u["name"], 0)
            if lvl >= u.get("max_level", 10**9):
                continue
            cost = upgrade_cost(u, lvl)
            if cost > st.gold - reserve:
                continue
            gain = 0.0
            for a in cfg["actions"]:
                if st.level >= a.get("unlock_level", 1) and (u["target"] in ("*", a["name"])):
                    lo, hi = a["reward"]
                    gain = max(gain, (lo + hi) / 2 * u["bonus"] / a["duration_s"])
            if gain <= 0:
                continue
            pb = cost / gain
            if pb < best_pb:
                best, best_pb = u, pb
        if best is None:
            break
        lvl = st.upgrades.get(best["name"], 0)
        cost = upgrade_cost(best, lvl)
        n = 1
        if best["growth"] == 1.0:  # flat cost: buy every affordable level at once
            room = best.get("max_level", 10**9) - lvl
            n = max(1, min(room, int((st.gold - reserve) // cost)))
        st.gold -= cost * n
        st.spent_upgrades += cost * n
        st.upgrades[best["name"]] = lvl + n
        bought += n
    if bought:
        refresh_caches(cfg, st)
    return bought


def apply_sinks(cfg: dict, st: PlayerState, elapsed_s: float) -> None:
    for s in cfg.get("sinks", []):
        if s["kind"] == "upkeep":  # flat cost per hour of play, scaled by level
            cost = s["per_hour"] * (1 + s.get("per_level", 0) * (st.level - 1)) * elapsed_s / 3600
        elif s["kind"] == "tax":  # % of wallet per hour (wealth tax / decay)
            cost = st.gold * s["pct_per_hour"] / 100 * elapsed_s / 3600
        else:
            raise ValueError(f"unknown sink kind {s['kind']}")
        cost = min(cost, st.gold)
        st.gold -= cost
        st.spent_sinks += cost


def run_session(cfg: dict, st: PlayerState, rng: random.Random, length_s: float) -> None:
    now = 0.0
    ready: dict[str, float] = {}
    sink_clock = 0.0
    while now < length_s:
        a = choose_action(cfg, st, ready, now)
        if a is None:  # everything on cooldown: idle until the next one is ready
            nxt = min(ready.values())
            wait = min(nxt, length_s) - now
            st.idle_s += wait
            now += wait
            continue
        if now + a["duration_s"] > length_s:
            break  # not enough session left to finish it
        lo, hi = a["reward"]
        gain = rng.uniform(lo, hi) * st.mult[a["name"]]
        if a.get("crit_chance") and rng.random() < a["crit_chance"]:
            gain *= a.get("crit_mult", 2.0)
        st.gold, lost = soft_capped(cfg, st.gold, gain)
        st.lost_softcap += lost
        st.earned += gain
        st.xp += a.get("xp", 0)
        while st.xp >= level_threshold(cfg, st.level):
            st.xp -= level_threshold(cfg, st.level)
            st.level += 1
        now += a["duration_s"]
        st.active_s += a["duration_s"]
        st.action_time[a["name"]] = st.action_time.get(a["name"], 0.0) + a["duration_s"]
        ready[a["name"]] = now + a.get("cooldown_s", 0.0)
        if now - sink_clock >= 60:
            apply_sinks(cfg, st, now - sink_clock)
            sink_clock = now
        buy_upgrades(cfg, st)
    if now > sink_clock:
        apply_sinks(cfg, st, now - sink_clock)


def simulate_player(cfg: dict, player_id: int, seed: int) -> list[dict]:
    """Returns one snapshot per day. Deterministic for (cfg, player_id, seed)."""
    rng = random.Random(f"{seed}:{player_id}")
    sess = cfg.get("session", {"min_s": 600, "max_s": 1800, "per_day": [1, 3]})
    st = PlayerState()
    refresh_caches(cfg, st)
    days = []
    for _day in range(cfg["days"]):
        for _ in range(rng.randint(*sess.get("per_day", [1, 3]))):
            run_session(cfg, st, rng, rng.uniform(sess["min_s"], sess["max_s"]))
        days.append({
            "gold": st.gold, "level": st.level, "earned": st.earned,
            "spent_upgrades": st.spent_upgrades, "spent_sinks": st.spent_sinks,
            "lost_softcap": st.lost_softcap, "upgrades": sum(st.upgrades.values()),
            "active_s": st.active_s, "idle_s": st.idle_s,
            "action_time": dict(st.action_time),
        })
    return days
