# nix-eval-diff

Compare `nix-instantiate` evaluation stats between two nixpkgs revisions or PRs. Detect evaluation regressions like increased attribute count or higher memory usage.

## Install and run

The flake provides a wrapped binary with `git`, `nix`, and Python 3 included.

```bash
nix run github:hsjobeki/nix-eval-diff -- --pr 12345
```

## Usage

```bash
# Compare a PR against master
nix-eval-diff --pr 12345

# Compare a specific revision against master
nix-eval-diff --rev some-branch-or-sha

# Use a different base
nix-eval-diff --base nixos-unstable --rev some-sha

# Run a specific benchmark only (hello or nixos)
nix-eval-diff --pr 12345 --bench hello
```

## Benchmarks

| Name    | Description                                  |
|---------|----------------------------------------------|
| `hello` | Evaluate `hello.drvPath` (non-strict)        |
| `nixos` | Evaluate a minimal NixOS toplevel (strict)   |

## How it works

- Maintain a bare nixpkgs clone in `~/.cache/nix-eval-diff/`.
- Shallow-fetch the requested revisions.
- Check out temporary worktrees for base and target.
- Run `nix-instantiate --eval` with `NIX_SHOW_STATS=1` in each worktree.
- Compare the stats and output a markdown table with deltas.
