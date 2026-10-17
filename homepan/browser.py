"""Local, visible browser workflow. No stealth, debug port, or cookie export."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import html
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlparse, parse_qs

from . import cli as core

BLOG_HOSTS = {"blog.naver.com", "m.blog.naver.com"}
TIMEOUT = 30000
PICKER = Path(__file__).with_name("picker.js").read_text(encoding="utf-8")


def normalized(text):
    return re.sub(r"[\s\u200b\ufeff]+", "", text)


def state_directory():
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library/Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return base / "Homepan"


def check_blog_id(value):
    core.need(bool(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value)), "블로그 ID 형식이 올바르지 않습니다")
    return value


def require_blog_url(value):
    parsed = core.url(value)
    core.need(parsed.hostname in BLOG_HOSTS and parsed.port in {None, 443}, "네이버 블로그 HTTPS 화면에서만 동작합니다")
    return parsed


def prepare_state(path):
    path = Path(path).expanduser().absolute()
    core.need(not path.is_symlink(), "세션 폴더에 심볼릭 링크를 사용할 수 없습니다")
    path = path.resolve()
    for parent in (path, *path.parents):
        git = parent / ".git"
        # Empty platform-reserved .git directories are not project repositories.
        core.need(not (git.is_file() or (git / "HEAD").exists()), "로그인 세션은 Git 저장소 밖에 보관해야 합니다")
    marker = path / "homepan-profile.json"
    if path.exists() and any(path.iterdir()):
        core.need(marker.is_file() and core.read(marker).get("owner") == "homepan", "기존 일반 브라우저 폴더를 사용할 수 없습니다")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        path.chmod(0o700)
    if not marker.exists():
        core.write(marker, {"owner": "homepan", "created_at": core.now()})
    core.need(not (path / "browser").is_symlink(), "브라우저 프로필 링크는 허용하지 않습니다")
    return path


@contextmanager
def profile_lock(state):
    """Kernel-released lock: a crash does not require deleting a stale PID file."""
    lock = open(state / "session.lock", "a+b")
    try:
        lock.seek(0, 2)
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise core.GateError("전용 브라우저가 이미 실행 중입니다. 기존 창을 정상 종료하세요") from exc
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    finally:
        lock.close()


def ask(message, expected=None):
    answer = input(message + "\n> ").strip()
    if expected is not None:
        core.need(answer == expected, "사용자가 작업을 중단했습니다")
    return answer


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def render_markdown(content):
    from markdown_it import MarkdownIt
    parser = MarkdownIt("commonmark", {"html": False}).enable("table").disable("image")
    rendered = parser.render(content)
    plain = PlainText()
    plain.feed(rendered)
    return rendered, "".join(plain.parts)


def frame_key(frame):
    parsed = urlparse(frame.url)
    return {"name": frame.name, "host": parsed.hostname, "path": parsed.path}


def locator_for(page, binding):
    frames = [f for f in page.frames if frame_key(f) == binding["frame"]]
    core.need(len(frames) == 1, "편집 프레임이 변경되었습니다. calibrate를 다시 실행하세요")
    frame = frames[0]
    require_blog_url(frame.url)
    locator = frame.locator(binding["selector"])
    core.need(locator.count() == 1 and locator.is_visible(), "연결한 요소가 없거나 중복됩니다. 화면을 확인하세요")
    actual = locator.evaluate("e => ({tag:e.tagName.toLowerCase(),role:e.getAttribute('role')||'',editable:e.isContentEditable,type:e.getAttribute('type')||''})")
    core.need(actual == {k: binding[k] for k in ("tag", "role", "editable", "type")}, "연결한 요소의 형태가 변경되었습니다")
    return locator


class Guard:
    def __init__(self, page, blog_id=None):
        self.denied = False
        self.blog_id = blog_id
        page.on("response", self.response)

    def response(self, response):
        host = urlparse(response.url).hostname or ""
        if host.endswith(".naver.com") and response.request.resource_type == "document" and response.status in {401, 403, 429}:
            self.denied = True

    def check(self, page):
        core.need(not self.denied, "접근 제한 응답을 받았습니다. 자동 재시도하지 않습니다")
        require_blog_url(page.url)
        if self.blog_id is not None:
            owners = set()
            for frame in page.frames:
                parsed = urlparse(frame.url)
                if parsed.hostname not in BLOG_HOSTS:
                    continue
                for key, values in parse_qs(parsed.query).items():
                    if key.lower() == "blogid":
                        owners.update(values)
                route = re.fullmatch(r"/([A-Za-z0-9_-]+)(?:/(?:[0-9]+|postwrite))?/?", parsed.path, re.I)
                if route:
                    owners.add(route[1])
            core.need(owners == {self.blog_id}, "편집 화면의 블로그 ID가 대상과 다르거나 확인되지 않습니다")
        for frame in page.frames:
            host = urlparse(frame.url).hostname or ""
            core.need(host != "nid.naver.com", "로그인/추가 인증 화면입니다. 작업을 멈추고 직접 확인하세요")
            if host not in BLOG_HOSTS:
                continue
            candidates = frame.locator('input[type="password"], input[autocomplete="one-time-code"], input[id*="captcha" i], iframe[src*="captcha" i]')
            core.need(not any(item.is_visible() for item in candidates.all()), "로그인 또는 보안 인증이 감지되었습니다")
            for item in frame.locator('[role="alert"], [role="dialog"]').all():
                if item.is_visible():
                    core.need(not re.search(r"비정상적인 접근|자동입력 방지|보안 인증|이용이 제한", item.inner_text()), "보안 확인/이용 제한 안내가 표시됐습니다")


def pick(page, label, kind="region"):
    require_blog_url(page.url)
    frames = [f for f in page.frames if urlparse(f.url).hostname in BLOG_HOSTS]
    try:
        for frame in frames:
            frame.evaluate(PICKER, {"label": label, "kind": kind})
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            for frame in frames:
                if frame.is_detached():
                    continue
                result = frame.evaluate("() => window.__homepanPicked")
                if result:
                    core.need(not result.get("cancelled"), "요소 선택을 취소했습니다")
                    result["frame"] = frame_key(frame)
                    return result
            page.wait_for_timeout(100)
        raise core.GateError("요소 선택 시간이 초과되었습니다")
    finally:
        for frame in frames:
            if not frame.is_detached():
                try:
                    frame.evaluate("() => window.__homepanPickCleanup?.()")
                except Exception:
                    pass  # Cleanup only; never used to ignore an input/publish failure.


def editable_value(locator):
    return locator.evaluate("e => ('value' in e ? e.value : e.innerText)")


def snapshot(content):
    return content.evaluate("""root => {
      let text = ''; const offsets = [];
      function visit(node) {
        if (node.nodeType === Node.TEXT_NODE) {text += node.textContent; return;}
        if (node.nodeType !== Node.ELEMENT_NODE) return;
        if (node.matches('script,style,[data-homepan-overlay]') || getComputedStyle(node).display === 'none') return;
        if (node.tagName === 'IMG') {offsets.push(Array.from(text.replace(/[\\s\\u200b\\ufeff]+/g,'')).length); return;}
        for (const child of node.childNodes) visit(child);
      }
      visit(root);
      return {text, image_offsets:offsets, tables:root.querySelectorAll('table').length,
              headings:root.querySelectorAll('h1,h2,h3,h4,h5,h6').length};
    }""")


def expected_content(plan):
    text = ""
    offsets = []
    tables = headings = 0
    for event in plan["events"]:
        if event["type"] == "markdown":
            rendered, plain = render_markdown(event["content"])
            text += plain
            tables += rendered.count("<table>")
            headings += len(re.findall(r"<h[1-6]>", rendered))
        else:
            offsets.append(len(normalized(text)))
            text += event["caption"] + "출처: " + event["credit"]
    return {"text": text, "image_offsets": offsets, "tables": tables, "headings": headings}


def compare_content(actual, expected):
    core.need(normalized(actual["text"]) == normalized(expected["text"]), "본문 읽기 결과가 원고와 다릅니다. 자동 재입력하지 않습니다")
    core.need(actual["image_offsets"] == expected["image_offsets"], "이미지 수 또는 본문 내 위치가 다릅니다")
    core.need(actual["tables"] == expected["tables"], "표 서식이 유지되지 않았습니다")
    core.need(actual["headings"] == expected["headings"], "소제목 서식이 유지되지 않았습니다")


def paste_html(page, rendered, plain):
    # Explicit rich-text paste. No stealth typing or fabricated human keystroke history.
    require_blog_url(page.url)
    page.bring_to_front()
    page.evaluate("""async ({markup, text}) => {
      await navigator.clipboard.write([new ClipboardItem({
        'text/html':new Blob([markup], {type:'text/html'}),
        'text/plain':new Blob([text], {type:'text/plain'})
      })]);
    }""", {"markup": rendered, "text": plain})
    page.keyboard.press("ControlOrMeta+V")


def caret_inside(content):
    return content.evaluate("e => {const s=window.getSelection(); return !!s?.anchorNode && e.contains(s.anchorNode) && (e.contains(document.activeElement) || e === document.activeElement);}")


def wait_images(content, count, timeout=TIMEOUT):
    content.evaluate("""(e, {count, timeout}) => new Promise((resolve,reject) => {
      const ready=()=>{const imgs=[...e.querySelectorAll('img')];return imgs.length===count && imgs.every(i=>i.complete && i.naturalWidth>0);};
      if(ready()) return resolve();
      const timer=setTimeout(()=>{cleanup();reject(new Error('image load timeout'));},timeout);
      const check=()=>{if(ready()){cleanup();resolve();}};
      const obs=new MutationObserver(check); obs.observe(e,{subtree:true,childList:true,attributes:true});
      e.addEventListener('load',check,true);
      function cleanup(){clearTimeout(timer);obs.disconnect();e.removeEventListener('load',check,true);}
    })""", {"count": count, "timeout": timeout})


class Writer:
    def __init__(self, page, binding, root, guard, prompt=ask):
        self.page, self.binding, self.root, self.guard, self.prompt = page, binding, Path(root), guard, prompt
        self.journal = self.root / "browser_progress.json"

    def save(self, status, event=None):
        core.write(self.journal, {"status": status, "event": event, "at": core.now(),
                                "final_sha256": core.digest(self.root / "final_post.json")})

    def write(self, plan):
        self.guard.check(self.page)
        core.need(not self.journal.exists(), "이 실행에 입력 시도 기록이 있습니다. 기존 초안을 확인하세요; 자동 재입력하지 않습니다")
        title = locator_for(self.page, self.binding["title"])
        body = locator_for(self.page, self.binding["body"])
        content = locator_for(self.page, self.binding["content"])
        core.need(not normalized(editable_value(title)), "제목이 비어 있지 않아 덮어쓰지 않습니다")
        initial = snapshot(content)
        core.need(not normalized(initial["text"]) and not initial["image_offsets"], "새 빈 원고에서만 시작할 수 있습니다")
        self.save("input_started")
        try:
            title.fill(plan["title"])
            core.need(editable_value(title) == plan["title"], "제목 입력 확인 실패")
            body.click()
            image_count = 0
            for index, event in enumerate(plan["events"]):
                self.guard.check(self.page)
                self.save("event_started", index)
                content = locator_for(self.page, self.binding["content"])
                if event["type"] == "markdown":
                    self.ensure_caret(content)
                    rendered, plain = render_markdown(event["content"])
                    paste_html(self.page, rendered, plain)
                    content.evaluate("""(e, text) => new Promise((resolve,reject) => {
                      const norm=s=>s.replace(/[\\s\\u200b\\ufeff]+/g,'');
                      const ready=()=>norm(e.innerText).includes(norm(text));
                      if(ready()) return resolve();
                      const observer=new MutationObserver(()=>{if(ready()){clearTimeout(timer);observer.disconnect();resolve();}});
                      const timer=setTimeout(()=>{observer.disconnect();reject(new Error('paste readback timeout'));},10000);
                      observer.observe(e,{subtree:true,childList:true,characterData:true});
                    })""", plain)
                else:
                    photo = locator_for(self.page, self.binding["photo_button"])
                    with self.page.expect_file_chooser(timeout=TIMEOUT) as chooser:
                        photo.click()
                    self.guard.check(self.page)
                    chooser.value.set_files(str(core.asset_path(self.root, event["file_path"])))
                    image_count += 1
                    wait_images(content, image_count)
                    self.ensure_caret(content)
                    caption = event["caption"] + "\n출처: " + event["credit"]
                    paste_html(self.page, "<p>" + html.escape(caption).replace("\n", "<br>") + "</p>", caption)
                self.save("event_completed", index)
            compare_content(snapshot(content), expected_content(plan))
            self.save("entered_needs_review")
        except BaseException:
            self.save("interrupted_check_existing_draft")
            raise

    def ensure_caret(self, content):
        if not caret_inside(content):
            self.prompt("본문 마지막 위치(사진 다음의 빈 문단)에 커서를 놓은 뒤 Enter를 누르세요.")
            self.guard.check(self.page)
            core.need(caret_inside(content), "본문 안의 입력 커서를 확인하지 못했습니다")


def review(root, page, binding, plan, guard, prompt=ask):
    guard.check(page)
    core.validate(root, "factcheck")
    print("카테고리:", plan["category"])
    print("태그:", ", ".join(plan["hashtags"]))
    prompt("화면에서 이미지 순서·캡션·서식, 첫 사진의 대표 지정, 위 카테고리와 태그 10개를 확인/설정하세요.\n모두 맞으면 '검토완료'를 입력하세요. 최종 발행 버튼은 누르지 마세요.", "검토완료")
    guard.check(page)
    core.state_check(root)
    core.validate(root, "factcheck")
    core.need(core.digest(root / "final_post.json") == plan["final_sha256"], "검토 도중 원고가 변경됐습니다")
    core.need(editable_value(locator_for(page, binding["title"])) == plan["title"], "검토 도중 제목이 변경됐습니다")
    compare_content(snapshot(locator_for(page, binding["content"])), expected_content(plan))
    if "editor" in core.state_check(root)["completed"]:
        core.validate(root, "editor")
        return  # Preserve the existing receipt hash when reviewing an existing draft.
    receipt = {"status": "verified", "final_sha256": plan["final_sha256"], "image_map_sha256": plan["image_map_sha256"],
               "checked_at": core.now(), "evidence": "브라우저 제목/본문/이미지 위치/표/소제목 대조 + 사용자의 캡션·서식·태그·대표·카테고리 확인",
               "verification_method": {"text_and_positions": "dom", "metadata_and_visuals": "operator"}}
    for key in ("title_matches", "body_matches", "images_match", "formatting_checked", "thumbnail_matches", "tags_match", "category_matches"):
        receipt[key] = True
    core.write(root / "editor_receipt.json", receipt)
    core.advance(root, "editor")


def post_urls(pages, blog_id):
    addresses = set()
    for candidate in pages:
        for frame in candidate.frames:
            try:
                address = core.canonical_post_url(frame.url)
            except (core.GateError, ValueError):
                continue
            if urlparse(address).path.split('/')[1] == blog_id:
                addresses.add(address)
    return addresses


def wait_post(page, blog_id, previous_urls, previous_pages, timeout=TIMEOUT):
    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        candidates = [p for p in page.context.pages if p is page or p not in previous_pages]
        new_urls = post_urls(candidates, blog_id) - previous_urls
        core.need(len(new_urls) <= 1, "새 게시물 URL이 여러 개입니다. 실제 발행 결과를 확인하세요")
        if new_urls:
            return new_urls.pop()
        page.wait_for_timeout(100)
    raise core.GateError("발행 결과 URL을 확인하지 못했습니다. 예약을 유지합니다. 기존 글 확인 전 재발행하지 마세요")


def verify_public(browser, address, plan, prompt=ask):
    # A fresh context proves the page is readable without the logged-in account.
    context = browser.new_context(accept_downloads=False)
    try:
        page = context.new_page()
        response = page.goto(address, wait_until="domcontentloaded", timeout=TIMEOUT)
        core.need(response is not None and response.ok, "비로그인 공개 페이지를 열 수 없습니다")
        Guard(page).check(page)
        print("비로그인 확인 창에서 실제 게시물의 제목과 본문 영역을 지정합니다.")
        title = pick(page, "게시물 제목 선택")
        content = pick(page, "사진을 포함한 게시물 본문 전체 영역 선택 (↑로 상위 영역)")
        core.need(normalized(locator_for(page, title).inner_text()) == normalized(plan["title"]), "공개 제목 불일치")
        content_locator = locator_for(page, content)
        expected = expected_content(plan)
        compare_content(snapshot(content_locator), expected)
        wait_images(content_locator, len(expected["image_offsets"]))
        prompt("비로그인 창에서 사진·캡션·본문이 올바르게 보이면 '공개확인'을 입력하세요.", "공개확인")
    finally:
        context.close()


def publish(root, page, binding, plan, guard, database, blog_id, prompt=ask):
    core.state_check(root)
    core.validate(root, "editor")
    guard.check(page)
    prompt("발행 옵션을 열어 대상 블로그·전체 공개·즉시 발행 설정을 확인하세요. 아직 최종 버튼을 누르지 말고 Enter를 누르세요.")
    target = pick(page, "최종 발행 버튼을 지정하세요 (선택만 하며 아직 클릭하지 않음)", "button")
    prompt("이 원고를 지금 공개하려면 '발행'을 입력하세요.", "발행")
    guard.check(page)
    core.state_check(root)
    core.validate(root, "editor")
    core.need(editable_value(locator_for(page, binding["title"])) == plan["title"], "발행 직전 제목 변경")
    compare_content(snapshot(locator_for(page, binding["content"])), expected_content(plan))
    button = locator_for(page, target)
    core.need(button.is_enabled(), "발행 버튼이 비활성화되어 있습니다")
    core.ledger_action(root, database, "reserve")
    core.write(root / "browser_progress.json", {"status": "publish_pending", "at": core.now()})
    previous_pages = list(page.context.pages)
    previous_urls = post_urls(previous_pages, blog_id)
    button.click(timeout=TIMEOUT)  # Exactly one click; no retry on an uncertain outcome.
    address = wait_post(page, blog_id, previous_urls, previous_pages)
    verify_public(page.context.browser, address, plan, prompt)
    record_publication(root, database, plan, address)
    return address


def record_publication(root, database, plan, address):
    core.write(root / "publication.json", {"status": "published", "url": address, "published_at": core.now(),
               "final_sha256": plan["final_sha256"], "public_page_verified": True,
               "evidence": "비로그인 컨텍스트의 제목·본문·이미지 위치·서식 대조와 사용자 시각 확인",
               "timestamp_basis": "publication_confirmation_time"})
    core.ledger_action(root, database, "record")
    core.write(root / "browser_progress.json", {"status": "published", "url": address, "at": core.now()})


def current_editor(context, blog_id, prompt=ask, existing=False):
    page = context.new_page()
    response = page.goto(f"https://blog.naver.com/{blog_id}", wait_until="domcontentloaded", timeout=TIMEOUT)
    core.need(response is not None and response.ok, "블로그 접근이 차단되거나 실패했습니다")
    action = "이 실행에서 작성한 기존 초안을 여세요" if existing else "새 빈 글쓰기 화면을 여세요. 기존 초안은 보존하세요"
    prompt(f"본인 블로그인지 확인하고 {action}. 준비되면 Enter를 누르세요.")
    candidates = [p for p in context.pages if urlparse(p.url).hostname in BLOG_HOSTS]
    core.need(candidates, "네이버 블로그 창을 찾지 못했습니다")
    page = candidates[-1]
    Guard(page, blog_id).check(page)
    return page


def calibrate(page, state, blog_id):
    fields = {}
    for key, label, kind in (("title", "빈 제목 입력란", "editable"), ("body", "빈 본문 입력란", "editable"),
                             ("content", "제목을 제외한 본문·사진 전체 영역 (↑로 상위 영역)", "region"),
                             ("photo_button", "PC 파일 선택창을 직접 여는 사진 버튼", "button")):
        print(label, "을 화면에서 지정하세요.")
        fields[key] = pick(page, label, kind)
        locator_for(page, fields[key])
    core.need(not normalized(editable_value(locator_for(page, fields["title"]))), "빈 제목으로 연결하세요")
    content = snapshot(locator_for(page, fields["content"]))
    core.need(not normalized(content["text"]) and not content["image_offsets"], "본문 영역이 비어 있지 않거나 잘못 선택됐습니다")
    config = {"version": 1, "blog_id": blog_id, "captured_at": core.now(), "bindings": fields,
              "status": "captured_from_live_ui_not_yet_write_tested"}
    core.write(state / "editor.json", config)
    return config


def main(argv=None):
    parser = argparse.ArgumentParser(description="본인 PC의 전용 프로필로 한 글씩 입력·검토합니다")
    parser.add_argument("--state-dir", type=Path, default=state_directory())
    parser.add_argument("--channel", choices=("chromium", "chrome", "msedge"), default="chromium")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("login")
    sub.add_parser("status")
    setup = sub.add_parser("calibrate")
    setup.add_argument("--blog-id", required=True)
    for name in ("write", "review", "recover"):
        cmd = sub.add_parser(name)
        cmd.add_argument("run", type=Path)
        if name in {"write", "review"}:
            cmd.add_argument("--publish", action="store_true", help="화면 검토 후 명시적으로 발행할 때만 사용")
        else:
            cmd.add_argument("--url", required=True, help="이미 발행된 글을 검증·기록하며 다시 클릭하지 않음")
    args = parser.parse_args(argv)
    try:
        if args.command == "status":
            state = args.state_dir.expanduser()
            print(json.dumps({"state_dir": str(state), "profile_exists": (state / "browser").is_dir(),
                              "editor_bound": (state / "editor.json").is_file(), "login_status": "requires_browser_check"}, ensure_ascii=False, indent=2))
            return 0
        from playwright.sync_api import sync_playwright
        if args.command == "calibrate":
            check_blog_id(args.blog_id)
        state = prepare_state(args.state_dir)
        plan = config = None
        if args.command in {"write", "review", "recover"}:
            args.run = args.run.resolve()
            core.editor_plan(args.run)
            plan = core.read(args.run / "editor_plan.json")
            config = core.read(state / "editor.json")
            check_blog_id(config["blog_id"])
            if args.command == "write":
                core.need(not (args.run / "browser_progress.json").exists(), "기존 입력 기록이 있습니다. review로 기존 초안을 확인하세요")
        with profile_lock(state), sync_playwright() as p:
            options = {"headless": False, "accept_downloads": False, "no_viewport": True}
            if args.channel != "chromium":
                options["channel"] = args.channel
            context = p.chromium.launch_persistent_context(str(state / "browser"), **options)
            context.set_default_timeout(TIMEOUT)
            try:
                if args.command == "login":
                    page = context.new_page()
                    page.goto("https://blog.naver.com/", wait_until="domcontentloaded", timeout=TIMEOUT)
                    ask("전용 브라우저에서 직접 로그인하세요. 추가 인증은 직접 처리하세요. 끝나면 Enter로 정상 종료합니다.")
                    print("전용 프로필을 보관합니다. 로그인 유지 여부는 다음 실행에서 확인합니다.")
                elif args.command == "calibrate":
                    page = current_editor(context, args.blog_id)
                    calibrate(page, state, args.blog_id)
                    print("실제 화면의 요소 연결을 저장했습니다. 네이버 입력 검증은 첫 실행에서 수행합니다.")
                elif args.command == "recover":
                    address = core.canonical_post_url(args.url)
                    core.need(urlparse(address).path.split('/')[1] == config["blog_id"], "다른 블로그의 URL입니다")
                    verify_public(context.browser, address, plan)
                    record_publication(args.run, state / "publications.sqlite3", plan, address)
                    print("기존 게시물 확인·기록 완료:", address)
                else:
                    page = current_editor(context, config["blog_id"], existing=args.command == "review")
                    guard = Guard(page, config["blog_id"])
                    context.grant_permissions(["clipboard-write"], origin="https://blog.naver.com")
                    if args.command == "write":
                        Writer(page, config["bindings"], args.run, guard).write(plan)
                    review(args.run, page, config["bindings"], plan, guard)
                    if args.publish:
                        address = publish(args.run, page, config["bindings"], plan, guard, state / "publications.sqlite3", config["blog_id"])
                        print("발행·공개 확인 완료:", address)
                    else:
                        ask("입력·검토가 끝났습니다. 브라우저에서 임시저장 상태를 직접 확인한 뒤 Enter를 누르세요. 자동 발행은 하지 않았습니다.")
            except (Exception, KeyboardInterrupt):
                if sys.stdin.isatty():
                    try:
                        ask("작업이 중단됐습니다. 브라우저에서 기존 초안/게시 여부를 확인하고 보존하세요. 확인 후 Enter로 종료합니다.")
                    except (EOFError, KeyboardInterrupt):
                        pass
                raise
            finally:
                context.close()
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        # Playwright traces may contain URLs/user text. Do not dump traces/session data.
        reason = str(exc) if isinstance(exc, core.GateError) else f"{type(exc).__name__}: 브라우저/패키지/연결 상태를 확인하세요. 자동 재시도하지 않습니다."
        print(json.dumps({"status": "blocked", "reason": reason}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
