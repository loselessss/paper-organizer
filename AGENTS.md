# AGENTS.md

Windows에서 다운로드한 학술 PDF를 판별·분류해 `.paperpack`으로 보관하고
전문 검색까지 제공하는 PyQt5 데스크탑 앱입니다. Python 3.12+, 표준 unittest.

작업을 시작하기 전에 [docs/HANDOFF.md](docs/HANDOFF.md)를 읽으세요. 환경 준비,
코드 지도, 이미 밟아본 지뢰, 다음 할 일이 정리돼 있습니다.

## 명령어

```bash
git submodule update --init
```

```bash
.venv\Scripts\python -m pip install -e ".[gui]"
```

```bash
.venv\Scripts\python -m unittest discover -s tests
```

```bash
.venv\Scripts\python -m paper_organizer.gui
```

## 절대 규칙

1. **`.paperpack`이 유일한 원본입니다.** `index/library.json`과
   `index/search.sqlite`는 지우고 다시 만들 수 있는 파생 캐시입니다. 캐시에만
   존재하는 데이터를 만들지 마세요.
2. **필드 우선순위는 `auto:regex` < `ai:*` < `user`.** 사람이 고친 필드는 AI가
   덮어쓰지 않습니다. 판정은 `_apply_ai_bibliography()` 한 곳에 모아 두세요.
3. **파일 삭제·이동은 사용자 승인을 받습니다.** 영구 삭제 대신 `trash/`로 옮기고
   복원 경로를 남깁니다.
4. **`ui/` 밖에서 PyQt5를 import 하지 마세요.** core·application 테스트가 PyQt
   없이 실행돼야 합니다.
5. **쓰기는 원자적으로.** 임시 파일에 쓴 뒤 `os.replace`로 교체합니다
   (`_atomic_json_write`, `create_paperpack` 참고).
6. **sPDF는 Submodule로 받아온다.** sPDF는 해당 Repository에서 받아오고 Paper organizer에서는 명시된 버전으로 고정하고 업데이트 알림 및 기능을 사용하지 않는다

## 코드 관례

- 모듈 docstring은 영어 한 줄, 사용자에게 보이는 메시지와 예외 문구는 한국어입니다.
- 테스트는 `tests/test_*.py`에 unittest로 작성합니다. 외부 네트워크를 호출하지 않고,
  HTTP는 가짜 클라이언트를 주입해 검증합니다(`tests/test_providers.py` 참고).
- 코드를 고쳤으면 커밋 전에 전체 테스트를 돌립니다.
- 커밋은 한 문장으로 설명되는 단위로 끊습니다.
- 버전 업데이트와 릴리스 노트 작업을 할 때는 [RELEASE_NOTE_RULES.md](RELEASE_NOTE_RULES.md)를
  읽고 대상 언어의 규칙에 맞춰 사용자에게 보이는 변경만 간결하게 적습니다.

## 주의가 필요한 지점

- 테이블에 정렬을 켤 때는 선택 항목을 행 번호가 아니라 `Qt.UserRole`에 심은 ID로
  찾아야 합니다. 안 그러면 정렬 후 엉뚱한 항목이 조작됩니다.
- `sqlite3.connect()`의 `with` 문은 연결을 닫지 않습니다. `contextlib.closing`으로
  감싸세요.
- 요약 응답 스키마에 필드를 추가하면 provider 3개와 테스트 픽스처를 모두 고쳐야
  합니다(`SummaryData.from_mapping()`이 필드 완전 일치를 요구).
