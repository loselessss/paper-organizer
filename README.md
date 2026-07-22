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
python -m unittest discover -s tests -v
python run.py identity "C:\path\to\paper.pdf"
python run.py reindex "C:\path\to\library"
```

GUI와 sPDF submodule 통합은 다음 구현 단계에서 추가합니다. 요약용 Ollama
모델은 설치본에 포함하지 않고 설치 후 사용자가 선택해 다운로드하도록 설계합니다.
