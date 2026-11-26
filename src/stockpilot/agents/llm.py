"""The one place that creates the AI model. Everything else receives a BaseChatModel,
so tests pass a fake one and switching providers is a setting, not a code change."""

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from stockpilot.core.config import Settings


class LlmNotConfiguredError(Exception):
    """The settings cannot create a model. A setup problem, not a business rule."""


def build_chat_model(settings: Settings) -> BaseChatModel:
    if settings.llm_provider == "google_genai":
        key = settings.google_api_key
        # An empty GOOGLE_API_KEY= copied from .env.example is a missing key too.
        if key is None or not key.get_secret_value().strip():
            raise LlmNotConfiguredError(
                "GOOGLE_API_KEY is not set: create a free key in Google AI Studio and put "
                "it in .env, or set LLM_PROVIDER=ollama to use a local model"
            )
        model = init_chat_model(
            settings.llm_model,
            model_provider="google_genai",
            api_key=key.get_secret_value(),
            temperature=settings.llm_temperature,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )
    else:
        model = init_chat_model(
            settings.llm_model,
            model_provider="ollama",
            base_url=settings.ollama_base_url,
            temperature=settings.llm_temperature,
            client_kwargs={"timeout": settings.llm_timeout_seconds},
        )
    # init_chat_model only returns a configurable wrapper when it gets no model name.
    assert isinstance(model, BaseChatModel)
    return model
