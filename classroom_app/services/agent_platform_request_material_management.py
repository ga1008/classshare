"""Reviewed normal material nodes and bounded uploads; binary bytes stay files."""


def build_capabilities(Capability, spec, ID):
    nullable_id = spec('integer', nullable=True, minimum=1, maximum=2**63-1)
    parent = spec('integer', required=True, nullable=True, minimum=1, maximum=2**63-1)
    name = spec('string', required=True, minLength=1, maxLength=120)
    version = spec('string', required=True, minLength=1, maxLength=80)
    path = {'material_id': ID}
    upload_limit = 32 * 1024 * 1024 + 32768
    return (
        Capability('http.materials.folders.list', '选择本人材料文件夹及其当前版本', 'GET', '/api/materials/folder-options',
            'materials_parts.node_ops', 'list_material_folder_options', 'd885948c367d53e4707b447a926207b67785d579d02b6a389cdf36dc234be801',
            {'query': {'exclude_subtree_of': nullable_id}}, mutates=False),
        Capability('http.materials.folder.create', '在有管理权的普通材料树中创建文件夹', 'POST', '/api/materials/folders',
            'materials_parts.node_ops', 'create_material_folder', '2fc02c87c9f54716f92b5df894528e1b082060e52b7231115007a9f9210f2393', {'body': {'name': name, 'parent_id': parent}}),
        Capability('http.materials.file.create', '在有管理权的指定普通目录创建Markdown文档', 'POST', '/api/materials/files',
            'materials_parts.node_ops', 'create_material_markdown_file', '196ffb441697ec2d6cda310f8de9524dab2b4f7f05c8597cb29abc502d0f8446', {'body': {'name': name, 'parent_id': parent,
                'content': spec('string', required=True, maxLength=24576, allowNewlines=True)}}, max_body_bytes=98304),
        Capability('http.materials.move', '按源节点和目标目录的读取版本移动普通材料', 'POST', '/api/materials/{material_id}/move',
            'materials_parts.node_ops', 'move_material_node', '22f1c7c15eae25fd36cb8d4718ec29429cf69902fd3b7bacebc08414352355bc', {'path': path, 'body': {
                'target_parent_id': parent, 'expected_updated_at': version, 'expected_target_updated_at': version}}),
        Capability('http.materials.delete.impact', '读取材料删除影响及完整对象快照确认令牌', 'GET', '/api/materials/{material_id}/delete-impact',
            'materials_parts.library', 'get_material_delete_impact', '0cb9450fb6e1404573de916b7e19899b616d7d3fe96b015eb39a7328a9058a80', {'path': path}, mutates=False),
        Capability('http.materials.delete', '按已确认的当前影响令牌删除材料，明确是否解除关联', 'DELETE', '/api/materials/{material_id}',
            'materials_parts.library', 'delete_material', '281104cc73c68e76d860c032022c5dc6bdbf0a1e3fde1776b2b543dacfb1effb', {'path': path, 'query': {
                'unlink_references': spec('boolean', required=True),
                'impact_token': spec('string', required=True, minLength=64, maxLength=64)}}),
        Capability('http.materials.upload', '上传任务文件到有管理权的普通材料目录（最多16个，共32MiB，暂不导入ZIP）', 'POST', '/api/materials/upload',
            'materials_parts.library', 'upload_materials', '9d4e123ae71f5a3f7267a26fe8d3b3bc514506620eaf0d2274fdfc94bda5274d', {'body': {'parent_id': parent}},
            transport='form', allows_files=True, max_body_bytes=upload_limit, forbidden_file_suffixes=('.zip',)),
        Capability('http.course.file.upload', '上传一个课程文件，明确公开与教师资源标志并取得文件编号', 'POST', '/api/courses/{course_id}/files/upload',
            'files', 'upload_course_file', '1c4b206fce932b40916a394bd159aa3921cc4eb31fa687d878eeec772928f11b', {'path': {'course_id': ID}, 'body': {
                'is_public': spec('boolean', required=True), 'is_teacher_resource': spec('boolean', required=True)}},
            transport='form', allows_files=True, max_body_bytes=upload_limit, file_field_name='file', max_files=1),
        Capability('http.collaboration.file.upload', '上传一个组内文件并返回文件编号，仅当前成员或课堂教师', 'POST', '/api/collaboration/groups/{group_id}/files',
            'collaboration', 'upload_study_group_file', '618faf5924c395d2c9f21878eb69e01b6aaa9ddb1396d7cd88038ff5ae05210a', {'path': {'group_id': ID}, 'body': {
                'description': spec('string', required=True, maxLength=500, allowNewlines=True)}},
            transport='form', allows_files=True, max_body_bytes=upload_limit, file_field_name='file', max_files=1),
    )
