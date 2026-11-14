#!/usr/bin/env bash
# The Postgres job's no-leak check (#143): start the backend on the current SOCBOARD_DATABASE_URL,
# send one login, and require the generic 500 with no argument word in the response or the log.
set -euo pipefail
make backend-run
code=$(curl -sS -o .scratch/missing.json -w "%{http_code}" -H "Content-Type: application/json" \
  -d '{"username": "analyst-a", "password": "ci-analyst-a-password"}' http://127.0.0.1:8000/api/login)
make backend-stop
echo "--- response ($code)"
cat .scratch/missing.json
echo
echo "--- backend log"
cat .scratch/backend.log
echo "---"
test "$code" = "500"
grep -q '"internal_error"' .scratch/missing.json
grep -q "Could not connect to the database." .scratch/backend.log
status=0
for word in "$@"; do
  if grep -qF -- "$word" .scratch/missing.json .scratch/backend.log; then echo "leaked: $word"; status=1; fi
done
test "$status" = 0
echo "no part of the URL in the response or the backend log"
