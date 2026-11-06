"""Reviewed complete normal poll lifecycle; owner/class rules stay upstream."""


def build_capabilities(RequestCapability,spec,ID):
    fields={
        'title':spec('string',required=True,maxLength=120),
        'description':spec('string',minLength=0,maxLength=1000,allowNewlines=True),
        'vote_type':spec('string',enum=['single','multiple'],maxLength=10),
        'deadline_at':spec('string',nullable=True,minLength=0,maxLength=40),
        'allow_change':spec('boolean'),
        'max_changes':spec('integer',minimum=0,maximum=2**31-1),
        'result_visibility':spec('string',enum=['always','after_vote','after_close'],maxLength=15),
    }
    options=spec('strings',minItems=2,maxItems=12,maxLength=160)
    targets={
        'participant_ids':spec('ids',minItems=0,maxItems=1000),
        'class_offering_ids':spec('ids',minItems=0,maxItems=50),
    }
    create={**fields,**targets,'options':{**options,'required':True},'status':spec('string',enum=['draft','active'],maxLength=10)}
    # Normal PUT replaces every common field. Require a complete representation
    # so a title edit cannot reset visibility, deadline or change-vote policy.
    update={key:{**value,'required':True} for key,value in fields.items()}
    update.update({**targets,'options':options})
    return (
        RequestCapability('http.polls.candidates','读取当前课堂的投票参与人候选','GET','/api/polls/classrooms/{class_offering_id}/candidates','polls','classroom_poll_candidates',
            '0f44768c2f8255e564eac33a68134e0012bd1190b003593cfeb5e3390edb9600',{'path':{'class_offering_id':ID}},mutates=False),
        RequestCapability('http.polls.classroom.create','在可访问课堂创建投票','POST','/api/polls/classrooms/{class_offering_id}/polls','polls','create_classroom_poll',
            'd2a37b1de9cb2103cfb2ddb06a6672d91f11bd5e74aaa6af0cd4a41d4af2e8a2',{'path':{'class_offering_id':ID},'body':create}),
        RequestCapability('http.polls.manage.list','读取自己管理的投票活动','GET','/api/polls/manage/list','polls','management_poll_list',
            '8837a764b3f32edfa9409863b20680a45f31af990170bb942a21fdfa292b503b',mutates=False),
        RequestCapability('http.polls.manage.offerings','读取可分配投票的课堂','GET','/api/polls/manage/offerings','polls','management_offerings',
            '3dab27bd3ff9ba4304ca9e2ab154da96f2a607e32db30e41da29d523cc2f931f',mutates=False),
        RequestCapability('http.polls.manage.create','教师创建可分配到多课堂的投票','POST','/api/polls/manage/polls','polls','create_management_poll',
            '123c1e6f19f884a21a17ec1b25442c5ed24486c2ea8df10da60061206508dcdd',{'body':create}),
        RequestCapability('http.polls.update','完整更新自己管理的投票设置','PUT','/api/polls/{poll_id}','polls','poll_update',
            '842650ddfdfe9793c3ca028f417e819c39aeef64a024dad4210c4b17f3064c70',{'path':{'poll_id':ID},'body':update}),
        RequestCapability('http.polls.status','开始结束或恢复自己管理的投票','POST','/api/polls/{poll_id}/status','polls','poll_set_status',
            '769ff67fddc214a548c68e9cfe61e004c6a2061ff30cf21fbc5922e293eed81e',{'path':{'poll_id':ID},'body':{'status':spec('string',required=True,enum=['draft','active','closed'],maxLength=10)}}),
        RequestCapability('http.polls.assignments','设置自己管理投票的课堂分配','POST','/api/polls/{poll_id}/assignments','polls','poll_set_assignments',
            'eddac271e43eb841bd9f97ce27d00f5fb3f5dcbfc65655a42afbab1dac8f6935',{'path':{'poll_id':ID},'body':{'class_offering_ids':spec('ids',required=True,minItems=0,maxItems=50)}}),
        RequestCapability('http.polls.delete','删除自己管理的投票及其记录','DELETE','/api/polls/{poll_id}','polls','poll_delete',
            '8f3f53ef7de7599dcbb797b2f05175e59fc64a4c982275b0a6bcde938b2121ac',{'path':{'poll_id':ID}}),
    )
