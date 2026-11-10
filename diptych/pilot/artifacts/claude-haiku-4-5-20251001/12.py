import json
import math

class Controller:
    def __init__(self, config: dict):
        self.min_limit = float(config['min_limit'])
        self.max_limit = float(config['max_limit'])
        self.limit = float(config['initial_limit'])
        
        self.last_setpoint = None
        self.error_history = []
        self.integral = 0.0
        self.last_error = 0.0
        self.saturated_ticks = 0
        self.tick_count = 0
    
    def tick(self, obs: dict) -> dict:
        latency_ms = float(obs['latency_ms'])
        setpoint_ms = float(obs['setpoint_ms'])
        
        # Setpoint change detection resets state
        if (self.last_setpoint is not None and 
            abs(self.last_setpoint - setpoint_ms) > 1e-9):
            self.error_history = []
            self.integral = 0.0
            self.last_error = 0.0
        
        self.last_setpoint = setpoint_ms
        error = latency_ms - setpoint_ms
        
        # Track saturation for anti-windup
        is_saturated = self.limit >= self.max_limit - 1e-9
        if is_saturated:
            self.saturated_ticks += 1
        else:
            self.saturated_ticks = 0
        
        # Maintain error history window
        self.error_history.append(error)
        if len(self.error_history) > 50:
            self.error_history.pop(0)
        
        # Calculate recency-weighted statistics
        n = len(self.error_history)
        if n > 0:
            weights = [float(i + 1) for i in range(n)]
            sum_weights = sum(weights)
            weighted_mean = sum(
                w * e for w, e in zip(weights, self.error_history)
            ) / sum_weights
            weighted_var = sum(
                w * (e - weighted_mean) ** 2
                for w, e in zip(weights, self.error_history)
            ) / sum_weights
            std_dev = math.sqrt(weighted_var)
        else:
            weighted_mean = 0.0
            std_dev = 0.0
        
        # Asymmetric proportional gains (down > 2 * up)
        if error > 0:
            p_term = -0.003 * error  # k_down
        else:
            p_term = -0.001 * error  # k_up (ratio 3:1 > 2:1)
        
        # Derivative term for trend response
        d_term = -0.0005 * (error - self.last_error)
        
        # Anti-windup integral (don't accumulate during saturation)
        if not is_saturated:
            self.integral += error * 0.00001
            self.integral = max(-5.0, min(5.0, self.integral))
        else:
            self.integral *= 0.99
        
        self.last_error = error
        
        # Volatility suppression (higher variance = more conservative)
        volatility_factor = 1.0 / (1.0 + 10.0 * std_dev / (setpoint_ms + 1.0))
        
        # Apply control law
        delta = (p_term + d_term + self.integral) * volatility_factor
        new_limit = self.limit + delta
        new_limit = max(self.min_limit, min(self.max_limit, new_limit))
        self.limit = new_limit
        
        # Fixed telemetry schema
        telemetry = {
            'limit': float(new_limit),
            'error_ms': float(error),
            'mean_error_ms': float(weighted_mean),
            'std_error_ms': float(std_dev),
            'integral_term': float(self.integral),
            'saturated_ticks': int(self.saturated_ticks),
            'history_len': int(len(self.error_history)),
        }
        
        self.tick_count += 1
        
        return {
            'limit': new_limit,
            'telemetry': telemetry
        }
    
    def snapshot(self) -> str:
        state = {
            'limit': self.limit,
            'min_limit': self.min_limit,
            'max_limit': self.max_limit,
            'last_setpoint': self.last_setpoint,
            'error_history': self.error_history,
            'integral': self.integral,
            'last_error': self.last_error,
            'saturated_ticks': self.saturated_ticks,
            'tick_count': self.tick_count,
        }
        return json.dumps(state)
    
    @classmethod
    def restore(cls, blob: str) -> "Controller":
        state = json.loads(blob)
        controller = cls({
            'min_limit': state['min_limit'],
            'max_limit': state['max_limit'],
            'initial_limit': state['limit'],
        })
        controller.limit = float(state['limit'])
        controller.last_setpoint = state['last_setpoint']
        controller.error_history = [float(e) for e in state['error_history']]
        controller.integral = float(state['integral'])
        controller.last_error = float(state['last_error'])
        controller.saturated_ticks = int(state['saturated_ticks'])
        controller.tick_count = int(state['tick_count'])
        return controller
