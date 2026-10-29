class Controller:
    def __init__(self, config: dict):
        self.min_cwnd = config.get('min_cwnd', 1.0)
        self.max_cwnd = config.get('max_cwnd', 2000.0)
        self.initial_cwnd = config.get('initial_cwnd', 4.0)
        
        self.cwnd = self.initial_cwnd
        self.ssthresh = self.max_cwnd
        self.last_loss_time = -float('inf')
        self.min_rtt = float('inf')
        self.in_slow_start = True
        self.recent_loss_samples = []
        self.recent_sent_sum = 0.0
        
    def tick(self, obs: dict) -> dict:
        t = obs['t']
        acked = obs['acked']
        lost = obs['lost']
        rtt = obs['rtt']
        timeout = obs['timeout']
        
        # Track minimum RTT for queueing detection
        if rtt > 0 and rtt < self.min_rtt:
            self.min_rtt = rtt
        
        # Estimate packets sent in this interval
        if rtt > 0:
            sent = self.cwnd / rtt
            self.recent_sent_sum += sent
        
        # Handle timeout - collapse to minimum
        if timeout:
            self.cwnd = self.min_cwnd
            self.ssthresh = max(2.0, self.initial_cwnd * 2.0)
            self.in_slow_start = True
            self.recent_loss_samples = []
            self.recent_sent_sum = 0.0
        
        # Handle losses (at most once per RTT)
        if lost > 0 and t - self.last_loss_time >= rtt:
            self.last_loss_time = t
            self.recent_loss_samples.append((t, lost))
            
            # Keep only recent loss samples (last ~30 RTTs)
            window_start = t - 30.0 * max(rtt, 1.0)
            self.recent_loss_samples = [
                (ts, pkt_loss) for ts, pkt_loss in self.recent_loss_samples 
                if ts > window_start
            ]
            
            # Calculate recent loss rate to distinguish congestion from random loss
            total_recent_loss = sum(pkt_loss for _, pkt_loss in self.recent_loss_samples)
            loss_rate = total_recent_loss / self.recent_sent_sum if self.recent_sent_sum > 0 else 0.0
            
            # Adaptive window reduction based on loss rate
            if loss_rate > 0.05:
                # High loss (>5%) - significant congestion
                self.cwnd = max(2.0, self.cwnd * 0.5)
            elif loss_rate > 0.01:
                # Moderate loss (1-5%) - some congestion
                self.cwnd = max(2.0, self.cwnd * 0.8)
            else:
                # Low loss (<1%) - mostly random
                self.cwnd = max(2.0, self.cwnd * 0.85)
            
            self.ssthresh = max(2.0, self.cwnd)
            self.in_slow_start = False
            self.recent_sent_sum = 0.0
        
        # RTT-based backoff for low-delay operation
        if self.min_rtt < float('inf') and rtt > 0 and not self.in_slow_start:
            rtt_ratio = rtt / self.min_rtt
            if rtt_ratio > 1.25:  # 25% RTT increase indicates queueing
                self.cwnd = max(self.min_cwnd, self.cwnd * 0.99)
        
        # Handle acks - increase window
        if acked > 0:
            if self.in_slow_start:
                # Slow start: double per RTT
                self.cwnd += acked
                # Exit slow start at threshold
                if self.cwnd >= self.ssthresh:
                    self.in_slow_start = False
            else:
                # Congestion avoidance: add 1 per RTT
                if self.cwnd > 0:
                    self.cwnd += acked / self.cwnd
        
        # Enforce window bounds
        self.cwnd = max(self.min_cwnd, min(self.max_cwnd, self.cwnd))
        
        telemetry = {
            'cwnd': self.cwnd,
            'in_slow_start': 1 if self.in_slow_start else 0,
        }
        
        return {
            'cwnd': self.cwnd,
            'telemetry': telemetry
        }
