"""Owner-saved event sources and instructions; external payloads never supply authority."""
from datetime import datetime
import json
import re
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from .models import BotId, StrictModel
from .routine_models import SavedWorkDefinition


EVENTS = {
    'slack': ['slack.message.created', 'slack.app_mention'],
    'github': ['github.issue.opened', 'github.issue.updated', 'github.comment.created', 'github.pull.opened', 'github.pull.updated'],
    'google_drive': ['drive.file.changed', 'drive.file.removed'],
    'google_calendar': ['calendar.event.created', 'calendar.event.updated', 'calendar.event.cancelled'],
    'gmail': ['gmail.message.added'],
    'webhook': ['custom.event'],
}
PROVIDERS = {'slack': 'slack', 'github': 'github', 'google_drive': 'google', 'google_calendar': 'google', 'gmail': 'google'}
READ_ACTIONS = {'slack': 'slack.history', 'github': 'github.issue', 'google_drive': 'drive.read', 'google_calendar': 'calendar.events', 'gmail': 'gmail.read'}


class SourceDefinition(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    provider: Literal['slack', 'github', 'google_drive', 'google_calendar', 'gmail', 'webhook']
    connection_id: UUID | None = None
    transport: Literal['poll', 'socket', 'http']
    resource: str = Field(default='', max_length=300)
    enabled: bool = True
    poll_interval_seconds: int = Field(default=60, ge=60, le=3600)
    expected_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode='after')
    def supported(self):
        if not self.name.strip():
            raise ValueError('Name this source')
        if (self.provider == 'webhook') != (self.connection_id is None):
            raise ValueError('Choose a connection for provider events; general webhooks have their own signing key')
        transports = {'slack': ('socket', 'http'), 'github': ('poll', 'http'), 'webhook': ('http',)}
        if self.transport not in transports.get(self.provider, ('poll',)):
            raise ValueError('This transport is not supported for this provider')
        if self.provider == 'github' and (not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', self.resource) or any(p in ('.', '..') for p in self.resource.split('/'))):
            raise ValueError('Choose an owner/repository source')
        if self.provider == 'google_calendar' and not self.resource.strip():
            raise ValueError('Choose a calendar ID, or primary')
        if self.provider in ('slack', 'gmail', 'google_drive', 'webhook') and self.resource:
            raise ValueError('Select channels, files or folders in the trigger filters')
        return self


class SourceSecrets(StrictModel):
    expected_revision: int = Field(ge=1)
    signing_secret: str | None = Field(default=None, min_length=24, max_length=1000)
    app_token: str | None = Field(default=None, min_length=20, max_length=1000)
    previous_secret_minutes: int = Field(default=0, ge=0, le=1440)


class EventFilter(StrictModel):
    field: Literal['resource', 'actor', 'text', 'labels', 'mime_type', 'parent_ids', 'event_type', 'custom_type']
    operator: Literal['equals', 'contains', 'starts_with'] = 'equals'
    value: str = Field(min_length=1, max_length=1000)


class TriggerDefinition(SavedWorkDefinition):
    source_id: UUID
    event_types: list[str] = Field(min_length=1, max_length=10)
    filters: list[EventFilter] = Field(default_factory=list, max_length=12)
    start_window_minutes: int = Field(default=1440, ge=2, le=10080)
    coalesce: bool = False
    quiet_seconds: int = Field(default=30, ge=5, le=300)
    max_wait_seconds: int = Field(default=300, ge=5, le=1800)
    max_pending: int = Field(default=100, ge=1, le=1000)
    ignore_bots: bool = True

    @model_validator(mode='after')
    def valid_trigger(self):
        if self.max_wait_seconds < self.quiet_seconds:
            raise ValueError('Maximum wait must be at least the quiet period')
        if self.coalesce and self.start_window_minutes * 60 < self.max_wait_seconds:
            raise ValueError('The permitted start delay must cover the maximum wait for combined updates')
        if len(set(self.event_types)) != len(self.event_types):
            raise ValueError('Event types must be unique')
        return self


class EventData(StrictModel):
    key: str = Field(min_length=1, max_length=200)
    event_type: str = Field(min_length=1, max_length=100)
    resource: str = Field(default='', max_length=4000)
    resource_version: str = Field(default='', max_length=300)
    actor: str = Field(default='', max_length=300)
    occurred_at: datetime
    text: str = Field(default='', max_length=24000)
    summary: str = Field(min_length=1, max_length=500)
    labels: list[str] = Field(default_factory=list, max_length=100)
    parent_ids: list[str] = Field(default_factory=list, max_length=10)
    mime_type: str = Field(default='', max_length=200)
    custom_type: str = Field(default='', max_length=100)
    is_bot: bool = False
    metadata: dict = Field(default_factory=dict)

    @model_validator(mode='after')
    def bounded(self):
        if self.occurred_at.tzinfo is None:
            raise ValueError('Event time requires an absolute timezone')
        if len(json.dumps(self.metadata, ensure_ascii=False)) > 8000:
            raise ValueError('Event metadata exceeds its size limit')
        if any(len(v) > 500 for v in self.labels + self.parent_ids):
            raise ValueError('Event labels or parents exceed their size limit')
        return self


class TriggerPreview(StrictModel):
    source_id: UUID
    bot_id: BotId
    event_types: list[str] = Field(min_length=1, max_length=10)
    filters: list[EventFilter] = Field(default_factory=list, max_length=12)
    ignore_bots: bool = True
    coalesce: bool = False
    event: EventData
