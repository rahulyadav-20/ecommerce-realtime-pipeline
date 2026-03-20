"""
Kafka Producer for Test Data Generation
Generates realistic e-commerce events for testing
"""

import json
import random
import time
import uuid
from datetime import datetime, timedelta
from typing import Dict, List
from kafka import KafkaProducer
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class EcommerceEventGenerator:
    """Generate realistic e-commerce events"""
    
    CATEGORIES = [
        'Electronics', 'Clothing', 'Home & Kitchen', 'Books',
        'Sports', 'Beauty', 'Toys', 'Groceries', 'Automotive', 'Health'
    ]
    
    PAYMENT_MODES = [
        'credit_card', 'debit_card', 'upi', 'net_banking', 
        'wallet', 'cod', 'emi'
    ]
    
    EVENT_TYPES = [
        'view_product',
        'add_to_cart',
        'remove_from_cart',
        'add_to_wishlist',
        'order_created',
        'payment_success',
        'payment_failed',
        'order_cancelled',
        'refund_processed'
    ]
    
    # Event type weights for realistic distribution
    EVENT_WEIGHTS = {
        'view_product': 50,
        'add_to_cart': 20,
        'remove_from_cart': 5,
        'add_to_wishlist': 10,
        'order_created': 8,
        'payment_success': 6,
        'payment_failed': 0.5,
        'order_cancelled': 0.3,
        'refund_processed': 0.2
    }
    
    def __init__(self, num_users=1000, num_products=5000):
        self.user_ids = [f"user_{i:06d}" for i in range(num_users)]
        self.product_ids = [f"prod_{i:06d}" for i in range(num_products)]
        self.active_carts = {}  # Track active cart items
        self.active_orders = {}  # Track orders for payment/cancellation
        
    def generate_event(self) -> Dict:
        """Generate a single realistic event"""
        
        # Choose event type based on weights
        event_type = random.choices(
            list(self.EVENT_WEIGHTS.keys()),
            weights=list(self.EVENT_WEIGHTS.values())
        )[0]
        
        user_id = random.choice(self.user_ids)
        product_id = random.choice(self.product_ids)
        category = random.choice(self.CATEGORIES)
        
        event = {
            'event_id': f"evt_{uuid.uuid4().hex[:12]}",
            'user_id': user_id,
            'product_id': product_id,
            'category': category,
            'event_type': event_type,
            'event_time': datetime.utcnow().isoformat() + 'Z'
        }
        
        # Add event-specific fields
        if event_type in ['view_product', 'add_to_wishlist']:
            # Simple events - no additional fields needed
            pass
            
        elif event_type == 'add_to_cart':
            quantity = random.randint(1, 5)
            price = round(random.uniform(10.0, 1000.0), 2)
            event['quantity'] = quantity
            event['price'] = price
            
            # Track cart item
            cart_key = f"{user_id}_{product_id}"
            self.active_carts[cart_key] = {'quantity': quantity, 'price': price}
            
        elif event_type == 'remove_from_cart':
            # Remove from cart if exists
            cart_key = f"{user_id}_{product_id}"
            if cart_key in self.active_carts:
                item = self.active_carts.pop(cart_key)
                event['quantity'] = item['quantity']
                event['price'] = item['price']
            else:
                # Random removal
                event['quantity'] = random.randint(1, 3)
                event['price'] = round(random.uniform(10.0, 500.0), 2)
                
        elif event_type == 'order_created':
            order_id = f"order_{uuid.uuid4().hex[:10]}"
            quantity = random.randint(1, 5)
            price = round(random.uniform(10.0, 1000.0), 2)
            
            event['order_id'] = order_id
            event['quantity'] = quantity
            event['price'] = price
            
            # Track order for payment
            self.active_orders[order_id] = {
                'user_id': user_id,
                'quantity': quantity,
                'price': price,
                'category': category
            }
            
        elif event_type in ['payment_success', 'payment_failed']:
            # Use existing order or create new one
            if self.active_orders:
                order_id = random.choice(list(self.active_orders.keys()))
                order = self.active_orders[order_id]
                
                event['order_id'] = order_id
                event['quantity'] = order['quantity']
                event['price'] = order['price']
                event['category'] = order['category']
                event['payment_mode'] = random.choice(self.PAYMENT_MODES)
                
                if event_type == 'payment_success':
                    # Remove from active orders on success
                    self.active_orders.pop(order_id)
            else:
                # Generate standalone payment event
                event['order_id'] = f"order_{uuid.uuid4().hex[:10]}"
                event['quantity'] = random.randint(1, 3)
                event['price'] = round(random.uniform(10.0, 500.0), 2)
                event['payment_mode'] = random.choice(self.PAYMENT_MODES)
                
        elif event_type in ['order_cancelled', 'refund_processed']:
            # Use existing order or create new one
            order_id = f"order_{uuid.uuid4().hex[:10]}"
            quantity = random.randint(1, 3)
            price = round(random.uniform(10.0, 500.0), 2)
            
            event['order_id'] = order_id
            event['quantity'] = quantity
            event['price'] = price
            
            if event_type == 'refund_processed':
                event['payment_mode'] = random.choice(self.PAYMENT_MODES)
        
        return event
    
    def generate_batch(self, batch_size: int) -> List[Dict]:
        """Generate a batch of events"""
        return [self.generate_event() for _ in range(batch_size)]


class KafkaEventProducer:
    """Produce events to Kafka"""
    
    def __init__(self, bootstrap_servers: str, topic: str):
        self.topic = topic
        self.producer = KafkaProducer(
            bootstrap_servers=bootstrap_servers,
            value_serializer=lambda v: json.dumps(v).encode('utf-8'),
            key_serializer=lambda k: k.encode('utf-8') if k else None,
            acks='all',
            retries=3,
            max_in_flight_requests_per_connection=1
        )
        logger.info(f"Connected to Kafka at {bootstrap_servers}")
    
    def send_event(self, event: Dict):
        """Send single event to Kafka"""
        key = event['user_id']  # Partition by user_id
        
        future = self.producer.send(self.topic, key=key, value=event)
        
        try:
            record_metadata = future.get(timeout=10)
            logger.debug(
                f"Sent event {event['event_id']} to partition {record_metadata.partition} "
                f"at offset {record_metadata.offset}"
            )
        except Exception as e:
            logger.error(f"Failed to send event: {e}")
    
    def send_batch(self, events: List[Dict]):
        """Send batch of events to Kafka"""
        for event in events:
            self.send_event(event)
        
        self.producer.flush()
    
    def close(self):
        """Close producer"""
        self.producer.close()
        logger.info("Kafka producer closed")


def main():
    """Main function to generate and send events"""
    
    # Configuration
    KAFKA_BOOTSTRAP_SERVERS = 'localhost:9092'
    KAFKA_TOPIC = 'ecommerce-events'
    EVENTS_PER_SECOND = 100
    BATCH_SIZE = 10
    
    logger.info("Starting E-Commerce Event Generator")
    logger.info(f"Target rate: {EVENTS_PER_SECOND} events/second")
    
    # Initialize generator and producer
    generator = EcommerceEventGenerator(num_users=5000, num_products=10000)
    producer = KafkaEventProducer(KAFKA_BOOTSTRAP_SERVERS, KAFKA_TOPIC)
    
    try:
        event_count = 0
        start_time = time.time()
        
        while True:
            batch_start = time.time()
            
            # Generate and send batch
            events = generator.generate_batch(BATCH_SIZE)
            producer.send_batch(events)
            
            event_count += BATCH_SIZE
            
            # Calculate sleep time to maintain target rate
            batch_duration = time.time() - batch_start
            target_batch_duration = BATCH_SIZE / EVENTS_PER_SECOND
            sleep_time = max(0, target_batch_duration - batch_duration)
            
            if sleep_time > 0:
                time.sleep(sleep_time)
            
            # Log progress every 1000 events
            if event_count % 1000 == 0:
                elapsed = time.time() - start_time
                rate = event_count / elapsed
                logger.info(
                    f"Generated {event_count} events | "
                    f"Rate: {rate:.2f} events/sec | "
                    f"Running time: {elapsed:.2f}s"
                )
                
    except KeyboardInterrupt:
        logger.info("Stopping event generation...")
    finally:
        producer.close()
        elapsed = time.time() - start_time
        logger.info(f"Total events generated: {event_count}")
        logger.info(f"Average rate: {event_count/elapsed:.2f} events/sec")


if __name__ == "__main__":
    main()
