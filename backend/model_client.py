"""Shared subscription completion transport; task schemas remain with their callers."""
import httpx

from .settings import settings
from .model_protocol import REAUTH_ERRORS

MESSAGES = {
    **dict.fromkeys(REAUTH_ERRORS, 'Reconnect the ChatGPT account in Workspace settings → Providers, then retry this saved work.'),
    'model_test_required': 'This model needs a fresh test after connecting or reconnecting. Open Workspace settings → Providers, select this teammate’s model, click Test model, then retry interpretation. Your saved work is preserved.',
    'codex_usage_limit': 'The Codex account reached a usage limit. Check Providers and retry when allowance is available. No API billing was used.',
    'codex_rate_limited': 'Codex temporarily limited requests. Retry later; no other provider or API billing was used.',
    'broker_busy': 'Model capacity is busy. Retry this saved work shortly.',
    'broker_offline': 'The local ChatGPT account broker is offline. Start it, then retry this saved work.',
    'connection_ended': 'The ChatGPT connection ended. Check Providers, then retry this saved work.',
    'subscription_sharing_usage_limit_exceeded': 'ChatGPT plan usage reached a limit. Open Providers → Manage usage, then recheck availability before retrying.',
    'subscription_sharing_usage_unavailable': 'ChatGPT usage availability is temporarily unavailable. Your saved work can be retried later.',
    'sign_in_required': 'Reconnect the ChatGPT account in Workspace settings → Providers, then retry this saved work.',
    'refresh_uncertain': 'ChatGPT token renewal was interrupted. Reconnect the account in Providers before retrying.',
    'verification_unavailable': 'ChatGPT identity verification is temporarily unavailable. The connection was preserved; retry later.',
    'plan_permission_required': 'Enable ChatGPT plan usage for this connection in Providers before retrying.',
    'subscription_sharing_unsupported_capability': 'This model request uses a capability unavailable with ChatGPT plan usage. Inspect the provider compatibility settings.',
    'request_failed': 'The ChatGPT model request could not complete. Check the connection and tested model in Providers; your saved work is preserved.',
}


class ModelConnectionError(ValueError):
    def __init__(self, code):
        self.code = code if isinstance(code, str) and code in MESSAGES else 'request_failed'
        super().__init__(MESSAGES[self.code])


def result(response):
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError:
        try:
            detail = response.json().get('detail')
        except (ValueError, AttributeError):
            detail = None
        raise ModelConnectionError(detail if isinstance(detail, str) and detail in MESSAGES else 'request_failed') from None
    return response.json()


def completion(provider, payload, *, caller='model'):
    with httpx.Client(timeout=180, follow_redirects=False, trust_env=False) as client:
        response = client.post('http://api:8000/internal/model-completion', headers={'Authorization': 'Bearer ' + settings.internal_token},
                               json={'provider_id': str(provider['id']), 'provider_revision': provider['revision'], 'provider_auth_epoch': provider['auth_epoch'], 'payload': payload, 'caller': caller})
        return result(response)


async def async_completion(provider, payload, *, caller='model'):
    async with httpx.AsyncClient(timeout=180, follow_redirects=False, trust_env=False) as client:
        response = await client.post('http://api:8000/internal/model-completion', headers={'Authorization': 'Bearer ' + settings.internal_token},
                                    json={'provider_id': str(provider['id']), 'provider_revision': provider['revision'], 'provider_auth_epoch': provider['auth_epoch'], 'payload': payload, 'caller': caller})
        return result(response)
