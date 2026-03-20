"""
Event Schema Definitions for E-Commerce Pipeline
Defines PySpark StructType schemas for event validation and processing
"""

from pyspark.sql.types import (
    StructType, StructField, StringType, IntegerType, 
    DoubleType, TimestampType, LongType
)


# Main event schema with all fields
EVENT_SCHEMA = StructType([
    StructField("event_id", StringType(), nullable=False),
    StructField("user_id", StringType(), nullable=False),
    StructField("product_id", StringType(), nullable=False),
    StructField("category", StringType(), nullable=False),
    StructField("order_id", StringType(), nullable=True),
    StructField("event_type", StringType(), nullable=False),
    StructField("quantity", IntegerType(), nullable=True),
    StructField("price", DoubleType(), nullable=True),
    StructField("payment_mode", StringType(), nullable=True),
    StructField("event_time", TimestampType(), nullable=False)
])


# Schema for Bronze layer (raw events with metadata)
BRONZE_SCHEMA = StructType([
    StructField("event_id", StringType(), nullable=False),
    StructField("user_id", StringType(), nullable=False),
    StructField("product_id", StringType(), nullable=False),
    StructField("category", StringType(), nullable=False),
    StructField("order_id", StringType(), nullable=True),
    StructField("event_type", StringType(), nullable=False),
    StructField("quantity", IntegerType(), nullable=True),
    StructField("price", DoubleType(), nullable=True),
    StructField("payment_mode", StringType(), nullable=True),
    StructField("event_time", TimestampType(), nullable=False),
    StructField("ingestion_time", TimestampType(), nullable=False),
    StructField("kafka_partition", IntegerType(), nullable=False),
    StructField("kafka_offset", LongType(), nullable=False),
    StructField("processing_time", TimestampType(), nullable=False)
])


# Schema for Silver layer (cleaned and validated)
SILVER_SCHEMA = StructType([
    StructField("event_id", StringType(), nullable=False),
    StructField("user_id", StringType(), nullable=False),
    StructField("product_id", StringType(), nullable=False),
    StructField("category", StringType(), nullable=False),
    StructField("order_id", StringType(), nullable=True),
    StructField("event_type", StringType(), nullable=False),
    StructField("quantity", IntegerType(), nullable=True),
    StructField("price", DoubleType(), nullable=True),
    StructField("payment_mode", StringType(), nullable=True),
    StructField("event_time", TimestampType(), nullable=False),
    StructField("event_date", StringType(), nullable=False),  # Partition key
    StructField("event_hour", IntegerType(), nullable=False),  # Partition key
    StructField("is_valid", StringType(), nullable=False),  # Y/N
    StructField("validation_errors", StringType(), nullable=True)
])


# Schema for Gold layer aggregations
GOLD_AGGREGATION_SCHEMA = StructType([
    StructField("window_start", TimestampType(), nullable=False),
    StructField("window_end", TimestampType(), nullable=False),
    StructField("event_type", StringType(), nullable=False),
    StructField("category", StringType(), nullable=False),
    StructField("event_count", LongType(), nullable=False),
    StructField("unique_users", LongType(), nullable=False),
    StructField("unique_products", LongType(), nullable=False),
    StructField("total_quantity", LongType(), nullable=True),
    StructField("total_revenue", DoubleType(), nullable=True),
    StructField("avg_price", DoubleType(), nullable=True),
    StructField("processing_time", TimestampType(), nullable=False)
])


# Valid event types
VALID_EVENT_TYPES = {
    'view_product',
    'add_to_cart',
    'remove_from_cart',
    'add_to_wishlist',
    'order_created',
    'payment_success',
    'payment_failed',
    'order_cancelled',
    'refund_processed'
}


# Event types that require quantity
QUANTITY_REQUIRED_EVENTS = {
    'add_to_cart',
    'remove_from_cart',
    'order_created'
}


# Event types that require price
PRICE_REQUIRED_EVENTS = {
    'add_to_cart',
    'order_created',
    'payment_success',
    'refund_processed'
}


# Event types that require order_id
ORDER_ID_REQUIRED_EVENTS = {
    'order_created',
    'payment_success',
    'payment_failed',
    'order_cancelled',
    'refund_processed'
}


# Event types that require payment_mode
PAYMENT_MODE_REQUIRED_EVENTS = {
    'payment_success',
    'payment_failed'
}


# Valid payment modes
VALID_PAYMENT_MODES = {
    'credit_card',
    'debit_card',
    'upi',
    'net_banking',
    'wallet',
    'cod',  # Cash on Delivery
    'emi'   # Equated Monthly Installment
}


# Valid categories (can be extended)
VALID_CATEGORIES = {
    'Electronics',
    'Clothing',
    'Home & Kitchen',
    'Books',
    'Sports',
    'Beauty',
    'Toys',
    'Groceries',
    'Automotive',
    'Health'
}


def validate_event(event_type: str, quantity: int, price: float, 
                   order_id: str, payment_mode: str) -> tuple[bool, str]:
    """
    Validate event based on business rules
    
    Returns:
        tuple: (is_valid: bool, error_message: str)
    """
    errors = []
    
    # Check event type
    if event_type not in VALID_EVENT_TYPES:
        errors.append(f"Invalid event_type: {event_type}")
    
    # Check quantity for events that require it
    if event_type in QUANTITY_REQUIRED_EVENTS and (quantity is None or quantity <= 0):
        errors.append(f"Missing or invalid quantity for {event_type}")
    
    # Check price for events that require it
    if event_type in PRICE_REQUIRED_EVENTS and (price is None or price <= 0):
        errors.append(f"Missing or invalid price for {event_type}")
    
    # Check order_id for events that require it
    if event_type in ORDER_ID_REQUIRED_EVENTS and not order_id:
        errors.append(f"Missing order_id for {event_type}")
    
    # Check payment_mode for events that require it
    if event_type in PAYMENT_MODE_REQUIRED_EVENTS:
        if not payment_mode:
            errors.append(f"Missing payment_mode for {event_type}")
        elif payment_mode not in VALID_PAYMENT_MODES:
            errors.append(f"Invalid payment_mode: {payment_mode}")
    
    is_valid = len(errors) == 0
    error_message = "; ".join(errors) if errors else None
    
    return is_valid, error_message


# Druid dimensions and metrics mapping
DRUID_DIMENSIONS = [
    "event_type",
    "category",
    "payment_mode",
    "event_date",
    "event_hour"
]

DRUID_METRICS = [
    {"name": "event_count", "type": "count"},
    {"name": "unique_users", "type": "hyperUnique", "fieldName": "user_id"},
    {"name": "unique_products", "type": "hyperUnique", "fieldName": "product_id"},
    {"name": "total_quantity", "type": "longSum", "fieldName": "quantity"},
    {"name": "total_revenue", "type": "doubleSum", "fieldName": "revenue"},
    {"name": "avg_price", "type": "doubleSum", "fieldName": "price"}
]
