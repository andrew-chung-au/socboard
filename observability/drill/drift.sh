#!/usr/bin/env bash
# The drill's drift check (#299): the planted defect must still apply to `main`'s files, and the existing
# Python suite must still pass with it. Called by observability/ci/check.sh (the `socboard observability`
# workflow), so an edit to socboard/cases.py, socboard/app.py or compose.yaml by another lane that breaks
# the patch, or a new test that catches the defect, fails that run before the drill is ever started.
#
#   bash observability/drill/drift.sh
#
# Works on a copy in a temporary folder: the checkout is never changed. The copy is the tracked and
# untracked-but-not-ignored files, made a git repository (some tests read git), then the patch is applied
# and `uv run --locked pytest` runs there: the Python half of `make test`. The frontend tests are left out
# because the defect touches no frontend file and the workflow installs no frontend packages.
# Exit 0 the patch applies and the suite passes; 1 the patch does not apply; otherwise pytest's exit code.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../.."
root="$(pwd)"
copy="$(mktemp -d)"
trap 'rm -rf "$copy"' EXIT

git ls-files -z --cached --others --exclude-standard | while IFS= read -r -d '' file; do
  [ -e "$file" ] && printf '%s\0' "$file"
done | xargs -0 cp --parents -t "$copy"

cd "$copy"
git init -q
git add -A
git -c user.name=drift -c user.email=drift@localhost commit -q -m "copy of the checkout"

if ! git apply --check "$root/observability/drill/defect.patch"; then
  echo "drift: observability/drill/defect.patch no longer applies to the checkout; refresh the patch (#299)" >&2
  exit 1
fi
git apply "$root/observability/drill/defect.patch"
echo "drift: the defect applies; running the existing Python suite with it (it must pass)"
# The two tests deselected here inspect the patch against the unpatched files; they cannot hold on a copy
# that already carries it, and the apply check above does their job.
uv run --locked pytest -q \
  --deselect tests/test_oncall_drill.py::test_defect_patch_applies_to_the_current_files \
  --deselect tests/test_oncall_drill.py::test_the_patch_check_fails_when_cases_py_drifts
echo "drift: the existing suite does not catch the defect, as the drill needs"
