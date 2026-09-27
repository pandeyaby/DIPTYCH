import json

class Controller:
    def __init__(self, config: dict):
        self.min_limit = float(config.get('min_limit', 1.0))
        self.max_limit = float(config.get('max_limit', 200.0))
        self.limit = float(config.get('initial_limit', 50.0))
        
        self.error_history = []
        self.latency_history = []
        self.last_setpoint = None
    
    def tick(self, obs: dict) -> dict:
        latency = float(obs['latency_ms'])
        setpoint = float(obs['setpoint_ms'])
        
        if self.last_setpoint is not None and self.last_setpoint != setpoint:
            self.error_history = []
            self.latency_history = []
        
        self.last_setpoint = setpoint
        error = latency - setpoint
        is_saturated = self.limit >= self.max_limit
        
        if not is_saturated:
            self.error_history.append(error)
            self.latency_history.append(latency)
        
        if len(self.error_history) > 30:
            self.error_history.pop(0)
        if len(self.latency_history) > 30:
            self.latency_history.pop(0)
        
        weighted_error = self._weighted_error()
        variance = self._calculate_variance()
        
        gain = 3.0 if error > 0 else 1.0
        adjustment = -gain * error
        
        if variance > 0:
            adjustment *= 1.0 / (1.0 + variance)
        
        new_limit = max(self.min_limit, min(self.max_limit, self.limit + adjustment))
        self.limit = new_limit
        
        telemetry = {
            'limit': self.limit,
            'error': error,
            'weighted_error': weighted_error,
            'variance': variance,
            'adjustment': adjustment,
            'saturated': float(is_saturated),
        }
        
        return {'limit': self.limit, 'telemetry': telemetry}
    
    def _weighted_error(self) -> float:
        if not self.error_history:
            return 0.0
        n = len(self.error_history)
        total, weight_sum = 0.0, 0.0
        for i in range(n):
            weight = 2.0 ** i
            total += weight * self.error_history[n - 1 - i]
            weight_sum += weight
        return total / weight_sum
    
    def _calculate_variance(self) -> float:
        if len(self.latency_history) < 2:
            return 0.0
        mean = sum(self.latency_history) / len(self.latency_history)
        return sum((x - mean) ** 2 for x in self.latency_history) / len(self.latency_history)
    
    def snapshot(self) -> str:
        state = {
            'limit': self.limit,
            'min_limit': self.min_limit,
            'max_limit': self.max_limit,
            'error_history': self.error_history,
            'latency_history': self.latency_history,
            'last_setpoint': self.last_setpoint,
        }
        return json.dumps(state)
    
    @classmethod
    def restore(cls, blob: str) -> "Controller":
        state = json.loads(blob)
        config = {
            'min_limit': state['min_limit'],
            'max_limit': state['max_limit'],
            'initial_limit': state['limit'],
        }
        c = cls(config)
        c.limit = state['limit']
        c.error_history = state['error_history']
        c.latency_history = state['latency_history']
        c.last_setpoint = state['last_setpoint']
        return c
