# Agent Abstraction-Layer Harness — Spec

## What this is

A test harness for measuring **at which level of abstraction an AI agent can
successfully complete a task**. The same task is presented to the agent through
three different interfaces, and we measure success rate at each.

The hypothesis being tested: agent capability is not a property of the model
alone, but of the model *plus the interface it is given*. A weaker model with a
well-designed API may outperform a stronger model driving a GUI.

## Why it matters

If agents only succeed at the scripting/API layer, then the product every
domain-tool vendor needs to build is a scripting surface designed for machines
rather than humans.

If agents succeed at the UI layer, then the moat around any specific application
is much thinner, because the agent is indifferent to which application it drives.

The harness is how you find out which is true.

## The three layers

| Layer | Agent sees | Agent emits | Characteristics |
|-------|-----------|-------------|-----------------|
| **UI** | Screenshots / DOM | Clicks, keystrokes | Universal, slow, brittle, poor error signal |
| **API** | Function signatures, docs | Code calling the API | Fast, deterministic, legible errors |
| **Kernel** | Primitive operations | Low-level op sequences | Max control, no guardrails, failures land on agent |

The same task must be expressible and solvable at all three layers, or the
comparison is meaningless.

## Core requirement: machine-verifiable output

The scorer must be deterministic. No LLM-as-judge in v1.

This is the constraint that makes the whole thing cheap to run, and it is the
constraint that should drive domain selection. A domain qualifies only if
"did it work?" can be answered by code.

## v1 domain: SVG / raster image manipulation

Chosen as a stand-in because it has the same structural properties as the
eventual target domain (CAD geometry) with none of the geometry-kernel pain.

- **Task example:** "Draw a red circle of radius 40 centred at (100, 100) on a
  200x200 blue background."
- **UI layer:** drive a minimal canvas web app (headless browser, screenshots in,
  click/drag out)
- **API layer:** expose a small drawing API (`draw_circle(x, y, r, colour)`),
  agent writes Python
- **Kernel layer:** agent emits raw SVG path data / direct pixel buffer ops
- **Scorer:** render output, pixel-diff against reference, pass if similarity
  above threshold

Task set should include deliberate difficulty gradient — simple shapes, then
compositions, then cases with overlap/occlusion where ordering matters.

## Eventual target domain: CAD geometry

Not in v1. Recorded here because the abstractions must not accidentally
preclude it.

- **UI layer:** drive FreeCAD or similar
- **API layer:** CadQuery / build123d
- **Kernel layer:** direct OCCT operations
- **Scorer:** watertight check, volume comparison, self-intersection check

Note: CAD is unusually well suited to this because correctness is genuinely
computable. That is rare and is the reason the eventual domain is worth the
effort.

## Architecture

Four components. Keep the seams clean — the whole point is swapping one part
while holding the others fixed.

### 1. Task
Data, not code. A task definition carries:
- an identifier and human-readable description
- the prompt/goal given to the agent
- whatever reference artifact the scorer needs
- a difficulty tier

Tasks must be layer-agnostic. The *same* task object is handed to any of the
three environments.

### 2. Environment (protocol)
Three implementations: `UIEnvironment`, `APIEnvironment`, `KernelEnvironment`.

Responsibilities:
- present the task to the agent in that layer's terms (tool definitions,
  available operations, initial observation)
- accept an agent action, execute it, return the resulting observation
  (including errors — error legibility is part of what is being measured)
- expose the final artifact for scoring
- reset cleanly between runs

### 3. Runner
The loop. Feed task in, take agent action, execute in environment, feed result
back, repeat until the agent declares completion or the turn budget is
exhausted. Records a full trace.

Needs a configurable turn limit — turns-to-completion is itself a metric worth
capturing, not just pass/fail.

### 4. Scorer (protocol)
Takes the final artifact plus the task's reference, returns pass/fail plus
whatever continuous measure is available (pixel similarity, volume delta).

## Agent interface

Abstract the agent behind a protocol too. Two implementations needed at minimum:

- **MockAgent** — scripted or trivially rule-based, so the plumbing can be
  tested without burning API calls. Build this first.
- **ModelAgent** — real model behind the same protocol.

Everything should be runnable end-to-end with the mock before any real model
is wired in.

## Output

A results table: model × layer × task → pass/fail, turns used, continuous score.
The headline artifact is the success-rate-by-layer comparison.

Persist raw traces. When something fails you want to read the transcript, and
the failure modes are the actually interesting output of this whole exercise.

## Build order

1. Task definitions + scorer for the SVG domain
2. MockAgent
3. Runner loop
4. APIEnvironment (simplest of the three, proves the shape)
5. KernelEnvironment
6. UIEnvironment (hardest — headless browser, screenshots)
7. ModelAgent, then actually run it

Do not build all three environments before the first end-to-end run works.
Get task → mock agent → API environment → scorer → result working first.

## Non-goals for v1

- LLM-as-judge scoring
- Multi-agent setups
- Cost/latency optimisation
- The CAD domain
- Any UI for viewing results — a CSV and raw traces are enough
