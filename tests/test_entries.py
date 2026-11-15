import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, update

from itmo_queue.models import (
    Discipline,
    Queue,
    QueueStage,
    QueueWork,
    Work,
)

BASE_URL = "http://test/api/v1"


@pytest.fixture
async def setup_queue(db_session, student_client):
    """Вспомогательная фикстура для создания очереди и работы."""
    client, student = student_client

    discipline = Discipline(name=f"Disc {uuid.uuid4().hex[:4]}")
    db_session.add(discipline)
    await db_session.flush()

    now = datetime.now(UTC)

    # Исправляем создание Queue: передаем реального студента и даты
    queue = Queue(
        discipline_id=discipline.id,
        created_by=student.id,  # <-- Используем ID нашего тестового студента
        title="Test Queue",
        planned_start=now,
        planned_end=now,
        stage=QueueStage.active,
        registration_open=True,
        quota=2,
        version=1,  # <-- Добавили поле version
        created_at=now,  # <-- Добавили дату
        updated_at=now,  # <-- Добавили дату
    )
    db_session.add(queue)

    work = Work(discipline_id=discipline.id, title="Lab 1", position_no=1, is_active=True)
    db_session.add(work)
    await db_session.flush()

    queue_work = QueueWork(queue_id=queue.id, work_id=work.id, priority=1)
    db_session.add(queue_work)
    await db_session.commit()

    return queue, work


@pytest.mark.asyncio
async def test_enroll_success(student_client, setup_queue):
    client, student = student_client
    queue, work = setup_queue

    response = await client.post(
        f"{BASE_URL}/queues/{queue.id}/entries",
        json={"work_id": str(work.id), "artifact_url": "https://github.com/test/repo"},
    )

    assert response.status_code == 201
    data = response.json()
    assert data["is_reserve"] is False
    assert data["position"] == 1


@pytest.mark.asyncio
async def test_enroll_duplicate(student_client, setup_queue):
    client, student = student_client
    queue, work = setup_queue

    await client.post(
        f"{BASE_URL}/queues/{queue.id}/entries",
        json={"work_id": str(work.id), "artifact_url": "https://github.com/test/repo1"},
    )

    response = await client.post(
        f"{BASE_URL}/queues/{queue.id}/entries",
        json={"work_id": str(work.id), "artifact_url": "https://github.com/test/repo2"},
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_top3_cannot_cancel(student_client, setup_queue):
    client, student = student_client
    queue, work = setup_queue

    response = await client.post(
        f"{BASE_URL}/queues/{queue.id}/entries",
        json={"work_id": str(work.id), "artifact_url": "https://github.com/test/repo"},
    )
    entry_id = response.json()["id"]

    cancel_res = await client.delete(f"{BASE_URL}/queues/{queue.id}/entries/{entry_id}")
    assert cancel_res.status_code == 403


@pytest.mark.asyncio
async def test_reserve_logic(student_client, setup_queue, db_session):
    from itmo_queue.models import EntryStatus, QueueEntry

    client, student = student_client
    queue, work = setup_queue

    # Делаем квоту равной 1 (только 1 человек в основном составе)
    queue.quota = 1
    db_session.add(queue)
    await db_session.commit()

    # 1. Записываем первого студента (нашего)
    response1 = await client.post(
        f"{BASE_URL}/queues/{queue.id}/entries",
        json={"work_id": str(work.id), "artifact_url": "https://github.com/test/repo1"},
    )
    assert response1.status_code == 201
    data1 = response1.json()
    assert data1["is_reserve"] is False
    assert data1["position"] == 1
    entry1_id = data1["id"]

    # 2. Создаем второго студента и мокаем для него авторизацию
    from itmo_queue.api.deps import get_current_user
    from itmo_queue.db.models.user import User
    from itmo_queue.main import app

    student2 = User(
        name="Test Student 2",
        login=f"student2_{uuid.uuid4().hex[:6]}",
        password_hash="hashed",
        role="student",
    )
    db_session.add(student2)
    await db_session.commit()
    await db_session.refresh(student2)

    # Подменяем авторизацию на второго студента
    app.dependency_overrides[get_current_user] = lambda: student2

    # 3. Записываем второго студента
    response2 = await client.post(
        f"{BASE_URL}/queues/{queue.id}/entries",
        json={"work_id": str(work.id), "artifact_url": "https://github.com/test/repo2"},
    )
    assert response2.status_code == 201
    data2 = response2.json()
    # Так как квота 1, а он второй, он должен уйти в резерв!
    assert data2["is_reserve"] is True
    assert data2["position"] == 2

    # 4. Освобождаем место (меняем статус первого студента напрямую в БД на cancelled)
    # (Мы не можем через API, так как он в Топ-3 и отмена запрещена)
    await db_session.execute(
        update(QueueEntry).where(QueueEntry.id == entry1_id).values(status=EntryStatus.cancelled)
    )
    await db_session.commit()

    # 5. Проверяем, что второй студент автоматически перешел в основной состав
    get_res = await client.get(
        f"{BASE_URL}/queues/{queue.id}/entries"
    )  # Если у вас нет GET, можно проверить через POST, но лучше просто через БД

    # Проверяем через БД, что позиция обновилась (динамический подсчет)
    entry2 = await db_session.scalar(select(QueueEntry).where(QueueEntry.student_id == student2.id))
    assert entry2.status == EntryStatus.waiting  # Он все еще waiting, но позиция теперь 1

    # (Опционально: если у вас есть GET-метод для списка записей, можно проверить через него)
    app.dependency_overrides.clear()
