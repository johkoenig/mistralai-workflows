"""Application identity shared by app-local libraries and generated modules."""

from env._base import BaseEnv


class Env(BaseEnv):
    """The application's identity name."""

    app_name: str = "github__johkoenig__mistralai-workflows"


env = Env()
