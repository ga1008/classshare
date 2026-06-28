"""Student resume console (简历管理与优化) service package.

See ``schema_resume`` for the data model and ``routers/resume_console`` for the
HTTP surface. Modules:

* ``resume_nav_service``        — left-rail navigation registry.
* ``resume_profile_service``    — personal info + list-section CRUD + validation.
* ``resume_attachment_service`` — image attachments for cert/skill/experience.
* ``resume_ai_service``         — synchronous AI helpers (optimize / suggest / tech stack).
* ``resume_generation_service`` — background AI jobs (self-intro / education / render).
* ``resume_render_service``     — résumé HTML assembly + docx/pdf export.
"""
