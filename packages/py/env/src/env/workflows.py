"""Workflows SDK settings: API base URL and deployment name."""

from env._base import BaseEnv


class Env(BaseEnv):
    """The Workflows API base URL and the deployment name."""

    workflows_base_url: str = "https://api.mistral.ai"
    deployment_name: str | None = None


env = Env()
