"""导出行归一化守护：psycopg dict 行 vs sqlite 元组行都必须给 pandas 值元组。

生产（postgres, dict_row）下 ``pd.DataFrame(cursor, columns=[...])`` 会把 dict
行按中文列名重索引成全 NaN——成绩导出因此产出整表空值。该测试钉住
engine-neutral 的取值行为。
"""

import unittest

from classroom_app.routers.homework_parts.exports import _rows_as_value_tuples


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class ExportRowNormalizationTests(unittest.TestCase):
    def test_dict_rows_yield_value_tuples_in_select_order(self):
        rows = [
            {"id": 114, "student_id_number": "2205301010323", "name": "学生乙", "class_name": "软工2302班"},
            {"id": 28, "student_id_number": "24055060102", "name": "学生甲", "class_name": "软工2406班"},
        ]
        result = _rows_as_value_tuples(_FakeCursor(rows))
        self.assertEqual(result[0], (114, "2205301010323", "学生乙", "软工2302班"))
        self.assertEqual(result[1], (28, "24055060102", "学生甲", "软工2406班"))

    def test_tuple_like_rows_pass_through(self):
        rows = [(1, "a"), (2, "b")]
        result = _rows_as_value_tuples(_FakeCursor(rows))
        self.assertEqual(result, [(1, "a"), (2, "b")])


if __name__ == "__main__":
    unittest.main()
