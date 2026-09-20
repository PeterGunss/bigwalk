"""
Stage 1: Zotero에서 복사한 인용을, 이미 열려 있는 한글 문서의 현재 커서 위치에
그대로 삽입하는 스크립트.

사용법:
1. 한글 문서를 열고, 인용을 넣고 싶은 위치에 커서를 둔다.
2. Zotero에서 인용할 항목을 선택하고 "인용 복사"(Ctrl+Shift+C 등)를 실행해
   클립보드에 인용 텍스트를 복사한다.
   (Zotero 환경설정 > 내보내기 에서 "빠른 복사"에 쓸 인용 스타일을 미리 정해둘 것)
3. 이 스크립트를 실행한다: python 01_insert_clipboard.py
4. 한글 문서의 커서 위치에 클립보드 내용이 삽입된다.
"""

import platform
import sys


def fail(message: str) -> None:
    print(f"[실패] {message}")
    sys.exit(1)


def main() -> None:
    if platform.system() != "Windows":
        fail(f"이 스크립트는 Windows에서만 동작합니다. 현재: {platform.system()}")

    try:
        import pyperclip
    except ImportError:
        fail("pyperclip 패키지가 없습니다. 'pip install -r requirements.txt'를 실행해주세요.")
        return

    try:
        from pyhwpx import Hwp
    except ImportError:
        fail("pyhwpx 패키지가 없습니다. 'pip install -r requirements.txt'를 실행해주세요.")
        return

    clipboard_text = pyperclip.paste()
    if not clipboard_text or not clipboard_text.strip():
        fail(
            "클립보드가 비어 있습니다. Zotero에서 인용을 먼저 복사한 뒤 다시 실행해주세요."
        )
        return

    print("클립보드 내용:")
    print(f"  {clipboard_text!r}")

    try:
        # new=False(기본값): 이미 열려 있는 한글 문서에 연결한다.
        hwp = Hwp()
    except Exception as e:
        fail(
            "한글에 연결하지 못했습니다. 한글 문서가 열려 있는지 확인해주세요.\n"
            f"원본 에러: {e}"
        )
        return

    try:
        hwp.insert_text(clipboard_text)
    except Exception as e:
        fail(f"텍스트 삽입 중 에러가 발생했습니다.\n원본 에러: {e}")
        return

    print("[성공] 클립보드 내용을 한글 문서 커서 위치에 삽입했습니다.")


if __name__ == "__main__":
    main()
