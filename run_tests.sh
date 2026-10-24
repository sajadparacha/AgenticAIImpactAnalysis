#!/usr/bin/env bash
# Run the full test suite. No Oracle database or API key is needed.
# After the run it prints a per-file results table and the list of failed tests.
#
# Usage:
#   ./run_tests.sh                        all tests
#   ./run_tests.sh tests/test_storage.py  one file
#   ./run_tests.sh -k approval -v         any pytest options
#   ./run_tests.sh --setup                create .venv and install dev dependencies first

set -euo pipefail

cd "$(dirname "$0")"

VENV=".venv"
PYTHON="$VENV/bin/python"

if [[ "${1:-}" == "--setup" ]]; then
  shift
  if [[ ! -x "$PYTHON" ]]; then
    echo "Creating $VENV ..."
    python3 -m venv "$VENV"
  fi
  "$PYTHON" -m pip install --quiet --upgrade pip
  "$PYTHON" -m pip install --quiet -e ".[dev]"
fi

if [[ ! -x "$PYTHON" ]]; then
  echo "No virtual environment at $VENV. Run: ./run_tests.sh --setup" >&2
  exit 1
fi

if ! "$PYTHON" -c "import pytest" 2>/dev/null; then
  echo "pytest is not installed in $VENV. Run: ./run_tests.sh --setup" >&2
  exit 1
fi

# Tests build their own settings. Drop OIA_* variables exported in this shell
# so a local Oracle or LLM configuration cannot leak into the run.
while IFS= read -r name; do
  unset "$name"
done < <(env | sed -n 's/^\(OIA_[A-Za-z0-9_]*\)=.*/\1/p')

REPORT="$(mktemp -t oia-tests.XXXXXX)"
trap 'rm -f "$REPORT"' EXIT

echo "Running tests with $("$PYTHON" --version) ..."
status=0
"$PYTHON" -m pytest -q --junitxml="$REPORT" -o junit_family=xunit1 "$@" || status=$?

if [[ -s "$REPORT" ]]; then
  "$PYTHON" - "$REPORT" <<'PY'
import sys
import xml.etree.ElementTree as ET
from collections import OrderedDict

color = sys.stdout.isatty()
GREEN, RED, YELLOW, BOLD, RESET = ("\033[32m", "\033[31m", "\033[33m", "\033[1m", "\033[0m") if color else ("",) * 5

root = ET.parse(sys.argv[1]).getroot()
files = OrderedDict()
problems = []

for case in root.iter("testcase"):
    name = case.get("name") or ""
    path = case.get("file") or ""
    if not path:
        parts = (case.get("classname") or "").split(".")
        while parts and parts[-1][:1].isupper():
            parts.pop()  # drop test class names
        path = "/".join(parts) + ".py" if any(parts) else "(unknown file)"
    row = files.setdefault(path, {"tests": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0, "time": 0.0})
    row["tests"] += 1
    row["time"] += float(case.get("time") or 0)
    outcome = "passed"
    for tag in ("failure", "error", "skipped"):
        node = case.find(tag)
        if node is not None:
            outcome = tag
            break
    if outcome == "failure":
        row["failed"] += 1
    elif outcome == "error":
        row["errors"] += 1
    elif outcome == "skipped":
        row["skipped"] += 1
    else:
        row["passed"] += 1
    if outcome in {"failure", "error"}:
        message = (node.get("message") or node.text or "").strip().splitlines()
        problems.append((outcome.upper(), f"{path}::{name}", message[0][:160] if message else ""))

headers = ("File", "Tests", "Passed", "Failed", "Errors", "Skipped", "Time", "Result")
rows = []
total = {"tests": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0, "time": 0.0}
for path, row in files.items():
    for key in total:
        total[key] += row[key]
    rows.append((path, row))

def result(row):
    if row["failed"] or row["errors"]:
        return f"{RED}FAIL{RESET}", 4
    return f"{GREEN}PASS{RESET}", 4

width = max([len(headers[0]), len("TOTAL")] + [len(path) for path, _ in rows])
line = "-" * (width + 58)

def fmt(label, row, bold=False):
    text, _ = result(row)
    start, end = (BOLD, RESET) if bold else ("", "")
    return (
        f"{start}{label:<{width}}  {row['tests']:>5}  {row['passed']:>6}  {row['failed']:>6}  "
        f"{row['errors']:>6}  {row['skipped']:>7}  {row['time']:>7.2f}s{end}  {text}"
    )

print()
print(f"{BOLD}Test results by file{RESET}")
print(line)
print(f"{headers[0]:<{width}}  {headers[1]:>5}  {headers[2]:>6}  {headers[3]:>6}  {headers[4]:>6}  {headers[5]:>7}  {headers[6]:>8}  {headers[7]}")
print(line)
for path, row in rows:
    print(fmt(path, row))
print(line)
print(fmt("TOTAL", total, bold=True))
print(line)

if problems:
    print()
    print(f"{RED}{BOLD}Failed tests ({len(problems)}){RESET}")
    for index, (kind, test, message) in enumerate(problems, start=1):
        print(f"{index:>3}. [{kind}] {test}")
        if message:
            print(f"     {YELLOW}{message}{RESET}")
else:
    print()
    print(f"{GREEN}{BOLD}No failed tests.{RESET}")
PY
fi

exit "$status"
