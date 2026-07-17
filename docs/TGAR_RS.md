# tgar-rs — future Rust implementation

TG Agent Relay’s **product name and operator paths stay here** (`tg-send.sh`,
`tg-poll.sh`, hooks, `relay.toml`). [**tgar-rs**](https://github.com/tzervas/tgar-rs)
is a separate repo for the Rust port; it does not replace this tree overnight.

## Roles

| Repo | Role |
|------|------|
| **tg-agent-relay** (this repo) | **Source of truth** for behavior, config, and releases until parity gates pass. |
| **tgar-rs** | Strangler implementation: `tgar` CLI and crates (`tgar-core`, `tgar-telegram`, …). |

New fixes and features land in **Python first** (#18 / #67). Rust work tracks the
module map in tgar-rs `docs/PORTING.md`.

## Cutover switch (planned)

Operators will select the implementation behind the same shell entrypoints:

```bash
# Default today and until a phase is stable:
export RELAY_IMPL=python

# Per-phase opt-in when dual-run smoke is green for that surface:
export RELAY_IMPL=rust
```

Details, phase table, rollback, and offline **`dual-run-smoke.sh`**:
[tgar-rs `docs/STRANGLER.md`](https://github.com/tzervas/tgar-rs/blob/main/docs/STRANGLER.md).

This is **orthogonal** to forcing legacy shell bodies:

```bash
export RELAY_PYTHON_SEND=0
export RELAY_PYTHON_POLL=0
```

See [DECISIONS.md](DECISIONS.md) D1 for the current Python-default send/poll path.

## Epic

Tracked under [#22](https://github.com/tzervas/tg-agent-relay/issues/22) (optional Rust
hotspots). No large Python rewrite is required for P22d — documentation and parity
tests only.