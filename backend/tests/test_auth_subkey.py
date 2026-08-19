from app.services.auth import (
    _external_task_legacy_user_ids,
    _external_task_user_id,
    _legacy_sso_match_keys,
)


def test_external_task_user_id_prefers_subkey_over_legacy_id():
    data = {
        "id": "old-id",
        "user_id": "old-user-id",
        "subkey": "new-subkey",
    }

    assert _external_task_user_id(data) == "new-subkey"
    assert _external_task_legacy_user_ids(data) == ["old-id", "old-user-id"]


def test_external_task_user_id_falls_back_to_old_fields():
    assert _external_task_user_id({"id": "old-id"}) == "old-id"
    assert _external_task_user_id({"user_id": "old-user-id"}) == "old-user-id"


def test_legacy_sso_match_keys_cover_username_and_email():
    keys = _legacy_sso_match_keys(
        name="周建华",
        username="zhou.jianhua",
        email="zhou@example.com",
    )

    assert ("username", "zhou.jianhua") in keys
    assert ("email", "zhou@example.com") in keys
