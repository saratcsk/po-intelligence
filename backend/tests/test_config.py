import copy

import pytest

from po_pipeline.config import DEFAULT_CONFIG, load_config, save_config, validate_config
from po_pipeline.validate import validate_po
from tests.test_validate import GOOD_PO


class FakeTable:
    def __init__(self):
        self.items = {}

    def get_item(self, Key):
        item = self.items.get(Key["po_id"])
        return {"Item": item} if item else {}

    def put_item(self, Item, **kwargs):
        self.items[Item["po_id"]] = Item


def cfg(**changes):
    c = copy.deepcopy(DEFAULT_CONFIG)
    for path, value in changes.items():
        section, key = path.split("__")
        c[section][key] = value
    return c


def test_defaults_are_valid_and_match_old_behaviour():
    assert validate_config(DEFAULT_CONFIG) == []
    assert validate_po(GOOD_PO, DEFAULT_CONFIG)["status"] == "READY"


def test_bad_values_are_rejected():
    problems = validate_config(cfg(accuracy__auto_approve_threshold=1.5, rules__uom_mismatch="maybe",
                                   orchestration__max_agent_turns=50,
                                   orchestration__model_id="anthropic.claude-opus"))
    assert len(problems) == 4


def test_making_a_field_required_turns_missing_into_an_error():
    po = {**GOOD_PO, "ship_to": None}
    assert validate_po(po, DEFAULT_CONFIG)["errors"] == 0                       # important -> warning
    assert validate_po(po, cfg(fields__ship_to="required"))["errors"] == 1
    assert validate_po(po, cfg(fields__ship_to="optional"))["issues"] == []


def test_rule_can_be_switched_off():
    po = copy.deepcopy(GOOD_PO)
    po["line_items"][0]["uom"] = "EA"                                           # catalog says BX
    assert validate_po(po)["warnings"] == 1
    assert validate_po(po, cfg(rules__uom_mismatch="off"))["warnings"] == 0


def test_stricter_threshold_sends_more_pos_to_review():
    po = copy.deepcopy(GOOD_PO)
    po["line_items"][1]["unit_price"] = 84.00                                  # one price warning, score 0.86
    assert validate_po(po)["status"] == "READY"
    assert validate_po(po, cfg(accuracy__auto_approve_threshold=0.9))["status"] == "NEEDS_REVIEW"


def test_save_creates_versions_and_history():
    table = FakeTable()
    assert load_config(table)["version"] == 0
    save_config(table, cfg(accuracy__auto_approve_threshold=0.9), "admin", "stricter")
    saved = save_config(table, cfg(accuracy__auto_approve_threshold=0.8), "admin", "looser")
    assert saved["version"] == 2
    assert load_config(table)["accuracy"]["auto_approve_threshold"] == 0.8
    assert float(table.items["CONFIG#v00001"]["config"]["accuracy"]["auto_approve_threshold"]) == 0.9


def test_invalid_config_is_never_saved():
    table = FakeTable()
    with pytest.raises(ValueError):
        save_config(table, cfg(accuracy__error_penalty=-1), "admin")
    assert table.items == {}


def test_stored_numbers_are_decimal_and_loaded_back_as_float():
    from decimal import Decimal

    table = FakeTable()
    save_config(table, cfg(accuracy__auto_approve_threshold=0.9), "admin")
    stored = table.items["CONFIG#current"]["config"]["accuracy"]["auto_approve_threshold"]
    assert isinstance(stored, Decimal)                                          # DynamoDB rejects float
    loaded = load_config(table)["accuracy"]["auto_approve_threshold"]
    assert isinstance(loaded, float) and loaded == 0.9                          # rules need float
