import asyncio
import json
import sys
from pathlib import Path

from mcp import Client
from mcp.client.stdio import StdioServerParameters

HERE = Path(__file__).parent

# How to launch the server: the same Python that runs this script (your venv's)
server_params = StdioServerParameters(
    command=sys.executable,
    args=[str(HERE / "server.py")],
)


def result_text(result) -> str:
    """Join the text blocks of a tool result into one string."""
    return "\n".join(
        block.text for block in result.content if getattr(block, "text", None)
    )


async def main():
    async with Client(server_params) as client:
        # 1. Ask the server what it offers
        listing = await client.list_tools()
        print(f"Server exposes {len(listing.tools)} tools:\n")
        for tool in listing.tools:
            first_line = (tool.description or "").strip().splitlines()[0]
            print(f"- {tool.name}: {first_line}")

        # 2. Look at the schema the model will eventually receive
        report_tool = next(t for t in listing.tools if t.name == "generate_status_report")
        schema = getattr(report_tool, "input_schema", None) or getattr(
            report_tool, "inputSchema", None
        )
        print("\nSchema for generate_status_report:")
        print(json.dumps(schema, indent=2))

        # 3. Call a tool with good arguments
        print("\n--- Calling generate_status_report ---")
        result = await client.call_tool("generate_status_report", {"repo_path": str(HERE)})
        print(result_text(result))

        # 4. Call a tool with bad arguments, to see how errors arrive
        print("\n--- Calling get_recent_commits with days=500 ---")
        result = await client.call_tool(
            "get_recent_commits", {"repo_path": str(HERE), "days": 500}
        )
        print("is_error:", result.is_error)
        print(result_text(result))


if __name__ == "__main__":
    asyncio.run(main())