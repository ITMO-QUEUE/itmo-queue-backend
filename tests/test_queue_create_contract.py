from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from itmo_queue.api.schemas.session import SessionCreate
from itmo_queue.main import create_app


def payload():
    now = datetime.now(UTC)
    return {
        "title": "Defense",
        "discipline_id": str(uuid4()),
        "planned_start": now.isoformat(),
        "planned_end": (now + timedelta(hours=1)).isoformat(),
        "teacher_ids": [str(uuid4())],
        "works": [{"title": "Lab", "priority": 1}],
        "auditorium": "3200",
        "registration_open": True,
    }


def test_new_creation_contract():
    data = payload()
    session = SessionCreate.model_validate(data)
    assert session.avg_duration_min == 10
    assert session.quota is None
    assert session.registration_open is True
    assert SessionCreate.model_validate(data | {"onlineLink": "https://ktalk.ru"}).onlineLink
    online = data | {"auditorium": None, "onlineLink": "https://ktalk.ru"}
    assert SessionCreate.model_validate(online).onlineLink
    for invalid in (
        {key: value for key, value in data.items() if key != "registration_open"},
        data | {"title": "   "},
        data | {"auditorium": None},
        data | {"auditorium": "   "},
        data | {"location": "old"},
        data | {"stream_ids": []},
        data | {"works": [{"work_id": str(uuid4()), "priority": 1}]},
        data | {"planned_end": data["planned_start"]},
    ):
        with pytest.raises(ValidationError):
            SessionCreate.model_validate(invalid)


def test_openapi_uses_creation_only_schema():
    schema = create_app().openapi()
    post = schema["paths"]["/api/v1/queues"]["post"]
    assert post["operationId"] == "createSession"
    assert post["security"]
    create = schema["components"]["schemas"]["SessionCreate"]["properties"]
    assert {"auditorium", "onlineLink", "works"} <= create.keys()
    assert "registration_open" in schema["components"]["schemas"]["SessionCreate"]["required"]
    created_work = schema["components"]["schemas"]["SessionCreatedWork"]["properties"]
    assert {"work_id", "title", "priority"} <= created_work.keys()
    options = schema["components"]["schemas"]["SessionOptions"]["properties"]
    assert set(options) == {"disciplines", "teachers"}
    assert "location" not in create and "stream_ids" not in create
    update = schema["components"]["schemas"]["SessionUpdate"]["properties"]
    assert {"location", "stream_ids", "works"} <= update.keys()
