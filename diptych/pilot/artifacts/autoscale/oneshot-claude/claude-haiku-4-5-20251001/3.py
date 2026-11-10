import math

class Controller:
    def __init__(self, config: dict):
        self.min_replicas = config.get('min_replicas', 1)
        self.max_replicas = config.get('max_replicas', 60)
        self.initial_replicas = config.get('initial_replicas', 10)
        self.target_utilization = config.get('target_utilization', 0.6)
        self.tolerance = config.get('tolerance', 0.1)
        self.stabilization_ticks = config.get('stabilization_ticks', 20)
        
        self.history = []
    
    def tick(self, obs: dict) -> dict:
        t = obs['t']
        current = obs['replicas']
        pods = obs['pods']
        
        ready_pods = [p for p in pods if p['ready']]
        starting_count = len(pods) - len(ready_pods)
        ready_count = len(ready_pods)
        
        utilizations = [p['utilization'] for p in ready_pods if p['utilization'] is not None]
        k = len(utilizations)
        
        if k > 0:
            avg_util = sum(utilizations) / k
            ratio = avg_util / self.target_utilization
            
            if 1.0 - self.tolerance <= ratio <= 1.0 + self.tolerance:
                recommendation = current
            else:
                recommendation = math.ceil(k * avg_util / self.target_utilization)
        else:
            recommendation = current
        
        if 0 < k < ready_count:
            original = recommendation
            dampening_factor = k / ready_count
            dampened = current + (recommendation - current) * dampening_factor
            
            if original > current:
                recommendation = math.ceil(dampened)
                recommendation = min(recommendation, original)
            elif original < current:
                recommendation = math.floor(dampened)
                recommendation = max(recommendation, original)
        
        if starting_count > 0 and k > 0:
            hypothetical_avg = k * avg_util / (k + starting_count)
            hypothetical = math.ceil((k + starting_count) * hypothetical_avg / self.target_utilization)
            recommendation = min(recommendation, max(current, hypothetical))
        
        max_increase = max(4, current)
        if recommendation > current:
            recommendation = min(recommendation, current + max_increase)
        
        self.history.append(recommendation)
        
        if len(self.history) > self.stabilization_ticks + 10:
            self.history = self.history[-(self.stabilization_ticks + 10):]
        
        if recommendation < current:
            window = self.history[-self.stabilization_ticks:]
            max_recent = max(window) if window else current
            recommendation = max(recommendation, max_recent)
        
        final = max(self.min_replicas, min(self.max_replicas, recommendation))
        
        return {
            'replicas': final,
            'telemetry': {
                'avg_utilization': sum(utilizations) / k if k > 0 else 0,
                'ready_pods': ready_count,
                'pods_with_metrics': k,
            }
        }
