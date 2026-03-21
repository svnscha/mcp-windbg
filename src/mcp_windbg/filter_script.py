from __future__ import annotations

import importlib.util
import inspect
import logging
from pathlib import Path
from typing import Any, Callable

from mcp.types import TextContent

logger = logging.getLogger(__name__)

ResolvedCallback = Callable[[str, dict[str, Any]], str | None]


def load_filter_script(script_path: str) -> "FilterScript":
    path = Path(script_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Filter script not found: {path}")

    module_name = f"mcp_windbg_filter_{path.stem}_{abs(hash(str(path)))}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load filter script: {path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    process_input = _resolve_callback(module, "process_input")
    process_output = _resolve_callback(module, "process_output")

    if process_input is None and process_output is None:
        raise ValueError(
            "Filter script must define process_input(text[, context]) and/or "
            "process_output(text[, context])"
        )

    logger.info("Loaded tool content filter script from %s", path)

    return FilterScript(
        path=path,
        process_input_callback=process_input,
        process_output_callback=process_output,
    )


class FilterScript:
    def __init__(
        self,
        path: Path,
        process_input_callback: ResolvedCallback | None,
        process_output_callback: ResolvedCallback | None,
    ) -> None:
        self.path = path
        self._process_input_callback = process_input_callback
        self._process_output_callback = process_output_callback

    def process_input(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None,
        transport: str,
        call_id: str,
    ) -> dict[str, Any] | None:
        if self._process_input_callback is None:
            return arguments

        if arguments is None:
            return None

        return self._transform_value(
            value=arguments,
            callback=self._process_input_callback,
            context={
                "hook": "input",
                "tool_name": tool_name,
                "transport": transport,
                "call_id": call_id,
            },
            path="$",
        )

    def process_output(
        self,
        tool_name: str,
        content: list[Any],
        transport: str,
        call_id: str,
    ) -> list[Any]:
        if self._process_output_callback is None:
            return content

        transformed_content: list[Any] = []
        for index, item in enumerate(content):
            if isinstance(item, TextContent):
                transformed_content.append(
                    item.model_copy(
                        update={
                            "text": self._apply_callback(
                                text=item.text,
                                callback=self._process_output_callback,
                                context={
                                    "hook": "output",
                                    "tool_name": tool_name,
                                    "transport": transport,
                                    "call_id": call_id,
                                    "content_index": index,
                                },
                            )
                        }
                    )
                )
                continue

            transformed_content.append(item)

        return transformed_content

    def _apply_callback(
        self,
        text: str,
        callback: ResolvedCallback,
        context: dict[str, Any],
    ) -> str:
        try:
            transformed_text = callback(text, context)
        except Exception as exc:
            raise RuntimeError(
                f"Filter script {self.path} failed in {context['hook']} hook: {exc}"
            ) from exc

        if transformed_text is None:
            return text

        if not isinstance(transformed_text, str):
            raise TypeError(
                f"Filter script {self.path} {context['hook']} hook must return a string or None"
            )

        return transformed_text

    def _transform_value(
        self,
        value: Any,
        callback: ResolvedCallback,
        context: dict[str, Any],
        path: str,
    ) -> Any:
        if isinstance(value, str):
            next_context = dict(context)
            next_context["argument_path"] = path
            return self._apply_callback(value, callback, next_context)

        if isinstance(value, list):
            return [
                self._transform_value(
                    value=item,
                    callback=callback,
                    context=context,
                    path=f"{path}[{index}]",
                )
                for index, item in enumerate(value)
            ]

        if isinstance(value, dict):
            return {
                key: self._transform_value(
                    value=item,
                    callback=callback,
                    context=context,
                    path=f"{path}.{key}",
                )
                for key, item in value.items()
            }

        return value


def _resolve_callback(module: Any, callback_name: str) -> ResolvedCallback | None:
    callback = getattr(module, callback_name, None)
    if callback is None:
        return None
    if not callable(callback):
        raise TypeError(f"{callback_name} in filter script must be callable")

    signature = inspect.signature(callback)
    positional_parameters = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    has_varargs = any(
        parameter.kind == inspect.Parameter.VAR_POSITIONAL
        for parameter in signature.parameters.values()
    )

    if len(positional_parameters) == 1 and not has_varargs:
        return lambda text, context: callback(text)

    if len(positional_parameters) >= 2 or has_varargs:
        return lambda text, context: callback(text, context)

    raise TypeError(
        f"{callback_name} in filter script must accept text or text and context"
    )
