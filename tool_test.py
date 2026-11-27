import ollama

MODEL = "qwen2.5:7b"

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "generate_status_report",
            "description": "Generate a ready-to-read status report: commits, PRs, issues, and blockers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_path": {
                        "type": "string",
                        "description": "Absolute path to a local git repository folder.",
                    },
                    "days": {
                        "type": "integer",
                        "description": "How many days back the report covers (1-90).",
                    },
                },
                "required": ["repo_path"],
            },
        },
    }
]

PROMPTS = [
    r"Generate a status report for C:\Users\tanis\Downloads\Code2",
    "What is the capital of France?",
    "Give me a report on my Code2 project for the last 14 days.",
]

for prompt in PROMPTS:
    response = ollama.chat(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        tools=TOOLS,
        options={"num_ctx": 8192},
    )
    message = response.message
    print(f"\nPrompt: {prompt}")
    if message.tool_calls:
        for call in message.tool_calls:
            print(f"  -> TOOL CALL: {call.function.name} {dict(call.function.arguments)}")
    else:
        print(f"  -> PLAIN ANSWER: {(message.content or '').strip()[:200]}")