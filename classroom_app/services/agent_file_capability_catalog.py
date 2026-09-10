"""One definition for MCP file tools and the on-demand capability catalogue."""
from copy import deepcopy


_SELECTORS = {
    "material_id": {"type": "integer", "minimum": 1},
    "submission_file_id": {"type": "integer", "minimum": 1},
    "collaboration_file_id": {"type": "integer", "minimum": 1},
    "course_file_id": {"type": "integer", "minimum": 1},
    "revision": {"type": "string"}, "path": {"type": "string"},
    "parent_task_id": {"type": "integer", "minimum": 1},
}
_TRANSPORTS = (
    ("platform_file", "授权文件文本或文档抽取", "authorized_file_snapshot",
     "读取按原下载权限授权的材料、作业附件、小组文件、课程共享文件或当前任务文件（五选一），返回文本或文档抽取及SHA256。历史任务文件须显式parent_task_id；不返回二进制base64。"),
    ("platform_download", "授权文件字节复制到本任务", "authorized_binary_task_input",
     "按当前用户原下载权限将文件字节复制到当前任务inputs目录，返回路径、大小和SHA256；用于直接处理Word/Excel/PDF/图片，二进制不进入模型上下文。五选一；历史文件需parent_task_id。单文件32MiB、inputs总计64MiB/128项，编辑请另存输出。"),
)


def file_transport_catalog() -> list[dict]:
    return [{"key": key, "tool": key, "title": title, "domain": "files",
             "status": "reviewed_file_transport", "guarantee": guarantee,
             "description": description, "roles": ["teacher", "student"],
             "limitations": "五种来源恰选一种；历史任务必须是同一主体的合法父链；每次执行重新验证当前用户原下载权限。不是任意主机文件访问、平台文件修改或删除能力。",
             "parameters": {"type": "object", "properties": deepcopy(_SELECTORS),
                            "required": [], "additionalProperties": False}}
            for key, title, guarantee, description in _TRANSPORTS]


def file_transport_tools() -> list[dict]:
    return [{"name": item["key"], "description": item["description"], "inputSchema": item["parameters"]}
            for item in file_transport_catalog()]
