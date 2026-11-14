"""OWNER: Sabal. Personal-baseline trend detection. Describes changes; never diagnoses.

Baseline = mean of valid distinct dates in the 30 days before the recent window.
Recent   = mean of the RECENT_DAYS calendar dates ending on the latest observed date.
Sources and data modes are never combined into a comparison.
"""

from datetime import UTC, date, timedelta
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
    name, fmt = LABELS[metric]

    def insufficient(reason: str) -> MetricTrend:
        return MetricTrend(
            metric=metric,
            baseline=None,
            recent=None,
            percent_change=None,
            direction="insufficient_data",
            notable=False,
            observation=reason,
        )

    not_enough = f"Not enough {name} data yet to compare with a baseline."
    if not observations:
        return insufficient(not_enough)

    # The shared contract defines UTC daily observations. SQLite returns naive
    # UTC datetimes, which must not be interpreted in the server's local zone.
    dated = [
        (observation.timestamp.replace(tzinfo=UTC).date(), observation)
        if observation.timestamp.tzinfo is None
        else (observation.timestamp.astimezone(UTC).date(), observation)
        for observation in observations
    ]
    latest_day = max(day for day, _ in dated)
    recent_start = latest_day - timedelta(days=RECENT_DAYS - 1)
    baseline_start = recent_start - timedelta(days=30)
    candidates = [
        (day, observation, getattr(observation, metric))
        for day, observation in dated
        if baseline_start <= day <= latest_day and getattr(observation, metric) is not None
    ]
    if len({(observation.source, observation.data_mode) for _, observation, _ in candidates}) > 1:
        return insufficient(
            f"Cannot compare {name} across different sources or data modes. "
            "Keep live and simulated histories separate."
        )

    # A retry can repeat a daily total; two differing totals have no ordering or
    # revision metadata here, so neither can safely represent that date.
    values_by_day: dict[date, set[int]] = {}
    for day, _, value in candidates:
        values_by_day.setdefault(day, set()).add(value)
    ambiguous_days = sum(len(values) > 1 for values in values_by_day.values())
    daily_values = {
        day: next(iter(values)) for day, values in values_by_day.items() if len(values) == 1
    }
    recent = [value for day, value in daily_values.items() if day >= recent_start]
    baseline = [value for day, value in daily_values.items() if day < recent_start]
    excluded = " Conflicting values for the same date were excluded." if ambiguous_days else ""
    if len(recent) < RECENT_DAYS or len(baseline) < MIN_BASELINE_DAYS:
        return insufficient(not_enough + excluded)
    b, r = mean(baseline), mean(recent)
    if b == 0:
        return insufficient(
            f"A percentage change in {name} cannot be calculated because the baseline is zero."
            + excluded
        )
    change = (r - b) / b
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
        observation=text + excluded,
    )


def compute_trends(observations: list[HealthObservation]) -> list[MetricTrend]:
    return [metric_trend(observations, m) for m in ("steps", "sleep_minutes", "resting_heart_rate")]
