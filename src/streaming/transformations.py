"""
Data Transformation Functions for E-Commerce Pipeline
Handles data cleansing, enrichment, and quality checks
"""

from pyspark.sql import DataFrame
from pyspark.sql.functions import (
    col, when, lit, current_timestamp, to_date, hour,
    expr, coalesce, trim, upper, regexp_replace, length
)
from typing import Tuple

from schema import (
    VALID_EVENT_TYPES, VALID_PAYMENT_MODES, VALID_CATEGORIES,
    QUANTITY_REQUIRED_EVENTS, PRICE_REQUIRED_EVENTS,
    ORDER_ID_REQUIRED_EVENTS, PAYMENT_MODE_REQUIRED_EVENTS
)


def add_metadata_columns(df: DataFrame) -> DataFrame:
    """
    Add metadata columns for Bronze layer
    
    Args:
        df: Input DataFrame from Kafka
        
    Returns:
        DataFrame with metadata columns
    """
    return df.select(
        col("value.event_id").alias("event_id"),
        col("value.user_id").alias("user_id"),
        col("value.product_id").alias("product_id"),
        col("value.category").alias("category"),
        col("value.order_id").alias("order_id"),
        col("value.event_type").alias("event_type"),
        col("value.quantity").alias("quantity"),
        col("value.price").alias("price"),
        col("value.payment_mode").alias("payment_mode"),
        col("value.event_time").alias("event_time"),
        col("timestamp").alias("ingestion_time"),
        col("partition").alias("kafka_partition"),
        col("offset").alias("kafka_offset"),
        current_timestamp().alias("processing_time")
    )


def clean_and_standardize(df: DataFrame) -> DataFrame:
    """
    Clean and standardize event data
    
    Args:
        df: Raw DataFrame
        
    Returns:
        Cleaned DataFrame
    """
    return df.select(
        # Clean string fields - trim and handle nulls
        trim(upper(col("event_id"))).alias("event_id"),
        trim(col("user_id")).alias("user_id"),
        trim(col("product_id")).alias("product_id"),
        
        # Standardize category - capitalize first letter
        when(col("category").isNotNull(), 
             expr("concat(upper(substring(category, 1, 1)), substring(category, 2))"))
        .alias("category"),
        
        # Clean order_id
        when(col("order_id").isNotNull(), trim(col("order_id")))
        .alias("order_id"),
        
        # Standardize event_type to lowercase
        when(col("event_type").isNotNull(), trim(lower(col("event_type"))))
        .alias("event_type"),
        
        # Ensure quantity is positive
        when(col("quantity").isNotNull() & (col("quantity") > 0), col("quantity"))
        .alias("quantity"),
        
        # Ensure price is positive and round to 2 decimals
        when(col("price").isNotNull() & (col("price") > 0), 
             expr("round(price, 2)"))
        .alias("price"),
        
        # Standardize payment_mode to lowercase
        when(col("payment_mode").isNotNull(), trim(lower(col("payment_mode"))))
        .alias("payment_mode"),
        
        # Keep timestamps as-is
        col("event_time"),
        col("ingestion_time"),
        col("kafka_partition"),
        col("kafka_offset"),
        col("processing_time")
    )


def add_validation_columns(df: DataFrame) -> DataFrame:
    """
    Add validation columns and partition keys for Silver layer
    
    Args:
        df: Cleaned DataFrame
        
    Returns:
        DataFrame with validation and partition columns
    """
    # Build validation logic
    validation_df = df.withColumn(
        "validation_errors",
        # Check event_type
        when(~col("event_type").isin(list(VALID_EVENT_TYPES)),
             concat(lit("Invalid event_type: "), col("event_type"), lit("; ")))
        .otherwise(lit(""))
        
        # Check quantity for events that require it
        .concat(
            when(col("event_type").isin(list(QUANTITY_REQUIRED_EVENTS)) & 
                 (col("quantity").isNull() | (col("quantity") <= 0)),
                 concat(lit("Missing/invalid quantity for "), col("event_type"), lit("; ")))
            .otherwise(lit(""))
        )
        
        # Check price for events that require it
        .concat(
            when(col("event_type").isin(list(PRICE_REQUIRED_EVENTS)) & 
                 (col("price").isNull() | (col("price") <= 0)),
                 concat(lit("Missing/invalid price for "), col("event_type"), lit("; ")))
            .otherwise(lit(""))
        )
        
        # Check order_id for events that require it
        .concat(
            when(col("event_type").isin(list(ORDER_ID_REQUIRED_EVENTS)) & 
                 col("order_id").isNull(),
                 concat(lit("Missing order_id for "), col("event_type"), lit("; ")))
            .otherwise(lit(""))
        )
        
        # Check payment_mode for events that require it
        .concat(
            when(col("event_type").isin(list(PAYMENT_MODE_REQUIRED_EVENTS)) & 
                 col("payment_mode").isNull(),
                 concat(lit("Missing payment_mode for "), col("event_type"), lit("; ")))
            .otherwise(lit(""))
        )
        
        # Check valid payment_mode
        .concat(
            when(col("payment_mode").isNotNull() & 
                 ~col("payment_mode").isin(list(VALID_PAYMENT_MODES)),
                 concat(lit("Invalid payment_mode: "), col("payment_mode"), lit("; ")))
            .otherwise(lit(""))
        )
        
        # Check valid category
        .concat(
            when(~col("category").isin(list(VALID_CATEGORIES)),
                 concat(lit("Invalid category: "), col("category"), lit("; ")))
            .otherwise(lit(""))
        )
    )
    
    # Determine if valid
    validation_df = validation_df.withColumn(
        "is_valid",
        when(col("validation_errors") == "", lit("Y")).otherwise(lit("N"))
    )
    
    # Clean up validation_errors
    validation_df = validation_df.withColumn(
        "validation_errors",
        when(col("validation_errors") == "", lit(None))
        .otherwise(regexp_replace(col("validation_errors"), "; $", ""))
    )
    
    # Add partition columns
    validation_df = validation_df \
        .withColumn("event_date", to_date(col("event_time"))) \
        .withColumn("event_hour", hour(col("event_time")))
    
    return validation_df


def enrich_events(df: DataFrame) -> DataFrame:
    """
    Enrich events with derived fields for analytics
    
    Args:
        df: Validated DataFrame
        
    Returns:
        Enriched DataFrame
    """
    return df \
        .withColumn("revenue", 
                   when(col("event_type").isin(["payment_success"]), 
                        col("price") * coalesce(col("quantity"), lit(1)))
                   .otherwise(lit(0.0))) \
        .withColumn("is_cart_action",
                   when(col("event_type").isin(["add_to_cart", "remove_from_cart"]), 
                        lit(True))
                   .otherwise(lit(False))) \
        .withColumn("is_order_action",
                   when(col("event_type").isin(["order_created", "order_cancelled"]), 
                        lit(True))
                   .otherwise(lit(False))) \
        .withColumn("is_payment_action",
                   when(col("event_type").isin(["payment_success", "payment_failed"]), 
                        lit(True))
                   .otherwise(lit(False))) \
        .withColumn("is_refund",
                   when(col("event_type") == "refund_processed", lit(True))
                   .otherwise(lit(False)))


def filter_valid_events(df: DataFrame) -> Tuple[DataFrame, DataFrame]:
    """
    Split DataFrame into valid and invalid events
    
    Args:
        df: DataFrame with validation columns
        
    Returns:
        Tuple of (valid_df, invalid_df)
    """
    valid_df = df.filter(col("is_valid") == "Y")
    invalid_df = df.filter(col("is_valid") == "N")
    
    return valid_df, invalid_df


def select_silver_columns(df: DataFrame) -> DataFrame:
    """
    Select columns for Silver layer storage
    
    Args:
        df: Enriched DataFrame
        
    Returns:
        DataFrame with Silver layer columns
    """
    return df.select(
        "event_id",
        "user_id",
        "product_id",
        "category",
        "order_id",
        "event_type",
        "quantity",
        "price",
        "payment_mode",
        "event_time",
        "event_date",
        "event_hour",
        "is_valid",
        "validation_errors"
    )


def prepare_for_druid(df: DataFrame) -> DataFrame:
    """
    Prepare aggregated data for Druid ingestion
    
    Args:
        df: Aggregated DataFrame
        
    Returns:
        DataFrame formatted for Druid
    """
    return df.select(
        col("window_start").alias("__time"),  # Druid timestamp column
        col("window_end"),
        col("event_type"),
        col("category"),
        col("payment_mode"),
        col("event_count"),
        col("unique_users"),
        col("unique_products"),
        col("total_quantity"),
        col("total_revenue"),
        col("avg_price")
    )


# Data quality check functions
def check_data_quality(df: DataFrame) -> dict:
    """
    Perform data quality checks and return metrics
    
    Args:
        df: DataFrame to check
        
    Returns:
        Dictionary with quality metrics
    """
    total_count = df.count()
    
    null_counts = {
        col_name: df.filter(col(col_name).isNull()).count()
        for col_name in df.columns
    }
    
    duplicate_count = df.count() - df.dropDuplicates(["event_id"]).count()
    
    return {
        "total_records": total_count,
        "null_counts": null_counts,
        "duplicate_count": duplicate_count,
        "null_percentage": {k: (v/total_count)*100 if total_count > 0 else 0 
                           for k, v in null_counts.items()}
    }


def apply_bronze_transformations(df: DataFrame) -> DataFrame:
    """
    Apply all transformations for Bronze layer
    
    Args:
        df: Raw Kafka DataFrame
        
    Returns:
        Transformed DataFrame for Bronze layer
    """
    return df \
        .transform(add_metadata_columns)


def apply_silver_transformations(df: DataFrame) -> Tuple[DataFrame, DataFrame]:
    """
    Apply all transformations for Silver layer
    
    Args:
        df: Bronze layer DataFrame
        
    Returns:
        Tuple of (valid_df, invalid_df)
    """
    cleaned_df = clean_and_standardize(df)
    validated_df = add_validation_columns(cleaned_df)
    enriched_df = enrich_events(validated_df)
    valid_df, invalid_df = filter_valid_events(enriched_df)
    
    return select_silver_columns(valid_df), invalid_df
