"""Connection factory and transaction boundary, evolved from lesson 15."""

from contextlib import contextmanager
from dataclasses import dataclass, field
import os


@dataclass(frozen=True)
class Settings:
    password: str = field(repr=False)
    ca_file: str | None = None


def load_settings():
    return Settings(password=os.environ["MYSQL_PASSWORD"], ca_file=os.getenv("MYSQL_CA_FILE"))


def open_connection(config):
    import pymysql

    return pymysql.connect(
        host="db", port=3306, user="task_app", password=config.password,
        database="task_service", charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor, autocommit=False,
        connect_timeout=5, read_timeout=10, write_timeout=10,
        local_infile=False,
        ssl_ca=config.ca_file,
        ssl_verify_cert=bool(config.ca_file),
        ssl_verify_identity=bool(config.ca_file),
        init_command="SET time_zone = '+00:00'",
    )


@contextmanager
def transaction(connection):
    connection.begin()
    try:
        yield
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
