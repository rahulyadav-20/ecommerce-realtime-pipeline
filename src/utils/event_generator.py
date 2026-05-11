#!/usr/bin/env python3
"""
event_generator.py — Synthetic e-commerce event producer for Kafka.

Simulates realistic user journeys through a purchase funnel:

  VIEW ──► ADD_TO_CART ──► ORDER_CREATED ──► PAYMENT_SUCCESS  (80 %)
                │                        └──► PAYMENT_FAILED   (20 %)
                │                              └──► ORDER_CANCELLED (30 % of failures)
                ├──► WISHLIST
                └──► (keep browsing)

Usage examples:
  # Default: 100 events/sec for 60 seconds
  python src/utils/event_generator.py

  # Stress test: 1 000 events/sec for 5 minutes
  python src/utils/event_generator.py --rate 1000 --duration 300

  # Dry-run: print JSON to stdout, no Kafka
  python src/utils/event_generator.py --rate 20 --duration 10 --dry-run

  # Custom broker / registry
  python src/utils/event_generator.py \\
      --bootstrap kafka-1:9092,kafka-2:9093 \\
      --schema-registry http://schema-registry:8081 \\
      --topic ecommerce.events.raw.v1 \\
      --rate 500 --duration 0          # 0 = run forever

Validation:
  After running, open Kafka UI at http://localhost:8085 and check the
  topic 'ecommerce.events.raw.v1' — at 100 ev/s for 60 s ≈ 6 000 messages.
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import random
import signal
import sys
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

# ── Config (project root → config/) ──────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from config.settings import cfg  # noqa: E402

# ---------------------------------------------------------------------------
# Optional confluent-kafka import (skipped in --dry-run with missing lib)
# ---------------------------------------------------------------------------
try:
    from confluent_kafka import Producer
    from confluent_kafka.schema_registry import SchemaRegistryClient
    from confluent_kafka.schema_registry.avro import AvroSerializer
    from confluent_kafka.serialization import MessageField, SerializationContext
    CONFLUENT_AVAILABLE = True
except ImportError:
    CONFLUENT_AVAILABLE = False

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("event_generator")

# =============================================================================
# Product catalogue — 10 categories × 10 SKUs = 100 products
# =============================================================================
CATALOGUE: dict[str, dict] = {
    "electronics": {
        "price_range": (500, 50_000),
        "skus": [f"ELEC-{i:03d}" for i in range(1, 11)],
        "names": [
            "Smartphone", "Laptop", "Tablet", "Smart TV", "Headphones",
            "Camera", "Smartwatch", "Speaker", "Gaming Console", "Drone",
        ],
    },
    "fashion": {
        "price_range": (200, 5_000),
        "skus": [f"FASH-{i:03d}" for i in range(1, 11)],
        "names": [
            "T-Shirt", "Jeans", "Dress", "Jacket", "Shoes",
            "Handbag", "Sunglasses", "Watch", "Belt", "Scarf",
        ],
    },
    "home_kitchen": {
        "price_range": (300, 15_000),
        "skus": [f"HOME-{i:03d}" for i in range(1, 11)],
        "names": [
            "Mixer", "Microwave", "Vacuum Cleaner", "Air Purifier", "Coffee Maker",
            "Toaster", "Juicer", "Rice Cooker", "Blender", "Induction Cooktop",
        ],
    },
    "books": {
        "price_range": (100, 2_000),
        "skus": [f"BOOK-{i:03d}" for i in range(1, 11)],
        "names": [
            "Fiction Novel", "Self-Help Guide", "Tech Manual", "Cookbook", "Biography",
            "Science Book", "History Book", "Children Book", "Art Book", "Travel Guide",
        ],
    },
    "sports_fitness": {
        "price_range": (500, 20_000),
        "skus": [f"SPRT-{i:03d}" for i in range(1, 11)],
        "names": [
            "Yoga Mat", "Dumbbells", "Treadmill", "Cycling Gloves", "Protein Powder",
            "Running Shoes", "Resistance Bands", "Skipping Rope", "Football", "Cricket Bat",
        ],
    },
    "beauty": {
        "price_range": (100, 3_000),
        "skus": [f"BEAU-{i:03d}" for i in range(1, 11)],
        "names": [
            "Face Cream", "Perfume", "Hair Serum", "Foundation", "Lipstick",
            "Eye Shadow", "Nail Polish", "Sunscreen", "Body Lotion", "Shampoo",
        ],
    },
    "toys_games": {
        "price_range": (200, 5_000),
        "skus": [f"TOYS-{i:03d}" for i in range(1, 11)],
        "names": [
            "Building Blocks", "Board Game", "Puzzle", "Action Figure", "Remote Car",
            "Doll House", "Science Kit", "Art Set", "Play Tent", "Musical Toy",
        ],
    },
    "automotive": {
        "price_range": (500, 25_000),
        "skus": [f"AUTO-{i:03d}" for i in range(1, 11)],
        "names": [
            "Car Charger", "Dash Cam", "Car Cover", "Tyre Inflator", "Jump Starter",
            "Car Vacuum", "Seat Cover", "GPS Tracker", "Wiper Blades", "Car Polish",
        ],
    },
    "food_grocery": {
        "price_range": (50, 1_000),
        "skus": [f"FOOD-{i:03d}" for i in range(1, 11)],
        "names": [
            "Olive Oil", "Pasta", "Organic Honey", "Nuts Mix", "Green Tea",
            "Protein Bar", "Instant Coffee", "Quinoa", "Dark Chocolate", "Spice Kit",
        ],
    },
    "health_wellness": {
        "price_range": (100, 5_000),
        "skus": [f"HLTH-{i:03d}" for i in range(1, 11)],
        "names": [
            "Multivitamins", "Fish Oil", "Glucose Monitor", "BP Monitor", "Massage Gun",
            "Thermometer", "Pulse Oximeter", "Sleep Aid", "Probiotics", "Collagen",
        ],
    },
}

PAYMENT_MODES = [
    "credit_card", "debit_card", "upi", "wallet",
    "net_banking", "emi", "cash_on_delivery",
]
DEVICES        = ["web",  "android", "ios"]
DEVICE_WEIGHTS = [0.45,   0.35,      0.20]

# Realistic-looking fake IP prefixes
_IP_FIRST_OCTETS = ["10", "192", "172", "203", "45", "103", "117", "49"]


# =============================================================================
# Session state machine
# =============================================================================

class State(str, Enum):
    BROWSING   = "BROWSING"    # viewing products
    CARTED     = "CARTED"      # added to cart
    ORDERING   = "ORDERING"    # placed order, awaiting payment
    DONE       = "DONE"        # terminal — payment succeeded or order cancelled


@dataclass
class Session:
    session_id:    str
    user_id:       Optional[str]
    device:        str
    ip:            str
    category:      str
    product_id:    str
    price:         float
    payment_mode:  str
    state:         State = State.BROWSING
    views:         int   = 0
    born_at:       float = field(default_factory=time.monotonic)

    @property
    def is_terminal(self) -> bool:
        return self.state == State.DONE


def _spawn_session() -> Session:
    """Create a fresh session with a random product and user profile."""
    cat   = random.choice(list(CATALOGUE))
    cat_d = CATALOGUE[cat]
    lo, hi = cat_d["price_range"]
    sku_idx = random.randrange(len(cat_d["skus"]))
    return Session(
        session_id   = str(uuid.uuid4()),
        user_id      = f"USR-{random.randint(1, 10_000):05d}" if random.random() < 0.70 else None,
        device       = random.choices(DEVICES, weights=DEVICE_WEIGHTS)[0],
        ip           = (
            f"{random.choice(_IP_FIRST_OCTETS)}"
            f".{random.randint(1, 254)}"
            f".{random.randint(1, 254)}"
            f".{random.randint(1, 254)}"
        ),
        category     = cat,
        product_id   = cat_d["skus"][sku_idx],
        price        = round(random.uniform(lo, hi), 2),
        payment_mode = random.choice(PAYMENT_MODES),
    )


def _advance(session: Session) -> dict[str, Any]:
    """
    Advance the session one step and return the Avro-compatible event dict.
    Nullable fields must be Python None (serialised as Avro null).
    """
    now_ms = int(time.time() * 1_000)

    def evt(event_type: str, **overrides) -> dict[str, Any]:
        return {
            "event_id":     str(uuid.uuid4()),
            "session_id":   session.session_id,
            "event_time":   now_ms,
            "event_type":   event_type,
            "device":       session.device,
            "ip":           session.ip,
            "user_id":      session.user_id,
            "product_id":   session.product_id,
            "category":     session.category,
            "quantity":     None,
            "price":        None,
            "payment_mode": None,
            **overrides,
        }

    # ── BROWSING ──────────────────────────────────────────────────────────────
    if session.state == State.BROWSING:
        session.views += 1
        if session.views < random.randint(1, 4):
            return evt("VIEW")
        roll = random.random()
        if roll < 0.60:
            session.state = State.CARTED
            return evt("ADD_TO_CART", quantity=random.randint(1, 3), price=session.price)
        if roll < 0.75:
            return evt("WISHLIST")
        # stay browsing a bit longer
        return evt("VIEW")

    # ── CARTED ────────────────────────────────────────────────────────────────
    if session.state == State.CARTED:
        roll = random.random()
        if roll < 0.70:
            session.state = State.ORDERING
            return evt(
                "ORDER_CREATED",
                quantity=random.randint(1, 3),
                price=session.price,
                payment_mode=session.payment_mode,
            )
        if roll < 0.85:
            session.state = State.DONE      # cart abandoned
            return evt("REMOVE_CART")
        return evt("VIEW")                  # reconsidering

    # ── ORDERING — payment resolution ─────────────────────────────────────────
    if session.state == State.ORDERING:
        session.state = State.DONE
        if random.random() < 0.80:          # 80 % success
            return evt("PAYMENT_SUCCESS", price=session.price, payment_mode=session.payment_mode)
        # 20 % failure — 30 % of those also cancel the order
        if random.random() < 0.30:
            return evt("ORDER_CANCELLED")
        return evt("PAYMENT_FAILED", payment_mode=session.payment_mode)

    # Fallback (DONE state — caller must recycle before calling again)
    return evt("VIEW")


# =============================================================================
# Metrics
# =============================================================================

@dataclass
class Metrics:
    produced:   int = 0
    errors:     int = 0
    start_time: float = field(default_factory=time.monotonic)

    def rate(self) -> float:
        elapsed = time.monotonic() - self.start_time
        return self.produced / elapsed if elapsed > 0 else 0.0

    def log(self) -> None:
        log.info(
            "produced=%d errors=%d rate=%.1f ev/s",
            self.produced, self.errors, self.rate(),
        )


# =============================================================================
# Kafka producer helpers
# =============================================================================

def _load_schema(schema_path: pathlib.Path) -> str:
    with open(schema_path) as f:
        return f.read()


def _build_producer(args: argparse.Namespace, schema_str: str):
    """Return (producer, avro_serializer) or (None, None) for dry-run."""
    if args.dry_run:
        return None, None

    sr_client = SchemaRegistryClient({"url": args.schema_registry})
    avro_ser  = AvroSerializer(
        schema_registry_client = sr_client,
        schema_str             = schema_str,
        to_dict                = lambda obj, _ctx: obj,   # already a dict
    )
    producer = Producer({
        "bootstrap.servers":        args.bootstrap,
        "acks":                     "all",
        "enable.idempotence":       True,
        "compression.type":         "zstd",
        "linger.ms":                5,
        "batch.size":               65_536,
        "queue.buffering.max.messages": 100_000,
    })
    return producer, avro_ser


def _delivery_report(err, msg) -> None:
    if err:
        log.warning("Delivery failed | topic=%s partition=%s error=%s",
                    msg.topic(), msg.partition(), err)


def _produce(
    producer,
    avro_ser,
    topic: str,
    event: dict[str, Any],
    metrics: Metrics,
    dry_run: bool,
) -> None:
    if dry_run:
        print(json.dumps(event, default=str))
        metrics.produced += 1
        return
    try:
        key = (event.get("user_id") or event["session_id"]).encode()
        value = avro_ser(
            event,
            SerializationContext(topic, MessageField.VALUE),
        )
        producer.produce(topic=topic, key=key, value=value, on_delivery=_delivery_report)
        metrics.produced += 1
    except Exception as exc:  # noqa: BLE001
        log.error("Produce error: %s", exc)
        metrics.errors += 1


# =============================================================================
# Main generator loop
# =============================================================================

def run(args: argparse.Namespace) -> None:
    # Locate schema file relative to repo root
    repo_root   = pathlib.Path(__file__).parents[2]
    schema_path = repo_root / "schemas" / "clickstream-event.avsc"

    if not schema_path.exists():
        log.error("Schema file not found: %s", schema_path)
        sys.exit(1)

    schema_str = _load_schema(schema_path)

    if not args.dry_run and not CONFLUENT_AVAILABLE:
        log.error(
            "confluent-kafka is not installed. "
            "Run: pip install -r requirements.txt   or use --dry-run"
        )
        sys.exit(1)

    producer, avro_ser = _build_producer(args, schema_str)
    metrics   = Metrics()
    sessions: list[Session] = [_spawn_session() for _ in range(args.sessions)]

    # Graceful shutdown on SIGINT / SIGTERM
    _stop = False

    def _handle_signal(sig, _frame):
        nonlocal _stop
        log.info("Shutdown signal received, finishing up …")
        _stop = True

    signal.signal(signal.SIGINT,  _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    log.info(
        "Starting generator | rate=%d ev/s | duration=%ss | sessions=%d | topic=%s | dry_run=%s",
        args.rate, args.duration or "∞", args.sessions, args.topic, args.dry_run,
    )

    deadline    = time.monotonic() + args.duration if args.duration else float("inf")
    tick_delay  = 1.0 / args.rate              # seconds between events
    last_log_at = time.monotonic()

    while not _stop and time.monotonic() < deadline:
        tick_start = time.monotonic()

        # Pick a random active session; recycle terminal ones
        idx     = random.randrange(len(sessions))
        session = sessions[idx]
        if session.is_terminal:
            sessions[idx] = _spawn_session()
            session       = sessions[idx]

        event = _advance(session)
        _produce(producer, avro_ser, args.topic, event, metrics, args.dry_run)

        # Periodic flush & log (every 5 s)
        if not args.dry_run and producer and metrics.produced % 500 == 0:
            producer.poll(0)

        now = time.monotonic()
        if now - last_log_at >= 5.0:
            metrics.log()
            last_log_at = now

        # Rate-limit
        elapsed = time.monotonic() - tick_start
        sleep_for = tick_delay - elapsed
        if sleep_for > 0:
            time.sleep(sleep_for)

    # Flush remaining messages
    if not args.dry_run and producer:
        log.info("Flushing producer …")
        producer.flush(timeout=30)

    log.info("=== Done ===")
    metrics.log()
    log.info(
        "Total produced: %d | Errors: %d | Elapsed: %.1fs",
        metrics.produced,
        metrics.errors,
        time.monotonic() - metrics.start_time,
    )


# =============================================================================
# CLI
# =============================================================================

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Synthetic e-commerce event producer for Kafka.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--rate", type=int, default=100, metavar="N",
        help="Target events per second.",
    )
    p.add_argument(
        "--duration", type=int, default=60, metavar="SECS",
        help="Run for N seconds. 0 = run until Ctrl-C.",
    )
    p.add_argument(
        "--topic", default=cfg.kafka.raw_topic,
        help=f"Kafka topic to produce to (default: {cfg.kafka.raw_topic}).",
    )
    p.add_argument(
        "--bootstrap", default=cfg.kafka.bootstrap_servers,
        help=f"Kafka bootstrap servers (default: {cfg.kafka.bootstrap_servers}).",
    )
    p.add_argument(
        "--schema-registry", default=cfg.kafka.schema_registry_url,
        dest="schema_registry",
        help=f"Confluent Schema Registry URL (default: {cfg.kafka.schema_registry_url}).",
    )
    p.add_argument(
        "--sessions", type=int, default=50, metavar="N",
        help="Number of concurrent simulated user sessions.",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Print JSON events to stdout instead of producing to Kafka.",
    )
    return p.parse_args()


if __name__ == "__main__":
    run(_parse_args())
