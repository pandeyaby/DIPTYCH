class Controller:
    def __init__(self, config: dict):
        self.min_cwnd = config.get('min_cwnd', 1.0)
        self.max_cwnd = config.get('max_cwnd', 2000.0)
        self.initial_cwnd = config.get('initial_cwnd', 4.0)
        
        self.cwnd = self.initial_cwnd
        self.ssthresh = self.max_cwnd
        self.min_rtt = None
        self.last_loss_t = -1000.0
        
    def tick(self, obs: dict) -> dict:
        t = obs['t']
        acked = obs['acked']
        lost = obs['lost']
        rtt = obs['rtt']
        timeout = obs['timeout']
        
        # Track minimum RTT for queue detection
        if self.min_rtt is None:
            self.min_rtt = rtt
        else:
            self.min_rtt = min(self.min_rtt, rtt)
        
        telemetry = {}
        
        # Timeout: full backoff to minimum window
        if timeout:
            self.cwnd = self.min_cwnd
            self.ssthresh = max(self.min_cwnd, 2.0)
            telemetry['event'] = 'timeout'
        # Loss: halve window at most once per RTT
        elif lost > 0 and t - self.last_loss_t >= rtt:
            self.last_loss_t = t
            self.cwnd = max(self.cwnd * 0.5, self.min_cwnd)
            self.ssthresh = max(self.cwnd, 2.0)
            telemetry['event'] = 'loss'
        else:
            # ACK-based window increase (AIMD)
            if acked > 0:
                if self.cwnd < self.ssthresh:
                    # Slow start: exponential growth
                    self.cwnd = self.cwnd + acked
                else:
                    # Congestion avoidance: linear growth (add 1 per RTT)
                    self.cwnd = self.cwnd + acked / self.cwnd
        
        # RTT-based early congestion detection
        # Reduce preemptively when queue is building to avoid losses
        if self.min_rtt is not None and self.min_rtt > 0:
            rtt_ratio = rtt / self.min_rtt
            if rtt_ratio > 1.15 and self.cwnd > self.min_cwnd * 2:
                self.cwnd = self.cwnd * 0.96
        
        # Clamp window to valid range
        self.cwnd = max(self.min_cwnd, min(self.max_cwnd, self.cwnd))
        
        telemetry['cwnd'] = self.cwnd
        
        return {'cwnd': self.cwnd, 'telemetry': telemetry}
