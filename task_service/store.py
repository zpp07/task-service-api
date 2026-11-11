"""One connection per operation; SQL belongs here, never in HTTP routes."""

from contextlib import closing, contextmanager
import logging
from uuid import uuid4

from task_service.database import transaction


logger = logging.getLogger(__name__)

TASK_SQL = (
    "SELECT t.id, t.run_key, t.title, t.status, COUNT(e.id) AS event_count "
    "FROM practice_tasks AS t LEFT JOIN practice_events AS e ON e.task_id = t.id "
    "WHERE t.id = %s GROUP BY t.id, t.run_key, t.title, t.status"
)


class TaskMissing(Exception):
    pass


class DatabaseFailure(Exception):
    pass


class MySQLStore:
    def __init__(self, connector, *, database_errors=None):
        if database_errors is None:
            import pymysql

            database_errors = (pymysql.MySQLError,)
        self._connect = connector
        self._errors = database_errors

    @contextmanager
    def _connection(self):
        try:
            with closing(self._connect()) as connection:
                yield connection
        except self._errors as error:
            number = error.args[0] if error.args and type(error.args[0]) is int else None
            logger.warning("mysql_operation_failed errno=%s", number)
            # Raw driver errors can include SQL/data. Do not expose them to HTTP
            # clients or logs; no retries, particularly after uncertain COMMIT.
            raise DatabaseFailure() from None

    def ready(self):
        with self._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT CURRENT_USER() AS account, DATABASE() AS database_name, @@autocommit AS autocommit")
                info = cursor.fetchone()
                cursor.execute("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
                cipher = cursor.fetchone()
                if (not info or info["database_name"] != "task_service"
                        or not info["account"].startswith("task_app@")
                        or info["autocommit"] != 0 or not cipher or not cipher["Value"]):
                    raise DatabaseFailure()
                # Read-only table/column availability check, NOT schema migration.
                cursor.execute("SELECT id, run_key, title, status FROM practice_tasks LIMIT 0")
                cursor.execute("SELECT id, task_id, kind FROM practice_events LIMIT 0")
        return {"status": "ready", "database": "task_service", "tls": True}

    def create(self, title):
        run_key = str(uuid4())  # Server generated; caller cannot choose it.
        with self._connection() as connection:
            with transaction(connection):
                with connection.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO practice_tasks (run_key, title) VALUES (%s, %s)",
                        (run_key, title),
                    )
                    task_id = cursor.lastrowid
                    cursor.execute(
                        "INSERT INTO practice_events (task_id, kind) VALUES (%s, %s)",
                        (task_id, "created"),
                    )
                    cursor.execute(TASK_SQL, (task_id,))
                    task = cursor.fetchone()
                    if task is None:
                        raise DatabaseFailure()
        # This return is AFTER transaction.__exit__ committed and connection
        # closed. Do not return an uncommitted success response.
        return task

    def get(self, task_id):
        with self._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(TASK_SQL, (task_id,))
                task = cursor.fetchone()
                if task is None:
                    raise TaskMissing()
        return task

    def events(self, task_id, after_id, limit):
        with self._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT id FROM practice_tasks WHERE id = %s", (task_id,))
                if cursor.fetchone() is None:
                    raise TaskMissing()
                cursor.execute(
                    "SELECT id, task_id, kind FROM practice_events "
                    "WHERE task_id = %s AND id > %s ORDER BY id LIMIT %s",
                    (task_id, after_id, limit),
                )
                items = cursor.fetchall()
        return {
            "items": items, "after_id": after_id, "limit": limit,
            "next_after_id": items[-1]["id"] if items else after_id,
        }
