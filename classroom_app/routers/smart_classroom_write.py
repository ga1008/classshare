"""Teacher-owned school synchronization with explicit authorization and readback."""
import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
import httpx

from ..database import get_db_connection
from ..dependencies import get_current_teacher
from ..services import smart_classroom_write_service as service
from ..services import smart_classroom_attendance_adapter as adapter
from ..services import smart_classroom_auto_sync_service as auto_sync
from ..services import smart_classroom_workflow_service as workflow_service
from ..services.smart_classroom_session_service import SmartClassroomSessionError
from ..services.attendance_report_service import sign_attendance_source_option, _decode_option
from ..services.trusted_network_service import require_same_origin

def _private_response(response:Response):
    response.headers["Cache-Control"]="no-store"


router=APIRouter(prefix="/api/classrooms/{offering_id}/login-attendance/rounds/{round_id}/external",tags=["login_attendance_external"],dependencies=[Depends(_private_response)])
session_router=APIRouter(prefix="/api/classrooms/{offering_id}/login-attendance/sessions/{session_id}/external",tags=["login_attendance_external"],dependencies=[Depends(_private_response)])


class Strict(BaseModel):
    model_config=ConfigDict(extra="forbid")


class Term(Strict):
    year:str=Field(min_length=9,max_length=9)
    term:int=Field(ge=1,le=2)


class Source(Strict):
    source_token:str=Field(min_length=1,max_length=16000)


class BindingCreate(Source):
    remote_checkin_id:str=Field(min_length=1,max_length=160)


class Preview(Strict):
    binding_id:str=Field(min_length=1,max_length=64)


class RunCreate(Strict):
    preview_id:str=Field(min_length=1,max_length=64)
    idempotency_key:str=Field(min_length=1,max_length=160)


class Revision(Strict):
    expected_revision:int=Field(ge=1)


class Location(Strict):
    latitude:float=Field(ge=-90,le=90,allow_inf_nan=False,strict=True)
    longitude:float=Field(ge=-180,le=180,allow_inf_nan=False,strict=True)
    captured_at_ms:int=Field(gt=0,strict=True)


class AutoSyncCreate(Source):
    slot_token:str=Field(min_length=1,max_length=20000)
    enabled:bool=Field(default=True,strict=True)
    allow_create:bool=Field(default=False,strict=True)
    acknowledge_session_takeover:bool=Field(default=False,strict=True)
    location:Location|None=None
    expected_revision:int|None=Field(default=None,ge=1)
    idempotency_key:str=Field(min_length=1,max_length=160)


class WorkflowSync(Strict):
    idempotency_key:str=Field(min_length=8,max_length=100)
    expected_binding_revision:int=Field(ge=1)
    location:Location|None=None


class AlignmentConfirmation(Strict):
    scope:Literal['course','session']
    candidate_id:str=Field(min_length=1,max_length=100)
    expected_course_revision:int=Field(ge=0)
    expected_binding_revision:int=Field(ge=0)
    idempotency_key:str=Field(min_length=8,max_length=100)


@session_router.get("/workflow")
def workflow(offering_id:int,session_id:int,user:dict=Depends(get_current_teacher)):
    with get_db_connection() as conn:
        return workflow_service.workflow(conn,user,offering_id,session_id)


@session_router.post("/match")
async def match_lesson(offering_id:int,session_id:int,request:Request,user:dict=Depends(get_current_teacher)):
    require_same_origin(request)
    return await _call(asyncio.wait_for(workflow_service.match_lesson(user,offering_id,session_id),timeout=90))


@session_router.post('/confirm')
async def confirm_alignment(offering_id:int,session_id:int,payload:AlignmentConfirmation,request:Request,user:dict=Depends(get_current_teacher)):
    require_same_origin(request)
    return await _call(asyncio.wait_for(workflow_service.confirm_alignment(user,offering_id,session_id,payload.model_dump()),timeout=90))


@router.post("/sync",status_code=202)
async def sync_workflow(offering_id:int,round_id:int,payload:WorkflowSync,request:Request,user:dict=Depends(get_current_teacher)):
    require_same_origin(request)
    return await _call(asyncio.wait_for(workflow_service.dispatch_sync(user,offering_id,round_id,payload.model_dump(exclude_none=True)),timeout=90))


def _context(offering_id,round_id,user,request=None):
    if request is not None:
        require_same_origin(request)
    with get_db_connection() as conn:
        ctx=service.load_read_context(conn,user,round_id)
    if ctx["class_offering_id"] is None:
        raise HTTPException(409,"此课堂已归档，请从历史记录查看；不能在已删除课堂继续同步。")
    if int(ctx["class_offering_id"])!=int(offering_id):raise HTTPException(404,"该点名不属于此课堂。")
    return ctx


async def _call(awaitable):
    try:return await awaitable
    except SmartClassroomSessionError as exc:
        busy=exc.code=="session_busy"
        raise HTTPException(503 if busy else 409,{"code":exc.code,"message":str(exc)},
                            headers={"Retry-After":"5"} if busy else None) from exc
    except adapter.AttendanceSourceError as exc:
        raise HTTPException(503,{"code":exc.code,"message":str(exc)}) from exc
    except (httpx.HTTPError,TimeoutError):
        raise HTTPException(503,{"code":"school_connection_unavailable","message":"学校连接暂不可用。已提交任务请查看任务状态，系统会先回读核实，请勿重复创建点名。"}) from None
    except ValueError as exc:
        message=str(exc) if "TLS" in str(exc) or "可信 CA" in str(exc) else "学校账号连接验证未通过，请重新验证原账号。"
        raise HTTPException(409,{"code":"needs_teacher_reauth","message":message}) from None


@router.get("/capabilities")
def get_capabilities(offering_id:int,round_id:int,user:dict=Depends(get_current_teacher)):
    _context(offering_id,round_id,user)
    return {"status":"success",**service.capabilities()}


@router.post("/source-options")
async def source_options(offering_id:int,round_id:int,payload:Term,request:Request,user:dict=Depends(get_current_teacher)):
    ctx=_context(offering_id,round_id,user,request)
    if (str(ctx["year"]),str(ctx["term"]))!=(payload.year,str(payload.term)):raise HTTPException(409,"请选择该点名所属的明确学期。")
    found=await _call(adapter.list_attendance_source_options(int(user["id"]),payload.year,payload.term))
    items=[]
    for item in found["items"]:
        source={"remote_schedule_id":str(item["id"]),"course_name":str(item.get("course") or ""),"course_code":str(item.get("courseId") or ""),
                "remote_class_id":str(item.get("claId") or ""),"teaching_class_name":str(item.get("claName") or item.get("chooseCourseNo") or ""),
                "academic_year":payload.year,"academic_term":payload.term,"external_account_key":found["external_account_key"],
                "credential_id":found.get("credential_id"),"platform_code":found["platform_code"],"school_code":"gxufl"}
        items.append({"course_name":source["course_name"],"course_code":source["course_code"],"teaching_class_name":source["teaching_class_name"],
                      "source_token":sign_attendance_source_option(int(user["id"]),source)})
    return {"status":"success","items":items,"capabilities":service.capabilities()}


@router.post("/checkins")
async def checkins(offering_id:int,round_id:int,payload:Source,request:Request,user:dict=Depends(get_current_teacher)):
    ctx=_context(offering_id,round_id,user,request)
    source=_decode_option(payload.source_token,int(user["id"]))
    if (str(ctx["year"]),str(ctx["term"]))!=(str(source["academic_year"]),str(source["academic_term"])):raise HTTPException(409,"来源学期不一致。")
    async def fetch():
        from ..services.smart_classroom_integration_service import open_authenticated_smart_classroom_client
        with get_db_connection() as conn:access=service._account_access(conn,user["id"],source["external_account_key"])
        async with service._account_lease(source["external_account_key"]):
            async with open_authenticated_smart_classroom_client(access) as (client,_,_login):
                rows=await adapter.fetch_source_checkins(client,source["remote_schedule_id"])
                return {"status":"success","items":rows,"message":"请选择准确点名；列表代表授课班标识不等于实际排课标识。"}
    return await _call(asyncio.wait_for(fetch(),timeout=90))


@router.post("/bindings")
async def bind(offering_id:int,round_id:int,payload:BindingCreate,request:Request,user:dict=Depends(get_current_teacher)):
    _context(offering_id,round_id,user,request)
    return await _call(service.bind_remote_round(user,round_id,payload.source_token,payload.remote_checkin_id))


@router.get("/bindings")
def bindings(offering_id:int,round_id:int,user:dict=Depends(get_current_teacher)):
    _context(offering_id,round_id,user)
    with get_db_connection() as conn:return {"items":service.list_bindings(conn,user,round_id)}


@router.post("/bindings/{binding_id}/reauthorize")
def reauthorize(offering_id:int,round_id:int,binding_id:str,payload:Revision,request:Request,user:dict=Depends(get_current_teacher)):
    _context(offering_id,round_id,user,request)
    with get_db_connection() as conn:
        result=service.reauthorize_binding(conn,user,round_id,binding_id,payload.expected_revision);conn.commit();return result


@router.post("/preview")
async def preview(offering_id:int,round_id:int,payload:Preview,request:Request,user:dict=Depends(get_current_teacher)):
    _context(offering_id,round_id,user,request)
    return await _call(service.create_preview(user,round_id,payload.binding_id))


@router.post("/runs",status_code=202)
def create_run(offering_id:int,round_id:int,payload:RunCreate,request:Request,user:dict=Depends(get_current_teacher)):
    _context(offering_id,round_id,user,request)
    with get_db_connection() as conn:
        result=service.enqueue_readback(conn,user,round_id,payload.preview_id,payload.idempotency_key);conn.commit();return result


@router.get("/runs")
def runs(offering_id:int,round_id:int,user:dict=Depends(get_current_teacher)):
    _context(offering_id,round_id,user)
    with get_db_connection() as conn:return {"items":service.list_runs(conn,user,round_id)}


@router.get("/runs/{run_id}")
def run(offering_id:int,round_id:int,run_id:str,user:dict=Depends(get_current_teacher)):
    _context(offering_id,round_id,user)
    return service.get_run(user,round_id,run_id)


@router.post("/runs/{run_id}/refresh")
async def refresh(offering_id:int,round_id:int,run_id:str,request:Request,user:dict=Depends(get_current_teacher)):
    _context(offering_id,round_id,user,request)
    return await _call(service.reconcile_run(user,round_id,run_id))


@router.post("/runs/{run_id}/pause")
def pause(offering_id:int,round_id:int,run_id:str,payload:Revision,request:Request,user:dict=Depends(get_current_teacher)):
    _context(offering_id,round_id,user,request)
    with get_db_connection() as conn:
        result=service.pause_run(conn,user,round_id,run_id,payload.expected_revision);conn.commit();return result


@router.post("/slot-options")
async def slot_options(offering_id:int,round_id:int,payload:Source,request:Request,user:dict=Depends(get_current_teacher)):
    _context(offering_id,round_id,user,request)
    return await _call(auto_sync.list_slot_options(user,round_id,payload.source_token))


@router.get("/auto-sync")
def get_auto_sync(offering_id:int,round_id:int,user:dict=Depends(get_current_teacher)):
    _context(offering_id,round_id,user)
    with get_db_connection() as conn:return auto_sync.get_auto_sync(conn,user,round_id)


@router.put("/auto-sync")
async def configure_auto_sync(offering_id:int,round_id:int,payload:AutoSyncCreate,request:Request,user:dict=Depends(get_current_teacher)):
    _context(offering_id,round_id,user,request)
    return await _call(auto_sync.configure_auto_sync(user,round_id,payload.model_dump(exclude_none=True)))


@router.post("/auto-sync/pause")
def pause_auto_sync(offering_id:int,round_id:int,payload:Revision,request:Request,user:dict=Depends(get_current_teacher)):
    _context(offering_id,round_id,user,request)
    with get_db_connection() as conn:
        result=auto_sync.pause_auto_sync(conn,user,round_id,payload.expected_revision);conn.commit();return result


@router.post("/auto-sync/retry")
def retry_auto_sync(offering_id:int,round_id:int,payload:Revision,request:Request,user:dict=Depends(get_current_teacher)):
    _context(offering_id,round_id,user,request)
    with get_db_connection() as conn:
        result=auto_sync.retry_auto_sync(conn,user,round_id,payload.expected_revision);conn.commit();return result


@router.post("/auto-sync/reauthorize")
async def reauthorize_auto_sync(offering_id:int,round_id:int,payload:Revision,request:Request,user:dict=Depends(get_current_teacher)):
    _context(offering_id,round_id,user,request)
    return await _call(auto_sync.reauthorize_auto_sync(user,round_id,payload.expected_revision))
