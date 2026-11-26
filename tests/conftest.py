"""Fixtures for every test."""

import os
from collections.abc import Iterator

import pytest

# The AI model settings of whoever runs the tests: a real key or another model
# in the shell must never change a test, nor be sent anywhere by one.
_LLM_VARIABLES = [
    name
    for name in os.environ
    if name.upper().startswith(("LLM_", "OLLAMA_")) or name.upper() == "GOOGLE_API_KEY"
]


@pytest.fixture(autouse=True)
def _no_llm_settings_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in _LLM_VARIABLES:
        monkeypatch.delenv(name)
    yield
