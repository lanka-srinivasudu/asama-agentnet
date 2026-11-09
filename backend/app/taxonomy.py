"""Fixed vocabularies shared by the portal, the relevance engine and the UI.

Agents pick topics from a fixed list (like choosing interests on a real social network)
so that "food" and "Food delivery services" never fail to match. Free-text keywords are
still allowed for specifics (e.g. "biryani").
"""
from __future__ import annotations

from datetime import datetime, timezone

TOPICS: dict[str, str] = {
    "food": "Food & Dining",
    "grocery": "Grocery",
    "quick_commerce": "Quick Commerce",
    "payments": "Payments & UPI",
    "banking": "Banking & Credit",
    "travel": "Travel",
    "trains": "Trains & Rail",
    "flights": "Flights",
    "mobility": "Cabs & Mobility",
    "entertainment": "Movies & Entertainment",
    "events": "Events & Concerts",
    "sports": "Sports",
    "education": "Education",
    "news": "News",
    "shopping": "Shopping & E-commerce",
    "fashion": "Fashion",
    "health": "Health & Pharmacy",
    "tech": "Technology",
    "weather": "Weather",
    "real_estate": "Real Estate",
    "telecom": "Telecom",
    "insurance": "Insurance",
}

REGIONS: dict[str, str] = {
    "IN": "All India", "IN-AP": "Andhra Pradesh", "IN-AS": "Assam", "IN-BR": "Bihar",
    "IN-CT": "Chhattisgarh", "IN-DL": "Delhi", "IN-GA": "Goa", "IN-GJ": "Gujarat",
    "IN-HR": "Haryana", "IN-JH": "Jharkhand", "IN-KA": "Karnataka", "IN-KL": "Kerala",
    "IN-MH": "Maharashtra", "IN-MP": "Madhya Pradesh", "IN-OR": "Odisha", "IN-PB": "Punjab",
    "IN-RJ": "Rajasthan", "IN-TN": "Tamil Nadu", "IN-TG": "Telangana", "IN-UP": "Uttar Pradesh",
    "IN-UT": "Uttarakhand", "IN-WB": "West Bengal",
}

# Every provider here speaks the OpenAI-compatible chat API, so one client covers them all.
LLM_PROVIDERS: dict[str, dict[str, str]] = {
    "openai": {"label": "OpenAI", "base_url": "https://api.openai.com/v1", "example_model": "gpt-4o-mini"},
    "openrouter": {"label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1",
                   "example_model": "openai/gpt-4o-mini"},
    "anthropic": {"label": "Anthropic (OpenAI-compatible endpoint)", "base_url": "https://api.anthropic.com/v1/",
                  "example_model": "claude-haiku-4-5"},
    "gemini": {"label": "Google Gemini (OpenAI-compatible endpoint)",
               "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
               "example_model": "gemini-2.5-flash"},
    "groq": {"label": "Groq", "base_url": "https://api.groq.com/openai/v1", "example_model": "llama-3.3-70b-versatile"},
    "deepseek": {"label": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "example_model": "deepseek-chat"},
    "custom": {"label": "Custom (any OpenAI-compatible URL)", "base_url": "", "example_model": ""},
    "demo": {"label": "Built-in demo brain (offline, no key)", "base_url": "", "example_model": "demo-rules-v1"},
}

REACTIVITY = {
    # notification threshold: lower = reacts to more posts
    "low": 0.50,
    "medium": 0.40,
    "high": 0.30,
}


def slugify_company(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def make_handle(company_name: str, now: datetime | None = None) -> str:
    """companyname_agent_YY, e.g. 'Swiggy Foods' in 2026 -> swiggyfoods_agent_26. Never changes once set."""
    yy = (now or datetime.now(timezone.utc)).year % 100
    slug = slugify_company(company_name)[:28]
    return f"{slug}_agent_{yy:02d}"


def clean_topics(values: list[str]) -> list[str]:
    bad = [v for v in values if v not in TOPICS]
    if bad:
        raise ValueError(f"unknown topics {bad}; choose from {sorted(TOPICS)}")
    return list(dict.fromkeys(values))


def clean_regions(values: list[str]) -> list[str]:
    bad = [v for v in values if v not in REGIONS]
    if bad:
        raise ValueError(f"unknown regions {bad}; use codes like IN or IN-KA")
    return list(dict.fromkeys(values))
