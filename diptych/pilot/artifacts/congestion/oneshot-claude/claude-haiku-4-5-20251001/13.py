class Controller:
    def __init__(self, config: dict):
        self.min_cwnd = config.get('min_cwnd', 1.0)
        self.max_cwnd = config.get('max_cwnd', 2000.0)
        self.cwnd = config.get('initial_cwnd', 4.0)
        
        self.ssthresh = self.max_cwnd
        self.state = 'slow_start'
        self.last_loss_tick = -1000
        self.min_rtt = float('inf')
        
    def tick(self, obs: dict) -> dict:
        t = obs['t']
        acked = obs['acked']
        lost = obs['lost']
        rtt = obs['rtt']
        timeout = obs['timeout']
        
        # Track minimum RTT observed (base delay)
        self.min_rtt = min(self.min_rtt, rtt)
        queue_rtt = rtt - self.min_rtt
        
        # Timeout: major backoff (full backoff requirement §5.1.1)
        if timeout:
            self.cwnd = self.min_cwnd
            self.ssthresh = self.min_cwnd * 2
            self.state = 'slow_start'
            self.last_loss_tick = t
            return {'cwnd': self.cwnd, 'telemetry': {'event': 'timeout'}}
        
        # Loss handling: back off to congestion avoidance (rate-limited to once per RTT)
        if lost > 0 and t - self.last_loss_tick > rtt:
            # In slow start: always respond to loss (end of probe phase)
            # In congestion avoidance: only if queue is building (likely congestion not random loss)
            if self.state == 'slow_start' or queue_rtt > self.min_rtt * 0.1:
                self.ssthresh = max(self.cwnd / 2, self.min_cwnd)
                self.cwnd = self.ssthresh
                self.state = 'congestion_avoidance'
                self.last_loss_tick = t
        
        # ACK: increase window (AIMD)
        if acked > 0:
            if self.state == 'slow_start':
                # Exponential growth: add acked packets (doubles each RTT)
                self.cwnd += acked
                if self.cwnd >= self.ssthresh:
                    self.cwnd = self.ssthresh
                    self.state = 'congestion_avoidance'
            else:
                # Linear growth: add 1/cwnd per packet acked (~1 per RTT)
                self.cwnd += acked / self.cwnd
        
        # Clamp to valid range
        self.cwnd = max(self.min_cwnd, min(self.max_cwnd, self.cwnd))
        
        return {
            'cwnd': self.cwnd,
            'telemetry': {
                'state': self.state,
                'queue_rtt': queue_rtt
            }
        }
