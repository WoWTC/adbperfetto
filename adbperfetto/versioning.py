"""Version derived from git: <VERSION_BASE>.<commit count> + short hash.

Source checkouts compute it live; the packaged exe uses _buildinfo.py
written by tools/write_buildinfo.py so it never shells out to git.
"""

import subprocess

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _git(run, repo_dir, args):
    return run(["git", *args], cwd=str(repo_dir),
               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
               text=True, timeout=5, creationflags=CREATE_NO_WINDOW)


def from_git(base: str, repo_dir, run=subprocess.run):
    """Return (version, build) for `repo_dir` via three git calls.

    build is the short HEAD hash plus "-dirty" when tracked files have
    uncommitted changes. Any failure falls back to (<base>.0, "unknown").
    """
    try:
        count = _git(run, repo_dir, ["rev-list", "--count", "HEAD"])
        short = _git(run, repo_dir, ["rev-parse", "--short", "HEAD"])
        dirty = _git(run, repo_dir,
                     ["status", "--porcelain", "--untracked-files=no"])
        if count.returncode != 0 or short.returncode != 0:
            raise RuntimeError("git failed")
        build = short.stdout.strip() or "unknown"
        if dirty.returncode == 0 and dirty.stdout.strip():
            build += "-dirty"
        return f"{base}.{count.stdout.strip() or '0'}", build
    except Exception:
        return f"{base}.0", "unknown"
