import uuid

import pytest

from itmo_queue.api.deps import get_current_user
from itmo_queue.db.models.user import User
from itmo_queue.main import app

BASE_URL = "http://test/api/v1"


@pytest.mark.asyncio
async def test_self_move_once(student_client, setup_queue, db_session):
    client, _ = student_client
    queue, work = setup_queue

    # Создаем 4-х студентов и записываем их
    users = []
    for i in range(4):
        u = User(
            name=f"U{i}", login=f"u{i}_{uuid.uuid4().hex[:4]}", password_hash="h", role="student"
        )
        db_session.add(u)
        await db_session.commit()
        users.append(u)

    entry_ids = []
    for i, u in enumerate(users):
        app.dependency_overrides[get_current_user] = lambda u=u: u
        res = await client.post(
            f"{BASE_URL}/queues/{queue.id}/entries",
            json={"work_id": str(work.id), "artifact_url": f"https://github.com/test/url{i}"},
        )
        assert res.status_code == 201
        entry_ids.append(res.json()["id"])

    # Переключаемся на 4-го студента (его запись на 4-м месте, не в Топ-3)
    app.dependency_overrides[get_current_user] = lambda: users[3]

    # Первый перенос
    move1 = await client.post(f"{BASE_URL}/queues/{queue.id}/entries/{entry_ids[3]}/move-to-end")
    assert move1.status_code == 200

    # Второй перенос (должен упасть с 400, так как лимит 1 раз)
    move2 = await client.post(f"{BASE_URL}/queues/{queue.id}/entries/{entry_ids[3]}/move-to-end")
    assert move2.status_code == 400

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_swap_and_double_accept(student_client, setup_queue, db_session):
    client, student1 = student_client
    queue, work = setup_queue

    # Создаем 3-х dummy-студентов, чтобы вытолкнуть наших из Топ-3
    for i in range(3):
        u = User(
            name=f"Dummy{i}",
            login=f"d{i}_{uuid.uuid4().hex[:4]}",
            password_hash="h",
            role="student",
        )
        db_session.add(u)
        await db_session.commit()
        app.dependency_overrides[get_current_user] = lambda u=u: u
        await client.post(
            f"{BASE_URL}/queues/{queue.id}/entries",
            json={"work_id": str(work.id), "artifact_url": f"https://github.com/test/dummy{i}"},
        )

    # Записываем Студента 1 (теперь он на 4-м месте)
    app.dependency_overrides[get_current_user] = lambda: student1
    res1 = await client.post(
        f"{BASE_URL}/queues/{queue.id}/entries",
        json={"work_id": str(work.id), "artifact_url": "https://github.com/test/url1"},
    )
    entry1_id = res1.json()["id"]

    # Создаем и записываем Студента 2 (он на 5-м месте)
    student2 = User(
        name="S2", login=f"s2_{uuid.uuid4().hex[:4]}", password_hash="h", role="student"
    )
    db_session.add(student2)
    await db_session.commit()
    app.dependency_overrides[get_current_user] = lambda: student2
    res2 = await client.post(
        f"{BASE_URL}/queues/{queue.id}/entries",
        json={"work_id": str(work.id), "artifact_url": "https://github.com/test/url2"},
    )
    entry2_id = res2.json()["id"]

    # Студент 2 предлагает обмен Студенту 1
    swap_res = await client.post(
        f"{BASE_URL}/queues/{queue.id}/swaps?from_entry_id={entry2_id}&to_entry_id={entry1_id}"
    )
    assert swap_res.status_code == 201
    swap_id = swap_res.json()["swap_id"]

    # Переключаемся на Студента 1, чтобы он принял
    app.dependency_overrides[get_current_user] = lambda: student1

    # Принимаем обмен
    accept_res = await client.post(f"{BASE_URL}/queues/{queue.id}/swaps/{swap_id}/accept")
    assert accept_res.status_code == 200

    # Повторное принятие возвращает сохранённый результат без второго обмена.
    accept_res2 = await client.post(f"{BASE_URL}/queues/{queue.id}/swaps/{swap_id}/accept")
    assert accept_res2.status_code == 200
    assert accept_res2.json()["status"] == "accepted"

    app.dependency_overrides.clear()
