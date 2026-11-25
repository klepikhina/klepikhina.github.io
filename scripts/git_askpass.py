#!/usr/bin/env python3
"""GIT_ASKPASS helper for the atlas sync.

Git asks for a username/password during HTTPS pushes. This returns a fixed
username and the GitHub token stored by sync_sources.py in macOS Keychain.
It is used only by the local sync process and never writes the token to disk.
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import sync_sources  # noqa: E402  (path is set up above)


def main() -> int:
    prompt = sys.argv[1] if len(sys.argv) > 1 else ""
    lowered = prompt.lower()
    if "username" in lowered:
        print("x-access-token")
        return 0
    try:
        token = sync_sources.get_secret("github_token")
    except Exception:
        token = ""
    if not token:
        print("", end="")
        return 1
    print(token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
