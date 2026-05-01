"""Built-in boot-md hook - run ~/.hermes/BOOT.md on gateway startup."""

import logging
import threading

logger = logging.getLogger("hooks.boot-md")

from hermes_constants import get_hermes_home

HERMES_HOME = get_hermes_home()
BOOT_FILE = HERMES_HOME / "BOOT.md"


def _build_boot_prompt(content: str) -> str:
    """Wrap BOOT.md content in a system-level instruction."""
    return (
        "\u4f60\u6b63\u5728\u6267\u884c Hermes \u542f\u52a8\u68c0\u67e5\u6e05\u5355\u3002"
        "\u8bf7\u4e25\u683c\u9075\u5faa\u4e0b\u9762 BOOT.md \u7684\u6307\u4ee4\u3002\n\n"
        "---\n"
        f"{content}\n"
        "---\n\n"
        "\u9010\u6761\u6267\u884c\u6307\u4ee4\u3002\u5982\u679c\u9700\u8981\u5411\u5e73\u53f0\u53d1\u9001\u6d88\u606f\uff0c"
        "\u8bf7\u4f7f\u7528 send_message \u5de5\u5177\u3002\n"
        "\u9ed8\u8ba4\u4f7f\u7528\u4e2d\u6587\u8f93\u51fa\uff1b\u53ea\u6709 BOOT.md \u660e\u786e\u8981\u6c42\u82f1\u6587\u65f6\u624d\u4f7f\u7528\u82f1\u6587\u3002\n"
        "\u4e0d\u8981\u8f93\u51fa\u65e5\u8bed\u3001\u5fb7\u8bed\u3001\u6cd5\u8bed\u3001\u897f\u73ed\u7259\u8bed\u6216\u5176\u4ed6\u8bed\u8a00\u3002\n"
        "\u5982\u679c\u6ca1\u6709\u9700\u8981\u5904\u7406\u6216\u6c47\u62a5\u7684\u4e8b\u9879\uff0c\u53ea\u56de\u590d\uff1a[SILENT]"
    )


def _run_boot_agent(content: str) -> None:
    """Spawn a one-shot agent session to execute the boot instructions."""
    try:
        from run_agent import AIAgent

        prompt = _build_boot_prompt(content)
        agent = AIAgent(
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            max_iterations=20,
        )
        result = agent.run_conversation(prompt)
        response = result.get("final_response", "")
        if response and "[SILENT]" not in response:
            logger.info("boot-md completed: %s", response[:200])
        else:
            logger.info("boot-md completed (nothing to report)")
    except Exception as e:
        logger.error("boot-md agent failed: %s", e)


async def handle(event_type: str, context: dict) -> None:
    """Gateway startup handler - run BOOT.md if it exists."""
    if not BOOT_FILE.exists():
        return

    content = BOOT_FILE.read_text(encoding="utf-8").strip()
    if not content:
        return

    logger.info("Running BOOT.md (%d chars)", len(content))

    thread = threading.Thread(
        target=_run_boot_agent,
        args=(content,),
        name="boot-md",
        daemon=True,
    )
    thread.start()
