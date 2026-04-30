"""Voice mode mixin for HermesCLI (phase 39b chunk 1).

Originally at cli.py:6460-6863. The composing class must initialize:
    _voice_lock, _voice_mode, _voice_tts, _voice_recorder,
    _voice_recording, _voice_processing, _voice_continuous,
    _voice_tts_done, _attached_images, _pending_input
and optionally _app, _should_exit, _no_speech_count.

UI helpers (_cprint, _DIM, _RST, _ACCENT, _BOLD) are imported from cli
inside each method body to avoid the cli <-> voice_mixin circular import.
``_is_termux_environment`` comes directly from ``hermes_constants``.
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
import time

from hermes_constants import is_termux as _is_termux_environment

logger = logging.getLogger(__name__)


class VoiceMixin:
    def _voice_start_recording(self):
        """Start capturing audio from the microphone."""
        from cli import _cprint, _DIM, _RST, _ACCENT, _BOLD  # noqa: F401

        if getattr(self, '_should_exit', False):
            return
        from tools.voice_mode import create_audio_recorder, check_voice_requirements

        reqs = check_voice_requirements()
        if not reqs["audio_available"]:
            if _is_termux_environment():
                details = reqs.get("details", "")
                if "Termux:API Android app is not installed" in details:
                    raise RuntimeError(
                        "Termux:API command package detected, but the Android app is missing.\n"
                        "Install/update the Termux:API Android app, then retry /voice on.\n"
                        "Fallback: pkg install python-numpy portaudio && python -m pip install sounddevice"
                    )
                raise RuntimeError(
                    "Voice mode requires either Termux:API microphone access or Python audio libraries.\n"
                    "Option 1: pkg install termux-api and install the Termux:API Android app\n"
                    "Option 2: pkg install python-numpy portaudio && python -m pip install sounddevice"
                )
            raise RuntimeError(
                "Voice mode requires sounddevice and numpy.\n"
                "Install with: pip install sounddevice numpy\n"
                "Or: pip install hermes-agent[voice]"
            )
        if not reqs.get("stt_available", reqs.get("stt_key_set")):
            raise RuntimeError(
                "Voice mode requires an STT provider for transcription.\n"
                "Option 1: pip install faster-whisper  (free, local)\n"
                "Option 2: Set GROQ_API_KEY (free tier)\n"
                "Option 3: Set VOICE_TOOLS_OPENAI_KEY (paid)"
            )

        with self._voice_lock:
            if self._voice_recording:
                return
            self._voice_recording = True

        voice_cfg = {}
        try:
            from hermes_cli.config import load_config
            voice_cfg = load_config().get("voice", {})
        except Exception:
            pass

        if self._voice_recorder is None:
            self._voice_recorder = create_audio_recorder()

        self._voice_recorder._silence_threshold = voice_cfg.get("silence_threshold", 200)
        self._voice_recorder._silence_duration = voice_cfg.get("silence_duration", 3.0)

        def _on_silence():
            """Called by AudioRecorder when silence is detected after speech."""
            with self._voice_lock:
                if not self._voice_recording:
                    return
            _cprint(f"\n{_DIM}Silence detected, auto-stopping...{_RST}")
            if hasattr(self, '_app') and self._app:
                self._app.invalidate()
            self._voice_stop_and_transcribe()

        try:
            from tools.voice_mode import play_beep
            play_beep(frequency=880, count=1)
        except Exception:
            pass

        try:
            self._voice_recorder.start(on_silence_stop=_on_silence)
        except Exception:
            with self._voice_lock:
                self._voice_recording = False
            raise
        if getattr(self._voice_recorder, "supports_silence_autostop", True):
            _recording_hint = "auto-stops on silence | Ctrl+B to stop & exit continuous"
        elif _is_termux_environment():
            _recording_hint = "Termux:API capture | Ctrl+B to stop"
        else:
            _recording_hint = "Ctrl+B to stop"
        _cprint(f"\n{_ACCENT}● Recording...{_RST} {_DIM}({_recording_hint}){_RST}")

        def _refresh_level():
            while True:
                with self._voice_lock:
                    still_recording = self._voice_recording
                if not still_recording:
                    break
                if hasattr(self, '_app') and self._app:
                    self._app.invalidate()
                time.sleep(0.15)
        threading.Thread(target=_refresh_level, daemon=True).start()

    def _voice_stop_and_transcribe(self):
        """Stop recording, transcribe via STT, and queue the transcript as input."""
        from cli import _cprint, _DIM, _RST, _ACCENT, _BOLD  # noqa: F401

        with self._voice_lock:
            if not self._voice_recording:
                return
            self._voice_recording = False
            self._voice_processing = True

        submitted = False
        wav_path = None
        try:
            if self._voice_recorder is None:
                return

            wav_path = self._voice_recorder.stop()

            try:
                from tools.voice_mode import play_beep
                play_beep(frequency=660, count=2)
            except Exception:
                pass

            if wav_path is None:
                _cprint(f"{_DIM}No speech detected.{_RST}")
                return

            if hasattr(self, '_app') and self._app:
                self._app.invalidate()
            _cprint(f"{_DIM}Transcribing...{_RST}")

            stt_model = None
            try:
                from hermes_cli.config import load_config
                stt_config = load_config().get("stt", {})
                stt_model = stt_config.get("model")
            except Exception:
                pass

            from tools.voice_mode import transcribe_recording
            result = transcribe_recording(wav_path, model=stt_model)

            if result.get("success") and result.get("transcript", "").strip():
                transcript = result["transcript"].strip()
                self._attached_images.clear()
                if hasattr(self, '_app') and self._app:
                    self._app.invalidate()
                self._pending_input.put(transcript)
                submitted = True
            elif result.get("success"):
                _cprint(f"{_DIM}No speech detected.{_RST}")
            else:
                error = result.get("error", "Unknown error")
                _cprint(f"\n{_DIM}Transcription failed: {error}{_RST}")

        except Exception as e:
            _cprint(f"\n{_DIM}Voice processing error: {e}{_RST}")
        finally:
            with self._voice_lock:
                self._voice_processing = False
            if hasattr(self, '_app') and self._app:
                self._app.invalidate()
            try:
                if wav_path and os.path.isfile(wav_path):
                    os.unlink(wav_path)
            except Exception:
                pass

            if not submitted:
                self._no_speech_count = getattr(self, '_no_speech_count', 0) + 1
                if self._no_speech_count >= 3:
                    self._voice_continuous = False
                    self._no_speech_count = 0
                    _cprint(f"{_DIM}No speech detected 3 times, continuous mode stopped.{_RST}")
                    return
            else:
                self._no_speech_count = 0

            if self._voice_continuous and not submitted and not self._voice_recording:
                def _restart_recording():
                    try:
                        self._voice_start_recording()
                        if hasattr(self, '_app') and self._app:
                            self._app.invalidate()
                    except Exception as e:
                        _cprint(f"{_DIM}Voice auto-restart failed: {e}{_RST}")
                threading.Thread(target=_restart_recording, daemon=True).start()

    def _voice_speak_response(self, text: str):
        """Speak the agent's response aloud using TTS (runs in background thread)."""
        from cli import _cprint, _DIM, _RST, _ACCENT, _BOLD  # noqa: F401

        if not self._voice_tts:
            return
        self._voice_tts_done.clear()
        try:
            from tools.tts_tool import text_to_speech_tool
            from tools.voice_mode import play_audio_file
            import re

            tts_text = text[:4000] if len(text) > 4000 else text
            tts_text = re.sub(r'```[\s\S]*?```', ' ', tts_text)
            tts_text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', tts_text)
            tts_text = re.sub(r'https?://\S+', '', tts_text)
            tts_text = re.sub(r'\*\*(.+?)\*\*', r'\1', tts_text)
            tts_text = re.sub(r'\*(.+?)\*', r'\1', tts_text)
            tts_text = re.sub(r'`(.+?)`', r'\1', tts_text)
            tts_text = re.sub(r'^#+\s*', '', tts_text, flags=re.MULTILINE)
            tts_text = re.sub(r'^\s*[-*]\s+', '', tts_text, flags=re.MULTILINE)
            tts_text = re.sub(r'---+', '', tts_text)
            tts_text = re.sub(r'\n{3,}', '\n\n', tts_text)
            tts_text = tts_text.strip()
            if not tts_text:
                return

            os.makedirs(os.path.join(tempfile.gettempdir(), "hermes_voice"), exist_ok=True)
            mp3_path = os.path.join(
                tempfile.gettempdir(), "hermes_voice",
                f"tts_{time.strftime('%Y%m%d_%H%M%S')}.mp3",
            )

            text_to_speech_tool(text=tts_text, output_path=mp3_path)

            if os.path.isfile(mp3_path) and os.path.getsize(mp3_path) > 0:
                play_audio_file(mp3_path)
                try:
                    os.unlink(mp3_path)
                    ogg_path = mp3_path.rsplit(".", 1)[0] + ".ogg"
                    if os.path.isfile(ogg_path):
                        os.unlink(ogg_path)
                except OSError:
                    pass
        except Exception as e:
            logger.warning("Voice TTS playback failed: %s", e)
            _cprint(f"{_DIM}TTS playback failed: {e}{_RST}")
        finally:
            self._voice_tts_done.set()

    def _handle_voice_command(self, command: str):
        """Handle /voice [on|off|tts|status] command."""
        from cli import _cprint, _DIM, _RST, _ACCENT, _BOLD  # noqa: F401

        parts = command.strip().split(maxsplit=1)
        subcommand = parts[1].lower().strip() if len(parts) > 1 else ""

        if subcommand == "on":
            self._enable_voice_mode()
        elif subcommand == "off":
            self._disable_voice_mode()
        elif subcommand == "tts":
            self._toggle_voice_tts()
        elif subcommand == "status":
            self._show_voice_status()
        elif subcommand == "":
            if self._voice_mode:
                self._disable_voice_mode()
            else:
                self._enable_voice_mode()
        else:
            _cprint(f"Unknown voice subcommand: {subcommand}")
            _cprint("Usage: /voice [on|off|tts|status]")

    def _enable_voice_mode(self):
        """Enable voice mode after checking requirements."""
        from cli import _cprint, _DIM, _RST, _ACCENT, _BOLD  # noqa: F401

        if self._voice_mode:
            _cprint(f"{_DIM}Voice mode is already enabled.{_RST}")
            return

        from tools.voice_mode import check_voice_requirements, detect_audio_environment

        env_check = detect_audio_environment()
        if not env_check["available"]:
            _cprint(f"\n{_ACCENT}Voice mode unavailable in this environment:{_RST}")
            for warning in env_check["warnings"]:
                _cprint(f"  {_DIM}{warning}{_RST}")
            return

        reqs = check_voice_requirements()
        if not reqs["available"]:
            _cprint(f"\n{_ACCENT}Voice mode requirements not met:{_RST}")
            for line in reqs["details"].split("\n"):
                _cprint(f"  {_DIM}{line}{_RST}")
            if reqs["missing_packages"]:
                if _is_termux_environment():
                    _cprint(f"\n  {_BOLD}Option 1: pkg install termux-api{_RST}")
                    _cprint(f"  {_DIM}Then install/update the Termux:API Android app for microphone capture{_RST}")
                    _cprint(f"  {_BOLD}Option 2: pkg install python-numpy portaudio && python -m pip install sounddevice{_RST}")
                else:
                    _cprint(f"\n  {_BOLD}Install: pip install {' '.join(reqs['missing_packages'])}{_RST}")
                    _cprint(f"  {_DIM}Or: pip install hermes-agent[voice]{_RST}")
            return

        with self._voice_lock:
            self._voice_mode = True

        try:
            from hermes_cli.config import load_config
            voice_config = load_config().get("voice", {})
            if voice_config.get("auto_tts", False):
                with self._voice_lock:
                    self._voice_tts = True
        except Exception:
            pass

        tts_status = " (TTS enabled)" if self._voice_tts else ""
        try:
            from hermes_cli.config import load_config
            _raw_ptt = load_config().get("voice", {}).get("record_key", "ctrl+b")
            _ptt_key = _raw_ptt.lower().replace("ctrl+", "c-").replace("alt+", "a-")
        except Exception:
            _ptt_key = "c-b"
        _ptt_display = _ptt_key.replace("c-", "Ctrl+").upper()
        _cprint(f"\n{_ACCENT}Voice mode enabled{tts_status}{_RST}")
        _cprint(f"  {_DIM}{_ptt_display} to start/stop recording{_RST}")
        _cprint(f"  {_DIM}/voice tts  to toggle speech output{_RST}")
        _cprint(f"  {_DIM}/voice off  to disable voice mode{_RST}")

    def _disable_voice_mode(self):
        """Disable voice mode, cancel any active recording, and stop TTS."""
        from cli import _cprint, _DIM, _RST, _ACCENT, _BOLD  # noqa: F401

        recorder = None
        with self._voice_lock:
            if self._voice_recording and self._voice_recorder:
                self._voice_recorder.cancel()
                self._voice_recording = False
            recorder = self._voice_recorder
            self._voice_mode = False
            self._voice_tts = False
            self._voice_continuous = False

        if recorder is not None:
            def _bg_shutdown(rec=recorder):
                try:
                    rec.shutdown()
                except Exception:
                    pass
            threading.Thread(target=_bg_shutdown, daemon=True).start()
            self._voice_recorder = None

        try:
            from tools.voice_mode import stop_playback
            stop_playback()
        except Exception:
            pass
        self._voice_tts_done.set()

        _cprint(f"\n{_DIM}Voice mode disabled.{_RST}")

    def _toggle_voice_tts(self):
        """Toggle TTS output for voice mode."""
        from cli import _cprint, _DIM, _RST, _ACCENT, _BOLD  # noqa: F401

        if not self._voice_mode:
            _cprint(f"{_DIM}Enable voice mode first: /voice on{_RST}")
            return

        with self._voice_lock:
            self._voice_tts = not self._voice_tts
        status = "enabled" if self._voice_tts else "disabled"

        if self._voice_tts:
            from tools.tts_tool import check_tts_requirements
            if not check_tts_requirements():
                _cprint(f"{_DIM}Warning: No TTS provider available. Install edge-tts or set API keys.{_RST}")

        _cprint(f"{_ACCENT}Voice TTS {status}.{_RST}")

    def _show_voice_status(self):
        """Show current voice mode status."""
        from cli import _cprint, _DIM, _RST, _ACCENT, _BOLD  # noqa: F401
        from hermes_cli.config import load_config
        from tools.voice_mode import check_voice_requirements

        reqs = check_voice_requirements()

        _cprint(f"\n{_BOLD}Voice Mode Status{_RST}")
        _cprint(f"  Mode:      {'ON' if self._voice_mode else 'OFF'}")
        _cprint(f"  TTS:       {'ON' if self._voice_tts else 'OFF'}")
        _cprint(f"  Recording: {'YES' if self._voice_recording else 'no'}")
        _raw_key = load_config().get("voice", {}).get("record_key", "ctrl+b")
        _display_key = _raw_key.replace("ctrl+", "Ctrl+").upper() if "ctrl+" in _raw_key.lower() else _raw_key
        _cprint(f"  Record key: {_display_key}")
        _cprint(f"\n  {_BOLD}Requirements:{_RST}")
        for line in reqs["details"].split("\n"):
            _cprint(f"    {line}")
