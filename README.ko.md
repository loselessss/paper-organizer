# Paper Organizer

[English README](README.md)

Windows에서 다운로드한 학술 PDF를 로컬 중심으로 정리하고, `.paperpack`으로
보관한 뒤 AI 요약과 전문 검색까지 이어 주는 개인용 데스크탑 앱입니다.

최신 설치 파일은 [GitHub Releases](https://github.com/loselessss/paper-organizer/releases/latest)에서 받을 수 있습니다.

## 왜 만들었나

논문 PDF는 금방 흩어집니다. 다운로드 폴더에는 파일명이 깨진 PDF가 쌓이고,
저자·연도·저널명은 제각각이며, 나중에 다시 찾으려면 제목이 아니라 기억나는
실험 조건이나 결과 문장에 의존해야 할 때가 많습니다.

Paper Organizer는 이 흐름을 한 앱 안에 묶기 위해 만들었습니다.

- 새 PDF를 찾아 학술 문서인지 판별합니다.
- 논문과 특허를 `.paperpack` 하나로 보관합니다.
- 제목, 저자, 연도, 저널/학회, 연구분야를 정리합니다.
- 로컬 AI 또는 사용자가 허용한 클라우드 AI로 요약합니다.
- 본문 전체를 색인해 키워드와 자연어 질문으로 다시 찾습니다.

목표는 논문 파일을 예쁘게 모으는 것보다, 나중에 실제로 다시 쓸 수 있는
개인 논문 라이브러리를 만드는 것입니다.

## 무엇을 했나

현재 앱은 다운로드 폴더 감시부터 라이브러리 검색까지 이어지는 기본 작업 흐름을
갖추고 있습니다.

1. 새 PDF 탐색
   - 감시 폴더에서 안정적으로 저장이 끝난 PDF만 검사합니다.
   - 짧은 일반 문서, 손상 파일, 중복 후보, 저장소 래퍼 페이지를 구분합니다.
   - OCR이 필요한 문서는 내장 RapidOCR 기반으로 본문을 보강합니다.

2. PaperPack 보관
   - PDF, 메타데이터, 본문, 분석 결과, 수정 이력을 `.paperpack` ZIP 패키지에
     함께 보관합니다.
   - `index/library.json`과 `index/search.sqlite`는 다시 만들 수 있는 캐시로
     취급하고, `.paperpack`을 유일한 원본으로 둡니다.

3. 서지와 연구분야 정리
   - 정규식, 외부 서지 검증, AI 분석을 조합해 제목·저자·연도·저널 정보를
     보강합니다.
   - 사람이 직접 고친 필드는 이후 AI가 덮어쓰지 않습니다.
   - 연구분야와 세부분야는 직접 관리할 수 있습니다.

4. AI 분석
   - 앱이 관리하는 내장 로컬 GGUF 모델을 기본으로 사용하고, OpenAI·Anthropic은 사용자가
     전송에 동의한 경우에만 사용합니다.
   - 연구논문, 리뷰논문, 특허를 구분해 다른 프롬프트로 분석합니다.
   - 백그라운드 분석 큐는 한 번에 한 편씩 처리해 저사양 PC에서도 부담을
     줄입니다.

5. 검색과 재활용
   - 라이브러리 표에서 제목·저자·연도·분야·분석 상태를 확인합니다.
   - 키워드 검색과 자연어 질문 검색을 지원합니다.
   - 검색 결과는 실제 PaperPack 본문 페이지를 근거로 표시합니다.
   - sPDF로 PDF를 열고 편집본을 다시 PaperPack에 적용할 수 있습니다.

## 주요 기능

- 다운로드 폴더와 사용자 지정 감시 폴더 스캔
- 학술 논문·리뷰논문·특허 PDF 판별
- 중복 PDF와 복수 문서 묶음 감지
- `.paperpack` 생성, 검증, 추출, 재색인
- 제목·저자·연도·저널/학회 외부 서지 검증
- 분야·세부분야 분류와 사용자 관리
- 내장 로컬 GGUF 기반 AI 요약
- OpenAI·Anthropic 선택 연동
- 연구논문·리뷰논문·특허별 요약 프롬프트
- 백그라운드 분석 큐와 실패 항목 재분석
- AI 요약 한국어 번역 캐시
- SQLite FTS5 전문 검색
- 자연어 논문 검색과 근거 페이지 표시
- 라이브러리 열 표시·순서 사용자 설정
- sPDF 서브모듈 연동 PDF 열기와 편집본 적용
- 내장 RapidOCR 기반 텍스트 보강
- GitHub Releases 기반 업데이트 확인
- PyInstaller와 Inno Setup 기반 Windows 설치 파일

## 로컬 우선 원칙

Paper Organizer는 기본적으로 로컬에서 동작합니다.

- PDF와 PaperPack은 사용자의 PC에 저장됩니다.
- 로컬 LLM 모델 파일은 앱이 관리하되, 설치본 용량을 줄이기 위해 사용자가 선택해
  별도로 설치합니다.
- 클라우드 AI는 사용자가 전송을 허용한 요청에만 사용합니다.
- API 키는 설정 파일이나 Git에 저장하지 않고 OS 자격 증명 저장소 또는 환경
  변수에서 읽습니다.
- 오류 정보에는 API 키, traceback, 논문 본문을 저장하지 않습니다.

## 설치

[최신 릴리스](https://github.com/loselessss/paper-organizer/releases/latest)에서
아래 파일 중 하나를 받아 실행하세요.

- `PaperOrganizer_Setup_latest.exe`
- `PaperOrganizer_Setup_<버전>.exe`

설치본에는 앱, sPDF 연동 코드, 기본 OCR 런타임이 포함됩니다. 로컬 LLM 가중치는
설치본 용량을 줄이기 위해 별도로 관리합니다.

## 개발 실행

Python 3.12 이상이 필요합니다. sPDF는 submodule로 고정되어 있으므로 처음 받은 뒤
초기화해야 합니다.

```powershell
git submodule update --init
python -m pip install -e ".[gui]"
python -m unittest discover -s tests
python -m paper_organizer.gui
```

현재 개발 PC의 가상환경을 쓰는 경우:

```powershell
.\.venv\Scripts\paper-organizer-gui.exe
```

설치 파일을 만들려면 GUI와 빌드 의존성, Inno Setup 6가 필요합니다.

```powershell
python -m pip install -e ".[gui,build]"
.\build_installer.bat
```

결과는 `Output\PaperOrganizer_Setup_<버전>.exe`에 생성됩니다.

## 문서

- [CHANGELOG.md](CHANGELOG.md): 버전별 변경 기록
- [PAPERPACK_FORMAT.md](PAPERPACK_FORMAT.md): `.paperpack` 파일 형식
- [docs/HANDOFF.md](docs/HANDOFF.md): 개발 인수인계, 코드 지도, 주의사항
- [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md): 설계와 장기 개발 범위

## 라이선스와 포함 자산

앱 안에는 향후 UI 폰트 검토를 위한 Pretendard 파일과 라이선스가 포함되어 있습니다.
sPDF는 `vendor/spdf` submodule로 고정된 국제판 버전을 사용하며, Paper Organizer는
sPDF의 업데이트 알림이나 자체 업데이트 기능을 사용하지 않습니다.
