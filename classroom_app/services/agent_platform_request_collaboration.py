"""Reviewed group roster, private work and group discussion HTTP operations.

These use the normal classroom/member/leader checks. A file id is only an
existing group artifact, never a storage path. Complete work/review payloads
prevent the normal replacement semantics from clearing omitted fields.
"""


def build_capabilities(RequestCapability, spec, ID):
    group_path = {'group_id': ID}
    optional_id = {**ID, 'required': False, 'nullable': True}
    assignment = spec('string', nullable=True, minLength=0, maxLength=80)
    fields = {
        'name': spec('string', maxLength=60),
        'description': spec('string', minLength=0, maxLength=1200, allowNewlines=True),
        'assignment_id': assignment,
        'join_policy': spec('string', enum=['open', 'locked', 'teacher_assigned', 'invite'], maxLength=20),
        'max_members': spec('integer', minimum=2, maximum=12),
        'leader_student_id': optional_id,
    }
    return (
        RequestCapability('http.collaboration.group.create', '创建课堂协作小组并按权限分配成员', 'POST',
            '/api/collaboration/classrooms/{class_offering_id}/groups', 'collaboration', 'create_study_group',
            '95ea70ecf710f4ba6eca808cef59b1c49fa78bdd82a270d71ad9a4cb916294b5', {'path': {'class_offering_id': ID}, 'body': {
                **fields, 'name': {**fields['name'], 'required': True}, 'member_student_ids': spec('ids', minItems=0, maxItems=12)}}),
        RequestCapability('http.collaboration.group.update', '教师或组长修改小组信息', 'PUT',
            '/api/collaboration/groups/{group_id}', 'collaboration', 'update_study_group',
            '9fa89140c50dc2abef82c67a035eae04fcb36072a5de0b8cb3ccf218a1d07a09', {'path': group_path, 'body': {
                **fields, 'status': spec('string', enum=['active', 'archived'], maxLength=10)}}),
        RequestCapability('http.collaboration.group.join', '以自己身份加入开放的小组', 'POST',
            '/api/collaboration/groups/{group_id}/join', 'collaboration', 'join_study_group',
            '5d3958eb615bb322602b4f6b417501346afd003659f240fcc4ac9ebecd5a9c23', {'path': group_path}),
        RequestCapability('http.collaboration.group.leave', '退出自己所在的小组', 'POST',
            '/api/collaboration/groups/{group_id}/leave', 'collaboration', 'leave_study_group',
            '7060b2a5856ae6ec0278f2abf6da1c1cc7a5034f73f6a25606de6750cba33146', {'path': group_path}),
        RequestCapability('http.collaboration.member.add', '教师添加课堂内的小组成员', 'POST',
            '/api/collaboration/groups/{group_id}/members', 'collaboration', 'add_study_group_member',
            '9b55c6ed3e6bd4f8682a2ca9b89798e87db610d6486a6b603825dfcae42815ad', {'path': group_path, 'body': {'student_id': ID}}),
        RequestCapability('http.collaboration.member.remove', '按教师或邀请组创建者权限移出成员', 'DELETE',
            '/api/collaboration/groups/{group_id}/members/{student_id}', 'collaboration', 'remove_study_group_member',
            '08d8aebecb8d124d6753384e81ce4a2bf35705e6aa9584601d0aeea3b6fa8865', {'path': {**group_path, 'student_id': ID}}),
        RequestCapability('http.collaboration.goal.update', '教师或组长设置小组目标与进度', 'PUT',
            '/api/collaboration/groups/{group_id}/goal', 'collaboration', 'set_study_group_goal',
            '08f8ab9d02391d48a293d583aee7f50f560728c00ef716c0253da799acb389de', {'path': group_path, 'body': {
                'goal_text': spec('string', minLength=0, maxLength=600, allowNewlines=True),
                'progress_percent': spec('integer', minimum=0, maximum=100)}}),
        RequestCapability('http.collaboration.submission.save', '教师或组长完整保存小组成果', 'PUT',
            '/api/collaboration/groups/{group_id}/submission', 'collaboration', 'save_group_submission',
            '43b56cb62a7de5bad1719b36af0dc50cc31034d644eed462e13879ea9d9ed032', {'path': group_path, 'body': {
                'assignment_id': assignment, 'title': spec('string', required=True, maxLength=80),
                'summary_md': spec('string', required=True, minLength=0, maxLength=6000, allowNewlines=True),
                'final_file_id': {**optional_id, 'required': True}}}, max_body_bytes=32768),
        RequestCapability('http.collaboration.peer_review.save', '以自己身份完整保存同伴互评及其可见性', 'POST',
            '/api/collaboration/groups/{group_id}/peer-reviews', 'collaboration', 'save_peer_review',
            'bb45b8b161c096deca83d3495ed4eb2e9fb533b44cae5d78ca147353728910cc', {'path': group_path, 'body': {
                'assignment_id': assignment, 'reviewee_student_id': ID,
                'responsibility_score': spec('integer', required=True, minimum=1, maximum=5),
                'collaboration_score': spec('integer', required=True, minimum=1, maximum=5),
                'quality_score': spec('integer', required=True, minimum=1, maximum=5),
                'comment': spec('string', required=True, minLength=0, maxLength=1200, allowNewlines=True),
                'share_with_reviewee': spec('boolean', required=True)}}),
        RequestCapability('http.collaboration.chat.read', '读取自己可访问的小组对话', 'GET',
            '/api/collaboration/groups/{group_id}/chat', 'collaboration', 'get_group_chat',
            '052dbe739aed4706e3a80f7c07ba87de9e9e5847ed182d19d5659165b0058e78', {'path': group_path, 'query': {
                'after_id': spec('integer', minimum=0, maximum=2**63-1)}}, mutates=False),
        RequestCapability('http.collaboration.chat.send', '以自己身份发送小组文字或已有组内附件', 'POST',
            '/api/collaboration/groups/{group_id}/chat', 'collaboration', 'send_group_chat',
            'c4f2e3d478dd6c5d4c6a70b5237ab0051f2bbe348f0d8b77a1fd6abf784eb385', {'path': group_path, 'body': {
                'content': spec('string', required=True, minLength=0, maxLength=800, allowNewlines=True),
                'attachment_ids': spec('ids', minItems=0, maxItems=10),
                'message_type': spec('string', enum=['text'], maxLength=4)}}),
        RequestCapability('http.collaboration.chat.recall', '在原有时间窗口内撤回自己发送的小组消息', 'POST',
            '/api/collaboration/groups/{group_id}/chat/{message_id}/recall', 'collaboration', 'recall_group_chat',
            '24d4afee5a2f15d4e2f2a9a1a493f949a189c2420f14664ecf7df2b3c9522d9f', {'path': {**group_path, 'message_id': ID}}),
    )
