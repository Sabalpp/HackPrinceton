# Personal health references and blood-pressure guidance

Evidence reviewed: 14 November 2026. Implementation: `backend/app/health/personal.py`.
This is an adult, nonpregnant MVP for recording measurements and showing descriptive
history. It does not diagnose hypertension, prescribe treatment, establish that a
reading is safe, or contact emergency services. Clinical review and validation of
the complete product remain outstanding.

## Three different concepts

| Concept | Meaning in FarCare | What it cannot do |
| --- | --- | --- |
| Stated reference | A value explicitly supplied by the person; always provisional | Establish a medically safe range or suppress safety guidance |
| Observed history | Median and variability from eligible completed local dates | Diagnose a condition, prove health, or establish treatment targets |
| Safety guidance | Conservative advice for reported warning symptoms or severe cuff readings | Verify the device, determine the cause, or replace clinical assessment |

There is no default “normal for me” of 120/80, no population value silently used as
a personal baseline, and no blood pressure inferred from Fitbit heart rate. Instant
heart-rate samples do not become resting heart rate or HRV. The app can record
manually entered cuff readings; the `device_validated` checkbox is the user's
attestation, **not independent verification by FarCare**.

## Clinical sources and bounded use

**Home measurement.** The American Heart Association recommends an independently
validated automatic upper-arm monitor with an appropriate cuff. Its instructions
include quiet seated rest for five minutes and two readings a minute apart. Model,
cuff size, measurement technique, and suitability for the individual matter. A
checkbox alone cannot establish measurement accuracy. [AHA home monitoring](https://www.heart.org/en/health-topics/high-blood-pressure/understanding-blood-pressure-readings/monitoring-your-blood-pressure-at-home)

**Diagnostic schedules differ from app learning.** NICE's diagnostic home-monitoring
protocol uses two seated readings at least a minute apart per session, morning and
evening, for at least four days and preferably seven. The first day's readings are
discarded when calculating the diagnostic average. The 2024 ESC guideline describes
morning/evening sessions, two readings one to two minutes apart, for three to seven
days, extending to seven when appropriate. FarCare implements neither diagnostic
average or schedule and does not claim that its seven qualified dates satisfy them.
[NICE NG136, recommendation 1.2.7](https://www.nice.org.uk/guidance/ng136/chapter/recommendations),
[2024 ESC guideline](https://academic.oup.com/eurheartj/article/45/38/3912/7741010)

**Severe readings and symptoms.** AHA home guidance describes systolic above 180
and/or diastolic above 120: without concerning symptoms, wait at least a minute and
repeat; a persistently very high reading warrants prompt contact with a healthcare
professional. With concerning symptoms, seek emergency help without waiting for
another measurement. FarCare includes equality (`systolic >= 180 OR diastolic >=
120`) as a conservative product boundary. AHA's home wording uses “higher than,”
whereas NICE's severe *clinic* pathway includes equality. Applying equality to this
home workflow is an explicit conservative design choice, not a claim that these
different clinical pathways are interchangeable. The app does not tell a person
with severe home readings to wait seven days. [AHA severe-BP guidance](https://www.heart.org/en/health-topics/high-blood-pressure/understanding-blood-pressure-readings/when-to-call-911-for-high-blood-pressure),
[NICE NG136, recommendations 1.5.1–1.5.2](https://www.nice.org.uk/guidance/ng136/chapter/recommendations)

**Symptoms take priority.** New chest pain, shortness of breath, new weakness or
numbness, vision change, difficulty speaking, or sudden severe headache trigger
advice to contact local emergency services, even if a BP number is missing or not
high. These represent current warning symptoms, not historical diagnoses. The
checklist is not exhaustive and does not screen out every emergency. Age/pregnancy
scope confirmation is required for adult numeric BP interpretation, but its absence
does not suppress warning-symptom guidance. [AHA heart-attack warning signs](https://www.heart.org/health-topics/heart-attack/warning-signs-of-a-heart-attack),
[CDC stroke warning signs](https://www.cdc.gov/stroke/signs-symptoms/)

**Low readings.** AHA describes low BP around below 90/60 and emphasizes the role of
symptoms and individual context. FarCare flags either systolic below 90 or diastolic
below 60 for review; this component-wise rule is a conservative app interpretation.
It does not label every low reading an emergency. Asymptomatic, otherwise eligible
low readings can appear in observed history; a personal reference never proves they
are safe. [AHA low-BP information](https://www.heart.org/en/health-topics/high-blood-pressure/the-facts-about-high-blood-pressure/low-blood-pressure-when-blood-pressure-is-too-low)

**Device limits.** FDA warns against unauthorized devices and software claiming to
measure BP, including some wearable products, because inaccurate readings can lead
to harmful decisions. This is not a claim that every cuffless device is invalid.
FarCare's MVP accepts reported cuff measurements only; Fitbit measurements never
supply BP. [FDA safety communication](https://www.fda.gov/medical-devices/safety-communications/do-not-use-unauthorized-devices-measuring-blood-pressure-fda-safety-communication)

## Engineering rules, not clinical thresholds

The following constants require product and clinical validation; none is a
guideline-backed promise of detection performance:

- Learning uses the previous 28 **completed local dates**, excluding today, with a
  target of seven eligible distinct dates. They need not be consecutive.
- BP needs two otherwise eligible readings on a date, at least 60 seconds apart.
  This is app coverage, not NICE's twice-daily diagnostic protocol. Paired original
  systolic/diastolic values, live mode, adult/nonpregnant confirmation, a reported
  validated cuff, and a completed symptom check are required.
- Wearable metrics require one reported daily summary per date from one source and
  device stream. Steps, reported resting heart rate, and main-sleep minutes retain
  their original meanings. Missing values stay missing; actual reported zero steps
  remain zero. Incomplete current-day totals cannot qualify.
- Live and simulated records, different source identifiers, or different device
  streams cannot be pooled. Pure simulation cannot establish a real baseline.
- Identical duplicates do not increase coverage. Conflicting simultaneous readings
  are excluded; conflicting daily summaries are excluded. BP can legitimately vary
  within a day, so qualified readings are reduced to a daily median. Dates have
  equal weight regardless of the number of readings that day.
- A marked acute/symptomatic/severe-BP date is excluded in full. An explicit unstable
  period pauses learning and comparison. Unreported illness, medication changes,
  activity, sleep context, or device changes remain possible confounders.
- At seven eligible dates, the center is the median of daily values and spread is
  the raw median absolute deviation (MAD). Optional comparisons use center ± three
  raw MAD; a value exactly on the boundary stays inside. This is not three standard
  deviations, has no claimed false-alarm rate, and is not a medical target. Zero MAD
  means no reliable deviation band is estimated. Negative lower bounds are clipped
  to zero for these nonnegative metrics.
- A current comparison cannot reuse a training date, a future timestamp, a different
  source/device/mode, or an acute/unqualified reading. Before observed learning is
  ready, a supplied reference permits only a labeled arithmetic difference with no
  safe/unsafe band. Absolute BP guidance always runs separately.
- A repeat severe BP is considered linked only when it is 60–3600 seconds after a
  prior severe cuff reading for the same supplied person/device identity. The
  one-hour maximum is an engineering association limit. The caller must authorize
  both readings as belonging to the same person; it must not silently pair people.

Within-person wearable histories have research value: a large observational study
of daily resting heart rate found substantial differences between people and
changes within individuals. It does not validate FarCare's seven-day rule, MAD
band, emergency detection, or performance in frail older adults. [Quer et al., PLOS
ONE (2020)](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0227709)
MAD is a standard robust measure of spread; choosing it does not make a health
alert clinically validated. [NIST, measures of scale](https://itl.nist.gov/div898/handbook/eda/section3/eda356.htm)

## Pure-function contracts and integration requirements

`assess_bp(reading, *, previous_reading=None, reference=None)` accepts the explicit
fields `systolic`, `diastolic`, timezone-aware `measured_at`, `source`, `data_mode`,
`device_validated`, `adult_nonpregnant`, `symptoms_checked`, and `symptoms`. Optional
`parent_id` and `source_stream` must agree for repeat association. `source` is
`manual_cuff`; `data_mode` is `live` or `simulated`. Missing symptom checks are never
interpreted as absence of symptoms. Numeric inputs must be finite, nonboolean,
positive, and ordered systolic greater than diastolic. Red-flag symptom advice takes
priority even when numeric data is absent. Simulated input never creates real
emergency guidance. Unknown symptom codes or malformed fields raise `ValueError`.

Output directly supports `BPAssessment` (`level`, `title`, `message`, `action`,
`source`, `override_allowed=False`) and includes internal eligibility, reason,
attestation, repeat, and evidence metadata. An assessment describes its recorded
event. A saved old assessment must not be shown as a newly detected emergency.
Neither this function nor the learning function sends a message, mutates a record,
calls a database, or makes a network request.

`build_baseline(records, metric, *, now, timezone_name, declared_reference=None,
current=None, unstable_period=False)` supports `steps`, `resting_heart_rate`,
`sleep_minutes`, `systolic`, and `diastolic`. Generic samples include `value`, aware
`observed_at`, `source`, `source_stream`, and `data_mode`. BP samples additionally
carry the complete paired reading and context above; scalar BP is not reconstructed.
Daily cloud summaries use `source="google_health"`, `kind="daily"`, and the original
`local_date`; the legacy source name `fitbit` is accepted as a separate identity.
This preserves source civil dates when UTC midnight was merely a storage label.
BP uses physical instants converted to the person's selected IANA timezone.

The result exposes status, eligible days/readings, required days, window bounds,
minimum readings per day, median/MAD, exclusions, provenance, provisional reference,
and optional comparison. `PersonalMetric` maps the provisional reference object's
`value` to its scalar `declared_reference`; the separate reference DTO retains its
provisional status. Invalid sample rows are counted and excluded, never imputed.
Invalid configuration, unknown metrics, and naive clocks fail explicitly.

Before deployment, a clinician must review wording and escalation behavior,
including pregnant/postpartum and pediatric exclusions, symptom interpretation,
low-BP handling, medication context, and performance in the intended population.
Country-specific contact guidance, the actual cuff model and fit, independent
validation, and any clinician-agreed targets remain external facts to establish.
No “inside observed band” message should be rendered as “healthy” or “safe.”
