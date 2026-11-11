"""Lesson16 HTTP/store unit tests; synthetic stores/connections, NOT MySQL E2E."""

from collections import deque

from fastapi.testclient import TestClient
import pytest

from task_service import api
from task_service import store as stores


KEY = "1bf4b811-cf70-40c0-ab94-0d96a6cf82b0"


def task(title="synthetic task"):
    return {"id": 7, "run_key": KEY, "title": title, "status": "pending", "event_count": 1}


class StubStore:
    def __init__(self):
        self.calls = []
        self.failure = False

    def _record(self, *args):
        self.calls.append(args)
        if self.failure:
            raise stores.DatabaseFailure()

    def ready(self):
        self._record("ready")
        return {"status": "ready", "database": "task_service", "tls": True}

    def create(self, title):
        self._record("create", title)
        return task(title)

    def get(self, task_id):
        self._record("get", task_id)
        if task_id != 7:
            raise stores.TaskMissing()
        return task()

    def events(self, task_id, after_id, limit):
        self._record("events", task_id, after_id, limit)
        return {"items": [], "after_id": after_id, "limit": limit, "next_after_id": after_id}


@pytest.fixture
def http():
    store = StubStore()
    app = api.create_app(store)
    with TestClient(app, base_url=api.ORIGIN) as client:
        yield client, store


def test_mysql_api_factory_does_not_read_configuration_or_connect_until_startup(monkeypatch):
    store = StubStore()
    calls = []

    def build():
        calls.append("startup")
        return store

    monkeypatch.setattr(api, "build_store", build)
    app = api.create_app()
    assert calls == []
    with TestClient(app, base_url=api.ORIGIN) as client:
        assert calls == ["startup"]
        assert client.get("/health").status_code == 200
    assert app.state.store is None


def test_mysql_api_health_is_not_database_readiness(http):
    client, store = http
    store.failure = True
    response = client.get("/health")
    assert response.json() == {"status": "ok", "storage": "mysql", "mode": "local-demo"}
    assert store.calls == []
    assert client.get("/ready").status_code == 503
    assert response.headers["Cache-Control"] == "no-store"


def test_mysql_api_ready_uses_store(http):
    client, store = http
    assert client.get("/ready").json() == {"status": "ready", "database": "task_service", "tls": True}
    assert store.calls == [("ready",)]


def test_mysql_api_create_and_get_contract(http):
    client, store = http
    response = client.post("/tasks", json={"title": "  ' OR 1=1 --  "}, headers={"Origin": api.ORIGIN})
    assert response.status_code == 201
    assert response.json()["title"] == "' OR 1=1 --"
    assert response.json()["event_count"] == 1
    assert store.calls == [("create", "' OR 1=1 --")]
    assert client.get("/tasks/7").json() == task()


@pytest.mark.parametrize("body", [
    {}, {"title": ""}, {"title": " "}, {"title": None}, {"title": 3},
    {"title": "a" * 201}, {"title": "ok", "owner_id": 1},
    {"title": "ok", "kind": "invalid-demo"}, {"title": "ok", "run_key": KEY},
])
def test_mysql_api_invalid_body_rejected_before_store(http, body):
    client, store = http
    response = client.post("/tasks", json=body, headers={"Origin": api.ORIGIN})
    assert response.status_code == 422
    assert store.calls == []


@pytest.mark.parametrize("identifier", ["0", "-1", "not-an-id", "1.5", str(2**64)])
def test_mysql_api_invalid_path_id_is_422(http, identifier):
    client, store = http
    assert client.get("/tasks/" + identifier).status_code == 422
    assert store.calls == []


def test_mysql_api_missing_task_is_404_not_validation_error(http):
    client, _ = http
    assert client.get("/tasks/999").json() == {"detail": "Task not found"}
    assert client.get("/tasks/999").status_code == 404


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"after_id": -1}, {"after_id": 2**64}])
def test_mysql_api_event_query_bounds(http, params):
    client, store = http
    assert client.get("/tasks/7/events", params=params).status_code == 422
    assert store.calls == []


def test_mysql_api_events_pass_explicit_cursor_and_limit(http):
    client, store = http
    response = client.get("/tasks/7/events?after_id=12&limit=3")
    assert response.status_code == 200
    assert response.json() == {"items": [], "after_id": 12, "limit": 3, "next_after_id": 12}
    assert store.calls == [("events", 7, 12, 3)]


@pytest.mark.parametrize("headers,status", [
    ({}, 403), ({"Origin": "https://evil.example"}, 403),
    ({"Origin": "http://localhost:18016"}, 403),
    ({"Origin": api.ORIGIN, "Content-Type": "text/plain"}, 415),
    ({"Origin": api.ORIGIN, "Host": "evil.example"}, 400),
])
def test_mysql_api_browser_boundary_before_store(http, headers, status):
    client, store = http
    assert client.post("/tasks", content='{"title":"test"}', headers=headers).status_code == status
    assert store.calls == []


def test_mysql_api_wrong_host_rejected_even_for_get(http):
    client, store = http
    assert client.get("/health", headers={"Host": "localhost:18016"}).status_code == 400
    assert store.calls == []


def test_mysql_api_validation_does_not_echo_unknown_sensitive_input(http):
    client, store = http
    response = client.post("/tasks", headers={"Origin": api.ORIGIN}, json={
        "title": "ok", "password": "synthetic-do-not-echo",
    })
    assert response.status_code == 422
    assert "synthetic-do-not-echo" not in response.text
    assert store.calls == []


@pytest.mark.parametrize("method,path", [
    ("get", "/ready"), ("get", "/tasks/7"), ("get", "/tasks/7/events"), ("post", "/tasks"),
])
def test_mysql_api_database_failures_are_sanitized_503(http, method, path):
    client, store = http
    store.failure = True
    kwargs = {"json": {"title": "ok"}, "headers": {"Origin": api.ORIGIN}} if method == "post" else {}
    response = getattr(client, method)(path, **kwargs)
    assert response.status_code == 503
    assert response.json() == {"detail": "Database operation failed"}


def test_mysql_api_openapi_exposes_only_the_small_lesson_contract(http):
    client, _ = http
    paths = client.get("/openapi.json").json()["paths"]
    assert set(paths) == {"/health", "/ready", "/tasks", "/tasks/{task_id}", "/tasks/{task_id}/events"}
    assert client.get("/docs").status_code == 200


class DriverError(Exception):
    pass


class Connection:
    def __init__(self, reads=(), *, fail_statement=None, commit_error=False):
        self.reads = deque(reads)
        self.fail_statement = fail_statement
        self.commit_error = commit_error
        self.calls = []
        self.statements = []

    def cursor(self):
        connection = self

        class Cursor:
            lastrowid = 7

            def __enter__(self):
                return self

            def __exit__(self, *args):
                connection.calls.append("cursor_closed")

            def execute(self, sql, values=None):
                connection.statements.append((sql, values))
                if connection.fail_statement == len(connection.statements):
                    raise DriverError(3819, "synthetic-secret-SQL")

            def fetchone(self):
                return connection.reads.popleft()

            def fetchall(self):
                return connection.reads.popleft()

        return Cursor()

    def begin(self):
        self.calls.append("begin")

    def commit(self):
        self.calls.append("commit")
        if self.commit_error:
            raise DriverError(2006, "synthetic lost acknowledgement")

    def rollback(self):
        self.calls.append("rollback")

    def close(self):
        self.calls.append("close")


def store_for(connection):
    return stores.MySQLStore(lambda: connection, database_errors=(DriverError,))


def test_mysql_store_create_owns_transaction_and_closes_before_return(monkeypatch):
    monkeypatch.setattr(stores, "uuid4", lambda: KEY)
    connection = Connection([task("literal ' text")])
    assert store_for(connection).create("literal ' text") == task("literal ' text")
    assert connection.calls == ["begin", "cursor_closed", "commit", "close"]
    assert connection.statements[0][1] == (KEY, "literal ' text")
    assert "literal ' text" not in connection.statements[0][0]
    assert connection.statements[1][1] == (7, "created")
    assert connection.statements[2][1] == (7,)


@pytest.mark.parametrize("failure", [1, 2, 3])
def test_mysql_store_sql_failure_rolls_back_closes_and_redacts(failure, caplog):
    connection = Connection(fail_statement=failure)
    with pytest.raises(stores.DatabaseFailure) as captured:
        store_for(connection).create("synthetic")
    assert captured.value.args == ()
    assert "rollback" in connection.calls and "commit" not in connection.calls
    assert connection.calls[-1] == "close"
    assert "3819" in caplog.text and "synthetic-secret-SQL" not in caplog.text


def test_mysql_store_commit_error_is_not_success_and_attempts_rollback():
    connection = Connection([task()], commit_error=True)
    with pytest.raises(stores.DatabaseFailure):
        store_for(connection).create("synthetic")
    assert connection.calls[-3:] == ["commit", "rollback", "close"]


def test_mysql_store_missing_created_row_rolls_back():
    connection = Connection([None])
    with pytest.raises(stores.DatabaseFailure):
        store_for(connection).create("synthetic")
    assert "rollback" in connection.calls and "commit" not in connection.calls
    assert connection.calls[-1] == "close"


def test_mysql_store_get_binds_id_and_closes():
    connection = Connection([task()])
    assert store_for(connection).get(7) == task()
    assert connection.statements == [(stores.TASK_SQL, (7,))]
    assert connection.calls[-1] == "close"


def test_mysql_store_get_missing_closes_and_raises_domain_error():
    connection = Connection([None])
    with pytest.raises(stores.TaskMissing):
        store_for(connection).get(7)
    assert connection.calls[-1] == "close"


@pytest.mark.parametrize("items,next_id", [([], 3), ([{"id": 9, "task_id": 7, "kind": "created"}], 9)])
def test_mysql_store_events_bind_pagination_and_use_stable_id_order(items, next_id):
    connection = Connection([{"id": 7}, items])
    assert store_for(connection).events(7, 3, 5) == {
        "items": items, "after_id": 3, "limit": 5, "next_after_id": next_id,
    }
    assert connection.statements[1][1] == (7, 3, 5)
    assert "ORDER BY id LIMIT %s" in connection.statements[1][0]
    assert connection.calls[-1] == "close"


def test_mysql_store_events_missing_parent_does_not_query_events():
    connection = Connection([None])
    with pytest.raises(stores.TaskMissing):
        store_for(connection).events(7, 0, 20)
    assert len(connection.statements) == 1
    assert connection.calls[-1] == "close"


def test_mysql_store_connector_failure_sanitized(caplog):
    def connect():
        raise DriverError(1045, "synthetic-password")

    store = stores.MySQLStore(connect, database_errors=(DriverError,))
    with pytest.raises(stores.DatabaseFailure):
        store.get(7)
    assert "1045" in caplog.text and "synthetic-password" not in caplog.text


def test_mysql_store_ready_checks_identity_tls_and_both_tables_without_writing():
    connection = Connection([
        {"account": "task_app@%", "database_name": "task_service", "autocommit": 0},
        {"Value": "TLS-synthetic"},
    ])
    assert store_for(connection).ready() == {"status": "ready", "database": "task_service", "tls": True}
    assert len(connection.statements) == 4
    assert all(sql.startswith(("SELECT", "SHOW")) for sql, _ in connection.statements)
    assert connection.calls[-1] == "close" and "commit" not in connection.calls


@pytest.mark.parametrize("info,cipher", [
    (None, {"Value": "TLS"}),
    ({"account": "root@localhost", "database_name": "task_service", "autocommit": 0}, {"Value": "TLS"}),
    ({"account": "task_app@%", "database_name": "wrong", "autocommit": 0}, {"Value": "TLS"}),
    ({"account": "task_app@%", "database_name": "task_service", "autocommit": 1}, {"Value": "TLS"}),
    ({"account": "task_app@%", "database_name": "task_service", "autocommit": 0}, {"Value": ""}),
])
def test_mysql_store_ready_rejects_wrong_session_and_closes(info, cipher):
    connection = Connection([info, cipher])
    with pytest.raises(stores.DatabaseFailure):
        store_for(connection).ready()
    assert connection.calls[-1] == "close"
