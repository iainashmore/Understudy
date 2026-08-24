> **This repository holds two projects.** `flowrunner/` is the Prompt Flow
> Recorder & Runner — see [flowrunner/README.md](flowrunner/README.md). The rest
> of this file describes the abstraction-layer harness, paused at step 5 of 7.

# Agent Abstraction-Layer Harness

Measures **at which level of abstraction an AI agent can complete a task**. The
same task is presented through three interfaces — UI, API, Kernel — and the
success rate is compared across them.

The hypothesis: agent capability is a property of the model *plus the interface
it is given*, not the model alone.

See `HARNESS_SPEC.md` for the full design.

## Status

Build steps 1–5 are done: **tasks, scorer, mock agents, the runner loop, and
two of the three layers**. The oracle passes all nine tasks at both the API and
kernel layers at score 1.000, so the harness is sound on everything built so
far. No real model has been run, so there are no capability results yet.

| # | Component | State |
|---|-----------|-------|
| 1 | Task definitions + scorer | done |
| 2 | MockAgent | done |
| 3 | Runner loop | done |
| 4 | APIEnvironment | done |
| 5 | KernelEnvironment | done |
| 6 | UIEnvironment | next |
| 7 | ModelAgent | |

## Layout

```
harness/task.py        Task, TaskBrief, Canvas, Difficulty, ScoringConfig
harness/scorer.py      Scorer protocol + PixelScorer
harness/image.py       artifact normalisation (PNG -> RGB array), blur
harness/interaction.py Layer, Action, Observation, Operation, Interface
harness/environment.py Environment protocol
harness/environments/  layer implementations + registry (api, kernel)
harness/agents/        Agent protocol + the mocks
harness/runner.py      the loop, RunResult, trace writing
harness/results.py     results CSV + success-rate-by-layer summary
harness/reference.py   golden recipe -> SVG -> PNG   (authoring only)
tasks/*.json           the task set, one file each
references/*.png       generated golden images
tools/generate_references.py
tools/run_sweep.py
tests/                 unit tests plus the threshold calibration
```

## Running

```bash
pip install -r requirements.txt
pytest                                        # 421 tests
python3 tools/generate_references.py
tools/run_sweep.py --check  # references current?
python3 tools/generate_references.py
tools/run_sweep.py          # regenerate after a recipe edit
```

## The task set

Nine tasks across the spec's three difficulty tiers.

| Tier | Tasks | What it adds |
|------|-------|--------------|
| simple | red circle, green rectangle, yellow triangle | one shape, explicit coordinates |
| composite | three circles, house, checkerboard | repetition, alignment, arithmetic |
| occlusion | overlapping circles, stacked squares, ring punch-out | draw order changes the picture |

Every prompt states the canvas size, background, all coordinates, and the
convention that y increases downwards. Nothing is left for the agent to guess,
because a pixel scorer cannot distinguish a defensible interpretation from a
wrong one.

Tasks are pure data and layer-agnostic: `Task` carries no hint of how the
drawing is to be performed, and a test fails the build if a prompt mentions
clicks, code, SVG, or any other layer's action space.

### Golden recipes are not part of the task

Each `tasks/*.json` also holds a `golden` recipe — the shape list the reference
image is rendered from. `load_task()` drops it; only the authoring tool reads
it. An environment that could see the recipe would be handed a shape list,
which is roughly the API layer's action space, i.e. the answer.

## Agents

`Agent` is a protocol: `reset(brief, interface)` once per run, then
`act(observation)` per turn until it returns `done` or the runner's budget runs
out. Implementations keep their own history.

The mocks exist so the loop can be exercised without spending API calls, and
they cover misbehaviour as well as the happy path — the failure modes are the
interesting output, so the runner has to handle them before a real model starts
producing them.

| Mock | Exercises |
|------|-----------|
| `ScriptedAgent` | a fixed plan; also covers wrong drawings and invalid operations, since those are just scripts |
| `NoOpAgent` | declares victory having drawn nothing — must fail at every layer |
| `LoopingAgent` | never stops, so the turn budget has to |
| `CrashingAgent` | raises mid-run; a timeout must fail one run, not the sweep |
| `ReactiveAgent` | reads the observation and changes its mind |

`retrying_agent()` builds the canonical feedback-loop case: emit something
invalid, read the error, correct it. Error legibility is one of the things being
measured, so at least one mock has to close that loop.

Every agent records the observations it was shown. That is not bookkeeping: a
scripted agent ignores its observations by construction, so without the record a
runner feeding back empty or stale observations would still pass every test.

### The oracle is a diagnostic, not a measurement

`oracle_agent(recipe, translate)` is handed the golden recipe and a
layer-specific translator. Its job is to prove an environment can produce a
passing artifact at all — if the oracle cannot pass a task through a layer, that
layer is broken, and a real agent failing there would look like a capability
result when it is a harness bug. Worth running against each new environment.
Oracle runs carry `is_oracle = True` and stay out of the results table.

### What an agent is allowed to see

`Task` holds the path to the reference image, so agents and environments get a
`TaskBrief` — id, prompt, canvas — and only the scorer gets the reference. A
model handed a file path is perfectly capable of reading it. Same reasoning as
stripping the golden recipe.

The `Interface` an environment hands over describes the operations available at
that layer and nothing else. Its preamble must never mention the task: the
moment one layer's framing carries a hint the others lack, the comparison stops
being about abstraction.

## The API layer

Structured operations, not executed Python: no sandbox, and the error signal
stays clean — which matters because error legibility is one of the properties
being compared. It is also the honest analogue of the eventual CAD target, where
the API layer is CadQuery-style calls.

```
draw_circle(cx, cy, r, fill)          draw_polygon(points, fill)
draw_rect(x, y, width, height, fill)  draw_line(x1, y1, x2, y2, stroke, stroke_width)
draw_ellipse(cx, cy, rx, ry, fill)    clear()
```

Shapes paint in order, later over earlier. The preamble says so, because that is
a fact about the interface and every layer documents its own semantics —
withholding it here while the UI's canvas makes it self-evident would be the
asymmetry, not the reverse.

Arguments are described once, in a spec used for both the documentation the
agent sees and the validation it is held to, so the two cannot drift. Errors
name the operation, the argument, and what was expected:

```
draw_circle: 'r' must be greater than 0, got -5
draw_circle: 'fill' must be a colour like '#ff0000' or '#f00', got 'red'
draw_polygon: 'points' needs at least three [x, y] pairs, got 2
unknown operation 'draw_square'. Available: clear, draw_circle, ..., done
```

Colours are hex only. Names look friendlier, but several in the task set do not
mean what a reader expects — the prompts' "brown" is `saddlebrown` and their
"dark red" is `firebrick` — so accepting names would quietly draw the wrong
colour instead of producing an error the agent can act on.

Rendered with Pillow, supersampled 4× and filtered down; references come out of
cairo, so a correct drawing here is never matching its own anti-aliasing.

### Known bias in the comparison

The golden recipes and the API vocabulary are both shape lists, so this layer is
structurally closest to how the tasks were authored. That is an advantage the
kernel and UI layers do not get, and it is not entirely separable from the
abstraction-level effect the harness is trying to measure. Worth stating plainly
when the results are read, rather than discovered later.

## The kernel layer

Below the API layer, not beside it. There are no shapes here — the agent works
out which pixels it wants and sets them.

```
fill(colour)                     set every pixel
set_pixels(pixels)               [[x, y, colour], ...]
write_spans(spans)               [[x, y, length, colour], ...]
```

Three properties define it, each the honest reading of *max control, no
guardrails, failures land on the agent*:

- **No shape primitives at all.** This is what makes the ladder monotone. Raw
  SVG path data — the other candidate for this rung — would have sat *above* the
  API layer, not below it.
- **Nothing is clipped.** A write leaving the buffer is rejected, not trimmed,
  so bounds arithmetic is the agent's problem.
- **No anti-aliasing.** Pixel values are exactly what the agent wrote.

Actions carry *sequences* of writes, as the spec's "low-level op sequences"
implies. One pixel per turn would put a circle out of reach of any sane budget,
and the layer would be measuring the turn limit rather than the abstraction. The
oracle solves the whole task set in 1–8 turns and 80–400 spans, well inside the
40-turn budget.

**What this layer does not do is give worse errors than the others.** Its
messages are terse because its operations are, but they name the entry, the
value and the limit. Degrading the error signal on purpose would be putting a
thumb on the scale for the hypothesis under test. A rejected batch also writes
nothing at all — validation completes before any pixel lands, so the agent is
never left guessing what state the buffer is in.

### Hard edges are not penalised

The references are anti-aliased and this layer cannot anti-alias, which looked
like it might sink the whole rung. Measured: a hard-edged rasterisation scores
**1.000** on every task, because the scorer's 1.5px blur absorbs the difference
entirely. Had it not, every kernel result would have been a measurement of the
scorer. There is a per-task regression test asserting exactly 1.000, not merely
"passes".

## Layer parity

`tests/test_layer_parity.py` holds the invariants that keep the comparison
honest, applied to whichever layers are registered — so a new environment has to
satisfy them the moment it is added, not whenever someone remembers to check:

- no preamble names a colour any task asks for, or uses task vocabulary;
- every preamble states the canvas geometry and coordinate convention;
- `done` is offered everywhere and implemented by no layer;
- every layer returns the canvas after every action — not just the UI one;
- rejected actions are observations carrying an error, never exceptions, and
  they list what is available;
- every artifact is a PNG at canvas size, and `reset` really does discard the
  previous run;
- the oracle passes every task at every layer.

That last one is the load-bearing check. Until it holds for a layer, a real
agent's failure there cannot be attributed to the agent.

## The runner

`Runner.run(task, environment, agent)` drives one agent through one task at one
layer: reset both, ask for an action, execute it, feed the observation back,
repeat until the agent says `done` or the budget runs out. Then score whatever
came out and write the trace.

The runner is the only component that sees both sides — the agent and the
environment get a `TaskBrief`, the scorer gets the reference, the runner holds
the `Task` that joins them.

**Outcomes are orthogonal to pass/fail.** A run ends `completed`, `turn_limit`,
`agent_error` or `environment_error`, and is scored either way. An agent that
drew the right thing and never declared completion is a different failure from
one that drew the wrong thing, and collapsing the two loses the distinction.
`passed` is the scorer's verdict and stays the headline number.

**A rejected action is not the end of a run.** Malformed or unsupported actions
come back as an observation carrying an error, and the agent gets to read it and
try again; environments raise only when they are actually broken. Error
legibility is one of the things being measured, so it has to be reachable.

**Turn budgets are per-layer** (`api` 20, `kernel` 40, `ui` 60 — provisional
until each environment has run). One UI click accomplishes far less than one API
call, so a single shared budget would hand the lower layers a handicap that
reads as incapability. For the same reason mean turn counts are reported
within-layer only and the summary says so.

An agent crash fails that run, not the sweep. Timeouts and malformed model
responses are expected, and the trace is what makes them readable afterwards.

### Traces

JSONL, one record per turn, written to `traces/<run_id>.jsonl` as the run
happens rather than assembled at the end — the run worth reading is usually the
one that fell over, and it should not take its own transcript with it.

```
run_start   task, layer, agent, turn limit, prompt, canvas
interface   the operation signatures the agent was shown
turn        action, observation, cumulative timings, token usage
run_end     outcome, turns used, pass/fail, metrics, scoring details
```

Images are never inlined — an observation records its byte count and a digest,
and the canvas is saved beside the trace (`final.png`, plus `turn_NNN.png` when
`capture_turn_images` is on). A trace with a base64 PNG per turn is not one
anyone will read.

## Results

`write_csv()` emits one row per run; `format_summary()` prints success rate by
layer and by difficulty tier. Where a layer stops working is more informative
than whether it works — a layer that handles single shapes and collapses on
occlusion is a different finding from one that fails everywhere.

Oracle runs are written to the CSV, flagged, and excluded from every rate. They
were handed the answer; counting them would inflate the one number this whole
exercise turns on.

## Scoring

`PixelScorer` compares the final artifact against the task's reference and
returns pass/fail plus continuous measures. Deterministic, no model in the loop.

The gate is **pixel accuracy**: the fraction of pixels matching the reference
within a per-channel tolerance, after a mild Gaussian blur. Two decisions worth
knowing about:

**Why blur.** Each layer rasterises with different machinery — cairo for the
reference, Pillow, numpy, a browser. Their anti-aliased edges disagree by up to
half the foreground/background delta, which no per-channel tolerance can
absorb. A ~1.5px blur erases that disagreement while a 2px position error
survives it.

**Why not mean error.** Mean error barely moves when a small shape is missing:
omitting the r=40 circle from `t01` costs only ~8%. Any threshold strict enough
to catch that would be intolerant of everything else. Mean error is still
reported — it is a useful signal, just a bad gate.

### The thresholds are calibrated, not guessed

`tests/test_task_validity.py` squeezes the threshold from both sides, per task,
using a rasteriser (`tests/independent_renderer.py`) unrelated to the one that
produced the references:

- a **correct** drawing must pass — otherwise the harness is measuring which
  renderer an agent happened to use;
- every **near-miss** must fail — blank canvas, figure shifted one pixel, shapes
  at 90% size, colours off by 40, circles squared off, a shape omitted, and for
  the occlusion tier the right shapes in the wrong order.

At `blur_sigma=1.5, channel_tolerance=24` correct drawings score exactly
**1.000** and the best wrong one scores **0.981**, so the **0.99** threshold
sits in the middle of the gap. A test asserts that headroom rather than
trusting the numbers to stay put.

The occlusion tier gets one extra check: reversing the draw order must fail. A
task filed under that tier that scores the same either way is a composition task
wearing a hat, and would overstate every layer's ordering ability.

> **Recalibrate when the UI environment lands.** A browser canvas is a fourth
> rasteriser and has not been measured. If correct UI drawings come back
> clustered just under 0.99, the threshold is wrong, not the layer — and reading
> that as a UI capability gap is exactly the mistake this harness exists to
> avoid.

## Decisions taken

Settled before building; recorded because they shape what the results mean.

| Question | Decision |
|----------|----------|
| Visual feedback between turns | Every layer gets the rendered canvas back after each turn. Giving it only to UI would measure sighted-vs-blind, not abstraction level. |
| What "kernel" means | Pixel/framebuffer operations, so the ladder is monotone. Raw SVG path data sits *above* the API layer, not below it. |
| API layer shape | Structured tool calls, not executed Python — no sandbox, cleaner error signal, and the honest analogue of CadQuery-as-tools. |
| Turn budgets | Per-layer, configurable. One UI click is not one API call, so cross-layer turn counts stay out of the headline. |
| Artifact format | Every layer normalises to a PNG at the canvas size. |
| Reference provenance | Rendered by a standalone golden path, never by an environment's own code. |
| Prompt parity | The task prompt is byte-identical across layers. Environment preambles describe the interface only and never mention the task. |
| Runs per cell | Configurable; 1 for the mock, 3 for real models. |
| Completion signal | A uniform `done` action at all three layers. |
| Traces | JSONL, one record per turn. Tokens and wall-clock recorded even though optimising them is a non-goal. |

## Extending

Adding a task: write `tasks/<id>.json` with `id`, `description`, `prompt`,
`canvas`, `difficulty`, and a `golden` recipe, then run
`tools/generate_references.py
tools/run_sweep.py`. The validity tests pick it up automatically and
will tell you if the prompt is under-specified or the tier is mislabelled.

Swapping the scorer: implement the `Scorer` protocol. The SVG scorer is meant to
be replaceable wholesale by a CAD one (watertightness, volume delta) without the
runner noticing — that is the seam the eventual domain change runs through.
