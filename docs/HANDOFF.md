# 인수인계 계획서

이 문서 하나만 읽으면 작업을 이어받을 수 있도록 정리했습니다. 사람이 읽어도
되고, Claude Code나 GPT Codex 같은 코딩 에이전트에게 그대로 물려줘도 됩니다.

- 대상 브랜치: `main`
- 상태: v1.7.0 릴리스 준비 완료
- 현재 개발 범위: PDF 제목 복구와 모델 선택 시 용도·환각 위험·요약 전략 안내,
  8B 미만 계층형 요약 및 8B 이상 고급 분석 경계, 한국어 자연어 검색의 영문
  원문 검색어 확장과 설치 Ollama 모델 우선 사용, PC 사양별 모델 상주 정책,
  PaperPack 등록/분석 시각 표시와 원문 보존형 한국어 번역 캐시

---

## 1. 개발 환경 준비

Python 3.12 이상이 필요합니다. sPDF는 submodule이라 `--recurse-submodules` 없이
clone 했다면 반드시 따로 받아야 합니다. 받지 않으면 `test_spdf_bridge`가 실패합니다.

```bash
git submodule update --init
```

```bash
python -m venv .venv
```

```bash
.venv\Scripts\python -m pip install -e ".[gui]"
```

```bash
.venv\Scripts\python -m unittest discover -s tests
```

GUI 실행은 다음과 같습니다.

```bash
.venv\Scripts\python -m paper_organizer.gui
```

설치 프로그램까지 만들려면 `.[gui,build]`로 설치한 뒤 Inno Setup 6를 깔고
`build_installer.bat`을 실행합니다. 아이콘을 다시 만들 일이 있으면
`python scripts/make_icon.py`를 실행하면 됩니다(Pillow 필요, build extras에 포함).

---

## 2. 코드 지도

레이어가 나뉘어 있고, 위 레이어만 아래를 참조합니다.

| 레이어 | 위치 | 역할 |
|---|---|---|
| core | `paper_organizer/core/` | PDF 지문·판별, paperpack ZIP, 분류기, 검색 색인, 통합 인덱스 |
| application | `paper_organizer/application/` | 워크플로 컨트롤러, 분석 큐, 백그라운드 실행기, 요약 서비스 |
| ui | `paper_organizer/ui/` | PyQt5 위젯과 다이얼로그 |
| infra | `paper_organizer/infra/` | 설정, 비밀키, Ollama 런타임·설치 |
| providers | `paper_organizer/providers/` | Ollama / OpenAI / Anthropic 요약 제공자 |

가장 자주 건드리게 되는 파일은 이 셋입니다.

- `application/library_workflow.py` — 스캔·자동 보관·분석 결과 저장·검색까지 대부분의 흐름이 여기 있습니다.
- `ui/library_workflow_widget.py` — 수집 화면, 분석 큐, 라이브러리 위젯.
- `core/paperpack.py` — 저장 포맷. 여기를 고칠 때는 `PAPERPACK_FORMAT.md`도 같이 고쳐야 합니다.

---

## 3. 자동 처리 흐름

```
다운로드 폴더
  → scan()            안정성 2회 확인 → 본문 지문 → 중복 후보 검사 → 학술 논문 판정
  → _auto_organize()  academic_likely + 중복 없음이면 승인 없이 보관
       · 정규식 분류기가 분야·세부분야·저널명을 채움 (field_sources = "auto:regex")
       · 페이지 본문을 content/content.json에 저장
       · papers/<분야>/<세부분야>/x.paperpack 생성, 분석 큐에 등록
  → BackgroundAnalysisService.run_next()   한 편씩 AI 분석
       · 반복 머리말·쪽번호·OCR 잡음을 제거하고 논문 구역별 문단 컨텍스트 구성
       · 4B 이하는 구역별 요약→최종 요약, 8B 이상은 전체 구역 직접 요약
       · 첫 페이지 서지 추출 + 요약·분류 정정 (field_sources = "ai:<provider>")
       · 분류가 바뀌면 paperpack을 새 분야 폴더로 이동
  → 검색 색인(index/search.sqlite) 증분 갱신
```

중복 후보가 있거나 판정이 불확실한 PDF는 자동 처리하지 않고 수집 화면에 남습니다.
이 동작은 설정의 `auto_organize_academic`으로 끌 수 있습니다.

---

## 4. 반드시 지켜야 할 원칙

이걸 어기면 데이터가 깨지거나 사용자 수정이 날아갑니다. 새 기능을 넣을 때마다
확인하세요.

**1) `.paperpack`이 유일한 원본입니다.**
`index/library.json`과 `index/search.sqlite`는 언제든 지우고 다시 만들 수 있는
파생 캐시입니다. 캐시에만 있고 paperpack에는 없는 데이터를 만들지 마세요.

**2) 필드 우선순위는 `auto:regex` < `ai:*` < `user` 입니다.**
`curation.field_sources`에 각 필드를 누가 채웠는지 기록합니다. 사람이 고친
필드(`user`)는 AI가 절대 덮어쓰지 않습니다. 이 판정은
`LibraryWorkflowController._apply_ai_bibliography()` 한 곳에 모아 뒀으니, 새로운
자동 채움을 추가할 때도 그 함수를 거치게 하세요.

**3) 파일을 지우거나 옮기는 동작은 사용자 승인을 받습니다.**
중복 파일은 영구 삭제하지 않고 앱 휴지통(`trash/`)으로 옮기고 복원 경로를 남깁니다.
입력 PDF 삭제는 기본이 꺼져 있습니다.

**4) `ui/` 밖에서는 PyQt5를 import 하지 않습니다.**
core·application 테스트가 PyQt 없이도 돌아야 합니다. UI가 필요한 값은 시그널이나
반환값으로 넘기세요.

**5) 쓰기는 원자적으로 합니다.**
임시 파일에 다 쓰고 `os.replace`로 교체하는 패턴을 씁니다
(`_atomic_json_write`, `create_paperpack` 참고). 중간에 실패해도 기존 파일이
손상되지 않아야 합니다.

---

## 5. 이미 밟아본 지뢰

같은 곳에서 두 번 넘어지지 않도록 남깁니다.

**분석 큐의 선택 항목은 행 번호로 찾으면 안 됩니다.**
헤더 정렬을 켜면 행 순서가 바뀌어 엉뚱한 논문이 삭제·분석됩니다. 지금은
`Qt.UserRole`에 `queue_id`를 심어 조회합니다. 테이블에 정렬을 추가할 때마다
같은 처리가 필요합니다.

**`sqlite3.connect()`의 `with` 문은 연결을 닫지 않습니다.**
트랜잭션만 관리합니다. Windows에서 파일이 잠긴 채 남아 임시 폴더 삭제가
실패합니다. `search_index.py`처럼 `contextlib.closing`으로 감싸세요.

**요약 응답 스키마는 필드가 정확히 일치해야 합니다.**
최종 응답은 `SummaryData.from_mapping()`이 `set(raw) != expected`로 검사합니다.
계층형 중간 응답에는 구조화 스키마를 보내지 않고 짧은 평문 근거만 받습니다.
중간 단계에 JSON이나 최종 분류·서지 필드를 다시 넣지 마세요. 최종 스키마를
바꾸면 provider 3개와 관련 테스트 픽스처를 모두 고쳐야 합니다. 4B 이하의
`advanced_analysis=False` 요청은 기여·한계가 없는 축소 스키마를 사용하고,
`SummaryData.from_mapping()`이 두 값을 빈 튜플로 정규화합니다.
최종 JSON은 기본 요청, `json_retry`, `json_repair` 순서로 최대 세 번 시도합니다.
세 번째 요청은 타입이 맞는 빈 JSON 틀과 직렬화 규칙을 명시하는 별도 프롬프트이며,
`json_retry_count`에는 실제 추가 요청 수(최대 2)를 기록합니다.
끝내 실패하면 `BackgroundAnalysisService`가 준비 단계에서 보존한
`RegexSummaryFallback`을 `LibraryWorkflowController.apply_analysis_failure()`에
넘깁니다. PaperPack의 `analysis.fallback`에는 `auto:regex` Abstract·페이지·
DOI/연도 후보만 저장하고, `workflow.analysis_status=failed`와
`needs_reanalysis=true`를 기록합니다. 이 값으로 `description.summary`를 채우지
마세요. 기존 성공 분석은 덮어쓰지 않고 `analysis.last_attempt`에 실패를 남깁니다.
`analysis.*.diagnostics`에는 stage/failure_kind/error_type/provider/model/
request_attempts/analysis_level/summary_strategy/output_language/included_sections를
저장합니다. traceback·API 키·본문은 넣지 않습니다. JSON과 언어 최종 실패는
`SummaryRetryExhaustedError`가 실제 시도 횟수와 안정적인 failure_kind를 전달합니다.

제목·저자·연도·저널은 요약 스키마에 넣지 않습니다.
`BibliographyRequest`가 첫 페이지에 작은 4필드 스키마를 별도로 요청하고,
`_validate_bibliography()`가 원문에 실제로 있는 값만 통과시킵니다.
ResearchGate 등 배포 플랫폼 차단 목록은 입력 정리와 검증에 함께 적용됩니다.

**요약 전처리는 PaperPack 본문을 바꾸지 않습니다.**
`application/summary_preprocessing.py`가 AI로 보낼 임시 컨텍스트만 구역별로
정리합니다. 전문 검색용 `content/content.json`은 원문 페이지 텍스트를 그대로
유지합니다. 긴 입력은 `summary_service._truncate_section_context()`가 각 검출
구역을 남기며 줄입니다.

**테스트용 PDF 본문이 500자 미만이면 `needs_ocr`로 분류됩니다.**
자동 보관 대상에서 빠져 테스트가 조용히 어긋납니다. 픽스처는 실제 논문 분량에
가깝게 쓰세요(`tests/test_auto_organize.py`의 `protein_pages()` 참고).

**수동 승인 흐름을 검증하는 테스트는 자동 보관을 꺼야 합니다.**
`save_paths(..., auto_organize_academic=False)`를 넘기세요. 안 그러면 논문이
자동으로 정리돼 검토 목록이 비어 버립니다.

**외부 실행 파일을 `shutil.which`만으로 찾으면 놓칩니다.**
앱이 상속한 PATH에 `WindowsApps`가 빠져 있으면 winget이 설치돼 있어도 못 찾습니다.
`find_winget_executable()`, `find_ollama_executable()`처럼 기본 설치 경로까지
확인하세요. 새 외부 도구를 붙일 때도 같은 패턴을 쓰면 됩니다.

---

## 6. 다음에 할 일

### (1) 추정 제목 깨짐 수정 — 완료

진단에 사용한 로컬 PDF의 실제 원인은 본문이나 UTF-8 파일 인코딩이 아니라 PDF
메타데이터 제목 `24È£_ÃÖÁ¾_`였습니다. 이를 CP949로
재해석하면 `24호_최종_`이지만 논문 제목이 아닌 제작용 이름이므로 그대로 쓰면
안 됩니다.

`application/library_workflow.py`의 `_default_metadata()`는 이제 UTF-8·CP949
mojibake를 먼저 복구하고, 제어문자·사설영역·대체문자와 `untitled`·`24호_최종_`
같은 작업용 제목을 거부합니다. 유효한 PDF 메타데이터는 계속 우선하며, 거부된
경우 첫 페이지 상단 절반에서 본문보다 충분히 큰 글자 줄을 시각 제목으로
선택한 뒤 기존 본문 첫 줄·파일명 순으로 대체합니다.

실제 로컬 샘플은 `Chaperone Plasmid Set`으로 복구됐습니다.
`tests/test_title_repair.py`가 깨진 한국어 메타데이터, UTF-8 mojibake와 정상
메타데이터 우선순위를 검증하며 전체 285개 테스트가 통과했습니다. 샘플 PDF는
사용자 로컬 진단 자료이므로 Git에 추가하지 않습니다.

### (2) 합성 벤치마크 결과로 기본 모델·프롬프트 결정

`tests/benchmark/README.md`의 실행기로 Qwen3와 Granite 3.3 2B, Phi-4 Mini,
Gemma 3 4B QAT, Ministral 3 3B 등 설치된 후보 모델을 돌린 뒤
`model_summary.csv`, `paper_scores.csv`, `recommendation.json`을 비교합니다.
OCR 문서는 모델 비교에서 제외합니다. 텍스트 레이어 6편의
`critical_negations` 보존과 `forbidden_hits`를 우선 봅니다. 결과 파일은 Git에
올리지 말고, 채택할 모델/컨텍스트 정책과 재현 명령만
문서화하세요.

설치 앱의 정밀 벤치마크 연결은 나중 단계입니다. 기본 사양 추천 모델을 기준으로
바로 아래·기준·바로 위 등급만 후보로 만들고, 사용자가 총 다운로드 용량을 확인해
명시적으로 선택했을 때만 최대 3개를 한 번에 받도록 합니다. 첫 실행에서 전체 모델을
자동 다운로드하거나 벤치마크를 강제하지 않습니다.

현재 선택 UI에는 파라미터 등급별 보수적 안내가 구현되어 있습니다. 0.6B는
벤치마크·분류 보조, 1.7B는 저사양 폴백, 4B급은 구역별 일반 요약, 8B는
기여·한계를 포함한 정밀 요약으로 표시합니다. 이는 절대 품질 점수가 아니므로,
실측 벤치마크가 확보되면 모델별 성공률·환각 점수로 문구를 보정해야 합니다.
Granite 3.3 2B는 공식상 한국어·요약·추출·긴 문서를 지원하지만 앱의 실측
벤치마크 전이므로 Qwen3 1.7B를 자동 추천에서 밀어내지 않는 비교 후보입니다.

### (3) 분류 체계 다듬기

`paper_organizer/models/taxonomy.json`에 학과 20개와 세부 전공이 들어 있습니다.
실제로 쓰다 보면 본인 분야가 잘 안 잡히는 경우가 나올 텐데, 그때는 해당 분야의
`keywords`를 보강하면 됩니다. 정규식 분류가 빗나가도 AI 단계에서 교정되므로
완벽할 필요는 없습니다.

- **검증**: `tests/test_classifier.py`에 케이스를 추가하고 돌리면 됩니다.
- **팁**: 설정 화면에서 주력 분야만 체크하면 그 분야들 안에서만 분류합니다. 분야가 좁을수록 정확도가 올라갑니다.

### (4) sPDF 선택 영역 번역·요약 — 1.0 이후 후속 목표

`DEVELOPMENT_PLAN.md` 7.9의 선택 영역 번역·요약 설계를 따릅니다. sPDF가 선택
텍스트·PDF 페이지·bounding box·문서 ID를 공개 브리지로 전달하고 Organizer는 그
범위만 현재 AI 제공자에 보냅니다. 결과는 임시 패널과 복사가 기본이며 사용자 승인
없이 정식 요약 필드나 PaperPack을 수정하지 않습니다.

sPDF 내부 객체를 Organizer에서 직접 읽지 마세요. 필요한 선택 이벤트/DTO는 먼저
sPDF 원본 저장소에 공용 기능으로 추가하고 submodule을 갱신합니다. 클라우드 전송
동의, 선택 영역 OCR의 명시적 실행, 요청 취소, 원문 위치 복귀를 통합 테스트에
포함합니다.

### (5) 공개 배포 전 보안 마감

`DEVELOPMENT_PLAN.md` 12.1을 기준으로 공통 redactor를 실제 오류 저장·표시 경로에
연결하고, 모든 자식 프로세스 환경에서 클라우드 API 키를 제거합니다. 제거
마법사에는 Windows 자격 증명 삭제를 별도 기본 선택으로 추가하며 설정·Ollama
모델·논문 라이브러리 삭제와 섞지 않습니다.

배포 규모가 커지기 전에는 Windows 설치파일에 Authenticode 서명과 타임스탬프를
적용하고 자동 업데이트 실행 직전 게시자 검증을 추가합니다. 앱의 월간 클라우드
예산은 실제 사용량 누적과 요청 차단이 구현되기 전까지 강제 한도라고 표시하지
않으며, 제공자별 전용 프로젝트 키와 제공자 콘솔 지출 한도를 우선 안내합니다.

---

## 7. 코딩 에이전트에게 맡길 때

이 저장소에는 `AGENTS.md`가 있어서 Claude Code나 Codex가 규칙을 자동으로 읽습니다.
작업을 시킬 때는 목표와 검증 방법을 같이 주면 결과가 좋습니다.

예시입니다.

```text
docs/HANDOFF.md의 "다음에 할 일 (1) 추정 제목 깨짐"을 진행해줘.
샘플 PDF는 samples/broken-title/ 에 넣어뒀어.
먼저 재현 테스트를 tests/test_title_repair.py 에 작성해서 실패하는 걸 확인하고,
그 다음에 _default_metadata()를 고쳐. 다 끝나면 전체 테스트를 돌려줘.
```

바꾸기 전에 반드시 전체 테스트가 통과하는 상태에서 시작하고, 끝난 뒤에도
`python -m unittest discover -s tests`로 확인하세요. 커밋은 한 문장으로 설명되는
단위로 끊는 편이 나중에 되돌리기 쉽습니다.
