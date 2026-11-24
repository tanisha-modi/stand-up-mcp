import subprocess
from datetime import datetime, timezone

import pytest
from mcp.server.mcpserver.exceptions import ToolError

import server


# ---------- fixtures and fakes ----------
@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Tests must not depend on the developer's real .env settings."""
    monkeypatch.delenv("ALLOWED_REPO_ROOTS", raising=False)

@pytest.fixture
def git_repo(tmp_path):
    """A real throwaway git repo with one commit."""

    def run(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    run("init")
    run("config", "user.name", "Test User")
    run("config", "user.email", "test@example.com")
    (tmp_path / "a.txt").write_text("hello")
    run("add", ".")
    run("commit", "-m", "first commit ✅ émoji")
    return tmp_path


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else []

    def json(self):
        return self._payload


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------- local git ----------


def test_read_commits_returns_commit_with_unicode(git_repo):
    commits = server._read_commits(str(git_repo), days=7, author=None, max_commits=50)
    assert len(commits) == 1
    assert commits[0]["author"] == "Test User"
    assert "émoji" in commits[0]["message"]


def test_author_filter_with_no_match_returns_empty(git_repo):
    assert server._read_commits(str(git_repo), 7, "nobody-here", 50) == []


@pytest.mark.parametrize("days", [0, 91, 500])
def test_days_out_of_range_is_rejected(git_repo, days):
    with pytest.raises(ToolError, match="days must be between"):
        server._read_commits(str(git_repo), days, None, 50)


def test_missing_folder_is_rejected(tmp_path):
    with pytest.raises(ToolError, match="Not a directory"):
        server._read_commits(str(tmp_path / "nope"), 7, None, 50)


def test_folder_without_git_is_rejected(tmp_path):
    with pytest.raises(ToolError, match="Not a git repository"):
        server._read_commits(str(tmp_path), 7, None, 50)


def test_github_url_is_rejected_as_repo_path():
    with pytest.raises(ToolError, match="Not a directory"):
        server._read_commits("https://github.com/me/proj", 7, None, 50)


def test_allowlist_blocks_other_folders(git_repo, tmp_path_factory, monkeypatch):
    other = tmp_path_factory.mktemp("elsewhere")
    monkeypatch.setenv("ALLOWED_REPO_ROOTS", str(other))
    with pytest.raises(ToolError, match="allowed repository roots"):
        server._read_commits(str(git_repo), 7, None, 50)


def test_allowlist_permits_matching_folder(git_repo, monkeypatch):
    monkeypatch.setenv("ALLOWED_REPO_ROOTS", str(git_repo.parent))
    assert len(server._read_commits(str(git_repo), 7, None, 50)) == 1


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/me/proj.git",
        "https://github.com/me/proj",
        "git@github.com:me/proj.git",
    ],
)
def test_repo_from_remote_handles_common_url_forms(git_repo, url):
    subprocess.run(
        ["git", "-C", str(git_repo), "remote", "add", "origin", url],
        check=True, capture_output=True,
    )
    assert server._repo_from_remote(git_repo) == "me/proj"


def test_repo_from_remote_without_origin_gives_clear_error(git_repo):
    with pytest.raises(ToolError, match="origin"):
        server._repo_from_remote(git_repo)


# ---------- small helpers ----------


@pytest.mark.parametrize("repo", ["nonsense", "a/b/c", "", "me/proj; rm -rf /"])
def test_check_repo_rejects_bad_values(repo):
    with pytest.raises(ToolError):
        server._check_repo(repo)


def test_check_repo_accepts_valid_value():
    server._check_repo("tanisha-modi/stand-up-mcp")


def test_parse_time_understands_github_format():
    assert server._parse_time("2026-11-20T00:22:23Z").year == 2026


def test_section_says_nothing_to_report_when_empty():
    assert "Nothing to report" in server._section("Done", [])


# ---------- cache ----------


@pytest.fixture
def temp_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DB_PATH", tmp_path / "cache.db")


def test_cache_round_trip(temp_cache):
    server._cache_put("k", {"a": 1})
    assert server._cache_get("k") == {"a": 1}


def test_cache_miss_returns_none(temp_cache):
    assert server._cache_get("missing") is None


def test_cache_entry_expires(temp_cache, monkeypatch):
    server._cache_put("k", [1])
    monkeypatch.setattr(server, "CACHE_TTL_SECONDS", -1)
    assert server._cache_get("k") is None


# ---------- GitHub client (no network: httpx.get is faked) ----------


def test_github_get_second_call_uses_cache(temp_cache, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    calls = []

    def fake_get(*args, **kwargs):
        calls.append(1)
        return FakeResponse(200, [{"number": 1}])

    monkeypatch.setattr(server.httpx, "get", fake_get)
    assert server._github_get("/repos/me/proj/pulls") == [{"number": 1}]
    assert server._github_get("/repos/me/proj/pulls") == [{"number": 1}]
    assert len(calls) == 1


@pytest.mark.parametrize(
    "status, message",
    [(401, "token"), (404, "not found"), (403, "refused"), (500, "HTTP 500")],
)
def test_github_errors_become_readable_messages(temp_cache, monkeypatch, status, message):
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.setattr(server.httpx, "get", lambda *a, **k: FakeResponse(status))
    with pytest.raises(ToolError, match=f"(?i){message}"):
        server._github_get("/repos/me/proj/pulls")


def test_missing_token_gives_clear_error(temp_cache, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(ToolError, match="GITHUB_TOKEN"):
        server._github_get("/repos/me/proj/pulls")


# ---------- the report tool, end to end ----------


def test_generate_status_report_builds_all_sections(git_repo, monkeypatch):
    fresh = now_iso()
    open_pr = {
        "number": 7, "title": "Add feature", "state": "open", "draft": False,
        "user": {"login": "me"}, "created_at": fresh, "updated_at": fresh,
        "merged_at": None, "html_url": "https://example.com/pr/7",
    }
    open_issue = {
        "number": 9, "title": "Bug report", "user": {"login": "me"},
        "labels": [], "created_at": fresh, "updated_at": fresh,
        "html_url": "https://example.com/issues/9",
    }

    def fake_github_get(path, params=None):
        if path.endswith("/pulls"):
            return [open_pr]
        if params and params.get("state") == "open":
            return [open_issue]
        return []

    monkeypatch.setattr(server, "_github_get", fake_github_get)

    report = server.generate_status_report(repo_path=str(git_repo), repo="me/proj")

    assert "# Status report: me/proj" in report
    assert "first commit" in report
    assert "PR #7: Add feature" in report
    assert "Open issue #9: Bug report" in report
    assert "## Done" in report and "## In progress" in report