import math
import threading


class TokenBucketLimiter:
    def __init__(self, capacity, refill, clock):
        self.capacity, self.refill, self.clock = capacity, refill, clock
        self.buckets = {}
        self.lock = threading.Lock()

    def consume(self, customer):
        with self.lock:
            now = self.clock()
            tokens, last = self.buckets.get(customer, (float(self.capacity), now))
            tokens = min(float(self.capacity), tokens + max(0.0, now - last) * self.refill)
            if tokens >= 1:
                self.buckets[customer] = (tokens - 1, now)
                return True, 0
            retry = 1 if self.refill <= 0 else max(1, math.ceil((1 - tokens) / self.refill))
            self.buckets[customer] = (tokens, now)
            return False, retry
