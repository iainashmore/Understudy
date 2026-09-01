> Kept as an illustration of anchor relocation. The flow and fixture it
> was made from are gone with the web driver; the point it makes -- that a
> visual anchor is found again after the thing it names has moved -- is
> exactly what carries a CAD application.

# Demo: select, rename, ask — with the dialog in two different places

The same flow (`examples/cad_rename_and_ask.yaml`), run twice against
`fixtures/cad_app`. Between runs the properties dialog opens **340px right and
230px down** from where it did before. The flow is not edited.

| | |
|---|---|
| `rename-flow-dialog-pos-a.png` | Run 1, dialog at (300, 170) |
| `rename-flow-dialog-pos-b.png` | Run 2, dialog at (640, 400) |
| `anchor-relocation.png` | The same `Done` anchor located in both runs |

Six steps per run: select `Pad.1` in the geometry tree, open the properties
dialog, edit the name to `MountingBracket`, click `Done`, ask the assistant
about the selection, wait for the reply.

Nothing is targeted by selector. Every control is found by locating a picture of
it in the current screenshot, so the click points are computed fresh each run.
All twelve interactions resolved at score 1.000.

Two details that make it work:

- **The dialog anchors carry no `region`.** They are searched across the whole
  window, so the dialog is found wherever it opened. Anchors for controls that
  do not move — the toolbar, the tree, the assistant panel — keep a region,
  which is faster and rules out false matches elsewhere.
- **Anchors sit on the parts that do not change.** The name field is reached by
  anchoring on the static `Name` label and clicking at an offset, because a
  field's appearance changes the moment it has different text in it. Same for
  the assistant's prompt box.

The rename propagates: the assistant's reply reads
`Echo: what is the purpose of the selected feature [selected: MountingBracket]`,
so the whole chain — selection, edit, commit, prompt — is verified by the
recorded output rather than assumed.

The `read` step reports an error in both runs because no OCR engine is installed
in this environment. The response *pixels* are still recorded. That is
deliberate: "could not read" must never look like "the assistant said nothing".
