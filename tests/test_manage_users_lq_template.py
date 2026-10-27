"""Render the real user-management template; no app, credentials or database.

These contracts cover the form/layer/controller boundaries left outside the
generic manage-shell tests. Interactive drafts, focus and permissions remain
covered by manage-users.spec.ts on the synthetic HTTP runtime.
"""
from collections import Counter
from html.parser import HTMLParser
import json
from pathlib import Path
import unittest

from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

from classroom_app.lq import lq_props
from classroom_app.services.manage_nav_service import build_manage_nav
from tools.ui.lint_lq import violations


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = "templates/manage/system/users.html"
CONTROLLER = "static/js/manage_users.js"


class UserPage(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.tags, self.fields, self.forms, self.scripts = [], [], [], []
        self.form_stack, self.nested_forms = [], []
        self.script = None
        self.feed(markup)

    def handle_starttag(self, tag, values):
        attrs = dict(values)
        self.tags.append((tag, attrs))
        if tag == "form":
            if self.form_stack:
                self.nested_forms.append((self.form_stack[-1], attrs.get("id")))
            self.form_stack.append(attrs.get("id"))
            self.forms.append(attrs.get("id"))
        if tag in {"input", "select", "textarea", "button"}:
            owner = attrs.get("form") or (self.form_stack[-1] if self.form_stack else None)
            self.fields.append((tag, attrs, owner))
        if tag == "script":
            self.script = {"attrs": attrs, "text": ""}

    def handle_endtag(self, tag):
        if tag == "form" and self.form_stack:
            self.form_stack.pop()
        if tag == "script" and self.script is not None:
            self.scripts.append(self.script)
            self.script = None

    def handle_data(self, data):
        if self.script is not None:
            self.script["text"] += data

    def by_id(self, identity):
        return next(attrs for _, attrs in self.tags if attrs.get("id") == identity)


class ManageUsersLqTemplateTests(unittest.TestCase):
    def setUp(self):
        partials = {f"partials/{name}.html": "" for name in (
            "lq_editor_head", "ai_workspace_widget", "vite_islands", "feedback_modal",
            "teacher_onboarding_modal", "markdown_assets",
        )}
        self.env = Environment(loader=ChoiceLoader([
            DictLoader(partials), FileSystemLoader(ROOT / "templates"),
        ]), autoescape=True)
        self.env.globals.update(lq_props=lq_props, asset_url=lambda path: "/static/" + path)
        self.user = {"id": 73, "role": "teacher", "name": "合成超管", "email": "admin@example.invalid"}
        self.teachers = [{"id": 74, "name": "合成教师", "email": "teacher@example.invalid",
                          "is_active": True, "is_super_admin": False}]

    def render_page(self, *, pilot=True, embedded=False):
        markup = self.env.get_template("manage/system/users.html").render(
            lq_pilot_enabled=pilot, embedded_mode=embedded, page_title="用户管理", active_page="users",
            user_info=self.user, manage_nav=build_manage_nav(self.user, "users", is_super_admin=True),
            lq_family_enabled=lambda key: True,
            teacher_accounts=self.teachers, initial_super_admin_name="合成初始超管",
            teacher_password_hint="至少 8 位", teacher_account_summary={
                "total_count": 1, "active_count": 1, "super_admin_count": 0,
                "inactive_count": 0, "cancelled_count": 0,
            },
        )
        return markup, UserPage(markup)

    def test_real_forms_keep_native_constraints_labels_and_external_submit_owners(self):
        _, page = self.render_page()
        forms = {"teacher-create-form", "teacher-edit-form", "teacher-membership-form", "teacher-password-form"}
        self.assertTrue(forms.issubset(page.forms))
        self.assertFalse(page.nested_forms)
        submits = [(attrs, owner) for tag, attrs, owner in page.fields
                   if tag == "button" and attrs.get("type") == "submit" and owner in forms]
        self.assertEqual(Counter(owner for _, owner in submits), Counter({name: 1 for name in forms}))
        for attrs, owner in submits:
            self.assertEqual(attrs.get("form"), owner)
            self.assertEqual(attrs.get("data-lq-component"), "button")
        ids = [attrs["id"] for _, attrs in page.tags if attrs.get("id")]
        self.assertEqual(len(ids), len(set(ids)))
        labelled = {attrs["for"] for tag, attrs in page.tags if tag == "label" and attrs.get("for")}
        for tag, attrs, owner in page.fields:
            if owner not in forms or tag == "button" or attrs.get("type") in {"hidden", "checkbox"}:
                continue
            self.assertIn(attrs["id"], labelled)
            for target in attrs.get("aria-describedby", "").split():
                self.assertIn(target, ids)
        for prefix in ("create", "edit", "membership"):
            number = page.by_id(f"{prefix}-employee-number")
            self.assertIn("required", number)
            self.assertEqual(number["type"], "text")  # Leading zeroes remain an identity, not a number.
            self.assertEqual(number["maxlength"], "64")
            self.assertEqual(page.by_id(f"{prefix}-school-code")["type"], "hidden")
        for identity in ("create-password", "reset-password"):
            self.assertEqual(page.by_id(identity)["minlength"], "8")
            self.assertEqual(page.by_id(identity)["autocomplete"], "new-password")
        self.assertIn("disabled", page.by_id("teacher-edit-fields"))
        revision_owners = {owner for _, attrs, owner in page.fields if attrs.get("name") == "expected_revision"}
        self.assertEqual(revision_owners, forms - {"teacher-create-form"})

    def test_each_dialog_has_a_hidden_root_named_surface_and_non_submitting_close(self):
        _, page = self.render_page()
        for identity in ("teacher-create-modal", "teacher-detail-modal", "teacher-membership-modal", "teacher-password-modal"):
            with self.subTest(dialog=identity):
                root = page.by_id(identity)
                self.assertIn("hidden", root)
                self.assertEqual(root["data-lq-dialog"], "modal")
                surfaces = [attrs for _, attrs in page.tags if attrs.get("role") == "dialog"
                            and attrs.get("aria-labelledby") == identity + "-title"]
                self.assertEqual(len(surfaces), 1)
                self.assertIn("lq-dialog__surface", surfaces[0]["class"].split())
                page.by_id(identity + "-title")
                close_buttons = [attrs for tag, attrs, _ in page.fields if tag == "button"
                                 and attrs.get("data-close-modal") == identity]
                self.assertEqual(len(close_buttons), 2)
                self.assertTrue(all(attrs.get("type") == "button" for attrs in close_buttons))
        self.assertEqual(page.by_id("teacher-search")["type"], "search")
        self.assertIn("data-lq-dropdown", page.by_id("teacher-status-filter"))

    def test_shell_modes_keep_one_external_controller_and_inert_escaped_data(self):
        injected = '</script><img src=x onerror="alert(1)">'
        self.teachers[0]["name"] = injected
        for pilot, embedded in ((False, False), (True, False), (True, True)):
            with self.subTest(pilot=pilot, embedded=embedded):
                _, page = self.render_page(pilot=pilot, embedded=embedded)
                controllers = [script for script in page.scripts if script["attrs"].get("src") == "/static/js/manage_users.js"]
                self.assertEqual(len(controllers), 1)
                self.assertEqual(controllers[0]["attrs"]["type"], "module")
                data = [script for script in page.scripts if script["attrs"].get("id") == "teacher-management-data"]
                self.assertEqual(len(data), 1)
                self.assertEqual(data[0]["attrs"]["type"], "application/json")
                self.assertEqual(json.loads(data[0]["text"])["teachers"][0]["name"], injected)
                self.assertFalse(any(attrs.get("onerror") for _, attrs in page.tags))
                self.assertFalse(any("teacher-create-form" in script["text"] for script in page.scripts
                                     if script["attrs"].get("type") != "application/json"))

    def test_reviewed_domain_sources_have_no_lq_source_violations(self):
        for path in (TEMPLATE, CONTROLLER, "static/js/organization_suggestions.js"):
            with self.subTest(path=path):
                self.assertEqual(violations(path, (ROOT / path).read_text(encoding="utf-8")), [])


if __name__ == "__main__":
    unittest.main()
