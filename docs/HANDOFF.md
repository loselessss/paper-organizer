# 인수인계 계획서

이 문서 하나만 읽으면 작업을 이어받을 수 있도록 정리했습니다. 사람이 읽어도
되고, Claude Code나 GPT Codex 같은 코딩 에이전트에게 그대로 물려줘도 됩니다.

- 대상 브랜치: `main`
- 상태: v1.9.1 감시·업데이트·UI 안정화 릴리스 준비 완료
- 현재 개발 범위: PDF 제목 복구와 모델 선택 시 용도·환각 위험·요약 전략 안내,
  8B 미만 계층형 요약 및 8B 이상 고급 분석 경계, 한국어 자연어 검색의 영문
  원문 검색어 확장과 설치 Ollama 모델 우선 사용, PC 사양별 모델 상주 정책,
  PaperPack 등록/분석 날짜 표시와 원문 보존형 한국어 번역 캐시·직렬 번역 큐,
  라이브러리 기본 화면·정렬, 새 PDF·분석 큐 통합 탭, 파일 이동 없는 제외 목록,
  HiDPI sPDF 렌더링

---

## 1. 개발 환경 준비

Python 3.12 이상이 필요합니다. sPDF는 submodule이라 `--recurse-submodules` 없이
clone 했다면 반드시 따로 받아야 합니다. 받지 않으면 `test_spdf_bridge`가 실패합니다.
현재 고정 버전은 sPDF 1.7.1이며 여러 줄 선택을 사각 영역이 아니라 시작·끝 단어
사이의 읽기 순서로 전달합니다.

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
       · 서로 다른 특허 표지 또는 DOI·초록 표지가 둘 이상이면 복수 문서로 표시하고 요약 제외
  → _auto_organize()  academic_likely + 중복 없음이면 승인 없이 보관
       · 정규식 분류기가 분야·세부분야·저널명을 채움 (field_sources = "auto:regex")
       · 페이지 본문을 content/content.json에 저장
       · papers/<분야>/<세부분야>/x.paperpack 생성, 분석 큐에 등록
  → BackgroundAnalysisService.run_next()   한 편씩 AI 분석
       · 반복 머리말·쪽번호·OCR 잡음을 제거하고 논문 구역별 문단 컨텍스트 구성
       · 4B 이하는 구역별 요약→최종 요약, 8B 이상은 전체 구역 직접 요약
       · 첫 페이지 서지 추출 + 요약·분류 정정 (field_sources = "ai:<provider>")
       · 특허는 patent-summary-v1으로 기술적 과제·청구 발명·실시예·효과를 분석하고
         Claims/청구범위 원문은 AI를 거치지 않고 그대로 PaperPack에 저장
       · 분류가 바뀌면 paperpack을 새 분야 폴더로 이동
  → 검색 색인(index/search.sqlite) 증분 갱신
```

중복 후보가 있거나 판정이 불확실하거나 복수 문서로 감지된 PDF는 자동 처리하지 않고
수집 화면에 남습니다. 복수 문서를 사용자가 보관하더라도 `복수 문서 · 요약 제외`로
표시하고 분석 큐에는 넣지 않습니다.
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

**라이브러리 갱신 후 첫 행을 무조건 선택하지 마세요.**
AI 번역·분석 완료로 표를 다시 만들 때는 행 번호가 아니라 `Qt.UserRole`의
PaperPack 절대 경로로 현재 선택과 다중 선택을 복원하고, 기존 정렬과 스크롤 위치도
유지합니다. 선택 항목이 결과에서 사라진 경우에만 첫 행을 선택합니다.

**새 대화상자는 공통 제목 표시줄 규칙을 적용합니다.**
실제 상황별 도움말을 구현하지 않은 `QDialog`는
`ui/dialog_utils.py`의 `suppress_context_help_button()`을 호출해 Windows 제목
표시줄의 불필요한 `?`를 제거합니다. 스플래시 이미지는 null 여부를 확인하기 전에
확대하지 말고, 먼저 만든 단색 캔버스를 안전한 대체 화면으로 유지합니다.

**요약 모델 변경은 Ollama 프로세스 재시작 사유가 아닙니다.**
Ollama 요청이 모델 ID를 직접 전달하므로 실행 중인 서버는 그대로 재사용합니다.
꺼져 있을 때만 `start_runtime()`으로 창 없는 `ollama serve`를 시작하세요. GPU
환경값 변경처럼 재시작이 필요한 경우에도 `restart_runtime()`은 트레이 앱을 열지
않고 숨김 서버만 다시 시작합니다.

**sPDF 편집본의 파일명은 PaperPack 원본 PDF 이름을 사용합니다.**
편집 작업공간은 계속 해시 디렉터리로 격리하되 탭·창 제목에는 manifest의
`document.original_name`이 보이도록 합니다. 구버전 `working.pdf`가 있으면 원자적
이름 변경으로 이전하고, 파일이 열려 있어 이전할 수 없으면 기존 경로를 유지해
사용자 편집 내용을 잃지 않습니다.

---

## 6. 완료 상태와 다음에 할 일

### 실제 남은 우선순위

1. Qwen3.5 2B를 16GB PC에서 반복 실행해 백그라운드 상주·언로드 안정성을
   확인하고 Qwen3 1.7B 교체 여부를 결정합니다.
2. sPDF 선택 영역에 텍스트 레이어가 없을 때 사용자가 명시적으로 실행하는
   선택 영역 OCR을 공개 DTO 흐름에 연결합니다.
3. 새로운 실제 오분류가 확인될 때만 taxonomy 키워드와 회귀 사례를 보강합니다.

### 최근 동작 변경

- 설치 앱의 업데이트 확인은 시작 5초 후뿐 아니라 실행 중에도 매시간 일정 여부를
  확인합니다. 마지막 성공 확인에서 24시간이 지났을 때만 GitHub에 요청하며,
  네트워크 실패는 성공 시각으로 기록하지 않아 다음 한 시간 주기에 재시도합니다.
- `요약 감시 옵션`의 `감시 폴더의 하위 폴더도 포함`을 켜면 모든 감시 폴더를
  재귀 탐색합니다. 자기 수집을 막기 위해 이때 PaperPack 라이브러리는 감시 폴더
  밖에 있어야 합니다.

### 완료 및 검증된 후속 안정화 작업

1. **구버전에서 잘못 잠긴 `Research Article` 제목 복구 — 완료**
   - 원인은 “AI가 첫 장을 읽지 않음”이 아닙니다. 예전 버전이 문서 유형 머리말인
     `Research Article`을 실제 제목으로 오인한 뒤 사용자가 정한 제목처럼
     `field_sources["bibliography.title"] = "user"`로 잠근 데이터가 남아 있는
     문제입니다.
   - 현재 버전의 일반 머리말 거부 로직만으로는 이미 저장된 PaperPack의 잘못된
     `user` 출처가 자동 복구되지 않으므로, 해당 값에 한정한 안전한 마이그레이션
     또는 재분석 경로가 필요했습니다.
   - 첫 라이브러리 로딩 때 PaperPack 변경 이력을 확인한 뒤, 초기 저장 과정에서
     잘못 `user`로 표시된 일반 머리말만 첫 페이지 시각 제목으로 복구합니다.
     사용자가 나중에 직접 제목을 바꾼 이력이 있거나 필드를 잠갔다면 건드리지
     않습니다.
   - `test_legacy_generic_user_title_is_repaired_from_embedded_pdf`와
     `test_true_user_title_named_research_article_is_not_repaired`가 자동 복구와
     실제 사용자 편집 보존을 각각 검증합니다. 알려진 `Research Article` 잠금
     문제는 구현과 회귀 테스트까지 완료됐으며 더 이상 다음 작업이 아닙니다.

2. **라이브러리에서 PaperPack 삭제 시 Windows 파일 점유 오류 수정**
   - 라이브러리에서 PaperPack을 지우려 하면 다른 프로세스가 파일을 사용 중이라는
     오류가 발생합니다. 삭제 동작은 영구 삭제가 아니라 기존 규칙대로 `trash/`로
     이동하고 복원 경로를 남겨야 합니다.
   - 삭제 직전 PaperPack ZIP, PDF, 검색 색인 SQLite 연결을 누가 열고 있는지
     확인하고, 라이브러리 미리보기·분석·검색 경로에서 파일 핸들이 닫히지 않는
     재현 테스트를 추가합니다. 특히 `sqlite3.connect()`는
     `contextlib.closing`으로 감쌌는지 확인해야 했습니다.
   - 라이브러리 제거는 이제 PaperPack과 연관 파일을 `trash/` 아래 작업 폴더로
     옮기고 원래 경로·해시·분석 큐 상태를 기록합니다. 수집 화면의 휴지통 목록에서
     원래 위치와 큐 상태로 복원할 수 있습니다. 이동 전 sPDF 작업본을 닫고,
     Windows의 일시적인 공유 위반은 짧게 재시도하며 계속 점유 중이면 sPDF와
     탐색기 미리보기를 닫으라는 구체적인 안내를 표시합니다.

3. **사용자 확인 UI 4건 안정화 — 완료**
   - 제외 파일 복원 창에 제외 사유 열과 선택 항목 상세 사유를 추가했습니다.
   - AI 번역·분석 갱신 후 선택 항목, 정렬과 스크롤이 유지됩니다.
   - 모든 앱 `QDialog`는 공통 도우미로 불필요한 제목 표시줄 `?`를 제거합니다.
   - 스플래시 자산이 없거나 손상돼도 null pixmap을 확대하지 않고 단색 대체
     화면으로 시작합니다.

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

`tests/benchmark/README.md`의 실행기로 Qwen3와 Granite 4.1 3B, Phi-4 Mini,
Gemma 3 4B QAT, Ministral 3 3B 등 설치된 후보 모델을 돌린 뒤
`model_summary.csv`, `paper_scores.csv`, `recommendation.json`을 비교합니다.
OCR 문서는 모델 비교에서 제외합니다. 텍스트 레이어 6편의
`critical_negations` 보존과 `forbidden_hits`를 우선 봅니다. 비공개 원본 결과는
Git에 올리지 않습니다. 대신 `tests/benchmark/tools/publish_private_scores.py`로
제목, 경로, 원문, 정답, 프롬프트와 모델 출력을 제거한 익명 점수만
`tests/benchmark/score_history/`에 누적해 Git에 올립니다.

설치 앱의 정밀 벤치마크 연결은 나중 단계입니다. 기본 사양 추천 모델을 기준으로
바로 아래·기준·바로 위 등급만 후보로 만들고, 사용자가 총 다운로드 용량을 확인해
명시적으로 선택했을 때만 최대 3개를 한 번에 받도록 합니다. 첫 실행에서 전체 모델을
자동 다운로드하거나 벤치마크를 강제하지 않습니다.

현재 선택 UI에는 역할과 최신 실논문 측정을 함께 표시합니다. Qwen3 1.7B는
백그라운드 서지·Abstract, Qwen3.5 4B는 정확도 우선 수동 본문 분석 기본,
Granite 4.1 3B는 속도 우선 수동 대안, 8B 이상은 기여·한계를 포함하는 고급
분석으로 안내합니다. Qwen3.5 2B와 Granite 3.3 2B는 비교 후보입니다.

동일한 16GB 내장 GPU 환경에서 실논문 6편과 4B급 본문 계층 요약 조건으로 측정한
Granite 4.1 3B는 연구 15.55점·리뷰 36.11점·서지 66.67점, 평균 11.998초,
JSON 재시도 0회였습니다. Qwen3.5 4B는 연구 18.61점·리뷰 49.22점·서지
87.50점, 평균 16.008초였습니다. 따라서 Granite는 약 25% 빠른 2순위 대안,
Qwen3.5 4B는 정확도 우선 1순위로 표시합니다. 이전 4편·다른 프롬프트·다른
하드웨어의 Granite 82.5점은 현재 추천 근거로 사용하지 않습니다. 익명 Granite
결과는 `tests/benchmark/score_history/real_papers_granite41_20260801.json`에 있습니다.

같은 비공개 실논문 4편의 이전 측정에서는 Qwen3 1.7B가 평균 27.81점,
연구논문 3편 평균 33.75점, 평균 97.13초를 기록했습니다. Granite 3.3 2B는
전체 평균 25.31점, 연구논문 평균 30.42점, 평균 183.33초였습니다. 따라서
초기 기본 조합은 Qwen3 1.7B와 Qwen3 4B였습니다. 최신 측정 이후에는
백그라운드에 Qwen3 1.7B, 수동 분석 기본에 Qwen3.5 4B를 사용합니다.
리뷰 프롬프트 v4를 실리뷰 논문 3편으로 확장한 실측은 Qwen3 1.7B 리뷰 평균
36.11점, Qwen3.5 4B 49.22점입니다. 연구 프롬프트 v10은 비교군·종점·수치 결과를
배경보다 먼저 보존하도록 바꿨고, Qwen3.5 4B의 실연구논문 3편 평균을 10.00점에서
18.61점으로 높였습니다. Qwen3 1.7B는 서지+Abstract 전용 경로에서 연구 평균
15.55점, 평균 2.517초, JSON 재시도 0회를 기록했습니다. 6편 전체 서지 평균은
각각 75.00점과 87.50점입니다.
따라서 1.7B는 백그라운드 서지·분류와 abstract 보존용, 4B는 수동 본문 종합용으로
취급합니다. 기존 0.6B 실측은 4편 중 서지 네 필드를 모두 맞춘 것이 1편뿐이라
서지 전용 후보에서도 제외합니다.
Qwen3 1.7B는 `abstract_only` 실행 전략으로 첫 페이지 서지와 논문 자체 Abstract만
처리합니다. 본문 섹션은 전송하지 않으며 Abstract가 없으면 `bibliography_only`로
서지 추출만 실행합니다. 이 경로는 기존 고급 기여·한계 필드를 덮어쓰지 않습니다.
리뷰논문 점수는 일반 연구논문과 분리해 평가하며, 비공개 문서를 추가할 때는
`tests/Real/inspect_private_pdfs.py`가 기존 manifest의 파일명·SHA-256을 이용해
익명 문서 ID를 유지하므로 파일 정렬 순서로 기존 결과를 덮어쓰지 않습니다.

### (2-1) 리뷰 논문 전용 프롬프트 — 완료

**2026-08-01 구현·실논문 재검증 완료.** `research_paper`·`review_paper`·`patent`를 분석 전에 결정하고, 사용자 확정 유형은 `document.type` 잠금으로 보호한다. 특허 형식과 명시적 리뷰 유형은 정규식으로 확정하지만 `a review of`·`this paper reviews` 같은 문장형 단서는 제목·초록 AI 2차 분류를 거친다. AI가 실패하거나 `uncertain`이면 연구논문으로 처리한다. 기존 PaperPack은 자동 변경하지 않고 라이브러리에 재분류 후보만 표시한다. 리뷰 v4는 섹션 단계부터 구체 대상·메커니즘·전략·응용을 보존하고, 축소 스키마에서는 결론과 근거 공백을 `summary`에 유지한다. 명시되지 않은 systematic review 검색법은 제거하며 리뷰 유형을 QA용 methods에 명시한다.

### (3) 분류 체계 다듬기

`paper_organizer/models/taxonomy.json`에 학과 20개와 세부 전공이 들어 있습니다.
실제로 쓰다 보면 본인 분야가 잘 안 잡히는 경우가 나올 텐데, 그때는 해당 분야의
`keywords`를 보강하면 됩니다. 정규식 분류가 빗나가도 AI 단계에서 교정되므로
완벽할 필요는 없습니다.

- **검증**: `tests/test_classifier.py`에 케이스를 추가하고 돌리면 됩니다.
- **팁**: 설정 화면에서 주력 분야만 체크하면 그 분야들 안에서만 분류합니다. 분야가 좁을수록 정확도가 올라갑니다.

### (4) sPDF 선택 영역 번역·요약 — 기본 구현 완료, 선택 영역 OCR 후속

공개 DTO, 선택 텍스트 전송, 임시 결과 패널·복사·취소는 구현됐습니다. 결과는
사용자 승인 없이 정식 요약 필드나 PaperPack을 수정하지 않습니다.

sPDF 내부 객체를 Organizer에서 직접 읽지 마세요. 필요한 선택 이벤트/DTO는 먼저
sPDF 원본 저장소에 공용 기능으로 추가하고 submodule을 갱신합니다. 클라우드 전송
동의, 선택 영역 OCR의 명시적 실행, 요청 취소, 원문 위치 복귀를 통합 테스트에
포함합니다.

### (5) 공개 배포 보안 마감 — 코드 서명 외 완료

`DEVELOPMENT_PLAN.md` 12.1을 기준으로 공통 redactor를 실제 오류 저장·표시 경로에
연결하고, 모든 자식 프로세스 환경에서 클라우드 API 키를 제거합니다. 제거
마법사에는 Windows 자격 증명 삭제를 별도 기본 선택으로 추가하며 설정·Ollama
모델·논문 라이브러리 삭제와 섞지 않습니다.

코드 서명 인증서를 실제로 마련하기 전에는 Windows 설치파일과 별도 SHA-256 파일을
함께 배포하고 자동 업데이트에서 해시를 검증합니다. 앱의 월간 클라우드
예산은 실제 사용량 누적과 요청 차단이 구현되기 전까지 강제 한도라고 표시하지
않으며, 제공자별 전용 프로젝트 키와 제공자 콘솔 지출 한도를 우선 안내합니다.

---

## 7. 코딩 에이전트에게 맡길 때

이 저장소에는 `AGENTS.md`가 있어서 Claude Code나 Codex가 규칙을 자동으로 읽습니다.
작업을 시킬 때는 목표와 검증 방법을 같이 주면 결과가 좋습니다.

현재 다음 작업 예시입니다.

```text
docs/HANDOFF.md의 "실제 남은 우선순위 (1) Qwen3.5 2B 16GB 안정성"을 진행해줘.
비공개 원문과 모델 출력은 Git에 넣지 말고 기존 익명 점수 게시 도구를 사용해.
백그라운드 반복 실행 후 최대 메모리, 실제 CPU/GPU 배치, 처리 시간, 실패와
언로드 후 메모리 회수를 기록하고 Qwen3 1.7B 교체 여부만 제안해줘.
```

바꾸기 전에 반드시 전체 테스트가 통과하는 상태에서 시작하고, 끝난 뒤에도
`python -m unittest discover -s tests`로 확인하세요. 커밋은 한 문장으로 설명되는
단위로 끊는 편이 나중에 되돌리기 쉽습니다.
