"""Tests for call_llm_generate's primary → fallback behaviour.

Context (2026-07-30): the primary LLM moved to the Volcengine Agent Plan
(ark runtime) with the previous DeepSeek-direct config kept as the
system-level fallback (AI_FALLBACK_* env vars). Every LLM feature —
translations, dynamic sentences, encouragement, daily reports, listening
stories — must survive a primary outage without per-call-site handling.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

import app.core.config as config_module
from app.services import llm_translation
from app.services.llm_translation import LlmTranslationSettings, call_llm_generate, with_agent_plan_primary

PRIMARY = LlmTranslationSettings(
    provider="openai",
    base_url="https://ark.cn-beijing.volces.com/api/plan/v3",
    model="deepseek-v4-flash-modelhub",
    api_key="plan-key",
)
FALLBACK = LlmTranslationSettings(
    provider="deepseek",
    base_url="https://api.deepseek.com",
    model="deepseek-v4-flash",
    api_key="direct-key",
)


@pytest.fixture()
def _no_env_fallback(monkeypatch):
    """Isolate fallback tests from any AI_FALLBACK_* env on the host."""
    monkeypatch.setattr(llm_translation, "_env_fallback_settings", lambda: None)


@pytest.mark.usefixtures("_no_env_fallback")
class TestFallback:
    def test_primary_success_does_not_touch_fallback(self, monkeypatch):
        calls: list[tuple[str, str]] = []

        def fake_dispatch(settings, prompt):
            calls.append((settings.base_url, settings.model))
            return "你好"

        monkeypatch.setattr(llm_translation, "_dispatch_llm_generate", fake_dispatch)
        assert call_llm_generate(PRIMARY, "hi") == "你好"
        assert calls == [("https://ark.cn-beijing.volces.com/api/plan/v3", "deepseek-v4-flash-modelhub")]

    def test_primary_failure_retries_with_attached_fallback(self, monkeypatch):
        calls: list[str] = []

        def fake_dispatch(settings, prompt):
            calls.append(settings.model)
            if len(calls) == 1:
                raise ValueError("LLM translation failed: HTTP 429")
            return "fallback-ok"

        monkeypatch.setattr(llm_translation, "_dispatch_llm_generate", fake_dispatch)
        primary = replace(PRIMARY, fallback=FALLBACK)
        assert call_llm_generate(primary, "hi") == "fallback-ok"
        assert calls == ["deepseek-v4-flash-modelhub", "deepseek-v4-flash"]

    def test_env_fallback_used_when_no_attached_fallback(self, monkeypatch):
        monkeypatch.setattr(llm_translation, "_env_fallback_settings", lambda: FALLBACK)
        calls: list[str] = []

        def fake_dispatch(settings, prompt):
            calls.append(settings.model)
            if len(calls) == 1:
                raise ValueError("boom")
            return "env-fallback-ok"

        monkeypatch.setattr(llm_translation, "_dispatch_llm_generate", fake_dispatch)
        assert call_llm_generate(PRIMARY, "hi") == "env-fallback-ok"
        assert calls == ["deepseek-v4-flash-modelhub", "deepseek-v4-flash"]

    def test_no_fallback_reraises_original_error(self, monkeypatch):
        def fake_dispatch(settings, prompt):
            raise ValueError("primary is down")

        monkeypatch.setattr(llm_translation, "_dispatch_llm_generate", fake_dispatch)
        with pytest.raises(ValueError, match="primary is down"):
            call_llm_generate(PRIMARY, "hi")

    def test_fallback_failure_propagates_its_error(self, monkeypatch):
        def fake_dispatch(settings, prompt):
            if settings.model == PRIMARY.model:
                raise ValueError("primary down")
            raise ValueError("fallback also down")

        monkeypatch.setattr(llm_translation, "_dispatch_llm_generate", fake_dispatch)
        primary = replace(PRIMARY, fallback=FALLBACK)
        with pytest.raises(ValueError, match="fallback also down"):
            call_llm_generate(primary, "hi")


class TestEnvFallbackSettings:
    """The real _env_fallback_settings (no _no_env_fallback fixture here)."""

    def test_returns_none_when_env_incomplete(self, monkeypatch):
        fake = type("S", (), {
            "ai_fallback_provider": "",
            "ai_fallback_base_url": "",
            "ai_fallback_model": "",
            "ai_fallback_api_key": None,
        })
        monkeypatch.setattr(config_module, "settings", fake())
        monkeypatch.setattr(llm_translation, "_env_fallback_cache", llm_translation._ENV_FALLBACK_UNSET)
        assert llm_translation._env_fallback_settings() is None

    def test_builds_and_caches_settings_from_app_config(self, monkeypatch):
        fake = type("S", (), {
            "ai_fallback_provider": "deepseek",
            "ai_fallback_base_url": "https://api.deepseek.com",
            "ai_fallback_model": "deepseek-v4-flash",
            "ai_fallback_api_key": " direct-key ",
        })
        monkeypatch.setattr(config_module, "settings", fake())
        monkeypatch.setattr(llm_translation, "_env_fallback_cache", llm_translation._ENV_FALLBACK_UNSET)
        resolved = llm_translation._env_fallback_settings()
        assert resolved is not None
        assert resolved.provider == "deepseek"
        assert resolved.base_url == "https://api.deepseek.com"
        assert resolved.model == "deepseek-v4-flash"
        assert resolved.api_key == "direct-key"
        # Cached: second call returns the same object without re-reading config.
        assert llm_translation._env_fallback_settings() is resolved


class TestAgentPlanPrimary:
    """with_agent_plan_primary: plan key present → plan primary + legacy fallback."""

    def test_no_plan_key_returns_base_unchanged(self):
        assert with_agent_plan_primary(FALLBACK, {}) is FALLBACK
        assert with_agent_plan_primary(FALLBACK, {"agentPlanApiKey": "  "}) is FALLBACK

    def test_plan_key_wraps_base_as_fallback(self):
        stored = {"agentPlanApiKey": "sk-plan-123"}
        wrapped = with_agent_plan_primary(FALLBACK, stored)
        assert wrapped.provider == "openai"
        assert wrapped.base_url == "https://ark.cn-beijing.volces.com/api/plan/v3"
        assert wrapped.model == "deepseek-v4-flash-modelhub"
        assert wrapped.api_key == "sk-plan-123"
        assert wrapped.fallback is FALLBACK

    def test_stored_base_url_and_model_win_over_defaults(self):
        stored = {
            "agentPlanApiKey": "sk-plan-123",
            "agentPlanBaseUrl": "https://ark.cn-beijing.volces.com/api/plan/v3/",
            "agentPlanModel": "doubao-seed-2-0-lite-260215",
        }
        wrapped = with_agent_plan_primary(FALLBACK, stored)
        assert wrapped.base_url == "https://ark.cn-beijing.volces.com/api/plan/v3/"
        assert wrapped.model == "doubao-seed-2-0-lite-260215"

    def test_explicit_overrides_skip_the_wrap(self):
        stored = {"agentPlanApiKey": "sk-plan-123"}
        assert with_agent_plan_primary(FALLBACK, stored, overrides_given=True) is FALLBACK
