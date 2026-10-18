from src.agent.screen import render
from src.phone.link import Interrupted, PhoneGone, new_command
from src.phone.messages import Done, Result
from src.server.api import Run


async def run_smoke(run: Run) -> None:
    link = run.link
    answered = 0

    async def send(action: str, show: bool, **fields) -> None:
        nonlocal answered
        result = await link.run(new_command(action, **fields))
        if not (result.error and result.error.code == "no_result"):
            answered += 1
        print_result(action, result, link.apps, show)

    try:
        await send("read_screen", True)
        await send("go_home", False)
        app = pick_app(link.apps)
        if app:
            await send("open_app", True, package_name=app)
        await send("go_back", True)
    except Interrupted as stopped:
        if isinstance(stopped, PhoneGone):
            raise
        await link.finish(Done(outcome="stopped", summary="Smoke test stopped."))
        return

    if answered:
        done = Done(outcome="completed", summary=f"Smoke test passed: the phone answered {answered} commands.")
    else:
        done = Done(outcome="failed", summary="Smoke test failed: the phone answered no commands.")
    print(done.summary)
    await link.finish(done)


def pick_app(apps: dict[str, str]) -> str | None:
    for package, label in apps.items():
        if label.strip().lower() == "doordash":
            return package
    return next(iter(apps), None)


def print_result(action: str, result: Result, apps: dict[str, str], show: bool) -> None:
    line = f"{action}: {result.status}"
    if result.error:
        line += f" ({result.error.code}: {result.error.message})"
    print(line)
    if show and result.screen:
        print(render(result.screen, apps).text)
        print()
