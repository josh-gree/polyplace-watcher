# Clean-room implementation brief

This branch contains a **specification and its test suite, but no
implementation** — `src/polyplace_watcher/` is an empty stub on purpose.
A previous implementation exists elsewhere; you must not look at it.

## The task

Implement `src/polyplace_watcher/` so that:

```sh
uv run pytest spec_tests
```

passes, **without modifying anything in `spec_tests/` or `SPEC.md`**.

## Read first, in this order

1. `SPEC.md` — what the system is and every behavior it must have. The
   numbered invariants (§2 especially) are the design; read them as such.
2. `spec_tests/README.md` — the pinned API surface (top-level package
   exports, the ASGI entrypoint, env vars, the one sanctioned white-box
   hook) and how the suite is organized. Everything not pinned there is
   your design freedom.
3. The test files themselves — every test's docstring cites the spec
   sentence it enforces.

## Rules

- **Do not consult the old implementation.** No reading other branches, no
  `git log`/`git show` archaeology on `src/` or `tests/`. The point of this
  exercise is an implementation derived from the spec alone.
- **Frozen, must not change:** the deploy entrypoint
  (`uvicorn polyplace_watcher.app:app` — used by `Justfile`, `Dockerfile`,
  Fly), the `/grid` wire format, the `/ws` message shape, and the env-var
  names in SPEC §7.
- **Yours to change:** everything under `src/polyplace_watcher/`, and
  `pyproject.toml` dependencies (add or remove as your design needs — the
  current list reflects the old implementation, not a requirement).

## Workflow hint

The suite is layered. Make the pure tests pass first, then move outward:

```sh
uv run pytest spec_tests -m "not integration"   # core fold + wire format
uv run pytest spec_tests                        # full (spawns anvil)
```

Integration tests need `anvil` on PATH (Foundry) and exercise the real
chain → watcher → HTTP/WS path with zero mocks. They are slower; don't
start there.

## The quality bar

Passing tests is necessary, not sufficient. The reason this rewrite exists
is **legibility**: the goal is the intrinsic complexity of SPEC.md and
nothing else. Concretely:

- The code should read like the spec is organized: a small pure core
  (events → grid fold, §2), the frozen wire codec (§5), and the
  time-and-failure machinery (§4) arranged legibly around them.
- SPEC §8 is a logging *budget*, not a floor — lifecycle events only.
- When in doubt between clever and boring, choose boring. The audience is
  a competent software engineer reading this cold in six months.

## Definition of done

1. `uv run pytest spec_tests` fully green.
2. `just watcher` against a local anvil (from `../polyplace-contracts`:
   `just anvil`) serves a working `/grid`, `/ws`, `/health`.
3. You can defend every line as required by a sentence in SPEC.md.
