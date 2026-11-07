"""One schedule calculation for preview and admission, independent of server timezone."""
import calendar
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .routine_models import Schedule

UTC = timezone.utc


def next_at(schedule, after, *, inclusive=False):
    schedule = Schedule.model_validate(schedule) if isinstance(schedule, dict) else schedule
    after = after.astimezone(UTC)
    boundary = after - timedelta(microseconds=1) if inclusive else after
    if schedule.kind == 'once':
        return schedule.at.astimezone(UTC) if schedule.at.astimezone(UTC) > boundary else None
    if schedule.kind == 'interval':
        anchor = schedule.at.astimezone(UTC)
        step = timedelta(minutes=schedule.interval_minutes)
        count = max(0, (boundary - anchor) // step + 1)
        return anchor + count * step
    zone = ZoneInfo(schedule.timezone)
    date = boundary.astimezone(zone).date()
    for offset in range(366 * 5):
        day = date + timedelta(days=offset)
        if schedule.kind == 'weekdays' and day.weekday() > 4:
            continue
        if schedule.kind == 'weekly' and day.weekday() not in schedule.weekdays:
            continue
        if schedule.kind == 'monthly':
            wanted = calendar.monthrange(day.year, day.month)[1] if schedule.last_day else schedule.month_day
            if day.day != wanted:
                continue
        wall = datetime(day.year, day.month, day.day, schedule.hour, schedule.minute)
        local = wall.replace(tzinfo=zone, fold=0)
        instant = local.astimezone(UTC)
        # Round-trip rejects nonexistent local times; fold=0 chooses the first repeated hour.
        if instant.astimezone(zone).replace(tzinfo=None) == wall and instant > boundary:
            return instant
    raise ValueError('No occurrence within five years')


def preview(schedule, at, count=5):
    result = []
    instant = next_at(schedule, at, inclusive=True)
    zone = ZoneInfo(schedule.timezone if isinstance(schedule, Schedule) else schedule['timezone'])
    while instant and len(result) < count:
        result.append({'utc': instant.isoformat(), 'local': instant.astimezone(zone).isoformat()})
        instant = next_at(schedule, instant)
    return result
