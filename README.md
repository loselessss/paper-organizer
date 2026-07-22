# Paper Organizer

Windows에서 다운로드한 학술 PDF를 로컬에서 판별·정리하고 JSON으로 색인하는
개인용 애플리케이션입니다. 설계와 단계별 범위는
[DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md)를 참고하세요.

현재 구현된 첫 번째 코어:

- 원자적으로 저장되는 애플리케이션 설정
- 다운로드가 끝난 안정된 PDF 탐색
- PDF 시그니처·암호·손상 여부 검사
- ResearchGate 등 저장소 래퍼 페이지 감지
- 파일 SHA-256과 래퍼 제거 본문 지문 생성
- `file_id` / `edition_id` / `work_id` 기반 동일성 비교
- sidecar JSON 기반 `library.json` 재구축

## 개발 실행

```powershell
Set-Location '<paper-organizer repository>'
python -m pip install -e '.[gui]'
python -m unittest discover -s tests -v
python run.py identity "C:\path\to\paper.pdf"
python run.py reindex "C:\path\to\library"
python -m paper_organizer.gui
```

가상환경을 사용하는 현재 개발 PC에서는 다음 명령으로 바로 실행할 수 있습니다.

```powershell
& '<paper-organizer repository>\.venv\Scripts\paper-organizer-gui.exe'
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
있습니다. `라이브러리` 화면에서는 제목·저자·연도·분류·태그·설명을 수정하고
sidecar JSON과 통합 인덱스를 즉시 갱신합니다.

선택적으로 OneDrive 안의 폴더를 `JSON 미러`로 지정할 수 있습니다. PDF는
동기화하지 않고 sidecar, 통합 인덱스와 편집 이력 JSON만 원자적으로 복사합니다.
로컬 sidecar가 원본이므로 OneDrive가 잠겨 있거나 충돌해도 완료된 PDF 이동을
되돌리지 않으며, 실패 내용은 경고로 표시합니다. 현재 미러는 안전을 위한
단방향 백업이며 여러 PC의 변경을 자동 병합하는 양방향 동기화는 아닙니다.

시작할 때 `Paper Organizer 0.2.1`과 `Created by SANGKYU SHIN, Ph.D.`가 표시되는
스플래시를 띄우고, 별도 작업 스레드에서 로컬 JSON 묶음과 동기화 폴더 상태를
읽은 뒤 메인 창을 엽니다.

자동 스캔은 기본 `저사양/절전` 프로필에서 300초마다 실행됩니다. `균형`은
60초, `고성능`은 15초를 제안하며 5~3600초 범위에서 직접 바꿀 수 있습니다.
새로 발견된 PDF는 `state/analysis-queue.json`에 저장되어 앱을 다시 시작해도
유지됩니다. 분석 큐에서는 우선순위를 바꾸거나, PDF를 삭제하지 않고 큐에서만
제거하거나, 선택한 항목을 즉시 요약 화면으로 보낼 수 있습니다. AI가 준비되지
않은 동안에는 네트워크나 로컬 모델을 자동 호출하지 않습니다.
