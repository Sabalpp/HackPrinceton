"""Labeled synthetic tri-axial accelerometer traces (units: g, 50 Hz, 6 s).

These are SIMULATED signals shaped after the phases described in fall-detection literature
(walking, free-fall dip, impact spike, post-impact stillness, orientation change). They
exist so the end-to-end pipeline can be demonstrated without raw IMU access (the Google
Health API exposes none). Replace / extend with public datasets (e.g. SisFall, KFall) in
research work — see docs/agents/SUMIT.md.

Axis convention: z is vertical when the wearer is upright; lying down puts gravity on x.
"""

import math
import random

FS = 50
DURATION_S = 6.0
Sample = tuple[float, float, float]

KINDS = ("walking", "sitting", "bending", "lying_down", "fall", "recovery")


def _orient(angle_deg: float) -> Sample:
    a = math.radians(angle_deg)  # 0 = upright (gravity on z), 90 = lying (gravity on x)
    return (math.sin(a), 0.0, math.cos(a))


def _walk(t: float) -> Sample:
    return (
        0.08 * math.sin(2 * math.pi * 0.9 * t),
        0.06 * math.sin(2 * math.pi * 1.8 * t + 1),
        1.0 + 0.25 * math.sin(2 * math.pi * 1.8 * t),
    )


def generate(kind: str, seed: int = 1) -> list[Sample]:
    if kind not in KINDS:
        raise ValueError(f"unknown trace kind {kind!r}; choose from {KINDS}")
    rng = random.Random(f"{kind}:{seed}")  # noqa: S311 reproducible synthetic data
    n = int(FS * DURATION_S)
    out: list[Sample] = []
    for i in range(n):
        t = i / FS
        if kind == "walking":
            x, y, z = _walk(t)
        elif kind == "sitting":
            ang = 0 if t < 2 else (17 if t > 3 else 17 * (t - 2))
            x, y, z = _orient(ang)
            if 2.6 <= t < 2.9:  # settle into chair: small dip then bump
                z *= 0.7 if t < 2.75 else 1.4
        elif kind == "bending":
            ang = 65 * math.sin(math.pi * (t - 1.5) / 3) if 1.5 <= t <= 4.5 else 0
            x, y, z = _orient(ang)
        elif kind == "lying_down":
            ang = 0 if t < 1.5 else (90 if t > 3.5 else 45 * (t - 1.5))
            x, y, z = _orient(ang)
        elif kind == "fall":
            if t < 2.0:
                x, y, z = _walk(t)
            elif t < 2.35:  # free fall
                x, y, z = (0.1, 0.05, 0.2)
            elif t < 2.45:  # impact
                x, y, z = (2.6, 0.8, 2.2)
            else:  # motionless on the floor
                x, y, z = _orient(85)
        else:  # recovery: stumble with impact, then keeps walking upright
            if 2.0 <= t < 2.2:
                x, y, z = (0.15, 0.05, 0.4)
            elif 2.2 <= t < 2.3:
                x, y, z = (1.2, 0.5, 2.5)
            else:
                x, y, z = _walk(t)
        noise = 0.015
        out.append((x + rng.gauss(0, noise), y + rng.gauss(0, noise), z + rng.gauss(0, noise)))
    return out
