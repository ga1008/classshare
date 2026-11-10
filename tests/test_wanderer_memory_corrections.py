"""Administrator maintenance preserves provenance, history and reward accounting."""
import uuid
import io
import json
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException
from classroom_app.services import wanderer_memory_correction_service as corrections
from classroom_app.services.wanderer_episodic_memory_service import record_memory
from classroom_app.services.wanderer_knowledge_service import add_knowledge
from tests.wanderer_behavior_support import WandererWorldTestCase


class MemoryCorrectionTest(WandererWorldTestCase):
    def setUp(self):
        super().setUp()
        with self.connect() as conn:
            for name in ('wanderer_memory_corrections', 'wanderer_memory_sources',
                         'wanderer_knowledge_revisions', 'wanderer_knowledge_growth'):
                conn.execute(f'DELETE FROM {name}')
            self.w = self.wanderer(conn, 'atai')
            conn.commit()

    def episode(self, conn):
        ident = record_memory(conn, self.w, kind='reflection', title='昨日理解',
                              content='昨日与朋友聊天。我以为好感度可以直接设置100。',
                              source_kind='reflection', source_ref='test', mirror=False)
        return dict(conn.execute('SELECT * FROM wanderer_episodic_memories WHERE id=?', (ident,)).fetchone())

    def payload(self, row, kind='episode', **overrides):
        return {'target_kind': kind, 'target_id': row['id'], 'expected_revision': 1,
                'expected_content_hash': corrections.content_hash(row, kind),
                'title': row['title'], 'content': '昨日与朋友聊天。好感度现由每日五档评分经系统结算。',
                'reason': '仅修订升级后过时规则，保留个人经历。', 'request_id': str(uuid.uuid4()), **overrides}

    def test_correct_preserves_history_identity_and_private_provenance(self):
        with self.connect() as conn:
            original = self.episode(conn)
            result = corrections.correct(conn, self.w['id'], self.payload(original), actor=self.teacher)
            item = result['item']
            self.assertEqual(item['id'], original['id'])
            for key in ('occurred_at', 'episode_hash', 'kind', 'source_ref', 'importance'):
                self.assertEqual(item[key], original[key])
            source = conn.execute('SELECT * FROM wanderer_memory_sources WHERE memory_id=?', (item['id'],)).fetchone()
            self.assertEqual(source['visibility'], 'private')
            self.assertEqual(source['author_kind'], 'admin_correction')
            history = corrections.history(conn, self.w['id'], 'episode', item['id'])['items']
            self.assertEqual(history[0]['before']['record'], original)
            self.assertEqual(history[0]['after']['revision'], 2)
            self.assertEqual(item['correction_revision'], 2)
            conn.commit()

    def test_replay_and_stale_edit_are_safe(self):
        with self.connect() as conn:
            original = self.episode(conn)
            request = self.payload(original)
            first = corrections.correct(conn, self.w['id'], request, actor=self.teacher)
            repeated = corrections.correct(conn, self.w['id'], request, actor=self.teacher)
            self.assertTrue(repeated['replayed'])
            self.assertEqual(first['item'], repeated['item'])
            self.assertNotIn('request_hash', repeated['correction'])
            for bad in (self.payload(original), {**request, 'content': '另一份内容'}):
                with self.assertRaises(HTTPException) as caught:
                    corrections.correct(conn, self.w['id'], bad, actor=self.teacher)
                self.assertEqual(caught.exception.status_code, 409)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_memory_corrections').fetchone()[0], 1)
            conn.commit()

    def test_wrong_owner_and_missing_expected_hash_rejected(self):
        with self.connect() as conn:
            original = self.episode(conn)
            other = self.wanderer(conn, 'lele')
            with self.assertRaises(LookupError):
                corrections.correct(conn, other['id'], self.payload(original), actor=self.teacher)
            with self.assertRaises(HTTPException):
                corrections.correct(conn, self.w['id'], self.payload(original, expected_content_hash=''), actor=self.teacher)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_memory_corrections').fetchone()[0], 0)

    def test_knowledge_maintenance_does_not_award_cultivation_or_reset_watermark(self):
        with self.connect() as conn:
            add_knowledge(conn, self.w, source_kind='world', source_ref='memory-correction-test',
                          title='规则', digest='旧规则摘要。', rng=lambda: 1)
            row = dict(conn.execute("SELECT * FROM wanderer_knowledge_entries WHERE source_ref='memory-correction-test'").fetchone())
            before = conn.execute('SELECT cultivation_score FROM wanderer_agents WHERE id=?', (self.w['id'],)).fetchone()[0]
            request = self.payload(row, 'knowledge', content='每日自省免费，最多三次实际AI请求，评分由系统结算。')
            result = corrections.correct(conn, self.w['id'], request, actor=self.teacher)
            self.assertEqual(conn.execute('SELECT cultivation_score FROM wanderer_agents WHERE id=?', (self.w['id'],)).fetchone()[0], before)
            self.assertEqual(corrections.history(conn, self.w['id'], 'knowledge', row['id'])['items'][0]['before']['content'], row['digest'])
            add_knowledge(conn, self.w, source_kind='world', source_ref='memory-correction-test', title='规则',
                          digest=request['content'], rng=lambda: 1)
            self.assertEqual(conn.execute('SELECT cultivation_score FROM wanderer_agents WHERE id=?', (self.w['id'],)).fetchone()[0], before)
            self.assertEqual(result['item']['correction_revision'], 2)
            conn.commit()

    def _manifest_preflight(self, request):
        from tools import wanderer_memory_corrections as tool
        with tempfile.TemporaryDirectory(prefix='memory-manifest-test-') as folder:
            manifest = Path(folder) / 'reviewed.json'
            manifest.write_text(json.dumps({'contract':'reviewed-memory-corrections-v1',
                'reviewed_at':'synthetic-test', 'entries':[{'wanderer_id':self.w['id'], 'request':request}]}), 'utf-8')
            with patch('sys.argv', ['memory-corrections', '--manifest', str(manifest)]), redirect_stdout(io.StringIO()) as output:
                tool.main()
            return json.loads(output.getvalue())

    def test_read_only_manifest_replay_checks_the_exact_original_request(self):
        with self.connect() as conn:
            original = self.episode(conn)
            request = self.payload(original)
            corrections.correct(conn, self.w['id'], request, actor={'id':0,'kind':'release_operator'})
            conn.commit()
            saved = dict(conn.execute('SELECT * FROM wanderer_episodic_memories WHERE id=?', (original['id'],)).fetchone())
        self.assertEqual(self._manifest_preflight(request)['status'], 'validated')
        with self.assertRaises(ValueError):
            self._manifest_preflight({**request, 'content':'同一编号不能预检另一份修订'})
        with self.connect() as conn:
            self.assertEqual(dict(conn.execute('SELECT * FROM wanderer_episodic_memories WHERE id=?', (original['id'],)).fetchone()), saved)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_memory_corrections').fetchone()[0], 1)

    def test_read_only_manifest_rejects_malformed_and_superseded_records(self):
        with self.connect() as conn:
            original = self.episode(conn)
            conn.commit()
        for extra in ({'content':'x'*3201}, {'request_id':'invalid'}, {'reason':''}):
            with self.assertRaises(ValueError):
                self._manifest_preflight(self.payload(original, **extra))
        with self.connect() as conn:
            conn.execute("UPDATE wanderer_episodic_memories SET status='superseded' WHERE id=?", (original['id'],))
            conn.commit()
            current = dict(conn.execute('SELECT * FROM wanderer_episodic_memories WHERE id=?', (original['id'],)).fetchone())
        with self.assertRaises(ValueError):
            self._manifest_preflight(self.payload(current))
        with self.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_memory_corrections').fetchone()[0], 0)

    def test_over_capacity_existing_memory_allows_equal_or_shorter_correction(self):
        from classroom_app.services.wanderer_resource_service import capacity, grandfather_capacity
        with self.connect() as conn:
            original = self.episode(conn)
            size = len(original['content'])
            grandfather_capacity(conn, self.w['id'], 'memory_chars', f"memory:{original['id']}", size)
            grandfather_capacity(conn, self.w['id'], 'memory_chars', 'synthetic-retained-history',
                                  capacity(conn, self.w['id'], 'memory_chars')['limit'] + 100)
            initial = capacity(conn, self.w['id'], 'memory_chars')
            self.assertGreater(initial['used'], initial['limit'])
            retained = dict(conn.execute("SELECT * FROM wanderer_capacity_allocations WHERE object_key='synthetic-retained-history'").fetchone())
            equal_text = '旧规则已修正' + '。' * (size - len('旧规则已修正'))
            first = corrections.correct(conn, self.w['id'], self.payload(original, content=equal_text), actor=self.teacher)
            self.assertEqual(capacity(conn, self.w['id'], 'memory_chars')['used'], initial['used'])
            current = dict(conn.execute('SELECT * FROM wanderer_episodic_memories WHERE id=?', (original['id'],)).fetchone())
            shorter = '已校正旧规则。'
            second = corrections.correct(conn, self.w['id'], self.payload(current, content=shorter,
                expected_revision=first['item']['correction_revision']), actor=self.teacher)
            self.assertEqual(second['item']['content'], shorter)
            self.assertEqual(capacity(conn, self.w['id'], 'memory_chars')['used'], initial['used'] - size + len(shorter))
            self.assertEqual(dict(conn.execute("SELECT * FROM wanderer_capacity_allocations WHERE object_key='synthetic-retained-history'").fetchone()), retained)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_episodic_memories WHERE wanderer_id=?', (self.w['id'],)).fetchone()[0], 1)
            conn.commit()
