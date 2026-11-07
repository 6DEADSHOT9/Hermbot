"""One application-owned policy description shared with the supported plugin."""
import json
from pathlib import Path

NATIVE_INSTRUCTION = json.loads((Path(__file__).resolve().parent.parent / 'plugins/hermbot/policy.json').read_text())['native_instruction']
