class Controller:
    def __init__(self, config: dict):
        self.min_cwnd = config.get('min_cwnd', 1.0)
        self.max_cwnd = config.get('max_cwnd', 2000.0)
        self.cwnd = config.get('initial_cwnd', 4.0)
        
        self.ssthresh = self.max_cwnd
        self.slow_start = True
        self.last_loss_t = -float('inf')
        self.ack_count = 0
    
    def tick(self, obs: dict) -> dict:
        t = obs['t']
        acked = obs['acked']
        lost = obs['lost']
        rtt = obs['rtt']
        timeout = obs['timeout']
        
        telemetry = {}
        
        if timeout:
            self.cwnd = self.min_cwnd
            self.ssthresh = max(2.0, self.cwnd / 2)
            self.slow_start = True
            self.ack_count = 0
            telemetry['event'] = 'timeout'
        else:
            if lost > 0 and t - self.last_loss_t >= rtt:
                self.cwnd = max(self.min_cwnd, self.cwnd / 2)
                self.ssthresh = self.cwnd
                self.slow_start = False
                self.last_loss_t = t
                self.ack_count = 0
                telemetry['event'] = 'loss'
            
            if acked > 0:
                if self.slow_start:
                    self.cwnd += acked
                    if self.cwnd >= self.ssthresh:
                        self.cwnd = self.ssthresh
                        self.slow_start = False
                        self.ack_count = 0
                else:
                    old_cwnd = self.cwnd
                    self.ack_count += acked
                    increase = int(self.ack_count / old_cwnd)
                    if increase > 0:
                        self.cwnd += increase
                        self.ack_count -= increase * old_cwnd
        
        self.cwnd = max(self.min_cwnd, min(self.cwnd, self.max_cwnd))
        telemetry['cwnd'] = self.cwnd
        
        return {'cwnd': self.cwnd, 'telemetry': telemetry}
