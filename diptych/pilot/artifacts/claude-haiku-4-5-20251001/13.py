import json

class Controller:
    def __init__(self, config: dict):
        self.min_limit = float(config.get('min_limit', 1.0))
        self.max_limit = float(config.get('max_limit', 200.0))
        self.limit = float(config.get('initial_limit', 50.0))
        
        self.ema_error = 0.0
        self.ema_sq_error = 0.0
        self.last_setpoint = None
        
        self.alpha = 0.1
        self.k_up = 0.5
        self.k_down = 1.5
        self.volatility_weight = 0.1
        self.decay_rate = 0.5
        
    def tick(self, obs: dict) -> dict:
        latency = obs['latency_ms']
        setpoint = obs['setpoint_ms']
        
        if self.last_setpoint != setpoint:
            self.ema_error = 0.0
            self.ema_sq_error = 0.0
            self.last_setpoint = setpoint
        
        error = latency - setpoint
        is_saturated = self.limit >= self.max_limit - 1e-9
        
        if is_saturated:
            self.ema_error *= (1 - self.decay_rate)
        else:
            self.ema_error = self.alpha * error + (1 - self.alpha) * self.ema_error
        
        self.ema_sq_error = self.alpha * (error * error) + (1 - self.alpha) * self.ema_sq_error
        variance = self.ema_sq_error - (self.ema_error ** 2)
        variance = max(0.0, variance)
        
        if self.ema_error > 0:
            correction = -self.k_down * self.ema_error
        else:
            correction = -self.k_up * self.ema_error
        
        correction -= self.volatility_weight * variance
        
        new_limit = self.limit + correction
        self.limit = max(self.min_limit, min(self.max_limit, new_limit))
        
        telemetry = {
            'limit': self.limit,
            'error': error,
            'ema_error': self.ema_error,
            'variance': variance,
            'correction': correction,
            'saturated': int(is_saturated),
        }
        
        return {
            'limit': self.limit,
            'telemetry': telemetry
        }
    
    def snapshot(self) -> str:
        state = {
            'min_limit': self.min_limit,
            'max_limit': self.max_limit,
            'limit': self.limit,
            'ema_error': self.ema_error,
            'ema_sq_error': self.ema_sq_error,
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
        controller = cls(config)
        controller.limit = state['limit']
        controller.ema_error = state['ema_error']
        controller.ema_sq_error = state['ema_sq_error']
        controller.last_setpoint = state['last_setpoint']
        return controller
