"""Job manifest schema + loader for the AgentM runner (agentm-runner.md).

Loads `.harness/jobs/*.yaml` into `JobManifest` objects. Read-only — this
module never runs a job, it only describes one. A malformed manifest fails
loud (raises `ManifestError` naming the offending file) rather than silently
dropping a job from the cycle, matching the seam's own never-demote
discipline.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:  # pragma: no cover - PyYAML is a repo-wide dependency already
    yaml = None

_DURATION_RE = re.compile(r"^(\d+)([smhdw])$")
_DURATION_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
_NAMED_SCHEDULE_SECONDS = {"hourly": 3600, "daily": 86400, "weekly": 604800}
# `02:00-06:00`, local time. The dash may be a hyphen or the en dash the design
# writes, because a manifest copied out of the design should not fail to load
# over a typographic choice.
_WINDOW_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)\s*[-–]\s*([01]\d|2[0-3]):([0-5]\d)$")

# T1 (the operator's personal space) is never a job target — that takes the
# separate, explicit, operator-authorized seam call a scheduled job never
# makes. Enforced at load time so a mistyped manifest fails at parse, not at
# the moment it would otherwise have written somewhere it shouldn't.
VALID_TIERS = ("T2", "T3")


class ManifestError(ValueError):
    """A job manifest failed to parse or violates the schema."""


def parse_duration(value: str) -> int:
    """"24h" / "7d" / "30m" -> seconds. Raises `ManifestError` if malformed."""
    m = _DURATION_RE.match(str(value).strip())
    if not m:
        raise ManifestError(f"malformed duration: {value!r} (expected e.g. '24h', '7d')")
    n, unit = m.groups()
    return int(n) * _DURATION_SECONDS[unit]


def schedule_interval_seconds(schedule: str) -> int:
    """A named cadence ("hourly"/"daily"/"weekly") or a raw duration -> seconds."""
    key = str(schedule).strip().lower()
    if key in _NAMED_SCHEDULE_SECONDS:
        return _NAMED_SCHEDULE_SECONDS[key]
    return parse_duration(schedule)


def parse_window(value: str) -> tuple[int, int]:
    """"02:00-06:00" -> (120, 360), minutes after local midnight.

    A window whose end is before its start wraps midnight (`22:00-02:00`). One
    whose start and end are equal is refused: it would mean either "never" or
    "always", and a manifest should not have to be read twice to know which.
    """
    m = _WINDOW_RE.match(str(value).strip())
    if not m:
        raise ManifestError(f"malformed window: {value!r} (expected e.g. '02:00-06:00')")
    h1, m1, h2, m2 = (int(g) for g in m.groups())
    start, end = h1 * 60 + m1, h2 * 60 + m2
    if start == end:
        raise ManifestError(f"window {value!r} opens and closes at the same minute")
    return start, end


@dataclass(frozen=True)
class JobManifest:
    name: str
    schedule: str
    lookback: str
    command: str
    tier: str = "T3"
    gate: Optional[str] = None
    budget_tokens: Optional[int] = None
    dry_run: bool = True
    # A job can be registered and off. Defaults to on, so every manifest
    # written before this field existed keeps running; a job that ships with
    # `enabled: false` is one whose command is real but whose schedule has not
    # been decided yet, and the cycle skips it by name in the report rather
    # than silently.
    #
    # Distinct from `dry_run`, which still runs the command and asks it not to
    # write. A disabled job does not run at all.
    enabled: bool = True
    # The hours a job may start in, local time, as written (`"02:00-06:00"`).
    # A job due outside its window waits for it rather than running at whatever
    # hour its interval happened to land on — without this, every `daily` job on
    # the machine drifted to the hour the runner first saw it (13:07 on
    # 2026-09-06), which is the middle of the working day. None means any hour.
    window: Optional[str] = None
    # Position within one cycle: lower runs first, ties by name. The night's
    # steps read each other's output — the binary refiles by the `type` the
    # batch just wrote — so the order they run in is part of what they mean,
    # and sorting by filename would have put the scorecard before the thing it
    # scores.
    order: int = 0
    path: Optional[Path] = None

    @property
    def interval_seconds(self) -> int:
        return schedule_interval_seconds(self.schedule)

    @property
    def window_minutes(self) -> Optional[tuple[int, int]]:
        return parse_window(self.window) if self.window else None

    @property
    def lookback_seconds(self) -> int:
        return parse_duration(self.lookback)


def _validate(name: str, data: dict, path: Path) -> JobManifest:
    for required in ("schedule", "lookback", "command"):
        if required not in data:
            raise ManifestError(f"{path}: missing required field {required!r}")

    tier = data.get("tier", "T3")
    if tier not in VALID_TIERS:
        raise ManifestError(
            f"{path}: tier {tier!r} is not a job-writable tier {VALID_TIERS} "
            "— T1 is never a job target"
        )

    # Fail loud at load time on a malformed schedule/lookback, not on first use.
    schedule_interval_seconds(str(data["schedule"]))
    parse_duration(str(data["lookback"]))

    window = data.get("window")
    if window is not None:
        parse_window(str(window))
        window = str(window).strip()

    order = data.get("order", 0)
    if isinstance(order, bool) or not isinstance(order, int):
        raise ManifestError(f"{path}: order {order!r} is not an integer")

    budget = data.get("budget")
    budget_tokens = budget.get("tokens") if isinstance(budget, dict) else None

    return JobManifest(
        name=name,
        schedule=str(data["schedule"]),
        lookback=str(data["lookback"]),
        command=str(data["command"]),
        tier=str(tier),
        gate=data.get("gate"),
        budget_tokens=budget_tokens,
        dry_run=bool(data.get("dry_run", True)),
        enabled=bool(data.get("enabled", True)),
        window=window,
        order=order,
        path=path,
    )


@dataclass(frozen=True)
class Refusal:
    """One manifest the loader would not accept, and why."""

    path: Path
    reason: str


def _manifest_paths(jobs_dir: Path) -> list[Path]:
    jobs_dir = Path(jobs_dir)
    if not jobs_dir.is_dir():
        return []
    return [p for p in sorted(jobs_dir.iterdir()) if p.suffix in (".yaml", ".yml")]


def _load_one(p: Path) -> JobManifest:
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ManifestError(f"{p}: invalid YAML ({e})") from e
    if not isinstance(data, dict):
        raise ManifestError(f"{p}: manifest body must be a mapping")
    return _validate(p.stem, data, p)


def load_manifests(jobs_dir: Path) -> list[JobManifest]:
    """Read every `*.yaml`/`*.yml` in `jobs_dir` into a `JobManifest`.

    Returns `[]` if `jobs_dir` doesn't exist (a fresh install with no jobs
    configured yet — not an error). Raises `ManifestError` on the first
    malformed manifest found, naming the offending file — the strict, all-or-
    nothing contract. The runner's cycle uses `load_manifests_lenient` since
    filing-v2 remainders task 1; this one stays for `--strict` and for callers
    that want a single answer.
    """
    if yaml is None:
        raise ManifestError("PyYAML not available — cannot parse job manifests")
    return [_load_one(p) for p in _manifest_paths(jobs_dir)]


def load_manifests_lenient(jobs_dir: Path) -> tuple[list[JobManifest], list[Refusal]]:
    """Every manifest that loads, and every one that does not, with its reason.

    One bad file used to stop every scheduled job on the machine, with a
    traceback in a launchd log as the only trace (2026-09-05: a `tier: T1`
    and a half-quoted command, each alone enough). Now a refused manifest is
    data — in the cycle report, the session brief and the doctor — and the
    jobs that load still run.
    """
    if yaml is None:
        raise ManifestError("PyYAML not available — cannot parse job manifests")
    loaded: list[JobManifest] = []
    refused: list[Refusal] = []
    for p in _manifest_paths(jobs_dir):
        try:
            loaded.append(_load_one(p))
        except ManifestError as e:
            refused.append(Refusal(path=p, reason=str(e)))
    return loaded, refused
