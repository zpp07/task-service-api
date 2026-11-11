"""Run inside the API container against a disposable Compose database."""
from contextlib import closing
import json
from urllib.request import Request, urlopen
from uuid import uuid4

import pymysql

from task_service.database import load_settings, open_connection, transaction


def request(path, body=None):
    headers = {"Host": "127.0.0.1:18016", "Origin": "http://127.0.0.1:18016"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    with urlopen(Request("http://127.0.0.1:8000" + path, data=data, headers=headers), timeout=10) as response:
        assert response.status == (201 if body is not None else 200)
        return json.load(response)


def verify_rollback():
    settings = load_settings()
    run_key = str(uuid4())
    with closing(open_connection(settings)) as connection:
        try:
            with transaction(connection), connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO practice_tasks (run_key, title) VALUES (%s, %s)",
                    (run_key, "Rollback check"),
                )
                cursor.execute(
                    "INSERT INTO practice_events (task_id, kind) VALUES (%s, %s)",
                    (cursor.lastrowid, "invalid"),
                )
        except pymysql.MySQLError as error:
            assert error.args[0] == 3819, error.args[0]
        else:
            raise AssertionError("Expected MySQL CHECK constraint failure")
    with closing(open_connection(settings)) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT id FROM practice_tasks WHERE run_key = %s", (run_key,))
        assert cursor.fetchone() is None


if __name__ == "__main__":
    assert request("/ready")["tls"] is True
    title = "Example task: ' OR 1=1 --"
    task = request("/tasks", {"title": title})
    assert task["title"] == title and task["event_count"] == 1
    path = f"/tasks/{task['id']}"
    assert request(path) == task
    page = request(path + "/events?limit=1")
    assert len(page["items"]) == 1
    event = page["items"][0]
    assert event["kind"] == "created" and event["task_id"] == task["id"]
    assert event["id"] == page["next_after_id"]
    assert request(path + f"/events?after_id={event['id']}")["items"] == []
    verify_rollback()
    print(json.dumps(task))
