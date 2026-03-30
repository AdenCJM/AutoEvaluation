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


class ModelClient:
    """Unified interface for LLM generation across providers."""

    # Per-token pricing (USD) by provider/model prefix. Used for cost estimates only.
    _PRICING = {
        "gemini-2.5-flash": (0.15e-6, 0.60e-6),
        "gemini-2.5-pro": (1.25e-6, 10.0e-6),
        "gemini-2.0-flash": (0.10e-6, 0.40e-6),
        "gpt-4o": (2.50e-6, 10.0e-6),
        "gpt-4o-mini": (0.15e-6, 0.60e-6),
        "gpt-4.1": (2.00e-6, 8.00e-6),
        "gpt-4.1-mini": (0.40e-6, 1.60e-6),
        "gpt-4.1-nano": (0.10e-6, 0.40e-6),
        "claude-sonnet-4": (3.00e-6, 15.0e-6),
        "claude-haiku-4": (0.80e-6, 4.00e-6),
        "claude-opus-4": (15.0e-6, 75.0e-6),
    }

    def __init__(self, provider: str, model: str, api_key_env: str):
        self.provider = provider.lower()
        self.model = model
        self.api_key_env = api_key_env
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self._token_lock = threading.Lock()
        load_env()
        self.api_key = os.environ.get(api_key_env)
        if not self.api_key:
            print(
                f"Error: {api_key_env} not set in .env or environment",
                file=sys.stderr,
            )
            sys.exit(1)
        self._client = self._get_client()

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
            return OpenAI(api_key=self.api_key)

        elif self.provider == "anthropic":
            try:
                import anthropic
            except ImportError:
                print("Error: pip install anthropic", file=sys.stderr)
                sys.exit(1)
            return anthropic.Anthropic(api_key=self.api_key)

        else:
            print(
                f"Error: Unknown provider '{self.provider}'. "
                f"Supported: gemini, openai, anthropic",
                file=sys.stderr,
            )
            sys.exit(1)

    # Errors that should be retried (transient)
    _RETRYABLE_ERRORS = (
        "RateLimitError", "ResourceExhausted", "APIConnectionError",
        "InternalServerError", "ServiceUnavailable",
    )

    def _is_retryable(self, error: Exception) -> bool:
        """Check if an error is transient and should be retried."""
        return type(error).__name__ in self._RETRYABLE_ERRORS

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
    ) -> str:
        """Generate text from the model with retry logic. Returns the response as a string."""
        max_attempts = 3
        base_delays = [2, 4, 8]

        for attempt in range(max_attempts):
            try:
                return self._generate_once(system_prompt, user_prompt, max_tokens)
            except Exception as e:
                if not self._is_retryable(e) or attempt == max_attempts - 1:
                    raise
                delay = base_delays[attempt] * (1 + random.uniform(-0.3, 0.3))
                print(f"  Retry {attempt + 1}/{max_attempts} after {type(e).__name__}, waiting {delay:.1f}s...", file=sys.stderr)
                time.sleep(delay)

        # Unreachable, but satisfies type checkers
        raise RuntimeError("Exhausted retries")

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
        for prefix, (input_price, output_price) in self._PRICING.items():
            if self.model.startswith(prefix):
                return (self.total_input_tokens * input_price +
                        self.total_output_tokens * output_price)
        return 0.0  # Unknown model — can't estimate

    def usage_summary(self) -> dict:
        """Return token usage and cost summary."""
        return {
            "input_tokens": self.total_input_tokens,
            "output_tokens": self.total_output_tokens,
            "estimated_cost_usd": round(self.estimated_cost_usd, 4),
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
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=max_tokens,
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
