"""Reviewed blog lifecycle, using the ordinary author/visibility/moderator rules.

Post detail records reading activity, so it is a B write admission. Editing and
deletion require the version returned by that detail, never a guessed revision.
The platform has no comment-edit endpoint; this factory does not invent one.
"""


def build_capabilities(RequestCapability, spec, ID):
    revision = spec('string', required=True, maxLength=100)
    return (
        RequestCapability('http.blog.post.detail', '阅读可见帖子及其当前版本', 'GET', '/api/blog/posts/{post_id}',
            'blog', 'api_get_post', '4770e54538f4f57fa2c8857c4ae5c60fbdebbed2b022f4a237873d15bafa8e3e', {'path': {'post_id': ID}}),
        RequestCapability('http.blog.post.update', '按当前版本修改自己的帖子或草稿', 'PUT', '/api/blog/posts/{post_id}',
            'blog', 'api_update_post', 'a52c29734b608ebe3eedf40881b1058a162ecb1d3e9da2477ee9fb0d4d65eab4', {'path': {'post_id': ID}, 'body': {
                'expected_updated_at': revision,
                'title': spec('string', maxLength=200),
                'content_md': spec('string', maxLength=50000, allowNewlines=True),
                'section_key': spec('string', maxLength=80),
                'author_display_mode': spec('string', enum=['real_name','nickname','anonymous'], maxLength=20),
                'visibility': spec('string', enum=['public','class_visible','selected_users'], maxLength=20),
                'visible_class_id': {**ID, 'required': False},
                'visible_user_identities': spec('strings', minItems=0, maxItems=40, maxLength=80),
                'allow_comments': spec('boolean'),
                'tags': spec('strings', minItems=0, maxItems=5, maxLength=40),
                'status': spec('string', enum=['draft','published'], maxLength=15),
            }}, max_body_bytes=220000),
        RequestCapability('http.blog.post.delete', '按已核对版本删除自己的帖子', 'DELETE', '/api/blog/posts/{post_id}',
            'blog', 'api_delete_post', 'ff0a8e0d86b794d307d43080b4b9b1bd3a000cb1920aa6e4cd35de3a2fb155e8', {'path': {'post_id': ID}, 'query': {'expected_updated_at': revision}}),
        RequestCapability('http.blog.comment.delete', '按本人、帖子作者或管理员权限删除评论及回复', 'DELETE', '/api/blog/comments/{comment_id}',
            'blog', 'api_delete_comment', '53599e0710d51a9d79a6ad46a60db01452da02865f7cb86ba497842e1ed41ea1', {'path': {'comment_id': ID}}),
        RequestCapability('http.blog.post.like', '切换自己对可见帖子的点赞', 'POST', '/api/blog/posts/{post_id}/like',
            'blog', 'api_like_post', '6b2215eb1b92d473510bb93aa49dc92533f92ef2a2b3fdb9196f558549784464', {'path': {'post_id': ID}}),
        RequestCapability('http.blog.comment.like', '切换自己对可见评论的点赞', 'POST', '/api/blog/comments/{comment_id}/like',
            'blog', 'api_like_comment', 'cbefc5507b90c873f374494bf34207fbd049314f0f6c6650a55b7ca96ab211f2', {'path': {'comment_id': ID}}),
        RequestCapability('http.blog.report.create', '举报自己可见的帖子、评论或机会信息', 'POST', '/api/blog/reports',
            'blog', 'api_create_blog_report', 'b1d60fafb99c99f294d646139af0e952f6f0709188b54729bb54f648a6d6e755', {'body': {
                'target_type': spec('string', required=True, enum=['post','comment','opportunity'], maxLength=20), 'target_id': ID,
                'reason_code': spec('string', required=True, enum=['false_information','spam','abuse','job_scam','privacy','other'], maxLength=30),
                'details': spec('string', minLength=0, maxLength=2000, allowNewlines=True),
            }}),
        RequestCapability('http.blog.report.resolve', '管理员记录待处理举报的核查结论', 'POST', '/api/blog/reports/{report_id}/resolve',
            'blog', 'api_resolve_blog_report', '338a5b71d61b341e6e8824facef398f4281a2ceeb3522cd62ca405b020d3145f', {'path': {'report_id': ID}, 'body': {
                'status': spec('string', required=True, enum=['resolved','dismissed'], maxLength=15),
                'notes': spec('string', minLength=0, maxLength=2000, allowNewlines=True),
            }}, response_contract='blog_report_resolution'),
    )
