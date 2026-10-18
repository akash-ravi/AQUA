import json

import pytest

from jarvis.messages import (
    Answer,
    BadMessage,
    Command,
    Done,
    Hello,
    Node,
    Pause,
    Rejoin,
    Result,
    Resume,
    Screen,
    Stop,
    TaskRequest,
    clean_screen,
    new_id,
    new_rejoin_key,
    parse_backend_message,
    parse_phone_message,
    to_json,
)

HELLO = {
    "type": "hello",
    "protocol": 1,
    "device": {"id": "fake-1234", "name": "Pixel 9", "sdk_int": 36},
    "actions": ["read_screen", "tap"],
    "apps": [{"label": "DoorDash", "package_name": "com.dd.doordash"}],
}


def screen_data(nodes: list[dict] | None = None) -> dict:
    return {"screen_id": "s-1", "package_name": "com.dd.doordash", "width": 1080, "height": 2400,
            "nodes": nodes if nodes is not None else [{"index": 1, "text": "Back", "bounds": [0, 0, 100, 100]}]}


def parse(data: dict):
    return parse_phone_message(json.dumps(data))


def rejected(raw) -> BadMessage:
    with pytest.raises(BadMessage) as caught:
        parse_phone_message(raw)
    return caught.value


def test_hello():
    message = parse(HELLO)
    assert isinstance(message, Hello)
    assert message.device.id == "fake-1234"
    assert message.apps[0].package_name == "com.dd.doordash"


def test_simple_phone_messages():
    assert isinstance(parse({"type": "task", "text": "order a pizza"}), TaskRequest)
    assert isinstance(parse({"type": "pause"}), Pause)
    assert isinstance(parse({"type": "resume"}), Resume)
    assert isinstance(parse({"type": "stop"}), Stop)
    assert parse({"type": "update", "text": "make it medium"}).text == "make it medium"
    answer = parse({"type": "answer", "ask_id": "q-1", "approved": True})
    assert isinstance(answer, Answer) and answer.approved is True and answer.text is None
    rejoin = parse({"type": "rejoin", "task_id": "t-1", "rejoin_key": "k",
                    "last_result": {"command_id": "c-1", "status": "unknown"}})
    assert isinstance(rejoin, Rejoin) and rejoin.last_result.status == "unknown"


def test_result_with_screen_and_defaults():
    data = {"type": "result", "command_id": "c-1", "status": "ok", "settled": True, "settle_ms": 420, "acted_on": 5,
            "method": "action", "screen": screen_data([
                {"index": 1, "text": "Back", "bounds": [0, 0, 100, 100], "clickable": True, "new_field": 1},
                {"index": 2, "parent": 1, "bounds": [0, 0, 50, 50], "scroll": ["down", "sideways"]},
            ]), "something_new": True}
    message = parse(data)
    assert isinstance(message, Result)
    first, second = message.screen.nodes
    assert first.bounds == (0, 0, 100, 100) and first.clickable and first.enabled
    assert not first.editable and not first.checked
    assert second.scroll == ["down"]
    assert message.screen.keyboard_top is None


def test_bytes_are_refused():
    error = rejected(b'{"type": "stop"}')
    assert error.code == "bad_message"
    assert "binary" in error.message


def test_not_json_or_not_an_object():
    assert rejected("{nope").code == "bad_message"
    assert rejected("[1, 2]").code == "bad_message"


def test_missing_or_unknown_type():
    assert rejected(json.dumps({"text": "hi"})).code == "unexpected_message"
    assert rejected(json.dumps({"type": "ready", "task_id": "t", "rejoin_key": "k"})).code == "unexpected_message"
    assert rejected(json.dumps({"type": "dance"})).code == "unexpected_message"


def test_validation_errors_are_bad_messages():
    assert rejected(json.dumps({**HELLO, "protocol": "one"})).code == "bad_message"
    assert rejected(json.dumps({"type": "hello", "protocol": 1})).code == "bad_message"
    assert rejected(json.dumps({"type": "task", "text": ""})).code == "bad_message"
    assert rejected(json.dumps({"type": "result", "command_id": "c-1", "status": "great"})).code == "bad_message"
    assert rejected(json.dumps({"type": "answer"})).code == "bad_message"
    bad_screen = {"type": "result", "command_id": "c-1", "status": "ok", "screen": {**screen_data(), "width": 0}}
    assert rejected(json.dumps(bad_screen)).code == "bad_message"


def test_text_limits():
    assert parse({"type": "task", "text": "x" * 2000}).text == "x" * 2000
    assert rejected(json.dumps({"type": "task", "text": "x" * 2001})).code == "bad_message"
    assert rejected(json.dumps({"type": "update", "text": "x" * 2001})).code == "bad_message"
    assert rejected(json.dumps({"type": "answer", "ask_id": "q-1", "text": "x" * 2001})).code == "bad_message"


def test_size_limits():
    padding = "x" * 64_000
    assert rejected(json.dumps({"type": "stop", "padding": padding})).code == "bad_message"
    result = {"type": "result", "command_id": "c-1", "status": "ok", "padding": padding}
    assert isinstance(parse(result), Result)
    huge = json.dumps({"type": "result", "command_id": "c-1", "status": "ok", "padding": "x" * 12_000_001})
    assert rejected(huge).code == "bad_message"


def test_image_size_limit():
    image = {"format": "jpeg", "data": "a" * 11_000_000, "width": 720, "height": 1600}
    result = {"type": "result", "command_id": "c-1", "status": "ok", "image": image}
    assert len(parse(result).image.data) == 11_000_000
    too_big = {**result, "image": {**image, "data": "a" * 11_000_001}}
    assert rejected(json.dumps(too_big)).code == "bad_message"


def test_node_texts_are_cut_and_nodes_capped():
    long = "y" * 600
    nodes = [{"index": i, "bounds": [0, 0, 10, 10]} for i in range(1, 1601)]
    nodes[0].update({"text": long, "description": long, "hint": long})
    result = parse({"type": "result", "command_id": "c-1", "status": "ok", "screen": screen_data(nodes)})
    first = result.screen.nodes[0]
    assert len(first.text) == len(first.description) == len(first.hint) == 500
    assert len(result.screen.nodes) == 1500
    assert result.screen.truncated is True


def test_short_screens_are_not_marked_truncated():
    result = parse({"type": "result", "command_id": "c-1", "status": "ok", "screen": screen_data()})
    assert result.screen.truncated is False


def test_apps_are_capped():
    apps = [{"label": f"App {i}", "package_name": f"com.example.app{i}"} for i in range(600)]
    hello = parse({**HELLO, "apps": apps})
    assert len(hello.apps) == 500
    assert hello.apps[0].label == "App 0"


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


def test_to_json_leaves_out_none():
    command = Command(command_id="c-1", action="tap", screen_id="s-1", target=4)
    data = json.loads(to_json(command))
    assert data == {"type": "command", "command_id": "c-1", "action": "tap", "revision": 0, "timeout_ms": 10_000,
                    "screen_id": "s-1", "target": 4}
    done = json.loads(to_json(Done(task_id="t-1", outcome="failed", summary="no")))
    assert "proof" not in done and "last_action" not in done


def test_backend_messages_round_trip():
    command = Command(command_id="c-1", action="type_text", screen_id="s-1", target=2, text="pizza", submit=True)
    assert parse_backend_message(to_json(command)) == command
    with pytest.raises(BadMessage):
        parse_backend_message(json.dumps({"type": "hello"}))


def test_ids():
    assert new_id("c").startswith("c-")
    assert new_id("t") != new_id("t")
    key = new_rejoin_key()
    assert len(key) >= 20 and key != new_rejoin_key()
