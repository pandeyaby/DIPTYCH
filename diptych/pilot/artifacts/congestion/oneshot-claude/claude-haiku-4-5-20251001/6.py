class Controller:
    def __init__(self, config: dict):
        self.min_cwnd = config.get('min_cwnd', 1.0)
        self.max_cwnd = config.get('max_cwnd', 2000.0)
        self.cwnd = config.get('initial_cwnd', 4.0)
        
        self.ssthresh = self.max_cwnd
        self.in_slow_start = True
        self.last_loss_time = -float('inf')
        self.min_rtt = None
    
    def tick(self, obs: dict) -> dict:
        t = obs['t']
        acked = obs['acked']
        lost = obs['lost']
        rtt = obs['rtt']
        timeout = obs['timeout']
        
        # Track minimum RTT observed
        if self.min_rtt is None:
            self.min_rtt = rtt
        else:
            self.min_rtt = min(self.min_rtt, rtt)
        
        if timeout:
            # Timeout: full backoff to minimum
            self.cwnd = self.min_cwnd
            self.ssthresh = self.min_cwnd
            self.in_slow_start = True
            self.last_loss_time = t
        else:
            if lost > 0:
                # Loss event: only respond once per RTT
                if t - self.last_loss_time >= rtt:
                    # Distinguish congestive loss from random loss using RTT
                    rtt_elevation = (rtt - self.min_rtt) / self.min_rtt if self.min_rtt > 0 else 0
                    
                    # Only back off if RTT is significantly elevated (>20%), indicating congestion
                    if rtt_elevation > 0.2:
                        # Congestive loss: halve window per AIMD
                        self.ssthresh = max(self.min_cwnd, self.cwnd / 2)
                        self.cwnd = self.ssthresh
                    # Random loss (RTT stable): don't back off
                    
                    self.in_slow_start = False
                    self.last_loss_time = t
            
            if acked > 0:
                if self.in_slow_start:
                    # Slow start: double window per RTT
                    self.cwnd += acked
                    if self.cwnd >= self.ssthresh:
                        self.in_slow_start = False
                else:
                    # Congestion avoidance with RTT-based growth modulation
                    rtt_elevation = (rtt - self.min_rtt) / self.min_rtt if self.min_rtt > 0 else 0
                    
                    if rtt_elevation > 0.15:
                        # Queue building significantly, slow growth
                        self.cwnd += acked / (self.cwnd * 2)
                    elif rtt_elevation > 0.05:
                        # Moderate queue buildup
                        self.cwnd += acked / (self.cwnd * 1.2)
                    else:
                        # Link underutilized, normal AIMD growth
                        self.cwnd += acked / self.cwnd
        
        # Enforce window bounds
        self.cwnd = max(self.min_cwnd, min(self.max_cwnd, self.cwnd))
        
        telemetry = {
            'cwnd': self.cwnd,
            'rtt': rtt,
            'min_rtt': self.min_rtt if self.min_rtt is not None else rtt,
        }
        
        return {
            'cwnd': self.cwnd,
            'telemetry': telemetry
        }
