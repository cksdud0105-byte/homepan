"""Dependency-free artifact gates. These checks do not establish factual truth."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs

STAGES = ("style", "topic", "draft", "images", "factcheck", "editor", "publish")
FILES = {
    "style": ("style_profile.json",),
    "topic": ("topic_brief.json",),
    "draft": ("draft.json",),
    "images": ("image_map.json",),
    "factcheck": ("factcheck.json", "final_post.json"),
    "editor": ("editor_receipt.json",),
    "publish": ("publication.json",),
}
MARKER = re.compile(r"\[IMAGE_(\d{2})(?::[^\]\n]+)?\]")
FACT = re.compile(r"\[FACT:([A-Za-z0-9_-]+)\]")


class GateError(ValueError):
    pass


def need(condition, message):
    if not condition:
        raise GateError(message)


def now():
    return datetime.now(timezone.utc).isoformat()


def timestamp(value):
    need(isinstance(value, str), "시간은 ISO 8601 문자열이어야 합니다")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GateError(f"잘못된 시간: {value}") from exc
    need(dt.tzinfo is not None, "시간대가 필요합니다")
    return dt


def url(value):
    need(isinstance(value, str), "URL 문자열이 필요합니다")
    p = urlparse(value)
    need(p.scheme == "https" and p.hostname and not p.username and not p.password,
         "출처 URL은 인증 정보 없는 HTTPS여야 합니다")
    return p


def read(path):
    try:
        with Path(path).open(encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        raise GateError(f"읽기 실패: {path}: {exc}") from exc
    need(isinstance(data, dict), f"JSON object 필요: {path}")
    return data


def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".homepan-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def text_field(data, key):
    need(isinstance(data.get(key), str) and data[key].strip(), f"{key}: 비어 있지 않은 문자열 필요")
    return data[key]


def list_field(data, key):
    need(isinstance(data.get(key), list) and data[key], f"{key}: 비어 있지 않은 배열 필요")
    return data[key]


def ids(items):
    result = [text_field(item, "id") for item in items]
    need(len(result) == len(set(result)), "중복 ID")
    return set(result)


def sources(data):
    items = list_field(data, "sources")
    for source in items:
        url(source.get("url"))
        timestamp(source.get("accessed_at"))
        text_field(source, "title")
        text_field(source, "evidence")
    return ids(items)


def slots(body):
    found = [f"IMAGE_{number}" for number in MARKER.findall(body)]
    need(8 <= len(found) <= 10, "이미지 슬롯은 8~10개 필요")
    need(found == [f"IMAGE_{i:02}" for i in range(1, len(found) + 1)],
         "이미지 슬롯은 IMAGE_01부터 중복 없이 순서대로 필요")
    need(body.count("[IMAGE_") == len(found), "잘못된 이미지 마커")
    return found


def post(data, final=False):
    text_field(data, "title")
    body = text_field(data, "body")
    slots(body)
    need(len(FACT.sub("", MARKER.sub("", body))) >= 1800, "본문은 이미지·팩트 마커 제외 1,800자 이상 필요")
    claims = list_field(data, "claims")
    claim_ids = ids(claims)
    need(set(FACT.findall(data["title"] + "\n" + body)) == claim_ids, "팩트 마커와 claims ID가 일치해야 합니다")
    for claim in claims:
        quote = text_field(claim, "text")
        need(quote in data["title"] + "\n" + body, f"본문에 없는 주장: {claim['id']}")
        list_field(claim, "source_ids")
    if final:
        tags = list_field(data, "hashtags")
        need(len(tags) == 10 and all(isinstance(t, str) and re.fullmatch(r"[^\s#]+", t) for t in tags),
             "해시태그는 #·공백 없는 문자열 정확히 10개 필요")
        need(len(set(t.casefold() for t in tags)) == 10, "중복 해시태그")
        text_field(data, "category")
        need(data.get("thumbnail") == "IMAGE_01", "대표 이미지는 IMAGE_01")
    else:
        titles = list_field(data, "title_candidates")
        need(len(titles) == 5 and len(set(titles)) == 5 and data["title"] in titles,
             "서로 다른 제목 5안과 그중 선택한 title 필요")


def asset_path(root, value):
    need(isinstance(value, str) and value, "이미지 상대 경로 필요")
    need(not Path(value).is_absolute(), "이미지는 실행 폴더 기준 상대 경로여야 합니다")
    need(".." not in Path(value).parts and "\\" not in value and ":" not in value,
         "이미지 경로는 실행 폴더 안의 정규 상대 경로여야 합니다")
    path = (root / value).resolve()
    need(path.is_relative_to(root.resolve()), "이미지 파일은 실행 폴더 안에 있어야 합니다")
    need(path.is_file(), f"이미지 파일 없음: {value}")
    return path


def image_size(path):
    """Read PNG/JPEG dimensions without executing or fetching image content."""
    data = path.read_bytes()
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24 and data[12:16] == b"IHDR":
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if data.startswith(b"\xff\xd8"):
        pos = 2
        while pos + 4 <= len(data):
            if data[pos] != 255:
                break
            marker = data[pos + 1]
            if marker == 255:
                pos += 1
                continue
            length = int.from_bytes(data[pos + 2:pos + 4], "big")
            if marker in {0xC0, 0xC1, 0xC2} and pos + 9 <= len(data):
                return int.from_bytes(data[pos + 7:pos + 9], "big"), int.from_bytes(data[pos + 5:pos + 7], "big")
            if length < 2:
                break
            pos += 2 + length
    raise GateError(f"PNG/JPEG 헤더를 읽을 수 없음: {path.name}")


def fingerprints(root, stage):
    result = {name: digest(root / name) for name in FILES[stage]}
    if stage == "images":
        for item in read(root / "image_map.json")["images"]:
            path = asset_path(root, item["file_path"])
            result[item["file_path"]] = digest(path)
    return result


def canonical_post_url(value):
    p = url(value)
    need(p.hostname in {"blog.naver.com", "m.blog.naver.com"} and p.port in {None, 443},
         "네이버 블로그 게시물 URL이 필요합니다")
    match = re.fullmatch(r"/([A-Za-z0-9_-]+)/([0-9]+)/?", p.path)
    if match:
        return f"https://blog.naver.com/{match[1]}/{match[2]}"
    query = parse_qs(p.query)
    if p.path == "/PostView.naver":
        blog = query.get("blogId", [""])[0]
        number = query.get("logNo", [""])[0]
        if re.fullmatch(r"[A-Za-z0-9_-]+", blog) and number.isdigit():
            return f"https://blog.naver.com/{blog}/{number}"
    raise GateError("게시물 번호가 있는 URL이 필요합니다")


def validate_one(root, stage):
    data = read(root / FILES[stage][0])
    if stage == "style":
        need(data.get("mode") in {"default", "benchmark"}, "style mode는 default 또는 benchmark")
        text_field(data, "name")
        for key in ("title_rules", "intro_rules", "body_rules", "tone_rules", "visual_rules"):
            list_field(data, key)
        if data["mode"] == "benchmark":
            list_field(data, "samples")
            text_field(data, "limitations")
    elif stage == "topic":
        ref_ids = sources(data)
        current = timestamp(data.get("researched_at"))
        need(-300 <= (datetime.now(timezone.utc) - current).total_seconds() <= 48 * 3600,
             "트렌드 조사 결과가 48시간보다 오래되었거나 미래입니다")
        candidates = list_field(data, "candidates")
        need(len(candidates) == 5, "실제 근거가 있는 후보 5개 필요; 부족하면 조사 단계에서 중단")
        need(data.get("selected_id") in ids(candidates), "선정 후보 ID 오류")
        for candidate in candidates:
            for key in ("keyword", "angle", "audience", "reason", "timeliness_reason"):
                text_field(candidate, key)
            need(set(list_field(candidate, "source_ids")) <= ref_ids, "후보 출처 ID 오류")
            score = candidate.get("editorial_score")
            need(type(score) in (int, float) and 0 <= score <= 100, "편집 우선순위 점수는 0~100")
        for source in data["sources"]:
            accessed = timestamp(source["accessed_at"])
            need(-300 <= (current - accessed).total_seconds() <= 48 * 3600, "실시간 출처 조회 시각 불일치")
            timestamp(source.get("published_at"))
    elif stage == "draft":
        post(data)
        ref_ids = {s["id"] for s in read(root / "topic_brief.json")["sources"]}
        if data.get("sources"):
            ref_ids |= sources(data)
        for claim in data["claims"]:
            need(set(claim["source_ids"]) <= ref_ids, "초안 주장 출처 ID 오류")
    elif stage == "images":
        images = list_field(data, "images")
        wanted = slots(read(root / "draft.json")["body"])
        need([i.get("id") for i in images] == wanted, "원고 슬롯과 이미지 매핑 불일치")
        hashes = []
        for item in images:
            path = asset_path(root, item.get("file_path"))
            hashes.append(digest(path))
            need(item.get("sha256") == hashes[-1], "이미지 해시 불일치")
            for key in ("alt_text", "caption", "credit", "rights_basis"):
                text_field(item, key)
            url(item.get("source_url"))
            need(item.get("rights_status") == "cleared", "이미지 이용 근거 확인 필요")
            width, height = image_size(path)
            need(width >= 800 and height >= 600, "이미지 최소 크기 800x600")
            if item["id"] == "IMAGE_01":
                need((width, height) == (1080, 1080), "썸네일은 1080x1080")
        need(len(set(hashes)) == len(hashes), "동일 이미지 파일 중복")
    elif stage == "factcheck":
        final = read(root / "final_post.json")
        post(final, final=True)
        need(slots(final["body"]) == slots(read(root / "draft.json")["body"]), "최종 원고 이미지 슬롯 변경: 이미지 단계를 다시 진행")
        need(data.get("status") == "passed", "팩트체크 미통과")
        need(data.get("final_sha256") == digest(root / "final_post.json"), "팩트체크 이후 최종 원고 변경")
        reviewed = timestamp(data.get("checked_at"))
        need(-300 <= (datetime.now(timezone.utc) - reviewed).total_seconds() <= 24 * 3600, "팩트체크는 24시간 내 결과 필요")
        need(data.get("coverage_reviewed") is True, "제목·본문·표·캡션의 누락 주장 검토 필요")
        need(data.get("image_map_sha256") == digest(root / "image_map.json"), "검증된 이미지 매핑 변경")
        need(data.get("unresolved") == [], "미해결 검증 항목 존재")
        ref_ids = sources(data)
        checks = list_field(data, "claims")
        need(ids(checks) == {c["id"] for c in final["claims"]}, "모든 최종 주장 검증 필요")
        for claim in final["claims"]:
            need(set(claim["source_ids"]) <= ref_ids, "최종 주장 출처 ID 오류")
        for check in checks:
            need(check.get("verdict") == "verified", "미검증 주장 존재")
            need(set(list_field(check, "source_ids")) <= ref_ids, "검증 출처 ID 오류")
            text_field(check, "reason")
        for key in ("numeric_review", "date_review", "legal_review", "causality_review", "caption_review"):
            text_field(data, key)
    elif stage == "editor":
        need(data.get("status") == "verified", "에디터 확인 미완료")
        need(data.get("final_sha256") == digest(root / "final_post.json"), "에디터 원고 해시 불일치")
        need(data.get("image_map_sha256") == digest(root / "image_map.json"), "에디터 이미지 해시 불일치")
        for key in ("title_matches", "body_matches", "images_match", "formatting_checked", "thumbnail_matches", "tags_match", "category_matches"):
            need(data.get(key) is True, f"에디터 확인 필요: {key}")
        text_field(data, "evidence")
        timestamp(data.get("checked_at"))
    elif stage == "publish":
        need(data.get("status") == "published", "발행 완료 미확인")
        canonical_post_url(data.get("url"))
        need(data.get("final_sha256") == digest(root / "final_post.json"), "발행 원고 해시 불일치")
        timestamp(data.get("published_at"))
        text_field(data, "evidence")
        need(data.get("public_page_verified") is True, "공개 페이지 내용 확인 필요")


def validate(root, stage):
    for previous in STAGES[:STAGES.index(stage) + 1]:
        validate_one(root, previous)


def state_check(root):
    state = read(root / "run.json")
    completed = state.get("completed", {})
    need(list(completed) == list(STAGES[:len(completed)]), "단계 상태 순서 오류")
    for stage, record in completed.items():
        need(record["hashes"] == fingerprints(root, stage),
             f"완료된 {stage} 산출물 변경; rewind {stage} 후 재검증 필요")
    return state


def advance(root, stage):
    state = state_check(root)
    completed = state["completed"]
    if stage in completed:
        validate(root, stage)
        return {"status": "already_completed", "stage": stage}
    need(len(completed) < len(STAGES) and STAGES[len(completed)] == stage, "이전 단계를 먼저 완료해야 합니다")
    validate(root, stage)
    completed[stage] = {"at": now(), "hashes": fingerprints(root, stage)}
    write(root / "run.json", state)
    return {"status": "completed", "stage": stage}


def editor_plan(root):
    state = state_check(root)
    need("factcheck" in state["completed"], "팩트체크 단계 완료 전 에디터 진입 금지")
    validate(root, "factcheck")
    final = read(root / "final_post.json")
    images = {i["id"]: i for i in read(root / "image_map.json")["images"]}
    body = FACT.sub("", final["body"])
    events = []
    start = 0
    for marker in MARKER.finditer(body):
        if body[start:marker.start()].strip():
            events.append({"type": "markdown", "content": body[start:marker.start()].strip()})
        item = images[f"IMAGE_{marker[1]}"]
        events.append({"type": "image", **item})
        start = marker.end()
    if body[start:].strip():
        events.append({"type": "markdown", "content": body[start:].strip()})
    plan = {"status": "prepared_not_entered", "final_sha256": digest(root / "final_post.json"),
            "image_map_sha256": digest(root / "image_map.json"), "title": FACT.sub("", final["title"]),
            "events": events, "hashtags": final["hashtags"], "category": final["category"], "thumbnail": "IMAGE_01"}
    write(root / "editor_plan.json", plan)
    return {"status": plan["status"], "events": len(events)}


def content_key(root):
    final = read(root / "final_post.json")
    content = FACT.sub("", MARKER.sub("", final["title"] + "\n" + final["body"]))
    return hashlib.sha256(re.sub(r"\s+", " ", content).strip().encode()).hexdigest()


def ledger_action(root, database, action):
    state = state_check(root)
    need("editor" in state["completed"], "에디터 검증 후 발행 예약 가능")
    validate(root, "editor")
    Path(database).parent.mkdir(parents=True, exist_ok=True)
    run_id = state["id"]
    key = content_key(root)
    with sqlite3.connect(database, timeout=10) as db:
        db.execute("CREATE TABLE IF NOT EXISTS posts (content_key TEXT PRIMARY KEY, run_id TEXT UNIQUE, status TEXT NOT NULL, url TEXT UNIQUE, updated_at TEXT NOT NULL)")
        if action == "reserve":
            try:
                db.execute("INSERT INTO posts VALUES (?, ?, 'pending', NULL, ?)", (key, run_id, now()))
            except sqlite3.IntegrityError as exc:
                raise GateError("이미 예약/발행된 실행 또는 원고입니다. 원격 게시물을 확인하고 자동 재발행하지 마세요") from exc
            return {"status": "pending", "message": "원격 발행 전 예약됨; 실패·시간 초과 시 기존 게시물부터 확인"}
        validate(root, "publish")
        publication = read(root / "publication.json")
        published_url = canonical_post_url(publication["url"])
        row = db.execute("SELECT status, url FROM posts WHERE content_key=? AND run_id=?", (key, run_id)).fetchone()
        need(row is not None, "reserve 없이 발행 결과를 기록할 수 없습니다")
        need(row[0] != "published" or row[1] == published_url, "기존 발행 URL과 충돌")
        try:
            db.execute("UPDATE posts SET status='published', url=?, updated_at=? WHERE content_key=? AND run_id=?", (published_url, now(), key, run_id))
        except sqlite3.IntegrityError as exc:
            raise GateError("다른 실행에 이미 기록된 게시물 URL") from exc
    return advance(root, "publish")


def default_style():
    return {"name": "기본 정보형 한국어", "mode": "default", "samples": [],
            "title_rules": ["확인한 이득과 조건을 구체적으로 제시", "과장 없이 제목 5안 비교"],
            "intro_rules": ["첫 문단에 핵심 결론과 적용 대상 요약"],
            "body_rules": ["짧은 문단과 설명형 소제목", "비교가 있는 경우 2~3열 표", "본문 1,800자 이상"],
            "tone_rules": ["차분한 해요체", "직접 겪지 않은 경험을 주장하지 않기"],
            "visual_rules": ["8~10개 이미지 슬롯", "대표 이미지 1080x1080"],
            "limitations": "벤치마크 미제공; 모바일 줄 수는 기기와 글꼴에 따라 달라짐"}


def export_run(root, output):
    root = root.resolve()
    state = state_check(root)
    need(list(state["completed"]) == list(STAGES[:5]), "팩트체크까지 완료한 새 원고만 PC로 내보낼 수 있습니다")
    need(not (root / "browser_progress.json").exists(), "이미 입력한 실행은 재입력용으로 내보내지 않습니다")
    validate(root, "factcheck")
    names = {"run.json"}
    for stage in STAGES[:5]:
        names.update(FILES[stage])
    for item in read(root / "image_map.json")["images"]:
        asset_path(root, item["file_path"])
        names.add(str(Path(item["file_path"])))
    need(output.suffix.lower() == ".zip", "출력 파일은 .zip이어야 합니다")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(names):
            archive.write(root / name, arcname=Path(name).as_posix())
    return {"status": "exported", "files": len(names), "archive": str(output), "sha256": digest(output),
            "note": "로그인 세션·브라우저 설정·DB는 포함하지 않습니다. 검토 유효기간은 유지됩니다."}


def analyze_sample(source, output):
    text = Path(source).read_text(encoding="utf-8")
    need(len(text.strip()) >= 200, "분석할 본문이 너무 짧습니다 (최소 200자)")
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    sentences = [s.strip() for s in re.split(r"[.!?。！？]+\s*|\n", text) if s.strip()]
    counts = {"해요체": 0, "격식체": 0, "음슴체": 0, "기타": 0}
    for sentence in sentences:
        ending = re.sub(r"[\s\"'’”]+$", "", sentence)
        key = "격식체" if re.search(r"(니다|니까)$", ending) else "해요체" if ending.endswith("요") else "음슴체" if re.search(r"(음|슴|임)$", ending) else "기타"
        counts[key] += 1
    result = {"status": "measurements_only", "sample_sha256": digest(source), "sample_name": Path(source).name,
              "characters": len(text), "paragraphs": len(paragraphs),
              "mean_paragraph_characters": round(sum(map(len, paragraphs)) / len(paragraphs), 1),
              "sentence_units": len(sentences), "ending_counts_heuristic": counts,
              "ending_percent_heuristic": {k: round(v * 100 / len(sentences), 1) for k, v in counts.items()},
              "limitations": "문장 분할·종결어미는 단순 규칙 추정. 제목/인용/목록을 본문과 분리하고 에이전트가 표본을 검토해야 함."}
    write(output, result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("run", type=Path)
    init.add_argument("--profile", type=Path)
    for name in ("validate", "advance", "rewind"):
        p = sub.add_parser(name)
        p.add_argument("run", type=Path)
        p.add_argument("stage", choices=STAGES)
    for name in ("status", "plan"):
        sub.add_parser(name).add_argument("run", type=Path)
    for name in ("reserve", "record"):
        p = sub.add_parser(name)
        p.add_argument("run", type=Path)
        p.add_argument("--db", type=Path, default=Path(".local/publications.sqlite3"))
    sample = sub.add_parser("analyze-sample")
    sample.add_argument("source", type=Path)
    sample.add_argument("output", type=Path)
    sub.add_parser("doctor")
    export = sub.add_parser("export")
    export.add_argument("run", type=Path)
    export.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            need(not args.run.exists(), "실행 폴더가 이미 있습니다. 새 경로를 사용하세요")
            profile = read(args.profile) if args.profile else default_style()
            args.run.mkdir(parents=True)
            write(args.run / "run.json", {"id": os.urandom(16).hex(), "created_at": now(), "completed": {}})
            write(args.run / "style_profile.json", profile)
            result = advance(args.run, "style")
        elif args.command == "analyze-sample":
            result = analyze_sample(args.source, args.output)
        elif args.command == "export":
            result = export_run(args.run, args.output)
        elif args.command == "doctor":
            result = {"python": sys.version.split()[0], "local_pipeline": "available",
                      "live_search": "requires_agent_web_tools_or_accessible_sources",
                      "naver_browser": "not_connected", "live_publish": "not_tested",
                      "credentials": "not_required_for_local_commands"}
        elif args.command == "status":
            state = state_check(args.run)
            completed = list(state["completed"])
            result = {"completed": completed, "next": STAGES[len(completed)] if len(completed) < len(STAGES) else None}
        elif args.command == "validate":
            state_check(args.run)
            validate(args.run, args.stage)
            result = {"status": "valid", "stage": args.stage, "scope": "structure_and_evidence_records_only"}
        elif args.command == "advance":
            need(args.stage != "publish", "발행 완료는 record 명령으로 기록하세요")
            result = advance(args.run, args.stage)
        elif args.command == "rewind":
            state = read(args.run / "run.json")
            need("publish" not in state["completed"], "발행 완료 실행은 수정하지 말고 새 실행을 만드세요")
            index = STAGES.index(args.stage)
            state["completed"] = {s: r for s, r in state["completed"].items() if STAGES.index(s) < index}
            write(args.run / "run.json", state)
            result = {"status": "rewound", "next": args.stage, "artifacts": "preserved", "publication_reservations": "preserved"}
        elif args.command == "plan":
            result = editor_plan(args.run)
        else:
            result = ledger_action(args.run, args.db, args.command)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (GateError, OSError, KeyError, TypeError, ValueError, sqlite3.Error) as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
