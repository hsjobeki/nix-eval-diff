#!/usr/bin/env python3
"""Compare nix-instantiate evaluation stats between two git revisions of nixpkgs."""

import argparse
import json
import os
import subprocess
import sys
import tempfile

NIXPKGS_REPO = "https://github.com/NixOS/nixpkgs.git"

BENCHMARKS = {
    "hello": {
        "label": "hello.drvPath",
        "expr": "(import ./. {}).hello.drvPath",
        "strict": False,
    },
    "nixos": {
        "label": "NixOS toplevel",
        "expr": """
            (import ./nixos {
              configuration = {
                boot.loader.grub.device = "/dev/sda";
                fileSystems."/".device = "/dev/sda1";
                fileSystems."/".fsType = "ext4";
              };
            }).config.system.build.toplevel.name
        """,
        "strict": True,
    },
}

# These keys are non-deterministic (timing, GC) and should not be flagged as regressions.
NOISY_KEYS = {"cpuTime", "time.cpu", "time.gc", "time.gcFraction", "gc.cycles", "gc.heapSize"}

CACHE_DIR = os.path.join(os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")), "nix-eval-diff")


def ensure_repo() -> str:
    """Ensure a bare repo exists in the cache dir, return its path."""
    repo_path = os.path.join(CACHE_DIR, "nixpkgs.git")
    if not os.path.isdir(repo_path):
        os.makedirs(CACHE_DIR, exist_ok=True)
        subprocess.run(
            ["git", "init", "--bare", "--quiet", repo_path],
            check=True,
        )
        subprocess.run(
            ["git", "remote", "add", "origin", NIXPKGS_REPO],
            cwd=repo_path,
            check=True,
            capture_output=True,
        )
    return repo_path


def fetch_ref(repo_path: str, refspec: str, label: str):
    """Fetch a single refspec from origin (shallow, depth=1)."""
    print(f"Fetching {label} ...", file=sys.stderr, end="", flush=True)
    subprocess.run(
        ["git", "fetch", "--quiet", "--depth=1", "origin", refspec],
        cwd=repo_path,
        check=True,
    )
    print(" done.", file=sys.stderr)


def resolve_rev(repo_path: str, rev: str) -> str:
    """Resolve a revision to its short hash + description."""
    result = subprocess.run(
        ["git", "log", "-1", "--format=%h %s", rev],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else rev


def eval_command(bench: dict) -> str:
    """Return the shell command string for display."""
    parts = [
        "NIX_SHOW_STATS=1",
        "NIX_SHOW_STATS_PATH=stats.json",
        "nix-instantiate",
        "--eval",
    ]
    if bench["strict"]:
        parts.append("--strict")
    expr = " ".join(bench["expr"].split())
    parts += ["-E", f"'{expr}'"]
    return " \\\n  ".join(parts)


def run_eval(worktree: str, bench: dict) -> dict:
    """Run nix-instantiate in a worktree and return the stats JSON."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        stats_path = f.name

    env = {**os.environ, "NIX_SHOW_STATS": "1", "NIX_SHOW_STATS_PATH": stats_path}
    cmd = ["nix-instantiate", "--eval"]
    if bench["strict"]:
        cmd.append("--strict")
    cmd += ["-E", bench["expr"]]

    result = subprocess.run(
        cmd,
        cwd=worktree,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace").strip()
        print(f"  WARNING: nix-instantiate exited {result.returncode}", file=sys.stderr)
        if stderr:
            print(f"  stderr: {stderr[:500]}", file=sys.stderr)

    try:
        with open(stats_path) as f:
            stats = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"  ERROR: could not read stats: {e}", file=sys.stderr)
        if os.path.exists(stats_path):
            os.unlink(stats_path)
        return {}

    os.unlink(stats_path)
    return stats


def flatten(d: dict, prefix: str = "") -> dict:
    """Flatten a nested dict into dot-separated keys."""
    out = {}
    for k, v in d.items():
        key = f"{prefix}{k}" if not prefix else f"{prefix}.{k}"
        if isinstance(v, dict):
            out.update(flatten(v, key))
        elif isinstance(v, (int, float)):
            out[key] = v
    return out


def compare(old: dict, new: dict) -> list[tuple[str, float, float, str, bool]]:
    """Return list of (key, old_val, new_val, delta_str, is_noisy) sorted by key."""
    all_keys = sorted(set(old) | set(new))
    rows = []
    for k in all_keys:
        o = old.get(k, 0)
        n = new.get(k, 0)
        if o == n == 0:
            continue
        noisy = k in NOISY_KEYS
        if o != 0:
            pct = (n - o) / abs(o) * 100
            delta = f"{pct:+.1f}%"
        else:
            delta = "new"
        rows.append((k, o, n, delta, noisy))
    return rows


def fmt_num(v: float) -> str:
    if isinstance(v, float) and not v.is_integer():
        return f"{v:.3f}"
    return f"{int(v):,}"


def md_table(rows: list[tuple[str, float, float, str, bool]]) -> str:
    """Format rows as a markdown table."""
    if not rows:
        return "_no stats_\n"

    lines = []
    lines.append(f"| {'Stat':<40} | {'Base':>14} | {'Rev':>14} | {'Delta':>8} |")
    lines.append(f"|{'-' * 42}|{'-' * 16}|{'-' * 16}|{'-' * 10}|")
    for key, o, n, delta, _noisy in rows:
        lines.append(f"| {key:<40} | {fmt_num(o):>14} | {fmt_num(n):>14} | {delta:>8} |")
    return "\n".join(lines) + "\n"


def checkout_worktree(repo_path: str, rev: str) -> str:
    """Create a temporary git worktree for the given revision."""
    tmp = tempfile.mkdtemp(prefix="nix-stats-")
    subprocess.run(
        ["git", "worktree", "add", "--detach", tmp, rev],
        cwd=repo_path,
        check=True,
        capture_output=True,
    )
    return tmp


def remove_worktree(repo_path: str, path: str):
    subprocess.run(
        ["git", "worktree", "remove", "--force", path],
        cwd=repo_path,
        capture_output=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="master", help="Base revision (default: master)")

    rev_group = parser.add_mutually_exclusive_group(required=True)
    rev_group.add_argument("--rev", help="Git revision to compare against base")
    rev_group.add_argument("--pr", type=int, help="GitHub PR number to compare against base")

    parser.add_argument(
        "--bench",
        choices=list(BENCHMARKS) + ["all"],
        default="all",
        help="Which benchmark to run (default: all)",
    )
    args = parser.parse_args()

    repo_path = ensure_repo()

    # Fetch base ref
    fetch_ref(repo_path, f"+refs/heads/{args.base}:refs/heads/{args.base}", args.base)

    if args.pr:
        fetch_ref(repo_path, f"+pull/{args.pr}/head:refs/prs/{args.pr}", f"PR #{args.pr}")
        rev = f"refs/prs/{args.pr}"
        rev_label = f"PR #{args.pr}"
    else:
        fetch_ref(repo_path, args.rev, args.rev)
        rev = "FETCH_HEAD"
        rev_label = args.rev

    benches = BENCHMARKS if args.bench == "all" else {args.bench: BENCHMARKS[args.bench]}

    base_desc = resolve_rev(repo_path, args.base)
    rev_desc = resolve_rev(repo_path, rev)

    print(f"# nix-instantiate stats: `{args.base}` vs `{rev_label}`\n")
    print(f"- **Base**: `{args.base}` ({base_desc})")
    print(f"- **Rev**:  `{rev_label}` ({rev_desc})\n")

    base_wt = checkout_worktree(repo_path, args.base)
    rev_wt = checkout_worktree(repo_path, rev)

    try:
        for _, bench in benches.items():
            print(f"## {bench['label']}\n")
            print(f"```\n{eval_command(bench)}\n```\n")

            sys.stdout.flush()
            print(f"Running on `{args.base}` ...", end="", flush=True, file=sys.stderr)
            base_stats = flatten(run_eval(base_wt, bench))
            print(f" done. Running on `{rev_label}` ...", end="", flush=True, file=sys.stderr)
            rev_stats = flatten(run_eval(rev_wt, bench))
            print(" done.\n", file=sys.stderr)

            rows = compare(base_stats, rev_stats)
            changed = [r for r in rows if r[3] != "+0.0%" and not r[4]]
            noisy = [r for r in rows if r[4]]
            unchanged = [r for r in rows if r[3] == "+0.0%" and not r[4]]

            if changed:
                print(f"### Changed ({len(changed)})\n")
                print(md_table(changed))
            else:
                print("### No deterministic changes\n")

            if noisy:
                print(f"\n<details><summary>Timing / non-deterministic ({len(noisy)})</summary>\n")
                print(md_table(noisy))
                print("</details>\n")

            if unchanged:
                print(f"\n<details><summary>Unchanged ({len(unchanged)})</summary>\n")
                print(md_table(unchanged))
                print("</details>\n")
    finally:
        remove_worktree(repo_path, base_wt)
        remove_worktree(repo_path, rev_wt)


if __name__ == "__main__":
    main()
