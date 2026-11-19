# econsim

A deterministic Monte Carlo simulator for game progression and economy loops. You describe actions, cooldowns, upgrade cost curves, currency sinks and soft/hard caps in JSON. econsim plays thousands of sessions with a greedy player and reports where the economy breaks:

- inflation
- progression stalls
- dominant strategies
- exploit loops
- cooldown starvation
- luck-driven wealth spread

It uses only the Python 3.10+ standard library. Results are identical for a given `(config, seed)`, including across worker counts.

```bash
python -m econsim run   configs/idle_miner.json -j 8
python -m econsim sweep configs/idle_miner.json --param upgrades.sharper_pick.growth \
                        --values 1.05 1.15 1.25 1.35 1.6 2.0 -j 8
python -m econsim run   configs/exploit_loop.json --json out.json
```

## Example output

`python -m econsim run configs/broken_inflation.json --players 100` runs the healthy config with its sinks and caps removed and its upgrade cost growth flattened:

```
357.5k |                        o
312.9k |                      o    *
268.2k |                    o   *
223.6k |                  o   *
179.0k |                o
134.4k |                  * *
 89.8k |              o *
 45.2k |            o *
   637 |o o o o o o *
       +----------------------------
        day 1                 day 14
        * gold p50  o gold p90

issues:
  [HIGH  ] inflation: median wallet grows 38%/day in 2nd half; sinks absorb only 43% of income
```

A sweep over the pick upgrade's cost growth (200 players × 14 days per row):

```
value  gold p50  lvl p50  sinks  idle  gold trend      issues
-----  --------  -------  -----  ----  --------------  ------------------------------
 1.05     48.1k       16    91%    0%  ▁▁▁▂▂▂▃▃▄▄▅▆▇█  dominant_action
 1.15     24.4k       16    94%    0%  ▁▁▁▁▂▂▂▃▄▅▆▆█▇  dominant_action
 1.25     13.9k       15    95%    0%  ▁▁▁▂▂▃▃▄▄▅▆▆▇█  dominant_action, wealth_spread
 1.35     12.2k       15    95%    0%  ▁▁▁▂▂▃▃▄▅▅▆▆█▇  -
  1.6     13.0k       15    94%    0%  ▁▁▁▂▂▃▃▃▅▅▄▆▆█  -
  2.0     10.3k       15    94%    0%  ▁▁▂▂▂▃▄▄▄▅▆▇█▇  wealth_spread
```

Below a growth of 1.35, tapping becomes so efficient that expeditions and the deep shaft turn into dead content. Above 1.6 nothing improves, and luck starts to dominate final wealth.

A sweep that taught us something: changing the wealth tax from 0% to 8% per hour barely moves the median wallet. The greedy player spends about 95% of income on upgrades before the tax can reach it. On this config, a percentage-of-wallet sink does nothing to an optimizer.

## Model

**Time is event-driven.** A session has a random length. The player repeatedly picks an action, and the clock advances by that action's `duration_s`. Cooldowns are exact timestamps. If every unlocked action is on cooldown, the player idles until the soonest one is ready, and that time counts as idle. Sessions per day and session length are drawn uniformly from `session.per_day` and `[min_s, max_s]`.

**Rewards.** `gold = U(lo, hi) × multiplier`, times `crit_mult` with probability `crit_chance`. The multiplier is `1 + Σ bonus × level` over the upgrades targeting that action (or `*`), so bonuses stack additively.

**Costs.** Upgrade level `n` costs `base_cost × growth^n`. With `growth` g and `bonus` b, the marginal cost per unit of extra income grows like gⁿ, while income grows linearly in n. Any g > 1 therefore eventually stalls an upgrade line. g = 1 never stalls it, and that is the exploit.

**Leveling.** XP from level L to L+1 is `base_xp × growth^(L-1)`. `unlock_level` gates actions.

**Sinks**, applied every 60 s of play and at session end:

- `upkeep`: `per_hour × (1 + per_level × (L-1))`, prorated by time;
- `tax`: `pct_per_hour` of the current wallet, prorated by time.

**Caps.** Income that pushes the wallet past `caps.soft` is scaled by `caps.efficiency`, and the wallet is clamped at `caps.hard`. The lost gold is tracked, so `gold == earned − upgrades − sinks − cap losses` holds exactly (this is tested).

**Player policy (greedy).**
- Actions: pick the unlocked, ready action with the highest expected gold per second of its own duration.
- Upgrades: after each action, buy the affordable upgrade with the shortest payback (`cost / added gold-per-second`), and repeat.
- Each decision buys at most 50 upgrade steps. Flat-cost upgrades are bought in bulk, since otherwise an uncapped flat upgrade hangs the simulation (it did, once).

**Aggregation.** For each day: wallet and level p10/p50/p90, median upgrade count, sink ratio `(upgrades + sinks + cap loss) / earned`, and idle %. Action share is computed over all players' active time.

### Detectors

Thresholds can be overridden in `"detectors": {...}`.

| issue | rule (default) |
|---|---|
| `inflation` | mean daily median-wallet growth in the 2nd half > 25% **and** final sink ratio < 60% |
| `progression_stall` | median level flat for ≥ 3 consecutive days |
| `dominant_action` | one action ≥ 80% of active time (when there are ≥ 2 actions) |
| `exploit_loop` | upgrade with `growth == 1` (unbounded scaling), or payback shorter than one use of its action |
| `cooldown_starvation` | players idle > 30% of session time |
| `wealth_spread` | final p90/p10 wallet > 10× |

### Assumptions you should know about

- **One archetype.** Everyone plays the same greedy optimizer. Real players are worse at optimizing, which makes inflation and dominance *less* severe than reported here, and stalls *more* severe. Add policies in `model.choose_action` and `model.buy_upgrades`.
- **Greedy is not optimal.** Shortest-payback buying is a good heuristic, but it is not a solved strategy. A config can look fine here and still be breakable by a determined player.
- **Single currency.** There is no premium currency, no trading and no player-to-player economy. Inflation here means wallet growth, not price levels.
- **Rewards are uniform random.** No streaks, pity timers or time-of-day effects.

## Performance

Measured on Windows 11, Python 3.14, 12 logical cores.

| run | players × days | sessions | time |
|---|---|---:|---:|
| `idle_miner` serial | 500 × 14 | ~14,000 | 26.4 s |
| `idle_miner` `-j 8` | 500 × 14 | ~14,000 | 9.3 s |
| `exploit_loop` serial | 300 × 10 | ~4,500 | 9.9 s |
| 6-value sweep `-j 8` | 200 × 14 × 6 | ~33,600 | 32.9 s |

The hot path caches multipliers and the cheapest upgrade cost, and refreshes them only when something is bought. That made it about 2× faster than the first version.

## Layout

```
econsim/   config.py (validate, dotted-path overrides) · model.py · analysis.py · report.py · cli.py
configs/   idle_miner.json (healthy) · broken_inflation.json · exploit_loop.json
tests/     20 unittest cases: determinism, parallel == serial, money conservation,
           cooldown timing, caps, every detector, CLI
```

```bash
python -m unittest discover -s tests -t .
```

## License

MIT
