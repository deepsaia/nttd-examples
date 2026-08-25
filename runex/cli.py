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

from runex import agent_server, networks, sessions
from runex.agent_server import AgentServer
from runex.kinds import KINDS, ExperimentKind, by_key
from runex.launcher import Launcher

console = Console()

app = typer.Typer(
    name="runex",
    help="Run an nttd experiment: choose an approach, choose a session, play it.",
    add_completion=False,
)

DEFAULT_API_URL = "http://127.0.0.1:8000"
DEFAULT_NS_HOST = agent_server.DEFAULT_HOST
DEFAULT_NS_PORT = agent_server.DEFAULT_PORT


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

    # The agent server comes BEFORE the session, and not only for tidiness. Standing one up
    # can mean starting a process and waiting a minute for it to load every network, and a
    # contestant who has already picked a live scored session should not then be asked to
    # wait. Whatever can fail should fail before anything is attached to a running game.
    server: AgentServer | None = None
    try:
        if chosen.agent_server:
            server = _agent_server(ns_host, ns_port, yes)
            chosen = _with_network(chosen, network, server, yes)

        picked = _choose_session(api_url, session)
        secret = _ask_token(api_url, picked.session_id, token, yes)

        launcher = Launcher(api_url)
        _summarise(chosen, picked, launcher.command(chosen, picked.session_id))
        if not yes and not Confirm.ask("\n  Start the experiment", default=True):
            console.print("[dim]  Nothing started.[/]")
            raise typer.Exit(code=0)

        console.rule(f"[bold]{chosen.title} playing {picked.session_id}")
        raise typer.Exit(code=launcher.run(chosen, picked.session_id, secret))
    finally:
        # Whatever happened: the run ended, the contestant declined, something refused, or
        # they pressed Ctrl-C. A server this launcher started is its to clean up.
        _stop_server(server)


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

    console.print("\n[bold cyan]  3[/]  [bold]Which session?[/]\n")
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


def _agent_server(host: str, port: int, unattended: bool) -> AgentServer:
    """Find a neuro-san server, or start one.

    This used to refuse: "No neuro-san server at localhost:8080, start it in another
    terminal". True, and a poor answer from a launcher whose whole job is to spare a
    contestant that terminal.
    """
    console.print("\n[bold cyan]  2[/]  [bold]Agent server[/]")

    if not unattended:
        answer = Prompt.ask("\n  Where is it, or where should it run", default=f"{host}:{port}")
        host, port = _split_address(answer, host, port)

    # Asked before deciding, because a busy port is two situations wanting opposite answers.
    # A neuro-san server already there should be USED; anything else there means ours needs
    # somewhere to go. Only a server answers with its networks.
    served = agent_server.looks_like_neuro_san(host, port)
    if served:
        console.print(
            f"     [green]already running[/] at {host}:{port}, "
            f"serving {len(served)} network(s). [dim]Left running afterwards.[/]"
        )
        return AgentServer(host, port)

    if agent_server.port_is_taken(host, port):
        console.print(
            f"     [yellow]{host}:{port} is busy, and what is there is not a neuro-san "
            "server.[/]"
        )
        free = agent_server.next_free_port(host, port + 1)
        if free is None:
            _die(
                f"Nothing is free between {port + 1} and "
                f"{port + agent_server.PORT_SEARCH_LIMIT}.",
                "Give a port yourself with --ns-port.",
            )
        if not unattended and not Confirm.ask(f"  Start one on {free} instead", default=True):
            _die("No agent server, so there is nothing to play with.", "")
        # Said out loud, and in the unattended path too. A launcher that quietly moved the
        # server to a port nobody named would leave a contestant looking for it on the one
        # they typed, and NSFlow, a browser tab and any second runex all pointed at the
        # wrong place.
        console.print(f"     [yellow]moving to {host}:{free}[/], the next port that is free")
        port = free

    return _start_server(host, port)


def _start_server(host: str, port: int) -> AgentServer:
    """Start one and wait for it to serve, saying what went wrong when it does not."""
    root = agent_server.project_root()
    if not agent_server.looks_like_a_project(root):
        # Reached only when neither the walk up from here nor the package's own location
        # finds a checkout, which means runex is installed and being run from somewhere
        # unrelated. Being in a SUBDIRECTORY is handled: project_root walks up, which is
        # what a shell with autocd needs, since a bare `runex` there is a cd into runex/.
        _die(
            f"No nttd-workbench checkout at or above {root}, so there is nothing to serve.",
            "The server reads its registries and .env as relative paths, so it needs the "
            "repository. cd into a checkout, or point at a server you started yourself.",
        )

    console.print(f"     starting a neuro-san server on {host}:{port}[dim] ...[/]")
    try:
        process = agent_server.start(host, port)
    except FileNotFoundError:
        _die(
            "`ns` is not installed, so no server can be started.",
            "uv sync --extra neuro-san",
        )

    with console.status("     loading agent networks", spinner="dots"):
        served = agent_server.wait_until_serving(host, port, process)

    if not served:
        AgentServer(host, port, process).stop()
        _die(
            f"The neuro-san server did not come up on {host}:{port}.",
            "What it said is in logs/runex-neuro-san.log. A missing ANTHROPIC_API_KEY in "
            ".env is the usual reason.",
        )

    console.print(f"     [green]serving {len(served)} network(s)[/]")
    return AgentServer(host, port, process)


def _split_address(answer: str, host: str, port: int) -> tuple[str, int]:
    """"localhost:8088", "8088" or "localhost", whichever a contestant typed."""
    text = answer.strip()
    if not text:
        return host, port
    if ":" in text:
        left, _, right = text.rpartition(":")
        return (left or host), _as_port(right, port)
    if text.isdigit():
        return host, _as_port(text, port)
    return text, port


def _as_port(text: str, fallback: int) -> int:
    try:
        value = int(text)
    except ValueError:
        console.print(f"     [yellow]{text!r} is not a port, using {fallback}.[/]")
        return fallback
    if not 1 <= value <= 65535:
        console.print(f"     [yellow]{value} is not a port, using {fallback}.[/]")
        return fallback
    return value


def _stop_server(server: AgentServer | None) -> None:
    """Stop a server this launcher started. One it merely found is left alone."""
    if server is None or not server.ours:
        return
    console.print(f"\n[dim]  Stopping the neuro-san server on {server.host}:{server.port}[/]")
    server.stop()


def _with_network(
    kind: ExperimentKind, preset: str, server: AgentServer, unattended: bool
) -> ExperimentKind:
    """Which agent network, from the ones the server says it is serving."""
    try:
        served = networks.fetch(server.host, server.port)
    except (httpx.HTTPError, ValueError) as failure:
        _die(
            f"{server.host}:{server.port} would not list its networks: {failure}",
            "If that is not a neuro-san server, point at the right one with --ns-port.",
        )

    if not served:
        _die(
            f"The neuro-san server at {server.host}:{server.port} serves no networks.",
            "Check AGENT_MANIFEST_FILE in .env, and that the manifest enables one.",
        )

    chosen = _pick_network(served, preset, unattended)
    return ExperimentKind(
        key=kind.key, title=f"{kind.title} / {chosen.name}", blurb=kind.blurb,
        module=kind.module, requires=kind.requires, install_hint=kind.install_hint,
        agent_server=kind.agent_server,
        extra_args=("--network", chosen.name,
                    "--host", server.host, "--port", str(server.port)),
    )


def _pick_network(
    served: list[networks.Network], preset: str, unattended: bool
) -> networks.Network:
    """One of the networks the server said it serves, and never one it did not.

    A name is only accepted if the server offered it. Passing --network for a network that is
    not being served used to be taken on trust, and the run then failed several turns in with
    an error about an unknown agent, which reads as a server fault.
    """
    # A precondition rather than a duplicate of the caller's check. Without it an empty list
    # renders an empty menu and prompts for a choice among no options, which is a hang.
    if not served:
        _die("The neuro-san server serves no networks.", "")

    if preset:
        for network in served:
            if network.name == preset:
                return network
        _die(
            f"The server does not serve a network called {preset}.",
            "It serves: " + ", ".join(network.name for network in served),
        )

    if len(served) == 1:
        # Not a prompt. A menu of one is a keystroke that teaches nothing, and saying it is the
        # only one served is the information the prompt would have carried.
        console.print(
            f"\n[bold cyan]  \u2022[/]  Network: [bold]{served[0].name}[/] "
            "[dim](the only one this server serves)[/]"
        )
        return served[0]

    if unattended:
        _die(
            f"The server serves {len(served)} networks and --yes forbids asking which.",
            "Pass --network with one of: " + ", ".join(n.name for n in served),
        )

    console.print("\n[bold cyan]  \u2022[/]  [bold]Which network?[/]\n")
    table = Table(box=None, padding=(0, 2), show_edge=False)
    table.add_column(" ", justify="right", style="cyan")
    table.add_column("Network", style="bold")
    table.add_column("What it plays")
    for number, network in enumerate(served, start=1):
        table.add_row(str(number), network.name, network.summary)
    console.print(table)

    answer = Prompt.ask(
        "\n  Choose", choices=[str(n + 1) for n in range(len(served))], default="1",
        show_choices=False,
    )
    return served[int(answer) - 1]


def _ask_token(api_url: str, session_id: str, preset: str, unattended: bool) -> str:
    """Step three: which company.

    The server will say what token it issued, and that is offered as the default so the usual
    case is one keystroke. It is shown rather than used silently, because a contestant running
    several sessions needs to see which company they are about to be.
    """
    if preset:
        return preset

    console.print("\n[bold cyan]  4[/]  [bold]Participant token[/]")
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
