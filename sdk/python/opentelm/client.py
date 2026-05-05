from __future__ import annotations

SDK_VERSION = "0.1.0"


class OpenTelLM:
    def __init__(
        self,
        api_key: str,
        endpoint: str = "http://localhost:8000",
        default_tags: dict[str, str] | None = None,
    ):
        self.api_key = api_key
        self.endpoint = endpoint.rstrip("/")
        self.sdk_version = SDK_VERSION
        self.deployment_version: str | None = None
        self.default_tags: dict[str, str] = default_tags or {}
        self.flusher = None
        self._instrumented = False

    def instrument(self) -> None:
        if self._instrumented:
            return
        from .flush import AsyncFlusher
        from .monkey_patch import instrument_all
        self.flusher = AsyncFlusher(self)
        instrument_all(self)
        self._instrumented = True
