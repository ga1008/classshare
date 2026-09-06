"""Controlled operator import. Default validates in a rolled-back transaction.

Usage: python -B tools/career_catalog_import.py path/to/approved.json [--apply]
Input: {"aliases": [...], "postings": [...], "network_restores": [...]}.
Aliases: school_code, alias_name, canonical_name, reason.
Postings: source, external_id, source_url, title, company, city, status(open/closed/expired),
job_description, checked_at, expires_at (ISO8601 with timezone). Optional school_code,
employment_type, published_at. Empty school_code means a shared public source.
Network restores: school_code, major_key, revision, reason. Restoration appends a
new version and supersedes pending shared work; it never deletes a version.
Use only a source the operator is authorized to maintain; import does not crawl.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input",type=Path)
    parser.add_argument("--apply",action="store_true",help="Commit the reviewed import to the configured database")
    args=parser.parse_args()
    if args.input.stat().st_size>8*1024*1024:
        parser.error("Import exceeds 8 MiB; split the reviewed batch")
    data=json.loads(args.input.read_text(encoding="utf-8-sig"))
    if not isinstance(data,dict) or set(data)-{"aliases","postings","network_restores"}:
        parser.error("Input must contain aliases and/or postings arrays")
    if any(not isinstance(data.get(key,[]),list) or len(data.get(key,[]))>200 for key in ("aliases","postings","network_restores")):
        parser.error("Each batch must contain at most 200 aliases and 200 postings")
    from classroom_app.database import get_db_connection
    from classroom_app.db.schema_career_path import ensure_career_path_schema
    from classroom_app.services.career_major_mapping_service import set_career_major_alias
    from classroom_app.services.career_job_posting_service import upsert_job_posting
    from classroom_app.services.career_lifecycle_service import restore_network_version
    with get_db_connection() as conn:
        try:
            ensure_career_path_schema(conn)
            aliases=[set_career_major_alias(conn,**item) for item in data.get("aliases",[])]
            postings=[upsert_job_posting(conn,item) for item in data.get("postings",[])]
            restored=[restore_network_version(conn,**item) for item in data.get("network_restores",[])]
            if args.apply:
                conn.commit()
            else:
                conn.rollback()
        except Exception:
            conn.rollback()
            raise
    print(json.dumps({"applied":args.apply,"alias_count":len(aliases),"posting_count":len(postings),
                      "postings":postings,"restored_networks":restored},ensure_ascii=False))


if __name__=="__main__":
    main()
