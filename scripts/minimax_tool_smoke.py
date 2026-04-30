#!/usr/bin/env python3
import json
import os
import sys

from openai import OpenAI
from hermes_cli.env_loader import load_hermes_dotenv


def main() -> int:
    load_hermes_dotenv()
    api_key = os.environ.get("MINIMAX_CN_API_KEY") or os.environ.get("MINIMAX_API_KEY")
    base_url = os.environ.get("MINIMAX_CN_BASE_URL", "https://api.minimaxi.com/v1")
    model = os.environ.get("MINIMAX_SMOKE_MODEL", "MiniMax-M2.7")
    if not api_key:
        print("MINIMAX_CN_API_KEY or MINIMAX_API_KEY is required", file=sys.stderr)
        return 2

    client = OpenAI(api_key=api_key, base_url=base_url)
    tools = [{
        "type": "function",
        "function": {
            "name": "ping",
            "description": "Echo a short message for smoke testing.",
            "parameters": {
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"]
            }
        }
    }]
    messages = [{
        "role": "user",
        "content": "Call the ping tool exactly once with message=smoke-ok. Do not answer normally before the tool call."
    }]
    first = client.chat.completions.create(model=model, messages=messages, tools=tools)
    choice = first.choices[0].message
    tool_calls = choice.tool_calls or []
    if not tool_calls:
        print("No tool call returned", file=sys.stderr)
        return 3
    tool_call = tool_calls[0]
    args = json.loads(tool_call.function.arguments or "{}")
    if args.get("message") != "smoke-ok":
        print(f"Unexpected tool args: {args}", file=sys.stderr)
        return 4

    messages.append({
        "role": "assistant",
        "content": choice.content or "",
        "tool_calls": [{
            "id": tool_call.id,
            "type": "function",
            "function": {
                "name": tool_call.function.name,
                "arguments": tool_call.function.arguments,
            },
        }],
    })
    messages.append({
        "role": "tool",
        "tool_call_id": tool_call.id,
        "content": json.dumps({"message": args["message"], "status": "ok"}, ensure_ascii=False),
    })

    second = client.chat.completions.create(model=model, messages=messages, tools=tools)
    final = second.choices[0].message.content or ""
    print(json.dumps({
        "model": model,
        "base_url": base_url,
        "tool_call_id": tool_call.id,
        "tool_name": tool_call.function.name,
        "tool_args": args,
        "final": final,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
