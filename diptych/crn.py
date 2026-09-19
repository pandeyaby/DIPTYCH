"""CRN closed-loop twin helpers for TRAJSWAP and VARSCALE."""
from __future__ import annotations
import hashlib, random
from dataclasses import dataclass
from typing import Sequence

def stream_id(master_seed: int, family: str) -> str:
    return hashlib.sha256(f"aomb.crn.{family}:{master_seed}".encode()).hexdigest()[:12]

@dataclass(frozen=True)
class VarTwins:
    stream_id: str
    mean: float
    scale_a: float
    scale_b: float
    a: tuple[float, ...]
    b: tuple[float, ...]

@dataclass(frozen=True)
class TrajTwins:
    stream_id: str
    drift_a: float
    drift_b: float
    traj_a: tuple[float, ...]
    traj_b: tuple[float, ...]
    residual: tuple[float, ...]

def make_varscale(*, master_seed: int, n: int, mean: float, scale_a: float, scale_b: float) -> VarTwins:
    rng = random.Random(master_seed)
    z = [rng.gauss(0.0, 1.0) for _ in range(n)]
    return VarTwins(
        stream_id=stream_id(master_seed, "varscale"),
        mean=mean, scale_a=scale_a, scale_b=scale_b,
        a=tuple(mean + scale_a * zi for zi in z),
        b=tuple(mean + scale_b * zi for zi in z),
    )

def make_traj(*, master_seed: int, n: int, drift_a: float, drift_b: float, noise: float = 1.0) -> TrajTwins:
    rng = random.Random(master_seed)
    z = [rng.gauss(0.0, 1.0) for _ in range(n)]
    ta = tuple(drift_a * i + noise * zi for i, zi in enumerate(z))
    tb = tuple(drift_b * i + noise * zi for i, zi in enumerate(z))
    return TrajTwins(
        stream_id=stream_id(master_seed, "trajswap"),
        drift_a=drift_a, drift_b=drift_b,
        traj_a=ta, traj_b=tb,
        residual=tuple(abs(ta[i] - tb[i]) for i in range(n)),
    )

def mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs)

def variance(xs: Sequence[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = mean(xs)
    return sum((x - m) ** 2 for x in xs) / (len(xs) - 1)

def max_abs(a: Sequence[float], b: Sequence[float], n: int | None = None) -> float:
    k = min(len(a), len(b)) if n is None else min(len(a), len(b), n)
    if k <= 0:
        return float("inf")
    return max(abs(a[i] - b[i]) for i in range(k))

def prove_varscale(a, b, *, mean_v, scale_a, scale_b, tol=1e-8):
    if len(a) != len(b) or scale_a <= 0:
        return False, {}
    z = [(x - mean_v) / scale_a for x in a]
    b_hat = [mean_v + scale_b * zi for zi in z]
    err = max_abs(b, b_hat)
    return err <= tol, {
        "reconstruct_err": err,
        "mean_a": mean(a), "mean_b": mean(b),
        "var_a": variance(a), "var_b": variance(b),
    }

def prove_traj(a, b, *, drift_a, drift_b, tol=1e-8):
    if len(a) != len(b):
        return False, {}
    na = [a[i] - drift_a * i for i in range(len(a))]
    nb = [b[i] - drift_b * i for i in range(len(b))]
    err = max_abs(na, nb)
    return err <= tol, {"shared_noise_err": err}
