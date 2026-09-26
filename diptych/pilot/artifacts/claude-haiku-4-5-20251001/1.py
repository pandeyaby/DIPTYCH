import json
import math


class Controller:
    def __init__(self, config: dict):
        self.min_limit = float(config['min_limit'])
        self.max_limit = float(config['max_limit'])
        self.limit = float(config['initial_limit'])
        
        self.last_setpoint = None
        self.error_history = []
        self.was_saturated_last = False
        
        self.Kp_up = 0.1
        self.Kp_down = 0.3
        self.alpha = 0.2
    
    def tick(self, obs: dict) -> dict:
        t = obs['t']
        latency = obs['latency_ms']
        setpoint = obs['setpoint_ms']
        
        if self.last_setpoint is not None and self.last_setpoint != setpoint:
            self.error_history = []
        self.last_setpoint = setpoint
        
        if setpoint > 0:
            error = (latency - setpoint) / setpoint
        else:
            error = 0.0
        
        self.error_history.append(error)
        if len(self.error_history) > 100:
            self.error_history = self.error_history[-100:]
        
        if len(self.error_history) > 0:
            weighted_sum = 0.0
            weight_sum = 0.0
            for i in range(len(self.error_history)):
                age = len(self.error_history) - 1 - i
                weight = (1.0 - self.alpha) ** age
                weighted_sum += self.error_history[i] * weight
                weight_sum += weight
            trend = weighted_sum / weight_sum
        else:
            trend = error
        
        if len(self.error_history) > 1:
            mean_error = sum(self.error_history) / len(self.error_history)
            variance = sum((e - mean_error) ** 2 for e in self.error_history) / len(self.error_history)
            volatility = math.sqrt(max(0.0, variance))
        else:
            volatility = 0.0
        
        if self.was_saturated_last:
            signal = error
        else:
            signal = trend
        
        if signal > 0:
            Kp = self.Kp_down
        else:
            Kp = self.Kp_up / (1.0 + 2.0 * volatility)
        
        response = -signal * Kp
        new_limit = self.limit * (1.0 + response)
        new_limit = max(self.min_limit, min(self.max_limit, new_limit))
        
        new_saturated = new_limit >= self.max_limit * 0.99
        
        telemetry = {
            'error': error,
            'limit': new_limit,
            'latency_ms': latency,
            'setpoint_ms': setpoint,
            'tick': t,
            'trend': trend,
            'volatility': volatility,
        }
        
        self.limit = new_limit
        self.was_saturated_last = new_saturated
        
        return {'limit': new_limit, 'telemetry': telemetry}
    
    def snapshot(self) -> str:
        state = {
            'Kp_down': self.Kp_down,
            'Kp_up': self.Kp_up,
            'alpha': self.alpha,
            'error_history': self.error_history,
            'last_setpoint': self.last_setpoint,
            'limit': self.limit,
            'max_limit': self.max_limit,
            'min_limit': self.min_limit,
            'was_saturated_last': self.was_saturated_last,
        }
        return json.dumps(state, separators=(',', ':'), sort_keys=True)
    
    @classmethod
    def restore(cls, blob: str) -> "Controller":
        state = json.loads(blob)
        obj = cls.__new__(cls)
        obj.Kp_down = state['Kp_down']
        obj.Kp_up = state['Kp_up']
        obj.alpha = state['alpha']
        obj.error_history = state['error_history']
        obj.last_setpoint = state['last_setpoint']
        obj.limit = state['limit']
        obj.max_limit = state['max_limit']
        obj.min_limit = state['min_limit']
        obj.was_saturated_last = state['was_saturated_last']
        return obj
