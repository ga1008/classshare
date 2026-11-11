"""Owned-cluster message transaction and administrator CAS races; no production DSN."""
import json
import os
import re
from pathlib import Path
from threading import Lock
import unittest
from unittest.mock import patch
import uuid

from fastapi import HTTPException
from classroom_app.services import social_message_service as social
from classroom_app.services import message_center_service as legacy
from classroom_app.services import wanderer_memory_correction_service as corrections
from classroom_app.services.wanderer_identity_service import load_wanderer, build_wanderer_user_dict
from classroom_app.services.wanderer_resource_service import snapshot
from classroom_app.services.social_common import dm_key
from classroom_app.time_utils import local_iso
from tests import test_wanderer_world_postgres as fixture
from tests import test_wanderer_legacy_delivery as authority_fixture


@unittest.skipUnless(os.environ.get('ASSESSMENT_REHEARSAL_TEST_CLUSTER'), 'Requires owned offline PostgreSQL')
class WandererDeliveryPostgresTest(unittest.TestCase):
    setUpClass = classmethod(fixture.WandererWorldPostgresTest.setUpClass.__func__)
    tearDownClass = classmethod(fixture.WandererWorldPostgresTest.tearDownClass.__func__)
    connect = fixture.WandererWorldPostgresTest.connect
    race = fixture.WandererWorldPostgresTest.race
    native_operation = authority_fixture.WandererLegacyDeliveryTest.native_operation

    def setUp(self):
        fixture.WandererWorldPostgresTest.setUp(self)
        from classroom_app.db import schema_social
        from classroom_app.db.schema_wanderer_delivery import ensure_wanderer_delivery_schema
        from classroom_app.db.schema_wanderer_reflection import ensure_wanderer_reflection_schema
        self.enterContext(patch.object(schema_social, '_SCHEMA_READY', False))
        with self.connect() as conn:
            schema_social.ensure_social_schema(conn, engine='postgres')
            ensure_wanderer_delivery_schema(conn, engine='postgres', allow_schema_changes=True)
            ensure_wanderer_reflection_schema(conn, engine='postgres', allow_schema_changes=True)
            conn.execute('CREATE TABLE class_offerings(id BIGINT PRIMARY KEY)')
            # Reuse the four original table definitions; the fixture only replaces SQLite's PK syntax.
            schema_text = (Path(__file__).parents[1] / 'classroom_app/db/schema_classroom_activity.py').read_text('utf-8')
            for table in ('private_messages', 'private_message_blocks', 'private_message_audit_logs', 'private_message_attachments'):
                sql = re.search(r'CREATE TABLE IF NOT EXISTS ' + table + r'\s*\(.*?\n\s*\)', schema_text, re.S).group(0)
                conn.execute(sql.replace('INTEGER PRIMARY KEY AUTOINCREMENT', 'BIGSERIAL PRIMARY KEY'))
            conn.execute("INSERT INTO social_friendships(identity_a,identity_b,status,requester_identity,created_at) VALUES('student:1','student:2','accepted','student:1',?)", (local_iso(),))
            conn.execute("INSERT INTO social_groups(id,name,owner_identity,created_at,updated_at) VALUES(1,'Synthetic','student:1',?,?)", (local_iso(), local_iso()))
            for sid in (1, 2):
                conn.execute("INSERT INTO social_group_members(group_id,member_identity,role,joined_at) VALUES(1,?,'member','2000')", (f'student:{sid}',))
            self.a = load_wanderer(conn, 1)
            conn.commit()
        self.user = build_wanderer_user_dict(self.a)
        self.peer = 'student:2'
        self.native_lock, self.native_source, self.native_fingerprint = Lock(), None, None
        # Identity-card rendering and remote notifications are not part of these SQL races.
        self.enterContext(patch.object(social, 'serialize_messages', side_effect=lambda conn, sender, rows: [dict(row) for row in rows]))
        self.enterContext(patch.object(social, '_wake_wanderers'))
        self.enterContext(patch('classroom_app.services.social_notification_service.notify_message'))
        self.enterContext(patch('classroom_app.services.wanderer_social_budget_service._award_interaction', return_value=0))
        self.enterContext(patch.object(legacy, '_resolve_contact', return_value={'identity': 'student:2', 'role': 'student',
            'user_pk': 2, 'display_name': 'Synthetic 2', 'can_send': True, 'is_blocked': False, 'class_offering_id': None}))
        self.enterContext(patch.object(legacy, '_load_latest_private_ai_reply_job_row', return_value=None))
        self.enterContext(patch.object(legacy, '_create_private_message_notification'))
        self.enterContext(patch('classroom_app.services.wanderer_inbox_service.enqueue_legacy'))

    def send(self, channel='social', *, rollback=False, body='Native synthetic message'):
        operation_id = str(uuid.uuid4())
        with self.native_operation(operation_id), self.connect() as conn:
            conn.execute("SET LOCAL statement_timeout='10s'")
            if channel == 'legacy':
                result = legacy.create_private_message(conn, self.user, contact_identity=self.peer, content=body)
                message_id = result['message']['id']
            else:
                key = 'group:1' if channel == 'group' else dm_key('student:1', 'student:2')
                result = social.send_message(conn, self.user, key, {'body': body, 'client_nonce': operation_id})
                message_id = result['id']
            if rollback:
                conn.rollback()
            else:
                conn.commit()
            return message_id

    def assert_single_delivery(self, channel):
        results = self.race(4, lambda _: self.send(channel))
        self.assertEqual(len(set(results)), 1)
        with self.connect() as conn:
            table = 'private_messages' if channel == 'legacy' else 'social_messages'
            self.assertEqual(conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wanderer_message_deliveries WHERE delivery_kind='sent'").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wanderer_message_deliveries WHERE delivery_kind='reused'").fetchone()[0], 3)
            self.assertEqual(snapshot(conn, 1, 'social_message')['used'], 3000000)

    def test_native_dm_different_operations_race_once(self):
        self.assert_single_delivery('social')

    def test_native_group_different_operations_race_once(self):
        self.assert_single_delivery('group')

    def test_legacy_different_operations_race_once(self):
        self.assert_single_delivery('legacy')

    def test_rolled_back_native_send_has_no_phantom_success(self):
        rolled_back = self.send(rollback=True)
        committed = self.send()
        self.assertNotEqual(rolled_back, committed)
        with self.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM social_messages').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_message_deliveries').fetchone()[0], 1)
            self.assertEqual(snapshot(conn, 1, 'social_message')['used'], 3000000)

    def test_administrator_conversation_search_and_message_cursor_sql(self):
        from classroom_app.services import wanderer_admin_service as admin
        self.send('social')
        self.send('group')
        self.send('legacy')
        with self.connect() as conn:
            conn.execute('CREATE TABLE teachers(id BIGINT PRIMARY KEY,name TEXT)')
            conn.commit()
            with patch.object(admin, '_identity_names', return_value={'student:1':{'name':'Synthetic 1'}, 'student:2':{'name':'Synthetic 2'}}):
                page = admin.social_conversations(conn, 1, q='Synthetic 2')
                self.assertEqual({item['type'] for item in page['items']}, {'dm', 'legacy'})
                all_rows = admin.social_conversations(conn, 1)
                self.assertEqual(len(all_rows['items']), 3)
                for item in all_rows['items']:
                    messages = admin.social_messages(conn, 1, item['key'], limit=1)
                    self.assertEqual(len(messages['items']), 1)
                    self.assertEqual(messages['viewer_identity'], 'student:1')
                    self.assertFalse(messages['has_more'])
                    empty = admin.social_messages(conn, 1, item['key'], before_id=messages['items'][0]['id'])
                    self.assertEqual(empty['items'], [])

    def assert_correction_cas(self, kind):
        with self.connect() as conn:
            if kind == 'episode':
                from classroom_app.services.wanderer_episodic_memory_service import record_memory
                ident = record_memory(conn, self.a, kind='reflection', title='Before', content='Original private recollection',
                                      source_kind='reflection', source_ref='native-cas', mirror=False)
                table = 'wanderer_episodic_memories'
            else:
                from classroom_app.services.wanderer_knowledge_service import add_knowledge
                add_knowledge(conn, self.a, source_kind='world', source_ref='native-cas', title='Before', digest='Original knowledge', rng=lambda: 1)
                table = 'wanderer_knowledge_entries'
                ident = conn.execute("SELECT id FROM wanderer_knowledge_entries WHERE source_ref='native-cas'").fetchone()[0]
            original = dict(conn.execute(f'SELECT * FROM {table} WHERE id=?', (ident,)).fetchone())
            before_score = conn.execute('SELECT cultivation_score FROM wanderer_agents WHERE id=1').fetchone()[0]
            conn.commit()
        def correct(index):
            payload = {'target_kind': kind, 'target_id': ident, 'expected_revision': 1,
                'expected_content_hash': corrections.content_hash(original, kind), 'title': 'After',
                'content': f'Corrected understanding {index}', 'reason': 'Native independent connection CAS test',
                'request_id': str(uuid.uuid4())}
            with self.connect() as conn:
                try:
                    corrections.correct(conn, 1, payload, actor={'id': 1})
                    conn.commit()
                    return 'saved'
                except HTTPException as exc:
                    conn.rollback()
                    self.assertEqual(exc.status_code, 409)
                    return 'conflict'
        self.assertCountEqual(self.race(2, correct), ['saved', 'conflict'])
        with self.connect() as conn:
            rows = conn.execute('SELECT before_json FROM wanderer_memory_corrections').fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(json.loads(rows[0][0])['record'], original)
            self.assertEqual(conn.execute('SELECT cultivation_score FROM wanderer_agents WHERE id=1').fetchone()[0], before_score)

    def test_administrator_episode_cas_keeps_one_complete_before_snapshot(self):
        self.assert_correction_cas('episode')

    def test_administrator_knowledge_cas_keeps_one_complete_before_snapshot(self):
        self.assert_correction_cas('knowledge')
