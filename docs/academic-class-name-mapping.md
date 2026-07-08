# 教务教学班名称转换表

## 背景

广西外国语学院教务系统（`school_code = gxufl`）同步过来的 `teaching_class_name` 是教务系统里的教学班代号，常见格式类似 `计算机网络原理-0006`。它适合做同步、去重和回溯，不适合作为平台里直接展示给教师和学生的班级名称。

平台页面、待办、日程、邮件提醒、文档预填等用户可见位置，应显示行政班名称，例如 `软工2401班`、`网工2303班（专升本）`。广外教务系统的数据必须通过 `teacher_academic_teaching_class_mappings` 转换表取得正确显示名。

## 转换表

表名：`teacher_academic_teaching_class_mappings`

核心字段：

| 字段 | 说明 |
| --- | --- |
| `teacher_id`、`school_code`、`academic_year`、`academic_term` | 限定教师、学校和学期范围 |
| `course_code`、`course_name` | 课程维度，用于区分同名教学班或别名 |
| `teaching_class_id`、`teaching_class_name` | 教务系统原始教学班标识和名称 |
| `teaching_class_aliases_json` | 教学班可匹配代称，例如教学班 ID、课程名 + 尾号、课程代码 + 尾号 |
| `admin_class_name` | 平台统一展示的行政班名称；合班时用 `、` 拼接 |
| `admin_class_names_json` | 行政班名称列表 |
| `admin_class_aliases_json` | 行政班可匹配代称，例如去掉 `班` 的简称、`扩招专升本` 的普通 `专升本` 写法 |
| `admin_class_count` | 行政班数量；需要精确单班匹配的场景只采信 `1` |
| `mapping_status` | `active` 表示当前有效，`stale` 表示本轮同步范围内未再次出现 |

唯一键是：

```text
teacher_id, school_code, academic_year, academic_term,
course_code, teaching_class_id, teaching_class_name
```

## 同步与更新

同步入口仍然是教务名册同步：

```text
sync_current_teacher_rosters_from_academic_system()
  -> _persist_rosters()
  -> refresh_teaching_class_mappings_from_roster()
```

每次名册同步成功后，系统会：

1. 写入或更新 `teacher_academic_roster_sync_items`，保留教学班、课程和 `class_composition`。
2. 写入或更新 `teacher_academic_roster_memberships`，保留学生、行政班和教学班关系。
3. 调用 `refresh_teaching_class_mappings_from_roster()` 刷新转换表。
4. 先把当前教师、当前同步范围内的旧映射标为 `stale`，再从最新名册关系重建并 upsert `active` 映射。

因此，新加入的教学班、新增行政班、以及教务系统新增的教学班代称，会随下一次名册同步进入转换表。若某个教学班暂时没有学生明细，系统也会从 `teacher_academic_roster_sync_items.class_composition` 尽量建立映射。

## 使用规则

业务代码不要在页面、模板或提醒里手写 `-0006` 等尾号解析规则。统一调用：

```python
from classroom_app.services.academic_class_mapping_service import (
    load_teaching_class_display_mappings,
    resolve_teaching_class_display_name_from_candidates,
    resolve_teaching_class_display_name,
)
```

读取历史日程、待办、邮件提醒、开课设置等带有多个候选名称的记录时，使用 `resolve_teaching_class_display_name_from_candidates()`，不要直接信任旧数据里的 `class_display_name`；早期同步可能已经把 `计算机网络原理-0006` 这类教务代号写进展示字段。

显示单个教学班名称时使用 `resolve_teaching_class_display_name()`；批量处理时使用 `load_teaching_class_display_mappings()`。

需要把智慧课堂或课堂开设记录自动精确匹配到一个平台行政班时，使用 `single_only=True`。合班映射仍可用于展示，但不要自动连到某一个班级，避免误绑定。

## 维护原则

- 广外教务数据展示优先级：转换表 `admin_class_name` > 名册中的行政班名 > 原始 `class_composition` > 原始 `teaching_class_name`。
- 旧数据读取时允许通过转换表自愈；写入新教务日程或待办时也应保存 `academic_teaching_class_name` 作为原始值，保存 `class_display_name` 作为展示值。
- 新增教务同步接口时，只要接口里出现教学班名或教学班代号，就应优先复用转换表服务，而不是新增一套名称清洗逻辑。
- 转换表是派生表，权威来源仍是教务名册同步数据；需要更新映射时应重新同步名册，不要直接手改显示字段。
