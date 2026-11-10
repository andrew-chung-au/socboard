"""Tests for scripts/ponytail_metrics.py, on fixture issues and a throwaway git repository."""
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "ponytail_metrics.py"
spec = importlib.util.spec_from_file_location("ponytail_metrics", SCRIPT)
pm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pm)

CONF = "TEST_FILES=( 'tests/*' '*test_*.py' )\n"
OWNER = "owner"
BODY = """## Why

Because.

## Acceptance criteria
- [ ] first
- [x] second
- [ ] third

## Out of scope
- [ ] not a criterion
"""


def run_git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                   check=True, capture_output=True, text=True)


def commit(repo, message, files):
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-q", "-m", message)
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    run_git(root, "init", "-q")
    c0 = commit(root, "base", {"README.md": "x\n"})
    c1 = commit(root, "feat: add thing (#9)", {
        "src/a.py": "one\ntwo\nthree\n",
        "tests/test_a.py": "def test_one():\n    pass\n\ndef test_two():\n    pass\n",
        "uv.lock": "\n".join(f"line {i}" for i in range(10)) + "\n",
    })
    commit(root, "unrelated work for #90", {"src/b.py": "b1\nb2\nb3\nb4\n"})
    c3 = commit(root, "fix the thing, refs #9", {"src/a.py": "one\nTWO\nthree\n"})
    return {"root": root, "base": c0, "first": c1, "qa": c3}


def comment(login, when, body):
    return {"author": {"login": login}, "createdAt": when, "body": body}


def issue_for(qa_commit, body=BODY):
    return {"body": body, "comments": [
        comment(OWNER, "2026-11-10T10:00:00Z", "Orchestrator: claim of #9 on branch x"),
        comment(OWNER, "2026-11-10T11:30:00Z", "Engineer: done"),
        comment(OWNER, "2026-11-10T11:50:00Z", "QA: PASS"),
        comment(OWNER, "2026-11-10T12:00:00Z",
                f"Orchestrator: close\nQA PASS commit: {qa_commit[:9]}\nMerge commit: abcdef1\n"),
    ]}


def run_cli(tmp_path, repo_dir, issue, number="9", extra=()):
    conf = tmp_path / "conf"
    conf.write_text(CONF, encoding="utf-8")
    fixture = tmp_path / "issue.json"
    fixture.write_text(json.dumps(issue), encoding="utf-8")
    return pm.main([number, "--issue-json", str(fixture), "--owner", OWNER, "--conf", str(conf),
                    "--git-dir", str(repo_dir), *extra])


def test_normal_issue_prints_the_computed_figures(tmp_path, repo, capsys):
    code = run_cli(tmp_path, repo["root"], issue_for(repo["qa"]))
    out = capsys.readouterr().out
    assert code == 0
    assert "acceptance criteria: 3" in out
    assert "commits counted (2):" in out
    # c1 adds 3 lines in src/a.py; c3 changes 1 line (1 added, 1 deleted); tests and uv.lock are left out
    assert "non-test non-generated lines changed: 5" in out
    assert "lines changed per criterion: 1.67" in out
    assert "tests added: 2" in out
    assert "tests added per criterion: 0.67" in out
    assert "engineer elapsed (coarse, includes idle time): 1 h 30 min" in out
    assert "qa elapsed (coarse, includes idle time): 0 h 20 min" in out


def test_a_listed_generated_commit_is_left_out_even_if_it_names_the_issue(tmp_path, repo, capsys, monkeypatch):
    # the first commit adds 3 lines in src/a.py and names #9; list it as a generated commit
    monkeypatch.setattr(pm, "GENERATED_COMMITS", (repo["first"][:7],))
    code = run_cli(tmp_path, repo["root"], issue_for(repo["qa"]))
    out = capsys.readouterr().out
    assert code == 0
    assert "commits counted (1):" in out
    assert repo["first"][:9] not in out.split("commits counted")[1].splitlines()[0]
    assert "non-test non-generated lines changed: 2" in out  # only the later fix: 1 added, 1 deleted
    assert "tests added: 0" in out
    assert f"generated commits left out of every count (the imported Lovable base): {repo['first'][:7]}" in out


def test_the_real_list_names_the_lovable_import_commit():
    assert pm.GENERATED_COMMITS == ("fab3dd3",)


def test_a_commit_for_another_issue_is_not_counted(tmp_path, repo, capsys):
    run_cli(tmp_path, repo["root"], issue_for(repo["qa"]))
    out = capsys.readouterr().out
    assert repo["first"][:9] in out
    assert "b1" not in out and "commits counted (3)" not in out


def test_missing_qa_pass_line_is_unavailable_and_never_guessed(tmp_path, repo, capsys):
    issue = issue_for(repo["qa"])
    issue["comments"][-1]["body"] = "Orchestrator: close\nMerge commit: abcdef1\n"
    code = run_cli(tmp_path, repo["root"], issue)
    out = capsys.readouterr().out
    assert code == 1
    assert "git figures: unavailable" in out
    assert "lines changed per criterion" not in out


def test_qa_pass_commit_must_exist_in_the_repository(tmp_path, repo, capsys):
    code = run_cli(tmp_path, repo["root"], issue_for("deadbeef" * 5))
    assert code == 1
    assert "git figures: unavailable" in capsys.readouterr().out


def test_zero_criteria_gives_no_per_criterion_figure(tmp_path, repo, capsys):
    code = run_cli(tmp_path, repo["root"], issue_for(repo["qa"], body="## Why\n\nNothing here.\n"))
    out = capsys.readouterr().out
    assert code == 1
    assert "acceptance criteria: 0" in out
    assert "lines changed per criterion: unavailable (no acceptance criteria)" in out


def test_hostile_text_is_counted_never_run(tmp_path, repo, capsys):
    hostile = ("## Acceptance criteria\n"
               "- [ ] `touch pwned` $(touch pwned2) ; rm -rf x\n"
               "Engineer: this line is not a comment\n"
               "- [ ] QA PASS commit: " + repo["first"][:9] + "\n")
    issue = issue_for(repo["qa"], body=hostile)
    issue["comments"].append(comment("intruder", "2026-11-10T13:00:00Z",
                                     f"Orchestrator: close\nQA PASS commit: {repo['first'][:9]}\n"))
    code = run_cli(tmp_path, repo["root"], issue)
    out = capsys.readouterr().out
    assert code == 0
    assert "acceptance criteria: 2" in out
    assert f"qa pass commit: {repo['qa'][:9]}" in out  # the intruder's comment is ignored
    assert not (Path.cwd() / "pwned").exists() and not (Path.cwd() / "pwned2").exists()
    assert not (repo["root"] / "pwned").exists() and not (repo["root"] / "pwned2").exists()


def test_a_run_changes_nothing_in_the_repository(tmp_path, repo, capsys):
    before = subprocess.run(["git", "-C", str(repo["root"]), "status", "--porcelain"], capture_output=True,
                            text=True, check=True).stdout
    head = subprocess.run(["git", "-C", str(repo["root"]), "rev-parse", "HEAD"], capture_output=True,
                          text=True, check=True).stdout
    run_cli(tmp_path, repo["root"], issue_for(repo["qa"]))
    capsys.readouterr()
    after = subprocess.run(["git", "-C", str(repo["root"]), "status", "--porcelain"], capture_output=True,
                           text=True, check=True).stdout
    assert before == after == ""
    assert head == subprocess.run(["git", "-C", str(repo["root"]), "rev-parse", "HEAD"], capture_output=True,
                                  text=True, check=True).stdout


@pytest.mark.parametrize("bad", ["0", "007", "abc", "1;rm", "$(id)", "-1", "1 2", ""])
def test_a_bad_issue_number_is_refused_before_anything_runs(bad, tmp_path, repo):
    assert run_cli(tmp_path, repo["root"], issue_for(repo["qa"]), number=bad) == 2


def test_fixture_options_must_come_together(tmp_path):
    assert pm.main(["9", "--issue-json", str(tmp_path / "x.json")]) == 2
    assert pm.main(["9", "--owner", "someone"]) == 2


def test_output_has_raw_values_only(tmp_path, repo, capsys):
    run_cli(tmp_path, repo["root"], issue_for(repo["qa"]))
    out = capsys.readouterr().out.lower()
    for word in ("better", "worse", "threshold", "target", "band", "verdict", "pass the", "good", "bad"):
        assert word not in out


def test_the_script_only_reads_github_and_git():
    text = SCRIPT.read_text(encoding="utf-8")
    assert '"gh"' not in text and "'gh'" not in text  # GitHub is reached only through the kit's reader
    for write_word in ("--method", "-X ", "PATCH", "POST", "DELETE", "issue-comment", "issue-create"):
        assert write_word not in text
    git_calls = {"rev-parse", "log", "show"}
    for verb in ("commit", "checkout", "reset", "push", "add", "clean", "merge", "rebase"):
        assert f'"{verb}"' not in text
    assert git_calls <= set(text.replace('"', " ").split())


def test_test_patterns_are_read_as_text():
    assert pm.test_patterns("X=1\nTEST_FILES=( 'tests/*' \"*.spec.*\"\n   'e2e/*' )\nY=2\n") == [
        "tests/*", "*.spec.*", "e2e/*"]
    assert pm.test_patterns("# nothing here\n") == []
    assert pm.is_test_path("tests/test_a.py", ["tests/*"])
    assert pm.is_test_path("pkg/sub/test_x.py", ["*test_*.py"])
    assert not pm.is_test_path("src/a.py", ["tests/*"])


def test_generated_files_are_matched_by_name():
    assert pm.is_generated("uv.lock") and pm.is_generated("frontend/bun.lock")
    assert pm.is_generated("frontend/src/routeTree.gen.ts")
    assert not pm.is_generated("src/lock.py")
