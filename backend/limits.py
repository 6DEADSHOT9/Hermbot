"""Resource policy shared by settings, lifecycle validation and the supervisor.

Machine facts describe Docker's machine, not necessarily the physical host. On
Docker Desktop its disk can exceed the host's free disk and its memory is only
what Docker was given. Detection may be wrong; floors and caps bound defaults.
"""

import math


MAX_BOTS = 30
MIN_BOTS = 3
MAX_ACTIVE = 10
MIN_MEMORY_MB = 1024
MAX_MEMORY_MB = 8192
MEMORY_OPTIONS_MB = (1024, 2048, 4096, 8192)
MIN_CPUS = 0.25
CORE_RESERVE_MB = 1536
# Estimates from one machine during a quiet period; replace with measurements.
IDLE_MEMORY_MB = 0.4 * 1024
PIDS_FLOOR = 512
PIDS_CAP = 4096
PIDS_MIN = 128
# Floors rest on three mostly idle bots on one Mac (261 threads, 2,087 MB peak).
# Re-measure under a heavy browser task before changing them.
NOFILE_SOFT = 1024
NOFILE_DEFAULT = 16384
NOFILE_FALLBACK_MAX = 65536
LOG_SIZE_MB = 10
LOG_FILES = 5
LOG_SIZE_HARD = (1, 100)
LOG_FILES_HARD = (1, 20)
SWAP_ALLOWANCE = 0.25
HELPER_PIDS_LIMIT = 64
BOT_OOM_SCORE_ADJ = 1000
CORE_OOM_SCORE_ADJ = -500
DATA_OOM_SCORE_ADJ = -997
# The order is not guaranteed if Postgres grows very large. Rootless setups may
# refuse negative scores; in that case only the bots' +1,000 can be relied on.
MIB = 1024 ** 2
GIB = 1024 ** 3


def _positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < value <= 1e300


def _fact(machine, name, minimum=0):
    value = (machine or {}).get(name)
    plausible_max = {"cpus": 1_000_000, "memory_bytes": 2 ** 60, "pid_max": 2 ** 32,
                     "threads_max": 2 ** 40, "nr_open": 2 ** 32,
                     "disk_total_bytes": 2 ** 70, "disk_free_bytes": 2 ** 70}
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and (
        minimum <= value <= plausible_max.get(name, 2 ** 70) and value > 0
    ) else None


def _counts(max_bots, max_active):
    if type(max_bots) is not int or not MIN_BOTS <= max_bots <= MAX_BOTS:
        raise ValueError(f"max_bots must be between {MIN_BOTS} and {MAX_BOTS}")
    if type(max_active) is not int or not 1 <= max_active <= MAX_ACTIVE:
        raise ValueError(f"max_active must be between 1 and {MAX_ACTIVE}")


def _process_ceiling(machine):
    values = [_fact(machine, name, PIDS_MIN) for name in ("pid_max", "threads_max")]
    return min(value for value in values if value is not None) if any(values) else None


def _enforcement(machine, support=None):
    # Support flags are not proof of enforcement. Inspect the created container
    # before claiming enforcement, especially on rootless or cgroup v1 setups.
    if support and (machine or {}).get(support) is False:
        return "not_enforced"
    return "unverified"


def describe(machine, max_bots=10, max_active=3, overrides=None):
    """Editable values and their bounds; does not discard now-invalid overrides."""
    _counts(max_bots, max_active)
    ceiling = _process_ceiling(machine)
    # Defaults divide by the product maximum, accepting smaller defaults for
    # small workspaces. Owner-set hard maximums follow the workspace maximum.
    share = 0.75 * ceiling / MAX_BOTS if ceiling else None
    # A share is intermediate, not a limit. Round then bound, keeping the floor.
    pids = max(PIDS_FLOOR, min(PIDS_CAP, 2 ** math.floor(math.log2(share)))) if share else PIDS_FLOOR
    pids_max = max(PIDS_FLOOR, math.floor(0.75 * ceiling / max_bots)) if ceiling else PIDS_FLOOR
    nr_open = _fact(machine, "nr_open")
    # RLIMIT_NOFILE is a plain per-process limit, not a per-bot share. Many
    # processes can still exhaust the system file table. System-wide monitoring
    # belongs in the measurement stage; a per-bot total needs a separate design.
    nofile_max = int(nr_open) if nr_open else NOFILE_FALLBACK_MAX
    rows = {
        "pids_limit": (pids, [PIDS_FLOOR, min(PIDS_CAP, pids_max)], [PIDS_MIN, pids_max],
                       f"75% × {ceiling} ÷ {MAX_BOTS} = share {share:g}; rounded down to a power of two, "
                       f"bounded to {PIDS_FLOOR}–{PIDS_CAP}." if ceiling else "Unknown process ceiling; use the 512 floor.",
                       "Stops one bot exhausting the machine's processes and threads.", "pids_limit", not ceiling),
        "nofile_hard": (min(NOFILE_DEFAULT, nofile_max), [min(4096, nofile_max), min(65536, nofile_max)],
                        [NOFILE_SOFT, nofile_max], "16,384 per process, capped by the kernel's fs.nr_open.",
                        "Bounds each process's open files; it does not bound the whole bot's open files.", None, not nr_open),
        "allow_swap": (False, [False, False], [False, True], "Swap is off: total memory plus swap equals memory.",
                       "Swapping makes memory unpredictable and can slow every bot.", "swap_limit", False),
        "log_size_mb": (LOG_SIZE_MB, [5, 50], list(LOG_SIZE_HARD), "10 MB per log file.",
                        "Rotates old logs before one container fills the disk.", None, False),
        "log_files": (LOG_FILES, [2, 10], list(LOG_FILES_HARD), "Keep five log files per container.",
                      "Keeps recent history while bounding log storage.", None, False),
    }
    result = {}
    for key, (computed, recommended, hard, formula, reason, support, uncertain) in rows.items():
        override = (overrides or {}).get(key)
        result[key] = {
            "value": computed if override is None else override,
            "source": "computed" if override is None else "owner",
            "computed_value": computed, "recommended": recommended, "hard": hard,
            "formula": formula, "reason": reason, "enforcement": _enforcement(machine, support),
            "applies": "recreate", "uncertain": uncertain,
            "available": hard[0] <= hard[1],
        }
    return result


def resolved_values(machine, max_bots=10, max_active=3, overrides=None):
    return {key: row["value"] for key, row in describe(machine, max_bots, max_active, overrides).items()}


def validate_overrides(values, machine, max_bots=10, max_active=3):
    """Validate sparse overrides; null resets an override to its computed value."""
    if not isinstance(values, dict):
        raise ValueError("Limit overrides must be an object")
    metadata = describe(machine, max_bots, max_active, values)
    for key, value in values.items():
        if key not in metadata:
            raise ValueError(f"Unknown resource limit: {key}")
        if value is None:
            continue
        if key == "allow_swap":
            if type(value) is not bool:
                raise ValueError("allow_swap must be true or false")
        elif type(value) is not int:
            raise ValueError(f"{key} must be an integer")
        low, high = metadata[key]["hard"]
        if not low <= value <= high:
            raise ValueError(f"{key} must be between {low} and {high}")
    return metadata


def override_warnings(values, machine, max_bots=10, max_active=3):
    metadata = validate_overrides(values, machine, max_bots, max_active)
    warnings = []
    reasons = {
        "pids_limit": "Too few processes can prevent a bot with browser tabs from working; high limits weaken isolation.",
        "nofile_hard": "Low limits can break tools; very large limits slow tools that scan every file number.",
        "allow_swap": "Swap can slow all bots and make memory use unpredictable.",
        "log_size_mb": "Smaller logs lose history sooner; larger logs use more disk.",
        "log_files": "Fewer files lose history sooner; more files use more disk.",
    }
    for key, value in values.items():
        low, high = metadata[key]["recommended"]
        if value is not None and not low <= value <= high:
            warnings.append(f"{key}={value}: {reasons[key]}")
    return warnings


def resource_bounds(machine, max_active=3):
    _counts(10, max_active)
    memory = _fact(machine, "memory_bytes", MIB)
    cpus = _fact(machine, "cpus", MIN_CPUS)
    memory_max = min(MAX_MEMORY_MB, math.floor(memory / MIB - CORE_RESERVE_MB)) if memory else MAX_MEMORY_MB
    target = min(4096, (memory / MIB - CORE_RESERVE_MB) / max_active) if memory else MIN_MEMORY_MB
    options = [value for value in MEMORY_OPTIONS_MB if value <= memory_max]
    default = max((value for value in options if value <= target), default=MIN_MEMORY_MB)
    # Detection is tested on recorded examples, weaker than running on those
    # machines. Missing or odd facts use floor defaults and remain uncertain.
    return {
        "memory_min_mb": MIN_MEMORY_MB, "memory_max_mb": memory_max,
        "memory_default_mb": default, "memory_options_mb": options,
        "cpus_min": MIN_CPUS, "cpus_max": cpus if cpus else 4,
        "cpus_default": min(1.5, cpus) if cpus else MIN_CPUS,
        "memory_uncertain": memory is None, "cpus_uncertain": cpus is None,
    }


def validate_resources(memory_mb, cpus, machine, existing=None):
    """Allow an unchanged legacy value, while checking any changed resource."""
    bounds = resource_bounds(machine)
    if type(memory_mb) is not int or memory_mb <= 0:
        raise ValueError("memory_mb must be a positive integer")
    if not _positive(cpus):
        raise ValueError("cpus must be a finite positive number")
    for key, value, low, high in (
        ("memory_mb", memory_mb, bounds["memory_min_mb"], bounds["memory_max_mb"]),
        ("cpus", cpus, bounds["cpus_min"], bounds["cpus_max"]),
    ):
        if existing is not None and value == existing.get(key):
            continue
        if not low <= value <= high:
            raise ValueError(f"{key} must be between {low} and {high} on this machine")


def _estimate(memory_limits, count, max_active, default):
    limits = sorted(memory_limits[:count] + [default] * max(0, count - len(memory_limits)), reverse=True)
    working = min(count, max_active)
    # Only non-working bots add idle memory. This pessimistic estimate warns,
    # never blocks: all working bots reaching their limits at once is unlikely.
    # Reserving every bot's full limit would allow fewer bots, but still cannot
    # prevent kills: bots can exceed their caps and Docker, the OS, other apps
    # and uncapped Postgres remain outside that stronger guarantee.
    return CORE_RESERVE_MB + sum(limits[:working]) + (count - working) * IDLE_MEMORY_MB


def capacity(machine, bots, max_bots=10, max_active=3):
    """Worst-case capacity and labelled MiB estimates, using actual bot limits."""
    _counts(max_bots, max_active)
    memory_limits = []
    for bot in bots:
        value = bot.get("memory_mb")
        if not _positive(value):
            raise ValueError("Every bot must have a positive memory_mb")
        memory_limits.append(value)
    memory_limits.sort(reverse=True)
    # Existing limits never change with concurrency. Only hypothetical new bots
    # get the newly computed default, which can grow when fewer runs are allowed.
    default = resource_bounds(machine, max_active)["memory_default_mb"]
    memory = _fact(machine, "memory_bytes", MIB)
    ceiling = _process_ceiling(machine)
    process_capacity = math.floor(0.75 * ceiling / PIDS_FLOOR) if ceiling else None
    hard_capacity = math.floor(0.75 * ceiling / PIDS_MIN) if ceiling else None
    memory_capacity = None
    if memory:
        memory_mb = memory / MIB
        last = len(memory_limits) + max_active
        memory_capacity = 0
        for count in range(1, last + 1):
            if _estimate(memory_limits, count, max_active, default) > memory_mb:
                break
            memory_capacity = count
        else:
            spare = memory_mb - _estimate(memory_limits, last, max_active, default)
            memory_capacity += math.floor(spare / IDLE_MEMORY_MB)
    known = {"processes": process_capacity, "memory": memory_capacity}
    available = {key: value for key, value in known.items() if value is not None}
    result = min(available.values()) if available else None
    # Above process capacity, per-bot limits still apply but all bots together
    # can exhaust processes. Memory capacity uses quiet-machine estimates.
    return {
        "capacity": result, "limiting_resources": [key for key, value in available.items() if value == result],
        "process_capacity": process_capacity, "process_hard_capacity": hard_capacity,
        "memory_capacity": memory_capacity, "uncertain": len(available) != len(known),
        "memory_total_mb": memory / MIB if memory else None,
        "memory_today_mb": _estimate(memory_limits, len(memory_limits), max_active, default),
        "memory_at_max_mb": _estimate(memory_limits, max(max_bots, len(memory_limits)), max_active, default),
        "bots_today": len(memory_limits), "max_bots": max_bots, "max_active": max_active,
        "memory_default_mb": default, "memory_estimated": True,
        "process_impossible": max_bots > hard_capacity if hard_capacity is not None else False,
        "above_capacity": max_bots > result if result is not None else None,
    }


def disk_preview(machine, max_bots=10):
    """Proposed disk numbers for display only; no disk policy is active yet."""
    _counts(max_bots, 3)
    total = _fact(machine, "disk_total_bytes", GIB)
    free = _fact(machine, "disk_free_bytes")
    valid = total is not None and free is not None and free <= total
    floor = min(100 * GIB, max(5 * GIB, total * 0.1)) if total else 5 * GIB
    usable = max(0, free * 0.8 - floor) if valid else None
    return {
        "active": False, "enforcement": "not_active_until_a2", "uncertain": not valid,
        "free_floor_bytes": floor,
        "default_gb": max(5, min(50, math.floor(usable / MAX_BOTS / GIB))) if valid else 5,
        "hard_max_gb": max(5, math.floor(usable / max_bots / GIB)) if valid else 5,
        "capacity": math.floor(usable / (5 * GIB)) if valid else None,
    }
