# Agent Abstraction-Layer Harness

Measures **at which level of abstraction an AI agent can complete a task**. The
same task is presented through three interfaces — UI, API, Kernel — and the
success rate is compared across them.

The hypothesis: agent capability is a property of the model *plus the interface
it is given*, not the model alone.

See `HARNESS_SPEC.md` for the full design.

## Status

Build step 1 of 7 is done: **task definitions and the scorer for the SVG
domain**. Nothing drives an agent yet.

| # | Component | State |
|---|-----------|-------|
| 1 | Task definitions + scorer | done |
| 2 | MockAgent | next |
| 3 | Runner loop | |
| 4 | APIEnvironment | |
| 5 | KernelEnvironment | |
| 6 | UIEnvironment | |
| 7 | ModelAgent | |

## Layout

```
harness/task.py        Task, Canvas, Difficulty, ScoringConfig, loaders
harness/scorer.py      Scorer protocol + PixelScorer
harness/image.py       artifact normalisation (PNG -> RGB array), blur
harness/reference.py   golden recipe -> SVG -> PNG   (authoring only)
tasks/*.json           the task set, one file each
references/*.png       generated golden images
tools/generate_references.py
tests/                 unit tests plus the threshold calibration
```

## Running

```bash
pip install -r requirements.txt
pytest                                        # 140 tests
python3 tools/generate_references.py --check  # references current?
python3 tools/generate_references.py          # regenerate after a recipe edit
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
`tools/generate_references.py`. The validity tests pick it up automatically and
will tell you if the prompt is under-specified or the tier is mislabelled.

Swapping the scorer: implement the `Scorer` protocol. The SVG scorer is meant to
be replaceable wholesale by a CAD one (watertightness, volume delta) without the
runner noticing — that is the seam the eventual domain change runs through.
