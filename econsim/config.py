"""Economy config: loading, validation, and dotted-path overrides for sweeps."""
from __future__ import annotations

import copy
import json
from pathlib import Path

REQUIRED_TOP = ("actions", "upgrades", "players", "days")


class ConfigError(ValueError):
    pass


def load(path: str | Path) -> dict:
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    validate(cfg)
    return cfg


def validate(cfg: dict) -> None:
    for k in REQUIRED_TOP:
        if k not in cfg:
            raise ConfigError(f"missing top-level key '{k}'")
    if not cfg["actions"]:
        raise ConfigError("need at least one action")
    names = set()
    for a in cfg["actions"]:
        for k in ("name", "duration_s", "reward"):
            if k not in a:
                raise ConfigError(f"action missing '{k}': {a}")
        if a["duration_s"] <= 0:
            raise ConfigError(f"action '{a['name']}' duration must be > 0")
        lo, hi = a["reward"]
        if lo > hi or lo < 0:
            raise ConfigError(f"action '{a['name']}' reward range invalid")
        names.add(a["name"])
    for u in cfg["upgrades"]:
        for k in ("name", "base_cost", "growth", "target", "bonus"):
            if k not in u:
                raise ConfigError(f"upgrade missing '{k}': {u}")
        if u["target"] != "*" and u["target"] not in names:
            raise ConfigError(f"upgrade '{u['name']}' targets unknown action '{u['target']}'")
        if u["growth"] < 1.0:
            raise ConfigError(f"upgrade '{u['name']}' growth < 1 makes costs shrink")
    sess = cfg.get("session", {})
    if sess and sess.get("min_s", 1) > sess.get("max_s", 1):
        raise ConfigError("session.min_s > session.max_s")


def set_path(cfg: dict, path: str, value) -> dict:
    """Return a copy with `a.b.0.c` set; list items may be addressed by
    index or by their `name` field (e.g. `upgrades.sharper_pick.growth`)."""
    out = copy.deepcopy(cfg)
    node = out
    keys = path.split(".")
    for i, k in enumerate(keys):
        last = i == len(keys) - 1
        if isinstance(node, list):
            if k.isdigit():
                idx = int(k)
            else:
                idx = next((j for j, it in enumerate(node) if it.get("name") == k), None)
                if idx is None:
                    raise ConfigError(f"no list item named '{k}' in path '{path}'")
            if last:
                node[idx] = value
            else:
                node = node[idx]
        else:
            if k not in node and not last:
                raise ConfigError(f"bad path '{path}' at '{k}'")
            if last:
                node[k] = value
            else:
                node = node[k]
    validate(out)
    return out
