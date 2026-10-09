class Controller:
    def __init__(self, config: dict):
        self.min_cwnd = float(config.get('min_cwnd', 1.0))
        self.max_cwnd = float(config.get('max_cwnd', 2000.0))
        self.cwnd = float(config.get('initial_cwnd', 4.0))
        
        self.ssthresh = self.max_cwnd
        self.in_slow_start = True
        self.last_loss_time = -1000.0
        self.rtt_min = float('inf')
        self.rtt_history = []
    
    def tick(self, obs: dict) -> dict:
        t = obs['t']
        acked = obs['acked']
        lost = obs['lost']
        rtt = obs['rtt']
        timeout = obs['timeout']
        
        # Track minimum RTT for baseline
        self.rtt_min = min(self.rtt_min, rtt)
        self.rtt_history.append(rtt)
        if len(self.rtt_history) > 100:
            self.rtt_history.pop(0)
        
        # Timeout: full backoff to minimum (satisfies requirement 1)
        if timeout:
            self.cwnd = self.min_cwnd
            self.ssthresh = self.min_cwnd * 2
            self.in_slow_start = True
            self.last_loss_time = t
        
        # Loss event handling
        elif lost > 0:
            # RENO: can reduce at most once per RTT
            time_since_loss = t - self.last_loss_time
            can_reduce = time_since_loss >= max(1.0, rtt)
            
            if can_reduce:
                # Distinguish congestion loss from random loss
                rtt_ratio = rtt / max(self.rtt_min, 0.1)
                
                # Congestion indicators: elevated RTT or multiple losses
                is_congestion_loss = rtt_ratio > 1.2 or lost > 1
                
                if is_congestion_loss:
                    # Congestion-related: halve window (RENO behavior)
                    self.ssthresh = max(self.min_cwnd * 2, self.cwnd / 2)
                    self.cwnd = self.ssthresh
                    self.in_slow_start = False
                else:
                    # Likely random loss: gentle reduction for high-loss paths
                    self.cwnd = max(self.min_cwnd, self.cwnd * 0.9)
                
                self.last_loss_time = t
        
        # No loss: increase window
        else:
            if self.in_slow_start:
                # Slow start: exponential growth (double per RTT)
                self.cwnd += acked
                
                if self.cwnd >= self.ssthresh:
                    self.in_slow_start = False
                    self.cwnd = self.ssthresh
            else:
                # Congestion avoidance: linear growth (add 1 per RTT)
                self.cwnd += acked / max(self.cwnd, 1.0)
            
            # RTT-based proactive backoff for low delay
            if len(self.rtt_history) > 5:
                recent_rtt = sum(self.rtt_history[-5:]) / 5
                # If queue is growing (RTT > 30% above baseline), reduce slightly
                if recent_rtt > self.rtt_min * 1.3:
                    self.cwnd *= 0.98
        
        # Enforce [min_cwnd, max_cwnd] bounds
        self.cwnd = max(self.min_cwnd, min(self.max_cwnd, self.cwnd))
        
        return {
            'cwnd': self.cwnd,
            'telemetry': {}
        }
