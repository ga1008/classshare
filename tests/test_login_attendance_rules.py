"""Golden attendance examples, no application database or network."""
import unittest
from classroom_app.services import login_attendance_rules as r


class AttendanceRulesTests(unittest.TestCase):
    def evaluate(self, points, count=None, **kwargs):
        return r.evaluate_roster([{"student_id":i,"expected":1} for i in range(1,(count or len(points))+1)],
            [{"student_id":i,"observed_at_ms":t,"ip_address":ip} for i,t,ip in points],
            start_ms=0,end_ms=8_000_000,networks=["10.1.0.0/24","10.2.0.0/24"],**kwargs)

    def test_one_login_no_duration_or_operation_is_enough(self):
        result=self.evaluate([(i,1_000_000,"10.1.0.7") for i in range(1,6)])
        self.assertTrue(all(x["suggestion"]=="PRESENT" for x in result["results"].values()))

    def test_exact_twenty_minute_boundary(self):
        points=[(i,2_000_000,"10.1.0.7") for i in range(1,7)]
        result=self.evaluate(points+[(7,3_199_999,"10.1.0.7"),(8,3_200_000,"10.1.0.7")])
        self.assertEqual(result["results"][7]["suggestion"],"PRESENT")
        self.assertEqual(result["results"][8]["suggestion"],"PENDING")

    def test_two_half_cohorts_do_not_manufacture_midpoint(self):
        result=self.evaluate([(i,1_000_000 if i<=30 else 3_340_000,"10.1.0.7") for i in range(1,61)])
        self.assertNotEqual(result["baseline"]["state"],"ready")
        self.assertTrue(all(x["suggestion"]=="PENDING" for x in result["results"].values()))

    def test_multiple_equivalent_networks_share_denominator(self):
        result=self.evaluate([(i,1_000_000,"10.1.0.7" if i<=30 else "10.2.0.7") for i in range(1,61)])
        self.assertEqual(result["baseline"]["main_count"],60)
        self.assertTrue(all(x["suggestion"]=="PRESENT" for x in result["results"].values()))

    def test_minimum_majority_is_not_disqualified_by_leave_one_out(self):
        result=self.evaluate([(i,1_000_000,"10.1.0.7") for i in range(1,102)],count=200)
        self.assertEqual(sum(x["suggestion"]=="PRESENT" for x in result["results"].values()),101)

    def test_many_retries_cannot_make_a_majority(self):
        result=self.evaluate([(1,1_000_000,"10.1.0.7")]*10000,count=60)
        self.assertEqual(result["baseline"]["valid_count"],1)
        self.assertEqual(result["results"][1]["suggestion"],"PENDING")

    def test_early_authenticated_request_is_not_replaced_by_late_login(self):
        result=self.evaluate([(i,1_000_000,"10.1.0.7") for i in range(1,6)]+[(1,4_000_000,"10.1.0.7")],count=5)
        self.assertEqual(result["results"][1]["first_seen_ms"],1_000_000)
        self.assertEqual(result["results"][1]["suggestion"],"PRESENT")

    def test_later_class_activity_can_supply_positive_evidence(self):
        result=self.evaluate([(i,4_000_000,"10.1.0.7") for i in range(1,7)]+[(7,1_000_000,"10.1.0.7"),(7,4_000_000,"10.1.0.7")],count=7)
        self.assertEqual(result["results"][7]["suggestion"],"PRESENT")
        self.assertEqual(result["results"][7]["first_seen_ms"],1_000_000)
        self.assertEqual(result["results"][7]["reason_code"],"later_access_cohort_matched")

    def test_foreign_network_and_missing_are_distinct(self):
        result=self.evaluate([(1,1_000_000,"10.3.0.7")],count=5)
        self.assertEqual(result["results"][1]["reason_code"],"network_unconfirmed")
        self.assertEqual(result["results"][2]["reason_code"],"no_access_evidence")

    def test_small_class_requires_explicit_teacher_reference(self):
        result=self.evaluate([(1,1_000_000,"10.1.0.7")],count=3,teacher_reference_ms=1_000_000)
        self.assertEqual(result["results"][1]["suggestion"],"PRESENT")
        self.assertEqual(result["results"][2]["suggestion"],"PENDING")

    def test_school_pairs_and_teacher_late_start_do_not_extend_end(self):
        for sections,start,end in [("2,3","08:30","10:00"),("4,5","10:20","11:50"),("10,11","19:30","21:00")]:
            period=r.resolve_session_window({"session_date":"2026-11-09","academic_section_text":sections})
            self.assertEqual(period["starts_at_ms"],r.to_ms("2026-11-09T"+start))
            self.assertEqual(period["ends_at_ms"],r.to_ms("2026-11-09T"+end))

    def test_no_guessed_class_time_or_world_network(self):
        with self.assertRaises(ValueError):r.resolve_session_window({"session_date":"2026-11-09"})
        with self.assertRaises(ValueError):r.normalize_networks(["0.0.0.0/0"])
        self.assertEqual(r.normalize_networks(["10.1.0.5"]),["10.1.0.5/32"])


if __name__=="__main__":unittest.main()
