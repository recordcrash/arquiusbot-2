from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, AsyncGenerator, Iterable

import openai
from constants.ai import MODEL_PRICING_TABLE

# --- Pricing Table & Helpers ---


def get_pricing_rates(model_string: str) -> dict:
    """
    Returns pricing rates for the given model.
    If the model is fine-tuned (starts with 'ft:'), it looks up the rates using the ft-prefixed key.
    If not found, it falls back to the base model (by stripping 'ft:').
    """
    model = get_base_model_name(model_string)
    if model.startswith("ft:"):
        rates = MODEL_PRICING_TABLE.get(model)
        if rates is not None:
            return rates
        else:
            # Fallback: remove "ft:" prefix and try the base model
            base_model = model[3:]
            return MODEL_PRICING_TABLE.get(
                base_model, {"input": 0, "cached_input": 0, "output": 0}
            )
    else:
        return MODEL_PRICING_TABLE.get(
            model, {"input": 0, "cached_input": 0, "output": 0}
        )


def calculate_cost(usage, model_string: str) -> float:
    """
    Calculates the total cost (in dollars) based on the usage and model rates.
    It takes into account:
      - input tokens,
      - cached input tokens,
      - output tokens.
    All costs are per 1,000,000 tokens.
    """
    rates = get_pricing_rates(model_string)
    input_tokens = usage.input_tokens
    # Why is this the only attribute that comes in a dict???
    cached_tokens = usage.input_tokens_details.get("cached_tokens", 0)
    output_tokens = usage.output_tokens

    # Multiply token counts by their respective rates, then divide by million
    total_cost = (
        input_tokens * rates["input"]
        + cached_tokens * rates["cached_input"]
        + output_tokens * rates["output"]
    ) / 1000000
    return max(total_cost, 0.0001)


# --- Text Processing Helpers ---


def get_base_model_name(model_string: str) -> str:
    """
    Returns the base model name by removing the fine-tuning prefix and extraneous information
    ft:gpt-4o-2024-08-06:org:finetuned_name:id -> ft:gpt-4o-2024-08-06
    gpt-4o-2024-08-06:org:finetuned_name:id -> gpt-4o-2024-08-06
    """
    if model_string.startswith("ft:"):
        return model_string.split(":")[0] + ":" + model_string.split(":")[1]
    return model_string.split(":")[0]


def get_user_friendly_model_string(model: str) -> str:
    """
    Returns either the base model name or the finetuned_name depending
    ft:gpt-4o-2024-08-06:org:finetuned_name:id -> finetuned_name
    gpt-4o-2024-08-06:org:finetuned_name:id -> gpt-4o-2024-08-06
    """
    if model.startswith("ft:"):
        return model.split(":")[3]
    return model.split(":")[0]


# --- AI Client ---


class AIClient:
    def __init__(
        self,
        api_key: str = None,
        censored_words: list[str] = None,
        censor_character: str = "*",
        server_emotes: dict[str, str] = None,
    ) -> None:
        if not censored_words:
            censored_words = []
        if not server_emotes:
            server_emotes = {}

        self.client: openai.AsyncClient | None = None
        if api_key:
            self.client = openai.AsyncClient(api_key=api_key)
        self.censored_words: list[str] = censored_words
        self.censor_character: str = censor_character
        self.server_emotes: dict[str, str] = server_emotes

    async def stream_response(
        self,
        model: str,
        label: str,
        system_prompt: str | None,
        prompt: str | list[dict[str, Any]],
        prev_resp_id: str | None = None,
        temperature: float = 1.0,
        on_completed: Callable[[dict], None] | None = None,
    ) -> AsyncGenerator[tuple[str | None, str | Any, str], Any]:
        input_characters = self._count_input_characters(prompt)
        input_tokens = input_characters // 4
        real_max_output_characters = 1000
        max_characters = real_max_output_characters - input_characters
        max_tokens = max_characters * 4

        client = self._require_client()

        stream = await client.responses.create(
            model=model,
            input=prompt,
            instructions=system_prompt,
            previous_response_id=prev_resp_id,
            temperature=temperature,
            max_output_tokens=max_tokens,
            stream=True,
        )

        model_label = label or get_user_friendly_model_string(model)

        content = ""
        footer = (
            f"☸{model_label} | 🌡{round(temperature, 3)} | ✉{input_tokens} | $0.0001"
        )
        response_id = None

        async for event in stream:
            if event.type == "response.created":
                response_id = event.response.id
            elif event.type == "response.output_text.delta":
                content += event.delta
                yield response_id, content, footer
            elif event.type == "response.completed":
                response_dict = self._response_to_dict(event.response)
                content = self._extract_text_from_output(
                    response_dict.get("output", [])
                )
                footer = self._build_footer_from_response(
                    response_dict,
                    model,
                    model_label,
                    temperature,
                    fallback_footer=footer,
                )
                if on_completed:
                    on_completed(response_dict)
                yield response_id, content, footer
            elif event.type == "response.error":
                yield response_id, "An error occurred.", footer
                break

    def _extract_text_from_output(
        self,
        output_items: Iterable[Any],
        *,
        fallback_text: str | Iterable[str] | None = None,
    ) -> str:
        collected: list[str] = []
        if output_items:
            for item in output_items:
                contents = self._get_attr_or_key(item, "content", None)
                if not contents:
                    continue
                if isinstance(contents, dict):
                    contents = [contents]
                for content in contents:
                    text = self._get_attr_or_key(content, "text", None)
                    if text:
                        collected.append(text)
        if not collected and fallback_text:
            if isinstance(fallback_text, str):
                collected.append(fallback_text)
            elif isinstance(fallback_text, Iterable):
                for chunk in fallback_text:
                    if isinstance(chunk, str):
                        collected.append(chunk)
        return "".join(collected).strip()

    def _count_input_characters(self, prompt: str | list[dict[str, Any]]) -> int:
        if isinstance(prompt, str):
            return len(prompt)
        total = 0
        for part in prompt:
            content = part.get("content", "")
            if isinstance(content, str):
                total += len(content)
            elif isinstance(content, list):
                for chunk in content:
                    if isinstance(chunk, dict) and chunk.get("type") == "text":
                        total += len(chunk.get("text", ""))
        return total

    def _strip_code_fences(self, text: str) -> str:
        stripped = text.strip()
        if stripped.startswith("```") and stripped.endswith("```"):
            lines = stripped.splitlines()
            if len(lines) >= 2:
                return "\n".join(lines[1:-1]).strip()
        return stripped

    def _require_client(self) -> openai.AsyncClient:
        if self.client is None:
            raise RuntimeError(
                "AIClient cannot make API calls without an API key"
            )
        return self.client

    def summarize_response_data(
        self, response_data: dict, model: str, label: str, temperature: float
    ) -> tuple[str | None, str, str]:
        response_id = response_data.get("id")
        content = self._extract_text_from_output(response_data.get("output", []))
        footer = self._build_footer_from_response(
            response_data,
            model,
            label or get_user_friendly_model_string(model),
            temperature,
            fallback_footer=(
                f"☸{label or get_user_friendly_model_string(model)} | "
                f"🌡{round(temperature, 3)} | ✉0 | $0.0000"
            ),
        )
        return response_id, content, footer

    def _get_attr_or_key(self, obj: Any, key: str, default: Any) -> Any:
        if obj is None:
            return default
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    def _response_to_dict(self, response: Any) -> dict:
        if isinstance(response, dict):
            return response
        if hasattr(response, "model_dump"):
            return response.model_dump()
        if hasattr(response, "dict"):
            return response.dict()
        raise TypeError("Unsupported response type for serialization")

    def _build_footer_from_response(
        self,
        response_data: dict,
        model: str,
        label: str,
        temperature: float,
        *,
        fallback_footer: str,
    ) -> str:
        usage_data = response_data.get("usage")
        if not usage_data:
            return fallback_footer

        usage_obj = self._build_usage_namespace(usage_data)
        cost = calculate_cost(usage_obj, model)
        total_tokens = getattr(
            usage_obj,
            "total_tokens",
            usage_obj.input_tokens + usage_obj.output_tokens,
        )
        return (
            f"☸{label} | "
            f"🌡{round(temperature, 3)} | "
            f"✉{total_tokens} | "
            f"${cost:.4f}"
        )

    def _build_usage_namespace(self, usage_data: Any) -> SimpleNamespace:
        if isinstance(usage_data, dict):
            input_tokens = usage_data.get("input_tokens", 0)
            output_tokens = usage_data.get("output_tokens", 0)
            input_details = usage_data.get("input_tokens_details", {})
            total_tokens = usage_data.get(
                "total_tokens", input_tokens + output_tokens
            )
        else:
            input_tokens = getattr(usage_data, "input_tokens", 0)
            output_tokens = getattr(usage_data, "output_tokens", 0)
            input_details = getattr(usage_data, "input_tokens_details", {})
            total_tokens = getattr(
                usage_data, "total_tokens", input_tokens + output_tokens
            )
        return SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            input_tokens_details=input_details,
            total_tokens=total_tokens,
        )

    def censor_text(self, text: str) -> str:
        for word in self.censored_words:
            text = text.replace(word, self.censor_character * len(word))
        return text

    def replace_emotes(self, text: str) -> str:
        for name, emote in self.server_emotes.items():
            text = text.replace(f":{name}:", emote)
        return text

    def sanitize_for_embed(self, text: str) -> str:
        """
        Replaces text for server emotes, censors it and trims it.
        """
        emoted_text = self.replace_emotes(text)
        censored_text = self.censor_text(emoted_text)
        trimmed_text = (
            censored_text[:1000] + "..." if len(censored_text) > 1000 else censored_text
        )
        return trimmed_text
