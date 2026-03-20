"""
Window Aggregation Logic for Real-Time Metrics
Calculates business KPIs over sliding windows
"""

from pyspark.sql import DataFrame
from pyspark.sql.functions import (
    window, col, count, countDistinct, sum as spark_sum,
    avg, current_timestamp, when, lit, expr
)
from pyspark.sql.types import StructType, StructField, StringType, LongType, DoubleType, TimestampType


def create_5min_aggregations(df: DataFrame, watermark_delay: str = "10 minutes") -> DataFrame:
    """
    Create 5-minute window aggregations for all metrics
    
    Args:
        df: Input DataFrame with events
        watermark_delay: Watermark delay for late data
        
    Returns:
        Aggregated DataFrame
    """
    # Set watermark for late data handling
    watermarked_df = df.withWatermark("event_time", watermark_delay)
    
    # Perform windowed aggregations
    aggregated = watermarked_df \
        .groupBy(
            window(col("event_time"), "5 minutes"),
            col("event_type"),
            col("category")
        ) \
        .agg(
            count("*").alias("event_count"),
            countDistinct("user_id").alias("unique_users"),
            countDistinct("product_id").alias("unique_products"),
            spark_sum("quantity").alias("total_quantity"),
            spark_sum(when(col("event_type") == "payment_success", 
                          col("price") * col("quantity")).otherwise(0)).alias("total_revenue"),
            avg("price").alias("avg_price")
        ) \
        .select(
            col("window.start").alias("window_start"),
            col("window.end").alias("window_end"),
            col("event_type"),
            col("category"),
            col("event_count"),
            col("unique_users"),
            col("unique_products"),
            col("total_quantity"),
            col("total_revenue"),
            col("avg_price"),
            current_timestamp().alias("processing_time")
        )
    
    return aggregated


def create_product_metrics(df: DataFrame, watermark_delay: str = "10 minutes") -> DataFrame:
    """
    Create product-level metrics
    
    Args:
        df: Input DataFrame with events
        watermark_delay: Watermark delay
        
    Returns:
        Product metrics DataFrame
    """
    watermarked_df = df.withWatermark("event_time", watermark_delay)
    
    product_metrics = watermarked_df \
        .groupBy(
            window(col("event_time"), "5 minutes"),
            col("product_id"),
            col("category")
        ) \
        .agg(
            count(when(col("event_type") == "view_product", 1)).alias("view_count"),
            count(when(col("event_type") == "add_to_cart", 1)).alias("add_to_cart_count"),
            count(when(col("event_type") == "remove_from_cart", 1)).alias("remove_from_cart_count"),
            count(when(col("event_type") == "add_to_wishlist", 1)).alias("wishlist_count"),
            count(when(col("event_type") == "order_created", 1)).alias("order_count"),
            countDistinct("user_id").alias("unique_viewers"),
            spark_sum(when(col("event_type") == "payment_success", 
                          col("price") * col("quantity")).otherwise(0)).alias("revenue")
        ) \
        .select(
            col("window.start").alias("window_start"),
            col("window.end").alias("window_end"),
            col("product_id"),
            col("category"),
            col("view_count"),
            col("add_to_cart_count"),
            col("remove_from_cart_count"),
            col("wishlist_count"),
            col("order_count"),
            col("unique_viewers"),
            col("revenue"),
            # Calculate conversion rate
            (col("order_count") / col("view_count") * 100).alias("view_to_order_conversion_pct"),
            current_timestamp().alias("processing_time")
        )
    
    return product_metrics


def create_user_metrics(df: DataFrame, watermark_delay: str = "10 minutes") -> DataFrame:
    """
    Create user-level metrics
    
    Args:
        df: Input DataFrame with events
        watermark_delay: Watermark delay
        
    Returns:
        User metrics DataFrame
    """
    watermarked_df = df.withWatermark("event_time", watermark_delay)
    
    user_metrics = watermarked_df \
        .groupBy(
            window(col("event_time"), "5 minutes"),
            col("user_id")
        ) \
        .agg(
            count("*").alias("total_events"),
            count(when(col("event_type") == "view_product", 1)).alias("products_viewed"),
            count(when(col("event_type") == "add_to_cart", 1)).alias("items_added_to_cart"),
            count(when(col("event_type") == "order_created", 1)).alias("orders_placed"),
            spark_sum(when(col("event_type") == "payment_success", 
                          col("price") * col("quantity")).otherwise(0)).alias("total_spent"),
            countDistinct("category").alias("categories_browsed")
        ) \
        .select(
            col("window.start").alias("window_start"),
            col("window.end").alias("window_end"),
            col("user_id"),
            col("total_events"),
            col("products_viewed"),
            col("items_added_to_cart"),
            col("orders_placed"),
            col("total_spent"),
            col("categories_browsed"),
            current_timestamp().alias("processing_time")
        )
    
    return user_metrics


def create_payment_metrics(df: DataFrame, watermark_delay: str = "10 minutes") -> DataFrame:
    """
    Create payment-specific metrics
    
    Args:
        df: Input DataFrame with events
        watermark_delay: Watermark delay
        
    Returns:
        Payment metrics DataFrame
    """
    payment_df = df.filter(
        col("event_type").isin(["payment_success", "payment_failed"])
    )
    
    watermarked_df = payment_df.withWatermark("event_time", watermark_delay)
    
    payment_metrics = watermarked_df \
        .groupBy(
            window(col("event_time"), "5 minutes"),
            col("payment_mode")
        ) \
        .agg(
            count(when(col("event_type") == "payment_success", 1)).alias("success_count"),
            count(when(col("event_type") == "payment_failed", 1)).alias("failure_count"),
            count("*").alias("total_attempts"),
            spark_sum(when(col("event_type") == "payment_success", 
                          col("price") * col("quantity")).otherwise(0)).alias("successful_revenue"),
            avg(when(col("event_type") == "payment_success", 
                    col("price") * col("quantity"))).alias("avg_transaction_value")
        ) \
        .select(
            col("window.start").alias("window_start"),
            col("window.end").alias("window_end"),
            col("payment_mode"),
            col("success_count"),
            col("failure_count"),
            col("total_attempts"),
            col("successful_revenue"),
            col("avg_transaction_value"),
            # Calculate success rate
            (col("success_count") / col("total_attempts") * 100).alias("success_rate_pct"),
            current_timestamp().alias("processing_time")
        )
    
    return payment_metrics


def create_category_metrics(df: DataFrame, watermark_delay: str = "10 minutes") -> DataFrame:
    """
    Create category-level metrics
    
    Args:
        df: Input DataFrame with events
        watermark_delay: Watermark delay
        
    Returns:
        Category metrics DataFrame
    """
    watermarked_df = df.withWatermark("event_time", watermark_delay)
    
    category_metrics = watermarked_df \
        .groupBy(
            window(col("event_time"), "5 minutes"),
            col("category")
        ) \
        .agg(
            count(when(col("event_type") == "view_product", 1)).alias("total_views"),
            count(when(col("event_type") == "add_to_cart", 1)).alias("cart_additions"),
            count(when(col("event_type") == "order_created", 1)).alias("orders"),
            count(when(col("event_type") == "payment_success", 1)).alias("successful_payments"),
            countDistinct("user_id").alias("unique_customers"),
            countDistinct("product_id").alias("unique_products_interacted"),
            spark_sum(when(col("event_type") == "payment_success", 
                          col("price") * col("quantity")).otherwise(0)).alias("revenue"),
            avg(when(col("event_type").isin(["payment_success", "order_created"]), 
                    col("price"))).alias("avg_product_price")
        ) \
        .select(
            col("window.start").alias("window_start"),
            col("window.end").alias("window_end"),
            col("category"),
            col("total_views"),
            col("cart_additions"),
            col("orders"),
            col("successful_payments"),
            col("unique_customers"),
            col("unique_products_interacted"),
            col("revenue"),
            col("avg_product_price"),
            # Calculate funnel metrics
            (col("cart_additions") / col("total_views") * 100).alias("view_to_cart_conversion_pct"),
            (col("orders") / col("cart_additions") * 100).alias("cart_to_order_conversion_pct"),
            (col("successful_payments") / col("orders") * 100).alias("payment_success_rate_pct"),
            current_timestamp().alias("processing_time")
        )
    
    return category_metrics


def create_order_metrics(df: DataFrame, watermark_delay: str = "10 minutes") -> DataFrame:
    """
    Create order-level metrics (for orders with order_id)
    
    Args:
        df: Input DataFrame with events
        watermark_delay: Watermark delay
        
    Returns:
        Order metrics DataFrame
    """
    order_df = df.filter(
        col("order_id").isNotNull()
    )
    
    watermarked_df = order_df.withWatermark("event_time", watermark_delay)
    
    order_metrics = watermarked_df \
        .groupBy(
            window(col("event_time"), "5 minutes"),
            col("category")
        ) \
        .agg(
            count(when(col("event_type") == "order_created", 1)).alias("orders_created"),
            count(when(col("event_type") == "order_cancelled", 1)).alias("orders_cancelled"),
            count(when(col("event_type") == "payment_success", 1)).alias("orders_paid"),
            count(when(col("event_type") == "payment_failed", 1)).alias("payment_failures"),
            count(when(col("event_type") == "refund_processed", 1)).alias("refunds"),
            spark_sum(when(col("event_type") == "payment_success", 
                          col("price") * col("quantity")).otherwise(0)).alias("gross_revenue"),
            spark_sum(when(col("event_type") == "refund_processed", 
                          col("price") * col("quantity")).otherwise(0)).alias("refund_amount")
        ) \
        .select(
            col("window.start").alias("window_start"),
            col("window.end").alias("window_end"),
            col("category"),
            col("orders_created"),
            col("orders_cancelled"),
            col("orders_paid"),
            col("payment_failures"),
            col("refunds"),
            col("gross_revenue"),
            col("refund_amount"),
            # Calculate net revenue
            (col("gross_revenue") - col("refund_amount")).alias("net_revenue"),
            # Calculate cancellation rate
            (col("orders_cancelled") / col("orders_created") * 100).alias("cancellation_rate_pct"),
            # Calculate refund rate
            (col("refunds") / col("orders_paid") * 100).alias("refund_rate_pct"),
            current_timestamp().alias("processing_time")
        )
    
    return order_metrics


def create_realtime_kpi_summary(df: DataFrame, watermark_delay: str = "10 minutes") -> DataFrame:
    """
    Create high-level KPI summary for dashboards
    
    Args:
        df: Input DataFrame with events
        watermark_delay: Watermark delay
        
    Returns:
        KPI summary DataFrame
    """
    watermarked_df = df.withWatermark("event_time", watermark_delay)
    
    kpi_summary = watermarked_df \
        .groupBy(window(col("event_time"), "5 minutes")) \
        .agg(
            count("*").alias("total_events"),
            countDistinct("user_id").alias("active_users"),
            countDistinct("product_id").alias("products_interacted"),
            count(when(col("event_type") == "view_product", 1)).alias("product_views"),
            count(when(col("event_type") == "add_to_cart", 1)).alias("cart_additions"),
            count(when(col("event_type") == "order_created", 1)).alias("orders_created"),
            count(when(col("event_type") == "payment_success", 1)).alias("successful_payments"),
            count(when(col("event_type") == "payment_failed", 1)).alias("failed_payments"),
            count(when(col("event_type") == "order_cancelled", 1)).alias("cancelled_orders"),
            count(when(col("event_type") == "refund_processed", 1)).alias("refunds"),
            spark_sum(when(col("event_type") == "payment_success", 
                          col("price") * col("quantity")).otherwise(0)).alias("total_revenue"),
            spark_sum(when(col("event_type") == "refund_processed", 
                          col("price") * col("quantity")).otherwise(0)).alias("refund_amount"),
            avg(when(col("event_type") == "payment_success", 
                    col("price") * col("quantity"))).alias("avg_order_value")
        ) \
        .select(
            col("window.start").alias("window_start"),
            col("window.end").alias("window_end"),
            col("total_events"),
            col("active_users"),
            col("products_interacted"),
            col("product_views"),
            col("cart_additions"),
            col("orders_created"),
            col("successful_payments"),
            col("failed_payments"),
            col("cancelled_orders"),
            col("refunds"),
            col("total_revenue"),
            col("refund_amount"),
            (col("total_revenue") - col("refund_amount")).alias("net_revenue"),
            col("avg_order_value"),
            # Calculate conversion metrics
            (col("cart_additions") / col("product_views") * 100).alias("view_to_cart_pct"),
            (col("orders_created") / col("cart_additions") * 100).alias("cart_to_order_pct"),
            (col("successful_payments") / col("orders_created") * 100).alias("order_to_payment_pct"),
            # Calculate failure rates
            (col("failed_payments") / (col("successful_payments") + col("failed_payments")) * 100).alias("payment_failure_rate_pct"),
            (col("cancelled_orders") / col("orders_created") * 100).alias("order_cancellation_rate_pct"),
            current_timestamp().alias("processing_time")
        )
    
    return kpi_summary
