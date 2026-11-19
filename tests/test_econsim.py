import json
import random
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from econsim import analysis, config, report
from econsim.cli import main
from econsim.model import PlayerState, refresh_caches, run_session, simulate_player, soft_capped, upgrade_cost

CONFIGS = Path(__file__).resolve().parent.parent / "configs"


def tiny(**over) -> dict:
    cfg = {
        "players": 20, "days": 6,
        "session": {"min_s": 300, "max_s": 600, "per_day": [1, 2]},
        "actions": [{"name": "tap", "duration_s": 2, "reward": [1, 3], "xp": 1},
                    {"name": "raid", "duration_s": 30, "cooldown_s": 120, "reward": [40, 60], "xp": 10}],
        "upgrades": [{"name": "pick", "target": "tap", "base_cost": 20, "growth": 1.4, "bonus": 0.2,
                      "max_level": 20}],
        "sinks": [], "caps": {},
    }
    cfg.update(over)
    config.validate(cfg)
    return cfg


class ConfigTests(unittest.TestCase):
    def test_example_configs_valid(self):
        for p in CONFIGS.glob("*.json"):
            config.load(p)

    def test_rejects_bad(self):
        cfg = tiny()
        with self.assertRaises(config.ConfigError):
            config.set_path(cfg, "upgrades.0.growth", 0.9)
        with self.assertRaises(config.ConfigError):
            config.set_path(cfg, "upgrades.0.target", "nope")
        with self.assertRaises(config.ConfigError):
            config.validate({"actions": []})

    def test_set_path_by_index_and_name(self):
        cfg = tiny()
        a = config.set_path(cfg, "upgrades.pick.bonus", 0.5)
        b = config.set_path(cfg, "upgrades.0.bonus", 0.5)
        self.assertEqual(a, b)
        self.assertEqual(cfg["upgrades"][0]["bonus"], 0.2)  # original untouched


class MathTests(unittest.TestCase):
    def test_geometric_cost(self):
        u = {"base_cost": 100, "growth": 1.5}
        self.assertEqual(upgrade_cost(u, 0), 100)
        self.assertAlmostEqual(upgrade_cost(u, 3), 337.5)

    def test_soft_and_hard_cap(self):
        cfg = {"caps": {"soft": 100, "efficiency": 0.5, "hard": 150}}
        self.assertEqual(soft_capped(cfg, 50, 40), (90, 0))
        new, lost = soft_capped(cfg, 90, 40)  # 10 at full rate, 30 at half
        self.assertAlmostEqual(new, 115)
        self.assertAlmostEqual(lost, 15)
        new, lost = soft_capped(cfg, 140, 100)
        self.assertEqual(new, 150)

    def test_percentile(self):
        xs = list(range(1, 101))
        self.assertAlmostEqual(analysis.percentile(xs, 50), 50.5)
        self.assertEqual(analysis.percentile(xs, 0), 1)
        self.assertEqual(analysis.percentile(xs, 100), 100)
        self.assertEqual(analysis.percentile([], 50), 0.0)


class SimTests(unittest.TestCase):
    def test_deterministic(self):
        cfg = tiny()
        self.assertEqual(simulate_player(cfg, 3, 42), simulate_player(cfg, 3, 42))
        self.assertNotEqual(simulate_player(cfg, 3, 42), simulate_player(cfg, 4, 42))

    def test_parallel_matches_serial(self):
        cfg = tiny()
        self.assertEqual(analysis.run(cfg, 1, 8, workers=1), analysis.run(cfg, 1, 8, workers=2))

    def test_money_conservation(self):
        cfg = tiny(sinks=[{"kind": "upkeep", "per_hour": 30}, {"kind": "tax", "pct_per_hour": 5}],
                   caps={"soft": 200, "efficiency": 0.5})
        d = simulate_player(cfg, 0, 0)[-1]
        balance = d["earned"] - d["spent_upgrades"] - d["spent_sinks"] - d["lost_softcap"]
        self.assertAlmostEqual(d["gold"], balance, places=6)
        self.assertGreaterEqual(d["gold"], 0)

    def test_cooldown_respected(self):
        cfg = tiny(actions=[{"name": "raid", "duration_s": 10, "cooldown_s": 50, "reward": [1, 1]}],
                   upgrades=[])
        st = PlayerState()
        refresh_caches(cfg, st)
        run_session(cfg, st, random.Random(0), 600)
        # one raid per 60 s window -> 10 raids, 100 s active, rest idle
        self.assertAlmostEqual(st.action_time["raid"], 100)
        self.assertAlmostEqual(st.idle_s, 500)

    def test_unlock_level_gates_action(self):
        cfg = tiny()
        cfg["actions"][1]["unlock_level"] = 999
        res = analysis.run(cfg, 0, 5)
        self.assertNotIn("raid", res["action_share"])

    def test_max_level_is_hard_cap(self):
        cfg = tiny(upgrades=[{"name": "pick", "target": "tap", "base_cost": 1, "growth": 1.0,
                              "bonus": 0.1, "max_level": 7}])
        st = PlayerState()
        refresh_caches(cfg, st)
        run_session(cfg, st, random.Random(0), 900)
        self.assertEqual(st.upgrades["pick"], 7)


class DetectorTests(unittest.TestCase):
    def issues(self, cfg, players=60):
        return {i["type"] for i in analysis.run(cfg, 0, players)["issues"]}

    def test_healthy_config_is_clean(self):
        cfg = config.load(CONFIGS / "idle_miner.json")
        res = analysis.run(cfg, 0, 60)
        self.assertNotIn("inflation", {i["type"] for i in res["issues"]})
        self.assertNotIn("exploit_loop", {i["type"] for i in res["issues"]})

    def test_inflation_flagged(self):
        self.assertIn("inflation", self.issues(config.load(CONFIGS / "broken_inflation.json")))

    def test_exploit_config(self):
        found = self.issues(config.load(CONFIGS / "exploit_loop.json"), 30)
        self.assertTrue({"exploit_loop", "dominant_action", "progression_stall"} <= found, found)

    def test_cooldown_starvation(self):
        cfg = tiny(actions=[{"name": "raid", "duration_s": 10, "cooldown_s": 90, "reward": [5, 5]}],
                   upgrades=[])
        self.assertIn("cooldown_starvation", self.issues(cfg, 5))

    def test_fast_payback_exploit(self):
        cfg = tiny(upgrades=[{"name": "pick", "target": "tap", "base_cost": 0.1, "growth": 1.5,
                              "bonus": 1.0, "max_level": 3}])
        self.assertIn("exploit_loop", self.issues(cfg, 5))


class ReportTests(unittest.TestCase):
    def test_sparkline_and_chart(self):
        self.assertEqual(len(report.sparkline([1, 2, 3, 4])), 4)
        self.assertEqual(report.sparkline([5, 5, 5]), "▁▁▁")
        chart = report.line_chart({"a": [1, 2, 3], "b": [3, 2, 1]}, height=5, width=10)
        self.assertEqual(len(chart.splitlines()), 8)

    def test_fmt(self):
        self.assertEqual(report.fmt_num(1500), "1.5k")
        self.assertEqual(report.fmt_num(2_500_000), "2.5M")

    def test_cli_run_and_sweep(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "r.json"
            buf = StringIO()
            with redirect_stdout(buf):
                main(["run", str(CONFIGS / "idle_miner.json"), "--players", "10", "--days", "4",
                      "--json", str(out)])
            self.assertIn("gold p50", buf.getvalue())
            self.assertEqual(len(json.loads(out.read_text())["days"]), 4)
            buf = StringIO()
            with redirect_stdout(buf):
                main(["sweep", str(CONFIGS / "idle_miner.json"), "--players", "5", "--days", "3",
                      "--param", "upgrades.sharper_pick.growth", "--values", "1.1", "1.5"])
            self.assertIn("sweep: upgrades.sharper_pick.growth", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
