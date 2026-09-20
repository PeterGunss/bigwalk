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

EXEC_TIMEOUT = 30
# 사용자가 Zotero의 검색/선택 창에서 시간을 들여 고를 수 있으므로 길게 잡는다.
RESPOND_TIMEOUT = 600

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("zotero_debug.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("zotero_bridge")

# 이번 세션(스크립트를 실행해둔 동안) 동안에만 유지되는 임시 저장소.
# 스크립트를 껐다 켜거나 문서를 닫았다 열면 사라진다 (다음 단계에서 파일로 영구 저장 예정).
_field_codes: dict[str, str] = {}  # field_id -> 숨겨진 인용 코드(CSL_CITATION 등)
_field_texts: dict[str, str] = {}  # field_id -> 화면에 보이는 텍스트
_field_order: list[str] = []  # 문서에 삽입된 순서대로의 field_id 목록
_document_data: str = ""
# Field.* 명령은 필드참조 자리에 null을 보내고 "방금 다룬 그 필드"를 뜻하는
# 경우가 많아서, 가장 최근에 만든/다룬 필드 ID를 기억해둔다.
_current_field_id: str | None = None


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
    # args: [docId, dataStr]
    global _document_data
    if len(args) > 1:
        _document_data = args[1]
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
    # args: [docId, fieldType, noteType]
    global _current_field_id
    field_id = f"ZOTERO_{uuid.uuid4().hex[:8]}"
    _current_field_id = field_id
    _field_order.append(field_id)
    log.info("새 필드 생성(임시, 실제 누름틀 아님): %s", field_id)
    return {"fieldID": field_id}


def _resolve_field_id(args) -> str | None:
    # Field.* 명령: args[1]이 필드참조. null이면 "방금 다룬 필드"로 간주한다.
    field_ref = args[1] if len(args) > 1 else None
    return field_ref or _current_field_id


def handle_Field_setText(hwp, doc_id, args):
    # args: [docId, fieldRef(null 가능), text, isRich]
    field_id = _resolve_field_id(args)
    text = args[2] if len(args) > 2 else ""
    if field_id:
        _field_texts[field_id] = text
    log.info("Field_setText: field_id=%s text=%r", field_id, text)
    hwp.insert_text(text)
    return None


def handle_Field_setCode(hwp, doc_id, args):
    # args: [docId, fieldRef(null 가능), code]
    field_id = _resolve_field_id(args)
    code = args[2] if len(args) > 2 else ""
    if field_id:
        _field_codes[field_id] = code
    log.info("Field_setCode: field_id=%s code 길이=%d", field_id, len(code or ""))
    return None


def handle_Field_getCode(hwp, doc_id, args):
    field_id = _resolve_field_id(args)
    return _field_codes.get(field_id, "")


def handle_Field_getText(hwp, doc_id, args):
    field_id = _resolve_field_id(args)
    return _field_texts.get(field_id, "")


def handle_Field_getNoteIndex(hwp, doc_id, args):
    # 각주/미주 인용은 아직 지원하지 않으므로 항상 0(본문).
    return 0


def handle_Document_getFields(hwp, doc_id, args):
    # "TEMP"는 Zotero가 지금 만들고 있는 중인(아직 완성 안 된) 필드에 붙이는
    # 임시 코드다. 그런 필드까지 목록에 섞어 돌려주면 Zotero가 혼란스러워하며
    # 멈추는 것으로 보여서, 이미 완성된(진짜 코드가 들어온) 필드만 돌려준다.
    ready = [
        fid for fid in _field_order if _field_codes.get(fid) not in (None, "", "TEMP")
    ]
    log.info(
        "Document_getFields: 추적 중 %d개 중 완료된 필드 %d개",
        len(_field_order),
        len(ready),
    )
    # [id, code, text, noteIndex]로 한 번에 묶어 보내는 방식은 멈추는 현상이
    # 있었다. Field.getCode/getText/getNoteIndex를 이미 구현해뒀으니, 대신
    # 필드 ID만 돌려주고 Zotero가 필요할 때 개별 조회하게 해본다.
    return list(ready)


def handle_Document_insertText(hwp, doc_id, args):
    # args: [docId, text]
    text = args[1] if len(args) > 1 else (args[0] if args else "")
    hwp.insert_text(text)
    return None


def handle_Document_complete(hwp, doc_id, args):
    return None


def handle_Document_displayAlert(hwp, doc_id, args):
    # args: [docId, text, icon, buttons] (다른 Document.* 명령과 같은 패턴으로 추정)
    message = args[1] if len(args) > 1 else (args[0] if args else "")
    log.info("Zotero 알림: %s", message)
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, str(message), "Zotero", 0)
    except Exception:
        pass
    return 1  # 기본적으로 "확인/예"에 해당하는 값으로 가정


HANDLERS = {
    # 실제 트래픽 확인 결과 명령 이름은 밑줄(_)이 아니라 점(.)으로 구분된다.
    "Application.getActiveDocument": handle_Application_getActiveDocument,
    "Document.getDocumentData": handle_Document_getDocumentData,
    "Document.setDocumentData": handle_Document_setDocumentData,
    "Document.activate": handle_Document_activate,
    "Document.canInsertField": handle_Document_canInsertField,
    "Document.cursorInField": handle_Document_cursorInField,
    "Document.insertField": handle_Document_insertField,
    "Field.setText": handle_Field_setText,
    "Field.setCode": handle_Field_setCode,
    "Field.getCode": handle_Field_getCode,
    "Field.getText": handle_Field_getText,
    "Field.getNoteIndex": handle_Field_getNoteIndex,
    "Document.getFields": handle_Document_getFields,
    "Document.insertText": handle_Document_insertText,
    "Document.complete": handle_Document_complete,
    "Document.displayAlert": handle_Document_displayAlert,
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
    resp = session.post(EXEC_URL, json=body, timeout=EXEC_TIMEOUT)

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

        if command == "Document.complete":
            # 이 명령 이후에는 Zotero가 더 이상 응답하지 않는 것으로 보여서
            # (응답을 보내면 요청이 그냥 멈춘다), 여기서 바로 거래를 끝낸다.
            log.info("=== 트랜잭션 종료 (Document.complete) ===")
            break

        log.debug(">> POST respond: %s", result)
        # Zotero의 검색/선택 창에서 사용자가 고르는 동안 이 응답이 한참
        # (몇 분까지) 지연될 수 있으므로 넉넉하게 잡는다.
        resp = session.post(RESPOND_URL, json=result, timeout=RESPOND_TIMEOUT)


def trigger(requests_module, command: str) -> None:
    import requests

    session = requests.Session()
    try:
        run_transaction(session, requests_module, command)
    except requests_module.exceptions.Timeout:
        log.error(
            "Zotero 응답 대기 시간이 초과됐습니다. Zotero 쪽에 열려 있는 인용/참고문헌 "
            "창이 있다면 완료하거나 취소해주세요. 계속 이 에러가 나면 Zotero를 "
            "재시작한 뒤 다시 시도해주세요."
        )
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
    print("  (이 스크립트를 끄지 않고 계속 켜둔 상태에서 삽입한 인용만 기억합니다.)")
    print("자세한 기록은 zotero_debug.log 파일에서 확인할 수 있습니다.")
    print("종료하려면 이 창에서 Ctrl+C.")

    import threading

    busy_lock = threading.Lock()

    def start_trigger(command: str) -> None:
        # keyboard 라이브러리의 내부 처리 스레드를 오래 붙잡고 있으면 이후 단축키
        # 입력을 놓치는 현상이 있어서, 실제 작업은 별도 스레드에서 실행한다.
        if not busy_lock.acquire(blocking=False):
            log.warning("이미 다른 작업이 진행 중이라 이번 단축키 입력은 무시합니다.")
            return

        def run():
            try:
                trigger(requests, command)
            finally:
                busy_lock.release()

        threading.Thread(target=run, daemon=True).start()

    keyboard.add_hotkey("ctrl+alt+c", lambda: start_trigger("addEditCitation"))
    keyboard.add_hotkey("ctrl+alt+b", lambda: start_trigger("addEditBibliography"))

    try:
        keyboard.wait()
    except KeyboardInterrupt:
        print("종료합니다.")


if __name__ == "__main__":
    main()
