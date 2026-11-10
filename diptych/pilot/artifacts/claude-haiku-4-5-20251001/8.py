import json
import math

class Controller:
    def __init__(self, config: dict):
        self.min_limit = config.get('min_limit', 1.0)
        self.max_limit = config.get('max_limit', 200.0)
        self.initial_limit = config.get('initial_limit', 50.0)
        
        self.limit = self.initial_limit
        self.error_ema = 0.0
        self.error_history = []
        self.saturated_ticks = 0
        self.last_setpoint = None
        self.tick_index = 0
        
        self.ema_alpha = 0.3
        self.history_window = 20
        self.gain_down = 0.5
        self.gain_up = 0.2
        
    def tick(self, obs: dict) -> dict:
        latency_ms = obs['latency_ms']
        setpoint_ms = obs['setpoint_ms']
        
        if self.last_setpoint is not None and self.last_setpoint != setpoint_ms:
            self.error_history = []
            self.error_ema = 0.0
            self.saturated_ticks = 0
        
        self.last_setpoint = setpoint_ms
        
        error = latency_ms - setpoint_ms
        
        self.error_history.append(error)
        if len(self.error_history) > self.history_window:
            self.error_history.pop(0)
        
        if self.tick_index == 0:
            self.error_ema = error
        else:
            self.error_ema = self.ema_alpha * error + (1 - self.ema_alpha) * self.error_ema
        
        was_saturated = self.saturated_ticks > 0
        is_saturated = self.limit >= self.max_limit * 0.99
        
        if was_saturated and not is_saturated:
            self.error_ema = error
            self.error_history = [error]
        
        if len(self.error_history) > 1:
            mean_error = sum(self.error_history) / len(self.error_history)
            variance = sum((e - mean_error) ** 2 for e in self.error_history) / len(self.error_history)
            std_dev = math.sqrt(variance)
        else:
            std_dev = 0.0
        
        volatility_factor = 1.0
        if setpoint_ms > 0 and std_dev > 0:
            cv = std_dev / setpoint_ms
            if cv > 0.02:
                volatility_factor = 1.0 + cv * 3.0
        
        if error > 0:
            correction = -error * self.gain_down * volatility_factor
        else:
            correction = -error * self.gain_up / volatility_factor
        
        new_limit = self.limit + correction
        new_limit = max(self.min_limit, min(self.max_limit, new_limit))
        self.limit = new_limit
        
        if is_saturated:
            self.saturated_ticks += 1
        else:
            self.saturated_ticks = 0
        
        self.tick_index += 1
        
        telemetry = {
            'error': error,
            'error_ema': self.error_ema,
            'std_dev': std_dev,
            'volatility_factor': volatility_factor,
            'correction': correction,
            'saturated_ticks': self.saturated_ticks,
            'limit': new_limit,
        }
        
        return {
            'limit': new_limit,
            'telemetry': telemetry,
        }
    
    def snapshot(self) -> str:
        state = {
            'min_limit': self.min_limit,
            'max_limit': self.max_limit,
            'initial_limit': self.initial_limit,
            'limit': self.limit,
            'error_ema': self.error_ema,
            'error_history': self.error_history,
            'saturated_ticks': self.saturated_ticks,
            'last_setpoint': self.last_setpoint,
            'tick_index': self.tick_index,
            'ema_alpha': self.ema_alpha,
            'history_window': self.history_window,
            'gain_down': self.gain_down,
            'gain_up': self.gain_up,
        }
        return json.dumps(state)
    
    @classmethod
    def restore(cls, blob: str) -> 'Controller':
        state = json.loads(blob)
        config = {
            'min_limit': state['min_limit'],
            'max_limit': state['max_limit'],
            'initial_limit': state['initial_limit'],
        }
        controller = cls(config)
        controller.limit = state['limit']
        controller.error_ema = state['error_ema']
        controller.error_history = state['error_history']
        controller.saturated_ticks = state['saturated_ticks']
        controller.last_setpoint = state['last_setpoint']
        controller.tick_index = state['tick_index']
        controller.ema_alpha = state['ema_alpha']
        controller.history_window = state['history_window']
        controller.gain_down = state['gain_down']
        controller.gain_up = state['gain_up']
        return controller
