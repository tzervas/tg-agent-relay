"""Build agent/repo stamp lines from env + git (no model calls)."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

_GH_SSH = re.compile(r"^git@github\.com:(?P<owner>[^/]+)/(?P<repo>[^/.]+)(?:\.git)?$")
_GH_HTTPS = re.compile(r"^https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/.]+)(?:\.git)?/?$")
_MERGED_RE = re.compile(r"\b(merged|MERGE)\b")


@dataclass(frozen=True)
class StampInfo:
    repo: str
    branch: str
    branch_url: str
    pr_url: str
    pr_state: str
    handle: str = ""

    def lines(self) -> list[str]:
        out: list[str] = []
        if self.handle:
            out.append(f"🤖 handle={self.handle}")
        if self.repo or self.branch:
            out.append(f"🏷 repo={self.repo} branch={self.branch}")
        if self.branch_url:
            out.append(f"🔗 branch: {self.branch_url}")
        if self.pr_url:
            out.append(f"🔗 pr: {self.pr_url}")
        if self.pr_state and self.pr_url:
            out.append(f"📌 status={self.pr_state}")
        return out

    def text(self) -> str:
        return "\n".join(self.lines())


def _env_map(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return env if env is not None else os.environ


def _parse_github_remote(url: str) -> tuple[str, str]:
    url = (url or "").strip()
    if not url:
        return "", ""
    m = _GH_SSH.match(url)
    if m:
        return m.group("owner"), m.group("repo")
    m = _GH_HTTPS.match(url)
    if m:
        return m.group("owner"), m.group("repo")
    return "", ""


def _run_git(args: list[str], cwd: Path) -> str:
    try:
        r = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if r.returncode != 0:
            return ""
        return (r.stdout or "").strip()
    except (OSError, subprocess.TimeoutExpired) as _exc:
        return ""


def _git_stamp_fields(cwd: Path) -> tuple[str, str, str]:
    remote = _run_git(["remote", "get-url", "origin"], cwd)
    owner, repo = _parse_github_remote(remote)
    branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    if branch == "HEAD":
        branch = ""
    slug = f"{owner}/{repo}" if owner and repo else (repo or "")
    branch_url = ""
    if owner and repo and branch:
        branch_url = f"https://github.com/{owner}/{repo}/tree/{branch}"
    return slug, branch, branch_url


def _pr_from_env(env: Mapping[str, str]) -> tuple[str, str]:
    url = (env.get("RELAY_PR_URL") or "").strip()
    state = (env.get("RELAY_PR_STATE") or "").strip().lower()
    num = (env.get("RELAY_PR_NUMBER") or "").strip()
    repo = (env.get("RELAY_REPO") or "").strip()
    if not url and num and repo and "/" in repo:
        url = f"https://github.com/{repo}/pull/{num}"
    if state not in ("open", "merged", "closed"):
        state = ""
    return url, state


def _gh_pr_lookup(cwd: Path) -> tuple[str, str]:
    if (os.environ.get("RELAY_PR_LOOKUP") or "").strip() not in ("1", "true", "yes", "on"):
        return "", ""
    try:
        r = subprocess.run(
            ["gh", "pr", "view", "--json", "url,state"],
            capture_output=True,
            text=True,
            timeout=8,
            cwd=str(cwd),
            check=False,
        )
        if r.returncode != 0 or not r.stdout.strip():
            return "", ""
        import json

        data = json.loads(r.stdout)
        url = str(data.get("url") or "")
        state = str(data.get("state") or "").lower()
        if state == "merged":
            return url, "merged"
        if state == "open":
            return url, "open"
        if state == "closed":
            return url, "closed"
        return url, ""
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, ValueError) as _exc:
        return "", ""


def build_stamp_info(
    *,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    force_merged: bool = False,
) -> StampInfo:
    e = _env_map(env)
    work = Path(cwd or e.get("RELAY_CWD") or os.getcwd())
    if not work.is_dir():
        work = Path(os.getcwd())

    repo_env = (e.get("RELAY_REPO") or "").strip()
    branch_env = (e.get("RELAY_BRANCH") or "").strip()
    pr_url, pr_state = _pr_from_env(e)

    git_slug, git_branch, branch_url = _git_stamp_fields(work)
    repo = repo_env or git_slug
    branch = branch_env or git_branch

    if not branch_url and repo and "/" in repo and branch:
        owner, name = repo.split("/", 1)
        branch_url = f"https://github.com/{owner}/{name}/tree/{branch}"

    if not pr_url:
        pr_url, gh_state = _gh_pr_lookup(work)
        if gh_state and not pr_state:
            pr_state = gh_state

    if force_merged and pr_url:
        pr_state = "merged"

    handle = ""
    try:
        from tg_agent_relay.agent_handle import build_handle_from_env

        handle = build_handle_from_env(e)
    except ImportError:
        handle = (e.get("RELAY_AGENT_HANDLE") or "").strip()
        if handle and not handle.startswith("@"):
            handle = f"@{handle}"

    return StampInfo(
        repo=repo,
        branch=branch,
        branch_url=branch_url,
        pr_url=pr_url,
        pr_state=pr_state,
        handle=handle,
    )


def build_stamp(
    *,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    body_for_merge_hint: str = "",
) -> str:
    merged_hint = bool(body_for_merge_hint and _MERGED_RE.search(body_for_merge_hint))
    info = build_stamp_info(cwd=cwd, env=env, force_merged=merged_hint)
    return info.text()


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Print agent stamp (env + git)")
    p.add_argument("--cwd", default="")
    args = p.parse_args(argv)
    cwd = args.cwd or None
    print(build_stamp(cwd=cwd))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
