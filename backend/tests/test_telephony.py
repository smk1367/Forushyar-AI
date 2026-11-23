from app.services.telephony.grandstream import (
    customer_phone_candidates,
    normalize_cdr,
    normalize_recording_references,
    parse_cdr_csv,
    parse_cdr_payload,
)


def test_parse_grandstream_tcp_csv_row():
    values = [
        "1", "acct", "09121234567", "101", "ext-did", "",
        "PJSIP/trunk-a", "", "Dial", "", "2026-11-17 10:00:00",
        "", "2026-11-17 10:00:12", "12", "0", "NO ANSWER", "",
        "uid-1", "inbound",
    ]
    parsed = parse_cdr_csv(",".join(values))
    assert len(parsed) == 1
    assert parsed[0]["src"] == "09121234567"
    assert parsed[0]["dst"] == "101"
    assert parsed[0]["userfield"] == "inbound"


def test_normalize_inbound_unanswered_call_and_recording():
    record = {
        "src": "09121234567",
        "dst": "101",
        "dcontext": "ext-did",
        "userfield": "inbound",
        "disposition": "NO ANSWER",
        "duration": "18",
        "billsec": "0",
        "uniqueid": "ucm-unique-1",
        "recordfiles": "monitor/2026-11-17-09121234567.wav",
    }
    normalized = normalize_cdr(record)
    assert normalized["direction"] == "inbound"
    assert normalized["customer_number"] == "09121234567"
    assert normalized["status"] == "missed"
    assert normalized["duration_seconds"] == 18
    assert normalized["recordings"] == [
        {"filedir": "monitor", "filename": "2026-11-17-09121234567.wav"}
    ]


def test_normalize_grouped_outbound_session():
    record = {
        "cdr": "session-7",
        "main_cdr": {
            "src": "102",
            "dst": "09351234567",
            "start": "2026-11-17 11:00:00",
            "duration": "40",
            "billsec": "32",
        },
        "sub_cdr_1": {
            "src": "102",
            "dst": "09351234567",
            "userfield": "external",
            "dst_trunk_name": "SIP-Trunk",
            "channel_ext": "102",
            "disposition": "ANSWERED",
            "recordfiles": ["monitor/outbound.wav"],
        },
    }
    normalized = normalize_cdr(record)
    assert normalized["ucm_unique_id"] == "session-7"
    assert normalized["direction"] == "outbound"
    assert normalized["customer_number"] == "09351234567"
    assert normalized["extension"] == "102"
    assert normalized["status"] == "answered"


def test_parse_json_and_xml_cdr_payloads():
    json_payload = b'{"cdr_root":[{"src":"09120000000","dst":"100","uniqueid":"json-1"}]}'
    xml_payload = b'<cdr><src>09120000001</src><dst>101</dst><uniqueid>xml-1</uniqueid></cdr>'
    assert parse_cdr_payload(json_payload)[0]["uniqueid"] == "json-1"
    assert parse_cdr_payload(xml_payload, "application/xml")[0]["uniqueid"] == "xml-1"


def test_recording_reference_rejects_path_traversal():
    assert normalize_recording_references(["../secret.wav", "monitor/ok.wav"]) == [
        {"filedir": "monitor", "filename": "ok.wav"}
    ]


def test_iranian_mobile_matches_stored_domestic_number():
    expected = ("9123456789", "09123456789", "989123456789", "00989123456789")
    for value in (*expected, "+989123456789", '"Desk 2" <9123456789>'):
        assert customer_phone_candidates(value) == expected


def test_extensions_landlines_and_foreign_numbers_keep_exact_matching():
    for value in ("101", "02112345678", "491234567890"):
        assert customer_phone_candidates(value) == (value,)
    assert customer_phone_candidates("") == ()
