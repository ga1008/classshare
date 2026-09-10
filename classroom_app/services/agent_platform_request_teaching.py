"""Reviewed teaching setup and versioned existing classroom plan edits.

Course/offering save routes accept existing ids on the Web. These two creation
capabilities intentionally omit those ids, so a model cannot turn creation into
replacement of lessons, sessions or linked classes. Planned classroom creation
requires the exact revision returned by the normal preview.
"""


def build_capabilities(RequestCapability, spec, ID):
    text = lambda size, **kw: spec('string', minLength=0, maxLength=size, **kw)
    course = {
        'name': spec('string', required=True, maxLength=120),
        'description': text(2000, allowNewlines=True), 'sect_name': text(80), 'department': text(160),
        'credits': spec('string', maxLength=6, pattern=r'\d{1,3}(?:\.\d{1,2})?'),
    }
    selection = {'class_id': ID, 'course_id': ID, 'semester_id': ID, 'textbook_id': ID}
    schedule = {
        **selection, 'class_ids': spec('ids', minItems=1, maxItems=30),
        'first_class_date': spec('string', maxLength=10, pattern=r'\d{4}-\d{2}-\d{2}'),
        'weekly_schedule': spec('json', maxBytes=4096, description='Array of {weekday: 0..6 (Monday..Sunday), section_count: positive integer}; normal schedule validation applies.'),
        'schedule_source': spec('string', enum=['fixed_cycle', 'academic_sync'], maxLength=20),
        'academic_teaching_class_id': text(160), 'academic_teaching_class_name': text(200),
    }
    return (
        RequestCapability('http.teaching.course.create', '新建自己的课程基础资料', 'POST', '/api/manage/courses/create',
            'manage_parts.classes_courses_courses', 'api_create_course', 'dc92879ab0f3096ba1783894a4760650d5e9124c071cd420debc5e9b16cd77b6', {'body': course}, transport='form'),
        RequestCapability('http.teaching.course.plan.create', '新建课程并配置课程课时内容', 'POST', '/api/manage/courses/save',
            'manage_parts.classes_courses_courses', 'api_save_course', 'e93b3b789579786346f1ac8a71fa134d7961ee2ebdba63a8850fa46f730eb766', {'body': {
                **course, 'total_hours': spec('integer', minimum=0, maximum=1000),
                'lessons': spec('json', required=True, maxBytes=48000, description='Array of {title, content, section_count, learning_material_id?}; normal lesson and material-use validation applies.'),
            }}, max_body_bytes=60000),
        RequestCapability('http.teaching.class.create', '新建自己的自定义班级', 'POST', '/api/manage/classes/custom',
            'manage_parts.classes_courses_classes', 'api_create_custom_class', '21637078f216cc4fd8617112e8726eac71fddae9c6d54373285149a96cdb41c9', {'body': {
                'class_name': spec('string', required=True, maxLength=120), 'school_name': text(160),
                'college': text(160), 'department': text(160), 'major': text(160),
                'description': text(1000, allowNewlines=True),
                'scope_level': spec('string', enum=['private','department','school'], maxLength=20),
            }}, transport='form'),
        RequestCapability('http.teaching.student.create', '向自己可管理班级加入新学生', 'POST', '/api/manage/classes/{class_id}/students',
            'manage_parts.classes_courses_classes', 'api_create_class_student', 'bfb203a4b5ea11e740c46698e095c177ddd86152a57e2c8b5533e727db497d55', {'path': {'class_id': ID}, 'body': {
                'name': spec('string', required=True, maxLength=80), 'student_id_number': spec('string', required=True, maxLength=80),
                'gender': text(20), 'email': text(160), 'phone': text(80),
            }}, transport='form'),
        RequestCapability('http.teaching.student.status', '按名单版本调整学生学籍并撤销旧授权', 'POST', '/api/manage/students/{student_id}/status',
            'manage_parts.classes_courses_classes', 'api_update_class_student_status', 'a234de0113e85fae4afe7930c8406ced1c8f06ab6e0bb64cd2c070a2b975f2fa', {'path': {'student_id': ID}, 'body': {
                'enrollment_status': spec('string', required=True, enum=['active','suspended'], maxLength=10),
                'enrollment_note': text(500, allowNewlines=True),
                'expected_updated_at': spec('string', required=True, maxLength=100, description='class.students enrollment_status_updated_at; use legacy only when the stored value is empty.'),
            }}, transport='form'),
        RequestCapability('http.teaching.offering.preview', '预览当前资料对应的课堂排课、历史影响及版本', 'POST', '/api/manage/class_offerings/preview',
            'manage_parts.classes_courses_offerings', 'api_preview_class_offering', 'c57753d4a872ee1f2e5b14e93e612957ad5cc97536dffa688aa6149242594e69', {'body': {
                **schedule, 'offering_id': spec('integer', minimum=1),
            }}, mutates=False),
        RequestCapability('http.teaching.offering.plan.create', '按已核对预览新开课堂并生成课次', 'POST', '/api/manage/class_offerings/save',
            'manage_parts.classes_courses_offerings', 'api_save_class_offering', '666ffa51e316a564ab0bf7477ffd58395cceb0fbba53e673ed4137e7b0c728af', {'body': {
                **schedule, 'expected_plan_revision': spec('string', required=True, minLength=64, maxLength=64, pattern=r'[0-9a-f]{64}'),
            }}),
        RequestCapability('http.teaching.offering.plan.update', '按新预览更新自己的课堂排课；停排保留历史、拒绝覆盖已使用内容', 'POST', '/api/manage/class_offerings/save',
            'manage_parts.classes_courses_offerings', 'api_save_class_offering', '666ffa51e316a564ab0bf7477ffd58395cceb0fbba53e673ed4137e7b0c728af', {'body': {
                **schedule, 'offering_id': ID,
                'expected_plan_revision': spec('string', required=True, minLength=64, maxLength=64, pattern=r'[0-9a-f]{64}'),
            }}),
        RequestCapability('http.teaching.offering.create', '新建课堂基础绑定（学期、教材、班级和课程）', 'POST', '/api/manage/class_offerings/create',
            'manage_parts.classes_courses_offerings', 'api_create_class_offering', '9d815747967293ae15814933708790f2b306b3b484f73cf0bda839abbb55e914', {'body': {
                **selection, 'class_ids': spec('string', maxLength=600, pattern=r'[1-9][0-9]*(?:,[1-9][0-9]*){0,29}'),
            }}, transport='form'),
    )
