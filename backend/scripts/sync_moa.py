"""手動補抓或僅匯入舊快取；不把金鑰放命令列。容器內從 /app 執行。"""
import argparse
import json
from app.db.session import SessionLocal
from app.services.moa_catalog import CATALOG
from app.services.moa_sync import sync, import_files

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--only',choices=list(CATALOG),action='append')
    parser.add_argument('--import-files',action='store_true')
    args=parser.parse_args()
    with SessionLocal() as db:
        result={'imported':import_files(db)} if args.import_files else sync(db,names=args.only)
    print(json.dumps(result,ensure_ascii=False))
    if not args.import_files and any(v!='ok' for v in result.values()):raise SystemExit(1)
