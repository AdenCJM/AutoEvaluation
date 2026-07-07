"""
Model Client — Provider Abstraction
=====================================
Thin wrapper over LLM providers so the rest of the engine doesn't care
which API you're using. Reads provider/model/key from config.

Supported providers: gemini, openai, anthropic
To add your own: add an elif block in _get_client().

Usage:
    from model_client import ModelClient
    client = ModelClient.from_config("config.yaml")
    response = client.generate("You are a helpful assistant.", "Write a haiku.")
"""

import json
import os
import random
import sys
import threading
import time
import yaml
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import PROJECT_ROOT, load_env


def extract_json_object(text: str) -> dict | None:
    """Best-effort extraction of a JSON object from model output.

    Handles markdown code fences and JSON embedded in surrounding prose.
    Returns None if no parseable object is found.
    """
    import re

    json_text = text.strip()
    if json_text.startswith("```"):
        json_text = re.sub(r'^```(?:json)?\s*', '', json_text)
        json_text = re.sub(r'\s*```$', '', json_text)

    try:
        parsed = json.loads(json_text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group())
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None
    return None


class ModelClient:
    """Unified interface for LLM generation across providers."""

    # Per-token pricing (USD) by provider/model prefix. Used for cost estimates
    # only. Longest prefix wins, so a specific entry (claude-sonnet-4-6)
    # shadows a broader one (claude-sonnet-4). Verified against provider
    # pricing pages 2026-07-07 — review when a new model generation lands.
    _PRICING = {
        # Gemini
        "gemini-3.5-flash": (1.50e-6, 9.00e-6),
        "gemini-3.1-flash-lite": (0.25e-6, 1.50e-6),
        "gemini-3.1-pro": (2.00e-6, 12.0e-6),
        "gemini-2.5-flash": (0.30e-6, 2.50e-6),
        # OpenAI
        "gpt-5.5": (5.00e-6, 30.0e-6),
        "gpt-5.4-mini": (0.75e-6, 4.50e-6),
        "gpt-5.4-nano": (0.20e-6, 1.25e-6),
        "gpt-5.4": (2.50e-6, 15.0e-6),
        # Anthropic
        "claude-fable-5": (10.0e-6, 50.0e-6),
        "claude-opus-4-8": (5.00e-6, 25.0e-6),
        "claude-opus-4-7": (5.00e-6, 25.0e-6),
        "claude-opus-4-6": (5.00e-6, 25.0e-6),
        "claude-opus-4-5": (5.00e-6, 25.0e-6),
        "claude-sonnet-5": (3.00e-6, 15.0e-6),
        "claude-sonnet-4-6": (3.00e-6, 15.0e-6),
        "claude-sonnet-4-5": (3.00e-6, 15.0e-6),
        "claude-haiku-4-5": (1.00e-6, 5.00e-6),
    }

    @classmethod
    def price_for_model(cls, model: str) -> tuple | None:
        """Return (input_price, output_price) per token, or None if unknown.

        Longest matching prefix wins so versioned IDs resolve correctly.
        """
        for prefix in sorted(cls._PRICING, key=len, reverse=True):
            if model.startswith(prefix):
                return cls._PRICING[prefix]
        return None

    def __init__(self, provider: str, model: str, api_key_env: str):
        self.provider = provider.lower()
        self.model = model
        self.api_key_env = api_key_env
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self._token_lock = threading.Lock()
        self._warned_unknown_pricing = False
        load_env()
        self.api_key = os.environ.get(api_key_env)
        if not self.api_key:
            print(
                f"Error: {api_key_env} not set in .env or environment",
                file=sys.stderr,
            )
            sys.exit(1)
        self._client = self._get_client()

    @property
    def pricing_known(self) -> bool:
        """Whether the configured model resolves to a pricing entry."""
        return self.price_for_model(self.model) is not None

    @classmethod
    def from_config(cls, config_path: str = "config.yaml", judge: bool = False) -> "ModelClient":
        """Create a ModelClient from a config.yaml file.

        When judge=True, reads judge_provider/judge_model/judge_api_key_env
        from config. Falls back to primary keys if judge keys are absent.
        """
        cfg = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
        if judge:
            return cls(
                provider=cfg.get("judge_provider", cfg.get("provider", "gemini")),
                model=cfg.get("judge_model", cfg.get("model", "gemini-2.5-flash")),
                api_key_env=cfg.get("judge_api_key_env", cfg.get("api_key_env", "GEMINI_API_KEY")),
            )
        return cls(
            provider=cfg.get("provider", "gemini"),
            model=cfg.get("model", "gemini-2.5-flash"),
            api_key_env=cfg.get("api_key_env", "GEMINI_API_KEY"),
        )

    def _get_client(self):
        """Initialise the provider-specific SDK client."""
        if self.provider == "gemini":
            try:
                from google import genai
            except ImportError:
                print("Error: pip install google-genai", file=sys.stderr)
                sys.exit(1)
            return genai.Client(api_key=self.api_key)

        elif self.provider == "openai":
            try:
                from openai import OpenAI
            except ImportError:
                print("Error: pip install openai", file=sys.stderr)
                sys.exit(1)
            # max_retries=0: generate() owns retry, so the SDK must not stack
            # its own attempts on top (compounds delays and defeats budgeting)
            return OpenAI(api_key=self.api_key, max_retries=0)

        elif self.provider == "anthropic":
            try:
                import anthropic
            except ImportError:
                print("Error: pip install anthropic", file=sys.stderr)
                sys.exit(1)
            return anthropic.Anthropic(api_key=self.api_key, max_retries=0)

        else:
            print(
                f"Error: Unknown provider '{self.provider}'. "
                f"Supported: gemini, openai, anthropic",
                file=sys.stderr,
            )
            sys.exit(1)

    # Errors that should be retried (transient) — name-match fallback for
    # providers whose SDK isn't importable in this process
    _RETRYABLE_ERRORS = (
        "RateLimitError", "ResourceExhausted", "APIConnectionError",
        "InternalServerError", "ServiceUnavailable", "OverloadedError",
    )

    def _is_retryable(self, error: Exception) -> bool:
        """Check if an error is transient and should be retried.

        Prefers isinstance checks against the active provider's SDK exception
        classes; falls back to class-name matching when the SDK types aren't
        available (e.g. in tests with mock errors).
        """
        try:
            if self.provider == "anthropic":
                import anthropic
                if isinstance(error, (anthropic.RateLimitError,
                                      anthropic.InternalServerError,
                                      anthropic.APIConnectionError)):
                    return True
                if isinstance(error, anthropic.APIStatusError):
                    return error.status_code in (408, 409, 429, 529) or error.status_code >= 500
                if isinstance(error, anthropic.APIError):
                    return False
            elif self.provider == "openai":
                import openai
                if isinstance(error, (openai.RateLimitError,
                                      openai.InternalServerError,
                                      openai.APIConnectionError)):
                    return True
                if isinstance(error, openai.APIStatusError):
                    return error.status_code in (408, 409, 429) or error.status_code >= 500
                if isinstance(error, openai.APIError):
                    return False
        except ImportError:
            pass
        return type(error).__name__ in self._RETRYABLE_ERRORS

    def _with_retry(self, fn):
        """Run fn with jittered exponential backoff on transient errors."""
        max_attempts = 3
        base_delays = [2, 4, 8]

        for attempt in range(max_attempts):
            try:
                return fn()
            except Exception as e:
                if not self._is_retryable(e) or attempt == max_attempts - 1:
                    raise
                delay = base_delays[attempt] * (1 + random.uniform(-0.3, 0.3))
                print(f"  Retry {attempt + 1}/{max_attempts} after {type(e).__name__}, waiting {delay:.1f}s...", file=sys.stderr)
                time.sleep(delay)

        # Unreachable, but satisfies type checkers
        raise RuntimeError("Exhausted retries")

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
    ) -> str:
        """Generate text from the model with retry logic. Returns the response as a string."""
        return self._with_retry(
            lambda: self._generate_once(system_prompt, user_prompt, max_tokens)
        )

    def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: dict,
        max_tokens: int = 2048,
    ) -> dict:
        """Generate a JSON object conforming to `schema` (a JSON Schema dict).

        Uses the provider's native structured-output mechanism where available
        (anthropic output_config.format, openai response_format json_schema,
        gemini response_schema) and falls back to prompt-and-parse otherwise.
        Raises ValueError if a valid JSON object cannot be obtained.
        """
        text = self._with_retry(
            lambda: self._generate_structured_once(system_prompt, user_prompt, schema, max_tokens)
        )
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            parsed = extract_json_object(text or "")
            if parsed is None:
                raise ValueError(f"Model returned non-JSON output: {(text or '')[:200]}")
            return parsed

    def _generate_structured_once(
        self, system_prompt: str, user_prompt: str, schema: dict, max_tokens: int
    ) -> str:
        """Single structured-generation attempt. Falls back to prompt-and-parse
        when the provider/SDK rejects native structured output."""
        if not getattr(self, "_structured_unsupported", False):
            try:
                return self._structured_native(system_prompt, user_prompt, schema, max_tokens)
            except Exception as e:
                if self._is_retryable(e):
                    raise
                # Native structured output rejected (older SDK or unsupported
                # model) — remember, and use prompt-and-parse from here on
                self._structured_unsupported = True
                print(
                    f"  Note: native structured output unavailable ({type(e).__name__}), "
                    f"falling back to prompt-and-parse",
                    file=sys.stderr,
                )
        fallback_system = (
            system_prompt
            + "\n\nYou MUST respond with ONLY a valid JSON object matching this JSON Schema:\n"
            + json.dumps(schema)
        )
        return self._generate_once(fallback_system, user_prompt, max_tokens)

    def _structured_native(
        self, system_prompt: str, user_prompt: str, schema: dict, max_tokens: int
    ) -> str:
        """Provider-native structured output. Raises on unsupported providers/SDKs."""
        if self.provider == "anthropic":
            response = self._client.messages.create(
                model=self.model,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
                max_tokens=max_tokens,
                output_config={"format": {"type": "json_schema", "schema": schema}},
            )
            inp, out = self._extract_usage(response)
            self._accumulate_tokens(inp, out)
            return response.content[0].text

        elif self.provider == "openai":
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_completion_tokens=max_tokens,
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "structured_output", "strict": True, "schema": schema},
                },
            )
            inp, out = self._extract_usage(response)
            self._accumulate_tokens(inp, out)
            return response.choices[0].message.content

        elif self.provider == "gemini":
            from google import genai

            response = self._client.models.generate_content(
                model=self.model,
                contents=user_prompt,
                config=genai.types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    max_output_tokens=max_tokens,
                    response_mime_type="application/json",
                    response_schema=schema,
                ),
            )
            inp, out = self._extract_usage(response)
            self._accumulate_tokens(inp, out)
            return response.text

        raise ValueError(f"No native structured output for provider: {self.provider}")

    def _extract_usage(self, response) -> tuple:
        """Extract (input_tokens, output_tokens) from a provider response."""
        try:
            if self.provider == "gemini":
                um = getattr(response, "usage_metadata", None)
                if um:
                    return (getattr(um, "prompt_token_count", 0) or 0,
                            getattr(um, "candidates_token_count", 0) or 0)
            elif self.provider == "openai":
                u = getattr(response, "usage", None)
                if u:
                    return (getattr(u, "prompt_tokens", 0) or 0,
                            getattr(u, "completion_tokens", 0) or 0)
            elif self.provider == "anthropic":
                u = getattr(response, "usage", None)
                if u:
                    return (getattr(u, "input_tokens", 0) or 0,
                            getattr(u, "output_tokens", 0) or 0)
        except Exception:
            pass
        return (0, 0)

    def _accumulate_tokens(self, inp: int, out: int) -> None:
        """Thread-safe token accumulation + write to per-process log."""
        with self._token_lock:
            self.total_input_tokens += inp
            self.total_output_tokens += out
        # Write per-process token log for cross-subprocess aggregation
        if inp or out:
            log_dir = PROJECT_ROOT / ".tmp"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / f"token_usage_{os.getpid()}.jsonl"
            try:
                entry = json.dumps({"input": inp, "output": out, "model": self.model})
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(entry + "\n")
            except OSError:
                pass  # Best-effort, don't crash on log failure

    @property
    def estimated_cost_usd(self) -> float:
        """Estimated cost based on accumulated tokens and known pricing."""
        pricing = self.price_for_model(self.model)
        if pricing is None:
            if not self._warned_unknown_pricing:
                self._warned_unknown_pricing = True
                print(
                    f"Warning: no pricing entry for model '{self.model}' — cost tracking "
                    f"is disabled and any max_cost_usd cap will NOT trigger. "
                    f"Add the model to ModelClient._PRICING to restore cost estimates.",
                    file=sys.stderr,
                )
            return 0.0
        input_price, output_price = pricing
        return (self.total_input_tokens * input_price +
                self.total_output_tokens * output_price)

    def usage_summary(self) -> dict:
        """Return token usage and cost summary."""
        return {
            "input_tokens": self.total_input_tokens,
            "output_tokens": self.total_output_tokens,
            "estimated_cost_usd": round(self.estimated_cost_usd, 4),
            "pricing_known": self.pricing_known,
        }

    def _generate_once(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
    ) -> str:
        """Single generation attempt without retry."""
        if self.provider == "gemini":
            from google import genai

            response = self._client.models.generate_content(
                model=self.model,
                contents=user_prompt,
                config=genai.types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    max_output_tokens=max_tokens,
                ),
            )
            inp, out = self._extract_usage(response)
            self._accumulate_tokens(inp, out)
            return response.text

        elif self.provider == "openai":
            # max_completion_tokens replaced max_tokens (rejected on gpt-5.x)
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_completion_tokens=max_tokens,
            )
            inp, out = self._extract_usage(response)
            self._accumulate_tokens(inp, out)
            return response.choices[0].message.content

        elif self.provider == "anthropic":
            response = self._client.messages.create(
                model=self.model,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
                max_tokens=max_tokens,
            )
            inp, out = self._extract_usage(response)
            self._accumulate_tokens(inp, out)
            return response.content[0].text

        else:
            raise ValueError(f"Unknown provider: {self.provider}")
