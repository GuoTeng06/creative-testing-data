"""Persistent operation-task API used by the B-computer worker."""

import hmac
import json
import os
import threading
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

import pymysql
from fastapi import APIRouter, Header, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator, model_validator

from data_loader import DB_CONFIG


TASK_TABLE = os.environ.get("OPERATION_TASK_TABLE", "operation_tasks").strip()
OPERATION_API_KEY = os.environ.get("OPERATION_API_KEY", "")

if not TASK_TABLE or not TASK_TABLE.replace("_", "").isalnum():
    raise RuntimeError("OPERATION_TASK_TABLE must contain only letters, numbers, and underscores")

_table_ready = False
_table_lock = threading.Lock()


def _require_api_key(x_operation_key: str | None = Header(default=None)) -> None:
    """Require a shared key only when OPERATION_API_KEY is configured."""
    if OPERATION_API_KEY and (
        not x_operation_key
        or not hmac.compare_digest(x_operation_key, OPERATION_API_KEY)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="鏃犳晥鐨勪换鍔℃帴鍙ｅ瘑閽?,
        )


router = APIRouter(
    prefix="/api/operation",
    tags=["operation"],
    dependencies=[],
)


class OperationTaskCreate(BaseModel):
    task_type: Literal["delist", "promotion_adjust"] = "delist"
    link_ids: list[str] = Field(min_length=1, max_length=1000)
    store_names: list[str] = Field(default_factory=list, max_length=1000)
    operator: str = Field(default="", max_length=255)
    direction: Literal["up", "down"] | None = None
    value: str | float | int | None = None

    @field_validator("link_ids")
    @classmethod
    def validate_link_ids(cls, values: list[str]) -> list[str]:
        cleaned = [str(value).strip() for value in values]
        if any(not value for value in cleaned):
            raise ValueError("link_ids 涓嶈兘鍖呭惈绌哄€?)
        return cleaned

    @field_validator("store_names")
    @classmethod
    def clean_store_names(cls, values: list[str]) -> list[str]:
        return [str(value).strip() for value in values]

    @model_validator(mode="after")
    def validate_promotion_adjust(self):
        if self.task_type == "promotion_adjust":
            if self.direction not in {"up", "down"}:
                raise ValueError("璋冩暣鎶曚骇浠诲姟蹇呴』鎻愪緵 direction=up 鎴?direction=down")
            if self.value is None or str(self.value).strip() == "":
                raise ValueError("璋冩暣鎶曚骇浠诲姟蹇呴』鎻愪緵 value")
        return self


class OperationTaskComplete(BaseModel):
    task_id: str = Field(min_length=1, max_length=64)
    result: Literal["ok", "failed", "error"] = "ok"
    error: str = Field(default="", max_length=4000)

    @field_validator("task_id")
    @classmethod
    def clean_task_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("task_id 涓嶈兘涓虹┖")
        return value


def _get_conn():
    config = dict(DB_CONFIG)
    config["cursorclass"] = pymysql.cursors.DictCursor
    config["autocommit"] = False
    return pymysql.connect(**config)


def _ensure_table(conn) -> None:
    global _table_ready
    if _table_ready:
        return

    with _table_lock:
        if _table_ready:
            return
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                CREATE TABLE IF NOT EXISTS `{TASK_TABLE}` (
                    `id` VARCHAR(64) NOT NULL,
                    `task_type` VARCHAR(32) NOT NULL,
                    `payload_json` LONGTEXT NOT NULL,
                    `status` VARCHAR(20) NOT NULL DEFAULT 'pending',
                    `result` VARCHAR(20) NULL,
                    `error` TEXT NULL,
                    `created_at` DATETIME(6) NOT NULL,
                    `completed_at` DATETIME(6) NULL,
                    PRIMARY KEY (`id`),
                    KEY `idx_operation_status_created`
                        (`status`, `created_at`, `id`)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
        conn.commit()
        _table_ready = True


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _datetime_text(value: Any) -> str:
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc).isoformat()
    return str(value or "")


def _row_to_task(row: dict[str, Any]) -> dict[str, Any]:
    try:
        task = json.loads(row.get("payload_json") or "{}")
    except (TypeError, ValueError):
        task = {}
    if not isinstance(task, dict):
        task = {}

    task.update(
        {
            "id": str(row["id"]),
            "task_type": row["task_type"],
            "status": row["status"],
            "created_at": _datetime_text(row["created_at"]),
        }
    )
    return task


def _database_error(action: str, exc: Exception) -> HTTPException:
    print(f"[OperationAPI] {action} failed: {exc}")
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="浠诲姟鏁版嵁搴撴殏涓嶅彲鐢?,
    )


@router.post("/create", status_code=status.HTTP_201_CREATED)
def create_operation_task(
    payload: OperationTaskCreate,
    x_operation_key: str | None = Header(default=None),
):
    _require_api_key(x_operation_key)
    task_id = uuid4().hex
    created_at = _utc_now()
    task = payload.model_dump(mode="json")
    task.update(
        {
            "id": task_id,
            "status": "pending",
            "created_at": _datetime_text(created_at),
        }
    )

    conn = None
    try:
        conn = _get_conn()
        _ensure_table(conn)
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO `{TASK_TABLE}`
                    (`id`, `task_type`, `payload_json`, `status`, `created_at`)
                VALUES (%s, %s, %s, 'pending', %s)
                """,
                (
                    task_id,
                    payload.task_type,
                    json.dumps(task, ensure_ascii=False, separators=(",", ":")),
                    created_at,
                ),
            )
        conn.commit()
        return {"success": True, "task": task}
    except Exception as exc:
        if conn:
            conn.rollback()
        raise _database_error("create task", exc) from exc
    finally:
        if conn:
            conn.close()


@router.get("/pending")
def get_pending_operation_tasks(
    limit: int = Query(default=100, ge=1, le=500),
    x_operation_key: str | None = Header(default=None),
):
    _require_api_key(x_operation_key)
    conn = None
    try:
        conn = _get_conn()
        _ensure_table(conn)
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT `id`, `task_type`, `payload_json`, `status`, `created_at`
                FROM `{TASK_TABLE}`
                WHERE `status` = 'pending'
                ORDER BY `created_at` ASC, `id` ASC
                LIMIT %s
                """,
                (limit,),
            )
            tasks = [_row_to_task(row) for row in cursor.fetchall()]
        return {"tasks": tasks, "count": len(tasks)}
    except Exception as exc:
        raise _database_error("list pending tasks", exc) from exc
    finally:
        if conn:
            conn.close()


@router.post("/complete")
def complete_operation_task(
    payload: OperationTaskComplete,
    x_operation_key: str | None = Header(default=None),
):
    _require_api_key(x_operation_key)
    conn = None
    try:
        conn = _get_conn()
        _ensure_table(conn)
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT `status`, `result`, `error`
                FROM `{TASK_TABLE}`
                WHERE `id` = %s
                FOR UPDATE
                """,
                (payload.task_id,),
            )
            current = cursor.fetchone()
            if not current:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="浠诲姟涓嶅瓨鍦?,
                )

            if current["status"] in {"completed", "failed"}:
                conn.commit()
                return {
                    "success": True,
                    "task_id": payload.task_id,
                    "status": current["status"],
                    "result": current["result"],
                    "error": current["error"] or "",
                    "already_completed": True,
                }

            new_status = "completed" if payload.result == "ok" else "failed"
            cursor.execute(
                f"""
                UPDATE `{TASK_TABLE}`
                SET `status` = %s,
                    `result` = %s,
                    `error` = %s,
                    `completed_at` = %s
                WHERE `id` = %s
                """,
                (
                    new_status,
                    payload.result,
                    payload.error,
                    _utc_now(),
                    payload.task_id,
                ),
            )
        conn.commit()
        return {
            "success": True,
            "task_id": payload.task_id,
            "status": new_status,
            "result": payload.result,
            "error": payload.error,
            "already_completed": False,
        }
    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception as exc:
        if conn:
            conn.rollback()
        raise _database_error("complete task", exc) from exc
    finally:
        if conn:
            conn.close()

