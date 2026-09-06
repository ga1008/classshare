"""Real public JD text and synthetic profiles: unparsed eligibility is not a pass."""
import json
import unittest
from datetime import date
from pathlib import Path

from classroom_app.services.resume.resume_requirement_service import evaluate_hard_requirements

TODAY=date(2026,9,6)
ROOT=Path(__file__).resolve().parents[1]


def checks(text,bundle=None):
    return evaluate_hard_requirements(bundle or {},text,today=TODAY)


def confirmed(items):
    required=[row for row in items if row["importance"]=="required"]
    return bool(required) and all(row["state"]=="met" for row in required)


class RequirementCoverageGuardTests(unittest.TestCase):
    def test_three_public_boc_jds_reject_false_confirmed_with_2020_graduate_and_300_scores(self):
        postings=json.loads((ROOT/'docs/career-job-source-candidates-review-2026-09-06.json').read_text(encoding='utf-8'))['postings']
        for posting,major in zip(postings,['计算机','法学','法学']):
            with self.subTest(posting=posting['title']):
                bundle={'education':[{'id':1,'degree':'本科','major':major,'end_date':'2020-06'}],
                        'certificate':[{'id':1,'name':'CET-4','description':'成绩300分','acquired_date':'2020-01'},
                                       {'id':2,'name':'CET-6','description':'成绩300分','acquired_date':'2020-01'}],
                        'experience':[{'id':1,'kind':'work','start_date':'2020-07','end_date':'2025-12'}]}
                result=checks(posting['job_description'],bundle)
                self.assertFalse(confirmed(result))
                guards={row.get('condition_key'):row['state'] for row in result if row.get('extraction_complete') is False}
                self.assertEqual(guards['graduation_window'],'unknown')
                self.assertEqual(guards['first_employment'],'unknown')
                self.assertEqual(guards['overseas_credential'],'unknown')
                self.assertEqual(guards['certificate_alternatives'],'unknown')
                self.assertTrue(any(row['type']=='education' and row['state']=='met' for row in result))

    def test_single_explicit_cohort_known_mismatch_but_ranges_or_missing_education_stay_unknown(self):
        profile={'education':[{'id':1,'degree':'本科','end_date':'2020-06'}]}
        result=checks('仅招聘2027届毕业生',profile)
        self.assertEqual(result[0]['state'],'failed')
        for text,education in [('2026-2027届毕业生',[{'end_date':'2026-06'}]),
                               ('2026届或2027届毕业生',[{'end_date':'2020-06'}]),
                               ('2027届毕业生',[{'end_date':'2020-06'}, {'end_date':''}]),
                               ('2027届毕业生',[{'end_date':'2020-99'}])]:
            with self.subTest(text=text,education=education):
                rows=checks(text,{'education':education})
                self.assertTrue(any(row['state']=='unknown' for row in rows))
                self.assertFalse(any(row['state']=='failed' for row in rows))

    def test_first_employment_and_overseas_status_are_not_inferred_from_missing_records(self):
        rows=checks('应届毕业生；毕业后首次就业；归国留学生须提供留服认证')
        self.assertEqual({row.get('condition_key') for row in rows},{'first_employment','overseas_credential'})
        self.assertTrue(all(row['state']=='unknown' for row in rows))

    def test_certificate_names_do_not_satisfy_score_thresholds(self):
        for text,name in [('CET-4 425分以上','CET-4'),('IELTS 6.5','IELTS'),('雅思6.5','雅思'),
                          ('TOEFL iBT 85分','TOEFL iBT'),('TOEIC 715','TOEIC'),
                          ('英语四级成绩不低于425','英语四级'),('雅思总分至少6.5','雅思')]:
            with self.subTest(text=text):
                rows=checks(text,{'certificate':[{'name':name,'description':'未填写分数','acquired_date':'2025-01'}]})
                self.assertFalse(confirmed(rows))
                self.assertEqual(rows[0].get('condition_key'),'certificate_score')
                self.assertEqual(rows[0]['state'],'unknown')

    def test_expired_certificate_option_never_fails_whole_or_group(self):
        profile={'certificate':[{'name':'CET-4','expiry_date':'2020-01','acquired_date':'2018-01'},
                                {'name':'IELTS','acquired_date':'2026-01','expiry_date':'2028-01'}]}
        for text in ['须有CET-4或IELTS证书','须有CET-4；或IELTS证书','CET-4 425分或IELTS 6.5分']:
            with self.subTest(text=text):
                rows=checks(text,profile)
                self.assertFalse(any(row['state']=='failed' for row in rows))
                self.assertEqual(rows[0]['condition_key'],'certificate_alternatives')
                self.assertEqual(rows[0]['state'],'unknown')

    def test_simple_certificate_validity_and_education_checks_still_work(self):
        bundle={'education':[{'degree':'本科','end_date':'2025-06'}],
                'certificate':[{'name':'高中教师资格证','acquired_date':'2024-01','expiry_date':'2028-01'}]}
        self.assertTrue(confirmed(checks('本科以上学历；高中教师资格证',bundle)))
        bundle['certificate'][0]['acquired_date']='2027-01'
        self.assertEqual(checks('高中教师资格证',bundle)[0]['state'],'unknown')
        bundle['certificate'][0].update(acquired_date='2020-01',expiry_date='2025-01')
        self.assertEqual(checks('高中教师资格证',bundle)[0]['state'],'failed')

    def test_clause_bound_has_visible_incomplete_guard_and_calendar_year_is_not_work_duration(self):
        result=checks('本科以上学历。'*45,{'education':[{'degree':'本科','end_date':'2020-01'}]})
        self.assertEqual(len(result),40)
        self.assertEqual(result[0]['condition_key'],'extraction_limit')
        self.assertFalse(confirmed(result))
        result=checks('2027年7月31日毕业后可开始全职工作')
        self.assertFalse(any(row['type']=='experience' for row in result))


if __name__=='__main__':unittest.main()
