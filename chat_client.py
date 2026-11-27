import asyncio
import sys
from pathlib import Path

import ollama
from mcp import Client
from mcp.client.stdio import StdioServerParameters

MODEL = "qwen2.5:7b"
MAX_STEPS = 5            # stop runaway tool-call loops
MAX_TOOL_ERRORS = 2      # stop after this many failed tool calls in a row
MAX_TOOL_CHARS = 6000    # keep huge tool results from flooding a small model's context
DIRECT_TOOLS = {"generate_status_report"}  # output the code built; the model never rewrites it
HERE = Path(__file__).parent
DEFAULT_REPO = str(HERE)  # change this if your main project lives elsewhere

SYSTEM_PROMPT = (
    "You are a developer assistant with tools for git and GitHub activity. "
    f"The user's main repository is located at: {DEFAULT_REPO} "
    "Use that path when the user does not give one. "
    "Use tools to get facts. Never invent commits, pull requests, or issues. "
    "If a tool returns an error, read the message and fix your arguments."
)

server_params = StdioServerParameters(
    command=sys.executable,
    args=[str(HERE / "server.py")],
)


def result_text(result) -> str:
    """Join the text blocks of a tool result into one string."""
    return "\n".join(
        block.text for block in result.content if getattr(block, "text", None)
    )


def simplify_schema(schema: dict) -> dict:
    """Trim a JSON schema so small models see less noise."""
    properties = {}
    for name, prop in schema.get("properties", {}).items():
        prop = dict(prop)
        prop.pop("title", None)
        if "anyOf" in prop:
            options = [o for o in prop.pop("anyOf") if o.get("type") != "null"]
            if options:
                prop.update(options[0])
        properties[name] = prop
    return {
        "type": "object",
        "properties": properties,
        "required": schema.get("required", []),
    }


def to_ollama_tool(tool) -> dict:
    """Convert an MCP tool definition into Ollama's tool format."""
    schema = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None) or {}
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": simplify_schema(schema),
        },
    }


async def run_turn(client, llm, tools, messages, model=MODEL, verbose=True):
    """Let the model call tools until it answers. Returns (answer, calls)."""
    calls = []
    errors_in_a_row = 0

    for _ in range(MAX_STEPS):
        response = await llm.chat(
            model=model,
            messages=messages,
            tools=tools,
            options={"num_ctx": 8192},  # temperature 0 = repeatable
        )
        message = response.message
        messages.append(message)

        if not message.tool_calls:
            return (message.content or "").strip(), calls

        for call in message.tool_calls:
            name = call.function.name
            args = dict(call.function.arguments)
            try:
                result = await client.call_tool(name, args)
                text = result_text(result)
                failed = bool(result.is_error)
            except Exception as e:  # unknown tool name, bad arguments, etc.
                text, failed = f"Error: {e}", True

            calls.append({"name": name, "args": args, "is_error": failed})
            if verbose:
                print(f"  [tool] {name}({args})")
                print(f"  [result] {text[:120]!r}{'...' if len(text) > 120 else ''}")
            messages.append(
                {"role": "tool", "tool_name": name, "content": text[:MAX_TOOL_CHARS]}
            )

            if failed:
                errors_in_a_row += 1
                if errors_in_a_row >= MAX_TOOL_ERRORS:
                    return f"I couldn't complete that. The tool reported: {text}", calls
            else:
                errors_in_a_row = 0
                if name in DIRECT_TOOLS:
                    # Return the code-built output exactly as the server produced it
                    messages.append({"role": "assistant", "content": text})
                    return text, calls

    return "(Stopped: the model kept calling tools without answering.)", calls


async def main():
    llm = ollama.AsyncClient()
    async with Client(server_params) as client:
        listing = await client.list_tools()
        tools = [to_ollama_tool(t) for t in listing.tools]
        print(f"Connected. Model: {MODEL}. Tools: {', '.join(t.name for t in listing.tools)}")
        print("Type a question, or 'exit' to quit.\n")

        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        while True:
            question = (await asyncio.to_thread(input, "You: ")).strip()
            if question.lower() in ("", "exit", "quit"):
                break
            messages.append({"role": "user", "content": question})
            answer, _ = await run_turn(client, llm, tools, messages)
            print(f"\nAssistant: {answer}\n")


if __name__ == "__main__":
    asyncio.run(main())