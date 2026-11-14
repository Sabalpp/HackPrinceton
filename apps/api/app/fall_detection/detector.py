"""Explainable threshold-based activity / possible-fall classifier for a 6 s IMU window.

Classic multi-phase heuristic: free-fall dip -> impact spike -> post-impact stillness +
orientation change => possible fall. Output `score` is a heuristic detector score in
[0, 1]; it is NOT a validated clinical probability and this is NOT a medical device.
"""

import math
from dataclasses import asdict, dataclass
from statistics import fmean, pstdev

from app.fall_detection.traces import FS, Sample

IMPACT_G = 2.4
FREE_FALL_G = 0.6
STILL_STD_G = 0.1
FALL_ANGLE_DEG = 45


@dataclass(frozen=True)
class Detection:
    label: str  # walking | sitting | bending | lying_down | possible_fall | recovered_after_impact
    score: float
    features: dict

    def to_dict(self) -> dict:
        return asdict(self)


def _mag(s: Sample) -> float:
    return math.sqrt(s[0] ** 2 + s[1] ** 2 + s[2] ** 2)


def _mean_vec(samples: list[Sample]) -> Sample:
    return (
        fmean(s[0] for s in samples),
        fmean(s[1] for s in samples),
        fmean(s[2] for s in samples),
    )


def _angle(a: Sample, b: Sample) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na, nb = _mag(a), _mag(b)
    if na == 0 or nb == 0:
        return 0.0
    return math.degrees(math.acos(max(-1.0, min(1.0, dot / (na * nb)))))


def classify(samples: list[Sample], fs: int = FS) -> Detection:
    if len(samples) < 3 * fs:
        raise ValueError("need at least 3 seconds of samples")
    mags = [_mag(s) for s in samples]
    peak_i = max(range(len(mags)), key=mags.__getitem__)
    peak = mags[peak_i]
    pre = mags[max(0, peak_i - fs) : peak_i] or [1.0]
    free_fall = min(pre)
    start_vec = _mean_vec(samples[:fs])
    end_vec = _mean_vec(samples[-fs:])
    post = samples[peak_i + fs // 4 : peak_i + fs // 4 + 2 * fs]
    post_std = pstdev([_mag(s) for s in post]) if len(post) > 2 else 1.0
    post_angle = _angle(start_vec, _mean_vec(post)) if post else 0.0
    features = {
        "peak_g": round(peak, 2),
        "min_g_before_peak": round(free_fall, 2),
        "post_impact_std_g": round(post_std, 3),
        "orientation_change_deg": round(post_angle, 1),
        "overall_std_g": round(pstdev(mags), 3),
    }

    if peak >= IMPACT_G:
        if post_std < STILL_STD_G and post_angle >= FALL_ANGLE_DEG:
            score = (
                0.35 * min(1.0, (peak - IMPACT_G) / 1.5)
                + 0.25 * (1.0 if free_fall <= FREE_FALL_G else 0.3)
                + 0.2 * max(0.0, (STILL_STD_G - post_std) / STILL_STD_G)
                + 0.2 * min(1.0, post_angle / 90)
            )
            return Detection("possible_fall", round(min(score, 0.99), 2), features)
        return Detection("recovered_after_impact", 0.0, features)

    total_angle = _angle(start_vec, end_vec)
    windows = [samples[i : i + fs // 2] for i in range(0, len(samples) - fs // 2 + 1, fs // 2)]
    max_tilt = max(_angle(start_vec, _mean_vec(w)) for w in windows)
    features.update(
        total_orientation_change_deg=round(total_angle, 1), max_tilt_deg=round(max_tilt, 1)
    )
    if total_angle >= 60 and features["overall_std_g"] < 0.25:
        return Detection("lying_down", 0.0, features)
    if max_tilt >= 40 and total_angle < 25:
        return Detection("bending", 0.0, features)
    if features["overall_std_g"] >= 0.12:
        return Detection("walking", 0.0, features)
    return Detection("sitting", 0.0, features)
