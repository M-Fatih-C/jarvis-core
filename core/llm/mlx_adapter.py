"""Qwen MLX adapter for local on-device inference using Apple MLX with concurrency & parsing safeguards."""

import asyncio
import gc
import json
import re
from typing import Any
from uuid import uuid4

from core.agent.exceptions import LLMError, ToolCallParseError
from core.config.settings import Settings, get_settings
from core.llm.base import LLMAdapter
from core.llm.schemas import (
    PROFILE_SETTINGS,
    InferenceProfile,
    LLMResponse,
    ProfileConfig,
)
from core.logging.setup import get_logger
from core.models.messages import ChatMessage, MessageRole
from core.models.tools import ToolCall, ToolDefinition

logger = get_logger("jarvis.llm.mlx")


class QwenMLXAdapter(LLMAdapter):
    """Adapter running Qwen 4-bit models natively on Apple Silicon using mlx-lm."""

    def __init__(
        self,
        model_id: str | None = None,
        profile: InferenceProfile | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._model_id = model_id or self._settings.model_id
        if profile is not None:
            self._profile = profile
        else:
            self._profile = InferenceProfile(self._settings.inference_profile)

        self._model: Any = None
        self._tokenizer: Any = None
        self._is_loaded = False
        self._lock = asyncio.Lock()
        self._inference_lock = asyncio.Lock()

    @property
    def is_loaded(self) -> bool:
        """Return True if model and tokenizer are resident in memory."""
        return self._is_loaded

    @property
    def profile(self) -> InferenceProfile:
        """Return the active inference profile."""
        return self._profile

    async def load(self) -> None:
        """Load model weights and tokenizer into unified memory."""
        async with self._lock:
            if self._is_loaded:
                return

            logger.info("loading_mlx_model", model_id=self._model_id, profile=self._profile.value)
            try:
                import mlx_lm

                def _do_load():
                    return mlx_lm.load(self._model_id)

                self._model, self._tokenizer = await asyncio.to_thread(_do_load)
                self._is_loaded = True
                logger.info("mlx_model_loaded_successfully", model_id=self._model_id)
            except Exception as exc:
                logger.error("mlx_model_load_failed", model_id=self._model_id, error=str(exc))
                raise LLMError(f"Failed to load MLX model '{self._model_id}': {exc}") from exc

    async def unload(self) -> None:
        """Free model weights from memory and invoke garbage collection."""
        async with self._lock:
            if not self._is_loaded:
                return

            logger.info("unloading_mlx_model", model_id=self._model_id)
            self._model = None
            self._tokenizer = None
            self._is_loaded = False
            gc.collect()
            try:
                import mlx.core as mx
                mx.metal.clear_cache()
            except Exception:
                pass
            logger.info("mlx_model_unloaded")

    async def health(self) -> bool:
        """Check if adapter is operational."""
        return self._is_loaded

    def _get_profile_config(self) -> ProfileConfig:
        return PROFILE_SETTINGS.get(self._profile, PROFILE_SETTINGS[InferenceProfile.FAST])

    def _format_messages_for_tokenizer(
        self,
        messages: list[ChatMessage],
    ) -> list[dict[str, Any]]:
        formatted = []
        for msg in messages:
            entry: dict[str, Any] = {
                "role": msg.role.value,
                "content": msg.content or "",
            }
            # Record assistant tool calls in conversation history
            if msg.role == MessageRole.ASSISTANT and msg.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False)
                            if isinstance(tc.arguments, dict)
                            else str(tc.arguments),
                        },
                    }
                    for tc in msg.tool_calls
                ]
            # Record tool result reference in conversation history
            elif msg.role == MessageRole.TOOL:
                if msg.tool_call_id:
                    entry["tool_call_id"] = msg.tool_call_id
                if msg.tool_name:
                    entry["name"] = msg.tool_name

            formatted.append(entry)
        return formatted

    def _format_tools_for_qwen(self, tools: list[ToolDefinition]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                },
            }
            for tool in tools
        ]

    def _parse_tool_calls(self, text: str) -> tuple[str, list[ToolCall]]:
        """Parse XML or JSON tool calls from generated text.
        
        Strictly raises ToolCallParseError if tool syntax is detected but corrupted.
        
        Returns:
            Tuple of (remaining clean content, list of ToolCall objects)
            
        Raises:
            ToolCallParseError: If model output has broken tool call syntax.
        """
        # Check for unclosed <tool_call> tags
        has_open_tag = "<tool_call>" in text
        has_close_tag = "</tool_call>" in text
        if has_open_tag and not has_close_tag:
            raise ToolCallParseError("Unclosed <tool_call> tag detected in model generation.")
        if has_close_tag and not has_open_tag:
            raise ToolCallParseError("Orphaned </tool_call> closing tag detected in model generation.")

        tool_calls: list[ToolCall] = []

        # 1. XML format: <tool_call><function=name>...</function></tool_call>
        xml_matches = list(re.finditer(
            r"<tool_call>\s*<function=([^>]+)>(.*?)</function>\s*</tool_call>",
            text,
            re.DOTALL,
        ))

        if xml_matches:
            for match in xml_matches:
                func_name = match.group(1).strip()
                if not func_name:
                    raise ToolCallParseError("Tool call specified empty function name.")
                body = match.group(2)
                args = {}
                param_matches = list(re.finditer(r"<parameter=([^>]+)>(.*?)</parameter>", body, re.DOTALL))
                for p_match in param_matches:
                    p_name = p_match.group(1).strip()
                    p_val_raw = p_match.group(2).strip()
                    # If value looks like JSON, attempt parsing; if corrupted, raise ToolCallParseError
                    if p_val_raw.startswith(("{", "[")):
                        try:
                            args[p_name] = json.loads(p_val_raw)
                        except json.JSONDecodeError as exc:
                            raise ToolCallParseError(f"Corrupted JSON in parameter '{p_name}': {exc}") from exc
                    else:
                        try:
                            args[p_name] = json.loads(p_val_raw)
                        except Exception:
                            args[p_name] = p_val_raw

                call_id = f"call_{uuid4().hex[:8]}"
                tool_calls.append(ToolCall(id=call_id, name=func_name, arguments=args))

            clean_text = re.sub(
                r"<tool_call>\s*<function=([^>]+)>(.*?)</function>\s*</tool_call>",
                "",
                text,
                flags=re.DOTALL,
            ).strip()
            # Enforce max 1 tool call per step
            return clean_text, tool_calls[:1]

        # 2. JSON format inside <tool_call>...</tool_call>
        raw_tool_blocks = list(re.finditer(r"<tool_call>\s*(.*?)\s*</tool_call>", text, re.DOTALL))
        if raw_tool_blocks:
            for match in raw_tool_blocks:
                raw_inner = match.group(1).strip()
                try:
                    data = json.loads(raw_inner)
                except json.JSONDecodeError as exc:
                    raise ToolCallParseError(f"Malformed JSON within <tool_call>: {exc}") from exc

                if not isinstance(data, dict):
                    raise ToolCallParseError("Tool call JSON must be a dictionary object.")

                name = data.get("name") or data.get("function")
                if not name:
                    raise ToolCallParseError("Tool call JSON is missing 'name' or 'function' property.")

                args = data.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError as exc:
                        raise ToolCallParseError(f"Malformed 'arguments' string in tool call: {exc}") from exc

                call_id = f"call_{uuid4().hex[:8]}"
                tool_calls.append(ToolCall(id=call_id, name=name, arguments=args))

            clean_text = re.sub(r"<tool_call>\s*.*?\s*</tool_call>", "", text, flags=re.DOTALL).strip()
            return clean_text, tool_calls[:1]

        # 3. Code blocks with explicit tool definitions
        code_blocks = list(re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL))
        for match in code_blocks:
            raw_block = match.group(1).strip()
            if any(k in raw_block for k in ('"name"', '"tool"', '"function"')) and any(k in raw_block for k in ('"arguments"', '"parameters"')):
                try:
                    data = json.loads(raw_block)
                    name = data.get("name") or data.get("tool") or data.get("function")
                    args = data.get("arguments") or data.get("parameters") or {}
                    if isinstance(args, str):
                        args = json.loads(args)
                    if name:
                        call_id = f"call_{uuid4().hex[:8]}"
                        tool_calls.append(ToolCall(id=call_id, name=name, arguments=args))
                except json.JSONDecodeError as exc:
                    raise ToolCallParseError(f"Corrupted JSON in tool block: {exc}") from exc

        if tool_calls:
            clean_text = re.sub(
                r"```(?:json)?\s*\{.*?\}\s*```",
                "",
                text,
                flags=re.DOTALL,
            ).strip()
            return clean_text, tool_calls[:1]

        return text.strip(), []

    async def generate(self, messages: list[ChatMessage]) -> LLMResponse:
        """Generate direct text response with concurrency serialization and active profile."""
        if not self._is_loaded:
            await self.load()

        profile = self._get_profile_config()
        formatted_messages = self._format_messages_for_tokenizer(messages)

        def _run_inference() -> str:
            import mlx_lm
            import mlx_lm.sample_utils

            sampler = mlx_lm.sample_utils.make_sampler(
                temp=profile.temperature,
                top_p=profile.top_p,
            )
            prompt = self._tokenizer.apply_chat_template(
                formatted_messages,
                add_generation_prompt=True,
                tokenize=False,
            )
            return mlx_lm.generate(
                self._model,
                self._tokenizer,
                prompt=prompt,
                max_tokens=profile.max_tokens,
                sampler=sampler,
                verbose=False,
            )

        try:
            async with self._inference_lock:
                raw_text = await asyncio.to_thread(_run_inference)
            return LLMResponse(
                content=raw_text.strip(),
                tool_calls=[],
                finish_reason="stop",
                model=self._model_id,
            )
        except Exception as exc:
            logger.error("mlx_generation_failed", error=str(exc))
            raise LLMError(f"Inference error: {exc}") from exc

    async def generate_with_tools(
        self,
        messages: list[ChatMessage],
        tools: list[ToolDefinition],
    ) -> LLMResponse:
        """Generate response with tools, concurrency guard, and active profile."""
        if not self._is_loaded:
            await self.load()

        profile = self._get_profile_config()
        formatted_messages = self._format_messages_for_tokenizer(messages)
        formatted_tools = self._format_tools_for_qwen(tools)

        def _run_tool_inference() -> str:
            import mlx_lm
            import mlx_lm.sample_utils

            sampler = mlx_lm.sample_utils.make_sampler(
                temp=profile.temperature,
                top_p=profile.top_p,
            )
            try:
                prompt = self._tokenizer.apply_chat_template(
                    formatted_messages,
                    tools=formatted_tools,
                    add_generation_prompt=True,
                    tokenize=False,
                )
            except (TypeError, Exception):
                # Fallback if tokenizer template doesn't support tools kwarg directly
                tools_desc = "\n".join([f"- {t.name}: {t.description}" for t in tools])
                injected_messages = list(formatted_messages)
                injected_messages[0] = {
                    "role": "system",
                    "content": f"{injected_messages[0]['content']}\n\nAvailable tools:\n{tools_desc}\n\nTo call a tool, respond with:\n<tool_call>\n{{\"name\": \"<tool_name>\", \"arguments\": {{...}}}}\n</tool_call>",
                }
                prompt = self._tokenizer.apply_chat_template(
                    injected_messages,
                    add_generation_prompt=True,
                    tokenize=False,
                )

            return mlx_lm.generate(
                self._model,
                self._tokenizer,
                prompt=prompt,
                max_tokens=profile.max_tokens,
                sampler=sampler,
                verbose=False,
            )

        try:
            async with self._inference_lock:
                raw_text = await asyncio.to_thread(_run_tool_inference)
            clean_content, tool_calls = self._parse_tool_calls(raw_text)

            return LLMResponse(
                content=clean_content or (None if tool_calls else ""),
                tool_calls=tool_calls,
                finish_reason="tool_calls" if tool_calls else "stop",
                model=self._model_id,
            )
        except ToolCallParseError:
            # Let ToolCallParseError propagate to runtime for retry/repair
            raise
        except Exception as exc:
            logger.error("mlx_tool_generation_failed", error=str(exc))
            raise LLMError(f"Tool generation error: {exc}") from exc
