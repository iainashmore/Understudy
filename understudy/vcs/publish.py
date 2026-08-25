"""Deciding what of a run belongs in the repository.

A run directory is mostly evidence: a transcript, the screenshots it links, the
results a machine reads -- and one video per variant, which is by far the
largest thing in it and the one thing git handles worst. Committing everything
is the obvious choice and the wrong one: a year of daily regression runs puts
several gigabytes of mp4 into a history that cannot be trimmed without
rewriting it, and every clone pays for it forever.

So video is left out by default and linked instead. Everything a reviewer
actually reads -- the transcript, the prompts, the responses, the screenshots
that prove what happened -- goes in, and stays small enough to be worth keeping.

That default is a judgement, not a law. `include_video` overrides it for the
run worth keeping in full, and a repository with Git LFS configured can take
them all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

#: Committed whatever else is excluded: this is the run.
ALWAYS = (".md", ".html", ".jsonl", ".csv", ".json", ".yaml", ".yml", ".txt",
          ".pdf")
#: Committed unless somebody asks otherwise. The evidence a person looks at.
IMAGES = (".png", ".jpg", ".jpeg", ".gif", ".webp")
#: Left out by default. Large, opaque to diffing, and permanent once committed.
VIDEO = (".mp4", ".webm", ".mov", ".avi", ".mkv")
#: Never committed by anything here, whatever the settings say.
NEVER = ("credentials.json",)

#: A file bigger than this is skipped and reported, whatever its suffix. A
#: 200MB screenshot is a mistake somewhere, and committing it is permanent.
DEFAULT_MAX_FILE_MB = 25.0


@dataclass(frozen=True)
class Selection:
    """What a publish would commit, and what it would leave behind."""

    include: tuple[str, ...] = ()
    excluded: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not self.include

    @property
    def total_bytes(self) -> int:
        return self._total

    _total: int = 0

    def summary(self) -> str:
        parts = [f"{len(self.include)} file(s)"]
        for reason, paths in sorted(self.excluded.items()):
            parts.append(f"{len(paths)} skipped ({reason})")
        return ", ".join(parts)


def select(
    run_dir: Path | str,
    root: Path | str,
    include_video: bool = False,
    include_images: bool = True,
    max_file_mb: float = DEFAULT_MAX_FILE_MB,
) -> Selection:
    """Choose the files of one run to commit. Paths are relative to `root`.

    Reports what it left out and why, rather than quietly dropping it. A
    transcript that links a video nobody can find should at least come with an
    explanation of where the video went.
    """
    run_dir, root = Path(run_dir).resolve(), Path(root).resolve()
    if root != run_dir and root not in run_dir.parents:
        raise ValueError(f"{run_dir} is not inside {root}")

    include: list[str] = []
    excluded: dict[str, list[str]] = {}
    total = 0
    limit = max_file_mb * 1024 * 1024

    def drop(reason: str, path: Path) -> None:
        excluded.setdefault(reason, []).append(_relative(path, root))

    for path in sorted(run_dir.rglob("*")):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()

        if path.name in NEVER:
            drop("never committed", path)
            continue
        if suffix in VIDEO and not include_video:
            drop("video", path)
            continue
        if suffix in IMAGES and not include_images:
            drop("images", path)
            continue
        if suffix not in ALWAYS + IMAGES + VIDEO:
            drop("unrecognised type", path)
            continue

        size = path.stat().st_size
        if limit and size > limit:
            drop(f"larger than {max_file_mb:g}MB", path)
            continue

        include.append(_relative(path, root))
        total += size

    return Selection(
        include=tuple(include),
        excluded={reason: tuple(paths) for reason, paths in excluded.items()},
        _total=total,
    )


def _relative(path: Path, root: Path) -> str:
    return str(path.resolve().relative_to(root)).replace("\\", "/")


def commit_message(run_dir: Path | str, results: list[dict] | None = None) -> str:
    """A subject line worth reading in a log six months from now."""
    run_dir = Path(run_dir)
    if not results:
        return f"Run {run_dir.name}"
    flow = results[0].get("flow") or run_dir.name
    total = len(results)
    ok = sum(1 for r in results if r.get("status") == "ok")
    outcome = "all ok" if ok == total else f"{ok}/{total} ok"
    return f"Run {flow}: {total} variant(s), {outcome}"


def suggest_message(paths: list[str]) -> str:
    """A commit subject written from what is actually being committed.

    Not a placeholder like "Update files": somebody reads this in a log six
    months from now, trying to find the day the click path changed. It is a
    starting point, always editable -- the point is that leaving it alone still
    produces something worth reading.
    """
    paths = [p for p in paths if p]
    if not paths:
        return ""

    runs = sorted({p.split("/")[1] for p in paths
                   if p.startswith("runs/") and "/" in p[5:]})
    others = [p for p in paths if not p.startswith("runs/")]

    if runs and not others:
        return (f"Publish run {runs[0]}" if len(runs) == 1
                else f"Publish {len(runs)} runs")

    flows = [p for p in others if p.endswith((".yaml", ".yml"))]
    rest = [p for p in others if p not in flows]

    parts = []
    if len(flows) == 1:
        parts.append(f"Update {Path(flows[0]).stem}")
    elif flows:
        parts.append(f"Update {len(flows)} flows")
    if rest:
        parts.append(f"{len(rest)} other file(s)" if parts
                     else f"Update {len(rest)} file(s)")
    if runs:
        parts.append(f"publish run {runs[0]}" if len(runs) == 1
                     else f"publish {len(runs)} runs")
    return ", ".join(parts) or f"Update {len(paths)} file(s)"


def video_note(selection: Selection, run_dir_name: str) -> str:
    """A file left in place of the videos, so the gap is explained.

    A transcript that links a recording which is not in the repository looks
    like a broken link. Saying plainly that it was left out, and where it is,
    turns that into a decision somebody made.
    """
    videos = selection.excluded.get("video", ())
    if not videos:
        return ""
    listed = "\n".join(f"  {name}" for name in videos)
    return (
        f"Recordings for {run_dir_name} were not committed.\n\n"
        f"{listed}\n\n"
        "Video is left out of the repository by default: it is the largest "
        "part of a run and git keeps it forever. The files are on the machine "
        "that produced the run, in this folder. To include them in future "
        "publishes, tick 'include video' -- and consider Git LFS first.\n"
    )
