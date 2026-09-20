"""
Stage 2: 터미널에서 스크립트를 매번 실행하지 않고, 단축키 한 번으로
클립보드 내용을 한글 문서에 삽입하는 상시 실행형 리스너.

사용법:
1. 이 스크립트를 실행해두면 (python 02_hotkey_listener.py), 창은 그대로 열어둔다.
   (최소화해도 되고, 백그라운드에 두면 된다.)
2. 한글 문서에서 인용을 넣을 위치에 커서를 둔다.
3. Zotero에서 "인용 복사"를 실행한다.
4. 단축키(기본값 Ctrl+Alt+Z)를 누르면 클립보드 내용이 한글 커서 위치에 삽입된다.
5. 리스너를 끝내려면 이 창에서 Ctrl+C를 누른다.

단축키를 바꾸고 싶으면 아래 HOTKEY 값을 수정하면 된다.
(예: "ctrl+alt+z", "ctrl+shift+q" 등. keyboard 라이브러리 표기법을 따른다.)
"""

import platform
import sys

HOTKEY = "ctrl+alt+z"


def beep(success: bool) -> None:
    try:
        import winsound

        if success:
            winsound.Beep(1000, 120)
        else:
            winsound.Beep(300, 250)
    except Exception:
        pass


def insert_clipboard_to_hwp(hwp_module, pyperclip_module) -> None:
    clipboard_text = pyperclip_module.paste()
    if not clipboard_text or not clipboard_text.strip():
        print("[실패] 클립보드가 비어 있습니다. Zotero에서 인용을 먼저 복사해주세요.")
        beep(False)
        return

    try:
        hwp = hwp_module.Hwp()
        hwp.insert_text(clipboard_text)
    except Exception as e:
        print(f"[실패] 한글에 삽입하지 못했습니다. 한글 문서가 열려 있는지 확인해주세요.\n원본 에러: {e}")
        beep(False)
        return

    print(f"[성공] 삽입 완료: {clipboard_text!r}")
    beep(True)


def main() -> None:
    if platform.system() != "Windows":
        print(f"[실패] 이 스크립트는 Windows에서만 동작합니다. 현재: {platform.system()}")
        sys.exit(1)

    try:
        import keyboard
    except ImportError:
        print("[실패] keyboard 패키지가 없습니다. 'pip install -r requirements.txt'를 실행해주세요.")
        sys.exit(1)

    try:
        import pyperclip
    except ImportError:
        print("[실패] pyperclip 패키지가 없습니다. 'pip install -r requirements.txt'를 실행해주세요.")
        sys.exit(1)

    try:
        import pyhwpx
    except ImportError:
        print("[실패] pyhwpx 패키지가 없습니다. 'pip install -r requirements.txt'를 실행해주세요.")
        sys.exit(1)

    print(f"리스너를 시작합니다. {HOTKEY} 를 누르면 클립보드 내용이 한글에 삽입됩니다.")
    print("종료하려면 이 창에서 Ctrl+C를 누르세요.")

    try:
        keyboard.add_hotkey(HOTKEY, lambda: insert_clipboard_to_hwp(pyhwpx, pyperclip))
    except Exception as e:
        print(
            "[실패] 단축키 등록에 실패했습니다. 터미널을 '관리자 권한으로 실행'한 뒤 "
            "다시 시도해보세요.\n"
            f"원본 에러: {e}"
        )
        sys.exit(1)

    try:
        keyboard.wait()
    except KeyboardInterrupt:
        print("리스너를 종료합니다.")


if __name__ == "__main__":
    main()
