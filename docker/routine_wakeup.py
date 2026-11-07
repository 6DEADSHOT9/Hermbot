"""Script-only native timer. A wake cannot supply instructions or grant access."""
import json
from pathlib import Path
from urllib import request


def main():
    data = json.loads((Path(__file__).parent.parent / 'hermbot-routine-wakeup.json').read_text())
    message = request.Request(data['url'], data=b'{}', method='POST', headers={'Authorization': 'Bearer ' + data['token'], 'Content-Type': 'application/json'})
    try:
        with request.urlopen(message, timeout=8) as response:
            response.read(1024)
    except Exception:
        # Never copy credential-bearing requests/provider responses into native logs.
        raise SystemExit('Hermbot routine wake failed; inspect scheduler health') from None


if __name__ == '__main__':
    main()
