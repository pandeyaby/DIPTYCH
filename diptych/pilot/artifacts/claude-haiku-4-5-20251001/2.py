import json
import math

class Controller:
    def __init__(self, config: dict):
        self.min_limit = float(config['min_limit'])
        self.max_limit = float(config['max_limit'])
        self.limit = float(config['initial_limit'])
        
        # Trend recency via exponential moving average of error
        self.ema_error = 0.0
        self.ema_initialized = False
        self.ema_alpha = 0.25
        
        # Volatility estimation via exponential moving average of squared error
        self.variance_ema = 0.0
        self.variance_alpha = 0.20
        
        # Anti-windup: track ticks at maximum limit
        self.saturated_ticks = 0
        
        # Setpoint tracking for reset on change
        self.last_setpoint = None
        
        # Control gains (asymmetric: kp_down > 2 * kp_up)
        self.kp_up = 0.25
        self.kp_down = 0.65
    
    def tick(self, obs: dict) -> dict:
        latency = float(obs['latency_ms'])
        setpoint = float(obs['setpoint_ms'])
        
        # Setpoint change: reset history for quiet transition
        if self.last_setpoint != setpoint:
            self.ema_error = 0.0
            self.ema_initialized = False
            self.variance_ema = 0.0
            self.saturated_ticks = 0
            self.last_setpoint = setpoint
        
        # Calculate current error
        error = latency - setpoint
        
        # Update trend estimate using exponential moving average
        if not self.ema_initialized:
            self.ema_error = error
            self.ema_initialized = True
        else:
            self.ema_error = self.ema_alpha * error + (1.0 - self.ema_alpha) * self.ema_error
        
        # Update variance estimate using EMA of squared error
        error_squared = error * error
        if self.variance_ema == 0.0:
            self.variance_ema = error_squared
        else:
            self.variance_ema = self.variance_alpha * error_squared + (1.0 - self.variance_alpha) * self.variance_ema
        
        # Asymmetric proportional control: strong response to high latency
        if self.ema_error > 0.0:
            correction = -self.kp_down * self.ema_error
        else:
            correction = -self.kp_up * self.ema_error
        
        # Track saturation state for anti-windup
        at_max_limit = self.limit >= self.max_limit - 1e-12
        was_saturated = self.saturated_ticks > 0
        
        if at_max_limit:
            self.saturated_ticks += 1
        else:
            # Anti-windup: bound response when exiting saturation
            if was_saturated and error <= 0.0:
                correction = min(correction, -error * 0.20)
            self.saturated_ticks = 0
        
        # Volatility suppression: conservative adjustments on high variance
        volatility = math.sqrt(self.variance_ema) if self.variance_ema > 0.0 else 0.0
        if volatility > 1e-10:
            suppression_factor = 1.0 / (1.0 + volatility)
            correction *= suppression_factor
        
        # Apply correction and enforce bounds
        self.limit += correction
        self.limit = max(self.min_limit, min(self.max_limit, self.limit))
        
        # Telemetry with stable schema
        telemetry = {
            'correction': correction,
            'error': error,
            'ema_error': self.ema_error,
            'limit': self.limit,
            'saturated': float(at_max_limit),
            'variance': self.variance_ema,
            'volatility': volatility,
        }
        
        return {
            'limit': self.limit,
            'telemetry': telemetry
        }
    
    def snapshot(self) -> str:
        state = {
            'ema_alpha': self.ema_alpha,
            'ema_error': self.ema_error,
            'ema_initialized': self.ema_initialized,
            'kp_down': self.kp_down,
            'kp_up': self.kp_up,
            'last_setpoint': self.last_setpoint,
            'limit': self.limit,
            'max_limit': self.max_limit,
            'min_limit': self.min_limit,
            'saturated_ticks': self.saturated_ticks,
            'variance_alpha': self.variance_alpha,
            'variance_ema': self.variance_ema,
        }
        return json.dumps(state, separators=(',', ':'), sort_keys=True)
    
    @classmethod
    def restore(cls, blob: str) -> "Controller":
        state = json.loads(blob)
        config = {
            'min_limit': state['min_limit'],
            'max_limit': state['max_limit'],
            'initial_limit': state['limit'],
        }
        controller = cls(config)
        controller.ema_alpha = state['ema_alpha']
        controller.ema_error = state['ema_error']
        controller.ema_initialized = state['ema_initialized']
        controller.kp_down = state['kp_down']
        controller.kp_up = state['kp_up']
        controller.last_setpoint = state['last_setpoint']
        controller.saturated_ticks = state['saturated_ticks']
        controller.variance_alpha = state['variance_alpha']
        controller.variance_ema = state['variance_ema']
        return controller
