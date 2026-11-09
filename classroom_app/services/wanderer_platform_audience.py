"""Reviewed content destinations, separate from the ordinary route's ACL.

This metadata never grants permission or implements a business operation.
Exact method/path pairs deliberately exclude future sharing endpoints. Resume
"publish" currently renders a student's own document; it sends nothing to an
employer and does not change its audience. Public profile/mood is not included.
"""

OWN_PRIVATE_WRITES = {
    'resume_console': {
        ('POST', '/api/resume/personal'),
        ('POST', '/api/resume/personal/suggest'),
        ('POST', '/api/resume/personal/avatar'),
        ('POST', '/api/resume/job-targets/analyze'),
        ('DELETE', '/api/resume/job-targets/{target_id}'),
        ('POST', '/api/resume/applications'),
        ('PUT', '/api/resume/applications/{application_id}'),
        ('DELETE', '/api/resume/applications/{application_id}'),
        ('POST', '/api/resume/sections/{section}'),
        ('PUT', '/api/resume/sections/{section}/{item_id}'),
        ('DELETE', '/api/resume/sections/{section}/{item_id}'),
        ('POST', '/api/resume/self-intro/optimize'),
        ('POST', '/api/resume/self-intro/generate'),
        ('POST', '/api/resume/education/seed'),
        ('POST', '/api/resume/suggestions/jobs/{job_id}/{action}'),
        ('POST', '/api/resume/self-intro/{intro_id}/job/{action}'),
        ('POST', '/api/resume/attachments'),
        ('DELETE', '/api/resume/attachments/{attachment_id}'),
        ('POST', '/api/resume/builder/validate'),
        ('POST', '/api/resume/resumes'),
        ('PUT', '/api/resume/resumes/{resume_id}'),
        ('POST', '/api/resume/resumes/{resume_id}/publish'),
        ('POST', '/api/resume/resumes/{resume_id}/optimize'),
        ('POST', '/api/resume/import'),
        ('POST', '/api/resume/resumes/{resume_id}/candidates/{candidate_id}/accept'),
        ('POST', '/api/resume/resumes/{resume_id}/candidates/{candidate_id}/reject'),
        ('POST', '/api/resume/resumes/{resume_id}/versions/{version_revision}/restore'),
        ('POST', '/api/resume/resumes/{resume_id}/job/{action}'),
        ('POST', '/api/resume/resumes/{resume_id}/import-conflicts/{conflict_index}/accept'),
        ('DELETE', '/api/resume/resumes/{resume_id}'),
    },
    'career_path': {
        ('POST', '/api/career-path/initialize'),
        ('POST', '/api/career-path/answers'),
        ('POST', '/api/career-path/progress'),
        ('POST', '/api/career-path/retry'),
        ('POST', '/api/career-path/cancel'),
        ('POST', '/api/career-path/reset'),
        ('POST', '/api/career-path/preferences'),
        ('POST', '/api/career-path/feedback'),
        ('POST', '/api/career-path/keywords'),
        ('POST', '/api/career-tools/events'),
        ('POST', '/api/career-path/job-postings/{posting_id}/target'),
    },
}


def content_audience(operation):
    module = str(getattr(operation, 'module', ''))
    if module.startswith('classroom_app.routers.') and (
            operation.method, operation.path) in OWN_PRIVATE_WRITES.get(module.rsplit('.', 1)[-1], ()):
        return 'own_private'
    return 'resource_dependent_or_unknown'
