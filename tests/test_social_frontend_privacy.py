"""社交前端隐私守卫 (docs/social-friends-groups-2026-10-27.md §12.2, §12.4).

Static source checks only (no app, HTTP or database):
  * no social/info-panel frontend file ever renders the word 侧写 or explains
    how matching works — 推荐 shows only the server hint and one neutral line;
  * the 个人信息面板 trigger is delegated (no MutationObserver scanning) and
    refuses blog avatars / data-user-card="off" subtrees;
  * the blog author popover offers the panel only for real-name kinds.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = '侧写'
SOCIAL_JS = ROOT / 'static/js/social'
INFO_PANEL_FILES = (
    ROOT / 'static/js/ability_radar.js',
    ROOT / 'static/css/lq/pages/social.css',
)


def frontend_files():
    files = sorted(SOCIAL_JS.rglob('*.js'))
    files += sorted((ROOT / 'templates/partials').glob('social_*'))
    files += [path for path in INFO_PANEL_FILES if path.exists()]
    return files


class SocialFrontendPrivacyTests(unittest.TestCase):
    def test_files_are_found(self):
        names = {path.name for path in frontend_files()}
        for expected in ('user_card.js', 'user_card_trigger.js', 'friends_discover.js', 'social_entry.html', 'ability_radar.js'):
            self.assertIn(expected, names)

    def test_no_file_mentions_the_forbidden_word(self):
        for path in frontend_files():
            with self.subTest(path=str(path.relative_to(ROOT))):
                self.assertNotIn(FORBIDDEN, path.read_text(encoding='utf-8'))

    def test_recommendations_use_only_the_neutral_line(self):
        source = (SOCIAL_JS / 'friends_discover.js').read_text(encoding='utf-8')
        self.assertIn("NEUTRAL_LINE = '每天根据大家的学习与互动特点更新'", source)
        for word in ('画像', '心理', '性格', '人格', '职业测试', '特质'):
            self.assertNotIn(word, source, word)

    def test_trigger_is_delegated_and_respects_opt_outs(self):
        source = (SOCIAL_JS / 'user_card_trigger.js').read_text(encoding='utf-8')
        self.assertNotIn('new MutationObserver', source)
        self.assertIn('[data-user-card="off"]', source)
        self.assertIn('[data-blog-avatar]', source)
        client = (SOCIAL_JS / 'social_client.js').read_text(encoding='utf-8')
        self.assertIn('bindUserCardTriggers(document', client)
        self.assertNotIn("import('./user_card.js')", client)  # the panel itself stays lazy

    def test_blog_popover_offers_the_panel_only_to_real_name_kinds(self):
        blog = (ROOT / 'static/js/blog.js').read_text(encoding='utf-8')
        match = re.search(r"const cardIdentity = \[([^\]]*)\]\.includes\(user\?\.kind\)", blog)
        self.assertIsNotNone(match)
        kinds = set(re.findall(r"'([a-z_]+)'", match.group(1)))
        self.assertEqual({'real', 'wanderer'}, kinds)
        self.assertEqual(1, blog.count('data-user-card='))

    def test_classroom_discussion_rows_opt_out(self):
        chat = (ROOT / 'static/js/chat.js').read_text(encoding='utf-8')
        self.assertIn("row.dataset.userCard = 'off'", chat)


if __name__ == '__main__':
    unittest.main()
