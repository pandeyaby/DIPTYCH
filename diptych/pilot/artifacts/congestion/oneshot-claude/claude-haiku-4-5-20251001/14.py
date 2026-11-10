class Controller:
    def __init__(self, config: dict):
        self.config = config
        self.cwnd = config.get('initial_cwnd', 4.0)
        self.min_cwnd = config.get('min_cwnd', 1.0)
        self.max_cwnd = config.get('max_cwnd', 2000.0)
        
        self.ssthresh = self.max_cwnd
        self.in_slow_start = True
        self.in_recovery = False
        
        # Sliding window for loss rate calculation
        self.history = []
        self.history_size = 50
        
    def tick(self, obs: dict) -> dict:
        t = obs['t']
        acked = obs['acked']
        lost = obs['lost']
        rtt = obs['rtt']
        timeout = obs['timeout']
        
        # Calculate packets sent this tick
        packets_sent = self.cwnd / rtt if rtt > 0 else self.cwnd
        
        # Maintain sliding window of (sent, lost) tuples
        self.history.append((packets_sent, lost))
        if len(self.history) > self.history_size:
            self.history.pop(0)
        
        # Calculate loss rate from recent history
        total_sent = sum(s for s, _ in self.history)
        total_lost = sum(l for _, l in self.history)
        loss_rate = total_lost / total_sent if total_sent > 0 else 0
        
        if timeout:
            # Timeout: collapse window and restart slow start
            self.cwnd = self.min_cwnd
            self.ssthresh = self.max_cwnd
            self.in_slow_start = True
            self.in_recovery = False
        else:
            # Handle packet losses
            if lost > 0 and not self.in_recovery:
                self.in_recovery = True
                
                # Adaptive reduction based on loss rate
                # Lower loss rates get gentler treatment for random losses
                if loss_rate > 0.2:
                    reduction_factor = 0.5
                elif loss_rate > 0.05:
                    reduction_factor = 0.65
                elif loss_rate > 0.01:
                    reduction_factor = 0.85
                else:  # Very low loss likely due to randomness
                    reduction_factor = 0.95
                
                self.cwnd *= reduction_factor
                self.ssthresh = self.cwnd
                self.in_slow_start = False
            
            # Exit recovery when we get acknowledged packets
            if acked > 0 and self.in_recovery:
                self.in_recovery = False
            
            # Increase window on acknowledged packets
            if acked > 0:
                if self.in_slow_start:
                    # Slow start: exponential increase
                    self.cwnd += acked
                    if self.cwnd >= self.ssthresh:
                        self.in_slow_start = False
                else:
                    # Congestion avoidance: linear increase (~1 packet per RTT)
                    self.cwnd += acked / self.cwnd
        
        # Ensure window stays within configured bounds
        self.cwnd = max(self.min_cwnd, min(self.cwnd, self.max_cwnd))
        
        telemetry = {
            'cwnd': self.cwnd,
            'loss_rate': loss_rate * 100 if loss_rate > 0 else 0.0,
            'slow_start': 1 if self.in_slow_start else 0,
        }
        
        return {
            'cwnd': self.cwnd,
            'telemetry': telemetry
        }
