"""Provider-agnostic chat models.

OpenAI, OpenRouter, Groq, DeepSeek, Gemini and Anthropic all expose an OpenAI-compatible
chat endpoint, so a single LangChain `ChatOpenAI` client with a different `base_url`
covers every provider a company picks in the portal. Provider "demo" uses no LLM at all.
"""
from __future__ import annotations

import time
from typing import Any

from ..crypto import decrypt
from ..models import Agent
from ..taxonomy import LLM_PROVIDERS


def resolve_base_url(provider: str, base_url: str | None) -> str | None:
    if base_url:
        return base_url
    preset = LLM_PROVIDERS.get(provider, {})
    return preset.get("base_url") or None


def make_chat_model(provider: str, model: str, api_key: str, base_url: str | None = None,
                    temperature: float = 0.6, timeout: float = 45.0) -> Any:
    from langchain_openai import ChatOpenAI  # imported lazily: the demo brain needs no LLM libraries

    headers = {}
    if provider == "openrouter":   # OpenRouter asks apps to identify themselves
        headers = {"HTTP-Referer": "https://github.com/lanka-srinivasudu/asama-agentnet", "X-Title": "AgentNet"}
    return ChatOpenAI(model=model, api_key=api_key, base_url=resolve_base_url(provider, base_url),
                      temperature=temperature, timeout=timeout, max_retries=1,
                      default_headers=headers or None)


def chat_model_for(agent: Agent) -> Any | None:
    if agent.llm_provider == "demo":
        return None
    key = decrypt(agent.llm_api_key_enc)
    if not key or not agent.llm_model:
        raise RuntimeError("LLM provider selected but the model or API key is missing - edit the agent in the portal")
    return make_chat_model(agent.llm_provider, agent.llm_model, key, agent.llm_base_url)


def test_llm(provider: str, model: str | None, api_key: str | None, base_url: str | None) -> tuple[bool, str, dict]:
    if provider == "demo":
        return True, "The built-in demo brain needs no key.", {}
    if not model or not api_key:
        return False, "Model and API key are required.", {}
    t0 = time.perf_counter()
    try:
        llm = make_chat_model(provider, model, api_key, base_url, temperature=0, timeout=20)
        msg = llm.invoke("Reply with the single word OK.")
        ms = int((time.perf_counter() - t0) * 1000)
        text = (getattr(msg, "content", "") or "").strip()[:40]
        return True, f"Connected to {model} in {ms} ms", {"reply": text, "latency_ms": ms}
    except Exception as e:  # noqa: BLE001 - surface provider errors to the portal as-is
        return False, f"{type(e).__name__}: {str(e)[:300]}", {}
