#!/usr/bin/env python3
"""Run Python's tests in another order: killer-ci's order condition.

  python ciorder.py unittest reverse 0 [the arguments for python -m unittest]
  python ciorder.py unittest random 7 discover -v
  python ciorder.py pytest random 7 [the arguments for python -m pytest]

It stands in for `python -m unittest` or `python -m pytest`, puts the current folder first on
sys.path as -m does (unless the interpreter runs with -P or -I), and changes the order of the
collected tests. reverse turns the whole order round. random shuffles the modules, then the
classes within each module, then the tests within each class, from the seed, so the same seed
gives the same order and class and module fixtures still run once each. ci.py copies this file
into each sandbox's ci/ folder. Standard library only (and pytest itself, for pytest).
"""
import os
import random
import sys


def arrange(items, key, mode, seed):
    """items in the new order; key(item) gives (module, class) so groups stay together."""
    if mode == "reverse":
        return list(reversed(items))
    rng = random.Random(seed)
    mods = {}
    for it in items:
        m, c = key(it)
        mods.setdefault(m, {}).setdefault(c, []).append(it)
    out = []
    names = list(mods)
    rng.shuffle(names)
    for m in names:
        classes = list(mods[m])
        rng.shuffle(classes)
        for c in classes:
            tests = list(mods[m][c])
            rng.shuffle(tests)
            out += tests
    return out


def run_unittest(mode, seed, args):
    import unittest

    def flatten(suite):
        for t in suite:
            if isinstance(t, unittest.TestSuite):
                yield from flatten(t)
            else:
                yield t

    def key(t):
        return type(t).__module__, type(t).__qualname__

    class Loader(unittest.TestLoader):
        def _arranged(self, suite):
            return self.suiteClass(arrange(list(flatten(suite)), key, mode, seed))

        def discover(self, *a, **k):
            return self._arranged(super().discover(*a, **k))

        def loadTestsFromNames(self, names, module=None):
            return self._arranged(super().loadTestsFromNames(names, module))

    unittest.main(module=None, argv=["python -m unittest", *args], testLoader=Loader())


def run_pytest(mode, seed, args):
    import pytest

    class Order:
        @pytest.hookimpl(trylast=True)
        def pytest_collection_modifyitems(self, session, config, items):
            def key(it):
                parts = it.nodeid.split("::")
                return parts[0], "::".join(parts[1:-1])
            items[:] = arrange(list(items), key, mode, seed)

    sys.exit(pytest.main(list(args), plugins=[Order()]))


def main():
    if len(sys.argv) < 4 or sys.argv[1] not in ("unittest", "pytest") or sys.argv[2] not in ("reverse", "random"):
        sys.exit("usage: ciorder.py unittest|pytest reverse|random <seed> [arguments]")
    kind, mode, seed, args = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4:]
    if not getattr(sys.flags, "safe_path", False) and not sys.flags.isolated:
        sys.path[0] = os.getcwd()   # as python -m puts it, instead of this file's folder
    if kind == "unittest":
        run_unittest(mode, seed, args)
    else:
        run_pytest(mode, seed, args)


if __name__ == "__main__":
    main()
