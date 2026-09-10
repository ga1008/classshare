"""A current reviewer confirms one material-scoped signature use request.

Claim/identity transfers and document application are separate operations. Only
a human may choose approve/reject after reviewing the original document and
the current request state shown by the platform.
"""
import hashlib
import json
import re

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder

ACTION_DEFINITIONS = {
    'review_signature_request': {
        'label':'核对签章申请并审批', 'done_label':'已处理签章申请', 'risk':'high',
        'execution_mode':'user_confirmation', 'roles':['teacher','student'], 'human_only':True,
        'description':'仅生成当前有权审批用户的确认提案：平台展示材料原快照和签章申请，由本人选择批准或拒绝。不能处理签章认领或身份转移，不能代用户确认。',
        'confirmation_note':'请阅读申请时文档，再由本人选择批准或拒绝。此处只处理该申请，不直接使用签章。',
        'fields':{'request_id':{'type':'int','required':True},
                  'expected_snapshot_id':{'type':'str','max_chars':64},
                  'expected_review_hash':{'type':'str','max_chars':64}},
    },
}


def _review(conn, user, request_id):
    from . import signature_service as signatures
    from .material_signature_service import authorized_request, load_material

    request = authorized_request(conn,user,request_id)
    if request.get('request_kind','use') != 'use':
        raise HTTPException(400, '签章认领与身份转移不能通过材料审批确认。')
    if not request.get('can_review'):
        raise HTTPException(403 if request['status']=='pending' else 409, '当前账号不能继续审批这条申请。')
    snapshot_id = str(request.get('snapshot_id') or '')
    row = conn.execute('SELECT * FROM signature_material_snapshots WHERE id=?',(snapshot_id,)).fetchone()
    if not row:
        raise HTTPException(409, '该申请没有可核对的材料快照，请通过原申请页面处理或重新申请。')
    snapshot = dict(row)
    actor = signatures.build_signature_actor(conn,user)
    signature_row = conn.execute('SELECT * FROM electronic_signatures WHERE id=?',(int(request['signature_id']),)).fetchone()
    if not signature_row:
        raise HTTPException(409, '签章已变化，请重新读取申请。')
    signature = dict(signature_row)
    source, issue = {}, ''
    try:
        material = load_material(conn,{'role':request['requester_role'],'id':request['requester_id']},snapshot)
        source = {key:material[key] for key in ('material_revision','content_fingerprint','owner_id')}
        if any(source[key] != snapshot[key] for key in source):
            issue = '材料内容或归属已变化，本次只能拒绝；请申请人重新申请。'
        requester = signatures.build_signature_actor(conn,{'role':request['requester_role'],'id':request['requester_id']})
        if not signatures.can_view_signature(requester,signature):
            issue = '申请人已不在签章可见范围内，本次只能拒绝。'
    except signatures.SignatureServiceError as exc:
        issue = '申请人的当前材料权限已变化，本次只能拒绝。'
        source = {'unavailable_status':exc.status_code}
    if request.get('signature_hash') and request['signature_hash'] != signature['file_hash']:
        issue = '签章图片已更换，本次只能拒绝；请申请人重新确认图片并申请。'
    if signature.get('status') != 'active' or signature.get('deleted_at'):
        issue = '签章已停用，本次只能拒绝。'
    admin_override = actor['is_super_admin'] and not any(
        entry['role']==actor['role'] and entry['id']==actor['id'] and entry['status']=='pending' for entry in request['reviewers'])
    warnings = ([{'code':'admin_override','message':'你将以管理员身份代为审批，决定会记录在你的账号下。'}] if admin_override else [])
    other_reviewers = sum(entry['status']=='pending' and (entry['role'],entry['id'])!=(actor['role'],actor['id'])
                          for entry in request['reviewers'])
    if other_reviewers:
        warnings.append({'code':'other_reviewers_pending','message':
            f'另有 {other_reviewers} 位待处理审批人。你的拒绝意见不会立即结束申请，其他审批人仍可作出决定。'})
    if issue:
        warnings.append({'code':'approval_unavailable','message':issue})
    review = {'request_id':request_id,'requester_name':request['requester_name'],
        'signature_name':request['signature_name'],'point_label':'、'.join(
            item.get('function_point_label') or item['function_point_key'] for item in request['items']) or request['function_point_key'],
        'material_title':snapshot['title'],'material_revision':snapshot['material_revision'],
        'request_note':request['request_note'],'document_url':f'/api/signatures/requests/{request_id}/preview',
        'snapshot_id':snapshot_id,'can_execute':True,'approve_allowed':not bool(issue),'warnings':warnings,
        'blocking_reasons':[],'scope_notice':'批准仅针对申请时的材料版本与签章点；实际应用签章另行进行。'}
    binding = {'request':request,'snapshot':{key:snapshot[key] for key in (
        'id','material_type','material_id','material_revision','owner_role','owner_id','content_fingerprint','title')},
        'signature':{key:signature.get(key) for key in ('file_hash','owner_role','owner_id','subject_role','subject_id',
            'scope_level','school_code','school_name','college','department','status','deleted_at')},
        'actor':{'role':actor['role'],'id':actor['id'],'admin':bool(actor['is_super_admin'])},'source':source,'review':review}
    digest = hashlib.sha256(json.dumps(binding,sort_keys=True,ensure_ascii=False,separators=(',',':'),default=str).encode()).hexdigest()
    return {'params':{'request_id':request_id,'expected_snapshot_id':snapshot_id,'expected_review_hash':digest},'review':review}


def prepare_user_confirmation(conn, *, action, params, user):
    from .signature_service import SignatureServiceError
    try:
        return _review(conn,user,params['request_id'])
    except SignatureServiceError as exc:
        raise HTTPException(exc.status_code,exc.message) from None


def dispatch_user_confirmation(conn, *, user, source_session_id, task_id, operation_id, action, params, confirmation_inputs):
    from .agent_action_registry import validate_action_params
    from .agent_operation_service import complete_user_agent_operation
    from .agent_business_confirmation_service import claim_business_confirmation
    from .signature_service import SignatureServiceError
    from .signature_workflow_service import review_access_request
    from .signature_workflow_lock_service import lock_signature_workflows

    clean, errors = validate_action_params(action,params,reject_unknown=True)
    if errors or any(not re.fullmatch('[0-9a-f]{64}',str(clean.get(key) or '')) for key in ('expected_snapshot_id','expected_review_hash')):
        raise HTTPException(400,'请先读取本次签章申请核对快照。')
    if not isinstance(confirmation_inputs,dict) or set(confirmation_inputs) != {'decision','accepted_warning_codes','confirmation_note'}:
        raise HTTPException(400,'请由本人选择审批决定并提交核对说明。')
    decision = confirmation_inputs['decision']
    codes, note = confirmation_inputs['accepted_warning_codes'], confirmation_inputs['confirmation_note']
    if (decision not in ('approve','reject') or not isinstance(codes,list) or len(codes)>10
            or any(not isinstance(code,str) or not re.fullmatch('[a-z_]{1,60}',code) for code in codes) or len(set(codes))!=len(codes)
            or not isinstance(note,str) or len(note)>300 or any(0xD800<=ord(char)<=0xDFFF for char in note)):
        raise HTTPException(400,'审批决定、提示选择或说明格式不正确。')
    declaration={'decision':decision,'accepted_warning_codes':sorted(codes),'confirmation_note':note.strip()}
    claim=claim_business_confirmation(conn,user=user,source_session_id=source_session_id,task_id=task_id,
        operation_id=operation_id,action=action,params={**clean,'user_confirmation':declaration})
    if not claim['claimed']:
        if claim['operation']['status']!='completed':
            raise HTTPException(409,'尚未取得本次审批的确定回执，请先核对。')
        return {'operation_id':operation_id,'result':claim['operation']['result'],'replayed':True}
    try:
        lock_signature_workflows(conn,request_ids=[clean['request_id']])
        current=_review(conn,user,clean['request_id'])
        if current['params']!=clean:
            raise HTTPException(409,'申请、材料或审批权限已变化，请重新读取并核对。')
        expected={item['code'] for item in current['review']['warnings']}
        if set(codes)!=expected or ((expected or decision=='reject') and not declaration['confirmation_note']):
            raise HTTPException(400,'请逐项核对所有提示并填写本次说明。')
        if decision=='approve' and not current['review']['approve_allowed']:
            raise HTTPException(409,'当前材料或签章状态不能批准，请重新读取并核对。')
        outcome=review_access_request(conn,user,clean['request_id'],action=decision,note=declaration['confirmation_note'],
            expected_snapshot_id=clean['expected_snapshot_id'])
    except SignatureServiceError as exc:
        raise HTTPException(exc.status_code,exc.message) from None
    label = '已批准签章申请' if decision=='approve' else (
        '已提交拒绝意见，申请仍待其他审批人处理' if outcome['request']['status']=='pending' else '已拒绝签章申请')
    result={'label':label,'request':jsonable_encoder(outcome['request']),
        'ref_id':clean['request_id'],'url':f"/api/signatures/requests/{clean['request_id']}/preview",'confirmation_source':'authenticated_user'}
    complete_user_agent_operation(conn,user=user,source_session_id=source_session_id,task_id=task_id,
        operation_id=operation_id,result=result)
    return {'operation_id':operation_id,'result':result,'replayed':False}
