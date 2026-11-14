"""The project-owned git guard lets through exactly one plain push of a claude/ branch to
origin, and leaves every other kit block in place
(`_docs/decisions/2026-11-14T0420Z-agent-push-claude-branches.md`)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / ".claude" / "hooks" / "guard_git_local.py"
KIT_GUARD = ROOT / "_docs" / "agent-kit" / "claude-code" / "hooks" / "guard_git.py"


def run(guard: Path, command: str) -> subprocess.CompletedProcess:
    payload = json.dumps({"tool_input": {"command": command}})
    return subprocess.run([sys.executable, str(guard)], input=payload, capture_output=True, text=True, timeout=30)


ALLOWED = [
    "git push -u origin claude/blissful-wright-vg0ycj",
    "git push --set-upstream origin claude/issue-22",
    "git push origin claude/some.branch_name-1",
    "git push origin claude/nested/name",
]

BLOCKED = [
    "git push",
    "git push origin",
    "git push origin main",
    "git push -u origin main",
    "git push origin HEAD",
    "git push origin claude/x:main",
    "git push origin +claude/x",
    "git push -f origin claude/x",
    "git push --force origin claude/x",
    "git push --force-with-lease origin claude/x",
    "git push --delete origin claude/x",
    "git push origin --delete claude/x",
    "git push --no-verify origin claude/x",
    "git push --all origin",
    "git push --mirror origin",
    "git push --tags origin claude/x",
    "git push upstream claude/x",
    "git push origin claude/x claude/y",
    "git push origin claude/",
    "git push origin claude/../main",
    "git push origin claude/x && git push origin main",
    "git push origin claude/x; rm -rf build",
    "echo hi | git push origin claude/x",
    "git -C other push origin claude/x",
    "sudo git push origin claude/x",
    "FOO=1 git push origin claude/x",
]

KIT_BLOCKS = [
    "git add .",
    "git add -A",
    "git commit -am message",
    "git commit -n -m message",
    "git config core.hooksPath /dev/null",
    "cat .env",
]


@pytest.mark.parametrize("command", ALLOWED)
def test_a_plain_push_of_a_claude_branch_to_origin_is_allowed(command):
    done = run(GUARD, command)
    assert done.returncode == 0, done.stderr
    assert run(KIT_GUARD, command).returncode == 2  # the kit alone blocks it; only the wrapper allows it


@pytest.mark.parametrize("command", BLOCKED)
def test_every_other_push_is_still_blocked(command):
    done = run(GUARD, command)
    assert done.returncode == 2
    assert "Blocked by the agent kit" in done.stderr


@pytest.mark.parametrize("command", KIT_BLOCKS)
def test_the_kits_other_blocks_are_unchanged(command):
    wrapped, kit = run(GUARD, command), run(KIT_GUARD, command)
    assert wrapped.returncode == kit.returncode == 2
    assert wrapped.stderr == kit.stderr


@pytest.mark.parametrize("command", ["git status --short", "make verify", "ls -la"])
def test_ordinary_commands_pass(command):
    assert run(GUARD, command).returncode == 0
