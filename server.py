import subprocess
from pathlib import Path

from mcp.server.mcpserver import MCPServer

mcp = MCPServer("standup-mcp")

# Unit separator: a character that never appears in commit messages
SEP = "\x1f"


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