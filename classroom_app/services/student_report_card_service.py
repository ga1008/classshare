"""学生个人成绩单。

按学生聚合所有已批改提交，给出：
- 逐课程的成绩时间线（我的分数 vs 班级平均分）；
- 每次任务的**匿名**班级分位区间（只告诉学生"你在前 25%"，绝不暴露他人个体分数）；
- 课程小结（均分、最近趋势）与全局摘要。

只读 submissions/assignments/courses，不新增任何写入。
（区别于 ``ordinary/exam_grade_record_service``：那是教师端成绩登记表 docx 导出。）
"""

from __future__ import annotations

from typing import Any

# 分位样本下限：班上批改人数少于该值时不给分位结论，避免"3 个人里的第 1 名"这种噪声。
MIN_BAND_SAMPLE = 5

PERCENTILE_BANDS: tuple[tuple[float, str, str], ...] = (
    (0.10, "前 10%", "top"),
    (0.25, "前 25%", "high"),
    (0.50, "前 50%", "mid"),
    (1.01, "后 50%", "low"),
)


def _percentile_band(rank: int, total: int) -> tuple[str, str]:
    ratio = rank / total
    for threshold, label, tone in PERCENTILE_BANDS:
        if ratio <= threshold:
            return label, tone
    return PERCENTILE_BANDS[-1][1], PERCENTILE_BANDS[-1][2]


def _load_my_graded_rows(conn, student_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT s.assignment_id, s.score, s.submitted_at, s.is_late_submission,
               a.title AS assignment_title, a.exam_paper_id, a.course_id,
               a.class_offering_id, c.name AS course_name
        FROM submissions s
        JOIN assignments a ON a.id = s.assignment_id
        JOIN courses c ON c.id = a.course_id
        WHERE s.student_pk_id = ?
          AND s.status = 'graded'
          AND s.score IS NOT NULL
          AND COALESCE(s.is_absence_score, 0) = 0
        ORDER BY s.submitted_at ASC, s.assignment_id ASC
        """,
        (int(student_id),),
    ).fetchall()
    return [dict(row) for row in rows]


def _load_class_scores(conn, assignment_ids: list[Any]) -> dict[Any, list[float]]:
    """每个作业的全班已批改分数（仅用于聚合，绝不外泄个体）。"""
    if not assignment_ids:
        return {}
    placeholders = ",".join("?" for _ in assignment_ids)
    rows = conn.execute(
        f"""
        SELECT assignment_id, score
        FROM submissions
        WHERE assignment_id IN ({placeholders})
          AND status = 'graded'
          AND score IS NOT NULL
          AND COALESCE(is_absence_score, 0) = 0
        """,
        tuple(assignment_ids),
    ).fetchall()
    scores: dict[Any, list[float]] = {}
    for row in rows:
        try:
            scores.setdefault(row["assignment_id"], []).append(float(row["score"]))
        except (TypeError, ValueError):
            continue
    return scores


def _course_trend(records: list[dict[str, Any]]) -> str:
    """最近三次 vs 之前的均分对比，给一句人话结论。"""
    scored = [r["my_score"] for r in records if r["my_score"] is not None]
    if len(scored) < 4:
        return "样本还少，继续积累"
    recent = scored[-3:]
    earlier = scored[:-3]
    diff = sum(recent) / len(recent) - sum(earlier) / len(earlier)
    if diff >= 3:
        return "稳步上升"
    if diff <= -3:
        return "最近有所下滑"
    return "保持平稳"


def build_student_report_card(conn, *, student_id: int) -> dict[str, Any]:
    my_rows = _load_my_graded_rows(conn, student_id)
    class_scores = _load_class_scores(conn, [row["assignment_id"] for row in my_rows])

    course_index: dict[int, dict[str, Any]] = {}
    all_scores: list[float] = []
    band_best: dict[str, int] = {}

    for row in my_rows:
        try:
            my_score = round(float(row["score"]), 1)
        except (TypeError, ValueError):
            continue
        all_scores.append(my_score)

        peers = class_scores.get(row["assignment_id"]) or []
        class_avg = round(sum(peers) / len(peers), 1) if peers else None
        band_label, band_tone = "", ""
        if len(peers) >= MIN_BAND_SAMPLE:
            rank = 1 + sum(1 for value in peers if value > my_score)
            band_label, band_tone = _percentile_band(rank, len(peers))
            band_best[band_tone] = band_best.get(band_tone, 0) + 1

        course_id = int(row["course_id"])
        course = course_index.setdefault(
            course_id,
            {
                "course_id": course_id,
                "course_name": str(row["course_name"] or "课程"),
                "records": [],
            },
        )
        course["records"].append(
            {
                "assignment_id": row["assignment_id"],
                "title": str(row["assignment_title"] or ("考试" if row["exam_paper_id"] else "作业")),
                "is_exam": bool(row["exam_paper_id"]),
                "kind_label": "考试" if row["exam_paper_id"] else "作业",
                "submitted_at": str(row["submitted_at"] or ""),
                "date_label": str(row["submitted_at"] or "")[:10],
                "my_score": my_score,
                "class_avg": class_avg,
                "class_count": len(peers),
                "band_label": band_label,
                "band_tone": band_tone,
                "is_late": bool(row["is_late_submission"]),
                "link_url": f"/assignment/{row['assignment_id']}",
            }
        )

    courses = list(course_index.values())
    for course in courses:
        records = course["records"]
        scored = [r["my_score"] for r in records if r["my_score"] is not None]
        course["record_count"] = len(records)
        course["avg_score"] = round(sum(scored) / len(scored), 1) if scored else None
        course["latest_score"] = scored[-1] if scored else None
        course["trend_label"] = _course_trend(records)
        # 折线图数据：与 records 顺序一致。
        course["chart"] = {
            "labels": [r["date_label"] or f"#{i + 1}" for i, r in enumerate(records)],
            "mine": [r["my_score"] for r in records],
            "class_avg": [r["class_avg"] for r in records],
        }
    courses.sort(key=lambda item: -(item["record_count"]))

    overall_avg = round(sum(all_scores) / len(all_scores), 1) if all_scores else None
    best_course = max(
        (c for c in courses if c["avg_score"] is not None),
        key=lambda c: c["avg_score"],
        default=None,
    )
    weakest_course = min(
        (c for c in courses if c["avg_score"] is not None),
        key=lambda c: c["avg_score"],
        default=None,
    )
    return {
        "courses": courses,
        "summary": {
            "record_total": len(all_scores),
            "course_total": len(courses),
            "overall_avg": overall_avg,
            "top_band_count": band_best.get("top", 0) + band_best.get("high", 0),
            "best_course": best_course["course_name"] if best_course else "",
            "weakest_course": (
                weakest_course["course_name"]
                if weakest_course and weakest_course is not best_course
                else ""
            ),
        },
    }
