# Performance Benchmark

End-to-end latency and throughput benchmark for the Flink ETL pipeline.

## Quick start

```bash
# Install dependencies
pip install confluent-kafka[avro] requests psutil

# Full benchmark (~18 minutes)
python tests/performance/benchmark.py

# Quick smoke test (~2 minutes)
python tests/performance/benchmark.py --quick

# Skip the Flink restart recovery phase
python tests/performance/benchmark.py --skip-recovery

# Dry-run (no Kafka — measures generator throughput only)
python tests/performance/benchmark.py --dry-run

# Override endpoints (running from host, not inside Docker)
python tests/performance/benchmark.py \
  --bootstrap localhost:9092 \
  --schema-registry http://localhost:8081 \
  --flink-url http://localhost:8082
```

## Analyze results

```bash
# Analyze latest CSV and write Markdown report
python tests/performance/analyze_results.py

# Analyze a specific CSV
python tests/performance/analyze_results.py tests/performance/results/benchmark_20260511_140000.csv

# Also generate PNG charts (requires matplotlib)
python tests/performance/analyze_results.py --png

# Print report to stdout
python tests/performance/analyze_results.py --stdout
```

## Outputs

| File | Description |
|------|-------------|
| `results/benchmark_YYYYMMDD_HHMMSS.csv` | Raw time-series metrics |
| `results/benchmark_YYYYMMDD_HHMMSS_meta.json` | Run metadata |
| `results/benchmark_YYYYMMDD_HHMMSS_report.md` | Markdown report |
| `results/benchmark_YYYYMMDD_HHMMSS_overview.png` | 4-panel PNG chart (optional) |
| `results/benchmark_YYYYMMDD_HHMMSS_latency_by_phase.png` | Latency bar chart (optional) |

## Benchmark phases

| Phase | Rate | Duration | Purpose |
|-------|------|----------|---------|
| warmup | 1 000 ev/s | 60 s | Prime JVM / Kafka caches |
| steady_low | 10 000 ev/s | 5 min | Baseline throughput |
| steady_mid | 50 000 ev/s | 5 min | Moderate load |
| steady_high | 80 000 ev/s | 5 min | Near-saturation |
| burst | 150 000 ev/s | 30 s | Spike, head-room test |
| recovery | 10 000 ev/s | 60 s | Post-restart recovery |

## SLO targets

| Metric | Target |
|--------|--------|
| p99 latency @ 1k ev/s | ≤ 500 ms |
| p99 latency @ 10k ev/s | ≤ 1 000 ms |
| p95 latency @ 50k ev/s | ≤ 2 000 ms |
| p95 latency @ 80k ev/s | ≤ 5 000 ms |
| Throughput @ 10k target | ≥ 9 000 ev/s |
| Throughput @ 80k target | ≥ 72 000 ev/s |
| Checkpoint duration | ≤ 30 000 ms |
| Kafka lag @ 10k ev/s | ≤ 50 000 events |
| Flink recovery time | ≤ 90 s |
