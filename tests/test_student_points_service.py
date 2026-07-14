"""学分币账本与兑换商店的单元测试（sqlite）。"""

import os
import unittest
from datetime import date

os.environ.setdefault("DB_ENGINE", "sqlite")

from classroom_app.database import get_db_connection, init_database
from classroom_app.services.student_points_service import (
    award_points_once,
    ensure_points_schema,
    get_points_balance,
    list_point_ledger,
    redeem_shop_item,
)
from classroom_app.services.student_streak_service import (
    ensure_streak_schema,
    record_student_activity,
    repair_missed_day,
)

STUDENT_ID = 9601


def _cleanup(conn):
    for table in ("student_point_ledger", "student_activity_streaks", "student_activity_days"):
        try:
            conn.execute(f"DELETE FROM {table} WHERE student_id = ?", (STUDENT_ID,))
        except Exception:
            pass


class StudentPointsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_database()

    def setUp(self):
        with get_db_connection() as conn:
            ensure_points_schema(conn)
            ensure_streak_schema(conn)
            _cleanup(conn)
            conn.commit()

    def tearDown(self):
        with get_db_connection() as conn:
            _cleanup(conn)
            conn.commit()

    def test_award_idempotent_and_balance(self):
        with get_db_connection() as conn:
            self.assertTrue(award_points_once(conn, STUDENT_ID, kind="daily_active", ref="2026-07-14"))
            # 同 (kind, ref) 只记一次。
            self.assertFalse(award_points_once(conn, STUDENT_ID, kind="daily_active", ref="2026-07-14"))
            self.assertTrue(award_points_once(conn, STUDENT_ID, kind="badge_unlock", ref="streak_3"))
            self.assertEqual(get_points_balance(conn, STUDENT_ID), 60)
            ledger = list_point_ledger(conn, STUDENT_ID)
            self.assertEqual(len(ledger), 2)
            conn.commit()

    def test_redeem_refused_when_insufficient_or_no_gap(self):
        with get_db_connection() as conn:
            # 余额不足。
            result = redeem_shop_item(conn, STUDENT_ID, "streak_repair_card")
            self.assertFalse(result["ok"])
            self.assertIn("不足", result["message"])

            # 余额够但没有可补缺口 → 效果失败，不扣费。
            award_points_once(conn, STUDENT_ID, kind="badge_unlock", ref="b1", amount=200)
            result2 = redeem_shop_item(conn, STUDENT_ID, "streak_repair_card")
            self.assertFalse(result2["ok"])
            self.assertEqual(get_points_balance(conn, STUDENT_ID), 200)

            # 不存在的商品。
            result3 = redeem_shop_item(conn, STUDENT_ID, "no-such-item")
            self.assertFalse(result3["ok"])
            conn.commit()

    def test_streak_repair_semantics(self):
        today = date(2026, 7, 14)
        with get_db_connection() as conn:
            # 活跃 11、12 日，缺 13 日，14 日回来 → current 断为 1。
            record_student_activity(conn, STUDENT_ID, active_date=date(2026, 7, 11))
            record_student_activity(conn, STUDENT_ID, active_date=date(2026, 7, 12))
            record_student_activity(conn, STUDENT_ID, active_date=today)
            broken = conn.execute(
                "SELECT current_streak FROM student_activity_streaks WHERE student_id = ?",
                (STUDENT_ID,),
            ).fetchone()
            self.assertEqual(int(broken["current_streak"]), 1)

            repaired = repair_missed_day(conn, STUDENT_ID, today=today)
            self.assertTrue(repaired["repaired"])
            self.assertEqual(repaired["repaired_date"], "2026-07-13")
            self.assertEqual(repaired["current_streak"], 4)

            # 缺口已缝合，再修一次应拒绝（保证补签卡不会被重复消费同一缺口）。
            again = repair_missed_day(conn, STUDENT_ID, today=today)
            self.assertFalse(again["repaired"])
            conn.commit()

    def test_repair_keeps_streak_row_consistent(self):
        today = date(2026, 7, 14)
        with get_db_connection() as conn:
            record_student_activity(conn, STUDENT_ID, active_date=date(2026, 7, 11))
            record_student_activity(conn, STUDENT_ID, active_date=date(2026, 7, 12))
            record_student_activity(conn, STUDENT_ID, active_date=today)
            repair_missed_day(conn, STUDENT_ID, today=today)
            row = conn.execute(
                "SELECT current_streak, longest_streak, last_active_date FROM student_activity_streaks WHERE student_id = ?",
                (STUDENT_ID,),
            ).fetchone()
            conn.commit()
        self.assertEqual(int(row["current_streak"]), 4)
        self.assertEqual(int(row["longest_streak"]), 4)
        self.assertEqual(str(row["last_active_date"]), today.isoformat())


class PointsShopPageSmokeTests(unittest.TestCase):
    """整页/接口冒烟：页面渲染、余额显示、无缺口兑换拒绝且不扣费。"""

    @classmethod
    def setUpClass(cls):
        init_database()

    def setUp(self):
        from classroom_app.app import app
        from classroom_app.dependencies import get_current_user

        with get_db_connection() as conn:
            ensure_points_schema(conn)
            ensure_streak_schema(conn)
            _cleanup(conn)
            award_points_once(conn, STUDENT_ID, kind="badge_unlock", ref="seed", amount=150)
            conn.commit()
        self._app = app
        self._dep = get_current_user
        app.dependency_overrides[get_current_user] = lambda: {
            "id": STUDENT_ID,
            "role": "student",
            "name": "Eve",
        }

    def tearDown(self):
        self._app.dependency_overrides.pop(self._dep, None)
        with get_db_connection() as conn:
            _cleanup(conn)
            conn.commit()

    def test_page_renders_and_redeem_refused_without_gap(self):
        from fastapi.testclient import TestClient

        client = TestClient(self._app)
        resp = client.get("/points")
        self.assertEqual(resp.status_code, 200)
        for marker in ("学习攒币", "补签卡", "🪙 150", "app-bottomnav", "最近流水"):
            self.assertIn(marker, resp.text)

        redeem = client.post("/api/points/redeem", json={"item_key": "streak_repair_card"})
        self.assertEqual(redeem.status_code, 400)
        with get_db_connection() as conn:
            self.assertEqual(get_points_balance(conn, STUDENT_ID), 150)


if __name__ == "__main__":
    unittest.main()
