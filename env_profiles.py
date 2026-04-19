from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent


def load_env_profile(default_profile: str) -> str:
    """Load profile env first, then fallback base .env.

    Env selection priority:
    1) ENV_PROFILE variable (if set)
    2) function default_profile
    """
    profile = (os.getenv("ENV_PROFILE", "").strip() or default_profile).strip()
    profile_file = BASE_DIR / f".env.{profile}"
    base_file = BASE_DIR / ".env"

    if profile_file.exists():
        load_dotenv(profile_file, override=True)
        loaded = str(profile_file.name)
    else:
        loaded = "none"

    if base_file.exists():
        # Keep this as fallback only, do not override profile values.
        load_dotenv(base_file, override=False)

    return loaded
