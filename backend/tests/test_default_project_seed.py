import asyncio
from unittest.mock import AsyncMock, Mock

from app.models import Project, QualityGateConfig
from app.seed_default_project import DEFAULT_PROJECT_ID, ensure_default_project


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


def test_creates_generic_project_when_project_table_is_empty():
    db = Mock()
    db.execute = AsyncMock(return_value=_ScalarResult(None))
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    created = asyncio.run(ensure_default_project(db))

    assert created is True
    project, gate = [call.args[0] for call in db.add.call_args_list]
    assert isinstance(project, Project)
    assert project.id == DEFAULT_PROJECT_ID
    assert project.name == "示例项目"
    assert project.case_id_prefix == "DEMO"
    assert isinstance(gate, QualityGateConfig)
    assert gate.project_id == project.id
    db.commit.assert_awaited_once()


def test_preserves_existing_projects():
    db = Mock()
    db.execute = AsyncMock(return_value=_ScalarResult("existing-project"))
    db.commit = AsyncMock()

    created = asyncio.run(ensure_default_project(db))

    assert created is False
    db.add.assert_not_called()
    db.commit.assert_not_awaited()
