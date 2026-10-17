# Homepan 블로그 제작 스킬

마스터 오케스트레이터 1개와 독립 스킬 7개, 단계별 산출물 검증 CLI입니다. 벤치마크 블로그를 나중에 받아 스타일 프로필을 교체할 수 있습니다.

현재 구현은 **에이전트가 실행하는 스킬 + 로컬 상태 관리 도구**입니다. Python CLI 자체가 검색/LLM/네이버 브라우저를 호출하는 무인 서비스는 아닙니다. 실제 네이버 에디터 어댑터와 로그인 연결은 추가 작업이며, 실게시 검증은 아직 수행하지 않았습니다.

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

Python 3.11 이상. 런타임 외부 패키지·API 키는 필요 없습니다.

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

마지막 명령은 이미지 업로드 위치와 서식용 텍스트가 포함된 `editor_plan.json`을 만듭니다. 브라우저에 입력하지는 않습니다. 실제 입력·검증 후 에이전트가 `editor_receipt.json`을 작성해야 다음 단계로 진행할 수 있습니다.

```bash
python -m homepan advance runs/first-post editor
python -m homepan reserve runs/first-post
# 사용자에게 발행 권한을 받은 브라우저 작업이 여기서 실제 발행·공개 페이지 확인을 수행
# 그 결과를 publication.json에 작성한 다음 기록
python -m homepan record runs/first-post
```

기본 중복 방지 DB는 `.local/publications.sqlite3`입니다. 동일 원고나 실행을 다시 예약하면 차단합니다. 이 도구는 발행 버튼을 클릭하지 않으며, 기록 JSON의 진실성을 독립적으로 입증하지 않습니다.

## 벤치마크 추가

사용 예: “`blog-style-cloner`로 이 블로그의 최근 글을 분석해서 `profiles/my-style`에 저장해 줘.”

1. 접근 가능한 글 3~5개 또는 제공된 본문을 분석합니다. 1개도 가능하지만 한계를 기록합니다.
2. 본문 통계 보조 도구: `python -m homepan analyze-sample 본문.txt measurements.json`.
3. 스킬이 측정 결과와 직접 검토를 합쳐 `style_profile.json`과 `.md`를 작성합니다.
4. 새 실행에 `python -m homepan init runs/next-post --profile profiles/my-style/style_profile.json`으로 적용합니다.

원문, 실행 결과, 로그인 자료는 기본적으로 Git에서 제외됩니다. 추후 버전 관리가 필요하면 개인정보 없는 프로필만 검토해서 추적 대상으로 추가하세요.

## 검증과 제한

테스트는 합성 자료로 상태 전환, 해시 변경 감지, 이미지 누락·경로 이탈·사용 권한 상태, 검증 미통과, 오래된 조사, 중복 발행 기록을 확인합니다. **실시간 조사·사실 여부·실제 에디터·실제 발행의 통합 테스트는 아닙니다.**

- 2만 PV, 홈판 노출, CTR, 3분 체류, 무오류는 보장할 수 없습니다.
- 타이핑 위장이나 가짜 오타는 구현하지 않습니다. 이미지 색상 변경이 저작권·중복 문제를 해결하지 않습니다.
- 지금 필요한 외부 연결과 확인 결과는 [연동 상태와 한계](docs/integrations.md)에 정리했습니다.
- 완료된 산출물을 수정하면 `python -m homepan rewind runs/first-post <단계>` 후 해당 단계부터 다시 검증합니다. 발행 예약 DB는 자동 삭제하지 않습니다.
- 단일 실행 상태 파일은 한 에이전트가 기록합니다. 여러 머신의 게시 중복 방지에는 공유 DB와 추가 동시성 제어가 필요합니다.
