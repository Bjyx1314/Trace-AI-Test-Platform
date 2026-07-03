import pytest

from app.agents.llm import _model_for
from app.config import settings


def test_ai_model_has_no_implicit_default(monkeypatch):
    monkeypatch.setattr(settings, "ai_model", None)

    with pytest.raises(RuntimeError, match="AI 模型未配置"):
        _model_for("anthropic")


def test_ai_model_uses_explicit_config(monkeypatch):
    monkeypatch.setattr(settings, "ai_model", "  configured-model  ")

    assert _model_for("openai") == "configured-model"
