class Controller:
    def __init__(self, config: dict):
        self.min_cwnd = config.get('min_cwnd', 1.0)
        self.max_cwnd = config.get('max_cwnd', 2000.0)
        self.initial_cwnd = config.get('initial_cwnd', 4.0)
        
        self.cwnd = self.initial_cwnd
        self.ssthresh = self.max_cwnd
        self.last_loss_time = None
    
    def tick(self, obs: dict) -> dict:
        t = obs['t']
        acked = obs['acked']
        lost = obs['lost']
        rtt = obs['rtt']
        timeout = obs['timeout']
        
        telemetry = {}
        
        # Timeout: collapse window and return to slow start
        if timeout:
            self.cwnd = self.min_cwnd
            self.ssthresh = max(2.0 * self.min_cwnd, self.cwnd / 2.0)
            self.last_loss_time = t
            telemetry['event'] = 'timeout'
        
        # Loss handling: reduce once per RTT
        elif lost > 0:
            can_reduce = (self.last_loss_time is None or t - self.last_loss_time >= rtt)
            if can_reduce:
                self.ssthresh = max(2.0 * self.min_cwnd, self.cwnd / 2.0)
                self.cwnd = self.ssthresh
                self.last_loss_time = t
                telemetry['event'] = 'loss'
        
        # ACK handling: increase window
        if acked > 0:
            if self.cwnd < self.ssthresh:
                # Slow start: increase by 1 per ACK (effective doubling per RTT)
                self.cwnd += acked
            else:
                # Congestion avoidance: increase by 1 per RTT worth of ACKs
                self.cwnd += acked / self.cwnd if self.cwnd > 0 else 0
        
        # Enforce window bounds
        self.cwnd = max(self.min_cwnd, min(self.max_cwnd, self.cwnd))
        
        telemetry['cwnd'] = self.cwnd
        telemetry['ssthresh'] = self.ssthresh
        
        return {
            'cwnd': self.cwnd,
            'telemetry': telemetry
        }
