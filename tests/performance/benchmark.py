#!/usr/bin/env python3
"""
benchmark.py — End-to-end performance benchmark for the Flink ETL pipeline.

Pipeline under test
───────────────────
  event_generator (this script)
    → Kafka  ecommerce.events.raw.v1
      → Flink EcomEtlJob (validate + dedup)
        → Kafka  ecommerce.events.clean.v1   ← latency measured here
          → ClickHouse / Druid / Iceberg (sink health checked, not measured)

Benchmark phases
────────────────
  1. warmup        1 min   @ 1 000 ev/s   — prime caches and JIT
  2. steady_low    5 min   @ 10 000 ev/s  — baseline throughput
  3. steady_mid    5 min   @ 50 000 ev/s  — moderate load
  4. steady_high   5 min   @ 80 000 ev/s  — near-saturation
  5. burst         30 sec  @ 150 000 ev/s — spike, measure head-room
  6. recovery      ~2 min  @ 10 000 ev/s  — restart Flink, measure recovery

Metrics collected (every POLL_INTERVAL seconds)
────────────────────────────────────────────────
  Throughput  — actual events/s produced and consumed
  Latency     — p50 / p95 / p99 end-to-end (producer→clean-topic), ms
  Kafka lag   — consumer group offset lag on the clean topic
  Checkpoint  — Flink lastCheckpointDuration and size
  Resources   — CPU %, memory MB for flink-jobmanager, kafka-1, kafka-2

Outputs
───────
  results/benchmark_<YYYYMMDD_HHMMSS>.csv        raw time-series
  results/benchmark_<YYYYMMDD_HHMMSS>_report.md  (written by analyze_results.py)

Usage
─────
  python tests/performance/benchmark.py
  python tests/performance/benchmark.py --quick           # short smoke test
  python tests/performance/benchmark.py --skip-recovery   # skip the restart phase
  python tests/performance/benchmark.py --flink-url http://localhost:8082

Dependencies
────────────
  pip install confluent-kafka[avro] requests psutil
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import pathlib
import random
import signal
import statistics
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── Config (project root → config/) ──────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from config.settings import cfg as _cfg  # noqa: E402

# ── Optional imports ──────────────────────────────────────────────────────────
try:
    from confluent_kafka import Producer, Consumer, KafkaError, TopicPartition
    from confluent_kafka.schema_registry import SchemaRegistryClient
    from confluent_kafka.schema_registry.avro import AvroSerializer
    from confluent_kafka.serialization import MessageField, SerializationContext
    HAS_KAFKA = True
except ImportError:
    HAS_KAFKA = False

try:
    import requests as _requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("benchmark")

# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

def _default_results_dir() -> str:
    return str(Path(__file__).resolve().parent / "results")


@dataclass
class BenchmarkConfig:
    # ── Service endpoints — pulled from config/settings.py by default ─────────
    # Override on the CLI: --bootstrap localhost:9092 --flink-url http://...
    bootstrap_servers:  str = field(default_factory=lambda: _cfg.kafka.bootstrap_servers)
    schema_registry_url: str = field(default_factory=lambda: _cfg.kafka.schema_registry_url)
    flink_rest_url:     str = field(default_factory=lambda: _cfg.flink.rest_url)

    # ── Kafka topics ─────────────────────────────────────────────────────────
    raw_topic:   str = field(default_factory=lambda: _cfg.kafka.raw_topic)
    clean_topic: str = field(default_factory=lambda: _cfg.kafka.clean_topic)

    # ── Sampling ─────────────────────────────────────────────────────────────
    # Track latency for 1 in every N events to bound memory at high rates.
    # At 150k ev/s this yields ~1 500 tracked events/s — statistically valid.
    latency_sample_rate: int = field(default_factory=lambda: _cfg.pipeline.latency_sample_rate)

    # ── Metrics collection ───────────────────────────────────────────────────
    poll_interval_sec: int = 5

    # ── Results ──────────────────────────────────────────────────────────────
    results_dir: str = field(default_factory=_default_results_dir)

    # ── Docker containers to monitor ─────────────────────────────────────────
    monitor_containers: List[str] = field(default_factory=lambda: [
        "flink-jobmanager",
        "flink-taskmanager-1",
        "kafka-1",
        "kafka-2",
    ])

    # ── Schema file path ─────────────────────────────────────────────────────
    avro_schema_path: str = field(
        default_factory=lambda: str(
            Path(__file__).resolve().parents[2] / "schemas" / "clickstream-event.avsc"
        )
    )


@dataclass
class Phase:
    name: str
    target_rate: int   # events / second
    duration_sec: int
    description: str = ""


# Standard benchmark phases (override with --quick)
STANDARD_PHASES: List[Phase] = [
    Phase("warmup",       1_000,   60, "1 min @ 1k ev/s — prime caches"),
    Phase("steady_low",  10_000,  300, "5 min @ 10k ev/s — baseline"),
    Phase("steady_mid",  50_000,  300, "5 min @ 50k ev/s — moderate load"),
    Phase("steady_high", 80_000,  300, "5 min @ 80k ev/s — near-saturation"),
    Phase("burst",      150_000,   30, "30 s @ 150k ev/s — spike"),
    Phase("recovery",   10_000,    60, "1 min @ 10k ev/s — post-restart recovery"),
]

QUICK_PHASES: List[Phase] = [
    Phase("warmup",      1_000, 15, "15 s @ 1k ev/s"),
    Phase("steady_low", 10_000, 30, "30 s @ 10k ev/s"),
    Phase("steady_mid", 50_000, 30, "30 s @ 50k ev/s"),
    Phase("burst",      80_000, 15, "15 s @ 80k ev/s"),
    Phase("recovery",   10_000, 30, "30 s recovery"),
]


# ══════════════════════════════════════════════════════════════════════════════
# DATA MODELS
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class MetricPoint:
    """One row in the output CSV."""
    ts:                    str   = ""   # ISO-8601
    phase:                 str   = ""
    elapsed_sec:           float = 0.0  # seconds since phase start
    target_rate:           int   = 0
    produced_total:        int   = 0
    consumed_total:        int   = 0
    actual_produce_rate:   float = 0.0  # events/s over last poll_interval
    actual_consume_rate:   float = 0.0
    kafka_lag:             int   = 0
    latency_p50_ms:        float = 0.0
    latency_p95_ms:        float = 0.0
    latency_p99_ms:        float = 0.0
    latency_max_ms:        float = 0.0
    latency_samples:       int   = 0
    checkpoint_duration_ms: int  = 0
    checkpoint_size_mb:    float = 0.0
    checkpoint_count:      int   = 0
    flink_jm_cpu_pct:      float = 0.0
    flink_jm_mem_mb:       float = 0.0
    flink_tm1_cpu_pct:     float = 0.0
    flink_tm1_mem_mb:      float = 0.0
    kafka1_cpu_pct:        float = 0.0
    kafka1_mem_mb:         float = 0.0
    flink_job_status:      str   = "UNKNOWN"
    errors:                int   = 0


# ══════════════════════════════════════════════════════════════════════════════
# LATENCY TRACKER
# ══════════════════════════════════════════════════════════════════════════════

class LatencyTracker:
    """
    Thread-safe tracker for end-to-end latency.

    The producer registers an event_id + produced_at_ns when a message is sent.
    The consumer looks up event_id and computes consumed_at_ns - produced_at_ns.

    A bounded deque prevents unbounded memory growth at high rates.
    At 1% sampling of 150k ev/s ≈ 1 500 entries/s; deque of 50 000 holds ~33 s.
    """
    MAX_TRACKED = 50_000

    def __init__(self):
        self._lock     = threading.Lock()
        self._pending: Dict[str, int] = {}   # event_id → produced_at_ns
        self._order    = deque()             # (event_id, produced_at_ns) for eviction
        self._samples: List[float] = []      # completed latencies in ms (this window)

    def record_produced(self, event_id: str, produced_at_ns: int) -> None:
        with self._lock:
            if len(self._pending) >= self.MAX_TRACKED:
                # Evict oldest entries
                while self._order and len(self._pending) >= self.MAX_TRACKED:
                    old_id, _ = self._order.popleft()
                    self._pending.pop(old_id, None)
            self._pending[event_id]  = produced_at_ns
            self._order.append((event_id, produced_at_ns))

    def record_consumed(self, event_id: str, consumed_at_ns: int) -> Optional[float]:
        with self._lock:
            produced_ns = self._pending.pop(event_id, None)
        if produced_ns is None:
            return None
        latency_ms = (consumed_at_ns - produced_ns) / 1_000_000
        if 0 < latency_ms < 300_000:      # sanity: 0–300 s
            with self._lock:
                self._samples.append(latency_ms)
            return latency_ms
        return None

    def drain_samples(self) -> List[float]:
        """Return and clear all latency samples collected since last call."""
        with self._lock:
            samples, self._samples = self._samples, []
        return samples

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)


# ══════════════════════════════════════════════════════════════════════════════
# FLINK REST CLIENT
# ══════════════════════════════════════════════════════════════════════════════

class FlinkClient:
    """Thin wrapper around the Flink REST API."""

    def __init__(self, base_url: str, timeout: int = 10):
        self.base_url = base_url.rstrip("/")
        self.timeout  = timeout

    def _get(self, path: str) -> Optional[dict]:
        if not HAS_REQUESTS:
            return None
        try:
            r = _requests.get(f"{self.base_url}{path}", timeout=self.timeout)
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            log.debug("Flink GET %s failed: %s", path, exc)
            return None

    def _post(self, path: str, payload: dict = None) -> Optional[dict]:
        if not HAS_REQUESTS:
            return None
        try:
            r = _requests.post(
                f"{self.base_url}{path}",
                json=payload or {},
                timeout=self.timeout,
            )
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            log.debug("Flink POST %s failed: %s", path, exc)
            return None

    def running_jobs(self) -> List[dict]:
        data = self._get("/jobs/overview")
        if not data:
            return []
        return [j for j in data.get("jobs", []) if j.get("state") == "RUNNING"]

    def first_running_job_id(self) -> Optional[str]:
        jobs = self.running_jobs()
        return jobs[0]["jid"] if jobs else None

    def job_status(self, job_id: str) -> str:
        data = self._get(f"/jobs/{job_id}")
        return (data or {}).get("state", "UNKNOWN")

    def checkpoint_info(self, job_id: str) -> dict:
        data = self._get(f"/jobs/{job_id}/checkpoints")
        if not data:
            return {}
        latest = data.get("latest", {}).get("completed", {})
        return {
            "duration_ms": latest.get("end_to_end_duration", 0),
            "size_mb":     round(latest.get("state_size", 0) / (1024 * 1024), 2),
            "count":       data.get("counts", {}).get("completed", 0),
        }

    def trigger_savepoint(self, job_id: str, target_dir: str) -> Optional[str]:
        result = self._post(
            f"/jobs/{job_id}/savepoints",
            {"target-directory": target_dir, "cancel-job": False},
        )
        return (result or {}).get("request-id")

    def cancel_job(self, job_id: str) -> bool:
        result = self._post(f"/jobs/{job_id}/stop", {"drain": False})
        return result is not None


# ══════════════════════════════════════════════════════════════════════════════
# DOCKER STATS COLLECTOR
# ══════════════════════════════════════════════════════════════════════════════

class DockerStatsCollector:
    """Collect CPU % and memory for a list of container names."""

    def __init__(self, containers: List[str]):
        self.containers = containers

    def collect(self) -> Dict[str, dict]:
        """Return {container_name: {cpu_pct, mem_mb}} for all containers."""
        stats: Dict[str, dict] = {c: {"cpu_pct": 0.0, "mem_mb": 0.0} for c in self.containers}
        try:
            cmd = [
                "docker", "stats", "--no-stream",
                "--format", "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}",
            ] + self.containers
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            for line in result.stdout.strip().splitlines():
                parts = line.split("\t")
                if len(parts) < 3:
                    continue
                name     = parts[0].strip().lstrip("/")
                cpu_str  = parts[1].strip().rstrip("%")
                mem_str  = parts[2].strip().split("/")[0].strip()

                try:
                    cpu_pct = float(cpu_str)
                except ValueError:
                    cpu_pct = 0.0

                mem_mb = self._parse_mem(mem_str)
                if name in stats:
                    stats[name] = {"cpu_pct": cpu_pct, "mem_mb": mem_mb}
        except Exception as exc:
            log.debug("docker stats failed: %s", exc)
        return stats

    @staticmethod
    def _parse_mem(s: str) -> float:
        s = s.strip()
        for suffix, mult in [("GiB", 1024), ("MiB", 1), ("kB", 1/1024), ("B", 1/1024/1024)]:
            if s.endswith(suffix):
                try:
                    return float(s[: -len(suffix)]) * mult
                except ValueError:
                    return 0.0
        return 0.0


# ══════════════════════════════════════════════════════════════════════════════
# KAFKA LAG MONITOR
# ══════════════════════════════════════════════════════════════════════════════

class KafkaLagMonitor:
    """Read consumer group lag via kafka-consumer-groups command."""

    def __init__(self, bootstrap_servers: str, group_id: str, topic: str):
        self.bootstrap_servers = bootstrap_servers
        self.group_id          = group_id
        self.topic             = topic

    def get_lag(self) -> int:
        """Return total lag (sum across all partitions) or -1 on error."""
        try:
            cmd = [
                "docker", "exec", "kafka-1",
                "kafka-consumer-groups",
                "--bootstrap-server", "kafka-1:29092",
                "--describe", "--group", self.group_id,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            total_lag = 0
            for line in result.stdout.splitlines():
                parts = line.split()
                # Header row: TOPIC  PARTITION  CURRENT-OFFSET  LOG-END-OFFSET  LAG  ...
                if len(parts) >= 6 and self.topic in parts[0]:
                    try:
                        lag = int(parts[4])
                        total_lag += lag
                    except (ValueError, IndexError):
                        pass
            return total_lag
        except Exception as exc:
            log.debug("KafkaLagMonitor failed: %s", exc)
            return -1


# ══════════════════════════════════════════════════════════════════════════════
# TOKEN BUCKET RATE LIMITER
# ══════════════════════════════════════════════════════════════════════════════

class TokenBucket:
    """
    Thread-safe token bucket for rate limiting.

    At high rates (>50k/s) the bucket accumulates tokens during batch sends
    rather than sleeping between individual messages.
    """
    def __init__(self, rate: float):
        self.rate      = rate           # tokens per second
        self.tokens    = 0.0
        self._last     = time.perf_counter()
        self._lock     = threading.Lock()

    def consume_or_wait(self, n: int = 1) -> None:
        """Block until `n` tokens are available."""
        while True:
            with self._lock:
                now          = time.perf_counter()
                self.tokens  = min(self.rate, self.tokens + (now - self._last) * self.rate)
                self._last   = now
                if self.tokens >= n:
                    self.tokens -= n
                    return
                wait = (n - self.tokens) / self.rate
            time.sleep(min(wait, 0.001))

    def set_rate(self, rate: float) -> None:
        with self._lock:
            self.rate = rate


# ══════════════════════════════════════════════════════════════════════════════
# EVENT PRODUCER
# ══════════════════════════════════════════════════════════════════════════════

CATEGORIES   = ["electronics", "fashion", "home_kitchen", "books", "sports_fitness",
                 "beauty", "toys_games", "automotive", "food_grocery", "health_wellness"]
EVENT_TYPES  = ["VIEW", "ADD_TO_CART", "WISHLIST", "ORDER_CREATED",
                "PAYMENT_SUCCESS", "PAYMENT_FAILED", "ORDER_CANCELLED", "REFUND"]
DEVICES      = ["web", "android", "ios", "other"]
PAYMENT_MODES= ["credit_card", "debit_card", "upi", "wallet", "net_banking", "emi"]

def _make_event() -> dict:
    category = random.choice(CATEGORIES)
    etype    = random.choice(EVENT_TYPES)
    has_price= etype in ("ORDER_CREATED", "PAYMENT_SUCCESS", "PAYMENT_FAILED", "REFUND")
    return {
        "event_id":    str(uuid.uuid4()),
        "session_id":  str(uuid.uuid4()),
        "event_time":  int(time.time() * 1000),
        "event_type":  etype,
        "device":      random.choice(DEVICES),
        "ip":          f"{random.randint(1,254)}.{random.randint(0,254)}.{random.randint(0,254)}.{random.randint(1,254)}",
        "user_id":     f"u-{random.randint(1, 100_000):07d}" if random.random() > 0.05 else None,
        "product_id":  f"SKU-{random.randint(1000,9999)}" if has_price else None,
        "category":    category,
        "quantity":    random.randint(1, 5) if has_price else None,
        "price":       round(random.uniform(10, 5000), 2) if has_price else None,
        "payment_mode":random.choice(PAYMENT_MODES) if "PAYMENT" in etype else None,
    }


class EventProducer(threading.Thread):
    """
    Background thread that produces events to the raw Kafka topic.

    Rate is controlled by a TokenBucket. The thread runs until stop() is called.
    Latency tracking: 1 in every `sample_rate` events is registered with the
    LatencyTracker so the consumer can compute end-to-end latency.
    """
    def __init__(
        self,
        config: BenchmarkConfig,
        tracker: LatencyTracker,
        rate: int,
        sample_rate: int = 100,
    ):
        super().__init__(daemon=True, name="producer")
        self.config      = config
        self.tracker     = tracker
        self.bucket      = TokenBucket(rate)
        self.sample_rate = sample_rate
        self._stop_ev    = threading.Event()
        self._pause_ev   = threading.Event()
        self._pause_ev.set()   # not paused initially

        # Counters (read from main thread without a lock — fine for non-critical stats)
        self.produced    = 0
        self.errors      = 0

        self._producer: Optional[Any] = None
        self._serializer: Optional[Any] = None

    def _build_producer(self):
        if not HAS_KAFKA:
            return
        producer_conf = {
            "bootstrap.servers": self.config.bootstrap_servers,
            "linger.ms":         10,
            "batch.size":        65536,
            "compression.type":  "snappy",
            "acks":              "1",         # leader ack only for speed
            "queue.buffering.max.messages": 500_000,
        }
        self._producer = Producer(producer_conf)

        # Load Avro schema for serialisation
        try:
            schema_path = pathlib.Path(self.config.avro_schema_path)
            if schema_path.exists():
                schema_str = schema_path.read_text()
                sr_client  = SchemaRegistryClient({"url": self.config.schema_registry_url})
                self._serializer = AvroSerializer(
                    sr_client, schema_str,
                    lambda obj, ctx: obj,
                )
        except Exception as exc:
            log.warning("Avro serializer unavailable (%s) — using JSON", exc)
            self._serializer = None

    def set_rate(self, rate: int) -> None:
        self.bucket.set_rate(rate)

    def pause(self) -> None:
        self._pause_ev.clear()

    def resume(self) -> None:
        self._pause_ev.set()

    def stop(self) -> None:
        self._stop_ev.set()

    def run(self) -> None:
        self._build_producer()
        seq = 0
        while not self._stop_ev.is_set():
            self._pause_ev.wait(timeout=1.0)
            if self._stop_ev.is_set():
                break

            self.bucket.consume_or_wait(1)
            event = _make_event()

            # Sample 1/N events for latency tracking
            track = (seq % self.sample_rate == 0)
            if track:
                produced_ns = time.monotonic_ns()

            try:
                if self._producer and self._serializer:
                    self._producer.produce(
                        topic=self.config.raw_topic,
                        key=event["event_id"].encode(),
                        value=self._serializer(
                            event,
                            SerializationContext(self.config.raw_topic, MessageField.VALUE),
                        ),
                        on_delivery=self._on_delivery,
                    )
                    self._producer.poll(0)
                elif self._producer:
                    self._producer.produce(
                        topic=self.config.raw_topic,
                        key=event["event_id"].encode(),
                        value=json.dumps(event).encode(),
                        on_delivery=self._on_delivery,
                    )
                    self._producer.poll(0)
                else:
                    # Dry-run mode
                    pass

                if track:
                    self.tracker.record_produced(event["event_id"], produced_ns)
                self.produced += 1

            except Exception as exc:
                log.debug("Produce error: %s", exc)
                self.errors += 1

            seq += 1

        if self._producer:
            self._producer.flush(timeout=30)

    def _on_delivery(self, err, msg):
        if err:
            self.errors += 1


# ══════════════════════════════════════════════════════════════════════════════
# EVENT CONSUMER (latency measurement)
# ══════════════════════════════════════════════════════════════════════════════

class EventConsumer(threading.Thread):
    """
    Background thread that consumes from the clean topic.

    For each message it tries to look up the event_id in the LatencyTracker.
    If found (i.e. this was a sampled event), it records the end-to-end latency.
    """
    def __init__(self, config: BenchmarkConfig, tracker: LatencyTracker, group_id: str):
        super().__init__(daemon=True, name="consumer")
        self.config    = config
        self.tracker   = tracker
        self.group_id  = group_id
        self._stop_ev  = threading.Event()
        self.consumed  = 0
        self.errors    = 0
        self._consumer: Optional[Any] = None

    def _build_consumer(self):
        if not HAS_KAFKA:
            return
        self._consumer = Consumer({
            "bootstrap.servers":      self.config.bootstrap_servers,
            "group.id":               self.group_id,
            "auto.offset.reset":      "latest",   # only measure events produced during this run
            "enable.auto.commit":     True,
            "fetch.min.bytes":        1,
            "fetch.max.wait.ms":      100,
        })
        self._consumer.subscribe([self.config.clean_topic])

    def stop(self):
        self._stop_ev.set()

    def run(self):
        self._build_consumer()
        while not self._stop_ev.is_set():
            if not self._consumer:
                time.sleep(0.1)
                continue
            try:
                msg = self._consumer.poll(timeout=0.2)
                if msg is None:
                    continue
                if msg.error():
                    if msg.error().code() != KafkaError._PARTITION_EOF:
                        log.debug("Consumer error: %s", msg.error())
                    continue

                consumed_ns = time.monotonic_ns()
                # Decode event_id from value (JSON or Avro decoded to dict)
                try:
                    value   = json.loads(msg.value().decode())
                    eid     = value.get("event_id", "")
                    self.tracker.record_consumed(eid, consumed_ns)
                except Exception:
                    pass

                self.consumed += 1

            except Exception as exc:
                log.debug("Consumer loop error: %s", exc)
                self.errors += 1

        if self._consumer:
            self._consumer.close()


# ══════════════════════════════════════════════════════════════════════════════
# BENCHMARK RUNNER
# ══════════════════════════════════════════════════════════════════════════════

class BenchmarkRunner:
    """Orchestrates all phases and writes the results CSV."""

    def __init__(self, config: BenchmarkConfig, phases: List[Phase], skip_recovery: bool):
        self.config        = config
        self.phases        = phases
        self.skip_recovery = skip_recovery

        self.tracker  = LatencyTracker()
        self.flink    = FlinkClient(config.flink_rest_url)
        self.docker   = DockerStatsCollector(config.monitor_containers)
        self.lag_mon  = KafkaLagMonitor(
            config.bootstrap_servers,
            f"benchmark-{int(time.time())}",
            config.clean_topic,
        )

        run_ts            = datetime.now().strftime("%Y%m%d_%H%M%S")
        results_dir       = pathlib.Path(config.results_dir)
        results_dir.mkdir(parents=True, exist_ok=True)
        self.csv_path     = results_dir / f"benchmark_{run_ts}.csv"
        self.meta_path    = results_dir / f"benchmark_{run_ts}_meta.json"
        self._rows: List[MetricPoint] = []
        self._run_ts      = run_ts

        self._stop        = threading.Event()
        signal.signal(signal.SIGINT,  self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, sig, frame):
        log.warning("Signal %s received — stopping benchmark", sig)
        self._stop.set()

    # ─────────────────────────────────────────────────────────────────────────
    # Phase execution
    # ─────────────────────────────────────────────────────────────────────────

    def run(self):
        log.info("=" * 72)
        log.info("Benchmark start  ts=%s", self._run_ts)
        log.info("Results CSV : %s", self.csv_path)
        log.info("=" * 72)

        # Pre-flight: check Flink
        job_id = self.flink.first_running_job_id()
        if job_id:
            log.info("Flink job detected: %s", job_id)
        else:
            log.warning("No running Flink job — checkpoint metrics will be 0")

        # Shared consumer group for this entire run
        group_id = f"benchmark-{self._run_ts}"
        consumer = EventConsumer(self.config, self.tracker, group_id)
        consumer.start()
        time.sleep(2)   # let consumer join the topic before we start producing

        prev_produced = 0
        prev_consumed = 0
        prev_ts       = time.perf_counter()

        for phase in self.phases:
            if self._stop.is_set():
                break
            if phase.name == "recovery" and self.skip_recovery:
                log.info("Skipping recovery phase (--skip-recovery)")
                continue

            log.info("")
            log.info("─" * 72)
            log.info("Phase: %s  target=%d ev/s  duration=%ds  %s",
                     phase.name, phase.target_rate, phase.duration_sec, phase.description)
            log.info("─" * 72)

            # Create producer for this phase
            producer = EventProducer(
                self.config, self.tracker,
                rate=phase.target_rate,
                sample_rate=self.config.latency_sample_rate,
            )

            if phase.name == "recovery":
                prev_produced, prev_consumed, prev_ts = self._run_recovery_phase(
                    phase, producer, consumer, job_id,
                    prev_produced, prev_consumed, prev_ts,
                )
            else:
                prev_produced, prev_consumed, prev_ts = self._run_phase(
                    phase, producer, consumer, job_id,
                    prev_produced, prev_consumed, prev_ts,
                )

            # Stop the producer at end of phase
            producer.stop()
            producer.join(timeout=10)

        consumer.stop()
        consumer.join(timeout=10)

        self._write_csv()
        self._write_meta(job_id)
        self._print_summary()

    # ─────────────────────────────────────────────────────────────────────────
    # Normal phase
    # ─────────────────────────────────────────────────────────────────────────

    def _run_phase(
        self,
        phase: Phase,
        producer: EventProducer,
        consumer: EventConsumer,
        job_id: Optional[str],
        prev_produced: int,
        prev_consumed: int,
        prev_ts: float,
    ) -> Tuple[int, int, float]:
        producer.start()
        phase_start  = time.perf_counter()
        phase_end    = phase_start + phase.duration_sec

        while time.perf_counter() < phase_end and not self._stop.is_set():
            time.sleep(self.config.poll_interval_sec)
            row = self._collect_metrics(
                phase, producer, consumer, job_id,
                prev_produced, prev_consumed, prev_ts, phase_start,
            )
            self._rows.append(row)
            prev_produced = producer.produced
            prev_consumed = consumer.consumed
            prev_ts       = time.perf_counter()
            self._log_row(row)

        return prev_produced, prev_consumed, prev_ts

    # ─────────────────────────────────────────────────────────────────────────
    # Recovery phase — restart Flink, measure resumption time
    # ─────────────────────────────────────────────────────────────────────────

    def _run_recovery_phase(
        self,
        phase: Phase,
        producer: EventProducer,
        consumer: EventConsumer,
        job_id: Optional[str],
        prev_produced: int,
        prev_consumed: int,
        prev_ts: float,
    ) -> Tuple[int, int, float]:
        """
        Recovery sequence:
          1. Pause the producer (keep events queued in Kafka)
          2. Restart flink-jobmanager container
          3. Measure seconds until Flink reports a RUNNING job
          4. Resume the producer
          5. Run at recovery rate for phase.duration_sec
        """
        log.info("[recovery] Pausing producer and restarting flink-jobmanager...")
        producer.start()
        producer.pause()
        time.sleep(2)

        restart_start = time.perf_counter()

        # Restart the JobManager (Docker will restart TaskManagers automatically
        # if they use the same restart policy)
        try:
            subprocess.run(
                ["docker", "restart", "flink-jobmanager"],
                check=True, timeout=30,
            )
            log.info("[recovery] flink-jobmanager restarted")
        except Exception as exc:
            log.warning("[recovery] Could not restart flink-jobmanager: %s", exc)

        # Poll until Flink reports a running job
        log.info("[recovery] Waiting for Flink to come back online...")
        recovery_time_sec = None
        for _ in range(120):        # wait up to 2 minutes
            time.sleep(1)
            new_job_id = self.flink.first_running_job_id()
            if new_job_id:
                recovery_time_sec = time.perf_counter() - restart_start
                log.info(
                    "[recovery] Flink job RUNNING in %.1f s  job_id=%s",
                    recovery_time_sec, new_job_id,
                )
                job_id = new_job_id
                break
        else:
            log.warning("[recovery] Flink did not recover within 120 s")

        # Resume production
        producer.resume()

        phase_start = time.perf_counter()
        phase_end   = phase_start + phase.duration_sec

        # Record the recovery time as a special row
        if recovery_time_sec is not None:
            row = self._collect_metrics(
                phase, producer, consumer, job_id,
                prev_produced, prev_consumed, prev_ts, phase_start,
            )
            row.flink_job_status = f"RECOVERED_IN_{recovery_time_sec:.1f}s"
            self._rows.append(row)
            prev_produced = producer.produced
            prev_consumed = consumer.consumed
            prev_ts       = time.perf_counter()

        # Standard polling for the rest of the recovery phase
        while time.perf_counter() < phase_end and not self._stop.is_set():
            time.sleep(self.config.poll_interval_sec)
            row = self._collect_metrics(
                phase, producer, consumer, job_id,
                prev_produced, prev_consumed, prev_ts, phase_start,
            )
            self._rows.append(row)
            prev_produced = producer.produced
            prev_consumed = consumer.consumed
            prev_ts       = time.perf_counter()
            self._log_row(row)

        return prev_produced, prev_consumed, prev_ts

    # ─────────────────────────────────────────────────────────────────────────
    # Metrics collection
    # ─────────────────────────────────────────────────────────────────────────

    def _collect_metrics(
        self,
        phase: Phase,
        producer: EventProducer,
        consumer: EventConsumer,
        job_id: Optional[str],
        prev_produced: int,
        prev_consumed: int,
        prev_ts: float,
        phase_start: float,
    ) -> MetricPoint:
        now          = time.perf_counter()
        interval     = max(now - prev_ts, 0.001)
        elapsed      = now - phase_start

        produced_now = producer.produced
        consumed_now = consumer.consumed

        # Throughput
        produce_rate = (produced_now - prev_produced) / interval
        consume_rate = (consumed_now - prev_consumed) / interval

        # Latency
        samples = self.tracker.drain_samples()
        p50 = p95 = p99 = pmax = 0.0
        if samples:
            sorted_s = sorted(samples)
            n        = len(sorted_s)
            p50  = sorted_s[int(n * 0.50)]
            p95  = sorted_s[int(n * 0.95)]
            p99  = sorted_s[min(int(n * 0.99), n - 1)]
            pmax = sorted_s[-1]

        # Kafka lag
        lag = self.lag_mon.get_lag()

        # Flink metrics
        ckpt   = self.flink.checkpoint_info(job_id) if job_id else {}
        status = self.flink.job_status(job_id) if job_id else "NO_JOB"

        # Docker stats
        docker_stats = self.docker.collect()

        jm_stats  = docker_stats.get("flink-jobmanager", {})
        tm1_stats = docker_stats.get("flink-taskmanager-1", {})
        k1_stats  = docker_stats.get("kafka-1", {})

        return MetricPoint(
            ts                    = datetime.now(timezone.utc).isoformat(),
            phase                 = phase.name,
            elapsed_sec           = round(elapsed, 1),
            target_rate           = phase.target_rate,
            produced_total        = produced_now,
            consumed_total        = consumed_now,
            actual_produce_rate   = round(produce_rate, 1),
            actual_consume_rate   = round(consume_rate, 1),
            kafka_lag             = lag,
            latency_p50_ms        = round(p50, 2),
            latency_p95_ms        = round(p95, 2),
            latency_p99_ms        = round(p99, 2),
            latency_max_ms        = round(pmax, 2),
            latency_samples       = len(samples),
            checkpoint_duration_ms= ckpt.get("duration_ms", 0),
            checkpoint_size_mb    = ckpt.get("size_mb", 0.0),
            checkpoint_count      = ckpt.get("count", 0),
            flink_jm_cpu_pct      = jm_stats.get("cpu_pct", 0.0),
            flink_jm_mem_mb       = jm_stats.get("mem_mb", 0.0),
            flink_tm1_cpu_pct     = tm1_stats.get("cpu_pct", 0.0),
            flink_tm1_mem_mb      = tm1_stats.get("mem_mb", 0.0),
            kafka1_cpu_pct        = k1_stats.get("cpu_pct", 0.0),
            kafka1_mem_mb         = k1_stats.get("mem_mb", 0.0),
            flink_job_status      = status,
            errors                = producer.errors + consumer.errors,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Output helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _log_row(self, row: MetricPoint) -> None:
        lag_str = f"lag={row.kafka_lag:,}" if row.kafka_lag >= 0 else "lag=n/a"
        log.info(
            "[%s] rate=%6.0f/s  p50=%5.0fms  p95=%5.0fms  p99=%5.0fms  %s  ckpt=%dms  %s",
            row.phase,
            row.actual_produce_rate,
            row.latency_p50_ms,
            row.latency_p95_ms,
            row.latency_p99_ms,
            lag_str,
            row.checkpoint_duration_ms,
            row.flink_job_status,
        )

    def _write_csv(self) -> None:
        if not self._rows:
            log.warning("No data rows to write")
            return
        fields = list(asdict(self._rows[0]).keys())
        with open(self.csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for row in self._rows:
                writer.writerow(asdict(row))
        log.info("CSV written: %s  (%d rows)", self.csv_path, len(self._rows))

    def _write_meta(self, job_id: Optional[str]) -> None:
        meta = {
            "run_ts":     self._run_ts,
            "csv_path":   str(self.csv_path),
            "job_id":     job_id,
            "config":     asdict(BenchmarkConfig()),
            "phases":     [{"name": p.name, "target_rate": p.target_rate,
                            "duration_sec": p.duration_sec} for p in self.phases],
            "total_rows": len(self._rows),
        }
        self.meta_path.write_text(json.dumps(meta, indent=2))
        log.info("Meta written: %s", self.meta_path)

    def _print_summary(self) -> None:
        if not self._rows:
            return
        log.info("")
        log.info("=" * 72)
        log.info("BENCHMARK SUMMARY")
        log.info("=" * 72)
        log.info("%-16s  %10s  %8s  %8s  %8s  %8s",
                 "Phase", "Avg rate", "p50 ms", "p95 ms", "p99 ms", "Max lag")
        log.info("-" * 72)

        # Group rows by phase
        by_phase: Dict[str, List[MetricPoint]] = {}
        for row in self._rows:
            by_phase.setdefault(row.phase, []).append(row)

        for pname, rows in by_phase.items():
            rates  = [r.actual_produce_rate for r in rows if r.actual_produce_rate > 0]
            p50s   = [r.latency_p50_ms for r in rows if r.latency_p50_ms > 0]
            p95s   = [r.latency_p95_ms for r in rows if r.latency_p95_ms > 0]
            p99s   = [r.latency_p99_ms for r in rows if r.latency_p99_ms > 0]
            lags   = [r.kafka_lag for r in rows if r.kafka_lag >= 0]

            avg_rate = statistics.mean(rates) if rates else 0
            avg_p50  = statistics.mean(p50s)  if p50s  else 0
            avg_p95  = statistics.mean(p95s)  if p95s  else 0
            avg_p99  = statistics.mean(p99s)  if p99s  else 0
            max_lag  = max(lags) if lags else 0

            log.info("%-16s  %10.0f  %8.1f  %8.1f  %8.1f  %8d",
                     pname, avg_rate, avg_p50, avg_p95, avg_p99, max_lag)

        log.info("=" * 72)
        log.info("Run analyze_results.py to generate the full Markdown report:")
        log.info("  python tests/performance/analyze_results.py %s", self.csv_path)


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def main():
    # Defaults come from config/settings.py (APP_ENV=local|docker|prod).
    # CLI flags override them for ad-hoc runs without changing the env file.
    _defaults = BenchmarkConfig()

    parser = argparse.ArgumentParser(
        description="Flink ETL end-to-end performance benchmark",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            f"Active environment: APP_ENV={_cfg.env}\n"
            f"Config file loaded: config/{_cfg.env}.env\n\n"
        ) + (__doc__ or ""),
    )
    parser.add_argument("--bootstrap",
                        default=_defaults.bootstrap_servers,
                        help=f"Kafka bootstrap servers (default: {_defaults.bootstrap_servers})")
    parser.add_argument("--schema-registry",
                        default=_defaults.schema_registry_url,
                        help=f"Schema Registry URL (default: {_defaults.schema_registry_url})")
    parser.add_argument("--flink-url",
                        default=_defaults.flink_rest_url,
                        help=f"Flink REST API URL (default: {_defaults.flink_rest_url})")
    parser.add_argument("--results-dir",
                        default=_defaults.results_dir,
                        help="Output directory for CSV and meta files")
    parser.add_argument("--quick",         action="store_true",
                        help="Run short smoke-test phases instead of full benchmark")
    parser.add_argument("--skip-recovery", action="store_true",
                        help="Skip the Flink restart recovery phase")
    parser.add_argument("--dry-run",       action="store_true",
                        help="Generate events without Kafka (measures generator throughput only)")
    parser.add_argument("--poll-interval", type=int, default=5,
                        help="Metrics collection interval in seconds (default: 5)")
    args = parser.parse_args()

    if not HAS_KAFKA and not args.dry_run:
        log.error("confluent-kafka not installed: pip install confluent-kafka[avro]")
        log.error("Or run with --dry-run to test without Kafka.")
        sys.exit(1)

    if not HAS_REQUESTS:
        log.warning("requests not installed — Flink checkpoint metrics will be 0")

    config = BenchmarkConfig(
        bootstrap_servers   = args.bootstrap,
        schema_registry_url = args.schema_registry,
        flink_rest_url      = args.flink_url,
        results_dir         = args.results_dir,
        poll_interval_sec   = args.poll_interval,
    )

    log.info("Benchmark config: env=%s  kafka=%s  flink=%s",
             _cfg.env, config.bootstrap_servers, config.flink_rest_url)

    phases = QUICK_PHASES if args.quick else STANDARD_PHASES
    runner = BenchmarkRunner(config, phases, skip_recovery=args.skip_recovery)
    runner.run()


if __name__ == "__main__":
    main()
