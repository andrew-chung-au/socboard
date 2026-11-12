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

tool-versions::
	@if command -v bun >/dev/null 2>&1; then echo "bun: $$(bun --version)"; else echo "bun: missing"; fi

# --- GitHub issues (the project's tasks) ------------------------------------

issue-number-ok = $(and $(filter 1,$(words $(1))),$(if $(subst 0,,$(subst 1,,$(subst 2,,$(subst 3,,$(subst 4,,$(subst 5,,$(subst 6,,$(subst 7,,$(subst 8,,$(subst 9,,$(1))))))))))),,ok),$(if $(subst x$(strip $(1))x,,x$(1)x),,ok),$(if $(filter 0%,$(1)),,ok),$(if $(word 10,$(subst 0,x ,$(subst 1,x ,$(subst 2,x ,$(subst 3,x ,$(subst 4,x ,$(subst 5,x ,$(subst 6,x ,$(subst 7,x ,$(subst 8,x ,$(subst 9,x ,$(1)))))))))))),,ok))

issue-hash: ## Print an issue body's sha256, author and owner check: make issue-hash N=<issue number>
	$(if $(call issue-number-ok,$(value N)),,$(error Usage: make issue-hash N=<issue number>))
	@python3 -B _docs/agent-kit/scripts/issue-hash.py "$$N"

issue-dump: ## Write an issue as JSON to .scratch/issue-<N>.json: make issue-dump N=<issue number>
	$(if $(call issue-number-ok,$(value N)),,$(error Usage: make issue-dump N=<issue number>))
	@mkdir -p .scratch
	@if python3 -B _docs/agent-kit/scripts/github_api.py dump "$(N)" > ".scratch/issue-$(N).json.tmp" 2>/dev/null && python3 -B -m json.tool ".scratch/issue-$(N).json.tmp" > /dev/null 2>&1; then mv ".scratch/issue-$(N).json.tmp" ".scratch/issue-$(N).json" && echo ".scratch/issue-$(N).json"; else rm -f ".scratch/issue-$(N).json.tmp"; echo "issue-dump: gh could not return valid JSON for issue $(N)" >&2; exit 1; fi

ponytail-metrics: ## Print the ponytail trial's per-issue figures from git and GitHub (read-only): make ponytail-metrics N=<issue number>
	$(if $(call issue-number-ok,$(value N)),,$(error Usage: make ponytail-metrics N=<issue number>))
	@python3 -B scripts/ponytail_metrics.py "$$N"

sweep-due: ## Say whether a sweep is due (read-only; the sweep interval is set in the script)
	@python3 -B _docs/agent-kit/scripts/sweep-due.py

tier-check: ## Check a change against its tier's path limit: make tier-check TIER=<light|standard|heavy> [BASE=<commit>]
	$(if $(and $(filter 1,$(words $(value TIER))),$(filter light standard heavy,$(value TIER))),,$(error Usage: make tier-check TIER=<light|standard|heavy> [BASE=<commit>]))
	$(if $(findstring $$,$(value BASE)),$(error Usage: make tier-check TIER=<light|standard|heavy> [BASE=<commit>] - BASE must not contain $$))
	@bash _docs/agent-kit/scripts/check-tier.sh "$$TIER" "$$BASE"

# --- Stack -----------------------------------------------------------------

install: ## Install dependencies
	uv sync --locked --all-groups
	$(MAKE) frontend-install

test: ## Run all unit tests (Python, then frontend)
	uv run --locked pytest
	$(MAKE) frontend-test

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

# --- When the kit came with a MANIFEST.sha256 (a released kit) -------------

kit-check: ## Say whether the kit folder is pristine or a derivative of KIT_VERSION (read-only)
	@python3 -B _docs/agent-kit/scripts/kit-check.py

kit-other-chars := a b c d e f g h i j k l m n o p q r s t u v w x y z A B C D E F G H I J K L M N O P Q R S T U V W X Y Z 0 1 2 3 4 5 6 7 8 9 . _ / -
kit-other-strip = $(if $(2),$(call kit-other-strip,$(subst $(firstword $(2)),,$(1)),$(wordlist 2,$(words $(2)),$(2))),$(1))
kit-root-ok = $(and $(filter 1,$(words $(1))),$(if $(call kit-other-strip,$(1),$(kit-other-chars)),,ok),$(if $(subst x$(strip $(1))x,,x$(1)x),,ok),$(filter-out /% -%,$(1)),$(if $(findstring ..,$(1)),,ok))

kit-compare: ## List how one kit folder differs from another (read-only, no default roots): make kit-compare KIT_A=<folder> KIT_B=<folder>
	$(if $(call kit-root-ok,$(value KIT_A)),,$(error Usage: make kit-compare KIT_A=<folder> KIT_B=<folder> - each one word, a relative path with letters digits . _ / - only))
	$(if $(call kit-root-ok,$(value KIT_B)),,$(error Usage: make kit-compare KIT_A=<folder> KIT_B=<folder> - each one word, a relative path with letters digits . _ / - only))
	@python3 -B _docs/agent-kit/scripts/kit-compare.py "$$KIT_A" "$$KIT_B"

kit-baseline: ## Find the commit of a clone of the kit repo that a live kit with no manifest was copied from (read-only): make kit-baseline KIT=<live kit folder> SOURCE=<clone folder>
	$(if $(call kit-root-ok,$(value KIT)),,$(error Usage: make kit-baseline KIT=<live kit folder> SOURCE=<clone folder> - each one word, a relative path with letters digits . _ / - only))
	$(if $(call kit-root-ok,$(value SOURCE)),,$(error Usage: make kit-baseline KIT=<live kit folder> SOURCE=<clone folder> - each one word, a relative path with letters digits . _ / - only))
	@python3 -B _docs/agent-kit/scripts/kit-baseline.py "$$KIT" "$$SOURCE"

# A plain relative path: one word of letters, digits, . _ / - only, no $ and no "..".
# Reuses kit-other-chars and kit-other-strip above (no .scratch/ requirement here).
kit-path-ok = $(and $(filter 1,$(words $(1))),$(if $(call kit-other-strip,$(1),$(kit-other-chars)),,ok),$(if $(subst x$(strip $(1))x,,x$(1)x),,ok),$(if $(findstring ..,$(1)),,ok))

kit-feedback: ## Write a read-only feedback bundle under .scratch/ from a project's kit (reverse import): make kit-feedback PROJECT=<project root> KIT=<reference kit>
	$(if $(call kit-path-ok,$(value PROJECT)),,$(error Usage: make kit-feedback PROJECT=<project root> KIT=<reference kit> - PROJECT: one word, letters digits . _ / - only, no ..))
	$(if $(call kit-path-ok,$(value KIT)),,$(error Usage: make kit-feedback PROJECT=<project root> KIT=<reference kit> - KIT: one word, letters digits . _ / - only, no ..))
	@python3 -B _docs/agent-kit/scripts/kit-feedback.py "$$PROJECT" "$$KIT"

# --- Project targets (this project's own; add below) -------------------------

decisions: ## List the decision records with their Status word (proposed, approved or superseded)
	@for f in _docs/decisions/*.md; do s=$$(grep -m1 -i -o 'status: [a-z]*' "$$f" | cut -d' ' -f2); printf '%-11s %s  (%s)\n' "$${s:-MISSING}" "$$(head -n 1 "$$f" | sed 's/^# //')" "$$(basename "$$f")"; done

# --- API contract (issue #9) -------------------------------------------------

contract-check: ## Validate openapi.yaml as OpenAPI 3.1 (fails on an invalid file): make contract-check [FILE=<path>]
	$(if $(value FILE),$(if $(call kit-root-ok,$(value FILE)),,$(error Usage: make contract-check [FILE=<path>] - FILE: one word, a relative path, letters digits . _ / - only, not starting with - or /, no ..)))
	uv run --locked openapi-spec-validator "$(if $(value FILE),$(FILE),openapi.yaml)"

# --- Frontend (issue #27) ----------------------------------------------------

frontend-install: ## Install the frontend packages from frontend/bun.lock; fails if the lockfile would change
	cd frontend && bun install --frozen-lockfile

frontend-build: ## Build the frontend to static files in frontend/dist (mock off) and check the output
	cd frontend && VITE_USE_MOCK=false bun run build
	@test -f frontend/dist/index.html || { echo "frontend-build: frontend/dist/index.html is missing"; exit 1; }
	@if [ -e frontend/.output ] || [ -e frontend/.wrangler ] || [ -n "$$(find frontend/dist \( -name server -o -name wrangler.json -o -name nitro.json -o -name '_worker.js' -o -name '*.mjs' \) -print -quit)" ]; then echo "frontend-build: server, worker or Nitro output found (a stale frontend/.output or frontend/.wrangler counts: remove it)"; exit 1; fi
	@if grep -rIlE 'analyst-a-pass|analyst-b-pass|lead-pass|<script>alert\(' frontend/dist; then echo "frontend-build: a demo credential or hostile fixture is in the production build"; exit 1; fi
	@if grep -rIlE 'fonts\.googleapis\.com|fonts\.gstatic\.com' frontend/dist; then echo "frontend-build: the build still mentions a Google fonts host"; exit 1; fi
	@echo "frontend-build: frontend/dist has index.html, no server output, no demo data, no Google fonts host"

frontend-test: ## Run the frontend tests (Vitest); run make frontend-install first
	@test -x frontend/node_modules/.bin/vitest || { echo "frontend-test: frontend/node_modules/.bin/vitest is missing; run make frontend-install first"; exit 1; }
	cd frontend && bun run test

frontend-start: ## Start the frontend dev server in the background (mock on; log: .scratch/frontend.log; http://127.0.0.1:5173/)
	@mkdir -p .scratch
	@test -x frontend/node_modules/.bin/vite || { echo "frontend-start: frontend/node_modules/.bin/vite is missing; run make frontend-install first"; exit 1; }
	@if [ -f .scratch/frontend.pid ]; then pid=$$(cat .scratch/frontend.pid); case "$$pid" in ''|*[!0-9]*|0*) echo "frontend-start: .scratch/frontend.pid does not hold a plain pid; check it and remove it by hand"; exit 1;; esac; if kill -0 "$$pid" 2>/dev/null; then echo "frontend is already running (pid $$pid)"; exit 1; fi; fi
	@nohup sh -c 'cd frontend && exec node_modules/.bin/vite dev --strictPort' > .scratch/frontend.log 2>&1 & echo $$! > .scratch/frontend.pid
	@sleep 1; pid=$$(cat .scratch/frontend.pid); if ! kill -0 "$$pid" 2>/dev/null || grep -q '^State:[[:space:]]*Z' "/proc/$$pid/status" 2>/dev/null; then echo "frontend-start: the server exited at once; last lines of .scratch/frontend.log:"; tail -n 20 .scratch/frontend.log; rm -f .scratch/frontend.pid; exit 1; fi; echo "frontend started (pid $$pid); log: .scratch/frontend.log"

frontend-stop: ## Stop the dev server started by frontend-start
	@if [ ! -f .scratch/frontend.pid ]; then echo "frontend is not running"; exit 0; fi; \
	pid=$$(cat .scratch/frontend.pid); \
	case "$$pid" in ''|*[!0-9]*|0*) echo "frontend-stop: .scratch/frontend.pid does not hold a plain pid; nothing killed; check the file"; exit 1;; esac; \
	if ! kill -0 "$$pid" 2>/dev/null; then rm -f .scratch/frontend.pid; echo "frontend was not running (stale pid file removed)"; exit 0; fi; \
	if [ -r "/proc/$$pid/cmdline" ]; then cmd=$$(tr '\0' ' ' < "/proc/$$pid/cmdline"); elif command -v ps >/dev/null 2>&1; then cmd=$$(ps -o command= -p "$$pid"); else cmd=; fi; \
	case "$$cmd" in *node_modules/.bin/vite\ dev*) ;; *) echo "frontend-stop: pid $$pid does not look like the dev server (its command line is not the one frontend-start runs, or it cannot be read); nothing killed; stop it by hand if it is yours"; exit 1;; esac; \
	kill "$$pid" 2>/dev/null; \
	for i in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$$pid" 2>/dev/null || break; sleep 1; done; \
	if kill -0 "$$pid" 2>/dev/null; then echo "frontend (pid $$pid) did not stop within 10 s; not forced; pid file kept"; exit 1; fi; \
	rm -f .scratch/frontend.pid; echo "frontend stopped"

# --- Backend (issue #10) -----------------------------------------------------

backend-run: ## Start the backend in the background on localhost (SOCBOARD_PORT, default 8000; log: .scratch/backend.log)
	@mkdir -p .scratch
	@test -x .venv/bin/python || { echo "backend-run: .venv/bin/python is missing; run make install first"; exit 1; }
	@if [ -f .scratch/backend.pid ]; then pid=$$(cat .scratch/backend.pid); case "$$pid" in ''|*[!0-9]*|0*) echo "backend-run: .scratch/backend.pid does not hold a plain pid; check it and remove it by hand"; exit 1;; esac; if kill -0 "$$pid" 2>/dev/null; then echo "backend is already running (pid $$pid)"; exit 1; fi; fi
	@nohup sh -c 'exec .venv/bin/python -m socboard.server' > .scratch/backend.log 2>&1 & echo $$! > .scratch/backend.pid
	@sleep 2; pid=$$(cat .scratch/backend.pid); if ! kill -0 "$$pid" 2>/dev/null || grep -q '^State:[[:space:]]*Z' "/proc/$$pid/status" 2>/dev/null; then echo "backend-run: the server exited at once; last lines of .scratch/backend.log:"; tail -n 20 .scratch/backend.log; rm -f .scratch/backend.pid; exit 1; fi; echo "backend started (pid $$pid); log: .scratch/backend.log"

backend-stop: ## Stop the backend started by backend-run
	@if [ ! -f .scratch/backend.pid ]; then echo "backend is not running"; exit 0; fi; \
	pid=$$(cat .scratch/backend.pid); \
	case "$$pid" in ''|*[!0-9]*|0*) echo "backend-stop: .scratch/backend.pid does not hold a plain pid; nothing killed; check the file"; exit 1;; esac; \
	if ! kill -0 "$$pid" 2>/dev/null; then rm -f .scratch/backend.pid; echo "backend was not running (stale pid file removed)"; exit 0; fi; \
	if [ -r "/proc/$$pid/cmdline" ]; then cmd=$$(tr '\0' ' ' < "/proc/$$pid/cmdline"); elif command -v ps >/dev/null 2>&1; then cmd=$$(ps -o command= -p "$$pid"); else cmd=; fi; \
	case "$$cmd" in *-m\ socboard.server*) ;; *) echo "backend-stop: pid $$pid does not look like the backend (its command line is not the one backend-run starts, or it cannot be read); nothing killed; stop it by hand if it is yours"; exit 1;; esac; \
	kill "$$pid" 2>/dev/null; \
	for i in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$$pid" 2>/dev/null || break; sleep 1; done; \
	if kill -0 "$$pid" 2>/dev/null; then echo "backend (pid $$pid) did not stop within 10 s; not forced; pid file kept"; exit 1; fi; \
	rm -f .scratch/backend.pid; echo "backend stopped"
