"""Signature visibility and material-bound requests through normal user routes.

Seeing a signature is never equivalent to using it. No image download, approval,
signature consumption or material application is granted by this catalog.
"""


def collection_response(payload, key):
    """Recognize the existing collection shape, without claiming business effects."""
    if ('status' in payload and not isinstance(payload['status'],str)) or payload.get('status') in ('error','failed') or payload.get('success') is False:
        return False
    if not isinstance(payload.get('items'),list) or len(payload['items'])>500 or not all(isinstance(item,dict) for item in payload['items']):
        return False
    if key=='http.signatures.function_points':
        return set(payload)=={'items'}
    actor=payload.get('actor')
    if not isinstance(actor,dict) or actor.get('role') not in {'teacher','student'} or type(actor.get('id')) is not int or actor['id']<=0:
        return False
    if key=='http.signatures.list':
        return (type(payload.get('total')) is int and payload['total']>=0 and isinstance(payload.get('stats'),dict)
                and isinstance(payload.get('selected_school'),dict) and isinstance(payload.get('school_options'),list))
    if key=='http.signatures.requests.list':
        return (type(payload.get('total')) is int and payload['total']>=0 and type(payload.get('offset')) is int
                and payload.get('direction') in {'incoming','outgoing'} and isinstance(payload.get('status'),str))
    if key=='http.signatures.teachers':
        return isinstance(payload.get('selected_school'),dict)
    return key in {'http.signatures.schools','http.signatures.usage'}


def build_capabilities(RequestCapability, spec, ID):
    text=lambda maximum=80:spec('string',minLength=0,maxLength=maximum)
    point=spec('string',required=True,minLength=1,maxLength=120,pattern=r'[a-z][a-z0-9_.]*')
    material={'material_type':spec('string',required=True,enum=['academic_final_material','assessment_plan'],maxLength=30),
              'material_id':spec('string',required=True,minLength=1,maxLength=160,pattern=r'[A-Za-z0-9_-]+')}
    return (
        RequestCapability('http.signatures.list','读取当前账号可见签章与可用状态','GET','/api/signatures/list','signatures','api_list_signatures',
            'e16176d2b06ce6cea93525eed6289226414d696151dc4ece880a4222d2a7aea3',{'query':{'q':text(),'school_code':text(),'owner_role':text(20),'subject_role':text(20),
                'scope':text(20),'identity_category':text(),'function_point_key':text(120),'limit':spec('integer',minimum=1,maximum=100)}},mutates=False,response_contract='signature_collection'),
        RequestCapability('http.signatures.schools','读取签章目录可选择的学校','GET','/api/signatures/schools','signatures','api_signature_school_options',
            '465555761c21c6c8e97e92370dce91703d2a5946720dac0edbe30e6b1eb7e1bc',{'query':{'q':text()}},mutates=False,response_contract='signature_collection'),
        RequestCapability('http.signatures.teachers','读取当前学校签章归属教师候选','GET','/api/signatures/teachers','signatures','api_signature_teacher_options',
            '15c9c5f206e293881c57e0209b0fb06d578e62bfd597de169458f97fe90cd142',{'query':{'q':text(),'school_code':text(),'limit':spec('integer',minimum=1,maximum=60)}},mutates=False,response_contract='signature_collection'),
        RequestCapability('http.signatures.function_points','读取平台启用的签章功能点','GET','/api/signatures/function-points','signatures','api_signature_function_points',
            'eef0310b17a7658ee02e35fc342c653c375fe53c231ead8a05529c960f888456',mutates=False,response_contract='signature_collection'),
        RequestCapability('http.signatures.requests.list','读取本人申请或当前账号有权处理的签章申请','GET','/api/signatures/requests','signatures','api_list_signature_access_requests',
            'add084daeed6e0e89aa2c19cb50cb7bcd74743a2ba93b222561bf7c20a6a1bc5',{'query':{'direction':spec('string',enum=['incoming','outgoing'],maxLength=10),
                'status':text(30),'q':text(),'document_type':text(),'requester_role':text(20),'identity':text(60),'organization':text(),
                'request_kind':text(),'batch_id':text(120),'offset':spec('integer',minimum=0,maximum=10000),'limit':spec('integer',minimum=1,maximum=50)}},mutates=False,response_contract='signature_collection'),
        RequestCapability('http.signatures.request.detail','读取有权查看的签章申请与原文档预览入口','GET','/api/signatures/requests/{request_id:int}','signatures','api_signature_request_detail',
            '72eb9b7ac0f36e73a1475b2d17c107d3d33860405c4e77b1503df0d3ef6fe39c',{'path':{'request_id':ID}},mutates=False),
        RequestCapability('http.signatures.usage','读取本人归属签章的使用记录','GET','/api/signatures/usage-logs','signatures','api_list_signature_usage_logs',
            '96c24d3202e4dd027ed6277c3eebfaaf76175dc1124f8b088b074eb6f7177fd7',{'query':{'limit':spec('integer',minimum=1,maximum=100)}},mutates=False,response_contract='signature_collection'),
        RequestCapability('http.signatures.point.state','读取指定材料版本的签章点、可用签章和当前申请流程','GET','/api/signatures/points/{function_point_key}/state','signatures','api_signature_point_state',
            '97fd35f7becfac1a4eff64c9c6ea1c12a75a88c778f4a3eb33d897c42767b53f',{'path':{'function_point_key':point},'query':{**material,'q':text()}},mutates=False),
        RequestCapability('http.signatures.flow.create','基于已读取的材料版本发起签章申请，保留文档快照并等待人工审批或应用','POST','/api/signatures/points/{function_point_key}/flows','signatures','api_create_signature_point_flow',
            '50ac8d7f72f09f5cb9cea6a8ef5a153254a11bd150f426c4d37581a7d3e7933a',{'path':{'function_point_key':point},'body':{**material,
                'expected_revision':spec('string',required=True,minLength=1,maxLength=160),
                'signature_ids':spec('ids',required=True,minItems=1,maxItems=12),'note':text(300)}},max_body_bytes=4096),
        RequestCapability('http.signatures.flow.end','结束本人尚未应用完成的签章流程并取消待审批项','POST','/api/signatures/point-flows/{flow_id:int}/end','signatures','api_end_signature_point_flow',
            '6663d2ae2a64100ab2d656bca015ccda6867bdf403ef883fed76a26e44d11e23',{'path':{'flow_id':ID}}),
        RequestCapability('http.signatures.request.cancel','撤销本人仍待审批的签章申请','POST','/api/signatures/requests/{request_id:int}/cancel','signatures','api_cancel_signature_access_request',
            '3e9cc20b1cc7777a49810ba754a0df3ccc577b8f09a797d5e0aea1a3c5e9bb38',{'path':{'request_id':ID}}),
    )
