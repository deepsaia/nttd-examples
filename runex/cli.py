"""`runex`: pick an approach, pick a session, play it.

Attaching a runner by hand means four things in the right order: know which runner, know the
session id, know the participant token, and know that the servers involved are up. Each one is
easy and each one is easy to get wrong, and the failure from getting one wrong arrives late and
looks like something else. A missing token reads as an authorisation bug, a neuro-san server
that is not running reads as an agent that will not answer.

So this asks the four questions in order, fills in every answer it can read from a server, and
refuses early with the fix rather than late with a traceback.

Nothing here is required. The same run starts with:

    uv run python -m examples.neuro_san_play --session <id> --token <token>
"""

from __future__ import annotations

import httpx
import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

from runex import sessions
from runex.kinds import KINDS, ExperimentKind, by_key
from runex.launcher import Launcher, reachable

console = Console()

app = typer.Typer(
    name="runex",
    help="Run an nttd experiment: choose an approach, choose a session, play it.",
    add_completion=False,
)

DEFAULT_API_URL = "http://127.0.0.1:8000"
DEFAULT_NS_HOST = "localhost"
DEFAULT_NS_PORT = 8080
FALLBACK_NETWORK = "ns_air_agent"


@app.command()
def main(
    api_url: str = typer.Option(DEFAULT_API_URL, "--api-url", envvar="NTTD_API_URL",
                                help="Where nttd is serving"),
    kind: str = typer.Option("", "--kind", help="Skip the first prompt: neuro-san, scripted"),
    session: str = typer.Option("", "--session", help="Skip the session prompt"),
    token: str = typer.Option("", "--token", envvar="NTTD_TOKEN", help="Skip the token prompt"),
    network: str = typer.Option("", "--network", help="Which neuro-san network to run"),
    ns_host: str = typer.Option(DEFAULT_NS_HOST, "--ns-host", envvar="NEURO_SAN_SERVER_HOST",
                                help="Where neuro-san is serving"),
    ns_port: int = typer.Option(DEFAULT_NS_PORT, "--ns-port", envvar="NEURO_SAN_SERVER_HTTP_PORT",
                                help="The neuro-san HTTP port"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask for confirmation"),
) -> None:
    """Choose an approach and a session, then play it."""
    _banner(api_url)

    chosen = _choose_kind(kind)
    picked = _choose_session(api_url, session)
    if chosen.key == "neuro-san":
        chosen = _with_network(chosen, network, ns_host, ns_port)
    secret = _ask_token(api_url, picked.session_id, token, yes)

    launcher = Launcher(api_url)
    _summarise(chosen, picked, launcher.command(chosen, picked.session_id))
    if not yes and not Confirm.ask("\n  Start the experiment", default=True):
        console.print("[dim]  Nothing started.[/]")
        raise typer.Exit(code=0)

    console.rule(f"[bold]{chosen.title} playing {picked.session_id}")
    raise typer.Exit(code=launcher.run(chosen, picked.session_id, secret))


def _banner(api_url: str) -> None:
    console.print()
    console.print(Panel(
        Text.from_markup(
            "[bold]nttd runex[/]\n"
            "[dim]An approach, a session, and the token that joins them.[/]\n"
            f"[dim]nttd at {api_url}[/]"
        ),
        border_style="cyan",
        padding=(0, 2),
    ))


def _choose_kind(preset: str) -> ExperimentKind:
    """Step one: what decides."""
    if preset:
        kind = by_key(preset)
        if kind is None:
            _die(f"No such approach: {preset}", "Try one of: " + ", ".join(k.key for k in KINDS))
        if not kind.ready:
            _die(f"{kind.title} cannot run: {kind.unavailable_because}", "")
        return kind

    console.print("\n[bold cyan]  1[/]  [bold]How will it play?[/]\n")
    table = Table(box=None, padding=(0, 2), show_edge=False)
    table.add_column(" ", justify="right", style="cyan")
    table.add_column("Approach", style="bold")
    table.add_column("What decides")

    runnable: list[ExperimentKind] = []
    for kind in KINDS:
        if kind.ready:
            runnable.append(kind)
            table.add_row(str(len(runnable)), kind.title, kind.blurb)
        else:
            table.add_row("", f"[dim]{kind.title}[/]", f"[dim]{kind.unavailable_because}[/]")
    console.print(table)

    if not runnable:
        _die("No approach can run in this environment.", "Try: uv sync --extra neuro-san")

    answer = Prompt.ask(
        "\n  Choose", choices=[str(n + 1) for n in range(len(runnable))], default="1",
        show_choices=False,
    )
    return runnable[int(answer) - 1]


def _choose_session(api_url: str, preset: str) -> sessions.Session:
    """Step two: which game."""
    try:
        every = sessions.fetch(api_url)
    except httpx.HTTPError as failure:
        _die(
            f"Could not reach nttd at {api_url}: {failure}",
            "Start it with:  uv run nttd server",
        )

    if preset:
        for found in every:
            if found.session_id == preset:
                return found
        _die(f"nttd does not know a session called {preset}", "")

    open_now = [found for found in every if found.attachable]
    if not open_now:
        _die(
            "No session is open to play.",
            "Stand one up with:\n"
            "    uv run nttd benchmark --config config/benchmark/t1_256_flat_1001_stepped.conf\n"
            "  or create and start it yourself:\n"
            "    uv run nttd session create --config config/benchmark/t1_256_flat_1001_stepped.conf\n"
            "    uv run nttd session start -s <session> --agent-companies 1",
        )

    console.print("\n[bold cyan]  2[/]  [bold]Which session?[/]\n")
    table = Table(box=None, padding=(0, 2), show_edge=False)
    table.add_column(" ", justify="right", style="cyan")
    table.add_column("Session")
    table.add_column("Scenario", style="bold")
    table.add_column("Mode")
    table.add_column("Days", justify="right")
    table.add_column("State")
    for number, found in enumerate(open_now, start=1):
        table.add_row(
            str(number),
            found.session_id,
            found.scenario,
            found.runtime_mode,
            str(found.game_days or "-"),
            _state(found),
        )
    console.print(table)

    answer = Prompt.ask(
        "\n  Choose", choices=[str(n + 1) for n in range(len(open_now))], default="1",
        show_choices=False,
    )
    picked = open_now[int(answer) - 1]
    if not picked.has_contestant:
        _die(
            f"{picked.session_id} was started with no contestant company, so nothing can play it.",
            "Start it with:  uv run nttd session start -s <session> --agent-companies 1",
        )
    return picked


def _state(found: sessions.Session) -> str:
    """The two things about a session that change what a run of it means."""
    live = "[green]running[/]" if found.running else f"[yellow]{found.status}[/]"
    return f"{live}  [magenta]scored[/]" if found.scored else f"{live}  [dim]practice[/]"


def _with_network(
    kind: ExperimentKind, preset: str, host: str, port: int
) -> ExperimentKind:
    """Which agent network, and a check that there is a server to run it on.

    Asked only when the server offers a choice. A prompt with one option is a keystroke that
    teaches nothing.
    """
    if not reachable(host, port):
        _die(
            f"No neuro-san server at {host}:{port}.",
            "Start it in another terminal with:  uv run ns run",
        )

    served = preset and [preset] or _networks(host, port)
    if len(served) == 1:
        console.print(f"\n[bold cyan]  •[/]  Network: [bold]{served[0]}[/]")
        chosen = served[0]
    else:
        console.print("\n[bold cyan]  •[/]  [bold]Which network?[/]\n")
        for number, name in enumerate(served, start=1):
            console.print(f"     [cyan]{number}[/]  {name}")
        answer = Prompt.ask(
            "\n  Choose", choices=[str(n + 1) for n in range(len(served))], default="1",
            show_choices=False,
        )
        chosen = served[int(answer) - 1]

    return ExperimentKind(
        key=kind.key, title=f"{kind.title} / {chosen}", blurb=kind.blurb, module=kind.module,
        requires=kind.requires, install_hint=kind.install_hint,
        extra_args=("--network", chosen, "--host", host, "--port", str(port)),
    )


def _networks(host: str, port: int) -> list[str]:
    """What the server says it serves, which beats reading the manifest off disk.

    The manifest is what the server was told to load; this is what it actually did.
    """
    try:
        reply = httpx.get(f"http://{host}:{port}/api/v1/list", timeout=10)
        reply.raise_for_status()
        names = [str(entry.get("agent_name")) for entry in reply.json().get("agents") or []]
    except (httpx.HTTPError, ValueError):
        return [FALLBACK_NETWORK]
    return sorted(name for name in names if name) or [FALLBACK_NETWORK]


def _ask_token(api_url: str, session_id: str, preset: str, unattended: bool) -> str:
    """Step three: which company.

    The server will say what token it issued, and that is offered as the default so the usual
    case is one keystroke. It is shown rather than used silently, because a contestant running
    several sessions needs to see which company they are about to be.
    """
    if preset:
        return preset

    console.print("\n[bold cyan]  3[/]  [bold]Participant token[/]")
    known = sessions.token_for(api_url, session_id)
    if known:
        console.print(f"     [dim]nttd issued[/] [green]{known}[/] [dim]for this session[/]")
        # --yes means do not ask anything that can be answered from a server. Prompting here
        # anyway would hang a scripted run on a question it had already declined to be asked.
        return known if unattended else Prompt.ask("\n  Token", default=known)

    if unattended:
        _die(
            f"No participant token for {session_id} and --yes forbids asking for one.",
            "Pass --token, or set NTTD_TOKEN, or start the session with --agent-companies 1.",
        )

    console.print(
        "     [dim]Copy it from the session you started, or from[/] "
        f"[cyan]nttd session attach {session_id}[/]"
    )
    answer = Prompt.ask("\n  Token")
    while not answer.strip():
        answer = Prompt.ask("  [yellow]A token is required[/]")
    return answer.strip()


def _summarise(kind: ExperimentKind, picked: sessions.Session, command: list[str]) -> None:
    """What is about to happen, in the words of what would have been typed by hand."""
    console.print()
    body = Text.from_markup(
        f"[bold]{kind.title}[/]  playing  [bold]{picked.scenario}[/]\n"
        f"[dim]session[/]  {picked.session_id}\n"
        f"[dim]mode[/]     {picked.runtime_mode}"
        + (f", {picked.game_days} game days" if picked.game_days else "")
        + ("\n[magenta]This run is scored.[/]" if picked.scored else "")
        + f"\n\n[dim]{' '.join(command)}[/]"
        + "\n[dim]NTTD_TOKEN is passed through the environment, not the command line.[/]"
    )
    console.print(Panel(body, border_style="green", padding=(0, 2), title="About to run"))


def _die(problem: str, fix: str) -> None:
    console.print(f"\n[red]  {problem}[/]")
    if fix:
        console.print(f"[dim]  {fix}[/]")
    console.print()
    raise typer.Exit(code=1)
