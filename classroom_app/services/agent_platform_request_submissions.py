"""Student draft/submit/withdraw reuse their complete ordinary Web workflows."""


def build_capabilities(Capability, spec, _id):
    assignment = spec('string', required=True, maxLength=128, pattern=r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}')
    version = spec('string', required=True, minLength=0, maxLength=128)
    answers = spec('json', maxBytes=400000)
    manifest = spec('json', maxBytes=65536)
    return (
        # This normal GET closes overdue assignments. It is deliberately a B
        # request with write admission, not mislabeled as a pure A projection.
        Capability('http.assignment.draft.get', '读取自己的作业草稿与提交版本', 'GET', '/api/assignments/{assignment_id}/draft',
            'homework_parts.drafts', 'get_assignment_draft', '349445997094dec0736f0de14a7000c2955d9ea57e02300f82b2db43578aca2c',
            {'path': {'assignment_id': assignment}}, response_contract='assignment_draft'),
        Capability('http.assignment.draft.save', '保存自己的作业草稿及任务附件，可分批保存后统一提交', 'POST', '/api/assignments/{assignment_id}/draft',
            'homework_parts.drafts', 'save_assignment_draft', 'b2c9c93d87e1436cc3a77f6b01aea2d65f1e938f0cfc25185275428507a01745',
            {'path': {'assignment_id': assignment}, 'body': {'answers_json': answers, 'manifest': manifest,
                'current_page': spec('integer', minimum=0, maximum=10000),
                'client_updated_at': spec('string', maxLength=100), 'replace_question_ids': spec('json', maxBytes=16384),
                'expected_submission_version': version}}, transport='form', allows_files=True, max_body_bytes=512000,
            response_contract='assignment_draft'),
        Capability('http.assignment.submit', '按当前学生身份提交作业，沿用原有附件与评分流程', 'POST', '/api/assignments/{assignment_id}/submit',
            'homework_parts.submissions', 'submit_assignment', 'f6d04ba0ca3b24f9b7328ebd1057ab32a6dd9b261a940ac4ed193fb003a47937',
            {'path': {'assignment_id': assignment}, 'body': {'answers_json': answers, 'manifest': manifest,
                'started_at': spec('string', maxLength=100), 'use_server_draft': spec('boolean'),
                'expected_submission_version': version}}, transport='form', allows_files=True, max_body_bytes=512000),
        Capability('http.assignment.withdraw', '撤回自己的未批改作业，沿用网页撤回规则', 'DELETE', '/api/assignments/{assignment_id}/withdraw',
            'homework_parts.submissions', 'withdraw_submission', '4fc6b50fa266d52fb6d82fe7b42a4dc575fae95bfcbf432d5351b3a4f77e6dc2',
            {'path': {'assignment_id': assignment}}),
    )
