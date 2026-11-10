import math
from collections import deque


class Controller:
    def __init__(self, config: dict):
        self.config = config
        self.min_replicas = config.get('min_replicas', 1)
        self.max_replicas = config.get('max_replicas', 60)
        self.initial_replicas = config.get('initial_replicas', 10)
        self.target_utilization = config.get('target_utilization', 0.6)
        self.tolerance = config.get('tolerance', 0.1)
        self.stabilization_ticks = config.get('stabilization_ticks', 20)
        
        self.rec_history = deque(maxlen=self.stabilization_ticks + 10)
        self.current_replicas = self.initial_replicas
    
    def tick(self, obs: dict) -> dict:
        current_replicas = obs['replicas']
        pods = obs['pods']
        
        ready_pods = [p for p in pods if p['ready']]
        ready_with_metrics = [p['utilization'] for p in ready_pods if p['utilization'] is not None]
        missing_metrics = len(ready_pods) - len(ready_with_metrics)
        starting_pods = len(pods) - len(ready_pods)
        
        if len(ready_with_metrics) == 0:
            recommendation = current_replicas
        else:
            avg_util = sum(ready_with_metrics) / len(ready_with_metrics)
            ratio = avg_util / self.target_utilization
            
            if abs(ratio - 1.0) <= self.tolerance:
                recommendation = current_replicas
            else:
                recommendation = math.ceil(len(ready_with_metrics) * avg_util / self.target_utilization)
        
        if missing_metrics > 0 and len(ready_with_metrics) > 0:
            avg_util = sum(ready_with_metrics) / len(ready_with_metrics)
            total_ready = len(ready_pods)
            avg_all = (sum(ready_with_metrics) + missing_metrics * avg_util) / total_ready
            ratio_all = avg_all / self.target_utilization
            
            if abs(ratio_all - 1.0) <= self.tolerance:
                rec_with_all = current_replicas
            else:
                rec_with_all = math.ceil(total_ready * avg_all / self.target_utilization)
            
            lower = min(current_replicas, rec_with_all)
            upper = max(current_replicas, rec_with_all)
            recommendation = max(lower, min(recommendation, upper))
        
        if starting_pods > 0 and len(ready_pods) > 0:
            total_pods = len(pods)
            avg_with_zeros = (sum(ready_with_metrics) if ready_with_metrics else 0) / total_pods
            ratio_with_zeros = avg_with_zeros / self.target_utilization
            
            if abs(ratio_with_zeros - 1.0) <= self.tolerance:
                rec_with_starting = current_replicas
            else:
                rec_with_starting = math.ceil(total_pods * avg_with_zeros / self.target_utilization)
            
            recommendation = min(recommendation, max(current_replicas, rec_with_starting))
        
        if recommendation > current_replicas:
            max_increase = max(4, current_replicas)
            recommendation = min(recommendation, current_replicas + max_increase)
        
        self.rec_history.append(recommendation)
        if recommendation < current_replicas and len(self.rec_history) > 0:
            max_past = max(self.rec_history)
            recommendation = max(recommendation, max_past)
        
        new_replicas = max(self.min_replicas, min(self.max_replicas, recommendation))
        
        telemetry = {
            'recommendation': recommendation,
            'new_replicas': new_replicas,
        }
        
        return {
            'replicas': new_replicas,
            'telemetry': telemetry
        }
