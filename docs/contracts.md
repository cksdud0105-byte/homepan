# 단계별 데이터 계약 v1

모든 JSON은 UTF-8 object입니다. 경로의 기준은 실행 폴더 `runs/<ID>/`입니다. 시간은 `2026-10-18T09:00:00+09:00`처럼 시간대를 포함한 ISO 8601로 기록합니다. 예시 날짜나 상태를 검증 없이 복사하지 않습니다.

CLI는 신뢰하는 로컬 에이전트의 산출물에 대한 구조·일관성 검사기입니다. 외부 조회, 문장 의미의 진실성, 라이선스 해석, 브라우저 화면 확인은 스킬을 실행하는 에이전트가 수행합니다. 검사 성공은 증거를 실제로 읽었다는 증명이 아닙니다.

## 공통 상태와 명령

순서: `style → topic → draft → images → factcheck → editor → publish`.

```bash
python -m homepan init runs/<ID>
python -m homepan status runs/<ID>
python -m homepan validate runs/<ID> <단계>
python -m homepan advance runs/<ID> <단계>
python -m homepan rewind runs/<ID> <단계>
```

- `init`: 새 실행 폴더, run.json, 기본 style_profile.json을 만들고 style을 완료합니다. 기존 경로는 덮어쓰지 않습니다.
- `validate`: 요청 단계까지 모든 선행 산출물을 검사하며 완료 기록을 바꾸지 않습니다.
- `advance`: 선행 단계 완료, 기록한 해시 불변, 현재 계약을 확인하고 완료 기록을 추가합니다.
- `status`: 산출물 변경 여부와 다음 단계를 표시합니다. 시의성 검토는 validate/advance/plan에서 다시 확인합니다.
- `rewind`: 해당 단계부터 완료 기록만 제거합니다. 산출물·DB 예약은 보존합니다. 발행 완료 실행은 되돌릴 수 없습니다.
- 오류는 JSON `status=blocked`와 종료 코드 1로 반환합니다. 성공은 종료 코드 0입니다.
- `run.json`은 도구가 관리합니다. 직접 고쳐 게이트를 건너뛰지 않습니다. 여러 에이전트가 같은 상태 파일을 동시에 수정하지 않습니다.

## 출처 object

```json
{
  "id": "S01",
  "url": "https://공식출처의-실제-주소",
  "title": "실제 문서 제목",
  "published_at": "시간대 포함 실제 게시 시각",
  "accessed_at": "실제로 조회한 시각",
  "evidence": "해당 주장을 뒷받침하는 구체적 문구 또는 표의 행"
}
```

URL은 인증 정보가 없는 HTTPS를 사용합니다. 게시일을 확인할 수 없으면 추정 날짜를 만들지 말고 다른 자료를 찾거나 조사 단계를 차단합니다. 출처에서 날짜만 제공하는 경우 원문 날짜 정밀도(`published_precision=date`)를 별도로 표시하고 그 날짜의 현지 자정을 정규화 값으로 사용합니다.

## 1. style_profile.json

필수: `name`, `mode`(`default`/`benchmark`), `title_rules`, `intro_rules`, `body_rules`, `tone_rules`, `visual_rules`.

각 rules는 비어 있지 않은 배열입니다. benchmark 모드에는 비어 있지 않은 `samples`, 분석 한계인 `limitations`가 필요합니다. samples 항목에는 원본 URL/파일명, 조회 시각, 본문 해시, 측정 근거를 저장합니다.

권장: revision, updated_at, metrics, excluded_patterns. 통계 보조 명령:

```bash
python -m homepan analyze-sample profiles/example/samples/post.txt profiles/example/measurements.json
```

이 명령은 통계만 만들며 완성 프로필이나 맞춤 스킬을 자동 생성하지 않습니다. 스킬이 표본을 검토하고 작성 규칙으로 변환합니다.

## 2. topic_brief.json

필수: researched_at, sources, candidates, selected_id.

각 후보의 필수 필드:

```json
{
  "id": "T01",
  "keyword": "주제",
  "angle": "독자에게 설명할 구체적인 관점",
  "audience": "독자층",
  "reason": "선정 근거",
  "timeliness_reason": "최근 변화 또는 다가오는 일정의 근거",
  "source_ids": ["S01"],
  "editorial_score": 80
}
```

후보는 서로 다른 ID로 정확히 5개입니다. selected_id는 존재하는 후보여야 합니다. 점수는 0~100의 편집 판단이며 확률/실측 CTR이 아닙니다. 조사 시각은 48시간 내여야 하며 미래 값은 5분의 시계 오차만 허용합니다. 출처 조회 시각 역시 조사 시각으로부터 48시간 내여야 합니다. 권장: score_breakdown, selection_reason, source_limitations.

## 3. draft.json

필수: title_candidates(서로 다른 제목 5개), title(그중 하나), body, claims. 추가 조사 출처는 sources에 저장합니다.

body는 Markdown입니다. 이미지·팩트 마커를 제외하고 최소 1,800자입니다. `[IMAGE_01: 설명]` 또는 `[IMAGE_01]`부터 8~10개를 순서대로 사용합니다. 동일 슬롯을 반복하거나 번호를 건너뛰지 않습니다.

검증할 문장 바로 뒤에 `[FACT:C01]`을 쓰고 다음과 같이 연결합니다.

```json
{
  "id": "C01",
  "text": "본문이나 제목에 실제 포함된 검증할 구절",
  "source_ids": ["S01"]
}
```

마커 ID와 claims ID 집합이 일치해야 합니다. 출처 ID는 topic 또는 draft의 sources에 있어야 합니다. 원문 인용은 사실 확인에 필요한 짧은 범위로 사용하고 전체 기사나 벤치마크 글을 복제하지 않습니다.

## 4. image_map.json

```json
{
  "images": [{
    "id": "IMAGE_01",
    "file_path": "images/01.png",
    "sha256": "실제 파일의 SHA-256",
    "source_url": "https://실제-출처",
    "rights_status": "cleared",
    "rights_basis": "확인한 이용 허락과 조건; 라이선스 URL/직접 제작 근거",
    "credit": "필요한 출처·저작자 표시; 불필요하면 그 근거",
    "alt_text": "이미지를 설명하는 텍스트",
    "caption": "본문 맥락에 맞는 검증된 캡션"
  }]
}
```

배열은 원고 슬롯 전체와 순서가 같아야 합니다. file_path는 실행 폴더 내부의 파일이며 외부로 나가는 심볼릭 링크도 차단합니다. PNG/JPEG 헤더를 읽어 크기를 검사합니다. 대표 이미지는 1080x1080, 나머지는 최소 800x600입니다. 같은 파일 해시는 중복 사용할 수 없습니다.

이 헤더 검사는 이미지 전체 디코딩·내용·권리를 검증하지 않습니다. 스킬 실행자가 실제 이미지를 열어 확인합니다. 개별 파일과 매핑 JSON 모두 완료 기록의 해시에 포함됩니다.

해시 계산 예:

```bash
python -c 'from homepan.cli import digest; print(digest("runs/<ID>/images/01.png"))'
```

## 5. final_post.json / factcheck.json

최종 원고 필수: title, body, claims, hashtags, category, thumbnail.

- body·claims는 draft와 같은 계약을 따릅니다.
- 이미지 슬롯은 이미지 단계에서 검증한 초안과 같아야 합니다.
- hashtags는 공백과 `#` 없이 중복 없는 문자열 정확히 10개입니다.
- thumbnail은 `IMAGE_01`입니다. category는 실제 대상 블로그의 분류와 대응시킵니다.

팩트체크 리포트:

```json
{
  "status": "passed",
  "checked_at": "실제 검토 시각",
  "coverage_reviewed": true,
  "final_sha256": "확정 final_post.json 파일 해시",
  "image_map_sha256": "확정 image_map.json 파일 해시",
  "sources": ["위 출처 object 형식의 실제 객체들"],
  "claims": [{
    "id": "C01",
    "verdict": "verified",
    "source_ids": ["S01"],
    "reason": "확인한 근거와 적용 범위"
  }],
  "unresolved": [],
  "errors": [],
  "numeric_review": "계산·단위 확인 내용 또는 해당 없음의 이유",
  "date_review": "날짜·시행 시점 확인 내용",
  "legal_review": "법령/정책 확인 내용 또는 해당 없음의 이유",
  "causality_review": "인과·상관·전망 구별 내용",
  "caption_review": "이미지와 캡션의 정확성 확인 내용"
}
```

위 예시는 필드 설명이며 통과한 리포트가 아닙니다. sources 배열에 실제 객체를 작성해야 합니다. 최종 claims와 검증 claims가 정확히 대응하고 모두 verified여야 하며, 최종 주장의 source_ids도 리포트 sources에 존재해야 합니다. 검토는 24시간 내여야 합니다. 미해결 항목은 빈 배열이어야 합니다. 해시 계산 후 최종 파일을 수정하면 검토를 다시 수행합니다.

## 6. editor_plan.json / editor_receipt.json

```bash
python -m homepan plan runs/<ID>
```

plan은 도구가 생성합니다. 상태는 `prepared_not_entered`이며 제목, 해시, 이미지/Markdown 이벤트, 태그, 카테고리, 대표 이미지를 담습니다. FACT 마커는 제거하고 IMAGE 마커는 이미지 이벤트로 변환합니다. Markdown 이벤트는 브라우저 어댑터가 실제 서식으로 변환해야 합니다.

실제 입력 후 editor_receipt에 다음 필드를 작성합니다.

```json
{
  "status": "verified",
  "final_sha256": "실제 원고 해시",
  "image_map_sha256": "실제 매핑 해시",
  "checked_at": "실제 확인 시각",
  "evidence": "실제로 확인한 화면/DOM 및 비교 결과",
  "title_matches": true,
  "body_matches": true,
  "images_match": true,
  "formatting_checked": true,
  "thumbnail_matches": true,
  "tags_match": true,
  "category_matches": true
}
```

실제 UI를 확인하지 않고 예시의 true를 복사하지 않습니다. 불일치는 수정하거나 차단 사유로 보고합니다.

## 7. publication.json / 중복 방지 DB

최종 발행 버튼을 누르기 전에 `reserve`를 수행합니다. 동일 실행이나 동일 제목·본문은 하나만 예약할 수 있습니다. 내용 키는 검증용 마커와 여백 차이를 정규화한 해시입니다. 이미지·태그만 바뀐 동일 본문도 중복으로 간주합니다.

실제 발행과 공개 페이지 확인 후:

```json
{
  "status": "published",
  "url": "https://blog.naver.com/실제블로그ID/실제게시물번호",
  "published_at": "실제 발행 시각",
  "final_sha256": "발행한 최종 원고 해시",
  "public_page_verified": true,
  "evidence": "실제 URL과 본문·이미지를 확인한 결과"
}
```

`python -m homepan record runs/<ID>`가 URL을 정규화하고 DB를 갱신한 다음 publish 단계를 완료합니다. `advance ... publish`는 허용하지 않습니다. 모바일 URL과 PostView.naver URL도 같은 게시물 주소로 정규화합니다.

DB와 run.json 사이에서 중단되더라도 같은 URL을 record하면 멱등적으로 재개할 수 있습니다. reserve 후 응답을 잃으면 새 예약을 만들거나 DB를 지우지 말고 실제 게시 여부를 확인합니다. 예약 발행 접수는 published가 아닙니다.
