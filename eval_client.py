# import asyncio
# import sys

# import ollama
# from mcp import Client

# import chat_client as cc

# REPO = "tanisha-modi/stand-up-mcp"  # a GitHub repo your token can read

# CASES = [
#     {"name": "report, default path",
#      "prompt": "Generate a status report for my project",
#      "tool": "generate_status_report", "args": {"repo_path": cc.DEFAULT_REPO}},
#     {"name": "report, 14 days",
#      "prompt": "Give me a report on my project for the last 14 days.",
#      "tool": "generate_status_report", "args": {"repo_path": cc.DEFAULT_REPO, "days": 14}},
#     {"name": "recent commits",
#      "prompt": "What did I commit in the last 3 days?",
#      "tool": "get_recent_commits", "args": {"days": 3}},
#     {"name": "open issues",
#      "prompt": f"How many open issues does {REPO} have?",
#      "tool": "list_open_issues", "args": {"repo": REPO}},
#     {"name": "pull requests",
#      "prompt": f"List the pull requests for {REPO}",
#      "tool": "list_pull_requests", "args": {"repo": REPO}},
#     {"name": "stale PRs",
#      "prompt": f"Are there any stale pull requests in {REPO}?",
#      "tool": "find_stale_prs", "args": {"repo": REPO}},
#     {"name": "no tool needed",
#      "prompt": "What is 12 times 12?",
#      "tool": None},
#     {"name": "blocked folder",
#      "prompt": r"Generate a status report for C:\Windows",
#      "blocked": True},
# ]


# def same(a, b) -> bool:
#     if isinstance(a, str) and isinstance(b, str):
#         norm = lambda s: s.replace("/", "\\").rstrip("\\").lower()
#         return norm(a) == norm(b)
#     return a == b


# def check(case, answer, calls) -> bool:
#     if case.get("blocked"):
#         # Pass only if the server refused and the answer is not a made-up report
#         return bool(calls) and all(c["is_error"] for c in calls) and "## Done" not in answer
#     if case["tool"] is None:
#         return not calls
#     if not calls or calls[0]["name"] != case["tool"]:
#         return False
#     return all(same(calls[0]["args"].get(k), v) for k, v in case.get("args", {}).items())


# async def run_model(model, client, llm, tools):
#     results = []
#     for case in CASES:
#         messages = [
#             {"role": "system", "content": cc.SYSTEM_PROMPT},
#             {"role": "user", "content": case["prompt"]},
#         ]
#         try:
#             answer, calls = await cc.run_turn(
#                 client, llm, tools, messages, model=model, verbose=False
#             )
#             ok = check(case, answer, calls)
#             used = ", ".join(c["name"] for c in calls) or "(none)"
#         except Exception as e:
#             ok, used = False, f"error: {str(e)[:60]}"
#         print(f"  {'PASS' if ok else 'FAIL'}  {case['name']:<22} tools: {used}")
#         results.append(ok)
#     return results


# async def main():
#     models = sys.argv[1:] or [cc.MODEL]
#     llm = ollama.AsyncClient()
#     all_results = {}
#     async with cc.Client(cc.server_params) as client:
#         listing = await client.list_tools()
#         tools = [cc.to_ollama_tool(t) for t in listing.tools]
#         for model in models:
#             print(f"\n=== {model} ===")
#             all_results[model] = await run_model(model, client, llm, tools)

#     lines = ["| Case | " + " | ".join(models) + " |",
#              "|---|" + "---|" * len(models)]
#     for i, case in enumerate(CASES):
#         cells = " | ".join("PASS" if all_results[m][i] else "FAIL" for m in models)
#         lines.append(f"| {case['name']} | {cells} |")
#     totals = " | ".join(f"{sum(all_results[m])}/{len(CASES)}" for m in models)
#     lines.append(f"| **Total** | {totals} |")

#     table = "\n".join(lines)
#     print("\n" + table)
#     with open("eval_results.md", "w", encoding="utf-8") as f:
#         f.write(table + "\n")
#     print("\nSaved to eval_results.md")


# if __name__ == "__main__":
#     asyncio.run(main())