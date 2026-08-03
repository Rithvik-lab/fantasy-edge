"""Append-only experiment log, so "is it getting better" has an answer.

Two roles for held-out data, and keeping them separate is the whole point:

    VALIDATION (2018-2023)  optimize against this freely
    CHECKPOINT (2024-2025)  look as often as you like, never SELECT on it

Looking at held-out data costs nothing. *Choosing* a config because it scored
well there is what makes the score dishonest — do it enough times and you've
found the config that best fits that season's noise, and the number stops
meaning anything.

So `run_checkpoint` prints the gap and deliberately refuses to rank configs.
The signal you want:

    validation improves + checkpoint improves  -> real gain
    validation improves + checkpoint flat      -> overfitting validation, stop

The genuine out-of-sample test is the 2026 season itself, which arrives a week
at a time and cannot be overfit because it has not happened.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

import polars as pl

from fantasyedge import config

LOG = config.ROOT / "experiments.jsonl"


def config_hash(features: list[str], params: dict, label: str) -> str:
    """Stable id for a modelling config, so repeats are recognisable."""
    blob = json.dumps(
        {"features": sorted(features), "params": dict(sorted(params.items())),
         "label": label},
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def log_run(
    name: str,
    label: str,
    features: list[str],
    params: dict,
    validation: dict[str, Any],
    checkpoint: dict[str, Any] | None = None,
    notes: str = "",
) -> dict:
    """Append one experiment. Never overwrites; history is the point."""
    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "name": name,
        "label": label,
        "config_hash": config_hash(features, params, label),
        "n_features": len(features),
        "features": sorted(features),
        "params": params,
        "validation": validation,
        "checkpoint": checkpoint,
        "notes": notes,
    }
    with LOG.open("a") as fh:
        fh.write(json.dumps(entry) + "\n")
    return entry


def history(label: str | None = None) -> pl.DataFrame:
    """Every logged run, flattened for comparison."""
    if not LOG.exists():
        return pl.DataFrame()

    rows = []
    for line in LOG.read_text().splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        if label and e.get("label") != label:
            continue
        row = {
            "ts": e["ts"], "name": e["name"], "label": e["label"],
            "hash": e["config_hash"], "n_features": e["n_features"],
        }
        for k, v in (e.get("validation") or {}).items():
            if isinstance(v, (int, float)):
                row[f"val_{k}"] = v
        for k, v in (e.get("checkpoint") or {}).items():
            if isinstance(v, (int, float)):
                row[f"chk_{k}"] = v
        rows.append(row)

    return pl.DataFrame(rows) if rows else pl.DataFrame()


def progress(label: str, metric: str = "mae") -> pl.DataFrame:
    """Has the metric actually improved over time, and is the gap widening?

    A growing val/checkpoint gap is the overfitting alarm.
    """
    h = history(label)
    if not h.height:
        return h

    cols = ["ts", "name", "n_features", f"val_{metric}"]
    if f"chk_{metric}" in h.columns:
        cols.append(f"chk_{metric}")
    out = h.select([c for c in cols if c in h.columns])

    if f"chk_{metric}" in out.columns:
        out = out.with_columns(
            (pl.col(f"chk_{metric}") - pl.col(f"val_{metric}")).round(3).alias("gap")
        )
    return out


def run_checkpoint(evaluate_fn, *args, **kwargs) -> dict:
    """Evaluate on the sealed seasons and return the numbers, with a warning.

    Deliberately returns a plain dict and refuses to compare configs. If you
    find yourself choosing between two models because of what this printed,
    that is the moment the checkpoint stops being trustworthy.
    """
    result = evaluate_fn(*args, **kwargs)
    result["_seasons"] = config.TEST_SEASONS
    result["_warning"] = (
        "Monitoring only. Do not select a config on these numbers — "
        "use validation for that."
    )
    return result
