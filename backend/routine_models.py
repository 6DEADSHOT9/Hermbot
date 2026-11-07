"""Owner-editable schedules; execution uses the existing work contract."""
from datetime import datetime
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, model_validator

from .models import BotId, OutcomeSpec, StrictModel


class Schedule(StrictModel):
    kind: Literal['once', 'interval', 'daily', 'weekdays', 'weekly', 'monthly']
    timezone: str = Field(default='UTC', max_length=100)
    at: datetime | None = None
    interval_minutes: int = Field(default=60, ge=1, le=525600)
    hour: int = Field(default=9, ge=0, le=23)
    minute: int = Field(default=0, ge=0, le=59)
    weekdays: list[int] = Field(default_factory=lambda: [0], min_length=1, max_length=7)
    month_day: int = Field(default=1, ge=1, le=31)
    last_day: bool = False

    @model_validator(mode='after')
    def valid(self):
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError('Choose an IANA timezone such as Asia/Kolkata') from None
        if any(day < 0 or day > 6 for day in self.weekdays) or len(set(self.weekdays)) != len(self.weekdays):
            raise ValueError('Weekdays must be unique numbers from Monday (0) through Sunday (6)')
        if self.kind in ('once', 'interval') and (not self.at or self.at.tzinfo is None):
            raise ValueError('One-time and interval schedules require an absolute start time with timezone')
        return self


class RoutineEffect(StrictModel):
    outcome_key: str = Field(max_length=80)
    action: str = Field(max_length=120)
    resource: str = Field(default='', max_length=4000)
    resource_label: str = Field(default='', max_length=300)
    instruction_quote: str = Field(min_length=1, max_length=5000)


class RecurringManagement(StrictModel):
    grant_id: UUID
    targets: list[BotId] = Field(min_length=1, max_length=20)
    operations: list[Literal['delegate', 'inspect', 'steer', 'cancel', 'reassign']] = Field(
        default_factory=lambda: ['delegate', 'inspect', 'steer', 'cancel', 'reassign'], min_length=1, max_length=5)

    @model_validator(mode='after')
    def valid(self):
        self.targets = sorted(set(self.targets))
        self.operations = sorted(set(self.operations))
        if 'delegate' not in self.operations:
            raise ValueError('Recurring coordination requires delegation permission')
        return self


class SavedWorkDefinition(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    bot_id: BotId
    conversation_id: UUID
    instructions: str = Field(min_length=1, max_length=12000)
    requirements: list[OutcomeSpec] = Field(min_length=1, max_length=30)
    requested_effects: list[RoutineEffect] = Field(default_factory=list, max_length=20)
    artifact_ids: list[UUID] = Field(default_factory=list, max_length=10)
    attachment_ids: list[UUID] = Field(default_factory=list, max_length=10)
    skill_refs: list[str] = Field(default_factory=list, max_length=10)
    active_budget_seconds: int = Field(default=3600, ge=60, le=86400)
    max_runs_per_day: int = Field(default=24, ge=1, le=1440)
    enabled: bool = True
    expected_revision: int | None = Field(default=None, ge=1)
    management: RecurringManagement | None = None

    @model_validator(mode='after')
    def complete(self):
        if self.management and self.bot_id in self.management.targets:
            raise ValueError('Choose other teammates as collaborators')
        if not self.name.strip() or not self.instructions.strip():
            raise ValueError('Supply a name and instructions')
        by_key = {s.key: s for s in self.requirements}
        if len(by_key) != len(self.requirements):
            raise ValueError('Required result keys must be unique')
        visiting, visited = set(), set()
        def visit(key):
            if key not in by_key or key in visiting:
                raise ValueError('Result dependencies must exist and have no cycles')
            if key in visited:
                return
            visiting.add(key)
            for dependency in by_key[key].depends_on:
                visit(dependency)
            visiting.remove(key)
            visited.add(key)
        for key in by_key:
            visit(key)
        for effect in self.requested_effects:
            spec = by_key.get(effect.outcome_key)
            if not spec or spec.kind != 'connector_effect' or effect.instruction_quote not in self.instructions:
                raise ValueError('External authorization must quote the saved instructions and name an external result')
            if not effect.resource and not effect.resource_label:
                raise ValueError('External authorization requires a destination')
            if spec.target.action and spec.target.action != effect.action:
                raise ValueError('External authorization differs from the requested result')
            if spec.target.resource and spec.target.resource != effect.resource:
                raise ValueError('External authorization differs from the requested destination')
            if not spec.target.resource and spec.target.display_name and spec.target.display_name != effect.resource_label:
                raise ValueError('External authorization differs from the requested destination')
        return self


class RoutineDefinition(SavedWorkDefinition):
    schedule: Schedule
    start_window_minutes: int = Field(default=15, ge=2, le=10080)
    catch_up: Literal['skip', 'latest'] = 'skip'
    window_mode: Literal['independent', 'since_success'] = 'independent'
    initial_lookback_minutes: int = Field(default=1440, ge=1, le=525600)


class RoutineControl(StrictModel):
    expected_revision: int = Field(ge=1)


class RoutineProposal(StrictModel):
    instruction_quote: str = Field(min_length=1, max_length=5000)
    routine_id: UUID | None = None
    definition: RoutineDefinition
