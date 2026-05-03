from __future__ import annotations

import os
from pathlib import Path

try:
    from hermes_constants import display_hermes_home, get_hermes_home
except ImportError:
    def get_hermes_home() -> Path:
        raw = os.environ.get("HERMES_HOME", "").strip()
        if raw:
            return Path(raw).expanduser()
        return Path.home() / ".hermes"


    def display_hermes_home() -> str:
        raw = os.environ.get("HERMES_HOME", "").strip()
        if raw:
            hermes_home_text = raw
            hermes_home = Path(raw).expanduser()
        else:
            hermes_home = Path.home() / ".hermes"
            hermes_home_text = str(hermes_home)
        home = Path.home().expanduser().resolve()
        try:
            rel = hermes_home.resolve().relative_to(home)
        except ValueError:
            return hermes_home_text
        if not rel.parts:
            return "~"
        return f"~/{rel.as_posix()}"
