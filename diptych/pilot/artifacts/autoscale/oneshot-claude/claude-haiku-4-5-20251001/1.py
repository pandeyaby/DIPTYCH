import math
from collections import deque

class Controller:
    def __init__(self, config: dict):
        self.min_replicas = config.get('min_replicas', 1)
        self.max_replicas = config.get('max_replicas', 60)
        self.initial_replicas = config.get('initial_replicas', 10)
        self.target_utilization = config.get('target_utilization', 0.6)
        self.tolerance = config.get('tolerance', 0.1)
        self.stabilization_ticks = config.get('stabilization_ticks', 20)
        
        self.current_replicas = self.initial_replicas
        self.rec_history = deque(maxlen=self.stabilization_ticks + 10)
    
    def tick(self, obs: dict) -> dict:
        t = obs['t']
        replicas = obs['replicas']
        pods = obs['pods']
        
        ready = [p for p in pods if p['ready']]
        with_metrics = [p for p in ready if p['utilization'] is not None]
        without_metrics = [p for p in ready if p['utilization'] is None]
        starting = [p for p in pods if not p['ready']]
        
        if with_metrics:
            k = len(with_metrics)
            u = sum(p['utilization'] for p in with_metrics) / k
            ratio = u / self.target_utilization
            
            if abs(ratio - 1.0) <= self.tolerance:
                recommendation = self.current_replicas
            else:
                recommendation = math.ceil(k * u / self.target_utilization)
        else:
            recommendation = self.current_replicas
        
        desired = recommendation
        
        if without_metrics:
            if desired > self.current_replicas:
                desired = self.current_replicas
        
        if starting:
            if desired > self.current_replicas:
                cap = len(ready) + len(starting)
                desired = min(desired, cap)
            desired = max(desired, self.current_replicas)
        
        if desired > self.current_replicas:
            max_increase = max(4, self.current_replicas)
            desired = min(desired, self.current_replicas + max_increase)
        
        self.rec_history.append(recommendation)
        
        if desired < self.current_replicas and self.rec_history:
            desired = max(desired, max(self.rec_history))
        
        desired = max(self.min_replicas, min(self.max_replicas, desired))
        
        self.current_replicas = desired
        
        telemetry = {
            'tick': t,
            'desired_replicas': desired,
            'recommendation': recommendation,
        }
        
        if with_metrics:
            telemetry['utilization'] = u
            telemetry['ready_with_metrics'] = k
        
        if without_metrics:
            telemetry['ready_without_metrics'] = len(without_metrics)
        
        if starting:
            telemetry['starting_pods'] = len(starting)
        
        return {
            'replicas': desired,
            'telemetry': telemetry,
        }
