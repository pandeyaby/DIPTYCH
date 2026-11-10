class Controller:
    def __init__(self, config: dict):
        self.min_cwnd = config.get('min_cwnd', 1.0)
        self.max_cwnd = config.get('max_cwnd', 2000.0)
        self.cwnd = config.get('initial_cwnd', 4.0)
        
        # TCP Reno state
        self.ssthresh = self.max_cwnd
        self.last_loss_time = -float('inf')
        self.min_rtt = None
        self.rtt_sum = 0.0
        self.rtt_count = 0
        self.loss_count = 0
        
    def tick(self, obs: dict) -> dict:
        t = obs['t']
        acked = obs['acked']
        lost = obs['lost']
        rtt = obs['rtt']
        timeout = obs['timeout']
        
        # Track RTT statistics for high loss detection
        if self.min_rtt is None or rtt < self.min_rtt:
            self.min_rtt = rtt
        self.rtt_sum += rtt
        self.rtt_count += 1
        
        # Timeout: full backoff
        if timeout:
            self.ssthresh = max(self.cwnd / 2.0, self.min_cwnd * 2.0)
            self.cwnd = self.min_cwnd
            self.last_loss_time = t
            self.loss_count = 0
        # Loss-based congestion control (at most once per RTT)
        elif lost > 0 and (t - self.last_loss_time) > rtt:
            # Detect if losses are frequent (congestion) vs isolated (random)
            self.loss_count += 1
            
            # Always apply multiplicative decrease on loss
            self.ssthresh = max(self.cwnd / 2.0, self.min_cwnd * 2.0)
            self.cwnd = self.ssthresh
            self.last_loss_time = t
        
        # ACK-based increase (AIMD)
        if acked > 0:
            if self.cwnd < self.ssthresh:
                # Slow start: exponential growth (add 1 per ACK)
                self.cwnd += acked
            else:
                # Congestion avoidance: linear growth (add 1 per RTT)
                self.cwnd += acked / self.cwnd
        
        # Enforce bounds
        cwnd_out = max(self.min_cwnd, min(self.max_cwnd, self.cwnd))
        
        return {
            'cwnd': cwnd_out,
            'telemetry': {
                'cwnd': cwnd_out,
                'ssthresh': self.ssthresh,
                'phase': 'slowstart' if self.cwnd < self.ssthresh else 'congavoid',
                'rtt': rtt,
                'min_rtt': self.min_rtt if self.min_rtt is not None else 0,
            }
        }
