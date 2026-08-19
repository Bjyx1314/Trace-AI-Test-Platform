from types import SimpleNamespace

from app.routers.requirements import (
    _add_requirement_participant,
    _participant_names,
    _requirement_visible_to_owner,
)


def test_new_requirement_participants_start_with_owner():
    assert _participant_names("张三") == ["张三"]
    assert _participant_names("  ") == []


def test_duplicate_import_adds_importer_without_losing_original_owner():
    req = SimpleNamespace(owner_name="张三", participant_names=None)

    changed = _add_requirement_participant(req, "李四")

    assert changed is True
    assert req.participant_names == ["张三", "李四"]
    assert _requirement_visible_to_owner(req, "张三")
    assert _requirement_visible_to_owner(req, "李四")


def test_duplicate_import_is_idempotent_for_same_importer():
    req = SimpleNamespace(owner_name="张三", participant_names=["张三", "李四"])

    changed = _add_requirement_participant(req, "李四")

    assert changed is False
    assert req.participant_names == ["张三", "李四"]
