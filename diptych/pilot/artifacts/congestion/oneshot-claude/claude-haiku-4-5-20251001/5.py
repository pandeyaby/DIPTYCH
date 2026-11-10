class Controller:
    def __init__(self, config: dict):
        self.min_cwnd = config.get('min_cwnd', 1.0)
        self.max_cwnd = config.get('max_cwnd', 2000.0)
        self.cwnd = config.get('initial_cwnd', 4.0)
        
        self.ssthresh = self.max_cwnd
        self.in_slow_start = True
        self.last_loss_reduction = -float('inf')
        
        # For loss rate estimation
        self.recent_packets = []
        
        # For RTT-based queue detection
        self.min_rtt = float('inf')
        self.smoothed_rtt = None
    
    def tick(self, obs: dict) -> dict:
        t = obs['t']
        acked = obs['acked']
        lost = obs['lost']
        rtt = obs['rtt']
        timeout = obs['timeout']
        
        # Track RTT for queue depth estimation
        if rtt > 0:
            self.min_rtt = min(self.min_rtt, rtt)
            if self.smoothed_rtt is None:
                self.smoothed_rtt = rtt
            else:
                self.smoothed_rtt = 0.9 * self.smoothed_rtt + 0.1 * rtt
        
        # Track packets for loss rate estimation
        sent = acked + lost
        self.recent_packets.append((sent, lost))
        if len(self.recent_packets) > 25:
            self.recent_packets = self.recent_packets[-25:]
        
        # Calculate loss rate over recent history
        total_sent = sum(s for s, _ in self.recent_packets)
        total_lost = sum(l for _, l in self.recent_packets)
        loss_rate = total_lost / max(total_sent, 1) if total_sent > 0 else 0
        
        # Estimate queue depth
        queue_depth = (self.smoothed_rtt - self.min_rtt) if self.min_rtt < float('inf') else 0
        
        # Handle timeout - aggressive backoff
        if timeout:
            self.cwnd = self.min_cwnd
            self.ssthresh = self.min_cwnd * 2
            self.in_slow_start = True
            self.last_loss_reduction = t
        
        # Handle loss - only on sustained high loss to tolerate random loss
        elif lost > 0 and loss_rate >= 0.015 and t - self.last_loss_reduction >= rtt:
            self.ssthresh = max(self.min_cwnd * 2, self.cwnd / 2)
            self.cwnd = self.ssthresh
            self.in_slow_start = False
            self.last_loss_reduction = t
        
        # Handle ACKs
        if acked > 0:
            if self.in_slow_start:
                # Slow start with queue awareness to reduce latency
                if queue_depth > self.min_rtt * 0.5:
                    self.cwnd += acked * 0.5
                else:
                    self.cwnd += acked
                
                if self.cwnd >= self.ssthresh:
                    self.in_slow_start = False
                    self.cwnd = self.ssthresh
            else:
                # Congestion avoidance
                self.cwnd += acked / rtt
        
        # Ensure window within bounds
        self.cwnd = max(self.min_cwnd, min(self.max_cwnd, self.cwnd))
        
        return {
            'cwnd': self.cwnd,
            'telemetry': {
                'ssthresh': self.ssthresh,
                'in_slow_start': self.in_slow_start,
                'loss_rate': loss_rate,
                'queue_depth': queue_depth
            }
        }
