"""Apply an explicitly reviewed memory-correction manifest after a database backup.

No discovery or heuristic rewriting happens here. Without --apply this only
validates the reviewed identities, revisions and hashes in a read-only transaction.
The manifest contains private text and must stay outside Git and public artifacts.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup-reference', default='')
    args = parser.parse_args()
    document = json.loads(args.manifest.read_text(encoding='utf-8-sig'))
    if document.get('contract') != 'reviewed-memory-corrections-v1' or not document.get('reviewed_at'):
        raise ValueError('Expected an explicitly reviewed correction manifest')
    entries = document.get('entries')
    if not isinstance(entries,list) or not 1 <= len(entries) <= 200:
        raise ValueError('Manifest must contain 1 to 200 reviewed records')
    if args.apply and not args.backup_reference.strip():
        raise ValueError('Record the verified pre-change database backup reference')
    from classroom_app.database import get_db_connection
    from classroom_app.services.wanderer_memory_correction_service import _revision, content_hash, correct, validate_request
    results=[]
    with get_db_connection() as conn:
        from classroom_app.db.connection import get_configured_db_engine
        if not args.apply and get_configured_db_engine()=='postgres':
            conn.execute('SET TRANSACTION READ ONLY')
        for entry in entries:
            wid = int(entry['wanderer_id'])
            request = entry['request']
            details = validate_request(wid, request, actor={'id':0,'kind':'release_operator'})
            table = details['table']
            row = conn.execute(f'SELECT * FROM {table} WHERE id=? AND wanderer_id=?', (details['ident'],wid)).fetchone()
            previous=conn.execute('SELECT request_hash FROM wanderer_memory_corrections WHERE id=?',(details['operation'],)).fetchone()
            if not row:
                raise ValueError('Reviewed record no longer exists for the owner')
            if previous and previous['request_hash'] != details['request_hash']:
                raise ValueError('Reviewed request ID already belongs to different correction content or ownership')
            if not previous and row['status'] == 'superseded':
                raise ValueError('Reviewed record has a newer version; review the active record instead')
            if not previous and (_revision(conn,request['target_kind'],request['target_id']) != request['expected_revision']
                    or content_hash(dict(row),request['target_kind']) != request['expected_content_hash']):
                raise ValueError('Reviewed record changed; inspect it again before applying')
            if args.apply:
                result=correct(conn,wid,request,actor={'id':0,'kind':'release_operator'})
                results.append({'correction_id':result['correction']['id'],'replayed':result['replayed']})
        if args.apply:
            conn.commit()
        else:
            conn.rollback()
    print(json.dumps({'status':'applied' if args.apply else 'validated','count':len(entries),
                      'backup_reference':args.backup_reference,'results':results},ensure_ascii=True))


if __name__=='__main__':
    main()
