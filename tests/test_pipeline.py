"""Synthetic local fixtures only: no real research, editor session, or publication."""
import contextlib
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zlib
import zipfile
from datetime import datetime, timedelta, timezone

from homepan import cli as h


def png(path, color):
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data) & 0xffffffff)
    width = height = 1080
    raw = (b'\0' + bytes(color) * width) * height
    path.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!IIBBBBB', width, height, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


def fixture(root):
    root.mkdir()
    h.write(root / 'run.json', {'id': root.name, 'created_at': h.now(), 'completed': {}})
    h.write(root / 'style_profile.json', h.default_style())
    source = {'id': 'S01', 'url': 'https://example.test/fixture', 'title': 'SYNTHETIC TEST SOURCE',
              'accessed_at': h.now(), 'published_at': h.now(), 'evidence': 'TEST ONLY, not factual evidence'}
    candidates = [{'id': f'T{i}', 'keyword': f'테스트 {i}', 'angle': '테스트 관점', 'audience': '테스트',
                   'reason': '합성 테스트', 'timeliness_reason': '합성 자료', 'source_ids': ['S01'], 'editorial_score': 50}
                  for i in range(1, 6)]
    h.write(root / 'topic_brief.json', {'researched_at': h.now(), 'sources': [source], 'candidates': candidates, 'selected_id': 'T1'})
    body = '합성 테스트 주장입니다.[FACT:C01]\n\n'
    for i in range(1, 9):
        body += f'## 테스트 문단 {i}\n\n' + '이 문장은 상태 전환 검사를 위한 합성 데이터이며 실제 정보가 아닙니다. ' * 8
        body += f'\n\n[IMAGE_{i:02}: 테스트 이미지]\n\n'
    claims = [{'id': 'C01', 'text': '합성 테스트 주장입니다.', 'source_ids': ['S01']}]
    draft = {'title': '합성 테스트 제목', 'title_candidates': ['합성 테스트 제목'] + [f'후보 {i}' for i in range(4)], 'body': body, 'claims': claims}
    h.write(root / 'draft.json', draft)
    (root / 'images').mkdir()
    images = []
    for i in range(1, 9):
        path = root / 'images' / f'{i}.png'
        png(path, (i * 20, 10, 10))
        images.append({'id': f'IMAGE_{i:02}', 'file_path': f'images/{i}.png', 'sha256': h.digest(path),
                       'source_url': source['url'], 'rights_status': 'cleared', 'rights_basis': 'Locally generated test fixture',
                       'credit': 'Test fixture', 'alt_text': '합성 이미지', 'caption': 'TEST ONLY'})
    h.write(root / 'image_map.json', {'images': images})
    final = {'title': draft['title'], 'body': body, 'claims': claims, 'hashtags': [f'태그{i}' for i in range(10)],
             'category': '합성 테스트', 'thumbnail': 'IMAGE_01'}
    h.write(root / 'final_post.json', final)
    report = {'status': 'passed', 'checked_at': h.now(), 'coverage_reviewed': True,
              'final_sha256': h.digest(root / 'final_post.json'), 'image_map_sha256': h.digest(root / 'image_map.json'),
              'sources': [source], 'claims': [{'id': 'C01', 'verdict': 'verified', 'source_ids': ['S01'], 'reason': 'SYNTHETIC TEST ONLY'}],
              'unresolved': [], 'errors': []}
    for field in ('numeric_review', 'date_review', 'legal_review', 'causality_review', 'caption_review'):
        report[field] = 'SYNTHETIC TEST ONLY'
    h.write(root / 'factcheck.json', report)
    receipt = {'status': 'verified', 'final_sha256': h.digest(root / 'final_post.json'),
               'image_map_sha256': h.digest(root / 'image_map.json'), 'checked_at': h.now(), 'evidence': 'SYNTHETIC TEST ONLY'}
    for field in ('title_matches', 'body_matches', 'images_match', 'formatting_checked', 'thumbnail_matches', 'tags_match', 'category_matches'):
        receipt[field] = True
    h.write(root / 'editor_receipt.json', receipt)
    h.write(root / 'publication.json', {'status': 'published', 'url': 'https://blog.naver.com/test_fixture/12345',
                                      'published_at': h.now(), 'final_sha256': h.digest(root / 'final_post.json'),
                                      'public_page_verified': True, 'evidence': 'SYNTHETIC TEST ONLY; never accessed this URL'})


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.tmp.name)
        cls.fixture = cls.base / 'fixture'
        fixture(cls.fixture)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        import shutil
        self.root = self.base / self._testMethodName
        shutil.copytree(self.fixture, self.root)
        self.db = self.root / 'ledger.sqlite3'

    def change(self, name, edit):
        data = h.read(self.root / name)
        edit(data)
        h.write(self.root / name, data)

    def through(self, stage):
        for s in h.STAGES[:h.STAGES.index(stage) + 1]:
            h.advance(self.root, s)

    def test_complete_local_contract_and_idempotent_record(self):
        self.through('factcheck')
        h.editor_plan(self.root)
        plan = h.read(self.root / 'editor_plan.json')
        self.assertEqual(len([e for e in plan['events'] if e['type'] == 'image']), 8)
        self.assertNotIn('[FACT:', json.dumps(plan))
        self.assertNotIn('[IMAGE_', json.dumps(plan))
        h.advance(self.root, 'editor')
        self.assertEqual(h.ledger_action(self.root, self.db, 'reserve')['status'], 'pending')
        self.assertEqual(h.ledger_action(self.root, self.db, 'record')['status'], 'completed')
        self.assertEqual(h.ledger_action(self.root, self.db, 'record')['status'], 'already_completed')

    def test_cannot_skip_stages(self):
        with self.assertRaisesRegex(h.GateError, '이전 단계'):
            h.advance(self.root, 'factcheck')

    def test_plan_requires_passed_stage(self):
        with self.assertRaisesRegex(h.GateError, '팩트체크'):
            h.editor_plan(self.root)

    def test_changed_upstream_invalidates_state(self):
        self.through('draft')
        self.change('draft.json', lambda d: d.update(title='changed'))
        with self.assertRaisesRegex(h.GateError, 'rewind draft'):
            h.state_check(self.root)

    def test_changed_image_bytes_invalidate_state(self):
        self.through('images')
        (self.root / 'images/1.png').write_bytes(b'changed')
        with self.assertRaisesRegex(h.GateError, 'rewind images'):
            h.state_check(self.root)

    def test_failed_factcheck_blocks_plan(self):
        self.through('images')
        self.change('factcheck.json', lambda d: d.update(status='blocked'))
        with self.assertRaisesRegex(h.GateError, '미통과'):
            h.advance(self.root, 'factcheck')

    def test_unresolved_claim_blocks(self):
        self.change('factcheck.json', lambda d: d.update(unresolved=['날짜 미확인']))
        with self.assertRaisesRegex(h.GateError, '미해결'):
            h.validate(self.root, 'factcheck')

    def test_final_edit_invalidates_factcheck(self):
        self.change('final_post.json', lambda d: d.update(category='changed'))
        with self.assertRaisesRegex(h.GateError, '원고 변경'):
            h.validate(self.root, 'factcheck')

    def test_missing_claim_verification_blocks(self):
        self.change('factcheck.json', lambda d: d['claims'][0].update(id='UNKNOWN'))
        with self.assertRaisesRegex(h.GateError, '모든 최종 주장'):
            h.validate(self.root, 'factcheck')

    def test_unverified_claim_blocks(self):
        self.change('factcheck.json', lambda d: d['claims'][0].update(verdict='unverified'))
        with self.assertRaisesRegex(h.GateError, '미검증'):
            h.validate(self.root, 'factcheck')

    def test_old_trend_blocks(self):
        old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        self.change('topic_brief.json', lambda d: d.update(researched_at=old))
        with self.assertRaisesRegex(h.GateError, '48시간'):
            h.validate(self.root, 'topic')

    def test_expired_factcheck_blocks(self):
        old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        self.change('factcheck.json', lambda d: d.update(checked_at=old))
        with self.assertRaisesRegex(h.GateError, '24시간'):
            h.validate(self.root, 'factcheck')

    def test_future_research_is_rejected(self):
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        self.change('topic_brief.json', lambda d: d.update(researched_at=future))
        with self.assertRaisesRegex(h.GateError, '미래'):
            h.validate(self.root, 'topic')

    def test_duplicate_tags_block(self):
        self.change('final_post.json', lambda d: d.update(hashtags=['동일'] * 10))
        with self.assertRaisesRegex(h.GateError, '중복 해시태그'):
            h.validate(self.root, 'factcheck')

    def test_missing_image_blocks(self):
        (self.root / 'images/2.png').unlink()
        with self.assertRaisesRegex(h.GateError, '이미지 파일 없음'):
            h.validate(self.root, 'images')

    def test_path_escape_blocks(self):
        self.change('image_map.json', lambda d: d['images'][0].update(file_path='../outside.png'))
        with self.assertRaisesRegex(h.GateError, '실행 폴더 안'):
            h.validate(self.root, 'images')

    def test_uncleared_rights_block(self):
        self.change('image_map.json', lambda d: d['images'][0].update(rights_status='unknown'))
        with self.assertRaisesRegex(h.GateError, '이용 근거'):
            h.validate(self.root, 'images')

    def test_bad_thumbnail_dimensions_block(self):
        path = self.root / 'images/1.png'
        data = bytearray(path.read_bytes())
        data[16:20] = (1000).to_bytes(4, 'big')
        path.write_bytes(data)
        self.change('image_map.json', lambda d: d['images'][0].update(sha256=h.digest(path)))
        with self.assertRaisesRegex(h.GateError, '1080x1080'):
            h.validate(self.root, 'images')

    def test_incomplete_editor_blocks(self):
        self.change('editor_receipt.json', lambda d: d.update(body_matches=False))
        with self.assertRaisesRegex(h.GateError, 'body_matches'):
            h.validate(self.root, 'editor')

    def test_duplicate_reservation_blocks(self):
        self.through('editor')
        h.ledger_action(self.root, self.db, 'reserve')
        with self.assertRaisesRegex(h.GateError, '이미 예약'):
            h.ledger_action(self.root, self.db, 'reserve')

    def test_duplicate_content_in_new_run_blocks(self):
        self.through('editor')
        h.ledger_action(self.root, self.db, 'reserve')
        self.change('run.json', lambda d: d.update(id='different-run'))
        with self.assertRaisesRegex(h.GateError, '이미 예약'):
            h.ledger_action(self.root, self.db, 'reserve')

    def test_record_without_reserve_blocks(self):
        self.through('editor')
        with self.assertRaisesRegex(h.GateError, 'reserve 없이'):
            h.ledger_action(self.root, self.db, 'record')

    def test_non_naver_publication_blocks(self):
        self.change('publication.json', lambda d: d.update(url='https://blog.naver.com.evil.test/test/1'))
        with self.assertRaisesRegex(h.GateError, '네이버 블로그'):
            h.validate(self.root, 'publish')

    def test_public_page_must_be_verified(self):
        self.change('publication.json', lambda d: d.update(public_page_verified=False))
        with self.assertRaisesRegex(h.GateError, '공개 페이지'):
            h.validate(self.root, 'publish')

    def test_url_normalization(self):
        self.assertEqual(h.canonical_post_url('https://m.blog.naver.com/PostView.naver?blogId=test&logNo=42'), 'https://blog.naver.com/test/42')

    def test_cli_error_exit_and_no_overwrite(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(h.main(['init', str(self.root)]), 1)
            self.assertEqual(h.main(['advance', str(self.root), 'publish']), 1)
        self.assertEqual(h.read(self.root / 'run.json')['completed'], {})

    def test_rewind_preserves_artifacts_and_invalidates_downstream(self):
        self.through('factcheck')
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(h.main(['rewind', str(self.root), 'draft']), 0)
        self.assertEqual(list(h.state_check(self.root)['completed']), ['style', 'topic'])
        self.assertTrue((self.root / 'final_post.json').exists())

    def test_benchmark_measurements_are_explicit_estimates(self):
        source = self.root / 'sample.txt'
        source.write_text(('설명해요. 확인합니다. 메모임.\n\n' * 30), encoding='utf-8')
        result = h.analyze_sample(source, self.root / 'measurements.json')
        self.assertEqual(result['status'], 'measurements_only')
        self.assertEqual(result['ending_counts_heuristic']['해요체'], 30)
        self.assertEqual(result['ending_counts_heuristic']['격식체'], 30)

    def test_export_only_whitelisted_artifacts_and_portable_state(self):
        self.through('factcheck')
        (self.root / 'cookies.json').write_text('not-a-real-secret')
        destination = self.root / 'transfer.zip'
        core_result = h.export_run(self.root, destination)
        self.assertEqual(core_result['status'], 'exported')
        target = self.root / 'unpacked'
        with zipfile.ZipFile(destination) as archive:
            self.assertNotIn('cookies.json', archive.namelist())
            self.assertNotIn('editor_receipt.json', archive.namelist())
            archive.extractall(target)
        self.assertIn('factcheck', h.state_check(target)['completed'])
        h.validate(target, 'factcheck')
        with self.assertRaises(FileExistsError):
            h.export_run(self.root, destination)

    def test_export_after_input_is_blocked(self):
        self.through('factcheck')
        h.write(self.root / 'browser_progress.json', {'status': 'input_started'})
        with self.assertRaisesRegex(h.GateError, '이미 입력'):
            h.export_run(self.root, self.root / 'transfer.zip')


if __name__ == '__main__':
    unittest.main()
