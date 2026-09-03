export class ApiError extends Error {
    constructor(message, status, details = {}) { super(message); this.status = status; this.details = details; }
}
export async function request(url, options = {}) {
    let response;
    try { response = await fetch(url, { credentials: 'same-origin', ...options }); }
    catch { throw new ApiError('网络连接中断。已保留本地修改，可重试保存。', 0); }
    let body;
    try { body = await response.json(); } catch { throw new ApiError('服务器没有返回有效数据，请检查登录状态后重试。', response.status); }
    if (!response.ok || body.status === 'error') throw new ApiError(body.error?.message || body.detail || '操作失败', response.status, body.error || {});
    return body.result ?? body;
}
export const jsonRequest = (url, method, value) => request(url, {method, headers:{'Content-Type':'application/json'},body:JSON.stringify(value)});
export function createApi(pack, lesson) {
    const base='/api/lessondoc/editor/packs/'+pack, query='?lesson_no='+lesson;
    return {
        base, query, previewUrl:'/materials/lessondoc-editor/'+pack+'/preview?lesson='+lesson, load:()=>request(base+'/document'+query),
        save:(attempt)=>jsonRequest(base+'/document'+query,'PUT',attempt),
        validate:(document)=>jsonRequest(base+'/validate'+query,'POST',{document}),
        history:()=>request(base+'/revisions'+query),
        revision:(id)=>request(base+'/revisions/'+id+query),
        restore:(id,revision,operation_id)=>jsonRequest(base+'/revisions/'+id+'/restore'+query,'POST',{revision,operation_id}),
    };
}
