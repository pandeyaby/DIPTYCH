class Controller:
    def __init__(self, config: dict):
        self.min_cwnd = config.get('min_cwnd', 1.0)
        self.max_cwnd = config.get('max_cwnd', 2000.0)
        self.initial_cwnd = config.get('initial_cwnd', 4.0)
        
        self.cwnd = self.initial_cwnd
        self.ssthresh = self.max_cwnd
        self.rtt_min = float('inf')
        self.last_rtt = 1.0
        self.last_loss_tick = -float('inf')
        self.loss_history = []
        
    def tick(self, obs: dict) -> dict:
        t = obs['t']
        acked = obs['acked']
        lost = obs['lost']
        rtt = obs['rtt']
        timeout = obs['timeout']
        
        # Update RTT tracking
        if rtt > 0:
            self.last_rtt = rtt
            if rtt < self.rtt_min:
                self.rtt_min = rtt
        
        # Track loss rate from recent history
        if acked + lost > 0:
            tick_loss_rate = lost / (acked + lost)
            self.loss_history.append(tick_loss_rate)
            if len(self.loss_history) > 100:
                self.loss_history.pop(0)
        
        # Calculate recent loss rate
        recent_loss_rate = sum(self.loss_history) / len(self.loss_history) if self.loss_history else 0
        
        # Window control logic
        if timeout:
            # Timeout: aggressive backoff to minimum
            self.cwnd = self.min_cwnd
            self.ssthresh = max(self.min_cwnd * 2, self.min_cwnd)
        
        elif lost > 0 and t > self.last_loss_tick + max(self.last_rtt, 1):
            # Loss event (at most once per RTT)
            self.last_loss_tick = t
            
            # Adaptive backoff based on loss rate
            # Higher loss rates (congestion) get more aggressive backoff
            # Lower loss rates (random) get less aggressive backoff
            if recent_loss_rate > 0.1:
                # High loss rate: 50% reduction (standard)
                backoff_multiplier = 0.5
            elif recent_loss_rate > 0.05:
                # Medium loss rate: 25% reduction
                backoff_multiplier = 0.75
            elif recent_loss_rate > 0.02:
                # Low loss rate: 15% reduction
                backoff_multiplier = 0.85
            else:
                # Very low loss rate (likely random): 5% reduction
                backoff_multiplier = 0.95
            
            self.cwnd = max(self.cwnd * backoff_multiplier, self.min_cwnd)
            self.ssthresh = self.cwnd
        
        elif acked > 0:
            # ACK-based window increase
            if self.cwnd < self.ssthresh:
                # Slow start: exponential growth
                self.cwnd += acked
            else:
                # Congestion avoidance: linear growth
                self.cwnd += acked / max(1.0, self.cwnd)
        
        # Clamp window to valid range
        self.cwnd = max(self.min_cwnd, min(self.cwnd, self.max_cwnd))
        
        # Telemetry for monitoring
        telemetry = {
            'cwnd': self.cwnd,
            'ssthresh': self.ssthresh,
            'loss_rate': recent_loss_rate,
            'rtt': self.last_rtt,
            'rtt_min': self.rtt_min if self.rtt_min < float('inf') else 0.0,
        }
        
        return {
            'cwnd': self.cwnd,
            'telemetry': telemetry,
        }
