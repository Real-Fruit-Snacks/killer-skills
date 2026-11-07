#!/usr/bin/env python3
"""Bookkeeping for a killer-upgrade run: dependency upgrades, each one proven by the project's own tests.

Commands:
  init      find the ecosystems (manifests, lockfiles, package managers), runtimes, CI actions and
            base images, and suggest the install, build and test commands
  custom    describe an ecosystem upgrade.py doesn't know: a command that lists what's out of date as
            JSON, and a command that applies one upgrade
  baseline  install, build and test in a fresh sandbox (the tests three times) and record what passes
  outdated  ask each ecosystem's own tool what's out of date; classify each upgrade patch, minor or major
  plan      group the upgrades into steps in plan.json: patch and minor, dev, each major, runtimes, actions
  add       add a step by hand: an edit (file, old text, new text) or a command
  hold      keep a package, or a whole step, at its version, with the reason
  try       apply a step on top of the accepted ones in a fresh sandbox, then install, build and test it
  bisect    find which upgrades break a failing batch, then split the batch
  sandbox   make a sandbox for an agent: the accepted steps and one step applied and installed
  migrate   check a migrator's code changes for a step with a fresh try
  notes     record a changelog reader's reply, rerunning any reproduction it gave
  status    every step and where it stands, whether the project changed, and what to do next
  land      commit each accepted step on a new local branch, then check the branch in a fresh sandbox
  decide    record a call the user should check
  wait      record an outward action that's prepared but not done
  report    write report.md and summary.json
  clean     delete the run's sandboxes and its package cache

An upgrade counts only when try (or bisect, migrate or land) has shown in a fresh sandbox that what
passed at the baseline still passes with it. Agents write reply.json in their sandbox's scratch folder.

Runs live in ~/.claude/killer-upgrade/<project>/<date>-<slug>/ and sandboxes in the temp folder;
KILLER_UPGRADE_HOME and KILLER_UPGRADE_SANDBOXES move them. Standard library only.
Run `upgrade.py <command> --help` for options.
"""
import importlib.util
from pathlib import Path

# The commands live in upgrade_lib.py, next to this file. It's loaded as a module so that Python
# caches its bytecode; this file stays small because a script run directly is compiled every time.
_spec = importlib.util.spec_from_file_location("killer_upgrade_lib", Path(__file__).resolve().parent / "upgrade_lib.py")
lib = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lib)

if __name__ == "__main__":
    lib.main(doc=__doc__)
