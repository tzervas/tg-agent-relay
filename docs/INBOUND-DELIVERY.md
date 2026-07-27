# Inbound delivery — why messages queued, and what changed

Operator doc for the spool/replay fix. Read this if inbound Telegram messages
stopped reaching a running agent session, or appeared all at once in a burst
long after you sent them.

## The failure

Two individually correct pieces combined into a message sink.

`scripts/ensure-inbound.sh` holds every backend FIFO open `RDWR` — a
*keepalive* — so `tg-poll` never hits `ENXIO` when no agent is attached yet.
That is deliberate and documented.

`deliver_to_backend` then treated a successful write as delivery.

With a keepalive attached there is always a reader from the kernel's point of
view, so the write **always succeeds** — into the 64K pipe buffer. Nothing
consumes it. The message is not delivered to the agent; it waits in the buffer
until some later Monitor attaches and drains the whole backlog as one burst, or
until the buffer fills. After that every further write fails, and each failed
message was **dropped permanently** — there was no spool, no retry, no ack
anywhere in the codebase.

`poll.py` already detected this and emitted `message_orphaned`. But it wrote
into the void *first*, emitted `message_delivered`, and only then checked for a
reader — so every orphaned message was also counted as delivered, and detection
never became recovery. `tg-poll.sh` was worse: no reader check at all, and
`message_delivered` emitted unconditionally, including on the failure branch
that had just emitted `deliver_skip`.

**Reading the old metrics, a message that no agent ever saw looked delivered.**

## What changed

Delivery is now gated on an attested reader, and anything undeliverable is
persisted instead of written into a buffer nobody drains.

- **`tg_agent_relay/spool.py`** — durable per-backend spool at
  `.run/spool/<backend>/`. Ordered replay, atomic claim so concurrent drains
  never double-deliver, emit-failure leaves the line pending, stale claims from
  a dead drainer are reclaimed, and it is bounded (`RELAY_SPOOL_MAX`, default
  1000) with a loud `spool_overflow` metric rather than silent truncation.
- **`poll.py` / `tg-poll.sh`** — check for a real agent reader *before* writing.
  No reader → spool for replay. `message_delivered` is emitted only when a
  reader was attested *and* the write landed. A failed write spools rather than
  drops.
- **`adapters/backend-fifo-reader.sh`** — drains the spool on attach and on an
  interval, so a session that comes up replays what it missed, in order.
- **`scripts/doctor-inbound.sh`** — reports spool depth per backend.

Keepalives are unchanged and still needed; they are just no longer mistaken for
delivery.

## Verifying on a live instance

```bash
cd ~/.claude/telegram-bridge      # or wherever the bridge is deployed
git pull

bash scripts/doctor-inbound.sh
```

Read the `agent_readers` column. This is the whole diagnosis:

| Output | Meaning |
|---|---|
| `agent_readers=0 … [ORPHAN]` | **No session is attached.** Inbound is being spooled, not delivered. Attach a Monitor. |
| `agent_readers=1+` | A real Monitor is attached; live delivery works. |
| `spooled=N` | `N` messages held for replay. Non-zero is fine and expected — it is what the spool is for. A depth that never *falls* means nothing is draining it. |

Attach a Monitor for the backend (this is the step that is usually missing —
`ensure-inbound.sh` deliberately does **not** do it, because a process that
drained the FIFO into a log would steal messages from the agent):

```bash
adapters/backend-fifo-reader.sh ~/.claude/telegram-bridge/sessions/fleet.fifo
```

In an agent harness, run that as the session's Monitor / event source. On
startup it replays the backlog, then streams live lines.

Drain a backlog by hand without attaching:

```bash
python -m tg_agent_relay.spool count fleet
python -m tg_agent_relay.spool drain fleet
```

## Metrics

| Event | Means |
|---|---|
| `message_delivered` | Reader attested **and** write landed. Now trustworthy. |
| `message_orphaned … spooled=1` | No reader; held for replay. Recoverable. |
| `message_orphaned … spooled=0` | No reader **and** the spool was unwritable. Investigate — this is the only remaining loss path. |
| `deliver_skip … reason=fifo_timeout spooled=1` | Write failed after a reader was attested; held for replay. |
| `message_spooled_ordered … reason=drain_in_flight` | A reader is attached, but a replay was still draining, so this line was appended behind the backlog instead of jumping the queue. |
| `spool_drained count=N` | A reader replayed `N` messages. |
| `spool_overflow` | Cap exceeded, oldest dropped. Raise `RELAY_SPOOL_MAX` or attach a reader. |

## Tuning

| Env | Default | Purpose |
|---|---|---|
| `RELAY_SPOOL_MAX` | `1000` | Max held lines per backend before oldest are dropped. |
| `RELAY_SPOOL_POLL_SECS` | `2` | Reader drain interval. |
| `RELAY_SPOOL_CLAIM_TTL` | `300` | Seconds before a dead drainer's claim is reclaimed. |
| `RELAY_SPOOL_REPLAY=0` | — | Disable replay in the reader (FIFO-only, legacy). |

## Ordering

Messages are delivered in arrival order across both channels. That is not
automatic: once a reader attaches, a direct FIFO write is read immediately
while spooled lines wait for the next drain tick, so a new message could
otherwise reach the agent *before* the backlog it follows. Both pollers
therefore append to the spool whenever a replay is still in flight
(`message_spooled_ordered`), and resume direct writes once the spool is empty.

The remaining loss path is a spool that cannot be written at all (full disk,
permissions). There the pollers fall back to the old best-effort FIFO write —
the kernel buffer is a poor destination, but it is recoverable by a later
reader, and dropping is not. That case is visible as `spooled=0`.

## Two implementation notes

**No signal trap in `backend-fifo-reader.sh`.** The main loop blocks in
`read < "$FIFO"`, and bash defers a trap until the current foreground command
returns — which never happens while no writer is attached. A `TERM`/`INT` trap
therefore makes an idle Monitor unkillable by anything short of `SIGKILL`. The
background drain child watches for its parent's death and exits on its own
instead, so cleanup never depends on signal handling in the parent.

**Reader detection fails open.** If the interpreter or
`lib/fifo_agent_readers.py` is unavailable, both pollers assume a reader is
present. A false "no reader" would divert *every* message to the spool and stall
live delivery, which is worse than the orphan it would guard against.
