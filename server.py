import subprocess
from pathlib import Path

import os
import re

import httpx
from dotenv import load_dotenv

from mcp.server.mcpserver import MCPServer

mcp = MCPServer("standup-mcp")

# Unit separator: a character that never appears in commit messages
SEP = "\x1f"

# Load .env from the same folder as server.py. We use an explicit path because
# Claude Desktop starts the server from a different working directory.
load_dotenv(Path(__file__).parent / ".env")

GITHUB_API = "https://api.github.com"
REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def _github_get(path: str, params: dict | None = None) -> list | dict:
    """Call the GitHub API and turn failures into readable tool errors."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ToolError("GITHUB_TOKEN is not set. Add it to the .env file.")

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        response = httpx.get(
            f"{GITHUB_API}{path}", headers=headers, params=params, timeout=15
        )
    except httpx.HTTPError as e:
        raise ToolError(f"Could not reach GitHub: {e}")

    if response.status_code == 401:
        raise ToolError("GitHub rejected the token (expired or invalid).")
    if response.status_code == 404:
        raise ToolError("Repository not found, or the token has no access to it.")
    if response.status_code in (403, 429):
        raise ToolError("GitHub refused the request (rate limit or missing permission).")
    if response.status_code >= 400:
        raise ToolError(f"GitHub API error: HTTP {response.status_code}")
    return response.json()


@mcp.tool()
def list_pull_requests(
    repo: str,
    state: str = "open",
    author: str | None = None,
    max_results: int = 20,
) -> list[dict]:
    """List pull requests for a GitHub repository.

    Args:
        repo: Repository in "owner/name" form, e.g. "tanisha-modi/stand-up-mcp".
        state: One of "open", "closed", or "all".
        author: Only include PRs opened by this GitHub username.
        max_results: Maximum number of PRs to return (1-50).
    """
    if not REPO_PATTERN.match(repo):
        raise ToolError('repo must look like "owner/name"')
    if state not in ("open", "closed", "all"):
        raise ToolError('state must be "open", "closed", or "all"')
    if not 1 <= max_results <= 50:
        raise ToolError("max_results must be between 1 and 50")

    pulls = _github_get(
        f"/repos/{repo}/pulls",
        params={"state": state, "per_page": 50, "sort": "updated", "direction": "desc"},
    )

    results = []
    for pr in pulls:
        if author and pr["user"]["login"].lower() != author.lower():
            continue
        results.append(
            {
                "number": pr["number"],
                "title": pr["title"],
                "author": pr["user"]["login"],
                "state": pr["state"],
                "draft": pr["draft"],
                "created_at": pr["created_at"],
                "updated_at": pr["updated_at"],
                "url": pr["html_url"],
            }
        )
        if len(results) >= max_results:
            break
    return results

@mcp.tool()
def get_recent_commits(
    repo_path: str,
    days: int = 7,
    author: str | None = None,
    max_commits: int = 50,
) -> list[dict]:
    """Get recent commits from a local git repository.

    Args:
        repo_path: Absolute path to the git repository.
        days: How many days back to look (1-90).
        author: Only include commits whose author matches this name or email.
        max_commits: Maximum number of commits to return (1-200).
    """
    # 1. Validate inputs before touching the file system or running anything
    if not 1 <= days <= 90:
        raise ValueError("days must be between 1 and 90")
    if not 1 <= max_commits <= 200:
        raise ValueError("max_commits must be between 1 and 200")

    path = Path(repo_path).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"Not a directory: {path}")
    if not (path / ".git").exists():
        raise ValueError(f"Not a git repository: {path}")

    # 2. Build the git command as a list (never a string) to avoid shell injection
    cmd = [
        "git", "-C", str(path), "log",
        f"--since={days}.days.ago",
        f"--max-count={max_commits}",
        f"--pretty=format:%h{SEP}%an{SEP}%aI{SEP}%s",
    ]
    if author:
        cmd.append(f"--author={author}")

    # 3. Run it, with a timeout so a hung process can't freeze the server
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=15, check=True
        )
    except FileNotFoundError:
        raise ValueError("git is not installed or not on PATH")
    except subprocess.TimeoutExpired:
        raise ValueError("git log timed out")
    except subprocess.CalledProcessError as e:
        raise ValueError(f"git log failed: {e.stderr.strip()}")

    # 4. Parse each line into a dictionary
    commits = []
    for line in result.stdout.splitlines():
        sha, name, date, message = line.split(SEP, 3)
        commits.append(
            {"sha": sha, "author": name, "date": date, "message": message}
        )
    return commits


if __name__ == "__main__":
    mcp.run()