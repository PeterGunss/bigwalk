"""
Stage 4a: Zotero의 실시간 인용 삽입 기능을 한글에 연결하는 다리(bridge).

Zotero는 로컬 23119 포트에서 HTTP 서버를 띄워두고 있다. 우리는 그 서버에
"인용 삽입해줘"라고 요청을 보내면, Zotero가 자체 검색창을 띄워 사용자가
항목/스타일을 고르게 하고, 그 뒤에 "지금 커서가 필드 안에 있어?",
"이 텍스트를 필드에 넣어줘" 같은 세부 명령들을 우리에게 순서대로 요청한다.
우리는 그 요청을 받아 실제로 한글을 조작하고, 결과를 다시 Zotero에게 돌려준다.

이 버전(Stage 4b)이 하는 일:
- 본문에 인용 삽입/참고문헌 자동 생성
- 문서를 저장해둔 상태라면, 문서 옆에 생기는 "<파일명>.zotero-fields.json"에
  인용 정보를 저장해뒀다가 스크립트를 껐다 켜거나 문서를 닫았다 열어도 이어서 씀
- 커서를 기존 인용 위 왼쪽 끝에 두고 Ctrl+Alt+C를 누르면 새로 추가가 아니라
  그 인용을 수정하는 모드로 들어감
- 참고문헌의 이탤릭체(저널명 등)와 들여쓰기/줄간격/항목간격 서식을 실제로 적용

아직 하지 않는 것:
- 문서를 저장하지 않은 상태("제목없음")에서는 인용 정보를 영구 저장할 방법이
  없어서, 저장 전까지는 스크립트를 껐다 켜면 정보가 사라짐

사용법:
1. 이 스크립트를 실행해둔다: python 03_zotero_bridge.py
2. Zotero와 한글을 둘 다 켜놓는다. 한글 문서에 커서를 원하는 위치에 둔다.
3. Ctrl+Alt+C 를 누르면 Zotero의 인용 삽입 창이 뜬다.
4. 스타일(예: APA)과 항목을 고르고 확인하면, 한글 커서 위치에 인용이 삽입된다.
5. 무슨 일이 있었는지 전부 zotero_debug.log 파일에 기록된다. 문제가 생기면
   이 파일 내용을 그대로 알려줄 것.
"""

import hashlib
import html
import json
import logging
import os
import platform
import re
import sys
import tempfile
import time
import uuid

ZOTERO_BASE_URL = "http://127.0.0.1:23119/connector/document"
EXEC_URL = f"{ZOTERO_BASE_URL}/execCommand"
RESPOND_URL = f"{ZOTERO_BASE_URL}/respond"

EXEC_TIMEOUT = 30
# 사용자가 Zotero의 검색/선택 창에서 시간을 들여 고를 수 있으므로 길게 잡는다.
RESPOND_TIMEOUT = 600

# PyInstaller로 --noconsole(터미널 창 숨김)로 빌드하면 sys.stdout/stderr가
# None이 되는데, 그 상태에서 print()나 로깅이 그걸 쓰려고 하면 죽는다. 콘솔이
# 없을 때는 아무 데도 안 쓰는 가짜 스트림으로 미리 바꿔둔다.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("zotero_debug.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("zotero_bridge")

# 현재 문서에 대한 인용 상태. run_transaction이 매 거래 시작 시 _load_state()로
# 디스크에서 채우고, 끝날 때 _save_state()로 다시 저장한다 (문서를 저장해둔
# 경우에만 - 자세한 내용은 _state_file_path 참고).
_field_codes: dict[str, str] = {}  # field_id -> 숨겨진 인용 코드(CSL_CITATION 등)
_field_texts: dict[str, str] = {}  # field_id -> 화면에 보이는 텍스트
_field_order: list[str] = []  # 문서에 삽입된 순서대로의 field_id 목록
_document_data: str = ""
# Field.* 명령은 필드참조 자리에 null을 보내고 "방금 다룬 그 필드"를 뜻하는
# 경우가 많아서, 가장 최근에 만든/다룬 필드 ID를 기억해둔다.
_current_field_id: str | None = None
# 지금 진행 중인 거래의 최상위 명령(addEditCitation/addEditBibliography 등).
_current_transaction_command: str | None = None
# 위 _field_* 전역 변수들이 지금 어느 문서(doc_id) 것인지. 다른 문서로 바뀌면
# _load_state()가 이 값을 보고 다시 읽어들인다.
_loaded_doc_id: str | None = None
# Document.setBibliographyStyle로 받아둔, 아직 적용 안 한 참고문헌 문단 서식.
_pending_bib_style: dict | None = None
# Zotero의 "노트(주석) 삽입" 기능(인용이 포함된 노트/하이라이트를 본문에 바로
# 삽입)이 Document.insertText로 HTML을 통째로 넣은 뒤, 그 안의 인용 부분을
# Document.convertPlaceholdersToFields로 "진짜 필드로 바꿔달라"고 요청하는
# 2단계 과정을 쓴다. placeholder_id -> 방금 평문으로 끼워넣은 위치/문구.
_pending_placeholders: dict[str, dict] = {}
# 지금 거래(run_transaction 한 번) 안에서 convertPlaceholdersToFields로 필드를
# 만든 적이 있는지. Document.getFields가 이 경우엔 빈 목록을 돌려주면 안 되는
# 것으로 보여서(아래 handle_Document_getFields 주석 참고) 구분해서 처리한다.
_transaction_had_note_conversion = False
# addEditCitation 거래 중에는 Document.getFields를 빈 목록으로 답해도 "인용
# 하나 검색해서 추가"하는 일반적인 흐름은 잘 동작하는 것이 확인됐다 (Zotero
# 소스 확인 결과, 이 흐름에서 만들어지는 Citation은 항상 정확히 1개뿐이라서,
# 뒤에서 필드 목록의 인덱스로 그 필드를 다시 찾는 과정 자체가 없기 때문).
_EMPTY_FIELDS_COMMANDS = {"addEditCitation"}
# addNote/addAnnotation은 다르다 - Zotero 소스(integration.js
# Session.prototype.cite -> _insertNoteIntoDocument)를 확인한 결과, 이 흐름은
# "지금 만들고 있는 자리표시 필드가 문서의 기존 필드 목록에서 몇 번째인지"를
# 거래 맨 처음의 Document.getFields 결과로 알아낸 뒤, 그 번호를 기준으로
# 나중에(주석이 여러 개 포함된 노트일 때) "새로 변환된 필드들이 그 번호 뒤에
# 몇 번째로 이어지는지"를 계산해서 Field.setCode/setText를 보낼 필드를 다시
# 찾는다. 그래서 이 두 명령에서는 처음부터 빈 목록이 아니라 실제(추적 중인
# 전부) 목록을 줘야, 이미 문서에 인용이 있는 상태에서 주석이 여러 개 포함된
# 노트를 삽입해도 그 번호 계산이 맞아떨어진다. (실제로 이 계산이 틀어지면,
# 새로 변환된 필드들에 Field.setCode/setText가 아예 오지 않고 Zotero 자체
# 오류창이 뜨는 것이 로그로 확인됐다.)
_NOTE_INSERT_COMMANDS = {"addNote", "addAnnotation"}


def _state_file_path(doc_id: str) -> str | None:
    # doc_id는 저장된 한글 파일의 전체 경로이거나("C:\...\논문.hwp"), 아직 저장
    # 안 한 문서면 항상 같은 문자열("hwp-untitled-document")이다. 후자는 스크립트를
    # 재시작했을 때 "이전의 그 문서"인지 알아낼 방법이 없으므로 영구 저장을
    # 포기한다. (사용자가 먼저 한글에서 파일 저장을 하면 이 문제가 없어진다.)
    #
    # 예전엔 문서 옆에 "<파일명>.zotero-fields.json"으로 바로 만들었는데,
    # 사용자 문서 폴더에 눈에 보이는 파일이 하나 더 생기는 게 거슬린다는
    # 피드백이 있어서, 문서와 같은 폴더가 아니라 사용자별 앱데이터 폴더
    # (%LOCALAPPDATA%\HwpZoteroBridge\fields\) 안에 문서 경로의 해시값으로
    # 이름 붙여서 저장한다. 문서 폴더에는 아무 것도 남지 않는다.
    if not doc_id or doc_id == "hwp-untitled-document":
        return None
    base_dir = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or tempfile.gettempdir()
    state_dir = os.path.join(base_dir, "HwpZoteroBridge", "fields")
    try:
        os.makedirs(state_dir, exist_ok=True)
    except OSError:
        log.exception("인용 상태 저장 폴더를 만들 수 없어 영속성 기능을 이번엔 건너뜁니다: %s", state_dir)
        return None
    digest = hashlib.sha256(doc_id.encode("utf-8")).hexdigest()
    return os.path.join(state_dir, f"{digest}.json")


def _reset_state() -> None:
    global _document_data
    _field_codes.clear()
    _field_texts.clear()
    _field_order.clear()
    _document_data = ""


def _existing_hwp_field_names(hwp) -> set[str] | None:
    # 지금 한글 문서에 실제로 남아있는 필드 이름 목록. Document.getFields가
    # 죽은(사용자가 직접 지운) 필드를 계속 돌려주지 않게 걸러내는 데만 쓴다.
    # option=0(전체 필드)으로 최대한 넓게 잡아서, 누름틀/셀필드 분류가 문서를
    # 저장하고 다시 열었을 때 살짝 달라지는 경우에도 "실제로 있는데 없다고
    # 착각"하는 일이 최대한 없도록 한다. 실패하면 None을 돌려주고, 호출하는
    # 쪽에서는 "대조 생략(=지우지 않음)"으로 처리한다.
    try:
        try:
            raw = hwp.get_field_list(number=0, option=0)  # 0=이름 그대로, 0=전체
        except AttributeError:
            raw = hwp.GetFieldList(Number=0, option=0)
        if not raw:
            return set()
        return {name for name in raw.split("\x02") if name}
    except Exception:
        log.exception("문서의 실제 필드 목록을 가져오지 못해 대조를 생략합니다.")
        return None


def _load_state(hwp, doc_id: str) -> None:
    # 여기서는 "문서에 지금 실제로 있는지"를 대조해서 걸러내지 않는다 - 문서를
    # 닫았다 막 다시 열었을 때는 한글의 필드 목록 조회가 일시적으로 비어있거나
    # 잘못된 값을 줄 수 있고, 그 상태에서 걸러내면 멀쩡한 인용 정보가 통째로
    # 조용히 사라지는 심각한 문제가 있었다(사용자 보고로 확인됨). 죽은 필드를
    # Zotero가 재사용하려는 문제는 Document.getFields를 응답할 때
    # (_prune_deleted_fields) 그때그때 확인하는 것으로 충분하다.
    global _loaded_doc_id, _document_data
    if doc_id == _loaded_doc_id:
        return  # 이미 이 문서 상태가 메모리에 로드돼 있음

    if (
        _loaded_doc_id == "hwp-untitled-document"
        and doc_id
        and doc_id != "hwp-untitled-document"
        and _field_order
    ):
        # 저장 안 된 "제목없음" 문서 상태로 인용을 넣다가, 방금 처음으로
        # 파일 저장을 해서 실제 경로가 생긴 경우다. 프로그램은 계속 켜져
        # 있었으니 메모리에 있는 내용이 맞는 내용이다 - 이걸 "다른 문서로
        # 전환"으로 착각해서 지워버리면(_reset_state) 아직 디스크에 한 번도
        # 저장되지 못했던 인용 정보가 통째로 사라진다(사용자 보고로 확인된
        # 문제). 지우지 않고 그대로 새 경로로 이어서 쓰고, 지금 바로 한 번
        # 저장해서 다음부터는 재시작/재오픈해도 유지되게 한다.
        log.info(
            "제목없음 문서가 방금 저장되어 실제 경로가 생김: %s. "
            "지금까지 메모리에 있던 인용 %d개를 그대로 이어감.",
            doc_id, len(_field_order),
        )
        _loaded_doc_id = doc_id
        _save_state(doc_id)
        return

    _reset_state()
    _loaded_doc_id = doc_id
    path = _state_file_path(doc_id)
    if not path or not os.path.exists(path):
        log.info("저장된 인용 상태 없음 (새 문서이거나 아직 저장 안 한 문서): doc_id=%s", doc_id)
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            saved = json.load(f)
    except Exception:
        log.exception("저장된 인용 상태 파일을 읽지 못함: %s", path)
        return

    _document_data = saved.get("document_data", "")
    for entry in saved.get("fields", []):
        fid = entry.get("id")
        if not fid:
            continue
        _field_order.append(fid)
        _field_codes[fid] = entry.get("code", "")
        _field_texts[fid] = entry.get("text", "")
    log.info("저장된 인용 상태 불러옴: %s (필드 %d개 복원)", path, len(_field_order))


def _save_state(doc_id: str) -> None:
    path = _state_file_path(doc_id)
    if not path:
        return
    data = {
        "document_data": _document_data,
        "fields": [
            {
                "id": fid,
                "code": _field_codes.get(fid, ""),
                "text": _field_texts.get(fid, ""),
            }
            for fid in _field_order
        ],
    }
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        log.info("인용 상태 저장함: %s (필드 %d개)", path, len(_field_order))
    except Exception:
        log.exception("인용 상태 저장 실패: %s", path)


def _hwp_is_running() -> bool:
    # Hwp()를 그냥 만들면(pyhwpx 기본 동작), 지금 떠 있는 한글이 하나도 없을 때
    # 새 빈 한글 창을 조용히 띄워버린다(게다가 아무도 그 창을 닫아주지 않아
    # 계속 남는다). 아래 백그라운드 점검 스레드는 사용자 조작 없이도 주기적으로
    # 도는 것이라 이 부작용을 반드시 피해야 하므로, COM Running Object Table을
    # 직접 뒤져서 이미 떠 있는 한글이 있는지만 가볍게(새 인스턴스를 만들지
    # 않고) 확인한다.
    try:
        import pythoncom
    except ImportError:
        return False
    try:
        context = pythoncom.CreateBindCtx(0)
        running_coms = pythoncom.GetRunningObjectTable()
        for moniker in running_coms.EnumRunning():
            if moniker.GetDisplayName(context, moniker).startswith("!HwpObject."):
                return True
        return False
    except Exception:
        return False


def _flush_untitled_transition_if_needed() -> None:
    # 버그 재현: "제목없음" 문서 상태로 인용을 넣고 참고문헌까지 만든 뒤, Zotero
    # 쪽으로는 아무 명령도 더 보내지 않은 채로(=거래가 한 번도 더 일어나지
    # 않은 채로) 한글에서 그냥 파일로 저장하고 프로그램들을 전부 종료하면,
    # 메모리에만 있던 인용 상태(_field_order/_document_data 등)가 디스크에
    # 한 번도 저장되지 못하고 통째로 사라졌다(사용자 보고로 확인됨). 원래
    # "제목없음 -> 방금 저장돼서 실제 경로가 생김" 전환은 _load_state가 "다음
    # 거래가 시작될 때" 되돌아보며 알아채는 구조라, 그 다음 거래가 영영 오지
    # 않으면 전환 자체가 일어나지 않는 게 원인이었다. 그래서 거래를 기다리지
    # 않고, 이 함수를 주기적으로(main()의 백그라운드 스레드) 불러서 "지금
    # 한글 문서가 이미 실제 경로로 저장돼 있는데 우리가 아직 제목없음으로
    # 알고 있는" 상태인지 직접 확인하고, 맞다면 즉시 저장해버린다.
    if _loaded_doc_id != "hwp-untitled-document" or not _field_order:
        return  # 저장할 내용이 없거나, 이미 실제 경로로 파악하고 있으면 할 일 없음
    if not _hwp_is_running():
        return
    try:
        hwp = get_hwp()
        doc_id = get_doc_id(hwp)
        _load_state(hwp, doc_id)  # doc_id가 실제 경로면 전환+저장까지 알아서 처리됨
    except Exception:
        log.exception("제목없음→저장된 문서 전환 확인 중 에러")


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
    # 지금까지 아무것도 안 했는데, 참고문헌 거래에서는 이 단계 바로 다음에
    # 항상 실패한다. 한글 창을 실제로 활성화(포커스)해보는 시도.
    # 메서드가 없거나 실패해도 전체 흐름은 계속 진행되게 안전하게 감싼다.
    try:
        hwp.XHwpWindows.Item(0).Activate()
    except Exception as e:
        log.debug("창 활성화 시도 실패(무시하고 계속 진행): %s", e)
    return None


def handle_Document_canInsertField(hwp, doc_id, args):
    return True


def handle_Document_cursorInField(hwp, doc_id, args):
    # 커서가 우리가 만든 Zotero 누름틀 안에 있으면 그 필드를 돌려주고, 그러면
    # addEditCitation이 "새로 추가"가 아니라 "이 인용 수정"으로 동작한다.
    # (한글 API 특성상, 필드의 맨 왼쪽에 커서가 붙어 있을 때만 감지된다 - 필드
    # 오른쪽 끝이나 필드를 벗어난 위치면 감지되지 않는다.)
    global _current_field_id
    try:
        try:
            name = hwp.get_cur_field_name(option=2)  # 2 = 누름틀만
        except AttributeError:
            name = hwp.GetCurFieldName(option=2)
    except Exception:
        log.exception("Document_cursorInField: 현재 필드 이름 조회 실패")
        return None
    if not name:
        return None
    name = name.split("{{")[0]  # "이름{{0}}" 형태로 올 수 있어서 정리
    if not _field_codes.get(name):
        # 우리가 추적하지 않는 필드(다른 용도의 누름틀이거나, 아직 코드가 없는
        # 만들다 만 필드)라면 "Zotero 필드 아님"으로 처리한다.
        return None
    _current_field_id = name
    log.info("Document_cursorInField: 커서가 기존 인용 필드 안에 있음: %s", name)
    return {
        "id": name,
        "code": _field_codes.get(name, ""),
        "text": _field_texts.get(name, ""),
        "noteIndex": None,
    }


def handle_Document_insertField(hwp, doc_id, args):
    # args: [docId, fieldType, noteType]
    global _current_field_id
    field_id = f"ZOTERO_{uuid.uuid4().hex[:8]}"
    _current_field_id = field_id
    _field_order.append(field_id)
    # Zotero가 이 응답으로 필드 객체를 바로 만들어 쓰는데, code/text가 없으면
    # (undefined) 나중에 그 값에 .trim() 같은 걸 호출하다 에러가 난다
    # (실제로 참고문헌 필드 생성 직후 이 문제로 크래시가 났었다).
    #
    # 빈 문자열("")로 채우면 또 다른 함정이 있다: Zotero 세션은
    # ignoreEmptyBibliography가 항상 켜져 있어서, 방금 만든 참고문헌 필드의
    # 텍스트(저희가 답한 값을 그대로 기억함, 다시 물어보지 않음)가 비어있으면
    # 실제 내용을 채우기도 전에 "빈 필드니까 지우자"며 없애버린다
    # (Field.removeCode). 그래서 빈 문자열 대신 "비어있지 않은" 자리표시
    # 텍스트를 준다 — 실제로 한글 문서에 보이는 건 아니고, Zotero가 내부적으로
    # "이 필드는 비어있지 않다"고 착각하게 만들 뿐이다.
    _field_codes[field_id] = ""
    _field_texts[field_id] = "{Bibliography}"
    try:
        hwp.create_field(field_id, "", "")
    except AttributeError:
        hwp.CreateField(field_id, "", "")
    log.info("새 누름틀(진짜 필드) 생성: %s", field_id)
    # Zotero 클라이언트 소스(httpIntegrationClient.js)를 직접 확인한 결과,
    # 필드참조 객체는 "fieldID"가 아니라 "id" 키를 읽고, code/text/noteIndex도
    # 함께 기대한다.
    return {"id": field_id, "code": "", "text": "{Bibliography}", "noteIndex": None}


def _resolve_field_id(args) -> str | None:
    # Field.* 명령: args[1]이 필드참조. null이면 "방금 다룬 필드"로 간주한다.
    field_ref = args[1] if len(args) > 1 else None
    return field_ref or _current_field_id


def _strip_tags(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", fragment))


def _html_bibliography_entries(raw_html: str) -> list[list[tuple[str, bool]]]:
    # 참고문헌은 <div class="csl-bib-body"><div class="csl-entry">...</div>...</div>
    # 형태의 HTML로 온다. 항목마다 (텍스트, 이탤릭여부) 조각의 리스트로 쪼갠다 -
    # 이렇게 하면 태그를 지운 순수 텍스트를 만드는 동시에, <i>...</i> 구간의
    # 글자 위치(오프셋)도 그대로 알 수 있어서 나중에 실제 이탤릭 서식을 그
    # 위치에 입힐 수 있다.
    entries_html = re.findall(r'<div class="csl-entry">(.*?)</div>', raw_html, re.DOTALL)
    if not entries_html:
        entries_html = [raw_html]
    entries: list[list[tuple[str, bool]]] = []
    for entry_html in entries_html:
        segments: list[tuple[str, bool]] = []
        pos = 0
        for m in re.finditer(r"<i>(.*?)</i>", entry_html, re.DOTALL):
            before = _strip_tags(entry_html[pos:m.start()])
            if before:
                segments.append((before, False))
            italic_text = _strip_tags(m.group(1))
            if italic_text:
                segments.append((italic_text, True))
            pos = m.end()
        tail = _strip_tags(entry_html[pos:])
        if tail:
            segments.append((tail, False))
        if segments:
            # 항목의 맨 앞/뒤 공백만 정리한다 (중간 조각들의 오프셋은 그대로 둬야
            # 나중에 이탤릭 위치 계산이 어긋나지 않는다).
            first_text, first_italic = segments[0]
            segments[0] = (first_text.lstrip(), first_italic)
            last_text, last_italic = segments[-1]
            segments[-1] = (last_text.rstrip(), last_italic)
            segments = [(t, i) for t, i in segments if t]
        if segments:
            entries.append(segments)
    return entries


def _apply_bibliography_italics(hwp, field_id: str, entries: list) -> None:
    # 각 항목(문단)의 이탤릭 조각에 실제 이탤릭 서식을 입힌다. 문단 하나가
    # 참고문헌 한 항목에 대응한다고 가정한다 (항목 사이는 "\r\n"으로 분리해서
    # 넣었으므로 맞다).
    try:
        if not hwp.move_to_field(field_id, text=True, start=True, select=False):
            log.warning("이탤릭 서식 적용 실패: 필드 시작으로 이동 못함 (%s)", field_id)
            return
        _, base_para, base_pos = hwp.get_pos()
    except Exception:
        log.exception("이탤릭 서식 적용 준비 중 에러 (field_id=%s)", field_id)
        return

    for i, segments in enumerate(entries):
        para = base_para + i
        offset = base_pos if i == 0 else 0
        for text, is_italic in segments:
            length = len(text)
            if is_italic and text.strip():
                try:
                    hwp.select_text(para, offset, para, offset + length)
                    hwp.set_font(Italic=True)
                except Exception:
                    log.exception(
                        "이탤릭 적용 실패: entry=%d para=%d offset=%d text=%r",
                        i, para, offset, text,
                    )
            offset += length

    try:
        hwp.move_to_field(field_id, text=True, start=True, select=False)
    except Exception:
        pass


def _apply_bibliography_paragraph_style(hwp, field_id: str, entry_count: int) -> None:
    # Document.setBibliographyStyle이 미리 넘겨준 들여쓰기/줄간격/항목간격을
    # 실제로 참고문헌 필드의 문단들에 적용한다.
    global _pending_bib_style
    style = _pending_bib_style
    _pending_bib_style = None
    if not style or entry_count <= 0:
        return
    try:
        hwp.move_to_field(field_id, text=True, start=True, select=False)
        _, base_para, _ = hwp.get_pos()
        hwp.select_text(base_para, 0, base_para + entry_count - 1, -1)
        hwp.set_para(
            Indentation=style["first_line_indent_pt"],
            LeftMargin=style["indent_pt"],
            LineSpacing=style["line_spacing_percent"],
            NextSpacing=style["entry_spacing_pt"],
        )
        hwp.move_to_field(field_id, text=True, start=True, select=False)
        log.info("참고문헌 문단 서식 적용함: %r", style)
    except Exception:
        log.exception("참고문헌 문단 서식 적용 실패 (field_id=%s)", field_id)


def handle_Field_setText(hwp, doc_id, args):
    # args: [docId, fieldRef(null 가능), text, isRich]
    field_id = _resolve_field_id(args)
    raw_text = args[2] if len(args) > 2 else ""
    bib_entries: list | None = None
    if "<div" in raw_text:
        # 참고문헌 필드: HTML 조각이 통째로 온다.
        bib_entries = _html_bibliography_entries(raw_text)
        # 한글(HWP) 필드 텍스트는 "\n" 단독으로는 줄바꿈(문단 구분)이 되지 않고
        # "\r\n"으로 넣어야 각 항목이 별도 줄로 분리된다.
        text = "\r\n".join("".join(t for t, _ in seg) for seg in bib_entries)
    else:
        # 인용 필드: isRich=True일 때 "&#38;"처럼 HTML 엔티티로 인코딩된
        # 텍스트가 온다. 그대로 넣으면 화면에 "&#38;"라는 글자가 그대로
        # 보이고, 나중에 Zotero가 "누가 수동으로 고쳤나?"라고 착각하는
        # 원인이 된다.
        text = html.unescape(raw_text)
    if field_id:
        _field_texts[field_id] = text
    log.info("Field_setText: field_id=%s text=%r", field_id, text)
    try:
        hwp.put_field_text(field_id, text)
    except AttributeError:
        hwp.PutFieldText(field_id, text)
    if bib_entries and field_id:
        _apply_bibliography_italics(hwp, field_id, bib_entries)
        _apply_bibliography_paragraph_style(hwp, field_id, len(bib_entries))
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


def handle_Field_removeCode(hwp, doc_id, args):
    # 이 필드를 더 이상 Zotero 인용/참고문헌으로 취급하지 않겠다는 뜻으로
    # 보인다 (예: 방금 만든 빈 참고문헌 컨테이너를 취소할 때). 코드만 비우고
    # 텍스트/필드 자체는 남겨둔다.
    field_id = _resolve_field_id(args)
    if field_id:
        _field_codes[field_id] = ""
    log.info("Field_removeCode: field_id=%s", field_id)
    return None


def handle_Field_delete(hwp, doc_id, args):
    # 필드 추적에서 완전히 제거한다. (아직 한글 문서에서 실제 누름틀 자체를
    # 지우는 처리는 하지 않는다 - 문제가 되면 다음 단계에서 다룬다.)
    field_id = _resolve_field_id(args)
    if field_id in _field_order:
        _field_order.remove(field_id)
    _field_codes.pop(field_id, None)
    _field_texts.pop(field_id, None)
    log.info("Field_delete: field_id=%s", field_id)
    return None


def handle_Field_select(hwp, doc_id, args):
    # UI에서 필드를 선택 표시하는 용도로 보이며, 지금 단계에서는 별도 동작이
    # 필요하지 않다.
    return None


def handle_Field_getText(hwp, doc_id, args):
    field_id = _resolve_field_id(args)
    return _field_texts.get(field_id, "")


def handle_Field_getNoteIndex(hwp, doc_id, args):
    # 각주/미주 인용은 아직 지원하지 않는다. 0은 "0번째 각주"로 해석될 수
    # 있어서, "각주 아님"을 뜻하도록 null로 바꿔본다.
    return None


def _prune_deleted_fields(hwp) -> None:
    # 사용자가 한글에서 직접 필드를 지워버리면(예: 참고문헌을 통째로 지우고
    # 다시 만들려는 경우) 우리 추적 목록에는 그대로 남아있게 된다. 그 상태로
    # Document.getFields에 죽은 필드를 계속 돌려주면, Zotero가 "이미 있는
    # 필드"라 믿고 그 필드를 재사용하려 드는데, put_field_text는 존재하지
    # 않는 필드에 대해 조용히 아무 것도 안 하기 때문에 - 트랜잭션은 정상
    # 종료되지만 화면에는 아무 변화도 없는 것처럼 보이는 문제가 있었다.
    existing = _existing_hwp_field_names(hwp)
    if existing is None:
        return  # 목록을 못 가져왔으면 기존 추적 상태를 그대로 믿는다
    missing = [fid for fid in _field_order if fid not in existing]
    if not missing:
        return
    if len(missing) == len(_field_order):
        # 추적 중인 필드가 "전부 다" 한꺼번에 사라진 것으로 나오면, 실제로
        # 지워졌다기보다 한글의 필드 목록 조회 자체가 일시적으로 이상한 값을
        # 준 것일 가능성이 높다(문서를 막 열었을 때 등). 이 경우 아무것도
        # 지우지 않고 그대로 둔다 - 인용 정보가 조용히 통째로 사라지는 것보다
        # 죽은 필드를 한 번 더 재사용 시도하다 실패하는 쪽이 훨씬 안전하다.
        log.warning(
            "추적 중인 필드 %d개가 전부 문서에 없다고 나와서 의심스러워 "
            "정리를 건너뜁니다. 실제 필드 목록=%r, 추적 목록=%r",
            len(_field_order), sorted(existing), list(_field_order),
        )
        return
    for fid in missing:
        log.info("문서에서 지워진 필드라 추적 목록에서도 제거함: %s", fid)
        _field_order.remove(fid)
        _field_codes.pop(fid, None)
        _field_texts.pop(fid, None)


def handle_Document_getFields(hwp, doc_id, args):
    _prune_deleted_fields(hwp)
    # addEditCitation 거래 중에는 비어있는 목록으로도 일반적인 "인용 하나
    # 검색해서 추가" 흐름은 잘 동작하는 것이 확인됐으므로, 노트 변환이 아직
    # 없었다면 그대로 빈 목록을 준다 (아래 _EMPTY_FIELDS_COMMANDS 주석 참고).
    if _current_transaction_command in _EMPTY_FIELDS_COMMANDS and not _transaction_had_note_conversion:
        log.info("Document_getFields: %s 거래 중이므로 빈 목록 반환", _current_transaction_command)
        return []

    if _current_transaction_command in _NOTE_INSERT_COMMANDS or _transaction_had_note_conversion:
        # addNote/addAnnotation은 거래 맨 처음부터(주석/노트를 고르기도 전에)
        # 실제 필드 목록이 필요하고, 노트 변환이 일어난 뒤에도 방금 만든
        # (아직 code가 비어있는) 필드들까지 전부 포함해야 한다 - 위
        # _NOTE_INSERT_COMMANDS 주석 참고. 그래서 이 경우엔 거르지 않고
        # 추적 중인 전부를 준다.
        ready = list(_field_order)
    else:
        ready = [
            fid for fid in _field_order if _field_codes.get(fid) not in (None, "", "TEMP")
        ]
    log.info(
        "Document_getFields: 추적 중 %d개 중 완료된 필드 %d개",
        len(_field_order),
        len(ready),
    )
    # Zotero 클라이언트 소스(httpIntegrationClient.js)를 확인한 결과, 배열이
    # 아니라 {"id","code","text","noteIndex"} 키를 가진 객체 목록을 기대한다.
    # (배열로 보내면 각 값이 전부 undefined로 읽혀서 내부적으로 에러가 났다.)
    return [
        {
            "id": fid,
            "code": _field_codes.get(fid, ""),
            "text": _field_texts.get(fid, ""),
            "noteIndex": None,
        }
        for fid in ready
    ]


_PLACEHOLDER_LINK_RE = re.compile(
    r'<a href="https://www\.zotero\.org/\?([^"]+)">(.*?)</a>', re.DOTALL
)


def _insert_note_html(hwp, raw_html: str) -> None:
    # Zotero가 노트(주석) 안의 인용을 <a href="https://www.zotero.org/?<임시ID>">
    # 표시텍스트</a> 형태의 "자리표시 링크"로 바꿔서 통째로 HTML로 보낸다.
    # 나머지(하이라이트 인용문 등)는 평문으로 그대로 넣고, 이 링크 부분만
    # 위치를 기억해뒀다가 뒤이어 오는 Document.convertPlaceholdersToFields
    # 요청에서 진짜 필드로 바꿔준다.
    pos = 0
    for m in _PLACEHOLDER_LINK_RE.finditer(raw_html):
        before = _strip_tags(raw_html[pos:m.start()])
        if before:
            hwp.insert_text(before)

        placeholder_id = m.group(1)
        citation_text = _strip_tags(m.group(2))
        try:
            _, start_para, start_pos = hwp.get_pos()
        except Exception:
            log.exception("주석 인용 위치 기록 실패(시작): placeholder=%s", placeholder_id)
            start_para = start_pos = None

        hwp.insert_text(citation_text)

        try:
            _, end_para, end_pos = hwp.get_pos()
        except Exception:
            log.exception("주석 인용 위치 기록 실패(끝): placeholder=%s", placeholder_id)
            end_para = end_pos = None

        _pending_placeholders[placeholder_id] = {
            "para": start_para if start_para == end_para else None,
            "start": start_pos,
            "end": end_pos,
            "text": citation_text,
        }
        pos = m.end()

    # 맨 끝의 </p></div></body></html> 같은 닫는 태그들 사이에 낀 줄바꿈만
    # 남는 경우가 많아서, 공백만 있으면 굳이 넣지 않는다.
    tail = _strip_tags(raw_html[pos:])
    if tail.strip():
        hwp.insert_text(tail)


def handle_Document_insertText(hwp, doc_id, args):
    # args: [docId, text]
    text = args[1] if len(args) > 1 else (args[0] if args else "")
    if "<a href=\"https://www.zotero.org/?" in text:
        # 노트/주석 삽입: HTML 안에 나중에 진짜 필드로 바꿔야 할 인용
        # 자리표시 링크가 섞여 있다.
        _insert_note_html(hwp, text)
    else:
        hwp.insert_text(text)
    return None


def handle_Document_convertPlaceholdersToFields(hwp, doc_id, args):
    # args: [docId, placeholderIds, noteType, fieldType]
    # (Zotero 클라이언트 소스 기준 실제 배선: 두 번째 자리가 placeholder ID
    # 목록이다 - 파라미터 이름과 실제 순서가 다르게 붙어있어서 소스만 보면
    # 헷갈리지만, 실제 트래픽으로 확인한 순서를 따른다.)
    #
    # Zotero 소스(integration.js _insertNoteIntoDocument) 확인 결과, 인용이
    # 여러 개인 노트에서는 placeholder들을 "문서에서 뒤에 있는 것부터" 순서로
    # 넘겨준다 (앞쪽을 먼저 지우면 뒤쪽 위치가 밀려서 엉망이 되는 걸 막으려고).
    # 그래서 실제 지우기/필드만들기 작업은 그 순서 그대로 해야 하지만,
    # _field_order(추적 목록)에는 문서에 실제 나타나는 순서(앞->뒤)로 넣어야
    # 한다 - Document.getFields를 다시 부를 때 Zotero가 "몇 번째 필드인지"로
    # 새로 만들어진 필드들을 다시 찾아가는데, 그 계산이 문서 순서를 전제로
    # 하기 때문이다 (뒤섞인 순서로 주면 엉뚱한 필드에 Field.setCode/setText가
    # 가거나 아예 안 가서 Zotero 쪽 오류 창이 뜬다).
    placeholder_ids = args[1] if len(args) > 1 else []
    results = []
    new_fields_in_doc_order = []  # (para, start, field_id) - 나중에 위치순 정렬
    for placeholder_id in placeholder_ids:
        info = _pending_placeholders.pop(placeholder_id, None)
        if not info or info.get("para") is None or info.get("start") is None or info.get("end") is None:
            log.warning("주석 인용을 필드로 바꾸지 못함(위치 정보 없음): placeholder=%s", placeholder_id)
            continue
        field_id = f"ZOTERO_{uuid.uuid4().hex[:8]}"
        try:
            hwp.select_text(info["para"], info["start"], info["para"], info["end"])
            hwp.HAction.Run("Delete")  # 방금 평문으로 넣어둔 자리표시 텍스트를 지운다
            try:
                hwp.create_field(field_id, "", "")
            except AttributeError:
                hwp.CreateField(field_id, "", "")
            try:
                hwp.put_field_text(field_id, info["text"])
            except AttributeError:
                hwp.PutFieldText(field_id, info["text"])
        except Exception:
            log.exception("주석 인용을 필드로 바꾸는 중 에러: placeholder=%s", placeholder_id)
            continue
        global _current_field_id, _transaction_had_note_conversion
        _current_field_id = field_id
        _field_codes[field_id] = ""
        _field_texts[field_id] = info["text"]
        _transaction_had_note_conversion = True
        new_fields_in_doc_order.append((info["para"], info["start"], field_id))
        log.info("노트 안 인용을 실제 필드로 변환함: placeholder=%s -> %s", placeholder_id, field_id)
        results.append({"id": field_id, "code": "", "text": info["text"], "noteIndex": None})
    new_fields_in_doc_order.sort(key=lambda t: (t[0], t[1]))
    _field_order.extend(fid for _, _, fid in new_fields_in_doc_order)
    return results


def handle_Document_setBibliographyStyle(hwp, doc_id, args):
    # args: [docId, firstLineIndent, indent, lineSpacing, entrySpacing, tabStops, tabStopCount]
    # 전부 트위프(twip, 1/20 포인트) 단위로 온다 (Zotero의
    # Cite.getBibliographyFormatParameters 기준: 예를 들어 내어쓰기 스타일이면
    # indent=720(=0.5인치), firstLineIndent=-720. lineSpacing은 "240 * 배수"
    # 형태라서 240=홑줄간격(100%)에 대응한다).
    #
    # 텍스트가 아직 필드에 들어가기 전에 이 명령이 먼저 오므로, 여기서는 값만
    # 저장해두고 실제 적용은 뒤이어 오는 Field.setText 처리 후에 한다
    # (_apply_bibliography_paragraph_style 참고).
    global _pending_bib_style
    try:
        first_line_indent_twip = args[1] or 0
        indent_twip = args[2] or 0
        line_spacing_twip = args[3] or 0
        entry_spacing_twip = args[4] or 0
        _pending_bib_style = {
            "first_line_indent_pt": first_line_indent_twip / 20,
            "indent_pt": indent_twip / 20,
            "line_spacing_percent": round(line_spacing_twip / 240 * 100) if line_spacing_twip else 100,
            "entry_spacing_pt": entry_spacing_twip / 20,
        }
    except (IndexError, TypeError, ZeroDivisionError):
        log.warning("Document_setBibliographyStyle: 인자가 예상과 다름 args=%r", args)
        return None
    log.info("Document_setBibliographyStyle: 저장함 %r (원본 args=%r)", _pending_bib_style, args)
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
    "Field.removeCode": handle_Field_removeCode,
    "Field.delete": handle_Field_delete,
    "Field.select": handle_Field_select,
    "Document.getFields": handle_Document_getFields,
    "Document.insertText": handle_Document_insertText,
    "Document.convertPlaceholdersToFields": handle_Document_convertPlaceholdersToFields,
    "Document.setBibliographyStyle": handle_Document_setBibliographyStyle,
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
    global _current_transaction_command, _transaction_had_note_conversion
    _current_transaction_command = initial_command
    _transaction_had_note_conversion = False
    hwp = get_hwp()
    doc_id = get_doc_id(hwp)
    _load_state(hwp, doc_id)
    log.info("=== 트랜잭션 시작: %s (doc_id=%s) ===", initial_command, doc_id)

    try:
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
    finally:
        # 어떻게 끝났든(성공/에러/타임아웃) 지금까지 쌓인 인용 상태는 저장해둔다.
        _save_state(doc_id)


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

    log.info(
        "Zotero 다리 스크립트 시작. Ctrl+Alt+C: 인용 삽입, Ctrl+Alt+A: 주석 삽입, "
        "Ctrl+Alt+N: 노트 삽입, Ctrl+Alt+B: 참고문헌, Ctrl+Alt+S: 스타일/언어 설정"
    )
    print("Ctrl+Alt+C 를 누르면 Zotero 인용 삽입 창이 열립니다.")
    print("Ctrl+Alt+A 를 누르면 Zotero 주석(하이라이트) 삽입 검색창이 바로 열립니다.")
    print("Ctrl+Alt+N 를 누르면 Zotero 노트 삽입 검색창이 바로 열립니다.")
    print("Ctrl+Alt+B 를 누르면 Zotero 참고문헌 삽입 창이 열립니다.")
    print("Ctrl+Alt+S 를 누르면 인용 스타일/언어 설정 창이 열립니다.")
    print("  (이 스크립트를 끄지 않고 계속 켜둔 상태에서 삽입한 인용만 기억합니다.)")
    print("자세한 기록은 zotero_debug.log 파일에서 확인할 수 있습니다.")
    print("종료하려면 트레이 아이콘 메뉴의 '종료'를 누르거나, 이 창에서 Ctrl+C.")

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

    def flush_untitled_transition() -> None:
        # 진행 중인 거래(busy_lock)와 한글 COM 호출이 겹치면 안 되므로, 거래가
        # 없을 때만(락을 바로 잡을 수 있을 때만) 확인한다 - 못 잡으면 이번엔
        # 건너뛰고 다음 기회(다음 주기 점검, 혹은 다음 거래 시작 시의
        # _load_state)에 맡긴다.
        if busy_lock.acquire(blocking=False):
            try:
                _flush_untitled_transition_if_needed()
            finally:
                busy_lock.release()

    def background_state_flusher() -> None:
        while True:
            time.sleep(15)
            try:
                flush_untitled_transition()
            except Exception:
                log.exception("백그라운드 인용 상태 점검 스레드 에러")

    threading.Thread(target=background_state_flusher, daemon=True).start()

    keyboard.add_hotkey("ctrl+alt+c", lambda: start_trigger("addEditCitation"))
    keyboard.add_hotkey("ctrl+alt+a", lambda: start_trigger("addAnnotation"))
    keyboard.add_hotkey("ctrl+alt+n", lambda: start_trigger("addNote"))
    keyboard.add_hotkey("ctrl+alt+b", lambda: start_trigger("addEditBibliography"))
    keyboard.add_hotkey("ctrl+alt+s", lambda: start_trigger("setDocPrefs"))

    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        print("[안내] pystray/Pillow가 없어서 트레이 아이콘/떠다니는 메뉴 없이 단축키만 사용합니다.")
        print("       (아이콘 버튼도 쓰려면 'pip install -r requirements.txt' 후 다시 실행해주세요.)")
        try:
            keyboard.wait()
        except KeyboardInterrupt:
            print("종료합니다.")
        flush_untitled_transition()
        return

    def make_icon_image():
        # 폰트 설치 여부에 기대지 않고, 두꺼운 선으로 큰 Z를 직접 그려서 작은
        # 트레이 아이콘 크기에서도 눈에 잘 띄게 한다. 배경은 눈에 띄는 붉은 원.
        size = 64
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse([2, 2, size - 2, size - 2], fill=(204, 41, 54, 255))
        pad, bar = 15, 9
        top, bottom = pad, size - pad
        draw.line([(pad, top), (size - pad, top)], fill="white", width=bar)
        draw.line([(size - pad, top), (pad, bottom)], fill="white", width=bar)
        draw.line([(pad, bottom), (size - pad, bottom)], fill="white", width=bar)
        return img

    try:
        import tkinter as tk
    except ImportError:
        tk = None
        log.warning("tkinter를 불러올 수 없어 떠다니는 메뉴는 생략합니다 (트레이 아이콘/단축키는 계속 동작).")

    toolbar_root = None
    if tk is not None:
        toolbar_root = tk.Tk()
        toolbar_root.title("Zotero-한글")
        toolbar_root.attributes("-topmost", True)
        toolbar_root.overrideredirect(True)  # 제목줄 없는 작은 패널
        screen_w = toolbar_root.winfo_screenwidth()
        toolbar_root.geometry(f"+{screen_w - 190}+80")

        frame = tk.Frame(toolbar_root, bg="#2b2b2b", padx=6, pady=6)
        frame.pack()

        drag = {"x": 0, "y": 0}

        def start_drag(event):
            drag["x"], drag["y"] = event.x, event.y

        def do_drag(event):
            x = toolbar_root.winfo_x() - drag["x"] + event.x
            y = toolbar_root.winfo_y() - drag["y"] + event.y
            toolbar_root.geometry(f"+{x}+{y}")

        handle = tk.Label(
            frame, text="⠿ Zotero-한글  (드래그해서 옮기기)",
            bg="#2b2b2b", fg="white", font=("맑은 고딕", 9), cursor="fleur",
        )
        handle.pack(fill="x", pady=(0, 4))
        handle.bind("<ButtonPress-1>", start_drag)
        handle.bind("<B1-Motion>", do_drag)

        def make_button(text, command):
            return tk.Button(
                frame, text=text, command=command, width=22,
                font=("맑은 고딕", 9),
            )

        make_button("인용 삽입 (Ctrl+Alt+C)", lambda: start_trigger("addEditCitation")).pack(fill="x", pady=2)
        make_button("주석 삽입 (Ctrl+Alt+A)", lambda: start_trigger("addAnnotation")).pack(fill="x", pady=2)
        make_button("노트 삽입 (Ctrl+Alt+N)", lambda: start_trigger("addNote")).pack(fill="x", pady=2)
        make_button("참고문헌 (Ctrl+Alt+B)", lambda: start_trigger("addEditBibliography")).pack(fill="x", pady=2)
        make_button("스타일/언어 설정 (Ctrl+Alt+S)", lambda: start_trigger("setDocPrefs")).pack(fill="x", pady=2)
        make_button("숨기기 (트레이/단축키는 계속 동작)", toolbar_root.withdraw).pack(fill="x", pady=(6, 0))

        credit = tk.Label(
            frame,
            text="ZoteroHWP V1.00\nDeveloped by Lee GunHyoung (with Claude Code)\npetergunss@gmail.com",
            bg="#2b2b2b", fg="#999999", font=("맑은 고딕", 8), justify="center",
        )
        credit.pack(fill="x", pady=(10, 0))

    def toggle_toolbar(icon=None, item=None):
        if toolbar_root is None:
            return
        if toolbar_root.state() == "withdrawn":
            toolbar_root.deiconify()
        else:
            toolbar_root.withdraw()

    def on_quit(icon, item):
        flush_untitled_transition()
        icon.stop()
        if toolbar_root is not None:
            toolbar_root.after(0, toolbar_root.quit)

    icon = pystray.Icon(
        "zotero_hwp_bridge",
        make_icon_image(),
        "Zotero-한글 다리",
        menu=pystray.Menu(
            pystray.MenuItem("인용 삽입 (Ctrl+Alt+C)", lambda icon, item: start_trigger("addEditCitation")),
            pystray.MenuItem("주석 삽입 (Ctrl+Alt+A)", lambda icon, item: start_trigger("addAnnotation")),
            pystray.MenuItem("노트 삽입 (Ctrl+Alt+N)", lambda icon, item: start_trigger("addNote")),
            pystray.MenuItem("참고문헌 (Ctrl+Alt+B)", lambda icon, item: start_trigger("addEditBibliography")),
            pystray.MenuItem("스타일/언어 설정 (Ctrl+Alt+S)", lambda icon, item: start_trigger("setDocPrefs")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("떠다니는 메뉴 보이기/숨기기", toggle_toolbar),
            pystray.MenuItem("종료", on_quit),
        ),
    )
    print("트레이 아이콘 + 화면에 떠다니는 메뉴가 함께 켜졌습니다 (단축키도 그대로 동작).")
    log.info("트레이 아이콘 + 떠다니는 메뉴 시작")
    # pystray를 detached 모드로 돌려서, tkinter의 메인루프와 한 프로세스에서
    # 함께 돌아가게 한다 (둘 다 "메인 스레드의 루프"를 원하므로, 트레이 쪽을
    # 백그라운드로 돌리고 tkinter 쪽을 메인 스레드에 남겨둔다).
    icon.run_detached()
    if toolbar_root is not None:
        toolbar_root.mainloop()
    else:
        try:
            keyboard.wait()
        except KeyboardInterrupt:
            pass
        flush_untitled_transition()
        icon.stop()
    print("종료합니다.")


if __name__ == "__main__":
    main()
