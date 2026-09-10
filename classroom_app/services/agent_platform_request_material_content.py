"""Bounded normal text source editing, including registered LessonDoc saves.

Response capacity stays within the B/MCP ledger budget. Larger sources receive
413 and require the file workflow. The model supplies the two read revisions;
the normal LessonDoc idempotency key is derived from the B operation UUID.
"""


def build_capabilities(RequestCapability, spec, ID):
    path = {'material_id': ID}
    bound = {'max_response_bytes': spec('integer', required=True, minimum=131072, maximum=131072)}
    revision = spec('string', required=True, minLength=1, maxLength=128)
    return (
        RequestCapability('http.materials.content.read', '读取可访问材料源码及保存版本（响应上限128KiB）', 'GET',
            '/api/materials/{material_id}/content', 'materials_parts.library', 'get_material_content',
            'e6e93bcac1210561876346490c00a79a92c0fb63ed5c9075a686db2e1bbd65de', {'path': path, 'query': bound}, mutates=False),
        RequestCapability('http.materials.content.save', '按读取版本保存自己或有管理权材料的源码（响应上限128KiB）', 'PUT',
            '/api/materials/{material_id}/content', 'materials_parts.library', 'update_material_content',
            '8ea9f648abec92b12515c43a051ad843dbebb6b81f98e0a151e5440cfc13ef8d', {'path': path, 'query': bound, 'body': {
                'content': spec('string', required=True, minLength=0, maxLength=24576, allowNewlines=True),
                'encoding': spec('string', required=True, enum=['utf-8-sig', 'utf-8', 'utf-16', 'utf-16-le', 'utf-16-be', 'gb18030', 'gbk'], maxLength=12),
                'revision': revision, 'source_revision': revision,
            }}, max_body_bytes=98304, server_operation_id_field='operation_id'),
    )
