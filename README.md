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
git clone --recurse-submodules <paper-organizer-repository-url>
python -m unittest discover -s tests -v
python run.py identity "C:\path\to\paper.pdf"
python run.py reindex "C:\path\to\library"
python -m pip install -e .[gui]
paper-organizer-gui
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
