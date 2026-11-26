"""Fixtures for every test."""

import os
from collections.abc import Iterator

import pytest

# The AI model settings of whoever runs the tests: a real key or another model
# must never change a test, nor be used by one.
_LLM_VARIABLES = [
    name
    for name in os.environ
    if name.upper().startswith(("LLM_", "OLLAMA_", "AGENT_")) or name.upper() == "GOOGLE_API_KEY"
]


@pytest.fixture(autouse=True)
def _no_real_ai_model(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in _LLM_VARIABLES:
        monkeypatch.delenv(name)
    # Environment variables win over the .env file, which may hold a real key:
    # with these, any Settings() built during a test cannot create a real model.
    monkeypatch.setenv("LLM_PROVIDER", "google_genai")
    monkeypatch.setenv("GOOGLE_API_KEY", "")
    yield
