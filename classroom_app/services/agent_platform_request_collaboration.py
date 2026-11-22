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
            '9c14221fb21add8495667a8a2ddea53fe8d55d70d012e6a96b1eb9d9287a9bd4', {'path': {'class_offering_id': ID}, 'body': {
                **fields, 'name': {**fields['name'], 'required': True}, 'member_student_ids': spec('ids', minItems=0, maxItems=12)}}),
        RequestCapability('http.collaboration.group.update', '教师或组长修改小组信息', 'PUT',
            '/api/collaboration/groups/{group_id}', 'collaboration', 'update_study_group',
            '4230a6f4823e12c1283627d58a2e64355ee1090a612eb2d645d6a3f7c3da2cae', {'path': group_path, 'body': {
                **fields, 'status': spec('string', enum=['active', 'archived'], maxLength=10)}}),
        RequestCapability('http.collaboration.group.join', '以自己身份加入开放的小组', 'POST',
            '/api/collaboration/groups/{group_id}/join', 'collaboration', 'join_study_group',
            'd62de52fdacf25ebb1a466da550ed1f24779340e7dc07c10341baf2683bf129f', {'path': group_path}),
        RequestCapability('http.collaboration.group.leave', '退出自己所在的小组', 'POST',
            '/api/collaboration/groups/{group_id}/leave', 'collaboration', 'leave_study_group',
            '3f47b3854c738fb2e716932bd1e3091a1ca3450c98302f5de187e4fb4ac49b17', {'path': group_path}),
        RequestCapability('http.collaboration.member.add', '教师添加课堂内的小组成员', 'POST',
            '/api/collaboration/groups/{group_id}/members', 'collaboration', 'add_study_group_member',
            'bc103d0f4b57c7c1ddd2f1d0b1273a3ff2effbf6ca06d56c65bf9bf9222c29fb', {'path': group_path, 'body': {'student_id': ID}}),
        RequestCapability('http.collaboration.member.remove', '按教师或邀请组创建者权限移出成员', 'DELETE',
            '/api/collaboration/groups/{group_id}/members/{student_id}', 'collaboration', 'remove_study_group_member',
            'd0ed6181dfc998c26f4ad7e99022e3bd76d7efd77d4dd4f5a6bac51cc38ba65b', {'path': {**group_path, 'student_id': ID}}),
        RequestCapability('http.collaboration.goal.update', '教师或组长设置小组目标与进度', 'PUT',
            '/api/collaboration/groups/{group_id}/goal', 'collaboration', 'set_study_group_goal',
            '24cc2c8c143bdc7f2a667a63cb85731a8aff546150ade5238b3770ac344f5e3a', {'path': group_path, 'body': {
                'goal_text': spec('string', minLength=0, maxLength=600, allowNewlines=True),
                'progress_percent': spec('integer', minimum=0, maximum=100)}}),
        RequestCapability('http.collaboration.submission.save', '教师或组长完整保存小组成果', 'PUT',
            '/api/collaboration/groups/{group_id}/submission', 'collaboration', 'save_group_submission',
            '4fbe1ae7b13d1f154537e401487e0c522cebb768b922b3ee628a94910a1d07c4', {'path': group_path, 'body': {
                'assignment_id': assignment, 'title': spec('string', required=True, maxLength=80),
                'summary_md': spec('string', required=True, minLength=0, maxLength=6000, allowNewlines=True),
                'final_file_id': {**optional_id, 'required': True}}}, max_body_bytes=32768),
        RequestCapability('http.collaboration.peer_review.save', '以自己身份完整保存同伴互评及其可见性', 'POST',
            '/api/collaboration/groups/{group_id}/peer-reviews', 'collaboration', 'save_peer_review',
            '64e8e112d28e815b2908c25e58b5355bd1f556bafdcf73ea446e51fc62278f4b', {'path': group_path, 'body': {
                'assignment_id': assignment, 'reviewee_student_id': ID,
                'responsibility_score': spec('integer', required=True, minimum=1, maximum=5),
                'collaboration_score': spec('integer', required=True, minimum=1, maximum=5),
                'quality_score': spec('integer', required=True, minimum=1, maximum=5),
                'comment': spec('string', required=True, minLength=0, maxLength=1200, allowNewlines=True),
                'share_with_reviewee': spec('boolean', required=True)}}),
        RequestCapability('http.collaboration.chat.read', '读取自己可访问的小组对话', 'GET',
            '/api/collaboration/groups/{group_id}/chat', 'collaboration', 'get_group_chat',
            'dcbcc4d1ebd2cc87044b562e1658619dfff3cf6d482bf8b515a29a4b5a86a70c', {'path': group_path, 'query': {
                'after_id': spec('integer', minimum=0, maximum=2**63-1)}}, mutates=False),
        RequestCapability('http.collaboration.chat.send', '以自己身份发送小组文字或已有组内附件', 'POST',
            '/api/collaboration/groups/{group_id}/chat', 'collaboration', 'send_group_chat',
            '548a373812c24c612f291483174108501f60593bc3deec463c41a5edc63b64d9', {'path': group_path, 'body': {
                'content': spec('string', required=True, minLength=0, maxLength=800, allowNewlines=True),
                'attachment_ids': spec('ids', minItems=0, maxItems=10),
                'message_type': spec('string', enum=['text'], maxLength=4)}}),
        RequestCapability('http.collaboration.chat.recall', '在原有时间窗口内撤回自己发送的小组消息', 'POST',
            '/api/collaboration/groups/{group_id}/chat/{message_id}/recall', 'collaboration', 'recall_group_chat',
            'be6580e8aa1efce677b50aafb09b6b383af74f9f484f666d81c9c6b7aaa5effe', {'path': {**group_path, 'message_id': ID}}),
    )
