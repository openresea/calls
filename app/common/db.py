import os

import psycopg2
import psycopg2.pool
from psycopg2.pool import PoolError, ThreadedConnectionPool


class StorageUnavailableError(RuntimeError):
    pass


_ops_pool: ThreadedConnectionPool | None = None


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name, "") or "").strip()
    return int(raw) if raw else default


def _pool_from_env(prefix: str, *, default_max: int = 10) -> ThreadedConnectionPool:
    user = os.environ[f"{prefix}_USER"]
    password = os.environ[f"{prefix}_PASS"]
    dbname = os.environ[f"{prefix}_NAME"]
    pool_min = _env_int(f"{prefix}_POOL_MIN", 1)
    pool_max = _env_int(f"{prefix}_POOL_MAX", default_max)
    if pool_min < 1:
        raise RuntimeError(f"{prefix}_POOL_MIN must be >= 1")
    if pool_max < pool_min:
        raise RuntimeError(f"{prefix}_POOL_MAX must be >= {prefix}_POOL_MIN")

    host = os.environ.get(f"{prefix}_HOST", "127.0.0.1")
    port = int(os.environ.get(f"{prefix}_PORT", "5432"))
    sslmode = os.environ.get(f"{prefix}_SSLMODE", "").strip()
    connect_kwargs = {}
    if sslmode:
        connect_kwargs["sslmode"] = sslmode
    return ThreadedConnectionPool(
        pool_min,
        pool_max,
        user=user,
        password=password,
        dbname=dbname,
        host=host,
        port=port,
        connect_timeout=10,
        **connect_kwargs,
    )


def init_pools() -> None:
    global _ops_pool
    if _ops_pool is None:
        _ops_pool = _pool_from_env("OPS_DB", default_max=10)


def get_ops():
    if _ops_pool is None:
        raise StorageUnavailableError("ops pool not initialised")
    last_error: Exception | None = None
    for _ in range(2):
        try:
            conn = _ops_pool.getconn()
        except PoolError as e:
            raise StorageUnavailableError("ops pool exhausted") from e
        except psycopg2.Error as e:
            raise StorageUnavailableError("ops database connection unavailable") from e

        try:
            if conn.closed:
                raise psycopg2.InterfaceError("connection already closed")
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            return conn
        except psycopg2.Error as e:
            last_error = e
            try:
                _ops_pool.putconn(conn, close=True)
            except Exception:
                pass

    raise StorageUnavailableError("ops database connection unavailable") from last_error


def put_ops(conn) -> None:
    if _ops_pool is None:
        raise StorageUnavailableError("ops pool not initialised")
    _ops_pool.putconn(conn, close=bool(getattr(conn, "closed", False)))


def purge_expired_rows() -> None:
    """Each service maintains only the authentication tables it may access."""
    admin = os.environ.get("APP_MODULE") == "app.admin.main:app"
    tables = (
        ["admin_login_codes", "pb_admin_sessions"]
        if admin
        else ["canvasser_login_codes", "pb_sessions"]
    )
    conn = get_ops()
    try:
        with conn, conn.cursor() as cur:
            from psycopg2 import sql

            for table in tables:
                cur.execute(
                    sql.SQL(
                        "DELETE FROM {} WHERE expires_at<now()-interval '1 day'"
                    ).format(sql.Identifier(table))
                )
            cur.execute(
                "DELETE FROM email_auth_limits WHERE resets_at<now()-interval '1 day'"
            )
    finally:
        put_ops(conn)
