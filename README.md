# Homepan 블로그 제작 스킬

마스터 오케스트레이터 1개와 독립 스킬 7개, 단계별 산출물 검증 CLI입니다. 벤치마크 블로그를 나중에 받아 스타일 프로필을 교체할 수 있습니다.

현재 구현은 **에이전트 스킬 + 로컬 상태 관리 + 본인 PC의 브라우저 입력·발행 도구**입니다. Windows 전용 프로필에서 직접 로그인하고 실제 편집 요소를 지정해 연결합니다. 화면 확인이 포함된 반자동 방식이며, 실제 네이버에서의 첫 입력·발행 검증은 아직 수행하지 않았습니다.

Windows 사용자는 [전용 브라우저 설치·로그인 안내](docs/windows-browser.md)부터 시작하세요. `scripts/setup-windows.cmd`로 설치하고 `scripts/login-windows.cmd`로 로그인합니다. 네이버 화면을 실제로 연결하기 전에는 검증되지 않은 선택자로 입력하지 않습니다.

## 스킬

|호출 이름|역할|
|---|---|
|[homepan-orchestrator](.agents/skills/homepan-orchestrator/SKILL.md)|전체 단계 조율·중단·재개|
|[blog-style-cloner](.agents/skills/blog-style-cloner/SKILL.md)|벤치마크 스타일 프로필 추출|
|[realtime-homefeed-radar](.agents/skills/realtime-homefeed-radar/SKILL.md)|근거 있는 소재 5개와 최종 주제 선정|
|[homefeed-viral-writer](.agents/skills/homefeed-viral-writer/SKILL.md)|제목 5안·원고·이미지 슬롯·주장 목록|
|[smart-image-curator](.agents/skills/smart-image-curator/SKILL.md)|이미지 파일·권리·출처·캡션 매핑|
|[zero-defect-factchecker](.agents/skills/zero-defect-factchecker/SKILL.md)|주장별 검토와 최종 원고 연결|
|[human-typing-injector](.agents/skills/human-typing-injector/SKILL.md)|편집 계획 생성과 연결된 브라우저 입력 절차|
|[auto-publisher-pro](.agents/skills/auto-publisher-pro/SKILL.md)|발행 절차·결과 확인·중복 방지 기록|

저장소 스킬을 지원하는 에이전트에서 `$homepan-orchestrator`처럼 호출합니다. 현재 세션에서 새 스킬을 자동 발견하지 못하면 해당 SKILL.md를 읽도록 요청하거나 새 작업에서 저장소를 엽니다. 로컬 파일 생성만으로 외부 서비스에 스킬이 설치되는 것은 아닙니다.

## 실행

Python 3.11 이상. 코어 CLI에는 외부 패키지·API 키가 필요 없습니다. 브라우저 기능은 `.[browser]` 추가 패키지와 Chromium이 필요합니다.

```bash
cd /workspace/homepan
python -m homepan doctor
python -m unittest discover -s tests -v
python -m homepan init runs/first-post
python -m homepan status runs/first-post
```

`init`은 기본 스타일과 상태 파일만 만듭니다. 이후 스킬이 실제 조사와 집필을 수행하여 [데이터 계약](docs/contracts.md)에 맞는 JSON을 저장합니다. 각 단계의 완료는 다음과 같이 기록합니다.

```bash
python -m homepan advance runs/first-post topic
python -m homepan advance runs/first-post draft
python -m homepan advance runs/first-post images
python -m homepan advance runs/first-post factcheck
python -m homepan plan runs/first-post
```

마지막 명령은 이미지 업로드 위치와 서식용 텍스트가 포함된 `editor_plan.json`을 만듭니다. 이 코어 명령 자체는 브라우저에 입력하지 않습니다. PC의 `python -m homepan.browser write runs/first-post`가 별도 프로필의 브라우저를 열어 입력하고, 화면 검토 후 `editor_receipt.json`과 editor 완료 상태를 기록합니다.

클라우드에서 만든 원고는 `python -m homepan export runs/first-post exports/first-post.zip`으로 묶어 PC에 전달할 수 있습니다. 로그인 세션은 ZIP에 포함하지 않습니다.

```bash
python -m homepan advance runs/first-post editor
python -m homepan reserve runs/first-post
# 사용자에게 발행 권한을 받은 브라우저 작업이 여기서 실제 발행·공개 페이지 확인을 수행
# 그 결과를 publication.json에 작성한 다음 기록
python -m homepan record runs/first-post
```

코어 CLI의 기본 중복 방지 DB는 `.local/publications.sqlite3`이고, Windows 브라우저 도구는 `%LOCALAPPDATA%\Homepan\publications.sqlite3`를 사용합니다. 두 도구를 함께 사용하면 코어 명령의 `--db`에 브라우저 DB를 지정하세요. 동일 원고나 실행을 다시 예약하면 차단합니다. 위 코어 명령은 발행 버튼을 클릭하지 않습니다. 브라우저의 `review ... --publish`는 화면 검토와 명시적인 발행 확인 후 한 번 클릭하고 비로그인 공개 페이지를 검증합니다.

## 벤치마크 추가

사용 예: “`blog-style-cloner`로 이 블로그 홈을 분석해서 `profiles/my-style`에 저장해 줘.” 기본값은 공개 최신 글 10개입니다. 사용자가 인기글을 지정하면 실제 인기글 목록을 기준으로 하며, 여러 블로그는 각각 분석한 뒤 비교합니다.

1. 기본 최신글 모드에서는 최신 공개 글 10개의 주제 적합도를 먼저 확인합니다. `fit` 글 중 최대 5편을 문체 표본으로 분석하며, 3편 미만이면 표본 수와 한계를 기록합니다.
2. 본문 통계 보조 도구: `python -m homepan analyze-sample 본문.txt measurements.json`.
3. 스킬이 측정 결과와 직접 검토를 합쳐 `style_profile.json`과 `.md`를 작성합니다.
4. 새 실행에 `python -m homepan init runs/next-post --profile profiles/my-style/style_profile.json`으로 적용합니다.

“인기글 10개씩” 요청은 선정된 10개 모두를 분석하며, 최신글 모드의 최대 5편 표본 제한을 적용하지 않습니다. UI에 공개된 인기글이 10개 미만이면 있는 글만 분석하고 부족 수를 기록합니다. 인기글이 보이지 않으면 최신글이나 공감 수로 대체하지 않습니다. [현재 10개 블로그 분석 요청과 접근 상태](benchmarks/homefeed-popular/README.md)를 참고하세요. 이 절차는 에이전트 스킬이며 Windows 브라우저 CLI에 인기글 자동 수집 명령을 추가하는 것은 아닙니다.

원문, 실행 결과, 로그인 자료는 기본적으로 Git에서 제외됩니다. 추후 버전 관리가 필요하면 개인정보 없는 프로필만 검토해서 추적 대상으로 추가하세요.

## 검증과 제한

테스트는 합성 자료로 상태 전환, 해시 변경 감지, 이미지 누락·경로 이탈·사용 권한 상태, 검증 미통과, 오래된 조사, 중복 발행 기록을 확인합니다. 브라우저 추가 패키지가 있으면 실제 Chromium에서 서식 붙여넣기, 파일 업로드, 프로필 지속성, 제한 화면 중단, 단일 발행 클릭·실패 복구도 검사합니다. **실시간 조사·사실 여부·실제 네이버의 통합 테스트는 아닙니다.** 브라우저 의존성이 없으면 해당 테스트는 skipped로 표시합니다.

- 2만 PV, 홈판 노출, CTR, 3분 체류, 무오류는 보장할 수 없습니다.
- 타이핑 위장이나 가짜 오타는 구현하지 않습니다. 이미지 색상 변경이 저작권·중복 문제를 해결하지 않습니다.
- 지금 필요한 외부 연결과 확인 결과는 [연동 상태와 한계](docs/integrations.md)에 정리했습니다.
- 완료된 산출물을 수정하면 `python -m homepan rewind runs/first-post <단계>` 후 해당 단계부터 다시 검증합니다. 발행 예약 DB는 자동 삭제하지 않습니다.
- 단일 실행 상태 파일은 한 에이전트가 기록합니다. 여러 머신의 게시 중복 방지에는 공유 DB와 추가 동시성 제어가 필요합니다.
