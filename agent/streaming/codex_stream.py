"""Codex Responses API streaming extracted from AIAgent as a reusable mixin."""
from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

logger = logging.getLogger(__name__)


class CodexStreamMixin:
    """Mixin for AIAgent: streams Codex Responses API and provides create-stream fallback."""

    def _run_codex_stream(
        self, api_kwargs: dict, client: Any = None, on_first_delta: callable = None
    ):
        """Execute one streaming Responses API request and return the final response."""
        import httpx as _httpx

        active_client = client or self._ensure_primary_openai_client(
            reason="codex_stream_direct"
        )
        max_stream_retries = 1
        has_tool_calls = False
        first_delta_fired = False
        # Accumulate streamed text so we can recover if get_final_response()
        # returns empty output (e.g. chatgpt.com backend-api sends
        # response.incomplete instead of response.completed).
        self._codex_streamed_text_parts: list = []
        for attempt in range(max_stream_retries + 1):
            collected_output_items: list = []
            try:
                with active_client.responses.stream(**api_kwargs) as stream:
                    for event in stream:
                        if self._interrupt_requested:
                            break
                        event_type = getattr(event, "type", "")
                        # Fire callbacks on text content deltas (suppress during tool calls)
                        if (
                            "output_text.delta" in event_type
                            or event_type == "response.output_text.delta"
                        ):
                            delta_text = getattr(event, "delta", "")
                            if delta_text:
                                self._codex_streamed_text_parts.append(delta_text)
                            if delta_text and not has_tool_calls:
                                if not first_delta_fired:
                                    first_delta_fired = True
                                    if on_first_delta:
                                        try:
                                            on_first_delta()
                                        except Exception:
                                            pass
                                self._fire_stream_delta(delta_text)
                        # Track tool calls to suppress text streaming
                        elif "function_call" in event_type:
                            has_tool_calls = True
                        # Fire reasoning callbacks
                        elif "reasoning" in event_type and "delta" in event_type:
                            reasoning_text = getattr(event, "delta", "")
                            if reasoning_text:
                                self._fire_reasoning_delta(reasoning_text)
                        # Collect completed output items — some backends
                        # (chatgpt.com/backend-api/codex) stream valid items
                        # via response.output_item.done but the SDK's
                        # get_final_response() returns an empty output list.
                        elif event_type == "response.output_item.done":
                            done_item = getattr(event, "item", None)
                            if done_item is not None:
                                collected_output_items.append(done_item)
                        # Log non-completed terminal events for diagnostics
                        elif event_type in ("response.incomplete", "response.failed"):
                            resp_obj = getattr(event, "response", None)
                            status = (
                                getattr(resp_obj, "status", None) if resp_obj else None
                            )
                            incomplete_details = (
                                getattr(resp_obj, "incomplete_details", None)
                                if resp_obj
                                else None
                            )
                            logger.warning(
                                "Codex Responses stream received terminal event %s "
                                "(status=%s, incomplete_details=%s, streamed_chars=%d). %s",
                                event_type,
                                status,
                                incomplete_details,
                                sum(len(p) for p in self._codex_streamed_text_parts),
                                self._client_log_context(),
                            )
                    final_response = stream.get_final_response()
                    # PATCH: ChatGPT Codex backend streams valid output items
                    # but get_final_response() can return an empty output list.
                    # Backfill from collected items or synthesize from deltas.
                    _out = getattr(final_response, "output", None)
                    if isinstance(_out, list) and not _out:
                        if collected_output_items:
                            final_response.output = list(collected_output_items)
                            logger.debug(
                                "Codex stream: backfilled %d output items from stream events",
                                len(collected_output_items),
                            )
                        elif self._codex_streamed_text_parts and not has_tool_calls:
                            assembled = "".join(self._codex_streamed_text_parts)
                            final_response.output = [
                                SimpleNamespace(
                                    type="message",
                                    role="assistant",
                                    status="completed",
                                    content=[
                                        SimpleNamespace(
                                            type="output_text", text=assembled
                                        )
                                    ],
                                )
                            ]
                            logger.debug(
                                "Codex stream: synthesized output from %d text deltas (%d chars)",
                                len(self._codex_streamed_text_parts),
                                len(assembled),
                            )
                    return final_response
            except (
                _httpx.RemoteProtocolError,
                _httpx.ReadTimeout,
                _httpx.ConnectError,
                ConnectionError,
            ) as exc:
                if attempt < max_stream_retries:
                    logger.debug(
                        "Codex Responses stream transport failed (attempt %s/%s); retrying. %s error=%s",
                        attempt + 1,
                        max_stream_retries + 1,
                        self._client_log_context(),
                        exc,
                    )
                    continue
                logger.debug(
                    "Codex Responses stream transport failed; falling back to create(stream=True). %s error=%s",
                    self._client_log_context(),
                    exc,
                )
                return self._run_codex_create_stream_fallback(
                    api_kwargs, client=active_client
                )
            except RuntimeError as exc:
                err_text = str(exc)
                missing_completed = "response.completed" in err_text
                if missing_completed and attempt < max_stream_retries:
                    logger.debug(
                        "Responses stream closed before completion (attempt %s/%s); retrying. %s",
                        attempt + 1,
                        max_stream_retries + 1,
                        self._client_log_context(),
                    )
                    continue
                if missing_completed:
                    logger.debug(
                        "Responses stream did not emit response.completed; falling back to create(stream=True). %s",
                        self._client_log_context(),
                    )
                    return self._run_codex_create_stream_fallback(
                        api_kwargs, client=active_client
                    )
                raise

    def _run_codex_create_stream_fallback(self, api_kwargs: dict, client: Any = None):
        """Fallback path for stream completion edge cases on Codex-style Responses backends."""
        active_client = client or self._ensure_primary_openai_client(
            reason="codex_create_stream_fallback"
        )
        fallback_kwargs = dict(api_kwargs)
        fallback_kwargs["stream"] = True
        fallback_kwargs = self._preflight_codex_api_kwargs(
            fallback_kwargs, allow_stream=True
        )
        stream_or_response = active_client.responses.create(**fallback_kwargs)

        # Compatibility shim for mocks or providers that still return a concrete response.
        if hasattr(stream_or_response, "output"):
            return stream_or_response
        if not hasattr(stream_or_response, "__iter__"):
            return stream_or_response

        terminal_response = None
        collected_output_items: list = []
        collected_text_deltas: list = []
        try:
            for event in stream_or_response:
                event_type = getattr(event, "type", None)
                if not event_type and isinstance(event, dict):
                    event_type = event.get("type")

                # Collect output items and text deltas for backfill
                if event_type == "response.output_item.done":
                    done_item = getattr(event, "item", None)
                    if done_item is None and isinstance(event, dict):
                        done_item = event.get("item")
                    if done_item is not None:
                        collected_output_items.append(done_item)
                elif event_type in ("response.output_text.delta",):
                    delta = getattr(event, "delta", "")
                    if not delta and isinstance(event, dict):
                        delta = event.get("delta", "")
                    if delta:
                        collected_text_deltas.append(delta)

                if event_type not in {
                    "response.completed",
                    "response.incomplete",
                    "response.failed",
                }:
                    continue

                terminal_response = getattr(event, "response", None)
                if terminal_response is None and isinstance(event, dict):
                    terminal_response = event.get("response")
                if terminal_response is not None:
                    # Backfill empty output from collected stream events
                    _out = getattr(terminal_response, "output", None)
                    if isinstance(_out, list) and not _out:
                        if collected_output_items:
                            terminal_response.output = list(collected_output_items)
                            logger.debug(
                                "Codex fallback stream: backfilled %d output items",
                                len(collected_output_items),
                            )
                        elif collected_text_deltas:
                            assembled = "".join(collected_text_deltas)
                            terminal_response.output = [
                                SimpleNamespace(
                                    type="message",
                                    role="assistant",
                                    status="completed",
                                    content=[
                                        SimpleNamespace(
                                            type="output_text", text=assembled
                                        )
                                    ],
                                )
                            ]
                            logger.debug(
                                "Codex fallback stream: synthesized from %d deltas (%d chars)",
                                len(collected_text_deltas),
                                len(assembled),
                            )
                    return terminal_response
        finally:
            close_fn = getattr(stream_or_response, "close", None)
            if callable(close_fn):
                try:
                    close_fn()
                except Exception:
                    pass

        if terminal_response is not None:
            return terminal_response
        raise RuntimeError(
            "Responses create(stream=True) fallback did not emit a terminal response."
        )

