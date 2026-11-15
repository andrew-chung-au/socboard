---
inclusion: always
---

# Evidence for reviewers, and steps a person can follow (human ruling, 2026-11-16)

Peer reviewers score this project against `_docs/planning/rubric.md`. They read the README and follow links; they cannot open the maintainer's GitHub settings, AWS account or terminal. These rules apply to every lane and every issue from now on.

1. **Every issue says what evidence it leaves.** Its acceptance criteria name the artefact a reviewer can open (a CI run, a pull request, a committed result file, a screenshot, a job summary) and the rubric criterion it supports. The Engineer links it from the README's reviewer section in the same pull request.
2. **Record a check when it happens, once.** When the maintainer or CI checks something, the result goes straight into a committed, dated record (a human task's result table, or the evidence timeline), with the date, what was checked, the result and a link. Nobody repeats a check only to produce evidence. A check is run again only when the thing it checked has changed since (for example, a bootstrap change replaces the instance), and the record says why.
3. **Steps a person runs are written as steps.** Any instruction a person is expected to carry out (a human task, a README section, a runbook, an issue or chat message) gives one command per block, what the person should see, and what to do if it differs, following `_docs/agent-kit/templates/human-task.md` until #252 replaces it. A value the person must choose comes with a suggested default made by a command.
4. **No video is planned.** Evidence is run links, committed results and screenshots, and the on-call agent's own pull request. A recording is optional and never the only evidence for a criterion.
5. **Evidence outlives the deployment.** The server runs for about a week. Before teardown, every link the README relies on must work without the live server (run logs, pull requests, committed files and images), and the README says the URLs are no longer live.
6. **The repository names no other project.** Write SOCBoard's choices in SOCBoard's own words (`previous-projects.md`).
