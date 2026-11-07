"""Compatibility entry point for current native-tool acceptance.

The original phase4-native.json is historical proof of the superseded blacklist.
Current acceptance verifies tools remain available alongside existing connections.
Use an explicit start, continue, verify or cleanup mode to avoid implicit submissions.
"""
from .native_tools_acceptance import main


if __name__ == '__main__':
    main()
