# SOCBoard project commands. Core targets come from the agent kit's
# templates/Makefile.mk; recipe lines are TAB-indented.

help: ## List targets
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z0-9_ -]+:.*## / {printf "  %-20s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

verify: ## Run the verification gate: make verify [BASE=<commit>]
	$(if $(findstring $$,$(value BASE)),$(error Usage: make verify [BASE=<commit>] - BASE must not contain $$))
	@bash _docs/agent-kit/scripts/verify.sh "$$BASE"

assert-clean: ## Fail if this folder has uncommitted changes (used after QA)
	@bash _docs/agent-kit/scripts/assert-clean.sh

hooks: ## Install the repo's git hooks (where agents commit; never in a clean clone)
	@bash _docs/agent-kit/scripts/install-hooks.sh

clean-scratch: ## Empty .scratch/ at the end of an issue (refuses while a started process is running)
	@for f in .scratch/*.pid; do if [ -f "$$f" ] && kill -0 "$$(cat "$$f")" 2>/dev/null; then echo "$$f: process $$(cat "$$f") is still running; stop it first"; exit 1; fi; done
	@mkdir -p .scratch && find .scratch -mindepth 1 -delete && echo ".scratch/ is now empty"

tool-versions:: ## Print each tool's version, or "<tool>: missing"
	@for t in git gh node npm python3 uv docker; do if command -v "$$t" >/dev/null 2>&1; then "$$t" --version 2>&1 | { read -r first; echo "$$t: $$first"; }; else echo "$$t: missing"; fi; done

# --- GitHub issues (the project's tasks) ------------------------------------

issue-number-ok = $(and $(filter 1,$(words $(1))),$(if $(subst 0,,$(subst 1,,$(subst 2,,$(subst 3,,$(subst 4,,$(subst 5,,$(subst 6,,$(subst 7,,$(subst 8,,$(subst 9,,$(1))))))))))),,ok),$(if $(subst x$(strip $(1))x,,x$(1)x),,ok),$(if $(filter 0%,$(1)),,ok),$(if $(word 10,$(subst 0,x ,$(subst 1,x ,$(subst 2,x ,$(subst 3,x ,$(subst 4,x ,$(subst 5,x ,$(subst 6,x ,$(subst 7,x ,$(subst 8,x ,$(subst 9,x ,$(1)))))))))))),,ok))

issue-hash: ## Print an issue body's sha256, author and owner check: make issue-hash N=<issue number>
	$(if $(call issue-number-ok,$(value N)),,$(error Usage: make issue-hash N=<issue number>))
	@python3 -B _docs/agent-kit/scripts/issue-hash.py "$$N"

issue-dump: ## Write an issue as JSON to .scratch/issue-<N>.json: make issue-dump N=<issue number>
	$(if $(call issue-number-ok,$(value N)),,$(error Usage: make issue-dump N=<issue number>))
	@mkdir -p .scratch
	@if gh issue view "$(N)" --json number,title,body,state,labels,author,comments > ".scratch/issue-$(N).json.tmp" 2>/dev/null && python3 -B -m json.tool ".scratch/issue-$(N).json.tmp" > /dev/null 2>&1; then mv ".scratch/issue-$(N).json.tmp" ".scratch/issue-$(N).json" && echo ".scratch/issue-$(N).json"; else rm -f ".scratch/issue-$(N).json.tmp"; echo "issue-dump: gh could not return valid JSON for issue $(N)" >&2; exit 1; fi

sweep-due: ## Say whether a sweep is due (read-only; the sweep interval is set in the script)
	@python3 -B _docs/agent-kit/scripts/sweep-due.py

tier-check: ## Check a change against its tier's path limit: make tier-check TIER=<light|standard|heavy> [BASE=<commit>]
	$(if $(and $(filter 1,$(words $(value TIER))),$(filter light standard heavy,$(value TIER))),,$(error Usage: make tier-check TIER=<light|standard|heavy> [BASE=<commit>]))
	$(if $(findstring $$,$(value BASE)),$(error Usage: make tier-check TIER=<light|standard|heavy> [BASE=<commit>] - BASE must not contain $$))
	@bash _docs/agent-kit/scripts/check-tier.sh "$$TIER" "$$BASE"

# --- Stack -----------------------------------------------------------------

install: ## Install dependencies
	uv sync --locked --all-groups

test: ## Run all unit tests
	uv run --locked pytest

test-one: ## Run one test file: make test-one FILE=path
	$(if $(findstring $$,$(value FILE)),$(error Usage: make test-one FILE=path/to/test_file - FILE must not contain $$))
	@test -n "$$FILE" || { echo "Usage: make test-one FILE=path/to/test_file"; exit 1; }
	uv run --locked pytest "$$FILE"

# --- GitHub module (wrappers; no labels, edits or closes except issue-close) ----

issue-file-chars := a b c d e f g h i j k l m n o p q r s t u v w x y z A B C D E F G H I J K L M N O P Q R S T U V W X Y Z 0 1 2 3 4 5 6 7 8 9 . _ / -
issue-strip-chars = $(if $(2),$(call issue-strip-chars,$(subst $(firstword $(2)),,$(1)),$(wordlist 2,$(words $(2)),$(2))),$(1))
issue-file-ok = $(and $(filter 1,$(words $(1))),$(if $(call issue-strip-chars,$(1),$(issue-file-chars)),,ok),$(if $(subst x$(strip $(1))x,,x$(1)x),,ok),$(filter .scratch/%,$(1)),$(if $(findstring ..,$(1)),,ok))

issue-create: ## File one issue from a file under .scratch/ (first line Title: <text>, blank line, body): make issue-create FILE=.scratch/<name>.md
	$(if $(call issue-file-ok,$(value FILE)),,$(error Usage: make issue-create FILE=.scratch/<name>.md - one word under .scratch/ with letters digits . _ / - only))
	@bash _docs/agent-kit/scripts/issue-create.sh "$(FILE)"

issue-comment: ## Post a file under .scratch/ as one comment (first line PM: Engineer: QA: or Orchestrator:): make issue-comment N=<issue number> FILE=.scratch/<name>.md
	$(if $(call issue-number-ok,$(value N)),,$(error Usage: make issue-comment N=<issue number> FILE=.scratch/<name>.md))
	$(if $(call issue-file-ok,$(value FILE)),,$(error Usage: make issue-comment N=<issue number> FILE=.scratch/<name>.md - one word under .scratch/ with letters digits . _ / - only))
	@bash _docs/agent-kit/scripts/issue-comment.sh "$(N)" "$(FILE)"

issue-reason-ok = $(and $(filter 1,$(words $(1))),$(filter completed not-planned,$(1)),$(if $(filter-out completed not-planned,$(1)),,ok),$(if $(subst x$(strip $(1))x,,x$(1)x),,ok))

issue-close: ## Close one issue when its recorded history shows the process was followed (the orchestrator only): make issue-close N=<issue number> [REASON=not-planned]
	$(if $(call issue-number-ok,$(value N)),,$(error Usage: make issue-close N=<issue number> [REASON=not-planned]))
	$(if $(value REASON),$(if $(call issue-reason-ok,$(value REASON)),,$(error Usage: make issue-close N=<issue number> [REASON=completed|not-planned])))
	@bash _docs/agent-kit/scripts/issue-close.sh "$(N)" $(if $(value REASON),"$$REASON")

# --- Permission log (procedures/permission-log.md) --------------------------

# Both targets follow the validation rule above. permission-report passes SINCE on and the
# script accepts only a real YYYY-MM-DD date (an empty SINCE means no limit).
# permission-logs-prune checks DAYS here (1 to 4 digits, no leading zero) and the script
# checks the 3650 limit, so a bad value deletes nothing.

permission-days-ok = $(and $(filter 1,$(words $(1))),$(if $(subst 0,,$(subst 1,,$(subst 2,,$(subst 3,,$(subst 4,,$(subst 5,,$(subst 6,,$(subst 7,,$(subst 8,,$(subst 9,,$(1))))))))))),,ok),$(if $(subst x$(strip $(1))x,,x$(1)x),,ok),$(if $(filter 0%,$(1)),,ok),$(if $(word 5,$(subst 0,x ,$(subst 1,x ,$(subst 2,x ,$(subst 3,x ,$(subst 4,x ,$(subst 5,x ,$(subst 6,x ,$(subst 7,x ,$(subst 8,x ,$(subst 9,x ,$(1)))))))))))),,ok))

permission-report: ## Summarise the permission logs of all tools: make permission-report [SINCE=<YYYY-MM-DD or YYYY-MM-DDTHH:MMZ>]
	$(if $(findstring $$,$(value SINCE)),$(error Usage: make permission-report [SINCE=<YYYY-MM-DD or YYYY-MM-DDTHH:MMZ>] - SINCE must not contain $$))
	@python3 -B _docs/agent-kit/scripts/permission-report.py "$$SINCE"

permission-logs-prune: ## Delete permission logs older than DAYS days (the human runs it): make permission-logs-prune DAYS=<1 to 3650>
	$(if $(call permission-days-ok,$(value DAYS)),,$(error Usage: make permission-logs-prune DAYS=<1 to 3650>))
	@python3 -B _docs/agent-kit/scripts/permission-logs-prune.py "$$DAYS"

# --- Project targets (this project's own; add below) -------------------------

decisions: ## List the decision records with their Status word (proposed, approved or superseded)
	@for f in _docs/decisions/*.md; do s=$$(grep -m1 -i -o 'status: [a-z]*' "$$f" | cut -d' ' -f2); printf '%-11s %s  (%s)\n' "$${s:-MISSING}" "$$(head -n 1 "$$f" | sed 's/^# //')" "$$(basename "$$f")"; done
