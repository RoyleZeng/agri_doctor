"""三源衝突總覽。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.db.session import get_db
from app.models import Crop, IssueType, MatchIssue, Pest, Severity

router = APIRouter(tags=["issues"])


@router.get("/issues")
def list_issues(
    issue_type: IssueType | None = Query(None),
    severity: Severity | None = Query(None),
    crop_id: int | None = Query(None),
    resolved: bool = Query(False),
    limit: int = Query(100, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    stmt = select(MatchIssue).where(MatchIssue.resolved.is_(resolved))
    if issue_type is not None:
        stmt = stmt.where(MatchIssue.issue_type == issue_type)
    if severity is not None:
        stmt = stmt.where(MatchIssue.severity == severity)
    if crop_id is not None:
        stmt = stmt.where(MatchIssue.crop_id == crop_id)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.options(selectinload(MatchIssue.crop), selectinload(MatchIssue.pest))
        .order_by(MatchIssue.severity, MatchIssue.id)
        .offset(offset)
        .limit(limit)
    ).all()

    return {
        "total": total,
        "items": [
            {
                "id": i.id,
                "issue_type": i.issue_type.value,
                "severity": i.severity.value,
                "crop": i.crop.name if i.crop else None,
                "crop_id": i.crop_id,
                "pest": i.pest.name if i.pest else None,
                "pest_id": i.pest_id,
                "summary": i.summary,
                "detail": i.detail,
            }
            for i in rows
        ],
    }


@router.get("/issues/summary")
def issue_summary(db: Session = Depends(get_db)) -> dict:
    rows = db.execute(
        select(MatchIssue.issue_type, MatchIssue.severity, func.count())
        .where(MatchIssue.resolved.is_(False))
        .group_by(MatchIssue.issue_type, MatchIssue.severity)
    ).all()
    return {
        "by_type": [
            {"issue_type": t.value, "severity": s.value, "count": c}
            for t, s, c in rows
        ],
        "total": sum(c for _, _, c in rows),
    }
