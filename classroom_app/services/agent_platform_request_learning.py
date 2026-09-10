"""Reviewed ordinary todo and student invitation HTTP capabilities.

The factory keeps route declarations independent of transport. Todo ownership,
classroom membership, invitation recipient/leader checks and invitation rate
limits stay in the original services. Source pins require another review when
the registered handlers change.
"""


def build_capabilities(RequestCapability, spec, ID):
    todo = {
        'title': spec('string', maxLength=120),
        'notes': spec('string', minLength=0, maxLength=1200, nullable=True, allowNewlines=True),
        'start_at': spec('string', minLength=0, maxLength=40, nullable=True),
        'due_at': spec('string', minLength=0, maxLength=40, nullable=True),
        'priority': spec('string', enum=['high','normal','low'], maxLength=10),
        'reminder_enabled': spec('boolean'),
        'email_reminder_enabled': spec('boolean'),
        'reminder_lead_minutes': spec('integer', minimum=1, maximum=43200),
        'completed': spec('boolean'),
    }
    create = {**todo, 'title': spec('string', required=True, maxLength=120)}
    # Account-level todos are a normal teacher-only feature. The router retains
    # that restriction; student personal todos use an accessible classroom.
    account_scope = {'class_offering_id': spec('integer', minimum=1, maximum=2**63-1, nullable=True)}
    return (
        RequestCapability('http.todos.account.create','添加自己的教师账户待办','POST','/api/todos','learning','create_account_todo',
            'bc1d880e888da4ec15d0061c1293515df50389f78a9a8febd9e8ee0421f674d2',{'body':{**create,**account_scope}}),
        RequestCapability('http.todos.account.update','更新自己的教师账户待办','PATCH','/api/todos/{todo_id}','learning','update_account_todo',
            'cc140898524f509637b90674adc8e899f55eb403038bc6e5f465809af8e28709',{'path':{'todo_id':ID},'body':{**todo,**account_scope}}),
        RequestCapability('http.todos.account.delete','删除自己的教师账户待办','DELETE','/api/todos/{todo_id}','learning','delete_account_todo',
            '259ebb7eda6fa3502ebaed61cef86c3d24ebfd323ef442b85bb91bc595310483',{'path':{'todo_id':ID}}),
        RequestCapability('http.todos.classroom.list','读取当前课堂中自己的待办','GET','/api/classrooms/{class_offering_id}/todos','learning','get_classroom_todos',
            '3592c0f8d50e7f714215dbf22c812fcef29cfd51901a9326c8f2de9c8d4a1849',{'path':{'class_offering_id':ID}},mutates=False),
        RequestCapability('http.todos.classroom.create','添加自己的课堂待办','POST','/api/classrooms/{class_offering_id}/todos','learning','create_classroom_todo',
            '97044b62775c3afd58a08707324ee08e6c526d8b4156aaaad8c3d2118636d1d9',{'path':{'class_offering_id':ID},'body':create}),
        RequestCapability('http.todos.classroom.update','更新或完成自己的课堂待办','PATCH','/api/classrooms/{class_offering_id}/todos/{todo_id}','learning','update_classroom_todo',
            'ef2a56f334b062e115c350981f29067103440f25051bc2ade732a88519bbf24a',{'path':{'class_offering_id':ID,'todo_id':ID},'body':todo}),
        RequestCapability('http.todos.classroom.delete','删除自己的课堂待办','DELETE','/api/classrooms/{class_offering_id}/todos/{todo_id}','learning','delete_classroom_todo',
            '5d809c7d8b32a95b0ef8f8f084f1d851312775a324e81108c12665ed1d4d3d99',{'path':{'class_offering_id':ID,'todo_id':ID}}),
        RequestCapability('http.collaboration.snapshot','读取可访问课堂的协作状态与自己的邀请','GET','/api/collaboration/classrooms/{class_offering_id}/snapshot','collaboration','collaboration_snapshot',
            'a9699ebc8af111a0c834090c5e79ede697c787824c29327836b6847111112f7a',{'path':{'class_offering_id':ID}},mutates=False),
        # group_id deliberately omitted: the original candidate query doesn't
        # authorize an arbitrary group before using its membership to filter.
        RequestCapability('http.collaboration.invite_candidates','读取当前课堂可邀请的同学','GET','/api/collaboration/classrooms/{class_offering_id}/invite-candidates','collaboration','get_invite_candidates',
            '44cb7577102575696740b7630e72709bac9dfaf98cd2c0eec799777c59410936',{'path':{'class_offering_id':ID}},mutates=False),
        RequestCapability('http.collaboration.student_group.create','学生发起自己的邀请小组','POST','/api/collaboration/classrooms/{class_offering_id}/student-groups','collaboration','create_student_invite_group',
            '60038cbb848c118242c2c8ffa5ffa9aab9a037505ac7df58975a652c00b4dec9',{'path':{'class_offering_id':ID},'body':{
                'name':spec('string',required=True,maxLength=60),'invitee_student_ids':spec('ids',minItems=0,maxItems=30)}}),
        RequestCapability('http.collaboration.group.invite','邀请同学加入自己发起的小组','POST','/api/collaboration/groups/{group_id}/invite','collaboration','invite_group_members',
            '24a539d46f1fc1ebf18962afb6fc0d065a8a1611607114016a6ed22fe672d1c6',{'path':{'group_id':ID},'body':{
                'invitee_student_ids':spec('ids',required=True,minItems=1,maxItems=30)}}),
        RequestCapability('http.collaboration.invitation.accept','接受发给自己的小组邀请','POST','/api/collaboration/invitations/{invitation_id}/accept','collaboration','accept_group_invitation',
            '9d9d7aec5a7524ee58daebfd8b4ef5510d56d1c699058ba8fa46b018d4c80784',{'path':{'invitation_id':ID}}),
        RequestCapability('http.collaboration.invitation.decline','拒绝发给自己的小组邀请','POST','/api/collaboration/invitations/{invitation_id}/decline','collaboration','decline_group_invitation',
            '410ebb4e473873c19ff76ab7bf0f66d4ce0607d7fb3e9a486423622fd05c3ff4',{'path':{'invitation_id':ID}}),
    )
