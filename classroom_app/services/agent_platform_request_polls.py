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
            '9524ce909ef86bd25c038b97849e743904fe57e600fa2c6d692907d9fcfa172a',{'path':{'class_offering_id':ID}},mutates=False),
        RequestCapability('http.polls.classroom.create','在可访问课堂创建投票','POST','/api/polls/classrooms/{class_offering_id}/polls','polls','create_classroom_poll',
            '4d5bdbaeea45f308c519e347547e1c3ada1a1b86a7885a23e8e8cdb300bef391',{'path':{'class_offering_id':ID},'body':create}),
        RequestCapability('http.polls.manage.list','读取自己管理的投票活动','GET','/api/polls/manage/list','polls','management_poll_list',
            'e22fff42fbcc2d75ef4c15f78bc598e081de9a277a8ca54bc2f45a6ea62032a3',mutates=False),
        RequestCapability('http.polls.manage.offerings','读取可分配投票的课堂','GET','/api/polls/manage/offerings','polls','management_offerings',
            '5fc69f9308e69ad4f660c529e2f4385f61b4906e40f9b03d918261611bb43383',mutates=False),
        RequestCapability('http.polls.manage.create','教师创建可分配到多课堂的投票','POST','/api/polls/manage/polls','polls','create_management_poll',
            '9f07ffb1c28117ac9da7a3b8b78c07b89bde2ffb9e809c7e25d0ec06f2955c54',{'body':create}),
        RequestCapability('http.polls.update','完整更新自己管理的投票设置','PUT','/api/polls/{poll_id}','polls','poll_update',
            '32abec3541858b541c4bb9eea295cb9e6a712e77560db42eeba4e26b877e64ea',{'path':{'poll_id':ID},'body':update}),
        RequestCapability('http.polls.status','开始结束或恢复自己管理的投票','POST','/api/polls/{poll_id}/status','polls','poll_set_status',
            'a7f9efa2bc1da36ec50e1ef47a467600d4de8c0f1f988f7f86a93d41fce4cbf6',{'path':{'poll_id':ID},'body':{'status':spec('string',required=True,enum=['draft','active','closed'],maxLength=10)}}),
        RequestCapability('http.polls.assignments','设置自己管理投票的课堂分配','POST','/api/polls/{poll_id}/assignments','polls','poll_set_assignments',
            '24743b0b8d1479614f7079562f63980068eb248e7f9e3fd3d3840e1927bdd3f0',{'path':{'poll_id':ID},'body':{'class_offering_ids':spec('ids',required=True,minItems=0,maxItems=50)}}),
        RequestCapability('http.polls.delete','删除自己管理的投票及其记录','DELETE','/api/polls/{poll_id}','polls','poll_delete',
            '20df8b31dd41c8bfcff6a6ea6edef5af8cb7fbe8fd32ec558ba15cf6e9fb8d2c',{'path':{'poll_id':ID}}),
    )
