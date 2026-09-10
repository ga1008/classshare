"""Fresh-user receipt adapter for the shared teaching confirmation domain."""
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder

from . import agent_teaching_confirmation_actions as domain

ACTION_DEFINITIONS = domain.ACTION_DEFINITIONS
prepare_user_confirmation = domain.prepare_teaching_confirmation


def dispatch_user_confirmation(conn, *, user, source_session_id, task_id, operation_id, action, params, confirmation_inputs):
    from .agent_action_registry import validate_action_params
    from .agent_business_confirmation_service import claim_business_confirmation
    from .agent_operation_service import complete_user_agent_operation

    clean, errors = validate_action_params(action, params, reject_unknown=True)
    if errors or not clean.get('expected_review_hash'):
        raise HTTPException(400, '请先读取本次教学业务核对快照。')
    if not isinstance(confirmation_inputs, dict) or set(confirmation_inputs) != {'accepted_warning_codes','confirmation_note','confirmation_text'}:
        raise HTTPException(400, '请提交本人核对说明、提示选择及手工输入的名称。')
    codes, note, name = (confirmation_inputs[key] for key in ('accepted_warning_codes','confirmation_note','confirmation_text'))
    if (not isinstance(codes, list) or len(codes)>30
            or any(not isinstance(code,str) or not 1<=len(code)<=100 for code in codes)
            or len(set(codes))!=len(codes) or not isinstance(note,str) or len(note)>2000 or not note.strip()
            or not isinstance(name,str) or len(name)>500 or not name.strip()
            or any(0xD800<=ord(char)<=0xDFFF for text in [note,name,*codes] for char in text)):
        raise HTTPException(400, '核对说明、提示选择或手工名称格式不正确。')
    declaration = {'accepted_warning_codes':sorted(codes),'confirmation_note':note.strip(),'confirmation_text':name.strip()}
    claim = claim_business_confirmation(conn,user=user,source_session_id=source_session_id,task_id=task_id,
        operation_id=operation_id,action=action,params={**clean,'user_confirmation':declaration})
    if not claim['claimed']:
        if claim['operation']['status'] != 'completed':
            raise HTTPException(409,'本次操作尚无确定回执，请先核对。')
        return {'operation_id':operation_id,'result':claim['operation']['result'],'replayed':True}
    result = jsonable_encoder(domain.execute_teaching_confirmation(conn,action=action,params=clean,
        user=user,confirmation_inputs=declaration))
    complete_user_agent_operation(conn,user=user,source_session_id=source_session_id,task_id=task_id,
        operation_id=operation_id,result=result)
    return {'operation_id':operation_id,'result':result,'replayed':False}
