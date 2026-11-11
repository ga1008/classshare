"""Operational counters distinguish real alternating turns from message volume."""
import unittest
from tools.wanderer_communication_snapshot import summarize_rows, bounded_summary


class CommunicationSnapshotTest(unittest.TestCase):
    def test_same_speaker_segments_do_not_prove_multiple_rounds(self):
        rows=[(i,'c','a' if i<9 else 'b',i,True,str(i)) for i in range(1,13)]
        value=summarize_rows(rows,since_epoch=0)
        self.assertEqual(value['ai_messages'],12)
        self.assertEqual(value['conversations_with_4_alternating_speaker_turns'],0)
        self.assertEqual(value['conversation_examples'][0]['speaker_turns'],2)

    def test_repeat_requires_no_new_incoming_and_inclusive_ten_minute_window(self):
        rows=[(1,'c','a',0,True,'same'),(2,'c','a',600,True,'same'),
              (3,'c','a',1201,True,'same'),(4,'c','b',1202,False,'response'),
              (5,'c','a',1203,True,'same')]
        value=summarize_rows(rows,since_epoch=0)
        self.assertEqual(value['plain_text_repeat_candidates'],1)
        self.assertEqual(value['repeat_examples'][0]['message_id'],2)

    def test_lookback_can_prove_repeat_but_does_not_count_old_turn(self):
        rows=[(1,'c','a',99,True,'same'),(2,'c','a',100,True,'same'),(3,'c','b',101,True,'reply')]
        value=summarize_rows(rows,since_epoch=100)
        self.assertEqual(value['plain_text_repeat_candidates'],1)
        self.assertEqual(value['ai_messages'],2)
        self.assertEqual(value['conversation_examples'][0]['speaker_turns'],2)

    def test_system_event_changes_delivery_watermark_without_becoming_spoken_turn(self):
        rows=[(1,'c','a',1,True,'same',True),(2,'c','system',2,False,None,False),
              (3,'c','a',3,True,'same',True)]
        value=summarize_rows(rows,since_epoch=0)
        self.assertEqual(value['plain_text_repeat_candidates'],0)
        self.assertEqual(value['messages'],2)
        self.assertEqual(value['conversation_examples'][0]['speaker_turns'],1)

    def test_truncation_is_explicit_and_unseen_rows_are_never_counted(self):
        rows=[(i,'c','a' if i%2 else 'b',i,True,str(i)) for i in range(1,8)]
        value=bounded_summary(rows,since_epoch=0,max_rows=4)
        self.assertTrue(value['truncated'])
        self.assertEqual(value['scanned_rows'],4)
        self.assertEqual(value['conversations_with_4_alternating_speaker_turns'],1)
        self.assertEqual(value['conversations_with_6_alternating_speaker_turns'],0)

    def test_attachment_or_empty_payload_without_hash_is_not_called_duplicate(self):
        value=summarize_rows([(1,'c','a',1,True,None),(2,'c','a',2,True,None)],since_epoch=0)
        self.assertEqual(value['plain_text_repeat_candidates'],0)
