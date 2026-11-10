class Controller:
    def __init__(self, config: dict):
        self.min_cwnd = config.get('min_cwnd', 1.0)
        self.max_cwnd = config.get('max_cwnd', 2000.0)
        self.initial_cwnd = config.get('initial_cwnd', 4.0)
        
        self.cwnd = float(self.initial_cwnd)
        self.ssthresh = float(min(self.max_cwnd, max(self.initial_cwnd * 16, 64)))
        self.last_loss_event_time = -float('inf')
    
    def tick(self, obs: dict) -> dict:
        t = obs['t']
        acked = obs['acked']
        lost = obs['lost']
        rtt = obs['rtt']
        timeout = obs['timeout']
        
        # Timeout: collapse to minimum window
        if timeout:
            self.ssthresh = max(self.min_cwnd, self.cwnd / 2)
            self.cwnd = self.min_cwnd
            self.last_loss_event_time = t
        
        # Loss event: halve window at most once per RTT
        elif lost > 0 and t - self.last_loss_event_time >= rtt:
            self.ssthresh = max(self.min_cwnd, self.cwnd / 2)
            self.cwnd = self.ssthresh
            self.last_loss_event_time = t
        
        # Increase window based on ACKs
        if acked > 0:
            if self.cwnd < self.ssthresh:
                # Slow start: exponential growth
                self.cwnd += acked
            else:
                # Congestion avoidance: linear growth
                self.cwnd += acked / self.cwnd
        
        # Clamp window to valid range
        self.cwnd = max(self.min_cwnd, min(self.max_cwnd, self.cwnd))
        
        telemetry = {
            'cwnd': self.cwnd,
        }
        
        return {
            'cwnd': self.cwnd,
            'telemetry': telemetry
        }
