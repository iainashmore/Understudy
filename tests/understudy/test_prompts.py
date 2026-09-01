"""Prompt variants, which live inside the flow."""

from __future__ import annotations

import pytest

from understudy.flow import parse_flow
from understudy.prompts import PromptsError, prompts_for, prompts_from_entries

ENTRIES = [
    {"id": "baseline", "prompt": "Summarise this in one paragraph."},
    {"id": "terse", "prompt": "Summarise this in one sentence."},
]


def flow_with(prompts):
    return parse_flow({
        "version": 1, "name": "t", "prompts": prompts,
        "targets": {"box": {"native": "Message"}},
        "steps": [{"action": "click", "target": "box"}],
    })


def test_variants_load_from_entries():
    prompts = prompts_from_entries(ENTRIES)
    assert [v.id for v in prompts] == ["baseline", "terse"]
    assert prompts.variants[0].prompt == "Summarise this in one paragraph."


def test_every_key_besides_id_is_a_variable():
    prompts = prompts_from_entries(
        [{"id": "formal", "prompt": "Summarise.", "style": "formal", "words": 50}]
    )
    assert prompts.variants[0].variables == {
        "prompt": "Summarise.", "style": "formal", "words": "50",
    }


def test_a_flow_carries_its_own_variants():
    prompts = prompts_for(flow_with(ENTRIES))
    assert [v.id for v in prompts] == ["baseline", "terse"]


def test_a_flow_without_variants_cannot_run():
    """One file is one test; a flow with no prompts is half a test."""
    with pytest.raises(Exception, match="prompts"):
        flow_with([])


def test_entries_need_an_id():
    with pytest.raises(PromptsError, match="entry 1 has no 'id'"):
        prompts_from_entries([{"prompt": "hello"}])


def test_duplicate_ids_are_rejected():
    # Two variants with one id would collide in the output directory.
    with pytest.raises(PromptsError, match="duplicate prompt id"):
        prompts_from_entries([{"id": "a", "prompt": "x"}, {"id": "a", "prompt": "y"}])


class TestSelection:
    def test_only_selects_and_orders(self):
        prompts = prompts_from_entries(ENTRIES).select(["terse", "baseline"])
        assert [v.id for v in prompts] == ["terse", "baseline"]

    def test_selecting_nothing_keeps_everything(self):
        assert len(prompts_from_entries(ENTRIES).select(None)) == 2

    def test_an_unknown_id_lists_what_exists(self):
        with pytest.raises(PromptsError, match="unknown prompt id.*nope"):
            prompts_from_entries(ENTRIES).select(["nope"])


class TestVariableCoverage:
    def test_a_complete_set_passes(self):
        prompts_from_entries(ENTRIES).check_provides({"prompt"})

    def test_a_missing_variable_is_caught_before_the_run(self):
        """Finding out on variant 40 of 50 wastes the sweep."""
        with pytest.raises(PromptsError, match="baseline: missing tone"):
            prompts_from_entries(ENTRIES).check_provides({"prompt", "tone"})

    def test_the_report_names_every_offending_variant(self):
        with pytest.raises(PromptsError) as caught:
            prompts_from_entries(ENTRIES).check_provides({"missing"})
        assert "baseline" in str(caught.value) and "terse" in str(caught.value)
