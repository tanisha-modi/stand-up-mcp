import subprocess
from pathlib import Path

import os
import re

import httpx
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

import json
import logging 
import sqlite3
import sys
import time
from contextlib import closing

mcp = MCPServer("standup-mcp")

# Unit separator: a character that never appears in commit messages
SEP = "\x1f"

# Load .env from the same folder as server.py. We use an explicit path because
# Claude Desktop starts the server from a different working directory.
load_dotenv(Path(__file__).parent / ".env")

GITHUB_API = "https://api.github.com"
REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


logging.basicConfig(stream=sys.stderr, level=logging.INFO)
log = logging.getLogger("standup-mcp")

DB_PATH = Path(__file__).parent / "cache.db"
CACHE_TTL_SECONDS = 300  # 5 minutes


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS cache ("
        "key TEXT PRIMARY KEY, value TEXT NOT NULL, fetched_at REAL NOT NULL)"
    )
    return conn


def _cache_get(key: str):
    """Return the cached value if it is still fresh, otherwise None."""
    try:
        with closing(_db()) as conn:
            row = conn.execute(
                "SELECT value, fetched_at FROM cache WHERE key = ?", (key,)
            ).fetchone()
    except sqlite3.Error as e:
        log.warning("cache read failed: %s", e)
        return None
    if row and time.time() - row[1] < CACHE_TTL_SECONDS:
        return json.loads(row[0])
    return None


def _cache_put(key: str, value) -> None:
    try:
        with closing(_db()) as conn, conn:
            conn.execute(
                "INSERT OR REPLACE INTO cache (key, value, fetched_at) VALUES (?, ?, ?)",
                (key, json.dumps(value), time.time()),
            )
    except sqlite3.Error as e:
        log.warning("cache write failed: %s", e)


def _github_get(path: str, params: dict | None = None) -> list | dict:
    """Call the GitHub API (with a short-lived cache) and raise readable errors."""
    key = path + "?" + json.dumps(params or {}, sort_keys=True)
    cached = _cache_get(key)
    if cached is not None:
        log.info("cache hit: %s", path)
        return cached

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
    data = response.json()
    _cache_put(key, data)
    log.info("cache miss, fetched: %s", path)
    return data

def _check_repo(repo: str) -> None:
    if not REPO_PATTERN.match(repo):
        raise ToolError('repo must look like "owner/name"')


def _parse_time(value: str) -> datetime:
    # GitHub returns times like "2026-11-20T00:22:23Z"
    return datetime.fromisoformat(value.replace("Z", "+00:00"))

@mcp.tool()
def clear_cache() -> str:
    """Delete all cached GitHub responses so the next request fetches fresh data."""
    try:
        with closing(_db()) as conn, conn:
            conn.execute("DELETE FROM cache")
    except sqlite3.Error as e:
        raise ToolError(f"Could not clear the cache: {e}")
    return "Cache cleared."

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
    _check_repo(repo)
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
def list_open_issues(
    repo: str,
    assignee: str | None = None,
    max_results: int = 20,
) -> list[dict]:
    """List open issues (not pull requests) for a GitHub repository.

    Args:
        repo: Repository in "owner/name" form, e.g. "tanisha-modi/stand-up-mcp".
        assignee: Only include issues assigned to this GitHub username.
        max_results: Maximum number of issues to return (1-50).
    """
    _check_repo(repo)
    if not 1 <= max_results <= 50:
        raise ToolError("max_results must be between 1 and 50")

    params = {"state": "open", "per_page": 50, "sort": "updated", "direction": "desc"}
    if assignee:
        params["assignee"] = assignee

    items = _github_get(f"/repos/{repo}/issues", params=params)

    results = []
    for item in items:
        # GitHub's issues endpoint also returns pull requests; skip those
        if "pull_request" in item:
            continue
        results.append(
            {
                "number": item["number"],
                "title": item["title"],
                "author": item["user"]["login"],
                "labels": [label["name"] for label in item["labels"]],
                "created_at": item["created_at"],
                "updated_at": item["updated_at"],
                "url": item["html_url"],
            }
        )
        if len(results) >= max_results:
            break
    return results


@mcp.tool()
def find_stale_prs(repo: str, stale_days: int = 7) -> list[dict]:
    """Find open pull requests that have had no activity for a while.

    Args:
        repo: Repository in "owner/name" form, e.g. "tanisha-modi/stand-up-mcp".
        stale_days: A PR is stale if untouched for at least this many days (0-365).
    """
    _check_repo(repo)
    if not 0 <= stale_days <= 365:
        raise ToolError("stale_days must be between 0 and 365")

    cutoff = datetime.now(timezone.utc) - timedelta(days=stale_days)
    pulls = _github_get(
        f"/repos/{repo}/pulls",
        params={"state": "open", "per_page": 50, "sort": "updated", "direction": "asc"},
    )

    stale = []
    for pr in pulls:
        updated = _parse_time(pr["updated_at"])
        if updated <= cutoff:
            stale.append(
                {
                    "number": pr["number"],
                    "title": pr["title"],
                    "author": pr["user"]["login"],
                    "days_idle": (datetime.now(timezone.utc) - updated).days,
                    "url": pr["html_url"],
                }
            )
    return stale

 
@mcp.tool()
def get_recent_commits(
    repo_path: str,
    days: int = 7,
    author: str | None = None,
    max_commits: int = 50,
) -> list[dict]:
    """Get recent commits from a local git repository.

    Args:
        repo_path: Absolute path to a local git repository folder, e.g. "C:\\Users\\tanis\\Downloads\\Code2". Not a GitHub URL.
        days: How many days back to look (1-90).
        author: Only include commits whose author name or email contains this text. Optional.
        max_commits: Maximum number of commits to return (1-200).
    """
    # 1. Validate inputs before touching the file system or running anything
    if not 1 <= days <= 90:
        raise ToolError("days must be between 1 and 90")
    if not 1 <= max_commits <= 200:
        raise ToolError("max_commits must be between 1 and 200")

    path = Path(repo_path).expanduser().resolve()
    if not path.is_dir():
        raise ToolError(f"Not a directory: {path}")
    if not (path / ".git").exists():
        raise ToolError(f"Not a git repository: {path}")

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
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=True,
        )
    except FileNotFoundError:
        raise ToolError("git is not installed or not on PATH")
    except subprocess.TimeoutExpired:
        raise ToolError("git log timed out")
    except subprocess.CalledProcessError as e:
        raise ToolError(f"git log failed: {e.stderr.strip()}")

    # 4. Parse each line into a dictionary
    commits = []
    for line in result.stdout.splitlines():
        sha, name, date, message = line.split(SEP, 3)
        commits.append(
            {"sha": sha, "author": name, "date": date, "message": message}
        )
    return commits


@mcp.resource("github://repo/{owner}/{name}/summary")
def repo_summary(owner: str, name: str) -> str:
    """A quick summary of a GitHub repository's open work: PR, issue, and stale PR counts."""
    repo = f"{owner}/{name}"
    _check_repo(repo)

    pulls = _github_get(
        f"/repos/{repo}/pulls",
        params={"state": "open", "per_page": 50, "sort": "updated", "direction": "desc"},
    )
    items = _github_get(
        f"/repos/{repo}/issues",
        params={"state": "open", "per_page": 50, "sort": "updated", "direction": "desc"},
    )
    issues = [i for i in items if "pull_request" not in i]

    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    stale = [p for p in pulls if _parse_time(p["updated_at"]) <= cutoff]

    return (
        f"Repository: {repo}\n"
        f"Open pull requests: {len(pulls)}\n"
        f"Open issues: {len(issues)}\n"
        f"Pull requests idle for 7+ days: {len(stale)}\n"
    )

REPORT_FORMAT = (
    "# Status report format\n"
    "\n"
    "## Done\n"
    "- One line per finished item (commits merged, issues closed), most important first.\n"
    "\n"
    "## In progress\n"
    "- Open pull requests and what they are waiting on.\n"
    "\n"
    "## Blockers / needs attention\n"
    "- Stale pull requests and open issues that need a decision.\n"
    "\n"
    "Rules: be concise, use plain language, mention PR and issue numbers, "
    "and never invent work that does not appear in the data.\n"
)


@mcp.resource("standup://report-format")
def report_format() -> str:
    """The format every standup or weekly status report should follow."""
    return REPORT_FORMAT


@mcp.prompt()
def weekly_report(repo: str, repo_path: str, days: int = 7) -> str:
    """Write a weekly status report from local commits plus GitHub PRs and issues.

    Args:
        repo: GitHub repository as "owner/name", e.g. "tanisha-modi/stand-up-mcp".
        repo_path: Local folder of the same repository.
        days: How many days back the report should cover.
    """
    return (
        f"Write a status report for the last {days} days.\n\n"
        f"1. Call get_recent_commits with repo_path={repo_path!r} and days={days}.\n"
        f"2. Call list_pull_requests with repo={repo!r}, state='all'.\n"
        f"3. Call list_open_issues with repo={repo!r}.\n"
        f"4. Call find_stale_prs with repo={repo!r}.\n\n"
        "Only report what the tool results show. If a tool returns nothing, say so "
        "instead of inventing activity.\n\n"
        "Use exactly this format:\n\n"
        f"{REPORT_FORMAT}"
    )

if __name__ == "__main__":
    mcp.run()