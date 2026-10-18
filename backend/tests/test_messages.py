import json

import pytest

from jarvis.messages import (
    Answer,
    BadMessage,
    Command,
    Node,
    Screen,
    Start,
    Stop,
    WireResult,
    clean_screen,
    new_id,
    parse_phone_message,
)
from jarvis.wire import message_to_wire, result_from_wire

START = {"type": "start", "task": "order a burrito", "device": {"width": 1080, "height": 2424, "sdk": 36}}


def node_data(index: int = 0, **fields) -> dict:
    return {"index": index, "class": "Button", "text": "Back", "desc": None, "id": None, "bounds": [0, 0, 100, 100],
            "clickable": True, "editable": False, "scrollable": False, "checked": None, **fields}


def screen_result(nodes: list[dict]) -> WireResult:
    data = {"screenId": "s7", "package": "com.dd.doordash", "nodes": nodes}
    return WireResult(id="c-1", ok=True, data=data)


def parse(data: dict):
    return parse_phone_message(json.dumps(data))


def rejected(raw) -> BadMessage:
    with pytest.raises(BadMessage) as caught:
        parse_phone_message(raw)
    return caught.value


def test_start():
    message = parse(START)
    assert isinstance(message, Start)
    assert message.task == "order a burrito" and message.device.width == 1080


def test_simple_phone_messages():
    assert isinstance(parse({"type": "stop"}), Stop)
    answer = parse({"type": "answer", "id": "q1", "text": "yes"})
    assert isinstance(answer, Answer) and answer.ask_id == "q1" and answer.text == "yes"
    result = parse({"type": "result", "id": "2", "ok": False, "error": "staleScreen", "message": "old", "extra": 1})
    assert isinstance(result, WireResult) and result.error == "staleScreen"


def test_bytes_are_refused():
    error = rejected(b'{"type": "stop"}')
    assert error.code == "bad_message"
    assert "binary" in error.message


def test_not_json_or_not_an_object():
    assert rejected("{nope").code == "bad_message"
    assert rejected("[1, 2]").code == "bad_message"


def test_missing_or_unknown_type():
    assert rejected(json.dumps({"task": "hi"})).code == "unexpected_message"
    assert rejected(json.dumps({"type": "hello"})).code == "unexpected_message"
    assert rejected(json.dumps({"type": "dance"})).code == "unexpected_message"


def test_validation_errors_are_bad_messages():
    assert rejected(json.dumps({"type": "start", "task": "x"})).code == "bad_message"
    assert rejected(json.dumps({**START, "task": ""})).code == "bad_message"
    assert rejected(json.dumps({**START, "device": {"width": 0, "height": 10}})).code == "bad_message"
    assert rejected(json.dumps({"type": "result", "id": "1"})).code == "bad_message"
    assert rejected(json.dumps({"type": "answer", "text": "yes"})).code == "bad_message"


def test_text_limits():
    assert parse({**START, "task": "x" * 2000}).task == "x" * 2000
    assert rejected(json.dumps({**START, "task": "x" * 2001})).code == "bad_message"
    assert rejected(json.dumps({"type": "answer", "id": "q1", "text": "x" * 2001})).code == "bad_message"


def test_size_limits():
    padding = "x" * 64_000
    assert rejected(json.dumps({"type": "stop", "padding": padding})).code == "bad_message"
    assert isinstance(parse({"type": "result", "id": "1", "ok": True, "padding": padding}), WireResult)
    huge = json.dumps({"type": "result", "id": "1", "ok": True, "padding": "x" * 12_000_001})
    assert rejected(huge).code == "bad_message"


def test_node_texts_are_cut_and_nodes_capped():
    long = "y" * 600
    nodes = [node_data(i) for i in range(1600)]
    nodes[0].update({"text": long, "desc": long})
    result = parse({"type": "result", "id": "c-1", "ok": True,
                    "data": {"screenId": "s7", "package": "p", "nodes": nodes}})
    assert len(result.data["nodes"]) == 1500 and result.data["truncated"] is True
    assert len(result.data["nodes"][0]["text"]) == len(result.data["nodes"][0]["desc"]) == 500


def test_screen_from_wire_maps_names_and_nests_by_bounds():
    nodes = [
        node_data(0, bounds=[0, 0, 1080, 2000], clickable=False, **{"class": "FrameLayout"}),
        node_data(1, bounds=[0, 0, 500, 200], text="Pepperoni"),
        node_data(2, bounds=[10, 10, 100, 100], text="Add", desc="add item", id="a:id/add", checked=True),
        node_data(3, bounds=[600, 0, 900, 200], editable=True, scrollable=True),
    ]
    command = Command(command_id="c-1", action="read_screen")
    screen = result_from_wire(screen_result(nodes), command, 1080, 2424).screen
    assert screen.screen_id == "s7" and screen.package_name == "com.dd.doordash" and screen.height == 2424
    assert [n.parent for n in screen.nodes] == [None, 0, 1, 0]
    add = screen.nodes[2]
    assert (add.description, add.resource_id, add.checkable, add.checked) == ("add item", "a:id/add", True, True)
    assert screen.nodes[3].editable and screen.nodes[3].scrollable and not screen.nodes[3].checkable


def test_failed_results_become_rejected_or_failed():
    command = Command(command_id="c-1", action="tap")
    stale = result_from_wire(WireResult(id="c-1", ok=False, error="staleScreen", message="m"), command, 1, 1)
    assert (stale.status, stale.error.code) == ("rejected", "stale_screen")
    broken = result_from_wire(WireResult(id="c-1", ok=False, error="actionFailed"), command, 1, 1)
    assert broken.status == "failed"
    shot = Command(command_id="c-2", action="screenshot")
    secure = result_from_wire(WireResult(id="c-2", ok=False, error="secureWindow"), shot, 1, 1)
    assert secure.error.code == "screenshot_blocked"


def test_screenshot_and_wrong_shapes():
    shot = Command(command_id="c-2", action="screenshot")
    ok = result_from_wire(WireResult(id="c-2", ok=True, data={"png": "AAAA", "width": 720, "height": 1616}), shot, 1, 1)
    assert ok.image.format == "png" and ok.image.width == 720
    bad = result_from_wire(WireResult(id="c-2", ok=True, data={}), shot, 1, 1)
    assert bad.status == "failed" and bad.error.code == "bad_result"


def make_screen(nodes: list[Node]) -> Screen:
    return Screen(screen_id="s-1", width=1000, height=2000, nodes=nodes)


def test_clean_screen_drops_duplicate_indexes():
    screen = make_screen([
        Node(index=1, text="first", bounds=(0, 0, 100, 100)),
        Node(index=1, text="second", bounds=(0, 0, 100, 100)),
        Node(index=2, text="other", bounds=(0, 0, 100, 100)),
    ])
    cleaned = clean_screen(screen)
    assert [(n.index, n.text) for n in cleaned.nodes] == [(1, "first"), (2, "other")]


def test_clean_screen_clips_and_drops_zero_size():
    screen = make_screen([
        Node(index=1, text="wide", bounds=(-50, -10, 1200, 2100)),
        Node(index=2, text="flat", bounds=(0, 100, 500, 100)),
        Node(index=3, text="off screen", bounds=(1100, 0, 1200, 100)),
        Node(index=4, text="fine", bounds=(10, 10, 20, 20)),
    ])
    cleaned = clean_screen(screen)
    assert [n.index for n in cleaned.nodes] == [1, 4]
    assert cleaned.nodes[0].bounds == (0, 0, 1000, 2000)


def test_clean_screen_fixes_orphan_and_forward_parents():
    screen = make_screen([
        Node(index=1, parent=2, text="points forward", bounds=(0, 0, 10, 10)),
        Node(index=2, parent=1, text="points back", bounds=(0, 0, 10, 10)),
        Node(index=3, parent=99, text="orphan", bounds=(0, 0, 10, 10)),
        Node(index=4, parent=4, text="self", bounds=(0, 0, 10, 10)),
    ])
    cleaned = clean_screen(screen)
    assert [n.parent for n in cleaned.nodes] == [None, 1, None, None]


def test_clean_screen_keeps_order_and_original():
    screen = make_screen([Node(index=5, text="b", bounds=(0, 0, 10, 10)), Node(index=2, text="a", bounds=(0, 0, 10, 10))])
    cleaned = clean_screen(screen)
    assert [n.index for n in cleaned.nodes] == [5, 2]
    assert cleaned is not screen


def test_parent_dropped_for_zero_size_becomes_none():
    screen = make_screen([
        Node(index=1, bounds=(0, 0, 0, 0)),
        Node(index=2, parent=1, text="child", bounds=(0, 0, 10, 10)),
    ])
    assert clean_screen(screen).nodes[0].parent is None


def test_commands_go_out_in_the_phone_format():
    sent = message_to_wire(Command(command_id="c-1", action="type_text", screen_id="s1", target=2, text="pizza"))
    assert sent == {"type": "command", "id": "c-1", "name": "setText",
                    "args": {"screenId": "s1", "index": 2, "text": "pizza"}}
    submit = Command(command_id="c-5", action="type_text", screen_id="s1", target=2, text="pizza", submit=True)
    assert message_to_wire(submit)["args"]["submit"] is True
    assert message_to_wire(Command(command_id="c-2", action="go_back"))["args"] == {}
    scroll = Command(command_id="c-3", action="scroll", screen_id="s1", target=3, direction="down")
    assert message_to_wire(scroll)["args"] == {"screenId": "s1", "index": 3, "direction": "down"}
    shot = Command(command_id="c-4", action="screenshot", max_side=1280)
    assert message_to_wire(shot)["args"] == {"maxWidth": 1280}


def test_ids():
    assert new_id("c").startswith("c-")
    assert new_id("t") != new_id("t")
