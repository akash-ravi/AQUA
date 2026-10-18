"""Search must be submitted, not merely typed; all phone/app traffic is synthetic."""
import json

import pytest

from jarvis.brain import Limits, brain_runner
from jarvis.messages import Command, WireResult
from jarvis.wire import result_from_wire
from tests.fakes.fake_ai import FAKEDASH_SCRIPT, FakeAI, FakeStep
from tests.fakes.fake_phone import FakePhone, run_fake_phone
from tests.fakes.fakedash import DOORDASH, FakeDash, button


def search_phone(app=None):
    app = app or FakeDash()
    app.package, app.page = DOORDASH, "dd_search"
    return FakePhone(app)


def type_query(phone, submit):
    screen = phone.read_screen()
    target = next(node.index for node in screen.nodes if node.editable)
    return phone.handle(Command(command_id="search-1", action="type_text", screen_id=screen.screen_id,
                                target=target, text="pizza", submit=submit))


def test_typing_without_submit_keeps_suggestions_open():
    phone = search_phone()
    assert type_query(phone, False).status == "ok"
    assert phone.app.search_text == "pizza"
    assert phone.app.page == "dd_search"
    assert phone.app.query == ""


def test_submit_runs_the_search():
    phone = search_phone()
    result = type_query(phone, True)
    assert result.status == "ok" and result.submitted is True
    assert phone.app.query == "pizza"
    assert phone.app.page != "dd_search"


class ButtonSearch(FakeDash):
    """A search screen whose field cannot invoke the keyboard's Search action."""

    def _search(self):
        page = super()._search()
        next(el for el in page.els if el.editable).submit = None
        page.els.append(button("Search now", self._submit_search))
        return page


def test_failed_submit_preserves_the_entered_query():
    phone = search_phone(ButtonSearch())
    result = type_query(phone, True)
    assert result.status == "failed" and result.error.code == "cant_submit"
    assert phone.app.search_text == "pizza"
    assert phone.app.query == ""


@pytest.mark.parametrize("data,expected", [({}, None), ({"submitted": True}, True),
                                          ({"submitted": False}, False), ({"submitted": "true"}, None)])
def test_submission_acknowledgement_is_explicit(data, expected):
    command = Command(command_id="c1", action="type_text", submit=True)
    result = result_from_wire(WireResult(id="c1", ok=True, data=data), command, 1080, 2400)
    assert result.submitted is expected


def test_cant_submit_is_partial_failure_not_safe_to_replay():
    result = result_from_wire(WireResult(id="c1", ok=False, error="cantSubmit", message="text entered"),
                              Command(command_id="c1", action="type_text", submit=True), 1080, 2400)
    assert result.status == "failed"
    assert result.error.code == "cant_submit"


@pytest.mark.parametrize("legacy", [False, True])
async def test_search_recovers_with_a_visible_button_without_retyping(start_server, test_settings, legacy):
    class LegacyPhone(FakePhone):
        def _type_text(self, command):
            # Older Android silently ignores the submit flag and returns an empty success.
            return super()._type_text(command.model_copy(update={"submit": False}))

    phone = LegacyPhone(ButtonSearch()) if legacy else FakePhone(ButtonSearch())
    ai = FakeAI(list(FAKEDASH_SCRIPT[:4]) + [
        FakeStep("tap", {"why": "Submit the search"}, find="Search now"),
        FakeStep("finish", {"summary": "Found pizza stores", "proof": ["Tony's Pizzeria"]}),
    ])
    server = await start_server(run_task=brain_runner(ai, Limits(loading_pause=0.01)))
    done = await run_fake_phone(server.url, "Search DoorDash for pizza", phone=phone)
    assert done.outcome == "completed", done.summary
    assert phone.app.query == "pizza"
    expected = "did not confirm search submission" if legacy else "search was not submitted"
    assert any(expected in message for message in ai.messages)
    events = [json.loads(line) for line in
              (test_settings.data_dir / "tasks" / f"{done.task_id}.jsonl").read_text().splitlines()]
    assert sum(event["event"] == "step_sending" and event.get("action") == "type_text" for event in events) == 1
