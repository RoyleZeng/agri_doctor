"""Optional SMTP change notices. Disabled until an operator configures delivery.

A deterministic, exclusive receipt file prevents automatic resending after an
uncertain network outcome. No external message is sent by installation/tests.
"""
from __future__ import annotations
import hashlib
import json
import os
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from sqlalchemy import select
from app.models.updates import SourceChange
from app.core.config import settings
from app.crawlers.base import job_lock


def deliver_ban_updates(db, *, sender=None):
    if not settings.update_email_enabled:
        return {'status':'disabled','sent':0}
    recipient = settings.update_email_to.strip()
    if not recipient or not settings.smtp_host or not settings.update_email_from:
        return {'status':'not_configured','sent':0}
    if any(c in recipient + settings.update_email_from for c in '\r\n'):
        raise ValueError('Invalid mail headers')
    directory = settings.raw_data_dir / 'notification_receipts'
    directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    sent = 0
    with job_lock(db, 'change-notifications'):
        changes = db.scalars(select(SourceChange).where(SourceChange.kind.in_(['fda_banned_added','fda_banned_changed']),
            SourceChange.detected_at >= datetime.now(timezone.utc)-timedelta(days=7)).order_by(SourceChange.id)).all()
        for change in changes:
            key = hashlib.sha256(f'{change.id}:{recipient}'.encode()).hexdigest()
            path = directory / f'{key}.json'
            try:
                fd = os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            except FileExistsError:
                continue
            with os.fdopen(fd,'w') as f: json.dump({'change_id':change.id,'status':'sending'},f)
            message = EmailMessage()
            message['Subject'] = f'植物醫生｜禁用名單更新 #{change.id}'
            message['From'],message['To'] = settings.update_email_from,recipient
            message['Message-ID'] = f'<{key}@agri-doctor.local>'
            message.set_content(f"{change.title}\n\n禁止銷售／使用日期（來源原文）：{change.effective_date or '未提供，請人工核對'}\n"+
                f"\n前次快照 #{change.previous_snapshot_id}\n{json.dumps(change.before,ensure_ascii=False,indent=2)}\n"+
                f"\n本次快照 #{change.snapshot_id}\n{json.dumps(change.after,ensure_ascii=False,indent=2)}\n"+
                f"\n官方來源：{change.source_url or '請至食藥署查核'}\n本站發現日不是公告日或生效日；請核對製造、銷售及使用的不同管制階段。")
            try:
                if sender:
                    sender(message)
                else:
                    with smtplib.SMTP_SSL(settings.smtp_host,settings.smtp_port,timeout=20,context=ssl.create_default_context()) as smtp:
                        if settings.smtp_username:
                            smtp.login(settings.smtp_username,settings.smtp_password.get_secret_value())
                        smtp.send_message(message)
                receipt = {'change_id':change.id,'status':'sent','sent_at':datetime.now(timezone.utc).isoformat()}
                sent += 1
            except Exception:
                receipt = {'change_id':change.id,'status':'unknown','message':'傳送結果需人工查核；不自動重送以免重複通知'}
            path.write_text(json.dumps(receipt,ensure_ascii=False),encoding='utf-8')
    return {'status':'processed','sent':sent}
