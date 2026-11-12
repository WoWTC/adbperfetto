import subprocess
import unittest

from adbperfetto.versioning import from_git


def fake_run(count="123", short="abc1234", dirty="", rc=0):
    def run(args, **kwargs):
        joined = " ".join(str(a) for a in args)
        if rc != 0:
            return subprocess.CompletedProcess(args, rc, "", "err")
        if "rev-list" in joined:
            return subprocess.CompletedProcess(args, 0, count + "\n", "")
        if "rev-parse" in joined:
            return subprocess.CompletedProcess(args, 0, short + "\n", "")
        return subprocess.CompletedProcess(args, 0, dirty, "")
    return run


class VersioningTests(unittest.TestCase):
    def test_clean(self):
        version, build = from_git("2.0", "C:/repo", run=fake_run())
        self.assertEqual(version, "2.0.123")
        self.assertEqual(build, "abc1234")

    def test_dirty(self):
        version, build = from_git(
            "2.0", "C:/repo", run=fake_run(dirty=" M x.py\n"))
        self.assertEqual(version, "2.0.123")
        self.assertEqual(build, "abc1234-dirty")

    def test_failure_fallback(self):
        version, build = from_git("2.0", "C:/repo", run=fake_run(rc=128))
        self.assertEqual((version, build), ("2.0.0", "unknown"))

    def test_exception_fallback(self):
        def run(args, **kwargs):
            raise OSError("no git")
        version, build = from_git("2.0", "C:/repo", run=run)
        self.assertEqual((version, build), ("2.0.0", "unknown"))

    def test_git_called_with_timeout_and_repo(self):
        seen = []

        def run(args, **kwargs):
            seen.append((args, kwargs))
            return subprocess.CompletedProcess(args, 1, "", "")

        from_git("9.9", "C:/repo", run=run)
        self.assertEqual(len(seen), 3)
        for _args, kwargs in seen:
            self.assertEqual(kwargs["cwd"], "C:/repo")
            self.assertEqual(kwargs["timeout"], 5)


if __name__ == "__main__":
    unittest.main()
