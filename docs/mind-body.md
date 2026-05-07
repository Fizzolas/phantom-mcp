# Mind & Body — how to think about Phantom

Phantom is designed around a simple metaphor:

- **LM Studio is the mind.** The chat model you have loaded does the
  reasoning, planning, language, and decision-making. It is the part
  that "thinks".
- **Phantom is the body.** Phantom is everything the mind needs to
  actually exist on your computer:
  - **Senses** (screenshots, OCR, file reads, window list, system info)
  - **Hands** (mouse, keyboard, shell, file writes, process control)
  - **Memory** (facts, tasks, traces, lessons, chunked notes)
  - **Reflective scaffolding** (`agent_*` tools the mind can call to
    structure its own goals, plans, self-checks and risk reviews)

Phantom is **not** an autonomous agent. There is no hidden loop. Every
tool call — including reflection — comes from the mind in LM Studio.
What Phantom provides is well-shaped scaffolding so a small local model
does not have to invent goal tracking, planning, and self-reflection
from scratch on every single turn.

---

## What "embodied agency" means in practice

A useful agent on your PC is not just one that can click and type. It
also needs to:

1. **Hold a goal**, not just a single instruction.
2. **Make a plan** of small, observable steps.
3. **Choose the next action** with the most relevant facts and
   lessons in mind, not just whatever happens to be in its short
   conversation context.
4. **Check itself** — recursively, in the spirit of an ouroboros —
   before sending an answer or taking an action that is hard to undo.
5. **Compare what it expected vs what it observed**, and turn
   repeated successes/failures into reusable lessons.

Phantom gives the mind one tool for each of those, all in the
`agent_*` namespace.

---

## The cognition tools

Each of these is small, deterministic, and bounded by character caps so
your context window is never wasted on cognition state.

| Tool                          | What it does                                                           |
|-------------------------------|------------------------------------------------------------------------|
| `agent_goal_start`            | Start a goal; record acceptance criteria + constraints; pull relevant facts/lessons. |
| `agent_plan_set`              | Store an ordered list of concrete next steps for the goal.             |
| `agent_plan_advance`          | Mark the current step done/failed and move the cursor.                 |
| `agent_next_action`           | Return a compact packet: current step, top-K relevant facts, lessons, recent failures. |
| `agent_status`                | Same shape, but defaults to the most recent in-progress task.          |
| `agent_reflect`               | Run a self-check checklist on a draft answer or planned action.        |
| `agent_risk_check`            | Classify a proposed tool call: `go` / `caution` / `block`.             |
| `agent_checkpoint`            | Record an intent + expectation right before acting.                    |
| `agent_after_action_review`   | Compare expected vs observed; promote durable patterns into lessons.   |
| `agent_understand`            | Pre-process a goal/action/answer: intent, assumptions, missing info, reversibility, recommendation. |
| `agent_confidence_check`      | 0..100 score + band (`high`/`moderate`/`low`/`very_low`) + `go`/`caution`/`block`. Combines evidence, plan alignment, recent failure rate, and risk. |
| `agent_action_review`         | One-shot pre-action gate: combines `understand` + `risk_check` + `confidence_check` into a single verdict. |
| `agent_stuck_detect`          | Heuristic flag for thrashing — repeated failures, same tool too many times, no plan advancement. |
| `agent_replan`                | Replace the plan with new steps + write a reason to the task timeline. |
| `agent_recover`               | Read-only recovery packet for a stalled task — failures, lessons, primary suggested move. |

### A worked example

Suppose the user asks: *"Open my latest invoice from Downloads and tell
me the total."*

A well-behaved chat model should approximately do this:

1. `agent_goal_start(task_id="invoice_total", goal=..., acceptance="Total in dollars reported back to user")`
2. `agent_plan_set("invoice_total", [...])` — e.g. list folder, find newest PDF, OCR it, extract total, summarize.
3. `agent_next_action("invoice_total")` to get its current step + any saved facts about Downloads paths.
4. `agent_risk_check(tool="file_read", args={"path": "..."} )` before reading. (`go` here.)
5. `agent_checkpoint(...)` before each non-trivial action. Then the
   real tool call. Then `agent_after_action_review(...)`.
6. `agent_reflect(draft=..., kind="answer")` before sending the user
   their final answer. Use the returned questions to decide if anything
   needs verification.

The model is still doing all of the *thinking*. Phantom is just making
the structure cheap to maintain.

---

## What Phantom can and cannot do

**Can:**

- See your screen, read files, list windows, run shell commands, type and click.
- Remember things between sessions (everything under `data/phantom_memory/`).
- Help the model self-check via the `agent_*` tools.
- Refuse a tool call by surfacing a `block` from `agent_risk_check`.

**Cannot:**

- Take actions on its own without the chat model triggering them. There
  is no daemon. There is no scheduler.
- Replace the model. If you have not loaded a tool-capable model in LM
  Studio, none of this works.
- Promise that a model will always *use* the cognition tools well —
  that depends on the prompt and on the model's instruction-following
  ability. The `SYSTEM_PROMPT.md` in this repo nudges it toward the
  good pattern.

---

## Safety notes

- `agent_risk_check` is heuristic. It will flag obviously destructive
  shell commands and writes to system directories, but it is not a
  sandbox. The user is the final authority.
- Memory is plain JSON / JSONL on disk under `data/phantom_memory/`.
  Inspect, edit, or delete at any time.
- There is no network call inside cognition tools. The optional
  `use_lms=True` path in `memory_learn_from_traces` is the only place
  that contacts LM Studio, and it is opt-in.
