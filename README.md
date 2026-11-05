# SOCBoard

SOCBoard is a web-based case management board for a security operations centre (SOC). Alerts arrive through an API, receive a severity from fixed rules, and appear in a shared analyst queue. Analysts claim an alert, investigate it and move it through a defined workflow to closure. Every status change, assignment and note is recorded in an audit trail.

All data is synthetic. The graded version uses no machine learning in its critical path, so every severity and every status change can be explained and tested.

## Problem

Small security teams receive alerts from many sources: failed logins, malware detections, unusual network traffic. Without a shared system, alerts sit in inboxes, chat threads and spreadsheets. This causes three problems:

- Nobody knows who owns an alert. Two analysts investigate the same one, or nobody does.
- There is no record of what happened. When an alert is closed, there is no trace of who decided what, or why.
- Priority is inconsistent. Each analyst judges severity differently, so urgent alerts wait behind noisy ones.

## Solution

SOCBoard is a web-based alert triage case manager. Alerts arrive through an API, get a consistent severity from fixed rules, and appear in an analyst queue. Analysts claim an alert, investigate it and move it through a defined workflow. Every step is recorded.

The data is synthetic. The graded version has no machine learning in its critical path, so every severity and every status change can be explained and tested.

## Users

- Analyst: works the queue, adds notes, moves cases through the workflow.
- Lead: reviews the queue, reassigns cases and checks the audit trail.

## Core functionality

1. Ingest: alerts come in through the API with a source, type, affected asset and timestamp.
2. Score: a fixed rule set assigns severity (for example, by alert type and asset criticality).
3. Queue: analysts see open alerts sorted by severity and age.
4. Triage: an analyst claims an alert, adds notes and moves it through the workflow.
5. Audit: every status change, assignment and note records who, what and when.

## Case workflow

New → Investigating → Closed (with a required closing reason, for example true positive, false positive or duplicate).

Blocked transitions, such as closing a New alert without investigating it, return a clear error and are never silently allowed.

## Expected behavior

- A new alert appears in the queue within [N] seconds with a severity.
- An alert can have only one owner at a time.
- Closing a case requires a reason.
- The audit trail for a case can be read in order, start to finish.
- Alert text is untrusted input: it is escaped when shown and never treated as instructions.

## Development

Requires Python 3.12, uv and make. From the project folder:

```sh
make install   # create the virtual environment and install dependencies
make test      # run the pytest suite
make verify    # run tests, whitespace and weakened-test checks
```

The web application is not built yet, so there is no `make run` target.

## Currently Out of Scope

Real log sources, real customer data, automated response actions and any AI-generated verdicts.

## Planned Future Development

A LangGraph triage step that suggests severity and a summary for an analyst to confirm, n8n for intake and escalation, and an MCP server for querying cases and checking VulnTriage for known exposures.