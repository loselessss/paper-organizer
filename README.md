# Paper Organizer

Windows에서 다운로드한 학술 PDF를 로컬에서 판별·정리하고 `.paperpack`으로
보관·색인하는 개인용 애플리케이션입니다. 설계와 단계별 범위는
[DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md)를 참고하세요.
`.paperpack` 공개 규격은 [PAPERPACK_FORMAT.md](PAPERPACK_FORMAT.md)에 정리되어 있습니다.

현재 구현된 첫 번째 코어:

- 원자적으로 저장되는 애플리케이션 설정
- 다운로드가 끝난 안정된 PDF 탐색
- PDF 시그니처·암호·손상 여부 검사
- ResearchGate 등 저장소 래퍼 페이지 감지
- 파일 SHA-256과 래퍼 제거 본문 지문 생성
- `file_id` / `edition_id` / `work_id` 기반 동일성 비교
- `.paperpack`과 기존 sidecar JSON 기반 `library.json` 재구축
- 표준 ZIP 기반 `.paperpack` 생성·수정 이력·무결성 검사·PDF 추출

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

기존 PDF/sidecar 라이브러리는 GUI의 `레거시 변환` 탭이나 `paperpack migrate-legacy`
명령으로 일괄 변환할 수 있습니다. 기본값은 기존 파일 유지입니다. 전체 변환·검증 후
기존 파일을 앱 휴지통으로 옮기려면 CLI에서
`--move-legacy-to-trash --confirm-move-legacy`를 함께 지정합니다.
휴지통으로 옮긴 기존 PDF와 JSON은 `레거시 변환` 탭 또는
`paperpack restore-migration` 명령으로 원래 위치에 복원할 수 있으며, 변환된
paperpack은 그대로 유지됩니다.

가상환경을 사용하는 현재 개발 PC에서는 다음 명령으로 바로 실행할 수 있습니다.

```powershell
.\.venv\Scripts\paper-organizer-gui.exe
```

`vendor/spdf`는 sPDF `main`을 추적하는 submodule입니다. GUI 브리지는 지연
로딩하므로 PyQt5가 없는 코어 테스트 환경에서도 패키지를 가져올 수 있습니다.
요약 AI는 로컬 Ollama(기본값), OpenAI API, Anthropic Claude API 중에서
선택할 수 있습니다. Ollama 모델은 설치본에 포함하지 않고 설치 후 선택
다운로드하며, 클라우드 API는 사용자가 전송에 동의한 요청에만 사용합니다.
API 키는 설정 JSON이나 Git에 저장하지 않고 OS 자격 증명 저장소(선택 의존성
`cloud`) 또는 `OPENAI_API_KEY`/`ANTHROPIC_API_KEY` 환경 변수에서 읽습니다.
클라우드 키는 요청 시점에만 조회하고 공식 API 주소에만 전송하며, 화면에는
마지막 네 글자만 표시합니다. Anthropic Admin API 키는 허용하지 않습니다.

현재 GUI 셸에는 제공자·모델·키·클라우드 처리량을 관리하는 AI 설정 화면과
PDF 전송 범위·페이지·예상 토큰을 먼저 보여주는 즉시 요약 위젯이 연결되어
있습니다. 즉시 요약은 임시 분석으로만 표시되며 PDF를 이동하거나 정식 색인을
수정하지 않습니다.

`수집 및 검토` 화면은 Windows 다운로드 폴더를 기본 입력으로 제안하며 다른
폴더도 선택할 수 있습니다. 안정성이 두 번 확인된 PDF만 본문 지문을 만들고,
ResearchGate 표지 변형을 포함한 중복 후보를 표시합니다. 파일 이동과 확인된
중복의 앱 휴지통 이동은 항상 사용자가 승인해야 하며 휴지통 파일은 복원할 수
있습니다. 승인한 신규 논문은 `.paperpack`으로 보관되며 입력 PDF는 기본적으로
유지합니다. 설정에서 검증 후 입력 PDF 삭제를 선택할 수 있고, 원본 유지 시에는
처리 영수증으로 같은 파일의 반복 검색을 막습니다. `라이브러리` 화면에서는
제목·저자·연도·분류·태그·설명을 수정하고
논문 메타데이터와 통합 인덱스를 즉시 갱신합니다. 저널명 또는 학회명은
`bibliography.venue`에 별도 저장하며 라이브러리 목록·통합 검색·클라우드 편집본에
포함됩니다. 저널의 영향력이나 유명도는 근거 없이 추정하지 않습니다.

선택적으로 OneDrive 안의 폴더를 `JSON 미러`로 지정할 수 있습니다. 새 저장 구조의
로컬 원본은 `.paperpack`이며, OneDrive에는 클라우드 도구가 수정하기 쉬운 단일
`portable-library.json`과 복구용 JSON 내보내기를 서로 분리해 저장합니다.
마지막 동기화 기준선 이후 한쪽만 바뀌면 반대쪽에 반영하고, 로컬과 클라우드가
모두 바뀌면 자동 덮어쓰기하지 않습니다. `클라우드 동기화` 화면에서 두 JSON을
나란히 비교해 `로컬 원본 사용` 또는 `클라우드 편집본 적용`을 선택할 수 있습니다.
클라우드 값을 적용하기 전 로컬 메타데이터는 `.paperpack`의 `history/`에 보관합니다.
클라우드에서 항목을 지워도 로컬 `.paperpack`은 자동 삭제하지 않습니다.

라이브러리나 분석 큐에서 paperpack을 sPDF 또는 즉시 요약으로 열면 내부 PDF를
SHA-256으로 검증해 로컬 캐시에 지연 추출합니다. 따라서 앱 시작 시 전체 PDF를
풀지 않으며, 기존 sidecar 기반 라이브러리도 읽기·검색 호환을 유지합니다.

라이브러리에서 sPDF로 열 때는 읽기 캐시와 분리된 편집 작업 복사본을 사용합니다.
sPDF에서 저장한 뒤 `편집본을 PaperPack에 적용`을 눌러야만 내부 PDF가 새 리비전으로
교체됩니다. 원본 package의 해시나 리비전이 편집 중 바뀌면 덮어쓰지 않고 충돌로
중단하며, `편집본 폐기`는 작업 복사본만 지웁니다. 적용 뒤 파일 정체성과 분석 큐를
갱신하고 기존 본문 색인과 AI 결과는 재분석 대상으로 표시합니다.

시작할 때 `Paper Organizer 0.6.0`과 `Created by SANGKYU SHIN, Ph.D.`가 표시되는
스플래시를 띄우고, 별도 작업 스레드에서 로컬 paperpack·JSON과 동기화 폴더 상태를
읽은 뒤 메인 창을 엽니다.

첫 실행 완료 기록이 없는 경우에는 메인 창보다 먼저 시작 및 종료 설정을
표시합니다. Windows 로그인 자동 시작 여부는 기본적으로 꺼져 있으며, X 버튼을
눌렀을 때 시스템 트레이에서 계속 실행할지 완전히 종료할지는 반드시 한 번
선택해야 합니다. 같은 선택은 이후 `설정 → 시작 및 종료 설정`에서 변경할 수
있습니다. 로그인 자동 시작은 현재 Windows 사용자에게만 등록됩니다.

자동 스캔은 기본 `저사양/절전` 프로필에서 300초마다 실행됩니다. `균형`은
60초, `고성능`은 15초를 제안하며 5~3600초 범위에서 직접 바꿀 수 있습니다.
새로 발견된 PDF는 `state/analysis-queue.json`에 저장되어 앱을 다시 시작해도
유지됩니다. 분석 큐에서는 우선순위를 바꾸거나, PDF를 삭제하지 않고 큐에서만
제거하거나, 선택한 항목을 즉시 요약 화면으로 보낼 수 있습니다. AI가 준비되지
않은 동안에는 네트워크나 로컬 모델을 자동 호출하지 않습니다.

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
