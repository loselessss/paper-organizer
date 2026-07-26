# 인수인계 계획서

이 문서 하나만 읽으면 작업을 이어받을 수 있도록 정리했습니다. 사람이 읽어도
되고, Claude Code나 GPT Codex 같은 코딩 에이전트에게 그대로 물려줘도 됩니다.

- 대상 브랜치: `main`
- 상태: v1.3.0 자연어 검색 구현, 테스트 210개 전부 통과
- 이번 회차 범위: 전문 검색 기반 자연어 질의 해석과 근거 답변 도입

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
       · 요약 + 분류·제목·저자·연도·저널명 정정 (field_sources = "ai:<provider>")
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
`SummaryData.from_mapping()`이 `set(raw) != expected`로 검사합니다. 스키마에
필드를 하나 추가하면 provider 3개와 관련 테스트 픽스처를 모두 고쳐야 합니다.

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

### (1) 추정 제목 깨짐 수정 — 원인 확정 후 착수

증상은 수집 화면의 `추정 제목`이 깨져 보이는 것입니다. UTF-8 인코딩 문제라기보다
① PDF 메타데이터의 mojibake, ② ToUnicode 매핑이 없는 CID 폰트에서 추출된 깨진
글리프, ③ 제목이 아닌 줄을 첫 줄로 잡는 heuristic 중 하나일 가능성이 높습니다.

- **먼저 할 일**: 깨져 보이는 PDF 샘플 2~3개를 확보해 재현 테스트를 작성합니다. 원인을 확인하기 전에 고치지 마세요.
- **손댈 곳**: `application/library_workflow.py`의 `_default_metadata()`.
- **방향**: 깨짐 감지(U+FFFD·제어문자·사설영역 비율) → cp1252↔UTF-8 재해석 복원 시도 → 본문 첫 줄 → 파일명 순으로 fallback.
- **참고**: AI 분석이 이미 제목을 정정하므로, 자동 분석을 켜 두면 상당수는 사후 보정됩니다. 급하지 않습니다.

### (2) 분류 체계 다듬기

`paper_organizer/models/taxonomy.json`에 학과 20개와 세부 전공이 들어 있습니다.
실제로 쓰다 보면 본인 분야가 잘 안 잡히는 경우가 나올 텐데, 그때는 해당 분야의
`keywords`를 보강하면 됩니다. 정규식 분류가 빗나가도 AI 단계에서 교정되므로
완벽할 필요는 없습니다.

- **검증**: `tests/test_classifier.py`에 케이스를 추가하고 돌리면 됩니다.
- **팁**: 설정 화면에서 주력 분야만 체크하면 그 분야들 안에서만 분류합니다. 분야가 좁을수록 정확도가 올라갑니다.

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
