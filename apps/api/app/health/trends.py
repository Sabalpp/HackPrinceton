"""OWNER: Sabal. Personal-baseline trend detection. Describes changes; never diagnoses.

Baseline = mean of the observations before the recent window (up to 30 days).
Recent   = mean of the last RECENT_DAYS observations.
"""

from statistics import mean

from app.schemas.contracts import HealthObservation, MetricTrend

RECENT_DAYS = 5
MIN_BASELINE_DAYS = 7
# Relative change considered notable, per metric (simple, explainable thresholds).
NOTABLE = {"steps": 0.25, "sleep_minutes": 0.12, "resting_heart_rate": 0.08}
LABELS = {
    "steps": ("daily steps", "{:,.0f}"),
    "sleep_minutes": ("nightly sleep", "{:.0f} min"),
    "resting_heart_rate": ("resting heart rate", "{:.0f} bpm"),
}
DISCLAIMER = (
    "FarCare describes changes compared with this person's own recent history. "
    "It does not diagnose conditions or replace medical advice."
)


def metric_trend(observations: list[HealthObservation], metric: str) -> MetricTrend:
    ordered = sorted(observations, key=lambda o: o.timestamp)
    values = [(o.timestamp, getattr(o, metric)) for o in ordered if getattr(o, metric) is not None]
    recent = [v for _, v in values[-RECENT_DAYS:]]
    baseline = [v for _, v in values[:-RECENT_DAYS][-30:]]
    name, fmt = LABELS[metric]
    if len(recent) < RECENT_DAYS or len(baseline) < MIN_BASELINE_DAYS:
        return MetricTrend(
            metric=metric,
            baseline=None,
            recent=None,
            percent_change=None,
            direction="insufficient_data",
            notable=False,
            observation=f"Not enough {name} data yet to compare with a baseline.",
        )
    b, r = mean(baseline), mean(recent)
    change = (r - b) / b if b else 0.0
    notable = abs(change) >= NOTABLE[metric]
    direction = "stable" if abs(change) < 0.05 else ("up" if change > 0 else "down")
    if direction == "stable":
        text = (
            f"Average {name} over the last {RECENT_DAYS} days ({fmt.format(r)}) is close to usual."
        )
    else:
        word = "higher" if change > 0 else "lower"
        text = (
            f"Average {name} over the last {RECENT_DAYS} days ({fmt.format(r)}) is "
            f"{abs(change):.0%} {word} than usual ({fmt.format(b)})."
        )
    return MetricTrend(
        metric=metric,
        baseline=round(b, 1),
        recent=round(r, 1),
        percent_change=round(change * 100, 1),
        direction=direction,
        notable=notable,
        observation=text,
    )


def compute_trends(observations: list[HealthObservation]) -> list[MetricTrend]:
    return [metric_trend(observations, m) for m in ("steps", "sleep_minutes", "resting_heart_rate")]
