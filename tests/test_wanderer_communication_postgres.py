"""Snapshot SQL and recovery admission on our owned native PostgreSQL cluster."""
import os
from datetime import datetime, timedelta
import unittest
from zoneinfo import ZoneInfo

from classroom_app.services import wanderer_runtime_service as runtime
from classroom_app.time_utils import local_iso
from tools.wanderer_communication_snapshot import collect_snapshot
from tools.assessment_postgres_rehearsal import connect_offline
from tests import test_wanderer_delivery_postgres as fixture


@unittest.skipUnless(os.environ.get('ASSESSMENT_REHEARSAL_TEST_CLUSTER'),'Requires owned offline PostgreSQL')
class CommunicationPostgresTest(unittest.TestCase):
    setUpClass=classmethod(fixture.WandererDeliveryPostgresTest.setUpClass.__func__)
    tearDownClass=classmethod(fixture.WandererDeliveryPostgresTest.tearDownClass.__func__)
    setUp=fixture.WandererDeliveryPostgresTest.setUp
    connect=fixture.WandererDeliveryPostgresTest.connect
    race=fixture.WandererDeliveryPostgresTest.race
    native_operation=fixture.WandererDeliveryPostgresTest.native_operation

    def snapshot(self):
        with connect_offline(cluster_dir=self.cluster,port=self.port,database=self.name) as conn:
            conn.execute('SET TRANSACTION READ ONLY')
            return collect_snapshot(conn,since=datetime.now(ZoneInfo('Asia/Shanghai'))-timedelta(hours=1))

    def seed_repeated(self,channel):
        from classroom_app.services.social_common import now_iso
        key='group:1' if channel=='social' else 'student:1|student:2|scope:0'
        with self.connect() as conn:
            ids=[]
            for number in range(2):
                if channel=='social':
                    result=conn.execute("INSERT INTO social_messages(conversation_key,conversation_type,sender_identity,message_type,body,body_format,created_at,client_nonce) VALUES(?,'group','student:1','text','Synthetic repeat','md',?,?) RETURNING id",(key,now_iso(),f'synthetic-{number}'))
                else:
                    result=conn.execute("INSERT INTO private_messages(conversation_key,sender_identity,sender_role,sender_user_pk,sender_display_name,recipient_identity,recipient_role,recipient_user_pk,recipient_display_name,content,created_at) VALUES(?,'student:1','student',1,'S1','student:2','student',2,'S2','Synthetic repeat',?) RETURNING id",(key,local_iso()))
                mid=int(result.fetchone()[0]);ids.append(mid)
                conn.execute("INSERT INTO wanderer_message_deliveries VALUES(?,1,1,'turn','same-chain',?,?,'same-payload',0,?,?,'sent',?)",
                    (f'{channel}-{number}',channel,key,mid,f'{channel}-{number}',local_iso()))
            conn.commit()
        return ids

    def test_snapshot_excludes_recalled_deleted_and_pre_rejoin_delivery(self):
        older,_newer=self.seed_repeated('social')
        self.assertEqual(self.snapshot()['duplicate_sent_ledger_candidates'],1)
        with self.connect() as conn:
            conn.execute('UPDATE social_messages SET recalled_at=? WHERE id=?',(local_iso(),older));conn.commit()
        self.assertEqual(self.snapshot()['duplicate_sent_ledger_candidates'],0)
        with self.connect() as conn:
            conn.execute('UPDATE social_messages SET recalled_at=NULL WHERE id=?',(older,))
            conn.execute("UPDATE social_group_members SET joined_at='2099' WHERE group_id=1 AND member_identity='student:1'")
            conn.commit()
        self.assertEqual(self.snapshot()['duplicate_sent_ledger_candidates'],0)
        legacy_old,_new=self.seed_repeated('legacy')
        self.assertEqual(self.snapshot()['duplicate_sent_ledger_candidates'],1)
        with self.connect() as conn:
            conn.execute('DELETE FROM private_messages WHERE id=?',(legacy_old,));conn.commit()
        self.assertEqual(self.snapshot()['duplicate_sent_ledger_candidates'],0)

    def test_failed_model_completion_race_schedules_only_one_new_event(self):
        with self.connect() as conn:
            event=runtime.enqueue_wake_event(conn,1,'inbox','synthetic-admission',{'scope':{'kind':'public'}})
            run=runtime._claim(conn,event['event_id'])
            conn.execute('UPDATE wanderer_turns SET dispatched_at=? WHERE turn_id=?',(local_iso(),run['turn_id']))
            conn.commit()
        results=self.race(4,lambda _:runtime._model_failure(self.connect,run,
            {'status':'failed','ai_calls':1,'error':'synthetic timeout'}))
        self.assertEqual(sum(bool(r.get('callback_event_id')) for r in results),1)
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wanderer_wake_events WHERE source='model_retry'").fetchone()[0],1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM wanderer_turns').fetchone()[0],1)
