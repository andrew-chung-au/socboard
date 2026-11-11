"""Tests for scripts/ponytail_metrics.py, on fixture issues and a throwaway git repository."""
import importlib.util
import json
import re
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
    c2 = commit(root, "unrelated work for #90", {"src/b.py": "b1\nb2\nb3\nb4\n"})
    c3 = commit(root, "fix the thing (#9)", {"src/a.py": "one\nTWO\nthree\n"})
    return {"root": root, "base": c0, "first": c1, "other": c2, "qa": c3}


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
    assert repo["other"][:9] not in out and "commits counted (3)" not in out


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
    # hostile text in a commit subject is matched by the rule and nothing is run
    hostile_sha = commit(repo["root"], "$(touch pwned3) `touch pwned4`; rm -rf x (#9)", {"src/h.py": "h\n"})
    code = run_cli(tmp_path, repo["root"], issue_for(hostile_sha))
    out = capsys.readouterr().out
    assert code == 0
    assert hostile_sha[:9] in out.split("commits counted")[1].splitlines()[0]
    for name in ("pwned", "pwned2", "pwned3", "pwned4"):
        assert not (Path.cwd() / name).exists() and not (repo["root"] / name).exists()


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
    out = re.sub(r"\b[0-9a-f]{7,40}\b", "", capsys.readouterr().out.lower())  # commit ids are not prose
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


# ---- the "belongs" rule (#36) ----

def history(tmp_path, messages):
    """A throwaway repository with one commit per message, each changing src/f.py by one line."""
    root = tmp_path / "hist"
    root.mkdir(parents=True)
    run_git(root, "init", "-q")
    commit(root, "base", {"README.md": "x\n"})
    shas = [commit(root, message, {"src/f.py": f"line {i}\n"}) for i, message in enumerate(messages)]
    return root, shas


def lines_of(out):
    """The 'commits counted', 'mention only' and 'shared with other issues' lines as (count, ids)."""
    found = {}
    for label in ("commits counted", "mention only", "shared with other issues"):
        line = next(item for item in out.splitlines() if item.startswith(label + " ("))
        count, _, ids = line[len(label) + 2:].partition("): ")
        found[label] = (int(count), [] if ids == "none" else ids.split())
    return found


def classify_run(tmp_path, capsys, messages, number="9"):
    root, shas = history(tmp_path, messages + ["tail (#9)"])
    code = run_cli(tmp_path, root, issue_for(shas[-1]), number=number)
    out = capsys.readouterr().out
    return code, lines_of(out), [sha[:9] for sha in shas[:-1]], out


def test_a_subject_ending_with_the_issue_is_counted_with_or_without_a_body_mention(tmp_path, capsys):
    code, found, ids, _ = classify_run(tmp_path, capsys, ["Add thing (#9)", "Add other (#9)\n\nSee #9 for more."])
    assert code == 0
    assert found["commits counted"][1][:2] == ids
    assert found["mention only"] == (0, []) and found["shared with other issues"] == (0, [])


def test_a_body_only_mention_is_refused_and_listed(tmp_path, capsys):
    code, found, ids, _ = classify_run(tmp_path, capsys, ["Add thing\n\nrefs #9"])
    assert found["commits counted"][0] == 1 and ids[0] not in found["commits counted"][1]
    assert found["mention only"] == (1, ids)


def test_a_subject_mention_without_the_trailing_reference_is_refused(tmp_path, capsys):
    _, found, ids, _ = classify_run(tmp_path, capsys, ["Add session summary for issue #9"])
    assert ids[0] not in found["commits counted"][1]
    assert found["mention only"] == (1, ids)


def test_a_revert_subject_is_mention_only(tmp_path, capsys):
    _, found, ids, _ = classify_run(tmp_path, capsys, ['Revert "Add thing (#9)"'])
    assert found["mention only"] == (1, ids)


@pytest.mark.parametrize("subject", ["Add thing (#90)", "Add thing (#19)"])
def test_the_number_match_is_exact(tmp_path, capsys, subject):
    _, found, ids, out = classify_run(tmp_path, capsys, [subject])
    assert ids[0] not in found["commits counted"][1]
    assert found["mention only"] == (0, []) and found["shared with other issues"] == (0, [])
    assert ids[0] not in out.split("commits counted")[1]


@pytest.mark.parametrize("subject", ["Plan work (#9, #27)", "Fix #9 (#27)", "Fix #27 (#9)"])
def test_shared_commits_count_for_neither_issue_and_are_listed(tmp_path, capsys, subject):
    _, found, ids, _ = classify_run(tmp_path, capsys, [subject])
    assert ids[0] not in found["commits counted"][1]
    assert found["shared with other issues"] == (1, ids)
    assert found["mention only"] == (0, [])
    # the other issue does not count it either
    root, shas = history(tmp_path / "other", [subject, "tail (#27)"])
    run_cli(tmp_path, root, issue_for(shas[-1]), number="27")
    other = lines_of(capsys.readouterr().out)
    assert shas[0][:9] not in other["commits counted"][1]
    assert other["shared with other issues"] == (1, [shas[0][:9]])


def test_the_lists_print_even_when_nothing_is_counted(tmp_path, capsys):
    root, shas = history(tmp_path, ["Add thing\n\nrefs #9", "Plan (#9, #27)"])
    code = run_cli(tmp_path, root, issue_for(shas[-1]))
    out = capsys.readouterr().out
    found = lines_of(out)
    assert code == 1
    assert found["commits counted"] == (0, [])
    assert found["mention only"] == (1, [shas[0][:9]])
    assert found["shared with other issues"] == (1, [shas[1][:9]])
    assert "git figures: unavailable" in out


def test_the_counted_line_keeps_its_form(tmp_path, repo, capsys):
    run_cli(tmp_path, repo["root"], issue_for(repo["qa"]))
    out = capsys.readouterr().out
    assert f"commits counted (2): {repo['first'][:9]} {repo['qa'][:9]}" in out
    assert "mention only (0): none" in out
    assert "shared with other issues (0): none" in out


# ---- elapsed time unknown, and the failures that stay failures (#36) ----

def test_unknown_elapsed_time_alone_exits_zero(tmp_path, repo, capsys):
    issue = issue_for(repo["qa"])
    issue["comments"] = [c for c in issue["comments"] if not c["body"].startswith("Engineer:")]
    code = run_cli(tmp_path, repo["root"], issue)
    out = capsys.readouterr().out
    assert code == 0
    assert "engineer elapsed (coarse, includes idle time): unknown" in out
    assert "qa elapsed (coarse, includes idle time): unknown" in out
    assert "unavailable" not in out


def test_unknown_elapsed_time_with_an_unavailable_git_figure_exits_one(tmp_path, repo, capsys):
    issue = issue_for(repo["qa"])
    issue["comments"] = [c for c in issue["comments"] if not c["body"].startswith("Engineer:")]
    issue["comments"][-1]["body"] = "Orchestrator: close\nMerge commit: abcdef1\n"
    code = run_cli(tmp_path, repo["root"], issue)
    out = capsys.readouterr().out
    assert code == 1
    assert "elapsed (coarse, includes idle time): unknown" in out
    assert "git figures: unavailable" in out


def test_an_unreadable_conf_exits_one(tmp_path, repo, capsys):
    fixture = tmp_path / "issue.json"
    fixture.write_text(json.dumps(issue_for(repo["qa"])), encoding="utf-8")
    code = pm.main(["9", "--issue-json", str(fixture), "--owner", OWNER, "--conf", str(tmp_path / "missing"),
                    "--git-dir", str(repo["root"])])
    assert code == 1
    assert "cannot read" in capsys.readouterr().err


def test_the_header_states_the_rule_and_the_exit_codes():
    header = pm.__doc__
    for phrase in ("(#N)", "other issue number", "mention only", "Shared", "at commit time", "unknown"):
        assert phrase in header


# ---- record framing (#40) ----

FAKE = "a" * 40


def figure_lines(out):
    return [line for line in out.splitlines()
            if line.startswith(("non-test non-generated lines changed", "test lines changed", "tests added:"))]


def walked_ids(root):
    return subprocess.run(["git", "-C", str(root), "log", "--no-merges", "--reverse", "--format=%H"],
                          check=True, capture_output=True, text=True).stdout.split()


def built_log(records):
    """Hand-built `git log -z --format=%H%x1f%B` output."""
    return "".join(f"{sha}\x1f{message}\n\0" for sha, message in records)


def forge_log_output(monkeypatch, output):
    """Make the script's message-log call (the one with -z) return `output`; other calls are real."""
    real = pm.git

    def fake(git_dir, *args):
        if "-z" in args:
            return output
        return real(git_dir, *args)
    monkeypatch.setattr(pm, "git", fake)


def test_one_line_forgery_adds_no_commit_and_the_real_commit_is_shared(tmp_path, capsys):
    forged = f"innocent (#27)\x1e{FAKE}\x1fforged (#9)"
    root, shas = history(tmp_path, [forged, "tail (#9)", "tail (#27)"])
    real = shas[0]
    for number in ("9", "27"):
        code = run_cli(tmp_path, root, issue_for(shas[-1]), number=number)
        out = capsys.readouterr().out
        assert code == 0
        assert "aaaaaaaaa" not in out
        found = lines_of(out)
        assert real[:9] in found["shared with other issues"][1]
        assert real[:9] not in found["commits counted"][1]
        assert found["commits counted"][1] == [shas[1][:9] if number == "9" else shas[2][:9]]


def test_body_forgery_adds_no_commit_and_the_real_commit_is_counted_for_its_issue(tmp_path, capsys):
    forged = f"innocent (#27)\n\x1e{FAKE}\x1fforged (#9)"
    root, shas = history(tmp_path, [forged, "tail (#9)", "tail (#27)"])
    real = shas[0][:9]
    code = run_cli(tmp_path, root, issue_for(shas[-1]), number="27")
    out = capsys.readouterr().out
    assert code == 0 and "aaaaaaaaa" not in out
    assert real in lines_of(out)["commits counted"][1]
    code = run_cli(tmp_path, root, issue_for(shas[-1]), number="9")
    out = capsys.readouterr().out
    assert code == 0 and "aaaaaaaaa" not in out
    found = lines_of(out)
    assert real in found["mention only"][1] and real not in found["commits counted"][1]


def forged_id_history(tmp_path, name, forge):
    root = tmp_path / name
    root.mkdir()
    run_git(root, "init", "-q")
    commit(root, "base", {"README.md": "x\n"})
    first = commit(root, "feat: add thing (#9)", {
        "src/a.py": "one\ntwo\nthree\n",
        "tests/test_a.py": "def test_one():\n    pass\n\ndef test_two():\n    pass\n",
    })
    message = "later work (#9)"
    if forge:
        message += f"\n\n\x1e{first}\x1fforged (#9)"
    later = commit(root, message, {"src/a.py": "one\nTWO\nthree\nfour\n"})
    return root, first, later


def test_a_forged_record_naming_a_real_id_does_not_count_it_twice(tmp_path, capsys):
    root, first, later = forged_id_history(tmp_path, "forged", True)
    code = run_cli(tmp_path, root, issue_for(later))
    out = capsys.readouterr().out
    assert code == 0
    assert lines_of(out)["commits counted"] == (2, [first[:9], later[:9]])
    plain_root, _, plain_later = forged_id_history(tmp_path, "plain", False)
    run_cli(tmp_path, plain_root, issue_for(plain_later))
    assert figure_lines(out) == figure_lines(capsys.readouterr().out)


def test_the_parsed_log_must_match_the_walked_list_exactly():
    ids = ["1" * 40, "2" * 40, "3" * 40]
    good = built_log([(ids[0], "a (#9)"), (ids[1], "b\n\nbody"), (ids[2], "c")])
    assert pm.parse_messages(ids, good) == [(ids[0], "a (#9)"), (ids[1], "b\n\nbody"), (ids[2], "c")]
    assert pm.parse_messages([], "") == []
    with pytest.raises(pm.Unavailable):  # same ids, different order
        pm.parse_messages(ids, built_log([(ids[1], "b"), (ids[0], "a"), (ids[2], "c")]))
    with pytest.raises(pm.Unavailable):  # message text with the old separators is only text
        pm.parse_messages(ids, built_log([(ids[0], f"a\x1e{ids[1]}\x1fb"), (ids[2], "c")]))
    with pytest.raises(pm.Unavailable):  # a record with no separator is not an id
        pm.parse_messages(ids, "garbage\0")


def test_git_show_is_only_given_ids_from_the_walked_list(tmp_path, monkeypatch, capsys):
    forged = f"innocent (#9)\x1e{FAKE}\x1fforged (#9)"
    root, shas = history(tmp_path, [forged, "tail (#9)"])
    real, shown_ids = pm.git, []

    def spy(git_dir, *args):
        if args[0] == "show":
            shown_ids.append(args[-1])
        return real(git_dir, *args)
    monkeypatch.setattr(pm, "git", spy)
    assert run_cli(tmp_path, root, issue_for(shas[-1])) == 0
    capsys.readouterr()
    assert shown_ids and set(shown_ids) <= set(walked_ids(root))


def refused(tmp_path, monkeypatch, capsys, build):
    root, shas = history(tmp_path, ["one (#9)", "two (#9)", "three (#9)"])
    forge_log_output(monkeypatch, built_log(build(shas)))
    code = run_cli(tmp_path, root, issue_for(shas[-1]))
    return code, capsys.readouterr().out


def test_a_record_whose_id_was_not_walked_is_refused(tmp_path, monkeypatch, capsys):
    code, out = refused(tmp_path, monkeypatch, capsys, lambda s: [(s[0], "one (#9)"), (FAKE, "forged (#9)"),
                                                                  (s[1], "two (#9)"), (s[2], "three (#9)")])
    assert code == 1
    assert "git figures: unavailable (" in out and "is not a commit git walked" in out
    assert "commits counted" not in out and "non-test non-generated lines changed" not in out


def test_a_repeated_record_id_is_refused(tmp_path, monkeypatch, capsys):
    code, out = refused(tmp_path, monkeypatch, capsys, lambda s: [(s[0], "one (#9)"), (s[1], "two (#9)"),
                                                                  (s[1], "two (#9)"), (s[2], "three (#9)")])
    assert code == 1
    assert "git figures: unavailable (" in out and "appears more than once" in out
    assert "commits counted" not in out and "non-test non-generated lines changed" not in out


def test_a_walked_id_missing_from_the_log_is_refused(tmp_path, monkeypatch, capsys):
    code, out = refused(tmp_path, monkeypatch, capsys, lambda s: [(s[0], "one (#9)"), (s[2], "three (#9)")])
    assert code == 1
    assert "git figures: unavailable (" in out and "missing from the log" in out
    assert "commits counted" not in out and "non-test non-generated lines changed" not in out


def test_a_commit_message_with_a_nul_byte_adds_no_record(tmp_path, capsys):
    root, shas = history(tmp_path, ["one (#9)"])
    tree = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD^{tree}"], check=True,
                          capture_output=True, text=True).stdout.strip()
    stamp = "t <t@example.com> 1700000000 +0000"
    message = f"nul commit (#9)\n\n\x1e{FAKE}\x1fforged (#9)\n\0hidden tail\n".encode()
    raw = f"tree {tree}\nparent {shas[0]}\nauthor {stamp}\ncommitter {stamp}\n\n".encode() + message
    nul = subprocess.run(["git", "-C", str(root), "hash-object", "-t", "commit", "-w", "--literally", "--stdin"],
                         input=raw, check=True, capture_output=True).stdout.decode().strip()
    subprocess.run(["git", "-C", str(root), "update-ref", "HEAD", nul], check=True, capture_output=True)
    code = run_cli(tmp_path, root, issue_for(nul))
    out = capsys.readouterr().out
    assert code in (0, 1)  # what git prints for the message after the NUL is not asserted
    assert "aaaaaaaaa" not in out
    found = lines_of(out)
    walked = {sha[:9] for sha in walked_ids(root)}
    for label in ("commits counted", "mention only", "shared with other issues"):
        assert set(found[label][1]) <= walked
