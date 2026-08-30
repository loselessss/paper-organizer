# Paper Organizer

[English README](README.md)

Windows에서 학술 PDF를 모으고, `.paperpack`으로 보관하고, AI 요약과 전문 검색까지
이어 주는 로컬 우선 데스크탑 앱입니다.

최신 설치 파일은
[GitHub Releases](https://github.com/loselessss/paper-organizer/releases/latest)에서
받을 수 있습니다.

## 왜 만들었나

논문 PDF는 모으기는 쉽고 다시 쓰기는 어렵습니다. 다운로드 폴더에는 파일명이 깨진
PDF가 쌓이고, 제목·저자·연도·저널 정보는 자주 비어 있거나 틀립니다. 나중에
기억나는 것도 제목보다는 실험 조건, 방법, 결과 문장인 경우가 많습니다.

Paper Organizer는 이 흐름을 하나의 로컬 라이브러리로 묶기 위해 만들었습니다. 새
PDF를 찾고, 논문과 특허를 판별하고, PaperPack으로 보관하고, 서지 정보를 확인하고,
AI 요약을 만들고, 전문 색인을 만들어 나중에 다시 찾을 수 있게 합니다.

## 무엇을 하나

1. 새 PDF를 찾습니다
   - 다운로드 폴더와 사용자가 고른 감시 폴더를 검사합니다.
   - 저장이 끝나 안정된 PDF만 읽습니다.
   - 연구논문, 리뷰논문, 특허, 중복 후보, 손상 파일, 복수 문서 묶음을 구분합니다.
   - 텍스트가 부족한 PDF는 내장 OCR로 본문을 보강합니다.

2. PaperPack으로 보관합니다
   - PDF, 메타데이터, 본문, 분석 결과, 수정 이력을 `.paperpack` ZIP 파일 하나에
     저장합니다.
   - `index/library.json`과 `index/search.sqlite`는 다시 만들 수 있는 캐시로
     취급합니다.
   - `.paperpack`을 유일한 원본으로 둡니다.

3. 서지와 연구분야를 정리합니다
   - 정규식 추출, AI 추출, PubMed/Crossref 검증을 조합합니다.
   - 제목, 저자, 연도, 저널/학회 필드별 출처를 기록합니다.
   - 사용자가 직접 고친 필드는 이후 AI 분석이 덮어쓰지 않습니다.
   - 연구분야와 세부분야를 직접 관리할 수 있습니다.

4. 요약하고 번역합니다
   - 앱이 관리하는 내장 로컬 GGUF 모델을 기본으로 사용합니다.
   - OpenAI와 Anthropic은 사용자가 클라우드 전송을 허용한 경우에만 사용합니다.
   - 연구논문, 리뷰논문, 특허를 구분해 별도 프롬프트로 분석합니다.
   - AI 분석 내용의 한국어 번역 캐시를 저장합니다.

5. 검색하고 다시 씁니다
   - 키워드 검색과 자연어 질문 검색을 지원합니다.
   - 실제 PaperPack 본문 페이지에서 찾은 근거 문맥을 보여줍니다.
   - 라이브러리 열 표시 여부와 순서를 사용자가 정할 수 있습니다.
   - 내장 sPDF 연동으로 PDF를 열고 편집본을 PaperPack에 다시 적용할 수 있습니다.

## 주요 기능

- 다운로드 폴더와 사용자 지정 감시 폴더 스캔
- 연구논문·리뷰논문·특허 PDF 판별
- 중복 PDF와 복수 문서 묶음 감지
- `.paperpack` 생성, 검증, 추출, 재색인
- PubMed/Crossref 기반 서지 검증
- 서지 이상값 감지와 필드별 출처 기록
- 연구분야·세부분야 분류와 사용자 관리
- 앱 전용 GGUF 모델 다운로드와 선택
- 기존 환경을 위한 legacy Ollama 제공자
- OpenAI·Anthropic 선택 연동
- 연구논문·리뷰논문·특허별 요약 프롬프트
- 백그라운드 분석 큐와 실패 항목 재분석
- AI 요약 한국어 번역 캐시
- SQLite FTS5 전문 검색
- 자연어 논문 검색과 근거 문맥 표시
- 라이브러리 열 표시·순서 사용자 설정
- sPDF 연동 PDF 열기와 편집본 적용
- 내장 RapidOCR 기반 텍스트 보강
- GitHub Releases 기반 업데이트 확인
- PyInstaller와 Inno Setup 기반 Windows 설치 파일

## 로컬 AI 준비

Paper Organizer 2.4.0부터 기본 로컬 AI는 앱이 직접 관리하는 GGUF 모델을 사용합니다.
설치본에는 앱, sPDF 연동, OCR 런타임, 로컬 AI 실행 기반이 포함되지만 대용량 모델
가중치는 포함하지 않습니다.

설치 후에는 다음 순서로 준비합니다.

1. `AI 설정`을 엽니다.
2. `내장 로컬 AI`를 선택합니다.
3. `모델 다운로드·관리`를 엽니다.
4. 추천 GGUF 모델 중 하나를 다운로드합니다.
5. 필요하면 백그라운드 분석 모델과 수동 요약 모델을 따로 선택합니다.

기본 로컬 AI 흐름에는 Ollama가 필요하지 않습니다. 기존 Ollama 사용자는 legacy
제공자로 계속 사용할 수 있지만, Paper Organizer가 Ollama나 공유 Ollama 모델 파일을
자동으로 삭제하지는 않습니다.

## 로컬 우선 원칙

- PDF와 PaperPack은 사용자의 PC에 저장됩니다.
- 로컬 모델 파일은 앱 전용 모델 폴더에 저장됩니다.
- 클라우드 AI는 사용자가 전송을 허용한 요청에서만 실행됩니다.
- API 키는 OS 자격 증명 저장소 또는 환경 변수에서 읽습니다.
- 오류 정보에는 API 키, traceback, 논문 본문을 저장하지 않습니다.

## 설치

[최신 릴리스](https://github.com/loselessss/paper-organizer/releases/latest)에서
아래 파일 중 하나를 받아 실행하세요.

- `PaperOrganizer_Setup_latest.exe`
- `PaperOrganizer_Setup_<버전>.exe`

로컬 AI 모델은 몇 GB가 될 수 있으므로 설치 후 앱 안에서 별도로 다운로드합니다.

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
- [RELEASE_NOTE_RULES.md](RELEASE_NOTE_RULES.md): 언어별 릴리스 노트 작성 규칙

## 라이선스와 포함 자산

앱 안에는 향후 UI 폰트 검토를 위한 Pretendard 파일과 라이선스가 포함되어 있습니다.
sPDF는 `vendor/spdf` submodule로 고정된 국제판 버전을 사용하며, Paper Organizer가
내부에서 sPDF를 열 때는 sPDF의 업데이트 알림과 자체 업데이트 동작을 끕니다.
