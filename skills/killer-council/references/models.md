# Seating members on models

Keep each seat on the same model in every round. If a seat's model changes, a change in its score might come from the model rather than the work.

## Same model for all

Leave `model` off the Agent call, so members run on the session's model. If the user named one model ("all Sonnet"), pass it on every call: `opus`, `sonnet`, `haiku` or `fable`.

## Mix of Claude models

Spread the seats across the Agent tool's `model` values (`opus`, `sonnet`, `haiku`, `fable`), round-robin unless the user named a lineup. Put the holdout juror on the model most seats use, so that a gap between its score and the council's points to overfitting rather than to two models calibrating differently.

Smaller models are noisier judges. If a seat's score jumps around from round to round while its issues barely change, mention that in the report instead of chasing the noise.

## Mix of providers (T3 Code)

This needs T3 Code's delegation tools. Outside T3 Code, tell the user it isn't available here and offer the Claude mix instead.

1. Load the tools with ToolSearch: `select:mcp__t3-code__orchestrator_capabilities,mcp__t3-code__delegate_task`.
2. Call `orchestrator_capabilities` once in the thread, before the first delegation. If the user didn't name a lineup, show them what's available and confirm which seat goes to which model (a short AskUserQuestion works).
3. Claude seats stay on the Agent tool. For every other seat, call `delegate_task` with:
   - `task`: the brief text, exactly as written, and nothing else. The child sees only this, which is what keeps it blind.
   - `title`: `"<Seat> review"`, with no round number.
   - `role: "review"`, `mode: "wait"`.
   - `isolation: "shared"` (the default). The child has to read the same working files, including your uncommitted fixes; a worktree would only have the last commit.
   - `target`: the provider and model from `orchestrator_capabilities`.
4. Send a round's delegations and Agent calls in the same message, so they all run together. If a delegation returns before the child is done (for example after a timeout), follow the `next` step it returns instead of polling.
5. If a provider or model the user chose isn't available, tell them and ask what to do. Don't quietly substitute another one.
6. Save these scores with `"source": "delegate:<provider>"`.
