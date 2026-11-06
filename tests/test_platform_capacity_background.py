"""Acceptance metrics must handle worker/producer event-append races."""
import unittest

from tools.platform_capacity_background import _projection_publication_delays


def event(kind, student, version, second):
    return {"event": kind, "student_id": student, "version": version, "at": f"2026-11-05T00:00:{second:02d}"}


class BackgroundProjectionMetricTests(unittest.TestCase):
    def test_publication_appended_before_committed_dirty_event_is_completed(self):
        # Admission starts at :01. Worker sees the commit and publishes at
        # :02 before the producer appends its dirty event at :03. The producer
        # retains admission time, so append order cannot invent an outstanding mark.
        events = [event("projection_published", 1, 7, 2), event("projection_dirty", 1, 7, 1)]
        self.assertEqual(([1.0], 0), _projection_publication_delays(events))

    def test_later_generation_completion_covers_prior_marks_but_other_users_do_not(self):
        events = [event("projection_dirty", 1, 7, 1), event("projection_dirty", 1, 8, 2),
                  event("projection_dirty", 2, 9, 2), event("projection_published", 1, 8, 4)]
        self.assertEqual(([3.0, 2.0], 1), _projection_publication_delays(events))

    def test_old_or_earlier_publications_cannot_complete_newer_mark(self):
        events = [event("projection_published", 1, 6, 4), event("projection_published", 1, 8, 1),
                  event("projection_dirty", 1, 7, 2)]
        self.assertEqual(([], 1), _projection_publication_delays(events))
