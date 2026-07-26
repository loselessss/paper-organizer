# Paper Organizer

Windows에서 다운로드한 학술 PDF를 로컬에서 판별·정리하고 `.paperpack`으로
보관·색인하는 개인용 애플리케이션입니다. 설계와 단계별 범위는
[DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md)를 참고하세요.
`.paperpack` 공개 규격은 [PAPERPACK_FORMAT.md](PAPERPACK_FORMAT.md)에 정리되어 있습니다.
개발을 이어받는다면 [docs/HANDOFF.md](docs/HANDOFF.md)부터 읽으세요. 환경 준비와
코드 지도, 지켜야 할 원칙, 다음 할 일이 한곳에 있습니다.

## 설치

[GitHub 최신 릴리스](https://github.com/loselessss/paper-organizer/releases/latest)에서
`PaperOrganizer_Setup_latest.exe` 또는 `PaperOrganizer_Setup_<버전>.exe`를 받아
실행하세요. `latest` 파일명은 새 릴리스에서도 유지됩니다. 설치된 앱은 시작할 때
백그라운드에서 새 릴리스를 확인하고, 사용자가 승인하면 설치파일을 내려받아
GitHub가 제공한 SHA-256과 대조한 뒤 설치를 시작합니다. 앱 내부 업데이터는
`latest` 별칭이 아니라 릴리스 번호와 정확히 일치하는 설치파일만 사용합니다.

현재 구현된 첫 번째 코어:

- 원자적으로 저장되는 애플리케이션 설정
- 다운로드가 끝난 안정된 PDF 탐색
- PDF 시그니처·암호·손상 여부 검사
- ResearchGate 등 저장소 래퍼 페이지 감지
- 파일 SHA-256과 래퍼 제거 본문 지문 생성
- `file_id` / `edition_id` / `work_id` 기반 동일성 비교
- `.paperpack`과 기존 sidecar JSON 기반 `library.json` 재구축
- 표준 ZIP 기반 `.paperpack` 생성·수정 이력·무결성 검사·PDF 추출
- 학과 수준 분류 체계와 정규식 1차 분류기
- 학술 논문 판정 시 승인 없이 보관하는 자동 파이프라인
- 재시작 가능한 분석 큐와 동시성 1의 백그라운드 AI 분석 실행기
- 페이지 단위 본문 저장과 SQLite FTS5 전문 검색
- 단순 단어 검색과 근거 기반 자연어 검색을 자동으로 고르는 RAG-lite 검색
- PyInstaller 실행본과 한국어 Inno Setup 설치 프로그램
- GitHub Releases 기반 앱 내 업데이트와 태그 기반 자동 Windows 릴리스

## 자동 처리 흐름

다운로드 폴더에 논문을 받아 두면 나머지는 백그라운드에서 진행됩니다.

1. 안정성이 두 번 확인된 PDF만 본문 지문을 만들고 중복 후보를 찾습니다.
2. 학술 논문 표식이 확인되고 중복 후보가 없으면 승인 없이 `.paperpack`으로
   보관합니다. 이때 분야·세부분야·저널명은 정규식 1차 분류가 채웁니다.
3. 분석 큐의 논문을 한 편씩 AI가 분석해 요약과 함께 분류·제목·저자·연도·
   저널명을 정정합니다. 분류가 바뀌면 논문을 새 분야 폴더로 옮깁니다.
4. 본문은 `content/content.json`에 페이지 단위로 보존되어 전문 검색에 쓰입니다.

중복 후보가 있거나 학술 논문으로 확신되지 않은 PDF는 자동 보관하지 않고
`수집 및 분석` 화면에 남겨 사람이 검토합니다. 자동 보관은 `폴더 및 감시 설정`
에서 끌 수 있습니다.

분류는 정규식 → AI → 사람 순서로 덮어씁니다. 사람이 라이브러리에서 고친
필드는 `curation.field_sources`에 `user`로 기록되어 이후 AI 분석이 덮어쓰지
않습니다. 분류 체계는 `paper_organizer/models/taxonomy.json`의 학과 수준
분류이며, 같은 설정 화면에서 주력 분야만 골라 후보를 좁힐 수 있습니다.

## 개발 실행

```powershell
Set-Location '<paper-organizer 저장소 폴더>'
python -m pip install -e '.[gui]'
python -m unittest discover -s tests -v
python run.py identity "C:\path\to\paper.pdf"
python run.py reindex "C:\path\to\library"
python run.py paperpack create "C:\path\to\paper.pdf" metadata.json paper.paperpack
python run.py paperpack inspect paper.paperpack
python run.py paperpack extract paper.paperpack restored.pdf
python run.py paperpack extract-many "C:\path\to\library\papers" --output-dir "C:\handoff\pdfs" --recursive
python run.py paperpack migrate-legacy "C:\path\to\library"
python run.py paperpack restore-migration "C:\path\to\library" migration-YYYYMMDD-HHMMSS-id
python -m paper_organizer.gui
```

`paperpack create`도 입력 PDF를 기본적으로 보존합니다. 패키지 생성·검증 후 작업
폴더의 입력 PDF까지 정리하려면 `--remove-source --confirm-remove-source`를 함께
지정합니다. 원본 삭제에 실패하면 새로 만든 `.paperpack`을 롤백합니다.

일괄 반출의 기본값은 `.paperpack` 보존입니다. 모든 PDF가 정상 추출되고 SHA-256까지
일치한 뒤 원본 패키지를 제거하려면 `--remove-source --confirm-remove-source`를 함께
지정해야 합니다. 하나라도 손상되었거나 추출에 실패하면 생성 중인 출력물을 정리하고
어떤 `.paperpack`도 제거하지 않습니다. 기존 출력 PDF는 덮어쓰지 않습니다.

기존 PDF/sidecar 라이브러리는 GUI의 `도구 → 레거시 라이브러리 변환`이나
`paperpack migrate-legacy` 명령으로 일괄 변환할 수 있습니다. 기본값은 기존 파일 유지입니다. 전체 변환·검증 후
기존 파일을 앱 휴지통으로 옮기려면 CLI에서
`--move-legacy-to-trash --confirm-move-legacy`를 함께 지정합니다.
휴지통으로 옮긴 기존 PDF와 JSON은 같은 다이얼로그 또는
`paperpack restore-migration` 명령으로 원래 위치에 복원할 수 있으며, 변환된
paperpack은 그대로 유지됩니다. 반대로 보관된 `.paperpack`에서 원본 PDF를
되찾으려면 `도구 → PDF 환원(일괄 추출)`을 사용합니다.

가상환경을 사용하는 현재 개발 PC에서는 다음 명령으로 바로 실행할 수 있습니다.

```powershell
.\.venv\Scripts\paper-organizer-gui.exe
```

`vendor/spdf`는 sPDF `main`의 검증된 1.5.3 커밋을 추적하는 submodule입니다.
손 도구·선택 도구와 즐겨찾기 툴바 동작을 포함하며, GUI 브리지는 지연
로딩하므로 PyQt5가 없는 코어 테스트 환경에서도 패키지를 가져올 수 있습니다.
요약 AI는 로컬 Ollama(기본값), OpenAI API, Anthropic Claude API 중에서
선택할 수 있습니다. Ollama 모델은 설치본에 포함하지 않고 설치 후 선택
다운로드하며, 클라우드 API는 사용자가 전송에 동의한 요청에만 사용합니다.
API 키는 설정 JSON이나 Git에 저장하지 않고 OS 자격 증명 저장소(선택 의존성
`cloud`) 또는 `OPENAI_API_KEY`/`ANTHROPIC_API_KEY` 환경 변수에서 읽습니다.
클라우드 키는 요청 시점에만 조회하고 공식 API 주소에만 전송하며, 화면에는
마지막 네 글자만 표시합니다. Anthropic Admin API 키는 허용하지 않습니다.

화면은 `수집 및 분석`과 `라이브러리` 두 탭이고, 나머지 기능은 `설정`·`도구`·
`AI` 메뉴에 있습니다. `AI` 메뉴에서 제공자(Ollama/OpenAI/Anthropic)를 바로
바꾸고 요약 AI 설정·Ollama 모델 관리·즉시 요약을 엽니다. 즉시 요약은 PDF 전송
범위·페이지·예상 토큰을 먼저 보여주는 임시 분석이며 PDF를 이동하거나 정식
색인을 수정하지 않습니다. 정리된 paperpack의 분석 큐는 기본적으로 백그라운드에서
동시성 1로 실행되며, 진행 상황은 창 아래 상태 표시줄에 논문 제목과 대기·완료·
실패 개수로 표시됩니다. 선택 항목을 즉시 분석하거나 실행기를 수동으로 시작·중지할
수 있고, 앱이 중단된 작업은 다음 시작 때 대기 상태로 복구합니다.
즉시 요약을 사용하는 동안에는 백그라운드 분석이 겹치지 않으며, 실패 항목은
오류 원인을 확인한 뒤 다시 분석할 수 있습니다. 요약 요청에는 불필요한
thinking/reasoning을 사용하지 않습니다. Ollama는 지원되는 GPU를 자동 활용하고,
앱이 직접 시작한 서버는 작업 완료 후 종료합니다.

`수집 및 분석` 화면은 왼쪽에 새로 찾은 PDF, 오른쪽에 분석 큐를 나란히 보여줍니다.
Windows 다운로드 폴더를 기본 입력으로 제안하며 `폴더 및 감시 설정`에서 다른
폴더를 여러 개 추가·제거하고 스캔 주기·자동 보관 여부·주력 분야를 지정할 수
있습니다. 각 감시 폴더는 독립적으로 확인하므로 한 폴더에 문제가 생겨도 나머지
폴더는 계속 처리합니다. 안정성이 두 번
확인된 PDF만 본문 지문을 만들고, ResearchGate 표지 변형을 포함한 중복 후보를
표시합니다. 새 PDF를 제외 목록으로 보내는 작업은 항상 사용자가 승인해야 하며
제외 파일은 복원할 수 있습니다. 보관한 논문의 입력 PDF는 기본적으로 유지하고, 설정에서
검증 후 삭제를 선택할 수 있습니다. 원본 유지 시에는 처리 영수증으로 같은 파일의
반복 검색을 막습니다. 1~2쪽 PDF는 일반 문서로 보고 제외하며, 3쪽 이상의 학술
논문과 특허를 수집
대상으로 판별합니다. 여러 감시 폴더에 같은 파일이 있어도 파일 ID로 한 번만
처리합니다.

`라이브러리` 화면은 왼쪽 목록과 오른쪽 상세로 나뉩니다. 오른쪽에서 제목·저자·
연도·분류·태그·설명을 수정하면 논문 메타데이터와 통합 인덱스가 즉시 갱신되고,
그 아래에 AI가 작성한 요약·연구 질문·방법·기여·한계·키워드가 표시됩니다.
저널명 또는 학회명은 `bibliography.venue`에 별도 저장하며 목록과 검색에
포함됩니다. 저널의 영향력이나 유명도는 근거 없이 추정하지 않습니다.

검색창은 메타데이터뿐 아니라 논문 본문 전체를 찾습니다. 본문은 `.paperpack` 안
`content/content.json`에 페이지 단위로 보존되고, `index/search.sqlite`의 FTS5
색인은 여기서 만들어지는 파생 캐시입니다. 색인을 지워도 `도구 → 검색 색인 재구축`
으로 언제든 다시 만들 수 있으며, 이때 본문이 비어 있던 기존 paperpack은 PDF에서
다시 추출해 채웁니다.

라이브러리 검색창은 검색 방식을 자동으로 고릅니다. 짧은 제목·저자·DOI·기술명은
SQLite에서 즉시 찾고, 물음표가 있거나 비교·이유·설명처럼 답변을 요구하는 문장은
선택한 AI가 검색어로 재구성합니다. AI 검색은 먼저 로컬 색인에서 최대 5편을 고른
뒤 실제로 일치한 PDF 페이지 본문만 사용해 한국어 답변과 근거 페이지를 표시합니다.
후보에 없는 논문 ID와 제공되지 않은 페이지 인용은 버립니다. 결과 행을 두 번
누르면 라이브러리의 원문과 저장된 분석으로 이동합니다. 별도 창은
`도구 → 자연어로 논문 찾기` 또는 `Ctrl+Shift+F`로도 열 수 있습니다.

Ollama 검색은 로컬에서 처리합니다. OpenAI나 Anthropic을 선택한 경우 저장된 전송
동의가 없으면 질문을 보내기 전 한 번, 후보 논문 본문을 보내기 전 후보 수와 실제
글자 수를 보여주고 한 번 동의를 받습니다. 라이브러리 전체를 클라우드로 보내지
않으며, 참고문헌은 검색에는 쓰되 논문의 연구 결과를 설명하는 근거로 쓰지 않습니다.

라이브러리나 분석 큐에서 paperpack을 sPDF 또는 즉시 요약으로 열면 내부 PDF를
SHA-256으로 검증해 로컬 캐시에 지연 추출합니다. 따라서 앱 시작 시 전체 PDF를
풀지 않으며, 기존 sidecar 기반 라이브러리도 읽기·검색 호환을 유지합니다.

라이브러리에서 sPDF로 열 때는 읽기 캐시와 분리된 편집 작업 복사본을 사용합니다.
sPDF에서 저장한 뒤 `편집본을 PaperPack에 적용`을 눌러야만 내부 PDF가 새 리비전으로
교체됩니다. 원본 package의 해시나 리비전이 편집 중 바뀌면 덮어쓰지 않고 충돌로
중단하며, `편집본 폐기`는 작업 복사본만 지웁니다. 적용 뒤 파일 정체성과 분석 큐를
갱신하고 기존 본문 색인과 AI 결과는 재분석 대상으로 표시합니다.

시작할 때 현재 `Paper Organizer` 버전과 `Created by SANGKYU SHIN, Ph.D.`가 표시되는
스플래시를 띄우고, 별도 작업 스레드에서 로컬 paperpack·JSON과 동기화 폴더 상태를
읽은 뒤 메인 창을 엽니다.

첫 실행 완료 기록이 없는 경우에는 메인 창보다 먼저 시작 및 종료 설정을
표시합니다. Windows 로그인 자동 시작 여부는 기본적으로 꺼져 있으며, X 버튼을
눌렀을 때 시스템 트레이에서 계속 실행할지 완전히 종료할지는 반드시 한 번
선택해야 합니다. 같은 선택은 이후 `설정 → 시작 및 종료 설정`에서 변경할 수
있습니다. 로그인 자동 시작은 현재 Windows 사용자에게만 등록됩니다.

`요약 AI 설정`의 `사양 다시 검사`는 별도 AI 런타임을 불러오지 않고 CPU·코어 수,
전체/가용 RAM, GPU·VRAM, Ollama 모델 저장 디스크 여유와 로컬 Ollama 버전·설치
모델을 확인합니다. `자동/속도/균형/품질/직접 선택` 프로필에 따라 번들된 오프라인
모델 카탈로그를 평가하고 모델별 다운로드 크기·예상 실행 메모리·적합도와 추천
이유를 표시합니다. 추천 모델을 선택해도 다운로드는 시작하지 않으며 사용자가 이미
선택한 모델도 자동으로 교체하지 않습니다.

같은 화면의 `Ollama 모델 관리`에서는 오프라인 카탈로그의 예상 다운로드 크기,
모델 디스크 여유와 설치 후 실제 크기·파라미터·양자화를 확인할 수 있습니다.
다운로드는 별도 작업 스레드에서 진행률을 표시하며 취소 후 다시 시도할 수 있습니다.
다운로드가 끝나도 설치 목록 확인과 짧은 구조화 JSON 응답 검증을 통과하기 전에는
활성 모델로 선택하지 않습니다. 기존 공유 모델도 다시 받지 않고 검증 후 선택할 수
있습니다. 모델 삭제는 모델별 확인을 거친 명시적 작업으로만 제공하며 앱 제거 과정은
공용 Ollama 저장소를 건드리지 않습니다.

Ollama 자체가 설치되어 있지 않거나 실행 중이 아니면 같은 화면에 `Ollama 설치 및
실행` 버튼이 나타납니다. 이미 설치되어 있으면 서버만 띄우고, 없을 때는 확인을 받아
winget으로 설치합니다. winget을 쓸 수 없거나 설치가 실패하면 공식 다운로드
주소를 안내하며, 앱이 임의의 경로에서 파일을 내려받지 않습니다.

자동 스캔은 기본 `저사양/절전` 프로필에서 300초마다 실행됩니다. `균형`은
60초, `고성능`은 15초를 제안하며 5~3600초 범위에서 직접 바꿀 수 있습니다.
새로 발견된 PDF는 `state/analysis-queue.json`에 저장되어 앱을 다시 시작해도
유지됩니다. 분석 큐에서는 우선순위를 바꾸거나, PDF를 삭제하지 않고 큐에서만
제거하거나, 선택한 항목을 바로 분석할 수 있습니다. 절전/균형/고성능 프로필의
분석 대기 간격은 각각 30/10/2초이고 항상 한 논문씩 처리합니다. AI가 준비되지
않은 동안에는 항목을 실행 중으로 선점하지 않으며 네트워크나 로컬 모델을 자동
호출하지 않습니다. 분석할 때도 라이브러리 전체를 AI 컨텍스트로 읽지 않고 현재
논문의 임시 PDF 하나만 사용합니다.

색인된 논문을 대화형으로 찾는 기능은 아직 구현하지 않았습니다. 설계는
[docs/conversational-search-design.md](docs/conversational-search-design.md)에
정리해 두었습니다.

기본 OCR은 sPDF의 격리된 RapidOCR 워커를 사용합니다. 페이지 좌표가 보존되어 PDF
검색·선택과 연결하기 쉽고, 한국어 인식 모델도 설치본에 포함되므로 별도 다운로드가
필요 없습니다. Ollama 비전 OCR은 품질 보강용 선택 기능으로 후속 추가할 예정이며,
요약 모델과 OCR 모델은 서로 독립적으로 선택합니다.
본문이 부족한 새 PDF는 GUI를 막지 않는 스캔 워커에서 RapidOCR을 먼저 실행하고,
인식 본문을 PaperPack에 저장해 검색과 후속 AI 분석에서 재사용합니다. RapidOCR은
CPU 친화적이며, 고품질 OCR이 필요하면 sPDF의 GPU 가속 VL 엔진을 사용할 수 있습니다.

## Windows 설치 프로그램 빌드

Python 3.12 가상환경에 GUI와 빌드 의존성을 설치하고 Inno Setup 6을 설치한 뒤 다음을
실행합니다.

```powershell
python -m pip install -e '.[gui,build]'
.\build_installer.bat
```

결과는 `Output\PaperOrganizer_Setup_<버전>.exe`입니다. 설치본에는 sPDF와 기본 OCR
런타임이 포함되지만 Ollama LLM 가중치는 포함되지 않습니다. 제거 프로그램은 앱
파일과 선택한 로그인 자동 시작 항목만 제거하며 `.paperpack`, 설정, API 키 저장소와
공용 Ollama 모델은 삭제하지 않습니다.

## `.paperpack` 형식

`.paperpack`은 독점 바이너리 DB가 아니라 일반 ZIP 파일입니다. 확장자를 `.zip`으로
복사해 열면 다음 표준 파일을 직접 확인하고 복구할 수 있습니다.

```text
example.paperpack
  mimetype
  manifest.json
  document/paper.pdf
  metadata/paper.json
  content/content.json
  history/revision-0001.json
```

PDF 엔트리는 압축하지 않아 원본 바이트를 보존하고, JSON만 압축합니다. `manifest.json`은
PDF·메타데이터·본문 색인의 SHA-256과 크기, 포맷 버전, 현재 리비전을 기록합니다.
수정할 때는 같은 폴더에 새 패키지를 완성하고 검증한 뒤 원자적으로 교체하므로 중간
실패가 기존 파일을 손상시키지 않습니다. 기존 `*.paper.json`/`*.content.json`은
마이그레이션 입력으로 계속 읽되 새 라이브러리의 진실의 원천으로 사용하지 않습니다.
