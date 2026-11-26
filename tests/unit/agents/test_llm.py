from typing import Any

import pytest
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_ollama import ChatOllama

from stockpilot.agents.llm import LlmNotConfiguredError, build_chat_model
from stockpilot.core.config import Settings

KEY = "test-key-not-real"


def _settings(**values: Any) -> Settings:
    # _env_file=None: a real key in the developer's .env must not reach the tests.
    return Settings(_env_file=None, **values)


def test_gemini_with_the_configured_options() -> None:
    model = build_chat_model(
        _settings(
            google_api_key=KEY,
            llm_model="gemini-test",
            llm_temperature=0.3,
            llm_timeout_seconds=12,
            llm_max_retries=4,
        )
    )

    assert isinstance(model, ChatGoogleGenerativeAI)
    assert model.model == "models/gemini-test"
    assert (model.temperature, model.timeout, model.max_retries) == (0.3, 12, 4)
    assert model.google_api_key is not None
    assert model.google_api_key.get_secret_value() == KEY


@pytest.mark.parametrize("key", [None, "", "   "], ids=["missing", "empty", "blank"])
def test_gemini_without_a_key_says_how_to_get_one(key: str | None) -> None:
    with pytest.raises(LlmNotConfiguredError, match=r"GOOGLE_API_KEY.*Google AI Studio"):
        build_chat_model(_settings(google_api_key=key))


def test_ollama_needs_no_key() -> None:
    model = build_chat_model(
        _settings(
            llm_provider="ollama",
            llm_model="qwen3:8b",
            ollama_base_url="http://ollama:11434",
            llm_timeout_seconds=12,
        )
    )

    assert isinstance(model, ChatOllama)
    assert (model.model, model.base_url, model.temperature) == (
        "qwen3:8b",
        "http://ollama:11434",
        0,
    )
    assert model.client_kwargs == {"timeout": 12}


def test_the_key_never_shows_in_the_settings() -> None:
    settings = _settings(google_api_key=KEY)

    assert KEY not in repr(settings)
    assert KEY not in str(settings.model_dump())


def test_gemini_is_the_default() -> None:
    settings = _settings()

    assert (settings.llm_provider, settings.llm_model) == ("google_genai", "gemini-3.5-flash-lite")
    assert settings.llm_temperature == 0
