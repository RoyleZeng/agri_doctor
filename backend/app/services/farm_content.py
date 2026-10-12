"""未發布的草稿不可流入農場；找不到覆寫時沿用原本有來源的範本。"""
from copy import deepcopy
from sqlalchemy import select
from app.models.farm_content import FarmContent
from app.services.farm_templates import TEMPLATES, VERSION, source_index


def template_base(template):
    return deepcopy(template) | {'sources': source_index()}


def published_templates(db):
    templates = {t['key']: template_base(t) | {'version': VERSION} for t in TEMPLATES}
    for row in db.scalars(select(FarmContent)).all():
        if row.published:
            templates[row.key] = deepcopy(row.published) | {'version': row.published_version}
    return list(templates.values())


def editor_rows(db):
    rows = {r.key: r for r in db.scalars(select(FarmContent)).all()}
    keys = list(dict.fromkeys([t['key'] for t in TEMPLATES] + list(rows)))
    base = {t['key']: template_base(t) for t in TEMPLATES}
    return [dict(key=key, revision=rows[key].revision if key in rows else 0,
                 draft=rows[key].draft if key in rows else base[key],
                 published_version=rows[key].published_version if key in rows else VERSION) for key in keys]
