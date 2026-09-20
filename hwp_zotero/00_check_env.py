"""
Stage 0: 한글(HWP) 자동화가 이 컴퓨터에서 정상 동작하는지 확인하는 스크립트.

실행: python 00_check_env.py

- Windows + 한컴오피스 한글(2024 이상)이 설치되어 있어야 합니다.
- 한글이 켜져 있지 않아도 자동으로 실행됩니다.
"""

import platform
import sys


def fail(message: str) -> None:
    print(f"[실패] {message}")
    sys.exit(1)


def main() -> None:
    if platform.system() != "Windows":
        fail(
            "이 스크립트는 Windows에서만 동작합니다. "
            f"현재 감지된 시스템: {platform.system()}"
        )

    try:
        from pyhwpx import Hwp
    except ImportError:
        fail(
            "pyhwpx 패키지를 찾을 수 없습니다. "
            "터미널에서 'pip install -r requirements.txt'를 먼저 실행해주세요."
        )
        return

    print("한글 프로그램을 실행하는 중입니다...")
    try:
        hwp = Hwp()
    except Exception as e:
        fail(
            "한글 자동화 객체를 생성하지 못했습니다. "
            "한컴오피스 한글이 정식 설치되어 있는지 확인해주세요.\n"
            f"원본 에러: {e}"
        )
        return

    try:
        hwp.insert_text(
            "[테스트] 이 문장이 자동으로 입력되었다면 한글 자동화 연결에 성공한 것입니다."
        )
    except Exception as e:
        fail(f"텍스트 삽입 중 에러가 발생했습니다.\n원본 에러: {e}")
        return

    print("[성공] 한글에 테스트 문장이 자동으로 입력되었습니다.")
    print("한글 창을 확인해보세요. 문제가 없다면 창은 닫지 말고 그대로 두셔도 됩니다.")


if __name__ == "__main__":
    main()
