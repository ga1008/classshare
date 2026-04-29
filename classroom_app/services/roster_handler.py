import sys
from typing import List, Any

import pandas as pd
from pathlib import Path
import traceback

from ..database import get_db_connection


def parse_excel_to_students(excel_path: Path) -> list[Any] | None:
    """
    解析 Excel 文件，返回学生字典列表。
    不直接写入数据库。
    """
    try:
        file_suffix = excel_path.suffix.lower()
        if file_suffix in ['.xlsx', '.xls']:
            df = pd.read_excel(excel_path, header=0)
        elif file_suffix == '.csv':
            df = pd.read_csv(excel_path, encoding='utf-8')
        else:
            raise ValueError(f"不支持的文件类型: {file_suffix}")

        df.columns = df.columns.str.strip()

        # 查找必需的列
        column_map = {
            "学号": None,
            "姓名": None,
            "性别": "gender",
            "邮箱": "email",
            "电子邮箱": "email",
            "手机号": "phone",
            "手机号码": "phone",
        }

        found_student_id = False
        found_name = False

        parsed_cols = {}

        for col in df.columns:
            if "学号" in col:
                parsed_cols["student_id_number"] = col
                found_student_id = True
            elif "姓名" in col:
                parsed_cols["name"] = col
                found_name = True
            else:
                for key, val in column_map.items():
                    if key in col:
                        parsed_cols[val] = col
                        break

        if not found_student_id or not found_name:
            raise ValueError("Excel文件必须包含 '学号' 和 '姓名' 两列。")

        # 转换为字典列表
        students = []
        for _, row in df.iterrows():
            student_data = {
                "student_id_number": str(row[parsed_cols["student_id_number"]]).strip(),
                "name": str(row[parsed_cols["name"]]).strip(),
                "gender": str(row.get(parsed_cols.get("gender"), "")).strip(),
                "email": str(row.get(parsed_cols.get("email"), "")).strip(),
                "phone": str(row.get(parsed_cols.get("phone"), "")).strip(),
            }
            if student_data["student_id_number"] and student_data["name"]:
                students.append(student_data)

        return students

    except Exception as e:
        print(f"[ERROR] 解析学生名单失败: {e}\n{traceback.format_exc()}", file=sys.stderr)
        return None
