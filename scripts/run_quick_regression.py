from __future__ import annotations

import subprocess
import sys


TEST_COMMANDS = [
    ["tests/agent/test_memory_provider.py", "-q"],
    ["tests/tools/test_delegate.py", "-q"],
    ["tests/tools/test_mcp_tool.py", "-k", "RunOnMCPLoopInterrupts or timeout_cancels_waiting_mcp_call", "-q"],
    ["tests/tools/test_process_registry.py", "-k", "NotificationConfiguration or close_stdin_allows_eof_driven_process_to_finish", "-q"],
    ["tests/test_model_tools.py", "-k", "coerce_number_does_not_convert_nonfinite_values or quiet_toolset_resolution_is_cached", "-q"],
    ["tests/gateway/test_session_store_optimizations.py", "-q"],
]


def main() -> int:
    python = sys.executable
    for args in TEST_COMMANDS:
        cmd = [python, "-m", "pytest", "-o", "addopts="] + args
        print(f"[quick-regression] running: {' '.join(cmd)}")
        completed = subprocess.run(cmd)
        if completed.returncode != 0:
            print(f"[quick-regression] failed: {' '.join(args)}")
            return completed.returncode
    print("[quick-regression] all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

