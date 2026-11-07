"""Absolute entry point for an isolated Python interpreter under launchd."""
from pathlib import Path
import sys

# -I disables environment/user-site imports; only this reviewed installation is added.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from model_broker.__main__ import main  # noqa: E402

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception:
        print('Hermbot broker could not start securely. Check the private directory and unlock Keychain. Existing account records were preserved.', file=sys.stderr)
        raise SystemExit(2) from None
