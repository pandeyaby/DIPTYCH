class Controller:
    def __init__(self, config: dict):
        self.min_cwnd = config.get('min_cwnd', 1.0)
        self.max_cwnd = config.get('max_cwnd', 2000.0)
        self.cwnd = config.get('initial_cwnd', 4.0)
        
        # Algorithm state
        self.state = 'slow_start'
        self.ssthresh = self.max_cwnd
        self.last_loss_t = -1000
        
        # History for loss rate and RTT estimation
        self.loss_window = []  # [(lost, total_packets), ...]
        self.rtt_samples = []  # [rtt, ...]
        self.window_before_loss_episode = None
        
    def tick(self, obs: dict) -> dict:
        t = obs['t']
        acked = obs['acked']
        lost = obs['lost']
        rtt = obs['rtt']
        timeout = obs['timeout']
        
        # Update history
        total_pkts = lost + acked
        if total_pkts > 0:
            self.loss_window.append((lost, total_pkts))
            if len(self.loss_window) > 80:
                self.loss_window.pop(0)
        
        self.rtt_samples.append(rtt)
        if len(self.rtt_samples) > 80:
            self.rtt_samples.pop(0)
        
        min_rtt = min(self.rtt_samples) if self.rtt_samples else rtt
        avg_rtt = sum(self.rtt_samples) / len(self.rtt_samples) if self.rtt_samples else rtt
        loss_rate = self._compute_loss_rate()
        
        # Timeout: immediate collapse to min
        if timeout:
            self.cwnd = self.min_cwnd
            self.state = 'slow_start'
            self.ssthresh = self.min_cwnd
            self.last_loss_t = t
            self.window_before_loss_episode = None
        
        # Full backoff on extreme congestion
        if loss_rate > 0.4:
            self.cwnd = max(self.min_cwnd, min(self.cwnd, 2.0))
        
        # Loss-based congestion response (adaptive backoff)
        if lost > 0 and t - self.last_loss_t >= max(1, int(avg_rtt)):
            if self.window_before_loss_episode is None:
                self.window_before_loss_episode = self.cwnd
            
            # Proportional backoff based on loss rate
            if loss_rate > 0.15:
                # High loss: aggressive reduction
                factor = 0.4
            elif loss_rate > 0.10:
                factor = 0.5
            elif loss_rate > 0.05:
                # Moderate loss
                factor = 0.7
            elif loss_rate > 0.02:
                # Low loss: gentle reduction for random losses
                factor = 0.88
            else:
                # Very low loss: minimal backoff
                factor = 0.96
            
            self.cwnd = max(self.min_cwnd, self.cwnd * factor)
            self.state = 'congestion_avoidance'
            self.ssthresh = self.cwnd
            self.last_loss_t = t
        
        # Window increase on ACKs
        if acked > 0:
            if self.state == 'slow_start':
                # Exit slow start early on RTT increase (for low delay)
                # Check if recent RTT is significantly higher than min RTT
                if min_rtt > 0 and avg_rtt > min_rtt * 1.12:
                    self.state = 'congestion_avoidance'
                    self.ssthresh = self.cwnd
                else:
                    # Exponential growth
                    self.cwnd = min(self.cwnd * 2.0, self.ssthresh)
            else:
                # Congestion avoidance: additive increase
                # Add 1 packet per RTT
                increase = 1.0 / max(1.0, avg_rtt)
                self.cwnd += increase
        
        # Clamp window to valid range
        self.cwnd = max(self.min_cwnd, min(self.max_cwnd, self.cwnd))
        
        telemetry = {
            'cwnd': self.cwnd,
            'state': self.state,
            'ssthresh': self.ssthresh,
            'loss_rate': loss_rate,
            'rtt': rtt,
            'min_rtt': min_rtt,
            'avg_rtt': avg_rtt,
        }
        
        return {
            'cwnd': self.cwnd,
            'telemetry': telemetry,
        }
    
    def _compute_loss_rate(self):
        if not self.loss_window:
            return 0.0
        total_lost = sum(lost for lost, _ in self.loss_window)
        total_packets = sum(total for _, total in self.loss_window)
        return total_lost / total_packets if total_packets > 0 else 0.0
