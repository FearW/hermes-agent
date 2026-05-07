"""Streaming and API-call methods extracted from AIAgent as a reusable mixin."""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import uuid
import time
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

from agent.model_metadata import is_local_endpoint


class StreamingAPICallMixin:
    """Mixin for AIAgent: interruptible API calls and unified streaming delivery."""

    def _anthropic_messages_create(self, api_kwargs: dict):

        if self.api_mode == "anthropic_messages":

            self._try_refresh_anthropic_client_credentials()

        return self._anthropic_client.messages.create(**api_kwargs)



    def _interruptible_api_call(self, api_kwargs: dict):

        """

        Run the API call in a background thread so the main conversation loop

        can detect interrupts without waiting for the full HTTP round-trip.



        Each worker thread gets its own OpenAI client instance. Interrupts only

        close that worker-local client, so retries and other requests never

        inherit a closed transport.

        """

        result = {"response": None, "error": None}

        request_client_holder = {"client": None}



        def _call():

            try:

                if self.api_mode == "codex_responses":

                    request_client_holder["client"] = (

                        self._create_request_openai_client(

                            reason="codex_stream_request"

                        )

                    )

                    result["response"] = self._run_codex_stream(

                        api_kwargs,

                        client=request_client_holder["client"],

                        on_first_delta=getattr(self, "_codex_on_first_delta", None),

                    )

                elif self.api_mode == "anthropic_messages":

                    result["response"] = self._anthropic_messages_create(api_kwargs)

                else:

                    request_client_holder["client"] = (

                        self._create_request_openai_client(

                            reason="chat_completion_request"

                        )

                    )

                    result["response"] = request_client_holder[

                        "client"

                    ].chat.completions.create(**api_kwargs)

            except Exception as e:

                logger.debug("_interruptible_api_call failed", exc_info=True)

                result["error"] = e

            finally:

                request_client = request_client_holder.get("client")

                if request_client is not None:

                    self._close_request_openai_client(

                        request_client, reason="request_complete"

                    )



        t = threading.Thread(target=_call, daemon=True)

        t.start()

        while t.is_alive():

            t.join(timeout=0.3)

            if self._interrupt_requested:

                # Force-close the in-flight worker-local HTTP connection to stop

                # token generation without poisoning the shared client used to

                # seed future retries.

                try:

                    if self.api_mode == "anthropic_messages":

                        from agent.anthropic_adapter import build_anthropic_client



                        self._anthropic_client.close()

                        self._anthropic_client = build_anthropic_client(

                            self._anthropic_api_key,

                            getattr(self, "_anthropic_base_url", None),

                        )

                    else:

                        request_client = request_client_holder.get("client")

                        if request_client is not None:

                            self._close_request_openai_client(

                                request_client, reason="interrupt_abort"

                            )

                except Exception:

                    logger.debug("_interruptible_api_call close on interrupt failed", exc_info=True)

                raise InterruptedError("Agent interrupted during API call")

        if result["error"] is not None:

            raise result["error"]

        return result["response"]



    # ── Unified streaming API call ─────────────────────────────────────────



    def _reset_stream_delivery_tracking(self) -> None:

        """Reset tracking for text delivered during the current model response."""

        self._current_streamed_assistant_text = ""



    def _record_streamed_assistant_text(self, text: str) -> None:

        """Accumulate visible assistant text emitted through stream callbacks."""

        if isinstance(text, str) and text:

            self._current_streamed_assistant_text = (

                getattr(self, "_current_streamed_assistant_text", "") + text

            )



    @staticmethod

    def _normalize_interim_visible_text(text: str) -> str:

        if not isinstance(text, str):

            return ""

        return re.sub(r"\s+", " ", text).strip()



    def _interim_content_was_streamed(self, content: str) -> bool:

        visible_content = self._normalize_interim_visible_text(

            self._strip_think_blocks(content or "")

        )

        if not visible_content:

            return False

        streamed = self._normalize_interim_visible_text(

            self._strip_think_blocks(

                getattr(self, "_current_streamed_assistant_text", "") or ""

            )

        )

        return bool(streamed) and streamed == visible_content



    def _emit_interim_assistant_message(self, assistant_msg: Dict[str, Any]) -> None:

        """Surface a real mid-turn assistant commentary message to the UI layer."""

        cb = getattr(self, "interim_assistant_callback", None)

        if cb is None or not isinstance(assistant_msg, dict):

            return

        content = assistant_msg.get("content")

        visible = self._strip_think_blocks(content or "").strip()

        if not visible or visible == "(empty)":

            return

        already_streamed = self._interim_content_was_streamed(visible)

        try:

            cb(visible, already_streamed=already_streamed)

        except Exception:

            logger.debug("interim_assistant_callback error", exc_info=True)



    def _fire_stream_delta(self, text: str) -> None:

        """Fire all registered stream delta callbacks (display + TTS)."""

        # If a tool iteration set the break flag, prepend a single paragraph

        # break before the first real text delta.  This prevents the original

        # problem (text concatenation across tool boundaries) without stacking

        # blank lines when multiple tool iterations run back-to-back.

        if getattr(self, "_stream_needs_break", False) and text and text.strip():

            self._stream_needs_break = False

            text = "\n\n" + text

        callbacks = [

            cb

            for cb in (self.stream_delta_callback, self._stream_callback)

            if cb is not None

        ]

        delivered = False

        for cb in callbacks:

            try:

                cb(text)

                delivered = True

            except Exception:

                logger.debug("_fire_stream_delta callback failed", exc_info=True)

        if delivered:

            self._record_streamed_assistant_text(text)



    def _fire_reasoning_delta(self, text: str) -> None:

        """Fire reasoning callback if registered."""

        cb = self.reasoning_callback

        if cb is not None:

            try:

                cb(text)

            except Exception:

                logger.debug("_fire_reasoning_delta callback failed", exc_info=True)



    def _fire_tool_gen_started(self, tool_name: str) -> None:

        """Notify display layer that the model is generating tool call arguments.



        Fires once per tool name when the streaming response begins producing

        tool_call / tool_use tokens.  Gives the TUI a chance to show a spinner

        or status line so the user isn't staring at a frozen screen while a

        large tool payload (e.g. a 45 KB write_file) is being generated.

        """

        cb = self.tool_gen_callback

        if cb is not None:

            try:

                cb(tool_name)

            except Exception:

                logger.debug("_fire_tool_gen_started callback failed", exc_info=True)



    def _has_stream_consumers(self) -> bool:

        """Return True if any streaming consumer is registered."""

        return (

            self.stream_delta_callback is not None

            or getattr(self, "_stream_callback", None) is not None

        )



    def _interruptible_streaming_api_call(

        self, api_kwargs: dict, *, on_first_delta: callable = None

    ):

        """Streaming variant of _interruptible_api_call for real-time token delivery.



        Handles all three api_modes:

        - chat_completions: stream=True on OpenAI-compatible endpoints

        - anthropic_messages: client.messages.stream() via Anthropic SDK

        - codex_responses: delegates to _run_codex_stream (already streaming)



        Fires stream_delta_callback and _stream_callback for each text token.

        Tool-call turns suppress the callback — only text-only final responses

        stream to the consumer.  Returns a SimpleNamespace that mimics the

        non-streaming response shape so the rest of the agent loop is unchanged.



        Falls back to _interruptible_api_call on provider errors indicating

        streaming is not supported.

        """

        if self.api_mode == "codex_responses":

            # Codex streams internally via _run_codex_stream. The main dispatch

            # in _interruptible_api_call already calls it; we just need to

            # ensure on_first_delta reaches it. Store it on the instance

            # temporarily so _run_codex_stream can pick it up.

            self._codex_on_first_delta = on_first_delta

            try:

                return self._interruptible_api_call(api_kwargs)

            finally:

                self._codex_on_first_delta = None



        result = {"response": None, "error": None}

        request_client_holder = {"client": None}

        first_delta_fired = {"done": False}

        deltas_were_sent = {

            "yes": False

        }  # Track if any deltas were fired (for fallback)

        # Wall-clock timestamp of the last real streaming chunk.  The outer

        # poll loop uses this to detect stale connections that keep receiving

        # SSE keep-alive pings but no actual data.

        last_chunk_time = {"t": time.time()}



        def _fire_first_delta():

            if not first_delta_fired["done"] and on_first_delta:

                first_delta_fired["done"] = True

                try:

                    on_first_delta()

                except Exception:

                    logger.debug("on_first_delta callback failed", exc_info=True)



        def _call_chat_completions():

            """Stream a chat completions response."""

            import httpx as _httpx



            _base_timeout = float(os.getenv("HERMES_API_TIMEOUT", 1800.0))

            _stream_read_timeout = float(os.getenv("HERMES_STREAM_READ_TIMEOUT", 120.0))

            # Local providers (Ollama, llama.cpp, vLLM) can take minutes for

            # prefill on large contexts before producing the first token.

            # Auto-increase the httpx read timeout unless the user explicitly

            # overrode HERMES_STREAM_READ_TIMEOUT.

            if (

                _stream_read_timeout == 120.0

                and self.base_url

                and is_local_endpoint(self.base_url)

            ):

                _stream_read_timeout = _base_timeout

                logger.debug(

                    "Local provider detected (%s) — stream read timeout raised to %.0fs",

                    self.base_url,

                    _stream_read_timeout,

                )

            stream_kwargs = {

                **api_kwargs,

                "stream": True,

                "stream_options": {"include_usage": True},

                "timeout": _httpx.Timeout(

                    connect=30.0,

                    read=_stream_read_timeout,

                    write=_base_timeout,

                    pool=30.0,

                ),

            }

            request_client_holder["client"] = self._create_request_openai_client(

                reason="chat_completion_stream_request"

            )

            # Reset stale-stream timer so the detector measures from this

            # attempt's start, not a previous attempt's last chunk.

            last_chunk_time["t"] = time.time()

            self._touch_activity("waiting for provider response (streaming)")

            stream = request_client_holder["client"].chat.completions.create(

                **stream_kwargs

            )



            # Capture rate limit headers from the initial HTTP response.

            # The OpenAI SDK Stream object exposes the underlying httpx

            # response via .response before any chunks are consumed.

            self._capture_rate_limits(getattr(stream, "response", None))



            content_parts: list = []

            tool_calls_acc: dict = {}

            tool_gen_notified: set = set()

            # Ollama-compatible endpoints reuse index 0 for every tool call

            # in a parallel batch, distinguishing them only by id.  Track

            # the last seen id per raw index so we can detect a new tool

            # call starting at the same index and redirect it to a fresh slot.

            _last_id_at_idx: dict = {}  # raw_index -> last seen non-empty id

            _active_slot_by_idx: dict = {}  # raw_index -> current slot in tool_calls_acc

            finish_reason = None

            model_name = None

            role = "assistant"

            reasoning_parts: list = []

            usage_obj = None

            _first_chunk_seen = False

            for chunk in stream:

                last_chunk_time["t"] = time.time()

                if not _first_chunk_seen:

                    _first_chunk_seen = True

                    self._touch_activity("receiving stream response")



                if self._interrupt_requested:

                    break



                if not chunk.choices:

                    if hasattr(chunk, "model") and chunk.model:

                        model_name = chunk.model

                    # Usage comes in the final chunk with empty choices

                    if hasattr(chunk, "usage") and chunk.usage:

                        usage_obj = chunk.usage

                    continue



                delta = chunk.choices[0].delta

                if hasattr(chunk, "model") and chunk.model:

                    model_name = chunk.model



                # Accumulate reasoning content

                reasoning_text = getattr(delta, "reasoning_content", None) or getattr(

                    delta, "reasoning", None

                )

                if reasoning_text:

                    reasoning_parts.append(reasoning_text)

                    _fire_first_delta()

                    self._fire_reasoning_delta(reasoning_text)



                # Accumulate text content — fire callback only when no tool calls

                if delta and delta.content:

                    content_parts.append(delta.content)

                    if not tool_calls_acc:

                        _fire_first_delta()

                        self._fire_stream_delta(delta.content)

                        deltas_were_sent["yes"] = True

                    else:

                        # Tool calls suppress regular content streaming (avoids

                        # displaying chatty "I'll use the tool..." text alongside

                        # tool calls).  But reasoning tags embedded in suppressed

                        # content should still reach the display — otherwise the

                        # reasoning box only appears as a post-response fallback,

                        # rendering it confusingly after the already-streamed

                        # response.  Route suppressed content through the stream

                        # delta callback so its tag extraction can fire the

                        # reasoning display.  Non-reasoning text is harmlessly

                        # suppressed by the CLI's _stream_delta when the stream

                        # box is already closed (tool boundary flush).

                        if self.stream_delta_callback:

                            try:

                                self.stream_delta_callback(delta.content)

                                self._record_streamed_assistant_text(delta.content)

                            except Exception:

                                logger.debug("stream_delta_callback failed", exc_info=True)



                # Accumulate tool call deltas — notify display on first name

                if delta and delta.tool_calls:

                    for tc_delta in delta.tool_calls:

                        raw_idx = tc_delta.index if tc_delta.index is not None else 0

                        delta_id = tc_delta.id or ""



                        # Ollama fix: detect a new tool call reusing the same

                        # raw index (different id) and redirect to a fresh slot.

                        if raw_idx not in _active_slot_by_idx:

                            _active_slot_by_idx[raw_idx] = raw_idx

                        if (

                            delta_id

                            and raw_idx in _last_id_at_idx

                            and delta_id != _last_id_at_idx[raw_idx]

                        ):

                            new_slot = max(tool_calls_acc, default=-1) + 1

                            _active_slot_by_idx[raw_idx] = new_slot

                        if delta_id:

                            _last_id_at_idx[raw_idx] = delta_id

                        idx = _active_slot_by_idx[raw_idx]



                        if idx not in tool_calls_acc:

                            tool_calls_acc[idx] = {

                                "id": tc_delta.id or "",

                                "type": "function",

                                "function": {"name": "", "arguments": ""},

                                "extra_content": None,

                            }

                        entry = tool_calls_acc[idx]

                        if tc_delta.id:

                            entry["id"] = tc_delta.id

                        if tc_delta.function:

                            if tc_delta.function.name:

                                entry["function"]["name"] += tc_delta.function.name

                            if tc_delta.function.arguments:

                                entry["function"]["arguments"] += (

                                    tc_delta.function.arguments

                                )

                        extra = getattr(tc_delta, "extra_content", None)

                        if extra is None and hasattr(tc_delta, "model_extra"):

                            extra = (tc_delta.model_extra or {}).get("extra_content")

                        if extra is not None:

                            if hasattr(extra, "model_dump"):

                                extra = extra.model_dump()

                            if isinstance(extra, dict) and isinstance(entry.get("extra_content"), dict):

                                merged_extra = dict(entry["extra_content"])

                                merged_extra.update(extra)

                                entry["extra_content"] = merged_extra

                            else:

                                entry["extra_content"] = extra

                        # Fire once per tool when the full name is available

                        name = entry["function"]["name"]

                        if name and idx not in tool_gen_notified:

                            tool_gen_notified.add(idx)

                            _fire_first_delta()

                            self._fire_tool_gen_started(name)



                if chunk.choices[0].finish_reason:

                    finish_reason = chunk.choices[0].finish_reason



                # Usage in the final chunk

                if hasattr(chunk, "usage") and chunk.usage:

                    usage_obj = chunk.usage



            # Build mock response matching non-streaming shape

            full_content = "".join(content_parts) or None

            mock_tool_calls = None

            has_truncated_tool_args = False

            if tool_calls_acc:

                mock_tool_calls = []

                for idx in sorted(tool_calls_acc):

                    tc = tool_calls_acc[idx]

                    arguments = tc["function"]["arguments"]

                    if arguments and arguments.strip():

                        try:

                            json.loads(arguments)

                        except json.JSONDecodeError:

                            has_truncated_tool_args = True

                    mock_tool_calls.append(

                        SimpleNamespace(

                            id=tc["id"],

                            type=tc["type"],

                            extra_content=tc.get("extra_content"),

                            function=SimpleNamespace(

                                name=tc["function"]["name"],

                                arguments=arguments,

                            ),

                        )

                    )



            effective_finish_reason = finish_reason or "stop"

            if has_truncated_tool_args:

                effective_finish_reason = "length"



            full_reasoning = "".join(reasoning_parts) or None

            mock_message = SimpleNamespace(

                role=role,

                content=full_content,

                tool_calls=mock_tool_calls,

                reasoning_content=full_reasoning,

            )

            mock_choice = SimpleNamespace(

                index=0,

                message=mock_message,

                finish_reason=effective_finish_reason,

            )

            return SimpleNamespace(

                id="stream-" + str(uuid.uuid4()),

                model=model_name,

                choices=[mock_choice],

                usage=usage_obj,

            )



        def _call_anthropic():

            """Stream an Anthropic Messages API response.



            Fires delta callbacks for real-time token delivery, but returns

            the native Anthropic Message object from get_final_message() so

            the rest of the agent loop (validation, tool extraction, etc.)

            works unchanged.

            """

            has_tool_use = False



            # Reset stale-stream timer for this attempt

            last_chunk_time["t"] = time.time()

            # Use the Anthropic SDK's streaming context manager

            with self._anthropic_client.messages.stream(**api_kwargs) as stream:

                for event in stream:

                    # Update stale-stream timer on every event so the

                    # outer poll loop knows data is flowing.  Without

                    # this, the detector kills healthy long-running

                    # Opus streams after 180 s even when events are

                    # actively arriving (the chat_completions path

                    # already does this at the top of its chunk loop).

                    last_chunk_time["t"] = time.time()



                    if self._interrupt_requested:

                        break



                    event_type = getattr(event, "type", None)



                    if event_type == "content_block_start":

                        block = getattr(event, "content_block", None)

                        if block and getattr(block, "type", None) == "tool_use":

                            has_tool_use = True

                            tool_name = getattr(block, "name", None)

                            if tool_name:

                                _fire_first_delta()

                                self._fire_tool_gen_started(tool_name)



                    elif event_type == "content_block_delta":

                        delta = getattr(event, "delta", None)

                        if delta:

                            delta_type = getattr(delta, "type", None)

                            if delta_type == "text_delta":

                                text = getattr(delta, "text", "")

                                if text and not has_tool_use:

                                    _fire_first_delta()

                                    self._fire_stream_delta(text)

                                    deltas_were_sent["yes"] = True

                            elif delta_type == "thinking_delta":

                                thinking_text = getattr(delta, "thinking", "")

                                if thinking_text:

                                    _fire_first_delta()

                                    self._fire_reasoning_delta(thinking_text)



                # Return the native Anthropic Message for downstream processing

                return stream.get_final_message()



        def _call():

            import httpx as _httpx



            _max_stream_retries = int(os.getenv("HERMES_STREAM_RETRIES", 2))



            try:

                for _stream_attempt in range(_max_stream_retries + 1):

                    try:

                        if self.api_mode == "anthropic_messages":

                            self._try_refresh_anthropic_client_credentials()

                            result["response"] = _call_anthropic()

                        else:

                            result["response"] = _call_chat_completions()

                        return  # success

                    except Exception as e:

                        if deltas_were_sent["yes"]:

                            # Streaming failed AFTER some tokens were already

                            # delivered.  Don't retry or fall back — partial

                            # content already reached the user.

                            logger.warning(

                                "Streaming failed after partial delivery, not retrying: %s",

                                e,

                            )

                            result["error"] = e

                            return



                        _is_timeout = isinstance(

                            e,

                            (

                                _httpx.ReadTimeout,

                                _httpx.ConnectTimeout,

                                _httpx.PoolTimeout,

                            ),

                        )

                        _is_conn_err = isinstance(

                            e,

                            (

                                _httpx.ConnectError,

                                _httpx.RemoteProtocolError,

                                ConnectionError,

                            ),

                        )



                        # SSE error events from proxies (e.g. OpenRouter sends

                        # {"error":{"message":"Network connection lost."}}) are

                        # raised as APIError by the OpenAI SDK.  These are

                        # semantically identical to httpx connection drops —

                        # the upstream stream died — and should be retried with

                        # a fresh connection.  Distinguish from HTTP errors:

                        # APIError from SSE has no status_code, while

                        # APIStatusError (4xx/5xx) always has one.

                        _is_sse_conn_err = False

                        if not _is_timeout and not _is_conn_err:

                            from openai import APIError as _APIError



                            if isinstance(e, _APIError) and not getattr(

                                e, "status_code", None

                            ):

                                _err_lower_sse = str(e).lower()

                                _SSE_CONN_PHRASES = (

                                    "connection lost",

                                    "connection reset",

                                    "connection closed",

                                    "connection terminated",

                                    "network error",

                                    "network connection",

                                    "terminated",

                                    "peer closed",

                                    "broken pipe",

                                    "upstream connect error",

                                )

                                _is_sse_conn_err = any(

                                    phrase in _err_lower_sse

                                    for phrase in _SSE_CONN_PHRASES

                                )



                        if _is_timeout or _is_conn_err or _is_sse_conn_err:

                            # Transient network / timeout error. Retry the

                            # streaming request with a fresh connection first.

                            if _stream_attempt < _max_stream_retries:

                                logger.info(

                                    "Streaming attempt %s/%s failed (%s: %s), "

                                    "retrying with fresh connection...",

                                    _stream_attempt + 1,

                                    _max_stream_retries + 1,

                                    type(e).__name__,

                                    e,

                                )

                                self._emit_status(

                                    f"⚠️ Connection to provider dropped "

                                    f"({type(e).__name__}). Reconnecting… "

                                    f"(attempt {_stream_attempt + 2}/{_max_stream_retries + 1})"

                                )

                                # Close the stale request client before retry

                                stale = request_client_holder.get("client")

                                if stale is not None:

                                    self._close_request_openai_client(

                                        stale, reason="stream_retry_cleanup"

                                    )

                                    request_client_holder["client"] = None

                                # Also rebuild the primary client to purge

                                # any dead connections from the pool.

                                try:

                                    self._replace_primary_openai_client(

                                        reason="stream_retry_pool_cleanup"

                                    )

                                except Exception:

                                    logger.debug("_replace_primary_openai_client failed on retry", exc_info=True)

                                continue

                            self._emit_status(

                                "❌ Connection to provider failed after "

                                f"{_max_stream_retries + 1} attempts. "

                                "The provider may be experiencing issues — "

                                "try again in a moment."

                            )

                            logger.warning(

                                "Streaming exhausted %s retries on transient error, "

                                "falling back to non-streaming: %s",

                                _max_stream_retries + 1,

                                e,

                            )

                        else:

                            _err_lower = str(e).lower()

                            _is_stream_unsupported = (

                                "stream" in _err_lower and "not supported" in _err_lower

                            )

                            if _is_stream_unsupported:

                                self._safe_print(

                                    "\n⚠  Streaming is not supported for this "

                                    "model/provider. Falling back to non-streaming.\n"

                                    "   To avoid this delay, set display.streaming: false "

                                    "in config.yaml\n"

                                )

                            logger.info(

                                "Streaming failed before delivery, falling back to non-streaming: %s",

                                e,

                            )



                        try:

                            # Reset stale timer — the non-streaming fallback

                            # uses its own client; prevent the stale detector

                            # from firing on stale timestamps from failed streams.

                            last_chunk_time["t"] = time.time()

                            result["response"] = self._interruptible_api_call(

                                api_kwargs

                            )

                        except Exception as fallback_err:

                            logger.debug("fallback non-streaming call failed", exc_info=True)

                            result["error"] = fallback_err

                        return

            finally:

                request_client = request_client_holder.get("client")

                if request_client is not None:

                    self._close_request_openai_client(

                        request_client, reason="stream_request_complete"

                    )



        _stream_stale_timeout_base = float(

            os.getenv("HERMES_STREAM_STALE_TIMEOUT", 180.0)

        )

        # Local providers (Ollama, oMLX, llama-cpp) can take 300+ seconds

        # for prefill on large contexts.  Disable the stale detector unless

        # the user explicitly set HERMES_STREAM_STALE_TIMEOUT.

        if (

            _stream_stale_timeout_base == 180.0

            and self.base_url

            and is_local_endpoint(self.base_url)

        ):

            _stream_stale_timeout = float("inf")

            logger.debug(

                "Local provider detected (%s) — stale stream timeout disabled",

                self.base_url,

            )

        else:

            # Scale the stale timeout for large contexts: slow models (like Opus)

            # can legitimately think for minutes before producing the first token

            # when the context is large.  Without this, the stale detector kills

            # healthy connections during the model's thinking phase, producing

            # spurious RemoteProtocolError ("peer closed connection").

            _est_tokens = sum(len(str(v)) for v in api_kwargs.get("messages", [])) // 4

            if _est_tokens > 100_000:

                _stream_stale_timeout = max(_stream_stale_timeout_base, 300.0)

            elif _est_tokens > 50_000:

                _stream_stale_timeout = max(_stream_stale_timeout_base, 240.0)

            else:

                _stream_stale_timeout = _stream_stale_timeout_base



        t = threading.Thread(target=_call, daemon=True)

        t.start()

        while t.is_alive():

            t.join(timeout=0.3)



            # Detect stale streams: connections kept alive by SSE pings

            # but delivering no real chunks.  Kill the client so the

            # inner retry loop can start a fresh connection.

            _stale_elapsed = time.time() - last_chunk_time["t"]

            if _stale_elapsed > _stream_stale_timeout:

                _est_ctx = sum(len(str(v)) for v in api_kwargs.get("messages", [])) // 4

                logger.warning(

                    "Stream stale for %.0fs (threshold %.0fs) — no chunks received. "

                    "model=%s context=~%s tokens. Killing connection.",

                    _stale_elapsed,

                    _stream_stale_timeout,

                    api_kwargs.get("model", "unknown"),

                    f"{_est_ctx:,}",

                )

                self._emit_status(

                    f"⚠️ No response from provider for {int(_stale_elapsed)}s "

                    f"(model: {api_kwargs.get('model', 'unknown')}, "

                    f"context: ~{_est_ctx:,} tokens). "

                    f"Reconnecting..."

                )

                try:

                    rc = request_client_holder.get("client")

                    if rc is not None:

                        self._close_request_openai_client(

                            rc, reason="stale_stream_kill"

                        )

                except Exception:

                    logger.debug("stale_stream_kill close failed", exc_info=True)

                # Rebuild the primary client too — its connection pool

                # may hold dead sockets from the same provider outage.

                try:

                    self._replace_primary_openai_client(

                        reason="stale_stream_pool_cleanup"

                    )

                except Exception:

                    logger.debug("stale_stream_pool_cleanup failed", exc_info=True)

                # Reset the timer so we don't kill repeatedly while

                # the inner thread processes the closure.

                last_chunk_time["t"] = time.time()



            if self._interrupt_requested:

                try:

                    if self.api_mode == "anthropic_messages":

                        from agent.anthropic_adapter import build_anthropic_client



                        self._anthropic_client.close()

                        self._anthropic_client = build_anthropic_client(

                            self._anthropic_api_key,

                            getattr(self, "_anthropic_base_url", None),

                        )

                    else:

                        request_client = request_client_holder.get("client")

                        if request_client is not None:

                            self._close_request_openai_client(

                                request_client, reason="stream_interrupt_abort"

                            )

                except Exception:

                    logger.debug("_interruptible_streaming_api_call close on interrupt failed", exc_info=True)

                raise InterruptedError("Agent interrupted during streaming API call")

        if result["error"] is not None:

            if deltas_were_sent["yes"]:

                # Streaming failed AFTER some tokens were already delivered to

                # the platform.  Re-raising would let the outer retry loop make

                # a new API call, creating a duplicate message.  Return a

                # partial "stop" response instead so the outer loop treats this

                # turn as complete (no retry, no fallback).

                logger.warning(

                    "Partial stream delivered before error; returning stub "

                    "response to prevent duplicate messages: %s",

                    result["error"],

                )

                _stub_msg = SimpleNamespace(

                    role="assistant",

                    content=None,

                    tool_calls=None,

                    reasoning_content=None,

                )

                return SimpleNamespace(

                    id="partial-stream-stub",

                    model=getattr(self, "model", "unknown"),

                    choices=[

                        SimpleNamespace(

                            index=0,

                            message=_stub_msg,

                            finish_reason="stop",

                        )

                    ],

                    usage=None,

                )

            raise result["error"]

        return result["response"]



    # ── Provider fallback ──────────────────────────────────────────────────



