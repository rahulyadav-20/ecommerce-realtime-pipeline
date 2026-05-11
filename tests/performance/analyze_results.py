#!/usr/bin/env python3
"""
analyze_results.py — Post-process benchmark CSV into a Markdown report.

Reads the CSV produced by benchmark.py and generates:
  • Per-phase statistics (mean, p50, p95, p99, max)
  • ASCII timeseries charts (throughput, latency, lag, resource usage)
  • SLO evaluation table
  • Markdown report saved alongside the CSV

Usage
─────
  python tests/performance/analyze_results.py tests/performance/results/benchmark_YYYYMMDD_HHMMSS.csv
  python tests/performance/analyze_results.py results/benchmark_*.csv   # latest

  Optional: generate PNG charts (requires matplotlib)
    python tests/performance/analyze_results.py results/benchmark_*.csv --png

Dependencies (core — no matplotlib required)
────────────────────────────────────────────
  Standard library only for the default ASCII report.
  pip install matplotlib  to also export PNG charts.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import pathlib
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

# ── Optional matplotlib ───────────────────────────────────────────────────────
try:
    import matplotlib
    matplotlib.use("Agg")   # non-interactive backend
    import matplotlib.pyplot as plt
    import matplotlib.gridspec as gridspec
    HAS_MPL = True
except ImportError:
    HAS_MPL = False


# ══════════════════════════════════════════════════════════════════════════════
# SLO TARGETS
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class SLO:
    name: str
    description: str
    unit: str
    target: float
    direction: str  # "below" or "above"

    def evaluate(self, value: float) -> Tuple[bool, str]:
        if self.direction == "below":
            passed = value <= self.target
        else:
            passed = value >= self.target
        status = "✓ PASS" if passed else "✗ FAIL"
        return passed, status


SLOS: List[SLO] = [
    SLO("p99_latency_warmup",    "p99 latency @ warmup (1k ev/s)",     "ms",       500,   "below"),
    SLO("p99_latency_steady",    "p99 latency @ 10k ev/s steady state","ms",      1000,   "below"),
    SLO("p95_latency_mid",       "p95 latency @ 50k ev/s",             "ms",      2000,   "below"),
    SLO("p95_latency_high",      "p95 latency @ 80k ev/s",             "ms",      5000,   "below"),
    SLO("throughput_steady",     "Actual throughput @ 10k target",      "ev/s",    9000,   "above"),
    SLO("throughput_high",       "Actual throughput @ 80k target",      "ev/s",   72000,   "above"),
    SLO("ckpt_duration_steady",  "Checkpoint duration @ steady state",  "ms",     30000,   "below"),
    SLO("kafka_lag_steady",      "Kafka lag @ 10k ev/s",               "events",  50000,   "below"),
    SLO("recovery_time",         "Flink recovery time after restart",   "s",         90,   "below"),
]


# ══════════════════════════════════════════════════════════════════════════════
# DATA LOADING
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class Row:
    ts:                     str
    phase:                  str
    elapsed_sec:            float
    target_rate:            int
    produced_total:         int
    consumed_total:         int
    actual_produce_rate:    float
    actual_consume_rate:    float
    kafka_lag:              int
    latency_p50_ms:         float
    latency_p95_ms:         float
    latency_p99_ms:         float
    latency_max_ms:         float
    latency_samples:        int
    checkpoint_duration_ms: int
    checkpoint_size_mb:     float
    checkpoint_count:       int
    flink_jm_cpu_pct:       float
    flink_jm_mem_mb:        float
    flink_tm1_cpu_pct:      float
    flink_tm1_mem_mb:       float
    kafka1_cpu_pct:         float
    kafka1_mem_mb:          float
    flink_job_status:       str
    errors:                 int


def load_csv(path: str) -> List[Row]:
    rows = []
    with open(path, newline="") as f:
        for rec in csv.DictReader(f):
            try:
                rows.append(Row(
                    ts                     = rec["ts"],
                    phase                  = rec["phase"],
                    elapsed_sec            = float(rec.get("elapsed_sec", 0) or 0),
                    target_rate            = int(rec.get("target_rate", 0) or 0),
                    produced_total         = int(rec.get("produced_total", 0) or 0),
                    consumed_total         = int(rec.get("consumed_total", 0) or 0),
                    actual_produce_rate    = float(rec.get("actual_produce_rate", 0) or 0),
                    actual_consume_rate    = float(rec.get("actual_consume_rate", 0) or 0),
                    kafka_lag              = int(rec.get("kafka_lag", -1) or -1),
                    latency_p50_ms         = float(rec.get("latency_p50_ms", 0) or 0),
                    latency_p95_ms         = float(rec.get("latency_p95_ms", 0) or 0),
                    latency_p99_ms         = float(rec.get("latency_p99_ms", 0) or 0),
                    latency_max_ms         = float(rec.get("latency_max_ms", 0) or 0),
                    latency_samples        = int(rec.get("latency_samples", 0) or 0),
                    checkpoint_duration_ms = int(rec.get("checkpoint_duration_ms", 0) or 0),
                    checkpoint_size_mb     = float(rec.get("checkpoint_size_mb", 0) or 0),
                    checkpoint_count       = int(rec.get("checkpoint_count", 0) or 0),
                    flink_jm_cpu_pct       = float(rec.get("flink_jm_cpu_pct", 0) or 0),
                    flink_jm_mem_mb        = float(rec.get("flink_jm_mem_mb", 0) or 0),
                    flink_tm1_cpu_pct      = float(rec.get("flink_tm1_cpu_pct", 0) or 0),
                    flink_tm1_mem_mb       = float(rec.get("flink_tm1_mem_mb", 0) or 0),
                    kafka1_cpu_pct         = float(rec.get("kafka1_cpu_pct", 0) or 0),
                    kafka1_mem_mb          = float(rec.get("kafka1_mem_mb", 0) or 0),
                    flink_job_status       = rec.get("flink_job_status", ""),
                    errors                 = int(rec.get("errors", 0) or 0),
                ))
            except Exception as exc:
                print(f"Warning: skipping malformed row: {exc}", file=sys.stderr)
    return rows


# ══════════════════════════════════════════════════════════════════════════════
# STATISTICS HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _pct(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    i = min(int(len(s) * p / 100), len(s) - 1)
    return s[i]

def _nonzero(values: List[float]) -> List[float]:
    return [v for v in values if v > 0]

def _safe_mean(values: List[float]) -> float:
    v = _nonzero(values)
    return statistics.mean(v) if v else 0.0

def _safe_max(values: List[float]) -> float:
    v = [x for x in values if x >= 0]
    return max(v) if v else 0.0


@dataclass
class PhaseStats:
    name:               str
    target_rate:        int
    sample_count:       int
    produce_rate_mean:  float
    produce_rate_p95:   float
    consume_rate_mean:  float
    lat_p50_mean:       float
    lat_p95_mean:       float
    lat_p99_mean:       float
    lat_max:            float
    lat_samples_total:  int
    kafka_lag_mean:     float
    kafka_lag_max:      int
    ckpt_dur_mean_ms:   float
    ckpt_dur_max_ms:    int
    ckpt_size_mean_mb:  float
    jm_cpu_mean:        float
    jm_mem_mean_mb:     float
    tm1_cpu_mean:       float
    tm1_mem_mean_mb:    float
    kafka_cpu_mean:     float
    kafka_mem_mean_mb:  float
    errors_total:       int


def compute_phase_stats(rows: List[Row]) -> PhaseStats:
    name         = rows[0].phase if rows else "unknown"
    target       = rows[0].target_rate if rows else 0
    pr           = _nonzero([r.actual_produce_rate for r in rows])
    cr           = _nonzero([r.actual_consume_rate for r in rows])
    lat50        = _nonzero([r.latency_p50_ms for r in rows])
    lat95        = _nonzero([r.latency_p95_ms for r in rows])
    lat99        = _nonzero([r.latency_p99_ms for r in rows])
    latmax       = _nonzero([r.latency_max_ms for r in rows])
    lags         = [r.kafka_lag for r in rows if r.kafka_lag >= 0]
    ckpt_dur     = _nonzero([float(r.checkpoint_duration_ms) for r in rows])
    ckpt_sz      = _nonzero([r.checkpoint_size_mb for r in rows])
    jm_cpu       = _nonzero([r.flink_jm_cpu_pct for r in rows])
    jm_mem       = _nonzero([r.flink_jm_mem_mb for r in rows])
    tm1_cpu      = _nonzero([r.flink_tm1_cpu_pct for r in rows])
    tm1_mem      = _nonzero([r.flink_tm1_mem_mb for r in rows])
    k1_cpu       = _nonzero([r.kafka1_cpu_pct for r in rows])
    k1_mem       = _nonzero([r.kafka1_mem_mb for r in rows])

    return PhaseStats(
        name               = name,
        target_rate        = target,
        sample_count       = len(rows),
        produce_rate_mean  = _safe_mean(pr),
        produce_rate_p95   = _pct(pr, 95),
        consume_rate_mean  = _safe_mean(cr),
        lat_p50_mean       = _safe_mean(lat50),
        lat_p95_mean       = _safe_mean(lat95),
        lat_p99_mean       = _safe_mean(lat99),
        lat_max            = _safe_max(latmax),
        lat_samples_total  = sum(r.latency_samples for r in rows),
        kafka_lag_mean     = _safe_mean([float(l) for l in lags]) if lags else 0,
        kafka_lag_max      = int(max(lags)) if lags else 0,
        ckpt_dur_mean_ms   = _safe_mean(ckpt_dur),
        ckpt_dur_max_ms    = int(_safe_max(ckpt_dur)),
        ckpt_size_mean_mb  = _safe_mean(ckpt_sz),
        jm_cpu_mean        = _safe_mean(jm_cpu),
        jm_mem_mean_mb     = _safe_mean(jm_mem),
        tm1_cpu_mean       = _safe_mean(tm1_cpu),
        tm1_mem_mean_mb    = _safe_mean(tm1_mem),
        kafka_cpu_mean     = _safe_mean(k1_cpu),
        kafka_mem_mean_mb  = _safe_mean(k1_mem),
        errors_total       = sum(r.errors for r in rows),
    )


# ══════════════════════════════════════════════════════════════════════════════
# ASCII CHARTS
# ══════════════════════════════════════════════════════════════════════════════

def _sparkline(values: List[float], width: int = 40) -> str:
    """Render a horizontal bar chart from a list of values."""
    if not values:
        return " " * width
    vmax = max(values) or 1
    bars = ["▁", "▂", "▃", "▄", "▅", "▆", "▇", "█"]
    n    = len(bars) - 1
    step = max(1, len(values) // width)
    sampled = [values[i] for i in range(0, len(values), step)][:width]
    return "".join(bars[min(int(v / vmax * n), n)] for v in sampled)


def _bar(value: float, max_val: float, width: int = 30, fill: str = "█") -> str:
    if max_val <= 0:
        return fill * 0 + "░" * width
    filled = min(int(value / max_val * width), width)
    return fill * filled + "░" * (width - filled)


def ascii_throughput_chart(phase_stats: List[PhaseStats]) -> str:
    lines = ["### Throughput (events/s)", ""]
    max_rate = max((s.produce_rate_mean for s in phase_stats), default=1)
    for s in phase_stats:
        bar    = _bar(s.produce_rate_mean, max_rate)
        target = f"{s.target_rate:>8,}"
        actual = f"{s.produce_rate_mean:>8,.0f}"
        ratio  = s.produce_rate_mean / s.target_rate * 100 if s.target_rate else 0
        lines.append(
            f"  {s.name:<14} [{bar}] {actual} ev/s  (target {target}  {ratio:.0f}%)"
        )
    return "\n".join(lines)


def ascii_latency_chart(phase_stats: List[PhaseStats]) -> str:
    lines = ["### End-to-End Latency (ms)", ""]
    lines.append(f"  {'Phase':<14}  {'p50':>8}  {'p95':>8}  {'p99':>8}  {'max':>8}  Sparkline")
    lines.append("  " + "─" * 80)
    max_p99 = max((s.lat_p99_mean for s in phase_stats), default=1) or 1
    for s in phase_stats:
        bar = _bar(s.lat_p99_mean, max_p99, width=20, fill="▓")
        lines.append(
            f"  {s.name:<14}  {s.lat_p50_mean:>8.1f}  "
            f"{s.lat_p95_mean:>8.1f}  {s.lat_p99_mean:>8.1f}  "
            f"{s.lat_max:>8.1f}  {bar}"
        )
    return "\n".join(lines)


def ascii_resource_chart(phase_stats: List[PhaseStats]) -> str:
    lines = ["### Resource Usage", ""]
    lines.append(f"  {'Phase':<14}  {'JM CPU':>7}  {'JM Mem':>8}  "
                 f"{'TM1 CPU':>8}  {'TM1 Mem':>8}  {'K1 CPU':>7}  {'K1 Mem':>8}")
    lines.append("  " + "─" * 72)
    for s in phase_stats:
        lines.append(
            f"  {s.name:<14}  {s.jm_cpu_mean:>6.1f}%  {s.jm_mem_mean_mb:>7.0f}MB  "
            f"{s.tm1_cpu_mean:>7.1f}%  {s.tm1_mem_mean_mb:>7.0f}MB  "
            f"{s.kafka_cpu_mean:>6.1f}%  {s.kafka_mem_mean_mb:>7.0f}MB"
        )
    return "\n".join(lines)


def ascii_checkpoint_chart(phase_stats: List[PhaseStats]) -> str:
    lines = ["### Checkpoint Performance", ""]
    lines.append(f"  {'Phase':<14}  {'Avg dur (ms)':>13}  {'Max dur (ms)':>13}  {'Avg size (MB)':>13}  {'Count':>6}")
    lines.append("  " + "─" * 72)
    for s in phase_stats:
        lines.append(
            f"  {s.name:<14}  {s.ckpt_dur_mean_ms:>13.0f}  "
            f"{s.ckpt_dur_max_ms:>13d}  {s.ckpt_size_mean_mb:>13.1f}  "
            f"{s.sample_count:>6}"
        )
    return "\n".join(lines)


def ascii_timeseries(rows: List[Row], field: str, title: str, unit: str, width: int = 60) -> str:
    values = [getattr(r, field) for r in rows]
    vmax   = max(values, default=0) or 1
    spark  = _sparkline(values, width=width)
    return (
        f"**{title}** (0 → {vmax:,.0f} {unit})\n"
        f"```\n{spark}\n```"
    )


# ══════════════════════════════════════════════════════════════════════════════
# SLO EVALUATION
# ══════════════════════════════════════════════════════════════════════════════

def evaluate_slos(rows: List[Row], by_phase: Dict[str, List[Row]]) -> List[dict]:
    """Return one dict per SLO with {name, target, actual, passed, status}."""
    results = []

    def _phase_val(phase_name: str, fn):
        r = by_phase.get(phase_name, [])
        return fn(r) if r else None

    mappings = {
        "p99_latency_warmup":   lambda: _phase_val("warmup",
                                    lambda r: _safe_mean([x.latency_p99_ms for x in r])),
        "p99_latency_steady":   lambda: _phase_val("steady_low",
                                    lambda r: _safe_mean([x.latency_p99_ms for x in r])),
        "p95_latency_mid":      lambda: _phase_val("steady_mid",
                                    lambda r: _safe_mean([x.latency_p95_ms for x in r])),
        "p95_latency_high":     lambda: _phase_val("steady_high",
                                    lambda r: _safe_mean([x.latency_p95_ms for x in r])),
        "throughput_steady":    lambda: _phase_val("steady_low",
                                    lambda r: _safe_mean([x.actual_produce_rate for x in r])),
        "throughput_high":      lambda: _phase_val("steady_high",
                                    lambda r: _safe_mean([x.actual_produce_rate for x in r])),
        "ckpt_duration_steady": lambda: _phase_val("steady_low",
                                    lambda r: _safe_mean([float(x.checkpoint_duration_ms) for x in r])),
        "kafka_lag_steady":     lambda: _phase_val("steady_low",
                                    lambda r: _safe_mean([float(x.kafka_lag) for x in r if x.kafka_lag >= 0])),
        "recovery_time":        lambda: None,    # extracted from job_status field
    }

    # Attempt to extract recovery time from status field
    for row in rows:
        if row.phase == "recovery" and "RECOVERED_IN_" in row.flink_job_status:
            try:
                t = float(row.flink_job_status.split("RECOVERED_IN_")[1].rstrip("s"))
                mappings["recovery_time"] = lambda t=t: t
            except Exception:
                pass
            break

    for slo in SLOS:
        fn     = mappings.get(slo.name)
        actual = fn() if fn else None
        if actual is None:
            results.append({
                "name": slo.name, "description": slo.description,
                "unit": slo.unit, "target": slo.target,
                "actual": "N/A", "passed": None, "status": "⚠ NO DATA",
            })
        else:
            passed, status = slo.evaluate(actual)
            results.append({
                "name": slo.name, "description": slo.description,
                "unit": slo.unit, "target": slo.target,
                "actual": round(actual, 1), "passed": passed, "status": status,
            })
    return results


# ══════════════════════════════════════════════════════════════════════════════
# MATPLOTLIB CHARTS (optional)
# ══════════════════════════════════════════════════════════════════════════════

def generate_png_charts(rows: List[Row], out_dir: pathlib.Path, prefix: str) -> List[str]:
    if not HAS_MPL:
        print("matplotlib not installed — skipping PNG charts", file=sys.stderr)
        return []

    paths = []

    # ── Figure 1: Throughput + Latency (4 subplots) ──────────────────────────
    fig, axes = plt.subplots(2, 2, figsize=(14, 8))
    fig.suptitle("Flink ETL Benchmark Results", fontsize=14, fontweight="bold")

    times     = [r.elapsed_sec for r in rows]
    phases    = [r.phase       for r in rows]
    colours   = {
        "warmup": "#aaaaaa", "steady_low": "#4c72b0", "steady_mid": "#55a868",
        "steady_high": "#c44e52", "burst": "#dd8452", "recovery": "#9467bd",
    }

    # Colour-coded scatter by phase
    def _scatter_phase(ax, y_values, ylabel):
        for phase_name, colour in colours.items():
            mask = [i for i, p in enumerate(phases) if p == phase_name]
            if mask:
                ax.scatter([times[i] for i in mask], [y_values[i] for i in mask],
                           label=phase_name, color=colour, s=20, alpha=0.7)
        ax.set_ylabel(ylabel)
        ax.set_xlabel("Elapsed (s)")
        ax.legend(fontsize=7, loc="upper left")
        ax.grid(alpha=0.3)

    ax = axes[0, 0]
    ax.set_title("Actual Throughput (ev/s)")
    _scatter_phase(ax, [r.actual_produce_rate for r in rows], "Events / second")
    for i, r in enumerate(rows):
        if i == 0 or rows[i].target_rate != rows[i-1].target_rate:
            ax.axhline(r.target_rate, linestyle="--", color="red", alpha=0.4, lw=1)

    ax = axes[0, 1]
    ax.set_title("End-to-End Latency (ms)")
    _scatter_phase(ax, [r.latency_p95_ms for r in rows], "p95 Latency (ms)")

    ax = axes[1, 0]
    ax.set_title("Kafka Consumer Lag")
    lags = [max(r.kafka_lag, 0) for r in rows]
    _scatter_phase(ax, lags, "Lag (events)")

    ax = axes[1, 1]
    ax.set_title("Flink JM CPU %")
    _scatter_phase(ax, [r.flink_jm_cpu_pct for r in rows], "CPU %")

    plt.tight_layout()
    p1 = out_dir / f"{prefix}_overview.png"
    fig.savefig(p1, dpi=120, bbox_inches="tight")
    plt.close(fig)
    paths.append(str(p1))

    # ── Figure 2: Latency distribution by phase ────────────────────────────
    by_phase: Dict[str, List[Row]] = defaultdict(list)
    for r in rows:
        by_phase[r.phase].append(r)

    phase_order = ["warmup", "steady_low", "steady_mid", "steady_high", "burst", "recovery"]
    present     = [p for p in phase_order if p in by_phase]

    if present:
        fig2, ax2 = plt.subplots(figsize=(10, 5))
        labels, p50_data, p95_data, p99_data = [], [], [], []
        for p in present:
            rs = by_phase[p]
            labels.append(f"{p}\n({rs[0].target_rate:,}/s)")
            p50_data.append(_safe_mean([r.latency_p50_ms for r in rs]))
            p95_data.append(_safe_mean([r.latency_p95_ms for r in rs]))
            p99_data.append(_safe_mean([r.latency_p99_ms for r in rs]))

        x     = range(len(labels))
        width = 0.25
        ax2.bar([i - width for i in x], p50_data, width, label="p50", color="#4c72b0")
        ax2.bar(x,                       p95_data, width, label="p95", color="#55a868")
        ax2.bar([i + width for i in x], p99_data, width, label="p99", color="#c44e52")
        ax2.set_xticks(list(x))
        ax2.set_xticklabels(labels, fontsize=9)
        ax2.set_ylabel("Latency (ms)")
        ax2.set_title("End-to-End Latency by Phase (p50 / p95 / p99)")
        ax2.legend()
        ax2.grid(axis="y", alpha=0.3)
        plt.tight_layout()
        p2 = out_dir / f"{prefix}_latency_by_phase.png"
        fig2.savefig(p2, dpi=120, bbox_inches="tight")
        plt.close(fig2)
        paths.append(str(p2))

    print(f"PNG charts saved: {paths}")
    return paths


# ══════════════════════════════════════════════════════════════════════════════
# MARKDOWN REPORT BUILDER
# ══════════════════════════════════════════════════════════════════════════════

def build_report(
    csv_path: str,
    rows: List[Row],
    phase_stats_list: List[PhaseStats],
    slo_results: List[dict],
    png_paths: List[str],
) -> str:
    ts       = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    by_phase = defaultdict(list)
    for r in rows:
        by_phase[r.phase].append(r)

    run_duration_min = rows[-1].elapsed_sec / 60 if rows else 0
    total_produced   = max((r.produced_total for r in rows), default=0)
    total_consumed   = max((r.consumed_total for r in rows), default=0)
    pass_count       = sum(1 for s in slo_results if s.get("passed") is True)
    fail_count       = sum(1 for s in slo_results if s.get("passed") is False)
    nodata_count     = sum(1 for s in slo_results if s.get("passed") is None)

    md = []
    md.append(f"# Flink ETL Performance Benchmark Report")
    md.append(f"")
    md.append(f"> Generated: {ts}  |  Source: `{csv_path}`")
    md.append(f"")

    # ── Executive summary ────────────────────────────────────────────────────
    md.append(f"## Executive Summary")
    md.append(f"")
    md.append(f"| Metric | Value |")
    md.append(f"|--------|-------|")
    md.append(f"| Total run time | {run_duration_min:.1f} min |")
    md.append(f"| Total events produced | {total_produced:,} |")
    md.append(f"| Total events consumed | {total_consumed:,} |")
    md.append(f"| Data rows collected | {len(rows)} |")
    md.append(f"| SLOs: PASS / FAIL / NO DATA | {pass_count} / {fail_count} / {nodata_count} |")
    md.append(f"")

    # ── SLO table ────────────────────────────────────────────────────────────
    md.append(f"## SLO Evaluation")
    md.append(f"")
    md.append(f"| Status | SLO | Target | Actual | Unit |")
    md.append(f"|--------|-----|--------|--------|------|")
    for s in slo_results:
        target = f"{s['target']:,}"
        actual = str(s["actual"]) if s["actual"] != "N/A" else "N/A"
        md.append(f"| {s['status']} | {s['description']} | {target} | {actual} | {s['unit']} |")
    md.append(f"")

    # ── Phase summary table ──────────────────────────────────────────────────
    md.append(f"## Per-Phase Summary")
    md.append(f"")
    md.append(f"| Phase | Target (ev/s) | Actual (ev/s) | p50 (ms) | p95 (ms) | p99 (ms) | Max lag | Ckpt (ms) | JM CPU |")
    md.append(f"|-------|--------------|---------------|----------|----------|----------|---------|-----------|--------|")
    for s in phase_stats_list:
        md.append(
            f"| {s.name} | {s.target_rate:,} | {s.produce_rate_mean:,.0f} "
            f"| {s.lat_p50_mean:.1f} | {s.lat_p95_mean:.1f} | {s.lat_p99_mean:.1f} "
            f"| {s.kafka_lag_max:,} | {s.ckpt_dur_mean_ms:.0f} | {s.jm_cpu_mean:.1f}% |"
        )
    md.append(f"")

    # ── ASCII charts ─────────────────────────────────────────────────────────
    md.append(f"## Charts (ASCII)")
    md.append(f"")
    md.append(f"```")
    md.append(ascii_throughput_chart(phase_stats_list))
    md.append(f"```")
    md.append(f"")
    md.append(f"```")
    md.append(ascii_latency_chart(phase_stats_list))
    md.append(f"```")
    md.append(f"")
    md.append(f"```")
    md.append(ascii_checkpoint_chart(phase_stats_list))
    md.append(f"```")
    md.append(f"")
    md.append(f"```")
    md.append(ascii_resource_chart(phase_stats_list))
    md.append(f"```")
    md.append(f"")

    # ── Timeseries sparklines ────────────────────────────────────────────────
    md.append(f"## Time-Series Overview")
    md.append(f"")
    md.append(ascii_timeseries(rows, "actual_produce_rate", "Throughput", "ev/s"))
    md.append(f"")
    md.append(ascii_timeseries(rows, "latency_p95_ms", "p95 Latency", "ms"))
    md.append(f"")
    md.append(ascii_timeseries(rows, "kafka_lag", "Kafka Consumer Lag", "events"))
    md.append(f"")
    md.append(ascii_timeseries(rows, "flink_jm_cpu_pct", "Flink JM CPU", "%"))
    md.append(f"")
    md.append(ascii_timeseries(rows, "flink_jm_mem_mb", "Flink JM Memory", "MB"))
    md.append(f"")

    # ── PNG chart links (if generated) ───────────────────────────────────────
    if png_paths:
        md.append(f"## PNG Charts")
        md.append(f"")
        for p in png_paths:
            fname = os.path.basename(p)
            md.append(f"![{fname}]({fname})")
        md.append(f"")

    # ── Per-phase detail sections ─────────────────────────────────────────────
    md.append(f"## Detailed Phase Analysis")
    md.append(f"")
    for s in phase_stats_list:
        md.append(f"### {s.name}  (target: {s.target_rate:,} ev/s)")
        md.append(f"")
        md.append(f"| Metric | Value |")
        md.append(f"|--------|-------|")
        md.append(f"| Sample count | {s.sample_count} |")
        md.append(f"| Produce rate (mean) | {s.produce_rate_mean:,.0f} ev/s |")
        md.append(f"| Consume rate (mean) | {s.consume_rate_mean:,.0f} ev/s |")
        md.append(f"| Latency p50 (mean) | {s.lat_p50_mean:.1f} ms |")
        md.append(f"| Latency p95 (mean) | {s.lat_p95_mean:.1f} ms |")
        md.append(f"| Latency p99 (mean) | {s.lat_p99_mean:.1f} ms |")
        md.append(f"| Latency max | {s.lat_max:.1f} ms |")
        md.append(f"| Latency samples | {s.lat_samples_total:,} |")
        md.append(f"| Kafka lag (mean) | {s.kafka_lag_mean:,.0f} |")
        md.append(f"| Kafka lag (max) | {s.kafka_lag_max:,} |")
        md.append(f"| Checkpoint duration (mean) | {s.ckpt_dur_mean_ms:.0f} ms |")
        md.append(f"| Checkpoint duration (max) | {s.ckpt_dur_max_ms} ms |")
        md.append(f"| Checkpoint size (mean) | {s.ckpt_size_mean_mb:.1f} MB |")
        md.append(f"| Flink JM CPU (mean) | {s.jm_cpu_mean:.1f}% |")
        md.append(f"| Flink JM Memory (mean) | {s.jm_mem_mean_mb:.0f} MB |")
        md.append(f"| Flink TM1 CPU (mean) | {s.tm1_cpu_mean:.1f}% |")
        md.append(f"| Flink TM1 Memory (mean) | {s.tm1_mem_mean_mb:.0f} MB |")
        md.append(f"| Kafka broker CPU (mean) | {s.kafka_cpu_mean:.1f}% |")
        md.append(f"| Kafka broker Memory (mean) | {s.kafka_mem_mean_mb:.0f} MB |")
        md.append(f"| Producer / consumer errors | {s.errors_total} |")
        md.append(f"")

    # ── Methodology ──────────────────────────────────────────────────────────
    md.append(f"## Methodology")
    md.append(f"")
    md.append(f"**Latency measurement:** A dedicated Kafka consumer reads from "
              f"`ecommerce.events.clean.v1` (the Flink output topic). "
              f"For 1 in every 100 produced events the `event_id` and producer "
              f"timestamp (nanosecond monotonic clock) are recorded. When the "
              f"consumer sees the same `event_id`, the elapsed time is the "
              f"end-to-end latency through Flink's validate → dedup pipeline.")
    md.append(f"")
    md.append(f"**Throughput measurement:** Actual events/s is computed over "
              f"each {5}-second poll interval from the running produced/consumed counters.")
    md.append(f"")
    md.append(f"**Checkpoint metrics:** Fetched from the Flink REST API "
              f"`GET /jobs/{{id}}/checkpoints` at every poll interval.")
    md.append(f"")
    md.append(f"**Resource metrics:** Collected via `docker stats --no-stream` "
              f"for `flink-jobmanager`, `flink-taskmanager-1`, and `kafka-1`.")
    md.append(f"")
    md.append(f"---")
    md.append(f"*Report generated by `tests/performance/analyze_results.py`*")

    return "\n".join(md)


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Analyze benchmark CSV and generate Markdown report",
    )
    parser.add_argument("csv_file", nargs="?",
                        help="Path to benchmark CSV (auto-detect latest in results/ if omitted)")
    parser.add_argument("--results-dir", default="tests/performance/results",
                        help="Directory to search for latest CSV")
    parser.add_argument("--png", action="store_true",
                        help="Also generate PNG charts (requires matplotlib)")
    parser.add_argument("--stdout", action="store_true",
                        help="Print report to stdout instead of writing to file")
    args = parser.parse_args()

    # ── Resolve CSV path ──────────────────────────────────────────────────────
    if args.csv_file:
        csv_path = pathlib.Path(args.csv_file)
    else:
        results_dir = pathlib.Path(args.results_dir)
        csvs = sorted(results_dir.glob("benchmark_*.csv"), key=lambda p: p.stat().st_mtime)
        if not csvs:
            print(f"No benchmark CSV found in {results_dir}", file=sys.stderr)
            sys.exit(1)
        csv_path = csvs[-1]
        print(f"Using latest CSV: {csv_path}")

    if not csv_path.exists():
        print(f"File not found: {csv_path}", file=sys.stderr)
        sys.exit(1)

    # ── Load data ─────────────────────────────────────────────────────────────
    rows = load_csv(str(csv_path))
    if not rows:
        print("CSV is empty or could not be parsed", file=sys.stderr)
        sys.exit(1)

    print(f"Loaded {len(rows)} rows from {csv_path}")

    # ── Group by phase ────────────────────────────────────────────────────────
    by_phase: Dict[str, List[Row]] = defaultdict(list)
    for r in rows:
        by_phase[r.phase].append(r)

    phase_order     = ["warmup", "steady_low", "steady_mid", "steady_high", "burst", "recovery"]
    ordered_phases  = [p for p in phase_order if p in by_phase]
    # Include any phases not in the standard order
    for p in by_phase:
        if p not in ordered_phases:
            ordered_phases.append(p)

    phase_stats_list = [compute_phase_stats(by_phase[p]) for p in ordered_phases]
    slo_results      = evaluate_slos(rows, by_phase)

    # ── PNG charts ────────────────────────────────────────────────────────────
    png_paths = []
    if args.png:
        prefix    = csv_path.stem
        png_paths = generate_png_charts(rows, csv_path.parent, prefix)

    # ── Build and write report ────────────────────────────────────────────────
    report = build_report(str(csv_path), rows, phase_stats_list, slo_results, png_paths)

    if args.stdout:
        print(report)
    else:
        report_path = csv_path.with_name(csv_path.stem + "_report.md")
        report_path.write_text(report, encoding="utf-8")
        print(f"\nReport written: {report_path}")

    # ── Quick pass/fail summary ───────────────────────────────────────────────
    pass_count  = sum(1 for s in slo_results if s.get("passed") is True)
    fail_count  = sum(1 for s in slo_results if s.get("passed") is False)
    nodata_count= sum(1 for s in slo_results if s.get("passed") is None)
    print(f"\nSLO summary: {pass_count} PASS  {fail_count} FAIL  {nodata_count} NO DATA")
    if fail_count:
        print("Failed SLOs:")
        for s in slo_results:
            if s.get("passed") is False:
                print(f"  ✗ {s['description']}: {s['actual']} {s['unit']} (target {s['target']})")
    sys.exit(1 if fail_count else 0)


if __name__ == "__main__":
    main()
