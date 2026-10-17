"""Real Chromium against intercepted synthetic pages. Never connects to Naver."""
import importlib.util
import os
from pathlib import Path
import shutil
import tempfile
import unittest
import sqlite3
import base64
import re
from unittest.mock import patch

from homepan import cli as core
from homepan import browser as b
from tests.test_pipeline import fixture

AVAILABLE = all(importlib.util.find_spec(name) for name in ('playwright', 'markdown_it'))
EDITOR = '''<!doctype html><html lang="ko"><meta charset="utf-8">
<style>body {padding-top:60px} #content {border:1px solid;min-height:120px} #body {min-height:100px} img {width:80px;height:80px}</style>
<input id="title" aria-label="제목">
<button id="photo" onclick="document.querySelector('#file').click()">사진</button>
<input id="file" type="file" accept="image/*" hidden>
<section id="content"><div id="body" contenteditable="true"></div></section>
<button id="publish" onclick="window.publishClicks++;history.pushState({},'', '/fixture_owner/12345')">최종 발행</button>
<script>
window.publishClicks=0;
document.querySelector('#file').addEventListener('change', e => {
 const content=document.querySelector('#body');
 const image=document.createElement('img');image.src=URL.createObjectURL(e.target.files[0]);
 content.append(image);
 const p=document.createElement('p');p.append(document.createElement('br'));content.append(p);
 content.focus();const range=document.createRange();range.selectNodeContents(p);range.collapse(false);
 const selection=getSelection();selection.removeAllRanges();selection.addRange(range);
 e.target.value='';
});
</script></html>'''


def binding(page, selector):
    result = page.locator(selector).evaluate("e=>({tag:e.tagName.toLowerCase(),role:e.getAttribute('role')||'',editable:e.isContentEditable,type:e.getAttribute('type')||''})")
    return {**result, 'selector': selector, 'frame': b.frame_key(page.main_frame)}


@unittest.skipUnless(AVAILABLE, 'Install browser extras to execute actual Chromium tests')
class BrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.p = sync_playwright().start()
        executable = os.environ.get('HOMEPAN_TEST_CHROMIUM') or shutil.which('chromium')
        cls.browser = cls.p.chromium.launch(headless=True, **({'executable_path': executable} if executable else {}))
        cls.temp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.temp.name)
        cls.original = cls.base / 'fixture'
        fixture(cls.original)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.p.stop()
        cls.temp.cleanup()

    def setUp(self):
        self.root = self.base / self._testMethodName
        shutil.copytree(self.original, self.root)
        for stage in core.STAGES[:5]:
            core.advance(self.root, stage)
        core.editor_plan(self.root)
        self.plan = core.read(self.root / 'editor_plan.json')
        self.context = self.browser.new_context()
        self.context.route('**/*', lambda route: route.fulfill(status=200, content_type='text/html', body=EDITOR)
                           if route.request.url.startswith('https://blog.naver.com/') else route.abort())
        self.context.grant_permissions(['clipboard-write'], origin='https://blog.naver.com')
        self.page = self.context.new_page()
        self.page.goto('https://blog.naver.com/PostWriteForm.naver?blogId=fixture_owner')
        self.fields = {name: binding(self.page, selector) for name, selector in
                       [('title', '#title'), ('body', '#body'), ('content', '#content'), ('photo_button', '#photo')]}
        self.guard = b.Guard(self.page, 'fixture_owner')

    def tearDown(self):
        self.context.close()

    def write(self):
        b.Writer(self.page, self.fields, self.root, self.guard, prompt=lambda *a: self.fail('Unexpected caret prompt')).write(self.plan)

    def test_real_clipboard_upload_and_readback(self):
        self.write()
        self.assertEqual(self.page.locator('#title').input_value(), self.plan['title'])
        self.assertEqual(self.page.locator('#content img').count(), 8)
        self.assertEqual(self.page.locator('#content h2').count(), 8)
        b.compare_content(b.snapshot(self.page.locator('#content')), b.expected_content(self.plan))
        self.assertEqual(core.read(self.root / 'browser_progress.json')['status'], 'entered_needs_review')
        self.assertEqual(self.page.evaluate('window.publishClicks'), 0)

    def test_rich_table_and_html_is_not_executed(self):
        markup, plain = b.render_markdown('## 표\n\n|항목|값|\n|---|---|\n|금액|100원|\n\n<script>window.pwned=1</script>')
        self.page.locator('#body').click()
        b.paste_html(self.page, markup, plain)
        self.assertEqual(self.page.locator('#body table').count(), 1)
        self.assertIsNone(self.page.evaluate('window.pwned'))
        self.assertEqual(b.normalized(b.snapshot(self.page.locator('#body'))['text']), b.normalized(plain))

    def test_emoji_image_offset_uses_unicode_characters(self):
        self.page.locator('#body').evaluate("e=>e.innerHTML='💡한글<img src=\"data:image/png;base64,\">뒤'")
        self.assertEqual(b.snapshot(self.page.locator('#body'))['image_offsets'], [3])

    def test_existing_title_is_preserved(self):
        self.page.locator('#title').fill('사용자의 기존 글')
        with self.assertRaisesRegex(core.GateError, '덮어쓰지'):
            self.write()
        self.assertEqual(self.page.locator('#title').input_value(), '사용자의 기존 글')
        self.assertFalse((self.root / 'browser_progress.json').exists())

    def test_security_challenge_stops_before_input(self):
        self.page.evaluate("document.body.insertAdjacentHTML('beforeend','<input id=captcha>')")
        with self.assertRaisesRegex(core.GateError, '보안 인증'):
            self.write()
        self.assertEqual(self.page.locator('#title').input_value(), '')

    def test_wrong_blog_target_stops_before_input(self):
        self.page.evaluate("history.replaceState({},'', '/PostWriteForm.naver?blogId=other_owner')")
        with self.assertRaisesRegex(core.GateError, '블로그 ID'):
            self.write()
        self.assertEqual(self.page.locator('#title').input_value(), '')

    def test_changed_control_fingerprint_stops(self):
        self.page.locator('#title').evaluate("e=>e.setAttribute('type','password')")
        with self.assertRaises(core.GateError):
            b.locator_for(self.page, self.fields['title'])

    def test_no_replay_after_successful_input(self):
        self.write()
        with self.assertRaisesRegex(core.GateError, '입력 시도 기록'):
            self.write()
        self.assertEqual(self.page.locator('#content img').count(), 8)

    def test_partial_failure_keeps_journal(self):
        self.page.locator('#photo').evaluate("e=>e.remove()")
        with self.assertRaises(core.GateError):
            self.write()
        self.assertEqual(core.read(self.root / 'browser_progress.json')['status'], 'interrupted_check_existing_draft')
        self.assertTrue(self.page.locator('#body').inner_text())

    def test_picker_does_not_press_publish(self):
        self.page.evaluate(b.PICKER, {'label': '발행 선택 테스트', 'kind': 'button'})
        self.page.locator('#publish').hover()
        self.page.locator('#publish').click()
        result = self.page.evaluate('window.__homepanPicked')
        self.assertEqual(result['selector'], '#publish')
        self.assertEqual(self.page.evaluate('window.publishClicks'), 0)

    def test_changed_body_blocks_review(self):
        self.write()
        self.page.locator('#body').evaluate("e=>e.append('임의 변경')")
        with self.assertRaisesRegex(core.GateError, '본문 읽기'):
            b.review(self.root, self.page, self.fields, self.plan, self.guard, prompt=lambda *a: None)
        self.assertNotIn('editor', core.read(self.root / 'run.json')['completed'])

    def test_review_is_repeatable_without_changing_receipt_hash(self):
        self.write()
        for _ in range(2):
            b.review(self.root, self.page, self.fields, self.plan, self.guard, prompt=lambda *a: None)
        self.assertIn('editor', core.state_check(self.root)['completed'])

    def test_duplicate_publish_does_not_click_again(self):
        self.write()
        b.review(self.root, self.page, self.fields, self.plan, self.guard, prompt=lambda *a: None)
        database = self.root / 'ledger.sqlite3'
        core.ledger_action(self.root, database, 'reserve')
        with patch.object(b, 'pick', return_value=binding(self.page, '#publish')):
            with self.assertRaisesRegex(core.GateError, '이미 예약'):
                b.publish(self.root, self.page, self.fields, self.plan, self.guard, database, 'fixture_owner', prompt=lambda *a: None)
        self.assertEqual(self.page.evaluate('window.publishClicks'), 0)

    def test_old_post_tab_is_not_a_new_publication(self):
        old = self.context.new_page()
        old.goto('https://blog.naver.com/fixture_owner/999')
        previous_pages = list(self.context.pages)
        previous_urls = b.post_urls(previous_pages, 'fixture_owner')
        with self.assertRaisesRegex(core.GateError, '발행 결과 URL'):
            b.wait_post(self.page, 'fixture_owner', previous_urls, previous_pages, timeout=200)
        self.page.locator('#publish').click()
        self.assertEqual(b.wait_post(self.page, 'fixture_owner', previous_urls, previous_pages), 'https://blog.naver.com/fixture_owner/12345')

    def test_single_click_success_records_published(self):
        self.write()
        b.review(self.root, self.page, self.fields, self.plan, self.guard, prompt=lambda *a: None)
        database = self.root / 'ledger.sqlite3'
        with patch.object(b, 'pick', return_value=binding(self.page, '#publish')), patch.object(b, 'verify_public') as verify:
            result = b.publish(self.root, self.page, self.fields, self.plan, self.guard, database, 'fixture_owner', prompt=lambda *a: None)
        self.assertEqual(result, 'https://blog.naver.com/fixture_owner/12345')
        verify.assert_called_once()
        self.assertEqual(self.page.evaluate('window.publishClicks'), 1)
        self.assertIn('publish', core.state_check(self.root)['completed'])

    def test_public_verification_failure_keeps_pending_no_retry(self):
        self.write()
        b.review(self.root, self.page, self.fields, self.plan, self.guard, prompt=lambda *a: None)
        database = self.root / 'ledger.sqlite3'
        with patch.object(b, 'pick', return_value=binding(self.page, '#publish')), patch.object(b, 'verify_public', side_effect=core.GateError('public unavailable')):
            with self.assertRaisesRegex(core.GateError, 'public unavailable'):
                b.publish(self.root, self.page, self.fields, self.plan, self.guard, database, 'fixture_owner', prompt=lambda *a: None)
        self.assertEqual(self.page.evaluate('window.publishClicks'), 1)
        with sqlite3.connect(database) as db:
            self.assertEqual(db.execute('SELECT status FROM posts').fetchone()[0], 'pending')
        self.assertNotIn('publish', core.state_check(self.root)['completed'])

    def test_public_check_uses_fresh_context_without_cookies(self):
        self.write()
        markup = self.page.locator('#content').inner_html()
        sources = iter('data:image/png;base64,' + base64.b64encode((self.root / f'images/{i}.png').read_bytes()).decode() for i in range(1, 9))
        markup = re.sub(r'src="blob:[^"]+"', lambda _: 'src="' + next(sources) + '"', markup)
        page_html = '<meta charset="utf-8"><h1 id="post-title">' + self.plan['title'] + '</h1><article id="post-content">' + markup + '</article>'
        contexts = []
        original_new_context = self.browser.new_context
        def fresh_context(**kwargs):
            context = original_new_context(**kwargs)
            self.assertEqual(context.cookies(), [])
            context.route('**/*', lambda route: route.fulfill(status=200, content_type='text/html', body=page_html)
                          if route.request.url == 'https://blog.naver.com/fixture_owner/12345' else route.abort())
            contexts.append(context)
            return context
        def choose(page, label, kind='region'):
            return binding(page, '#post-title' if '제목' in label else '#post-content')
        self.context.add_cookies([{'name': 'synthetic_session', 'value': 'test', 'domain': 'blog.naver.com', 'path': '/'}])
        with patch.object(self.browser, 'new_context', side_effect=fresh_context), patch.object(b, 'pick', side_effect=choose):
            b.verify_public(self.browser, 'https://blog.naver.com/fixture_owner/12345', self.plan, prompt=lambda *a: None)
        self.assertEqual(len(contexts), 1)
        self.assertEqual(contexts[0].pages, [])

    def test_broken_public_image_does_not_pass_loaded_check(self):
        from playwright.sync_api import Error
        self.page.locator('#body').evaluate("e=>e.innerHTML='<img src=\"data:image/png;base64,broken\">'")
        with self.assertRaises(Error):
            b.wait_images(self.page.locator('#body'), 1, timeout=100)

    def test_persistent_profile_retains_only_its_own_session(self):
        profile = self.root / 'synthetic-profile'
        executable = os.environ.get('HOMEPAN_TEST_CHROMIUM') or shutil.which('chromium')
        options = {'headless': True, **({'executable_path': executable} if executable else {})}
        context = self.p.chromium.launch_persistent_context(str(profile), **options)
        self.assertIsNotNone(context.browser)
        context.add_cookies([{'name': 'synthetic_session', 'value': 'fixture', 'domain': 'blog.naver.com', 'path': '/', 'expires': 2000000000}])
        context.close()
        context = self.p.chromium.launch_persistent_context(str(profile), **options)
        try:
            self.assertEqual(context.cookies()[0]['name'], 'synthetic_session')
        finally:
            context.close()


class ProfileTests(unittest.TestCase):
    def test_reject_existing_unowned_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'Cookies').write_text('test')
            with self.assertRaisesRegex(core.GateError, '기존 일반 브라우저'):
                b.prepare_state(Path(tmp))

    def test_reject_profile_inside_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / '.git').mkdir()
            (Path(tmp) / '.git/HEAD').write_text('ref: refs/heads/main\n')
            with self.assertRaisesRegex(core.GateError, 'Git 저장소 밖'):
                b.prepare_state(Path(tmp) / 'profile')

    def test_profile_lock_prevents_two_sessions_and_releases(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = b.prepare_state(Path(tmp))
            with b.profile_lock(state):
                with self.assertRaisesRegex(core.GateError, '이미 실행 중'):
                    with b.profile_lock(state):
                        self.fail('Second session acquired a lock')
            with b.profile_lock(state):
                pass

    def test_wrong_origin_is_rejected(self):
        for address in ['https://blog.naver.com.evil.test/', 'http://blog.naver.com/', 'https://blog.naver.com:8080/']:
            with self.assertRaises(core.GateError):
                b.require_blog_url(address)
