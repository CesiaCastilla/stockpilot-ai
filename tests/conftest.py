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
def _no_real_ai_model(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    # The evals are the only tests meant to use the real model. They never run
    # by accident: pytest's addopts exclude them unless `-m eval` is given.
    if request.node.get_closest_marker("eval") is not None:
        yield
        return
    for name in _LLM_VARIABLES:
        monkeypatch.delenv(name)
    # Environment variables win over the .env file, which may hold a real key:
    # with these, any Settings() built during a test cannot create a real model.
    monkeypatch.setenv("LLM_PROVIDER", "google_genai")
    monkeypatch.setenv("GOOGLE_API_KEY", "")
    yield
