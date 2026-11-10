class Controller:
    def __init__(self, config: dict):
        self.min_cwnd = config.get('min_cwnd', 1.0)
        self.max_cwnd = config.get('max_cwnd', 2000.0)
        self.cwnd = config.get('initial_cwnd', 4.0)
        self.ssthresh = float('inf')
        
        self.min_rtt = None
        self.last_loss_time = -1.0
        self.last_rtt_at_loss = 1.0
        
    def tick(self, obs: dict) -> dict:
        t = obs['t']
        acked = obs['acked']
        lost = obs['lost']
        rtt = obs['rtt']
        timeout = obs['timeout']
        
        # Track minimum RTT (base path delay)
        if self.min_rtt is None:
            self.min_rtt = rtt
        else:
            self.min_rtt = min(self.min_rtt, rtt)
        
        if timeout:
            # Timeout: aggressive backoff
            self.cwnd = max(self.min_cwnd, self.cwnd / 4)
            self.ssthresh = max(self.cwnd, self.min_cwnd)
            self.last_loss_time = t
            
        elif lost > 0 and t - self.last_loss_time > self.last_rtt_at_loss:
            # Loss event: reduce at most once per RTT
            # Use RTT elevation as congestion signal
            queue_fraction = (rtt - self.min_rtt) / self.min_rtt
            
            if queue_fraction > 0.1:
                # Significant queueing: RTT elevated > 10%
                # Likely congestion, do standard multiplicative decrease
                self.ssthresh = max(self.cwnd / 2, self.min_cwnd)
                self.cwnd = self.ssthresh
            else:
                # Minimal queueing: loss likely spurious/random
                # Gentle reduction instead of halving
                self.cwnd = max(self.cwnd * 0.95, self.min_cwnd)
            
            self.last_loss_time = t
            self.last_rtt_at_loss = rtt
        
        # Window increase on acknowledgments
        if acked > 0:
            if self.cwnd < self.ssthresh:
                # Slow start: exponential growth
                self.cwnd += acked
            else:
                # Congestion avoidance: additive growth
                self.cwnd += acked / self.cwnd
        
        # Enforce window bounds
        self.cwnd = max(self.min_cwnd, min(self.cwnd, self.max_cwnd))
        
        return {
            'cwnd': self.cwnd,
            'telemetry': {
                'ssthresh': self.ssthresh if self.ssthresh != float('inf') else -1,
                'min_rtt': self.min_rtt if self.min_rtt is not None else -1,
            }
        }
