"""Runs dashboard: streamlit run dashboard.py  (reads data/tasks/*.jsonl, refreshes while a run is live)"""
import json
import os
import time
from datetime import datetime
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

TASKS = Path(os.environ.get("JARVIS_DATA_DIR", Path(__file__).parent / "data")) / "tasks"
LIVE_SECONDS = 30  # a run with no ending whose notebook changed this recently is still going
STATE_EVENT = '"event": "state"'  # the full task snapshot, rewritten after every step; only the last one matters
PARTS = ["AI", "Phone", "Waiting on you", "Other"]
COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#c3c2b7"]
OUTCOMES = {"completed": "✅ Completed", "failed": "❌ Failed", "unconfirmed": "⚠️ Unconfirmed", "needs_user": "❓ Needs you",
            "stopped": "⏹ Stopped", "interrupted": "🔌 Disconnected", "running": "🔵 Running", "incomplete": "… Incomplete"}

st.set_page_config(page_title="Jarvis runs", layout="wide")


# ---------- reading notebooks ----------

def read_events(path: Path) -> list[dict]:
    events, last_state = [], None
    with open(path, encoding="utf-8") as file:
        for line in file:
            if STATE_EVENT in line:
                last_state = line
                continue
            try:
                events.append(json.loads(line))
            except ValueError:
                continue  # the line being written right now
    if last_state:
        try:
            events.append(json.loads(last_state))
        except ValueError:
            pass
    return events


def seconds(at: str) -> float:
    return datetime.fromisoformat(at).timestamp()


def summarize(path: Path, events: list[dict]) -> dict:
    def last(kind):
        return next((e for e in reversed(events) if e["event"] == kind), None)

    task, done, timing = last("task") or {}, last("done"), last("timing")
    turns = [e for e in events if e["event"] == "turn"]
    if done:
        outcome = done["outcome"]
    elif any(e["event"] == "status" and e.get("to") == "interrupted" for e in events):
        outcome = "interrupted"
    elif time.time() - path.stat().st_mtime < LIVE_SECONDS:
        outcome = "running"
    else:
        outcome = "incomplete"
    if timing is None:
        timing = {"total": seconds(events[-1]["at"]) - seconds(events[0]["at"]) if events else 0,
                  "steps": sum(1 for t in turns if t.get("step")),
                  "ai_calls": sum(t.get("ai_calls", 0) for t in turns),
                  "commands": sum(t.get("commands", 0) for t in turns),
                  **{k: sum(t.get(k, 0) for t in turns) for k in ("ai", "phone", "user")}}
    return {"id": path.stem, "goal": task.get("goal", ""), "model": task.get("ai", ""),
            "started": events[0]["at"] if events else None, "outcome": outcome,
            "summary": done["summary"] if done else "", "proof": (done or {}).get("proof"), **timing}


@st.cache_data(ttl=2)
def load_runs() -> list[dict]:
    paths = sorted(TASKS.glob("t-*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [{"summary": summarize(p, ev), "events": ev} for p in paths if (ev := read_events(p))]


def split_turns(events: list[dict]) -> list[dict]:
    """Setup (until setup_done), then one group per turn event; trailing events are the turn in progress."""
    t0 = seconds(events[0]["at"])
    groups, current = [], {"n": 0, "name": "setup", "events": []}
    for e in events:
        e["t"] = seconds(e["at"]) - t0
        if e["event"] in ("state", "done", "timing"):
            continue
        if e["event"] == "setup_done":
            current["end"] = e["t"]
            groups.append(current)
            current = {"n": len(groups), "events": []}
        elif e["event"] == "turn":
            current.update(turn=e, end=e["t"], start=e["t"] - e.get("total", 0))
            groups.append(current)
            current = {"n": len(groups), "events": []}
        else:
            current["events"].append(e)
    if current["events"]:
        current["live"] = True
        groups.append(current)
    return groups


def spans(groups: list[dict]) -> pd.DataFrame:
    rows, sending, asking = [], {}, None
    for g in groups:
        for e in g["events"]:
            kind = e["event"]
            if kind == "ai_call":
                what = f"{e.get('model', '')} · {e.get('input_tokens', 0):,} in ({e.get('cached_tokens', 0):,} cached) · {e.get('output_tokens', 0):,} out"
                rows.append({"lane": "AI", "start": e["t"] - e["ms"] / 1000, "end": e["t"], "turn": g["n"],
                             "what": what if e.get("ok") else f"failed: {e.get('problem')}", "ms": e["ms"],
                             "problem": not e.get("ok")})
            elif kind == "step_sending":
                sending[e["command_id"]] = e
            elif kind == "step_result" and e["command_id"] in sending:
                sent = sending.pop(e["command_id"])
                rows.append({"lane": "Phone", "start": sent["t"], "end": e["t"], "turn": g["n"],
                             "what": f"{sent['action']} {sent.get('label') or ''} → {e['status']} {e.get('error') or ''}".strip(),
                             "ms": e.get("ms", round((e["t"] - sent["t"]) * 1000)), "problem": e["status"] != "ok",
                             "action": sent["action"]})
            elif kind == "ask":
                asking = e
            elif kind == "answer" and asking:
                rows.append({"lane": "You", "start": asking["t"], "end": e["t"], "turn": g["n"],
                             "what": f"{asking['text'][:80]} → {e.get('text') or e['status']}",
                             "ms": round((e["t"] - asking["t"]) * 1000), "problem": False})
                asking = None
    return pd.DataFrame(rows)


def breakdown(row: dict) -> dict:
    ai, phone, user = row.get("ai", 0), row.get("phone", 0), row.get("user", 0)
    return {"AI": ai, "Phone": phone, "Waiting on you": user, "Other": max(0.0, row.get("total", 0) - ai - phone - user)}


def stacked(df: pd.DataFrame, x: str, x_title: str, tooltip_extra: list) -> alt.Chart:
    long = df.melt(id_vars=[x, *tooltip_extra], value_vars=PARTS, var_name="part", value_name="seconds")
    return alt.Chart(long).mark_bar(size=18, cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(
        x=alt.X(f"{x}:O", title=x_title, sort=None),
        y=alt.Y("seconds:Q", title="seconds", stack=True),
        color=alt.Color("part:N", scale=alt.Scale(domain=PARTS, range=COLORS), legend=alt.Legend(orient="top", title=None)),
        order=alt.Order("part_order:Q"),
        tooltip=[*tooltip_extra, "part", alt.Tooltip("seconds:Q", format=".2f")],
    ).transform_calculate(part_order=f"indexof({json.dumps(PARTS)}, datum.part)")


def fmt(sec: float) -> str:
    return f"{sec * 1000:.0f} ms" if sec < 1 else f"{sec:.1f} s" if sec < 60 else f"{int(sec // 60)}m {sec % 60:.0f}s"


# ---------- pages ----------

def overview(runs: list[dict]) -> None:
    st.header("All runs")
    if not runs:
        st.info(f"No runs yet in {TASKS}. Start a task from the phone and it shows up here.")
        return
    df = pd.DataFrame([r["summary"] for r in runs])
    done = df[df.outcome != "running"]
    c = st.columns(4)
    c[0].metric("Runs", len(df))
    c[1].metric("Completed", f"{(df.outcome == 'completed').sum()} / {len(df)}")
    c[2].metric("Median run", fmt(done.total.median()) if len(done) else "–")
    calls = df[df.ai_calls > 0]
    c[3].metric("Median AI call", fmt((calls.ai / calls.ai_calls).median()) if len(calls) else "–")

    chart = df.iloc[::-1].copy()
    chart["run"] = [f"{i + 1}" for i in range(len(chart))]
    chart["goal "] = chart.goal.str.slice(0, 60)
    for part, values in zip(PARTS, zip(*[breakdown(r).values() for r in chart.to_dict("records")])):
        chart[part] = values
    st.subheader("Where the time went, per run (oldest → newest)")
    st.altair_chart(stacked(chart, "run", "run", ["goal ", "outcome"]), width='stretch')

    table = df.assign(outcome=df.outcome.map(lambda o: OUTCOMES.get(o, o)), started=pd.to_datetime(df.started).dt.tz_convert(None))
    st.dataframe(table[["started", "goal", "outcome", "total", "ai", "phone", "user", "steps", "ai_calls", "model", "id"]],
                 hide_index=True, width='stretch',
                 column_config={k: st.column_config.NumberColumn(format="%.1f s") for k in ("total", "ai", "phone", "user")})


def run_page(run: dict) -> None:
    s, events = run["summary"], run["events"]
    groups = split_turns(events)
    turns = [g for g in groups if "turn" in g]
    sp = spans(groups)
    parts = breakdown(s)

    st.header(s["goal"] or s["id"])
    st.markdown(f"**{OUTCOMES.get(s['outcome'], s['outcome'])}** · {s['summary'] or ''}")
    st.caption(f"{s['id']} · {s['model']} · started {s['started']}")
    if s.get("proof"):
        st.caption("Proof on screen: " + " · ".join(f"“{p}”" for p in s["proof"]))

    c = st.columns(6)
    c[0].metric("Total", fmt(s["total"]))
    c[1].metric("AI", fmt(parts["AI"]), f"{parts['AI'] / max(s['total'], 1e-9):.0%} of run", delta_color="off")
    c[2].metric("Phone", fmt(parts["Phone"]), f"{s.get('commands', 0)} commands", delta_color="off")
    c[3].metric("Waiting on you", fmt(parts["Waiting on you"]))
    c[4].metric("Steps", s.get("steps", 0))
    ai_rows = sp[sp.lane == "AI"] if len(sp) else sp
    c[5].metric("Avg AI call", fmt(ai_rows.ms.mean() / 1000) if len(ai_rows) else "–", f"{len(ai_rows)} calls", delta_color="off")

    # what went wrong / where the time went
    st.subheader("Flags")
    flags = []
    if s["outcome"] in ("failed", "unconfirmed", "needs_user", "interrupted", "incomplete"):
        flags.append(("🔴", f"{OUTCOMES[s['outcome']]}: {s['summary'] or 'no summary'}"))
    if turns:
        slow = max(turns, key=lambda g: g["turn"]["total"])
        flags.append(("🐢", f"Slowest turn #{slow['n']} ({slow['turn']['tool']}): {fmt(slow['turn']['total'])} — "
                             f"AI {fmt(slow['turn']['ai'])}, phone {fmt(slow['turn']['phone'])}"))
    if s["total"] and parts["AI"] / s["total"] > 0.6:
        out = [e.get("output_tokens", 0) for e in events if e["event"] == "ai_call" and e.get("ok")]
        flags.append(("🧠", f"AI is {parts['AI'] / s['total']:.0%} of the run; avg {sum(out) // max(1, len(out)):,} output tokens per call "
                             "(try JARVIS_EFFORT=low or a faster model)"))
    invalid = [e["reason"] for e in events if e["event"] == "invalid_reply"]
    if invalid:
        flags.append(("⚠️", f"{len(invalid)} AI replies rejected: " + "; ".join(dict.fromkeys(invalid))[:300]))
    no_change = [g for g in turns if (g["turn"].get("step") or {}).get("change", "").startswith("no change")]
    if no_change:
        flags.append(("⚠️", f"{len(no_change)} actions changed nothing: turns " + ", ".join(f"#{g['n']}" for g in no_change)))
    if len(sp):
        bad = sp[(sp.lane == "Phone") & sp.problem]
        if len(bad):
            flags.append(("🔴", f"{len(bad)} phone commands failed or were rejected: " + "; ".join(bad.what.head(5))))
        failed_ai = sp[(sp.lane == "AI") & sp.problem]
        if len(failed_ai):
            flags.append(("🔴", f"{len(failed_ai)} AI calls failed: " + "; ".join(failed_ai.what.head(3))))
    for icon, text in flags or [("✅", "Nothing stands out.")]:
        st.markdown(f"{icon} {text}")

    st.subheader("Where the time went")
    st.altair_chart(alt.Chart(pd.DataFrame([{"part": k, "seconds": v} for k, v in parts.items()])).mark_bar(size=22).encode(
        x=alt.X("sum(seconds):Q", stack="normalize", title=None, axis=alt.Axis(format="%")),
        color=alt.Color("part:N", scale=alt.Scale(domain=PARTS, range=COLORS), legend=alt.Legend(orient="top", title=None)),
        order=alt.Order("part_order:Q"), tooltip=["part", alt.Tooltip("seconds:Q", format=".2f")],
    ).transform_calculate(part_order=f"indexof({json.dumps(PARTS)}, datum.part)").properties(height=60), width='stretch')
    st.caption("Other = wait steps, backoff after AI errors, screenshot drawing and checks.")

    if turns:
        st.subheader("Latency per turn")
        df = pd.DataFrame([{"turn": g["n"], "tool": g["turn"].get("tool") or "–",
                            "label": ((g["turn"].get("step") or {}).get("label") or "")[:50],
                            **breakdown(g["turn"])} for g in turns])
        st.altair_chart(stacked(df, "turn", "turn", ["tool", "label"]), width='stretch')

    if len(sp):
        st.subheader("Timeline")
        st.altair_chart(alt.Chart(sp).mark_bar(height=14, cornerRadius=2).encode(
            x=alt.X("start:Q", title="seconds since start"), x2="end:Q",
            y=alt.Y("lane:N", sort=["AI", "Phone", "You"], title=None),
            color=alt.Color("lane:N", scale=alt.Scale(domain=["AI", "Phone", "You"], range=COLORS[:3]), legend=None),
            stroke=alt.condition("datum.problem", alt.value("#d03b3b"), alt.value(None)), strokeWidth=alt.value(2),
            tooltip=["turn", "what", "ms"],
        ).properties(height=140), width='stretch')

        phone = sp[sp.lane == "Phone"]
        if len(phone):
            st.subheader("Phone commands by type")
            stats = phone.groupby("action").ms.agg(["count", "mean", "median", "max"]).reset_index().sort_values("mean", ascending=False)
            st.dataframe(stats.rename(columns={"mean": "avg ms", "median": "median ms", "max": "max ms"}).round(0),
                         hide_index=True, width='stretch')

    st.subheader("Journey")
    slowest = {g["n"] for g in sorted(turns, key=lambda g: -g["turn"]["total"])[:3]} if len(turns) > 3 else set()
    for g in groups:
        st.markdown(journey_title(g, slowest), unsafe_allow_html=False)
        with st.expander("details", expanded=g.get("live", False)):
            if "turn" in g:
                t = g["turn"]
                st.caption(f"{fmt(t['total'])} = AI {fmt(t['ai'])} ({t['ai_calls']} calls) + phone {fmt(t['phone'])} "
                           f"({t['commands']} commands) + you {fmt(t['user'])}")
            for e in g["events"]:
                st.text(f"+{e['t']:7.2f}s  {describe(e)}")

    with st.expander(f"All events ({len(events)})"):
        st.dataframe(pd.DataFrame([{k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in e.items()}
                                   for e in events if e["event"] != "state"]), width='stretch')


def journey_title(g: dict, slowest: set) -> str:
    if g.get("name") == "setup":
        return f"**Setup** — connect, list apps, first screen · {fmt(g.get('end', 0))}"
    t = g.get("turn")
    if t is None:
        return f"**#{g['n']}** 🔵 in progress…"
    step = t.get("step") or {}
    decision = next((e for e in reversed(g["events"]) if e["event"] == "decision"), {})
    args = decision.get("args") or {}
    what = step.get("label") or args.get("reason") or args.get("summary") or args.get("question") or ""
    result = step.get("change") or step.get("error") or step.get("status") or ""
    status = step.get("status", "")
    icon = ("🔴" if status in ("failed", "rejected", "blocked") else "🟠" if status == "unknown"
            else "🟡" if result.startswith("no change") or status in ("declined", "cancelled", "not sent") else "🟢" if step else "⚪")
    tags = []
    if g["n"] in slowest:
        tags.append("🐢 slow")
    if t.get("ai_calls", 0) > 1:
        tags.append(f"{t['ai_calls']} AI calls")
    if any(e["event"] == "invalid_reply" for e in g["events"]):
        tags.append("⚠️ rejected reply")
    line = f"**#{g['n']}** `{t.get('tool') or '–'}` {what[:90]}"
    if result:
        line += f"  {icon} → {result[:120]}"
    return f"{line}  ·  **{fmt(t['total'])}**" + (f"  ·  {' · '.join(tags)}" if tags else "")


def describe(e: dict) -> str:
    kind = e["event"]
    if kind == "ai_call":
        if not e.get("ok"):
            return f"AI call failed after {e['ms']} ms: {e.get('problem')}"
        return (f"AI call {e['ms']} ms · {e.get('input_tokens', 0):,} in ({e.get('cached_tokens', 0):,} cached) · "
                f"{e.get('output_tokens', 0):,} out" + (" · picture" if e.get("image") else "") + (" · retry" if e.get("retry") else ""))
    if kind == "decision":
        return f"decided {e['tool']} {json.dumps(e.get('args'), ensure_ascii=False)}" + (f"  ({e['note']})" if e.get("note") else "")
    if kind == "invalid_reply":
        return f"REJECTED reply: {e['reason']}"
    if kind == "step_sending":
        return f"→ phone {e['action']} {e.get('label') or ''}" + (" (confirmed)" if e.get("confirmed") else "")
    if kind == "step_result":
        return f"← {e['status']} {e.get('error') or ''} in {e.get('ms', '?')} ms"
    if kind == "screenshot":
        return f"screenshot {e.get('bytes', 0) // 1024} KB ({e.get('why')})"
    if kind == "ask":
        return f"asked you ({e['kind']}): {e['text']}"
    if kind == "answer":
        return f"you answered: {e.get('text') or e['status']}"
    rest = {k: v for k, v in e.items() if k not in ("at", "event", "t")}
    return f"{kind} {json.dumps(rest, ensure_ascii=False) if rest else ''}"


# ---------- layout ----------

runs = load_runs()
with st.sidebar:
    st.title("Jarvis runs")
    live = st.toggle("Live refresh", value=True)
    labels = {"overview": "📊 All runs"} | {r["summary"]["id"]: f"{OUTCOMES.get(r['summary']['outcome'], '')[:2]} "
                                                               f"{r['summary']['goal'][:40] or r['summary']['id']}"
                                          for r in runs}
    choice = st.radio("Run", list(labels), format_func=labels.get, label_visibility="collapsed")

if choice == "overview":
    overview(runs)
else:
    run_page(next(r for r in runs if r["summary"]["id"] == choice))

if live:
    time.sleep(3)
    st.rerun()
