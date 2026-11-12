---
inclusion: always
---

# Every issue has a place in the agreed order

Applies to the orchestrator and the PM in this repository.

The process grooms only the next issue in the agreed order (`process.md`, Lifecycle steps 1 and 2), and only a groomed issue can be approved. An issue outside the order is therefore never groomed and never reaches the human. The 2026-11-12 prioritization review found twelve open issues in that state.

- When an issue is filed (follow-up, sweep proposal, QA risk), add it to "Supporting issues, in order" in `_docs/planning/roadmap-1-core.md` in the same session, with its trigger (the Core issue it must land before, or the event that brings it up). Tell the human it was placed and where.
- At each decision point, check the supporting list for issues whose trigger has come; groom those next and say so.
- At each sweep, list any open issue that is in neither the Core task list nor the supporting list.

This is a local rule until the kit carries it: the matching `process.md` change is queued for the epoch batch, and the gap is a finding to report upstream.
