"""Logging settings: log level and output format (json or console)."""

from typing import Literal

from env._base import BaseEnv


class Env(BaseEnv):
    """Log level and log format settings."""

    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "console"


env = Env()
