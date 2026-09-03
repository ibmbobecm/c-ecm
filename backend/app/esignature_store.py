"""Signature-request tracking (Repository pattern, same shape as every
other *_store.py module) — records what was sent to DocuSign for which
resource, and its last known status. The actual signing happens entirely
on DocuSign's side; this module never touches document content.
"""

import datetime
import json
import uuid

import sqlalchemy as sa

from . import db

_metadata = sa.MetaData()

esignature_requests = sa.Table(
    "esignature_requests", _metadata,
    sa.Column("id", sa.String(32), primary_key=True),
    sa.Column("connection_id", sa.String(32), nullable=False),
    sa.Column("resource_id", sa.String(255), nullable=False),
    sa.Column("resource_type", sa.String(64), nullable=False),
    sa.Column("resource_name", sa.String(255)),
    sa.Column("envelope_id", sa.String(64), nullable=False),
    sa.Column("status", sa.String(64), nullable=False, server_default="sent"),
    sa.Column("signers_json", sa.Text, nullable=False, server_default="[]"),
    sa.Column("subject", sa.Text),
    sa.Column("requested_by", sa.String(255), nullable=False),
    sa.Column("created_at", sa.String(40), nullable=False),
    sa.Column("completed_at", sa.String(40)),
    sa.Column("signed_version_number", sa.Integer),
    sa.Index("idx_esig_resource", "connection_id", "resource_id"),
    sa.Index("idx_esig_envelope", "envelope_id", unique=True),
)

_engine = db.get_engine("esignature")


def init_db() -> None:
    db.create_all(_metadata, "esignature")


def _row(row) -> dict:
    return {
        "id": row["id"],
        "connection_id": row["connection_id"],
        "resource_id": row["resource_id"],
        "resource_type": row["resource_type"],
        "resource_name": row["resource_name"],
        "envelope_id": row["envelope_id"],
        "status": row["status"],
        "signers": json.loads(row["signers_json"]),
        "subject": row["subject"],
        "requested_by": row["requested_by"],
        "created_at": row["created_at"],
        "completed_at": row["completed_at"],
        "signed_version_number": row["signed_version_number"],
    }


def create(
    connection_id: str, resource_id: str, resource_type: str, resource_name: str | None,
    envelope_id: str, signers: list[dict], subject: str, requested_by: str,
) -> dict:
    rid = uuid.uuid4().hex
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with _engine.begin() as conn:
        conn.execute(
            esignature_requests.insert().values(
                id=rid, connection_id=connection_id, resource_id=resource_id, resource_type=resource_type,
                resource_name=resource_name, envelope_id=envelope_id, status="sent",
                signers_json=json.dumps(signers), subject=subject, requested_by=requested_by, created_at=now,
            )
        )
        row = conn.execute(sa.select(esignature_requests).where(esignature_requests.c.id == rid)).mappings().first()
    return _row(row)


def get(request_id: str) -> dict | None:
    with _engine.connect() as conn:
        row = conn.execute(
            sa.select(esignature_requests).where(esignature_requests.c.id == request_id)
        ).mappings().first()
    return _row(row) if row else None


def get_by_envelope_id(envelope_id: str) -> dict | None:
    with _engine.connect() as conn:
        row = conn.execute(
            sa.select(esignature_requests).where(esignature_requests.c.envelope_id == envelope_id)
        ).mappings().first()
    return _row(row) if row else None


def list_for_resource(connection_id: str, resource_id: str) -> list[dict]:
    stmt = (
        sa.select(esignature_requests)
        .where(esignature_requests.c.connection_id == connection_id, esignature_requests.c.resource_id == resource_id)
        .order_by(esignature_requests.c.created_at.desc())
    )
    with _engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [_row(r) for r in rows]


def list_all(*, connection_id: str | None = None, status: str | None = None) -> list[dict]:
    stmt = sa.select(esignature_requests)
    if connection_id:
        stmt = stmt.where(esignature_requests.c.connection_id == connection_id)
    if status:
        stmt = stmt.where(esignature_requests.c.status == status)
    stmt = stmt.order_by(esignature_requests.c.created_at.desc())
    with _engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [_row(r) for r in rows]


def update_status(request_id: str, status: str, *, completed: bool = False, signed_version_number: int | None = None) -> None:
    with _engine.begin() as conn:
        if completed:
            conn.execute(
                esignature_requests.update()
                .where(esignature_requests.c.id == request_id)
                .values(
                    status=status,
                    completed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    signed_version_number=signed_version_number,
                )
            )
        else:
            conn.execute(
                esignature_requests.update().where(esignature_requests.c.id == request_id).values(status=status)
            )


def delete_for_resource(connection_id: str, resource_id: str) -> None:
    with _engine.begin() as conn:
        conn.execute(
            esignature_requests.delete().where(
                esignature_requests.c.connection_id == connection_id, esignature_requests.c.resource_id == resource_id
            )
        )


def delete_for_connection(connection_id: str) -> None:
    with _engine.begin() as conn:
        conn.execute(esignature_requests.delete().where(esignature_requests.c.connection_id == connection_id))
