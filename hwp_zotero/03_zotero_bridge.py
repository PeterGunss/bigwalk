"""
Stage 4a: Zotero의 실시간 인용 삽입 기능을 한글에 연결하는 다리(bridge).

Zotero는 로컬 23119 포트에서 HTTP 서버를 띄워두고 있다. 우리는 그 서버에
"인용 삽입해줘"라고 요청을 보내면, Zotero가 자체 검색창을 띄워 사용자가
항목/스타일을 고르게 하고, 그 뒤에 "지금 커서가 필드 안에 있어?",
"이 텍스트를 필드에 넣어줘" 같은 세부 명령들을 우리에게 순서대로 요청한다.
우리는 그 요청을 받아 실제로 한글을 조작하고, 결과를 다시 Zotero에게 돌려준다.

이 첫 버전(Stage 4a)의 목표는 "본문에 인용 삽입"만 되게 하는 것이다.
아직 하지 않는 것(다음 단계에서 추가):
- 이미 삽입된 인용을 다시 클릭해서 수정하는 기능
- 참고문헌(Bibliography) 자동 생성/갱신
- 문서를 닫고 다시 열어도 인용 정보가 유지되는 것 (지금은 이 스크립트를 실행하는
  동안에만 메모리에 저장됨)

사용법:
1. 이 스크립트를 실행해둔다: python 03_zotero_bridge.py
2. Zotero와 한글을 둘 다 켜놓는다. 한글 문서에 커서를 원하는 위치에 둔다.
3. Ctrl+Alt+C 를 누르면 Zotero의 인용 삽입 창이 뜬다.
4. 스타일(예: APA)과 항목을 고르고 확인하면, 한글 커서 위치에 인용이 삽입된다.
5. 무슨 일이 있었는지 전부 zotero_debug.log 파일에 기록된다. 문제가 생기면
   이 파일 내용을 그대로 알려줄 것.
"""

import json
import logging
import platform
import sys
import uuid

ZOTERO_BASE_URL = "http://127.0.0.1:23119/connector/document"
EXEC_URL = f"{ZOTERO_BASE_URL}/execCommand"
RESPOND_URL = f"{ZOTERO_BASE_URL}/respond"

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("zotero_debug.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("zotero_bridge")

# 이번 세션 동안에만 유지되는 임시 저장소.
# field_id -> 숨겨진 인용 코드(CSL_CITATION 등). Stage 4b에서 파일로 영구 저장할 예정.
_field_codes: dict[str, str] = {}
_document_data: str = ""


def get_hwp():
    from pyhwpx import Hwp

    return Hwp()


def get_doc_id(hwp) -> str:
    """현재 한글 문서를 식별할 ID. 저장된 파일이면 경로, 아니면 임시 ID."""
    try:
        path = hwp.Path
        if path:
            return path
    except Exception:
        pass
    return "hwp-untitled-document"


# ---- Zotero가 요청할 수 있는 각 명령의 처리기 ----
# 아직 정확한 인자 형태를 모르는 명령이 많아서, 일단 최대한 안전한 기본값을
# 돌려주고 무슨 인자가 왔는지 로그로 남긴다.


def handle_Application_getActiveDocument(hwp, doc_id, args):
    return {"documentID": doc_id}


def handle_Document_getDocumentData(hwp, doc_id, args):
    return _document_data


def handle_Document_setDocumentData(hwp, doc_id, args):
    global _document_data
    if args:
        _document_data = args[0]
    return None


def handle_Document_activate(hwp, doc_id, args):
    return None


def handle_Document_canInsertField(hwp, doc_id, args):
    return True


def handle_Document_cursorInField(hwp, doc_id, args):
    # Stage 4a에서는 항상 "필드 안에 없음"으로 처리한다.
    # (기존 인용 수정 기능은 아직 지원하지 않음)
    return None


def handle_Document_insertField(hwp, doc_id, args):
    field_id = f"ZOTERO_{uuid.uuid4().hex[:8]}"
    log.info("새 필드 생성(임시, 실제 누름틀 아님): %s", field_id)
    return {"fieldID": field_id}


def handle_Field_setText(hwp, doc_id, args):
    field_id = args[0] if len(args) > 0 else None
    text = args[1] if len(args) > 1 else ""
    log.info("Field_setText: field_id=%s text=%r", field_id, text)
    hwp.insert_text(text)
    return None


def handle_Field_setCode(hwp, doc_id, args):
    field_id = args[0] if len(args) > 0 else None
    code = args[1] if len(args) > 1 else ""
    if field_id:
        _field_codes[field_id] = code
    log.info("Field_setCode: field_id=%s code 길이=%d", field_id, len(code or ""))
    return None


def handle_Document_getFields(hwp, doc_id, args):
    # Stage 4a에서는 필드를 영구 추적하지 않으므로 항상 빈 목록.
    # (참고문헌 생성은 이 목록이 채워져야 제대로 동작하므로 Stage 4b에서 다룬다.)
    return []


def handle_Document_insertText(hwp, doc_id, args):
    text = args[0] if args else ""
    hwp.insert_text(text)
    return None


def handle_Document_complete(hwp, doc_id, args):
    return None


def handle_Document_displayAlert(hwp, doc_id, args):
    message = args[0] if args else ""
    log.info("Zotero 알림: %s", message)
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, str(message), "Zotero", 0)
    except Exception:
        pass
    return 1  # 기본적으로 "확인/예"에 해당하는 값으로 가정


HANDLERS = {
    "Application_getActiveDocument": handle_Application_getActiveDocument,
    "Document_getDocumentData": handle_Document_getDocumentData,
    "Document_setDocumentData": handle_Document_setDocumentData,
    "Document_activate": handle_Document_activate,
    "Document_canInsertField": handle_Document_canInsertField,
    "Document_cursorInField": handle_Document_cursorInField,
    "Document_insertField": handle_Document_insertField,
    "Field_setText": handle_Field_setText,
    "Field_setCode": handle_Field_setCode,
    "Document_getFields": handle_Document_getFields,
    "Document_insertText": handle_Document_insertText,
    "Document_complete": handle_Document_complete,
    "Document_displayAlert": handle_Document_displayAlert,
}


def dispatch(hwp, doc_id, command, args):
    handler = HANDLERS.get(command)
    if handler is None:
        log.warning("알 수 없는 명령 (아직 처리기 없음): %s, args=%r", command, args)
        return None
    try:
        return handler(hwp, doc_id, args)
    except Exception:
        log.exception("명령 처리 중 에러: %s", command)
        return None


def run_transaction(session, requests_module, initial_command: str) -> None:
    hwp = get_hwp()
    doc_id = get_doc_id(hwp)
    log.info("=== 트랜잭션 시작: %s (doc_id=%s) ===", initial_command, doc_id)

    body = {"command": initial_command, "docId": doc_id}
    log.debug(">> POST execCommand: %s", body)
    resp = session.post(EXEC_URL, json=body, timeout=30)

    while True:
        log.debug("<< status=%s body=%s", resp.status_code, resp.text[:2000])

        if resp.status_code >= 400:
            log.error("Zotero가 에러를 반환했습니다 (status=%s): %s", resp.status_code, resp.text)
            break

        if not resp.text.strip():
            log.info("=== 트랜잭션 종료 (빈 응답) ===")
            break

        try:
            data = resp.json()
        except ValueError:
            log.info("=== 트랜잭션 종료 (JSON 아님, 최종 결과로 간주) ===")
            break

        if not isinstance(data, dict) or "command" not in data:
            log.info("=== 트랜잭션 종료 (최종 결과: %r) ===", data)
            break

        command = data["command"]
        args = data.get("arguments", [])
        log.info("Zotero 요청: %s args=%r", command, args)

        result = dispatch(hwp, doc_id, command, args)
        log.debug(">> POST respond: %s", result)
        resp = session.post(RESPOND_URL, json=result, timeout=30)


def trigger(requests_module, command: str) -> None:
    import requests

    session = requests.Session()
    try:
        run_transaction(session, requests_module, command)
    except requests_module.exceptions.ConnectionError:
        log.error(
            "Zotero(포트 23119)에 연결할 수 없습니다. Zotero가 실행 중인지 확인해주세요."
        )
    except Exception:
        log.exception("트랜잭션 중 예기치 못한 에러")


def main() -> None:
    if platform.system() != "Windows":
        print(f"[실패] 이 스크립트는 Windows에서만 동작합니다. 현재: {platform.system()}")
        sys.exit(1)

    try:
        import requests
    except ImportError:
        print("[실패] requests 패키지가 없습니다. 'pip install -r requirements.txt'를 실행해주세요.")
        sys.exit(1)

    try:
        import keyboard
    except ImportError:
        print("[실패] keyboard 패키지가 없습니다. 'pip install -r requirements.txt'를 실행해주세요.")
        sys.exit(1)

    log.info("Zotero 다리 스크립트 시작. Ctrl+Alt+C: 인용 삽입, Ctrl+Alt+B: 참고문헌")
    print("Ctrl+Alt+C 를 누르면 Zotero 인용 삽입 창이 열립니다.")
    print("Ctrl+Alt+B 를 누르면 Zotero 참고문헌 삽입 창이 열립니다.")
    print("  (Document_getFields가 아직 빈 목록만 돌려주므로, 참고문헌은 아직")
    print("   제대로 만들어지지 않을 수 있습니다 - 로그 확인용으로 먼저 테스트해보세요.)")
    print("자세한 기록은 zotero_debug.log 파일에서 확인할 수 있습니다.")
    print("종료하려면 이 창에서 Ctrl+C.")

    keyboard.add_hotkey("ctrl+alt+c", lambda: trigger(requests, "addEditCitation"))
    keyboard.add_hotkey("ctrl+alt+b", lambda: trigger(requests, "addEditBibliography"))

    try:
        keyboard.wait()
    except KeyboardInterrupt:
        print("종료합니다.")


if __name__ == "__main__":
    main()
