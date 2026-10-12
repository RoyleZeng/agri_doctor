"""Fetch a small, explicit set of official cultivation references; never crawl user URLs.

Run: python -m app.crawlers.planning
Snapshots are immutable. Updating references does NOT silently update reviewed templates.
"""
from __future__ import annotations

import hashlib
import argparse
import json
import os
import ssl
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import certifi
from selectolax.parser import HTMLParser

SOURCES = [
    ("rice-care", "台灣良質米生產技術之改進", "農業部", "https://www.moa.gov.tw/ws.php?id=2415", ["水稻", "灌排水"]),
    ("rice-pests", "一期稻作病蟲害管理要點", "農業知識入口網", "https://kmweb.moa.gov.tw/knowledgebase.php?func=1&id=195236&keyword=&type=13180", ["二化螟", "分蘗"]),
    ("tomato-care", "小果番茄栽培管理", "臺南區農業改良場", "https://book.tndais.gov.tw/Brochure/tech96.htm", ["番茄", "定植", "粉蝨"]),
    ("cabbage-care", "甘藍田間管理", "農業知識入口網・甘藍主題館", "https://kmweb.moa.gov.tw/subject/subject.php?id=13325", ["結球", "採收"]),
    ("cabbage-disease", "甘藍黑腐病", "農業知識入口網・甘藍主題館", "https://kmweb.moa.gov.tw/subject/subject.php?id=13434&print=Y", ["黑腐病", "病徵"]),
    ("cabbage-pests", "設施栽培蔬菜害蟲之生態與防治", "臺南區農業改良場", "https://book.tndais.gov.tw/Brochure/tech97.htm", ["小菜蛾", "甘藍"]),
]
ROOT = Path(__file__).resolve().parents[2]


def extract_html(content: bytes, encoding: str = "utf-8") -> str:
    tree = HTMLParser(content.decode(encoding, errors="replace"))
    for node in tree.css("script,style,nav,header,footer"):
        node.decompose()
    return tree.body.text(separator="\n", strip=True) if tree.body else ""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish", action="store_true", help="Publish sources after reviewing their compatibility with the curated templates")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    destination = ROOT / "data" / "raw" / "planning" / now.strftime("%Y%m%dT%H%M%S%fZ")
    destination.mkdir(parents=True)
    results = []
    context = ssl.create_default_context(cafile=certifi.where())
    # The legacy Tainan site omits intermediate certificates. Both bundled certificates
    # chain to the existing certifi TWCA Global Root; TLS/hostname validation stays on.
    context.load_verify_locations(str(Path(__file__).parent / "certs" / "twca_intermediates.pem"))
    if os.environ.get("PLANNING_CA_BUNDLE"):
        context.load_verify_locations(os.environ["PLANNING_CA_BUNDLE"])
    with httpx.Client(timeout=30, follow_redirects=True, verify=context, headers={"User-Agent": "Mozilla/5.0 AgriDoctorBot/0.1 (cultivation-reference archive)", "Accept-Encoding": "identity"}) as client:
        for key, title, publisher, url, required in SOURCES:
            record = dict(id=key, title=title, publisher=publisher, url=url, fetched_at=now.isoformat(), publication_date=None)
            try:
                response = client.get(url)
                response.raise_for_status()
                encoding = "big5" if b"big5" in response.content[:3000].lower() else response.encoding
                body = extract_html(response.content, encoding or "utf-8")
                if not all(word in body for word in required):
                    raise ValueError("Required content markers missing; possible site change or error page")
                digest = hashlib.sha256(response.content).hexdigest()
                (destination / f"{key}.html").write_bytes(response.content)
                (destination / f"{key}.txt").write_text(body, encoding="utf-8")
                record.update(status="ok", sha256=digest, resolved_url=str(response.url), bytes=len(response.content), snapshot=str(destination.relative_to(ROOT) / f"{key}.html"))
            except Exception as exc:
                record.update(status="failed", error=str(exc))
            results.append(record)
            print(f"{key}: {record['status']}", flush=True)
            time.sleep(1.5)
    (destination / "manifest.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    if any(r["status"] != "ok" for r in results):
        raise SystemExit(f"Incomplete crawl; previous published sources preserved. See {destination}")
    # This index records provenance only; curated agronomy remains versioned separately.
    target = ROOT / "app" / "data" / ("planning_sources.json" if args.publish else "planning_sources_candidate.json")
    target.parent.mkdir(exist_ok=True)
    archive = target.parent / "planning_archive"
    archive.mkdir(exist_ok=True)
    # Ship the small reference set with the application for offline provenance checks.
    # Full crawler runs remain in data/raw, which the repository intentionally ignores.
    for record in results:
        raw = (ROOT / record["snapshot"]).read_bytes()
        archived = archive / f"{record['id']}-{record['sha256']}.html"
        if archived.exists() and hashlib.sha256(archived.read_bytes()).hexdigest() != record["sha256"]:
            raise ValueError(f"Archive corruption: {archived}")
        if not archived.exists():
            archived.write_bytes(raw)
        record["snapshot"] = str(archived.relative_to(ROOT))
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)
    print(f"Saved {len(results)} verified source snapshots to {destination}")
    print(f"{'Published' if args.publish else 'Staged for content review'}: {target}")


if __name__ == "__main__":
    main()
