# Paper Organizer 전용 앱 아이콘(ICO·PNG)을 생성하는 개발용 스크립트
"""Generate the multi-resolution application icon with Pillow.

실행: python scripts/make_icon.py
출력: paper_organizer/assets/paper-organizer.ico, paper-organizer-icon.png
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ASSETS = Path(__file__).resolve().parents[1] / "paper_organizer" / "assets"
BASE = 512

NAVY = (11, 30, 58, 255)  # 스플래시와 같은 남색 배경
PAPER = (248, 250, 253, 255)
PAPER_EDGE = (208, 218, 232, 255)
FOLD = (188, 239, 255, 255)  # 스플래시 포인트 색(#bcefff)
BAR_TITLE = (31, 78, 158, 255)
BAR_TEXT = (150, 168, 194, 255)
TAB_COLORS = [
    (46, 160, 113, 255),  # 분류 태그: 초록
    (240, 173, 45, 255),  # 노랑
    (226, 91, 81, 255),  # 빨강
]


def draw_icon(size: int) -> Image.Image:
    image = Image.new("RGBA", (BASE, BASE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # 둥근 남색 배경 타일
    draw.rounded_rectangle((16, 16, BASE - 16, BASE - 16), radius=96, fill=NAVY)

    # 종이: 가운데 살짝 왼쪽, 오른쪽 위 접힘
    left, top, right, bottom = 128, 88, 384, 424
    fold = 64
    draw.polygon(
        [
            (left, top),
            (right - fold, top),
            (right, top + fold),
            (right, bottom),
            (left, bottom),
        ],
        fill=PAPER,
        outline=PAPER_EDGE,
        width=4,
    )
    draw.polygon(
        [(right - fold, top), (right - fold, top + fold), (right, top + fold)],
        fill=FOLD,
    )

    # 제목 줄 + 본문 줄
    draw.rounded_rectangle((160, 152, 320, 176), radius=10, fill=BAR_TITLE)
    for index, y in enumerate((208, 244, 280, 316)):
        width = 192 if index % 2 == 0 else 160
        draw.rounded_rectangle((160, y, 160 + width, y + 16), radius=8, fill=BAR_TEXT)

    # 오른쪽에 겹쳐 붙는 분류 탭 3개 (자동 분류 모티프)
    for index, color in enumerate(TAB_COLORS):
        y = 168 + index * 84
        draw.rounded_rectangle((352, y, 428, y + 52), radius=18, fill=color)

    return image.resize((size, size), Image.LANCZOS)


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    largest = draw_icon(256)
    largest.save(
        ASSETS / "paper-organizer.ico",
        format="ICO",
        sizes=[(value, value) for value in sizes],
    )
    draw_icon(256).save(ASSETS / "paper-organizer-icon.png", format="PNG")
    print(f"아이콘 생성 완료: {ASSETS}")


if __name__ == "__main__":
    main()
