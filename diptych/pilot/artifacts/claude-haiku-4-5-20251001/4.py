import json

class Controller:
    def __init__(self, config: dict):
        self.config = config
        self.min_limit = float(config['min_limit'])
        self.max_limit = float(config['max_limit'])
        self.limit = float(config['initial_limit'])
        self.error_history = []
        self.last_setpoint = None
        self.saturated_since = None
        
        self.k_down = 0.15
        self.k_up = 0.06
        self.decay = 0.1
    
    def tick(self, obs: dict) -> dict:
        t = obs['t']
        latency = float(obs['latency_ms'])
        setpoint = float(obs['setpoint_ms'])
        arrival_rps = float(obs['arrival_rps'])
        admitted_rps = float(obs['admitted_rps'])
        
        if self.last_setpoint != setpoint:
            self.error_history = []
            self.saturated_since = None
            self.last_setpoint = setpoint
        
        error = latency - setpoint
        self.error_history.append([t, error])
        if len(self.error_history) > 100:
            self.error_history = self.error_history[-100:]
        
        n = len(self.error_history)
        weights = [(1.0 - self.decay) ** (n - 1 - i) for i in range(n)]
        weight_sum = sum(weights)
        
        avg_error = sum(e * w for (_, e), w in zip(self.error_history, weights)) / weight_sum
        variance = sum(w * (e - avg_error) ** 2 for (_, e), w in zip(self.error_history, weights)) / weight_sum
        
        if self.saturated_since is not None and t - self.saturated_since > 5:
            effective_error = 0.8 * error + 0.2 * avg_error
        else:
            effective_error = avg_error
        
        if effective_error > 0:
            delta = -self.k_down * effective_error
        else:
            dampen = 1.0 - 0.5 * min(1.0, variance / (1.0 + abs(effective_error)))
            delta = -self.k_up * effective_error * dampen
        
        self.limit += delta
        self.limit = max(self.min_limit, min(self.max_limit, self.limit))
        
        if self.limit >= self.max_limit - 1e-9:
            if self.saturated_since is None:
                self.saturated_since = t
        else:
            self.saturated_since = None
        
        telemetry = {
            'limit': self.limit,
            'error': error,
            'avg_error': avg_error,
            'variance': variance,
            'history_size': len(self.error_history),
            'saturated_ticks': (t - self.saturated_since) if self.saturated_since is not None else 0,
        }
        
        return {'limit': self.limit, 'telemetry': telemetry}
    
    def snapshot(self) -> str:
        return json.dumps({
            'config': self.config,
            'limit': self.limit,
            'error_history': self.error_history,
            'last_setpoint': self.last_setpoint,
            'saturated_since': self.saturated_since,
        })
    
    @classmethod
    def restore(cls, blob: str) -> 'Controller':
        d = json.loads(blob)
        c = cls(d['config'])
        c.limit = d['limit']
        c.error_history = d['error_history']
        c.last_setpoint = d['last_setpoint']
        c.saturated_since = d['saturated_since']
        return c
