#!/usr/bin/env python3
"""Bookkeeping and checks for a killer-ci run: tests that pass here but fail on CI, and flaky tests.

Commands:
  init            create the run folder: the test command, how its results are read, how many runs
  ci-read         read the CI config (runners, versions, env, commands, checkout depth); suggest conditions
  logs            read failing CI runs with gh, read-only (or a saved log), and pull out the failing tests
  conditions      list the conditions ci.py can recreate, what each imitates, and whether it works here
  run             run the tests in a fresh copy under a set of conditions, or under each condition alone
  hunt            run the suite many times under the same conditions: per-test counts and the flaky list
  bisect          find the smallest set of conditions that still breaks a test, then prove it
  sandbox         make a copy of the project under a failure's conditions, for one diagnoser agent
  candidate add   save a diagnoser's fix, and refuse it when it hides the failure instead of fixing it
  candidate show  print a candidate's diff and what the scan found
  verify          prove a fix: K runs in a row under the conditions, a run without them, the full suite
  land            commit proven fixes on a new local branch through a worktree, then check the branch
  status          the failures, the fixes, and whether the real project changed
  decide          record a call the user should check (with --allow, let a refused fix be verified)
  wait            record an outward action prepared but not done
  report          write report.md and summary.json
  clean           delete the run's sandboxes

A failure is reproduced when, in fresh copies of the project, the test fails under the conditions
and passes without them, twice each. A fix is proven when the test passes K runs in a row under
those conditions, K being the smallest number of runs the old failure rate would pass by chance
less than 1% of the time (ceil(ln 0.01 / ln(1 - rate)), at least 10, at most --max-runs), then
passes without them, and nothing that passed before fails. Agents write reply.json in their
sandbox's scratch folder.

Runs live in ~/.claude/killer-ci/<project>/<date>-<slug>/ and sandboxes in the temp folder;
KILLER_CI_HOME and KILLER_CI_SANDBOXES move them. KILLER_CI_GH names the gh to run (a JSON list
like ["gh"]) and KILLER_CI_YQ the yq ("none" reads YAML line by line). Standard library only.
Run `ci.py <command> --help` for options.
"""
import importlib.util
from pathlib import Path

# The commands live in ci_lib.py, next to this file. It's loaded as a module so that Python caches its
# bytecode; this file stays small because a script run directly is compiled every time.
_spec = importlib.util.spec_from_file_location("killer_ci_lib", Path(__file__).resolve().parent / "ci_lib.py")
lib = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lib)

if __name__ == "__main__":
    lib.main(doc=__doc__)
