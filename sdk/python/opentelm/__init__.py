from .client import OpenTelLM

_client: OpenTelLM | None = None


def init(api_key: str, endpoint: str = "http://localhost:8000", **kwargs) -> OpenTelLM:
    """Initialize the SDK and auto-instrument supported LLM libraries.

    Call this once at application startup, before importing openai/anthropic.
    The SDK monkey-patches those libraries so all subsequent calls are traced
    with zero code changes in the developer's app.
    """
    global _client
    _client = OpenTelLM(api_key=api_key, endpoint=endpoint, **kwargs)
    _client.instrument()
    return _client


def tag_version(version: str | None) -> None:
    """Tag the current deployment version for prompt regression tracking."""
    if _client:
        _client.deployment_version = version


def set_version(version: str | None) -> None:
    """Alias of tag_version() — matches the Node.js SDK's setVersion() name.

    Both names are supported; new code should prefer set_version() for
    cross-language consistency.
    """
    tag_version(version)


def get_version() -> str | None:
    return _client.deployment_version if _client else None


def get_client() -> OpenTelLM | None:
    return _client
