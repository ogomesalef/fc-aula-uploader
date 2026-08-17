"""Interface interativa (TUI leve) para revisar e confirmar o plano."""

from __future__ import annotations

import re
from pathlib import Path

import questionary
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from aula_uploader.batch import BatchChapterDraft
from aula_uploader.catalog import CatalogStore
from aula_uploader.media import format_bytes, format_duration
from aula_uploader.naming import AulaArquivo
from aula_uploader.ollama_client import detect_ollama, suggest_titles
from aula_uploader.plan import (
    Acao,
    PlanoItem,
    parse_bunny_folder_id,
    parse_curso_id,
    titulos_duplicados,
)
from aula_uploader.portal_client import CapituloInfo, CapituloResumo, CursoInfo, PortalClient
from aula_uploader.session import PORTAL_LABELS
from aula_uploader.state import UploadState

console = Console()
TOTAL_STEPS = 6

_ACAO_STYLE = {
    Acao.CRIAR: "bold green",
    Acao.ENVIAR: "bold cyan",
    Acao.PULAR: "bold magenta",
    Acao.FORCAR: "bold yellow",
}
_ACAO_HINT = {
    Acao.CRIAR: "aula nova no capítulo",
    Acao.ENVIAR: "aula existe, ainda sem vídeo",
    Acao.PULAR: "conteúdo já existe com vídeo",
    Acao.FORCAR: "reenviar mesmo com vídeo",
}


def _gap() -> None:
    """Espaço vertical entre blocos da TUI."""
    console.print()


def _make_table(title: str = "", *, show_header: bool = True) -> Table:
    return Table(
        title=title or None,
        show_header=show_header,
        box=box.ROUNDED,
        padding=(0, 1),
        pad_edge=True,
        expand=False,
    )


def format_acao(acao: Acao) -> str:
    style = _ACAO_STYLE.get(acao, "white")
    return f"[{style}]{acao.value}[/{style}]"


def show_acao_legend(plano: list[PlanoItem] | None = None) -> None:
    """Legenda só com as ações presentes no plano (ou todas, se vazio)."""
    presentes = {item.acao for item in plano} if plano else set(_ACAO_HINT)
    if not presentes:
        return
    lines = []
    for acao in (Acao.CRIAR, Acao.ENVIAR, Acao.PULAR, Acao.FORCAR):
        if acao in presentes:
            lines.append(f"  {format_acao(acao)}  —  {_ACAO_HINT[acao]}")
    console.print(
        Panel(
            "\n".join(lines),
            title="Legenda",
            border_style="dim",
            padding=(0, 1),
        )
    )


def banner() -> None:
    console.print(
        Panel.fit(
            "[bold]aula-uploader[/bold]\n"
            "Cria capítulos e envia aulas (vídeos) no portal administrativo.\n"
            "[dim]A pasta Bunny precisa existir antes.[/dim]",
            border_style="cyan",
        )
    )


def show_step(number: int, title: str, description: str = "") -> None:
    """Limpa a tela e mostra uma única etapa por vez."""
    console.clear()
    progress = "  ".join(
        "[cyan]●[/cyan]" if idx <= number else "[dim]○[/dim]"
        for idx in range(1, TOTAL_STEPS + 1)
    )
    body = f"[bold]{title}[/bold]"
    if description:
        body += f"\n[dim]{description}[/dim]"
    console.print(
        Panel(
            body,
            title=f"aula-uploader · Etapa {number}/{TOTAL_STEPS}",
            subtitle=progress,
            border_style="cyan",
            padding=(1, 2),
        )
    )
    _gap()


def ask_portal() -> str:
    from aula_uploader.session import DEFAULT_URLS, PORTAL_LABELS

    escolha = questionary.select(
        "Portal de destino:",
        choices=[
            questionary.Choice(
                f"{PORTAL_LABELS[key]} — {DEFAULT_URLS[key]}",
                value=key,
            )
            for key in PORTAL_LABELS
        ],
    ).ask()
    if not escolha:
        raise SystemExit(1)
    return escolha


def ask_login(portal_key: str) -> PortalClient:
    """Login no início do fluxo, pedindo credenciais de forma explícita."""
    from aula_uploader.session import (
        clear_session,
        enable_session_persistence,
        ensure_authenticated,
        has_saved_session,
    )

    console.print(
        "\n[dim]Use o mesmo e-mail e senha do login administrativo do portal.[/dim]"
    )
    console.print(
        "[dim]Mais seguro: digitar agora e não salvar sessão.[/dim]"
    )

    use_saved = False
    if has_saved_session(portal_key):
        escolha = questionary.select(
            "Há uma sessão salva neste computador. O que deseja fazer?",
            choices=[
                questionary.Choice(
                    "Reutilizar sessão salva (mais conveniente)",
                    value="reuse",
                ),
                questionary.Choice(
                    "Fazer login de novo sem salvar (mais seguro)",
                    value="fresh",
                ),
                questionary.Choice(
                    "Apagar sessão salva e fazer login de novo",
                    value="clear",
                ),
            ],
        ).ask()
        if not escolha:
            raise SystemExit(1)
        if escolha == "clear":
            clear_session(portal_key)
            console.print("[green]Sessão anterior removida.[/green]")
        elif escolha == "reuse":
            use_saved = True

    username = ""
    password = ""

    while True:
        if not use_saved:
            username, password = _ask_credentials(portal_key)
        try:
            portal = ensure_authenticated(
                portal_key,
                username=username,
                password=password,
                log=lambda m: console.print(f"  {m}"),
                force=not use_saved,
                persist_session=False,
                use_saved_session=use_saved,
            )
            break
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]Login falhou: {exc}[/red]")
            if use_saved:
                console.print(
                    "[dim]A sessão salva pode ter expirado. Vamos pedir usuário e senha.[/dim]"
                )
                use_saved = False
                clear_session(portal_key)
                continue
            console.print("[dim]Digite de novo o usuário e a senha do portal.[/dim]")
            if not ask_yes_no("Tentar login novamente?", default=True):
                raise SystemExit(1) from exc

    if use_saved:
        console.print("[green]✓ Autenticado com sessão salva.[/green]")
        return portal

    salvar = ask_yes_no(
        "Salvar sessão neste computador para as próximas execuções?\n"
        "  (Não = mais seguro · Sim = não pedir senha de novo)",
        default=False,
    )
    if salvar:
        path = enable_session_persistence(portal, portal_key)
        console.print(f"[green]✓ Sessão salva em {path}[/green]")
        console.print("[dim]Remova depois com: aula-uploader logout[/dim]")
    else:
        console.print("[green]✓ Login OK — sessão só nesta execução.[/green]")
    return portal


def _ask_credentials(portal_key: str) -> tuple[str, str]:
    """Pede usuário/senha no terminal. .env só vira sugestão, nunca login silencioso."""
    import getpass

    from aula_uploader.session import get_credentials

    _base, env_user, env_pass = get_credentials(portal_key)

    if env_user and env_pass:
        escolha = questionary.select(
            "Como deseja autenticar?",
            choices=[
                questionary.Choice(
                    "Digitar usuário e senha agora (recomendado)",
                    value="prompt",
                ),
                questionary.Choice(
                    f"Usar o .env local ({env_user})",
                    value="env",
                ),
            ],
        ).ask()
        if not escolha:
            raise SystemExit(1)
        if escolha == "env":
            console.print("[dim]Usando credenciais do .env neste computador.[/dim]")
            return env_user, env_pass

    default_user = env_user or ""
    user = questionary.text(
        "E-mail do portal:",
        default=default_user,
        validate=lambda text: bool(text.strip()),
    ).ask()
    if user is None:
        raise SystemExit(1)
    password = getpass.getpass("Senha (não será exibida): ")
    if not password:
        raise RuntimeError("Senha é obrigatória.")
    return user.strip(), password


def ask_target_mode() -> str:
    """Primeira opção é criar capítulo com pasta Bunny já preparada."""
    escolha = questionary.select(
        "Como deseja enviar as aulas?",
        choices=[
            questionary.Choice(
                "Criar um novo capítulo e depois subir as aulas",
                value="create",
            ),
            questionary.Choice(
                "Usar um capítulo que já existe",
                value="existing",
            ),
            questionary.Choice(
                "Usar um capítulo já mapeado neste computador",
                value="mapped",
            ),
            questionary.Choice(
                "Criar vários capítulos de uma vez (lote)",
                value="batch",
            ),
        ],
    ).ask()
    if not escolha:
        raise SystemExit(1)
    return escolha


def capitulo_admin_url(base_url: str, capitulo_id: int) -> str:
    return f"{base_url.rstrip('/')}/admin/curso/conteudo/{capitulo_id}/capitulo"


def curso_admin_url(base_url: str, curso_id: int) -> str:
    return f"{base_url.rstrip('/')}/admin/curso/capitulo/{curso_id}/curso"


def ask_after_upload(
    *,
    portal_url: str,
    capitulo: CapituloResumo | None = None,
    curso_nome: str = "",
    curso_id: int | None = None,
    has_failures: bool = False,
    allow_same_chapter: bool = True,
) -> str:
    """Depois do upload: link do portal + próxima ação (não encerra sozinho)."""
    _gap()
    if capitulo is not None:
        curso = capitulo.curso_nome or (
            f"ID {capitulo.curso_id}" if capitulo.curso_id else "—"
        )
        detalhe = (
            f"Curso: {curso}\n"
            f"Capítulo: {capitulo.nome} (ID {capitulo.id})"
        )
    else:
        curso = curso_nome or (f"ID {curso_id}" if curso_id else "—")
        detalhe = f"Curso: {curso}"
        if curso_id:
            detalhe += f" (ID {curso_id})"

    console.print(
        Panel(
            f"[bold]Conferir no portal[/bold]\n"
            f"[cyan]{portal_url}[/cyan]\n\n"
            f"{detalhe}",
            title="Próximo passo",
            border_style="cyan",
            padding=(1, 2),
        )
    )
    _gap()
    if has_failures:
        console.print(
            "[yellow]Houve falhas neste envio. Você pode tentar de novo "
            "no mesmo capítulo ou escolher outro destino.[/yellow]"
        )
        _gap()

    choices = []
    if allow_same_chapter and capitulo is not None:
        choices.append(
            questionary.Choice(
                "Enviar mais vídeos neste mesmo capítulo",
                value="same_chapter",
            )
        )
    choices.extend(
        [
            questionary.Choice(
                "Outro capítulo já mapeado (lista local)",
                value="mapped",
            ),
            questionary.Choice(
                "Outro capítulo (colar link do portal)",
                value="existing",
            ),
            questionary.Choice(
                "Criar um capítulo novo e subir aulas",
                value="create",
            ),
            questionary.Choice(
                "Criar vários capítulos de uma vez (lote)",
                value="batch",
            ),
            questionary.Choice(
                "Finalizar / encerrar",
                value="exit",
            ),
        ]
    )
    escolha = questionary.select("O que deseja fazer agora?", choices=choices).ask()
    if not escolha:
        return "exit"
    return escolha


def ask_mapped_chapter(
    catalog: CatalogStore,
    portal,
) -> tuple[int, int] | None:
    """Navega por curso → capítulo; ao escolher o curso, sincroniza com o portal."""
    courses = catalog.courses()
    if not courses:
        console.print(
            "[yellow]Ainda não há cursos mapeados neste computador.[/yellow]\n"
            "[dim]Informe um curso ou capítulo uma vez; ele será mapeado em segundo plano.[/dim]"
        )
        return None

    course_id = questionary.select(
        "Curso já mapeado:",
        choices=[
            questionary.Choice(
                f"{course.nome} (ID {course.id}) · {len(course.chapters)} capítulo(s)",
                value=course.id,
            )
            for course in courses
        ],
    ).ask()
    if course_id is None:
        raise SystemExit(1)

    course_id = int(course_id)
    cached = catalog.get_course(course_id)
    console.print(
        f"\n[cyan]Atualizando[/cyan] capítulos de "
        f"[bold]{cached.nome if cached else course_id}[/bold] no portal…"
    )
    try:
        course = catalog.sync_course(portal, course_id)
        console.print(
            f"[green]✓[/green] Catálogo atualizado · "
            f"{len(course.chapters)} capítulo(s)"
        )
    except Exception as exc:  # noqa: BLE001
        console.print(
            f"[yellow]Não foi possível atualizar agora:[/yellow] {exc}\n"
            "[dim]Usando a lista salva neste computador (pode estar desatualizada).[/dim]"
        )
        course = catalog.get_course(course_id)

    if course is None or not course.chapters:
        console.print("[yellow]Este curso ainda não possui capítulos no catálogo.[/yellow]")
        return None

    chapter_id = questionary.select(
        f"Capítulo de {course.nome}:",
        choices=[
            questionary.Choice(
                f"{chapter.ordem if chapter.ordem else '—'} · {chapter.nome} (ID {chapter.id})",
                value=chapter.id,
            )
            for chapter in course.chapters
        ],
    ).ask()
    if chapter_id is None:
        raise SystemExit(1)
    return course_id, int(chapter_id)


def ask_curso_id() -> int:
    console.print(
        "[dim]Cole o link do curso no admin ou somente o ID dele.[/dim]"
    )
    while True:
        valor = questionary.text(
            "Link/ID do curso:\n"
            "  ex.: .../admin/curso/capitulo/291/curso"
        ).ask()
        if valor is None:
            raise SystemExit(1)
        try:
            return parse_curso_id(valor)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")


def show_curso(curso: CursoInfo, *, last_order: int | None = None) -> None:
    table = _make_table("Curso confirmado", show_header=False)
    table.add_column("Campo", style="bold", no_wrap=True)
    table.add_column("Valor")
    table.add_row("Curso", curso.nome)
    table.add_row("ID", str(curso.id))
    if last_order is None:
        table.add_row("Capítulos existentes", "Nenhum")
        table.add_row("Ordem sugerida", "1")
    else:
        table.add_row("Última ordem existente", str(last_order))
        table.add_row("Ordem sugerida para o novo capítulo", str(last_order + 1))
    console.print(table)
    _gap()


def ask_new_chapter_details(suggested_order: int) -> tuple[str, str, int]:
    """Retorna nome, ID Bunny extraído da URL e ordem do capítulo."""
    while True:
        nome = questionary.text(
            "Nome do novo capítulo:",
            validate=lambda text: bool(text.strip()),
        ).ask()
        if nome is None:
            raise SystemExit(1)
        bunny_url = questionary.text(
            "URL ou ID da pasta Bunny:",
            instruction="A pasta deve já existir no Bunny.",
        ).ask()
        if bunny_url is None:
            raise SystemExit(1)
        try:
            bunny_id = parse_bunny_folder_id(bunny_url)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            continue
        order = questionary.text(
            "Ordem do capítulo:",
            default=str(suggested_order),
            validate=lambda text: text.isdigit() and int(text) > 0,
        ).ask()
        if order is None:
            raise SystemExit(1)
        return nome.strip(), bunny_id, int(order)


def ask_capitulo_id() -> int:
    """Pede o capítulo em loop até receber um ID/URL válidos."""
    from aula_uploader.plan import parse_capitulo_id

    console.print(
        "[dim]Use a URL da lista de aulas do capítulo "
        "(.../admin/curso/conteudo/<ID>/capitulo), não a URL do curso.[/dim]"
    )
    while True:
        valor = questionary.text(
            "Cole o link do capítulo (ou o ID):\n"
            "  ex.: .../admin/curso/conteudo/299/capitulo"
        ).ask()
        if valor is None:
            raise SystemExit(1)
        valor = valor.strip().strip("'\"")
        if not valor:
            console.print("[yellow]Informe o link ou o ID do capítulo.[/yellow]")
            continue
        try:
            capitulo_id = parse_capitulo_id(valor)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            console.print("[dim]Tente de novo.[/dim]")
            continue
        console.print(f"  → Capítulo {capitulo_id}")
        return capitulo_id


def ask_source_path() -> Path:
    """Pede pasta/.zip em loop até existir um caminho válido."""
    from aula_uploader.media import is_zip, normalize_user_path

    console.print(
        "[dim]Dica: no terminal do macOS/Linux você pode arrastar a pasta "
        "ou o .zip para esta janela.[/dim]"
    )
    while True:
        valor = questionary.path(
            "Pasta com os vídeos ou arquivo .zip:"
        ).ask()
        if valor is None:
            raise SystemExit(1)
        path = normalize_user_path(valor)
        if path.is_dir() or is_zip(path):
            console.print(f"  → {path}")
            return path
        console.print(f"[red]Pasta ou .zip não encontrado: {path}[/red]")
        console.print(
            "[dim]Caminhos com espaço ficam assim: "
            "/Users/.../Meu Curso/teste (sem barra antes do espaço).[/dim]"
        )
        console.print("[dim]Tente de novo.[/dim]")


def ask_yes_no(prompt: str, *, default: bool = True) -> bool:
    return bool(
        questionary.confirm(prompt, default=default).ask()
    )


def show_environment(*, ollama_info=None) -> None:
    from aula_uploader.media import ffprobe_available

    table = _make_table("Ambiente", show_header=False)
    table.add_column("Item", style="bold", no_wrap=True)
    table.add_column("Status")
    table.add_row("ffprobe (duração)", "ok" if ffprobe_available() else "ausente (opcional)")
    if ollama_info is None:
        ollama_info = detect_ollama()
    if ollama_info.reachable:
        models = ", ".join(ollama_info.models[:5]) or "(nenhum)"
        rec = ollama_info.recommended or "—"
        table.add_row("Ollama", f"ok — modelos: {models}")
        table.add_row("Modelo sugerido", rec)
    elif ollama_info.installed:
        table.add_row("Ollama", "instalado, mas não responde em 127.0.0.1:11434")
    else:
        table.add_row("Ollama", "não instalado (opcional)")
    console.print(table)
    _gap()


def show_destino(
    *,
    portal_key: str,
    capitulo: CapituloResumo,
    pasta: Path,
    total: int,
) -> None:
    table = _make_table("Destino no portal", show_header=False)
    table.add_column("Campo", style="bold", no_wrap=True)
    table.add_column("Valor")
    table.add_row("Portal", PORTAL_LABELS.get(portal_key, portal_key))

    if capitulo.curso_nome and capitulo.curso_id:
        curso_txt = f"{capitulo.curso_nome} (ID {capitulo.curso_id})"
    elif capitulo.curso_nome:
        curso_txt = capitulo.curso_nome
    elif capitulo.curso_id:
        curso_txt = f"ID {capitulo.curso_id}"
    else:
        curso_txt = "—"
    table.add_row("Curso", curso_txt)
    table.add_row("Capítulo", f"{capitulo.nome} (ID {capitulo.id})")
    if str(pasta) not in {"", "—"}:
        table.add_row("Pasta/ZIP", str(pasta))
    if total:
        table.add_row("Aulas", str(total))
    console.print(table)
    _gap()


def show_plan_table(plano: list[PlanoItem]) -> None:
    table = _make_table("Plano de upload")
    table.add_column("Ordem", justify="right", no_wrap=True)
    table.add_column("Título", overflow="fold")
    table.add_column("Arquivo", overflow="ellipsis", max_width=42)
    table.add_column("Tamanho", justify="right", no_wrap=True)
    table.add_column("Duração", justify="right", no_wrap=True)
    table.add_column("Ação", justify="center", no_wrap=True)
    for item in plano:
        aula = item.aula
        marca = "*" if aula.ordem_inferida else ""
        table.add_row(
            f"{aula.ordem}{marca}",
            aula.titulo,
            aula.path.name,
            format_bytes(aula.tamanho_bytes),
            format_duration(aula.duracao_segundos),
            format_acao(item.acao),
        )
    console.print(table)
    _gap()
    show_acao_legend(plano)
    if any(item.aula.ordem_inferida for item in plano):
        console.print("[dim]* ordem inferida (sem número claro no nome)[/dim]")
    show_duplicate_warning([item.aula for item in plano])
    _gap()


def show_duplicate_warning(aulas: list[AulaArquivo]) -> dict[str, list[str]]:
    """Avisa sobre títulos repetidos e devolve o que encontrou."""
    duplicados = titulos_duplicados(aulas)
    if not duplicados:
        return {}
    linhas = "\n".join(
        f"  [bold]{titulo}[/bold] — {', '.join(arquivos)}"
        for titulo, arquivos in duplicados.items()
    )
    console.print(
        Panel(
            "Estes títulos se repetem no lote. O portal trataria como a mesma "
            "aula, e um vídeo sobrescreveria o outro.\n\n"
            f"{linhas}\n\n"
            "[dim]Volte e renomeie antes de enviar.[/dim]",
            title="Títulos duplicados",
            border_style="red",
            padding=(1, 2),
        )
    )
    return duplicados


def review_aulas(aulas: list[AulaArquivo]) -> list[AulaArquivo]:
    """Escolhe normalização e sempre deixa revisar/editar os nomes."""
    ollama = detect_ollama()
    show_step(
        4,
        "Revisar nomes das aulas",
        "Escolha como preparar os títulos e confirme se ficou certo.",
    )
    show_environment(ollama_info=ollama)

    modo = questionary.select(
        "Como deseja preparar os títulos?",
        choices=[
            questionary.Choice(
                "Normalização local — rápida, sem IA",
                value="auto",
            ),
            questionary.Choice(
                (
                    f"Usar Ollama — {ollama.recommended} (recomendado para IA)"
                    if ollama.recommended
                    else "Usar Ollama para sugerir nomes"
                ),
                value="ollama",
                disabled=None if ollama.reachable and ollama.models else "Ollama indisponível",
            ),
            questionary.Choice(
                "Editar manualmente — revisar uma aula por vez",
                value="manual",
            ),
        ],
    ).ask()
    if not modo:
        raise SystemExit(1)

    if modo == "ollama":
        model = ollama.recommended
        assert model
        console.print(
            f"\n[cyan]IA local:[/cyan] usando [bold]{model}[/bold]. "
            "Ele é o melhor modelo de texto disponível para esta tarefa."
        )
        console.print("[dim]Somente os nomes dos arquivos são enviados ao Ollama local.[/dim]")
        console.print("Consultando IA...")
        try:
            suggestions = suggest_titles(
                [a.path.name for a in aulas], model=model, host=ollama.host
            )
            apply_suggestions(aulas, suggestions)
            console.print("[green]✓ Sugestões aplicadas. Revise antes de continuar.[/green]")
        except Exception as exc:  # noqa: BLE001
            console.print(f"[yellow]IA local não respondeu corretamente: {exc}[/yellow]")
            console.print(
                "[dim]A normalização local foi mantida. Você pode editar cada título abaixo.[/dim]"
            )
    elif modo == "auto":
        console.print(
            "\n[green]✓[/green] Normalização local aplicada. "
            "Confira os nomes abaixo — dá para editar se algo ficou estranho."
        )

    # Sempre revisa: local/IA podem errar e o usuário precisa poder corrigir.
    while True:
        show_step(
            4,
            "Confirmar nomes das aulas",
            "Setas para escolher · Enter para editar · continue quando estiver certo.",
        )
        _print_aulas(aulas)
        escolha = questionary.select(
            "Selecione uma aula para editar ou continue:",
            choices=[
                *[
                    questionary.Choice(
                        f"Editar #{a.ordem}: {a.titulo}  ←  {a.path.name}",
                        value=f"edit:{idx}",
                    )
                    for idx, a in enumerate(aulas)
                ],
                questionary.Choice("Continuar — nomes estão corretos", value="ok"),
                questionary.Choice("Cancelar", value="cancel"),
            ],
        ).ask()
        if not escolha or escolha == "cancel":
            raise SystemExit(1)
        if escolha == "ok":
            return aulas
        idx = int(escolha.split(":")[1])
        aulas[idx] = _edit_aula(aulas[idx])


def _print_aulas(aulas: list[AulaArquivo]) -> None:
    table = _make_table("Aulas (pré-visualização)")
    table.add_column("Ordem", justify="right", no_wrap=True)
    table.add_column("Título", overflow="fold")
    table.add_column("Arquivo", overflow="ellipsis", max_width=42)
    table.add_column("Tamanho", justify="right", no_wrap=True)
    for a in aulas:
        table.add_row(
            f"{a.ordem}{'*' if a.ordem_inferida else ''}",
            a.titulo,
            a.path.name,
            format_bytes(a.tamanho_bytes),
        )
    console.print(table)
    _gap()


def _edit_aula(aula: AulaArquivo) -> AulaArquivo:
    nova_ordem = questionary.text(
        f"Ordem para {aula.path.name}:",
        default=str(aula.ordem),
        validate=lambda t: t.isdigit() and int(t) > 0,
    ).ask()
    novo_titulo = questionary.text(
        "Título da aula:",
        default=aula.titulo,
        validate=lambda t: bool(t.strip()),
    ).ask()
    if not nova_ordem or not novo_titulo:
        return aula
    aula.ordem = int(nova_ordem)
    aula.titulo = novo_titulo.strip()
    aula.ordem_inferida = False
    return aula


def apply_suggestions(
    aulas: list[AulaArquivo],
    suggestions: list[dict[str, str | int]],
) -> None:
    """Aplica títulos da IA sem deixar números voltarem ao título/ordem."""
    by_name = {str(s.get("arquivo")): s for s in suggestions}
    for aula in aulas:
        sug = by_name.get(aula.path.name)
        if not sug:
            for key, value in by_name.items():
                if key and key in aula.path.name:
                    sug = value
                    break
        if not sug or not sug.get("titulo"):
            continue

        titulo = str(sug["titulo"]).strip()
        titulo = re.sub(
            r"^\s*\d+(?:\.\d+)*\s*(?:[-–—_:]\s*)?",
            "",
            titulo,
        ).strip()
        if titulo:
            aula.titulo = titulo

        # A regra local é mais confiável para 9.1 → ordem 1.
        # A IA só decide a ordem quando o arquivo não tem número.
        suggested_order = int(sug.get("ordem") or 0)
        if aula.ordem_inferida and suggested_order > 0:
            aula.ordem = suggested_order
            aula.ordem_inferida = False


def ask_publish_status(*, force_publicar: bool = False) -> str:
    """Pergunta se cria como publicado ou rascunho.

    Returns:
        ``"1"`` publicado ou ``"0"`` rascunho.
    """
    if force_publicar:
        return "1"

    _gap()
    escolha = questionary.select(
        "Como criar as aulas novas?",
        choices=[
            questionary.Choice("1) Publicar agora", value="1"),
            questionary.Choice("2) Criar como rascunho", value="0"),
        ],
    ).ask()
    if not escolha:
        raise SystemExit(1)
    return escolha


def confirm_upload(plano: list[PlanoItem], *, publicar: bool) -> bool:
    duplicados = titulos_duplicados([item.aula for item in plano])
    criar = sum(1 for p in plano if p.acao == Acao.CRIAR)
    enviar = sum(1 for p in plano if p.acao in {Acao.ENVIAR, Acao.FORCAR})
    pular = sum(1 for p in plano if p.acao == Acao.PULAR)
    status = "Publicado" if publicar else "Rascunho"
    _gap()
    console.print(
        Panel(
            f"[bold green]{criar}[/bold green] criar  ·  "
            f"[bold cyan]{enviar}[/bold cyan] enviar  ·  "
            f"[bold magenta]{pular}[/bold magenta] pular\n\n"
            f"Status das aulas novas: [bold]{status}[/bold]",
            title="Resumo",
            border_style="cyan",
            padding=(1, 2),
        )
    )
    _gap()
    if duplicados:
        # Confirmar por engano aqui sobrescreveria vídeos; o default vira Não.
        console.print(
            f"[red]{len(duplicados)} título(s) duplicado(s) no lote.[/red]"
        )
        return ask_yes_no(
            "Enviar mesmo assim? (recomendado: N, para renomear antes)",
            default=False,
        )
    return ask_yes_no(
        "Confirma o upload no portal? (N = cancelar)",
        default=True,
    )


def run_upload_screen(
    portal: PortalClient,
    *,
    capitulo_id: int,
    plano: list[PlanoItem],
    state: UploadState,
    status_criacao: str,
    portal_key: str = "",
    capitulo: CapituloResumo | None = None,
) -> tuple[int, int, list[tuple[str, str]]]:
    """Etapa 6: executa o upload com progresso visual."""
    from aula_uploader.runner import executar_plano

    show_step(
        6,
        "Enviar aulas",
        "Criando e enviando os vídeos. Não feche o terminal até terminar.",
    )
    portal_url = capitulo_admin_url(portal.base_url, capitulo_id)
    if capitulo is not None:
        destino = f"{capitulo.nome} (ID {capitulo.id})"
        if capitulo.curso_nome:
            destino = f"{capitulo.curso_nome} · {destino}"
        portal_label = PORTAL_LABELS.get(portal_key, portal_key) if portal_key else ""
        header = Table(show_header=False, box=None, padding=(0, 1))
        header.add_column(style="dim")
        header.add_column()
        if portal_label:
            header.add_row("Portal", portal_label)
        header.add_row("Destino", destino)
        header.add_row(
            "Status",
            "Publicado" if status_criacao == "1" else "Rascunho",
        )
        console.print(header)
        console.print()

    reporter = _UploadReporter(total=len(plano))
    with reporter.live:
        ok, pulados, falhas = executar_plano(
            portal,
            capitulo_id=capitulo_id,
            plano=plano,
            state=state,
            status_criacao=status_criacao,
            log=reporter,
        )
    reporter.print_final(
        ok=ok,
        pulados=pulados,
        falhas=falhas,
        state_path=state.path,
        portal_url=portal_url,
    )
    return ok, pulados, falhas


class _UploadReporter:
    """Renderiza progresso de upload sem dump de log."""

    def __init__(self, *, total: int) -> None:
        from rich.live import Live
        from rich.progress import (
            BarColumn,
            Progress,
            SpinnerColumn,
            TaskProgressColumn,
            TextColumn,
        )

        self.total = max(total, 1)
        self.current_index = 0
        self.current_title = ""
        self.current_action = ""
        self.status = "Preparando..."
        self.file_label = ""
        self.done_lines: list[str] = []
        self._progress = Progress(
            SpinnerColumn(style="cyan"),
            TextColumn("[bold]{task.description}[/bold]"),
            BarColumn(bar_width=28, complete_style="cyan", finished_style="green"),
            TaskProgressColumn(),
            console=console,
            expand=False,
        )
        self._task_overall = self._progress.add_task(
            "Geral", total=self.total, completed=0
        )
        self._task_file = self._progress.add_task(
            "Arquivo", total=100, completed=0, visible=False
        )
        self.live = Live(
            self._render(),
            console=console,
            refresh_per_second=8,
            transient=False,
        )

    def __call__(self, msg: str) -> None:
        text = (msg or "").strip()
        if not text:
            return

        item_match = re.match(
            r"^\[(\d+)/(\d+)\]\s+(.+?)\s+\(([^)]+)\)\s*$",
            text,
        )
        if item_match:
            self.current_index = int(item_match.group(1))
            self.current_title = item_match.group(3).strip()
            self.current_action = item_match.group(4).strip()
            self.status = f"{self.current_action}…"
            self.file_label = ""
            self._progress.update(
                self._task_overall,
                completed=self.current_index - 1,
                description=f"Aula {self.current_index}/{self.total}",
            )
            self._progress.update(
                self._task_file, completed=0, total=100, visible=False
            )
            self.live.update(self._render())
            return

        chunk_match = re.search(r"Chunk\s+(\d+)/(\d+)\s+\((\d+)%\)", text)
        if chunk_match:
            index = int(chunk_match.group(1))
            total_chunks = int(chunk_match.group(2))
            percent = int(chunk_match.group(3))
            self.status = f"Enviando chunk {index}/{total_chunks}"
            self._progress.update(
                self._task_file,
                completed=percent,
                total=100,
                visible=True,
                description="Upload",
            )
            self.live.update(self._render())
            return

        upload_match = re.match(
            r"^Upload:\s+(.+?)\s+\(([\d.]+)\s*MB,\s*(\d+)\s*chunks?\)\s*$",
            text,
        )
        if upload_match:
            name = upload_match.group(1)
            size_mb = upload_match.group(2)
            chunks = upload_match.group(3)
            self.file_label = f"{name} · {size_mb} MB · {chunks} chunks"
            self.status = "Iniciando upload…"
            self._progress.update(
                self._task_file, completed=0, total=100, visible=True, description="Upload"
            )
            self.live.update(self._render())
            return

        if text == "OK" or text.endswith(" OK"):
            self.done_lines.append(f"[green]✓[/green] {self.current_title}")
            self.status = "Concluída"
            self._progress.update(self._task_overall, completed=self.current_index)
            self._progress.update(self._task_file, completed=100, visible=False)
            self.live.update(self._render())
            return

        if text.startswith("FALHA:"):
            erro = text.removeprefix("FALHA:").strip()
            self.done_lines.append(
                f"[red]✗[/red] {self.current_title} — {erro}"
            )
            self.status = "Falhou"
            self._progress.update(self._task_overall, completed=self.current_index)
            self._progress.update(self._task_file, visible=False)
            self.live.update(self._render())
            return

        if "Já existe" in text or "pulando" in text.lower():
            self.done_lines.append(f"[dim]–[/dim] {self.current_title} (pulada)")
            self.status = "Pulada"
            self._progress.update(self._task_overall, completed=self.current_index)
            self.live.update(self._render())
            return

        # Mensagens auxiliares curtas (criar aula, salvar conteúdo…).
        if text.startswith("http://") or text.startswith("https://"):
            return
        if "URL:" in text:
            self.status = "Upload concluído"
            self.live.update(self._render())
            return

        cleaned = text.lstrip()
        if len(cleaned) > 90:
            cleaned = cleaned[:87] + "…"
        self.status = cleaned
        self.live.update(self._render())

    def _render(self):
        from rich.console import Group
        from rich.text import Text

        lines: list = [
            self._progress,
            Text(""),
        ]
        if self.current_title:
            lines.append(
                Text.from_markup(
                    f"[bold]Agora:[/bold] {self.current_title} "
                    f"[dim]({self.current_action})[/dim]"
                )
            )
        if self.file_label:
            lines.append(Text.from_markup(f"[dim]{self.file_label}[/dim]"))
        lines.append(Text.from_markup(f"[cyan]{self.status}[/cyan]"))

        if self.done_lines:
            lines.append(Text(""))
            lines.append(Text.from_markup("[bold]Concluídas[/bold]"))
            # Mostra as últimas para não estourar a tela.
            for line in self.done_lines[-8:]:
                lines.append(Text.from_markup(f"  {line}"))
            hidden = len(self.done_lines) - 8
            if hidden > 0:
                lines.append(Text.from_markup(f"  [dim]… e mais {hidden}[/dim]"))

        return Panel(
            Group(*lines),
            title=f"Progresso · {self.current_index}/{self.total}",
            border_style="cyan",
        )

    def print_final(
        self,
        *,
        ok: int,
        pulados: int,
        falhas: list[tuple[str, str]],
        state_path,
        portal_url: str = "",
    ) -> None:
        if falhas:
            style = "red"
            titulo = "Upload com falhas"
        elif ok or pulados:
            style = "green"
            titulo = "Upload concluído"
        else:
            style = "yellow"
            titulo = "Nada enviado"

        body = (
            f"[green]{ok}[/green] ok  ·  "
            f"[dim]{pulados}[/dim] puladas  ·  "
            f"[red]{len(falhas)}[/red] falhas\n"
            f"[dim]Estado: {state_path}[/dim]"
        )
        if portal_url:
            body += f"\n\n[bold]Conferir no portal:[/bold]\n[cyan]{portal_url}[/cyan]"
        if falhas:
            detalhes = "\n".join(
                f"[red]✗[/red] {titulo_aula}: {msg}" for titulo_aula, msg in falhas
            )
            body += f"\n\n{detalhes}\n[dim]Retome com: aula-uploader resume[/dim]"
        console.print()
        console.print(Panel(body, title=titulo, border_style=style, padding=(1, 2)))


BATCH_STEPS = 7


def show_batch_step(number: int, title: str, description: str = "") -> None:
    """Etapa do fluxo em lote (paralelo ao assistente simples)."""
    console.clear()
    progress = "  ".join(
        "[cyan]●[/cyan]" if idx <= number else "[dim]○[/dim]"
        for idx in range(1, BATCH_STEPS + 1)
    )
    body = f"[bold]{title}[/bold]"
    if description:
        body += f"\n[dim]{description}[/dim]"
    console.print(
        Panel(
            body,
            title=f"aula-uploader · Lote {number}/{BATCH_STEPS}",
            subtitle=progress,
            border_style="cyan",
            padding=(1, 2),
        )
    )
    _gap()


def show_batch_chapters_table(chapters) -> None:
    from aula_uploader.batch import BatchChapterDraft

    table = _make_table("Capítulos do lote")
    table.add_column("#", justify="right", no_wrap=True)
    table.add_column("Ordem", justify="right", no_wrap=True)
    table.add_column("Nome", overflow="fold")
    table.add_column("Bunny", no_wrap=True)
    table.add_column("Situação", no_wrap=True)
    for idx, chapter in enumerate(chapters, start=1):
        assert isinstance(chapter, BatchChapterDraft)
        if chapter.capitulo_id and chapter.already_existed:
            situacao = "[magenta]já existe[/magenta]"
        elif chapter.capitulo_id:
            situacao = "[green]criar[/green]"
        else:
            situacao = "[dim]pendente[/dim]"
        table.add_row(
            str(idx),
            str(chapter.ordem),
            chapter.nome,
            chapter.bunny_folder_id,
            situacao,
        )
    console.print(table)
    _gap()


def build_batch_chapters(
    *,
    suggested_order: int,
    existing: list[CapituloInfo] | None = None,
) -> list[BatchChapterDraft] | None:
    """Monta a lista de capítulos do lote (nome, ordem, Bunny URL)."""
    from aula_uploader.batch import batch_reuse_warnings, validate_batch_chapters

    chapters: list[BatchChapterDraft] = []
    next_order = suggested_order

    while True:
        show_batch_step(
            2,
            "Montar capítulos",
            "Informe nome, ordem e URL Bunny de cada capítulo. Depois revise.",
        )
        if chapters:
            show_batch_chapters_table(chapters)

        escolha = questionary.select(
            "Capítulos do lote:",
            choices=[
                questionary.Choice("Adicionar capítulo", value="add"),
                questionary.Choice(
                    "Editar um capítulo",
                    value="edit",
                    disabled=None if chapters else "Nenhum ainda",
                ),
                questionary.Choice(
                    "Remover um capítulo",
                    value="remove",
                    disabled=None if chapters else "Nenhum ainda",
                ),
                questionary.Choice(
                    "Continuar — revisar lista",
                    value="done",
                    disabled=None if chapters else "Adicione pelo menos um",
                ),
                questionary.Choice("Cancelar lote", value="cancel"),
            ],
        ).ask()
        if not escolha or escolha == "cancel":
            return None
        if escolha == "done":
            errors = validate_batch_chapters(chapters, existing=existing)
            if errors:
                console.print("[red]Corrija antes de continuar:[/red]")
                for err in errors:
                    console.print(f"  • {err}")
                _gap()
                continue
            break
        if escolha == "add":
            draft = _ask_batch_chapter_fields(default_order=next_order)
            if draft is None:
                continue
            chapters.append(draft)
            next_order = max(c.ordem for c in chapters) + 1
            continue
        if escolha == "edit":
            idx = _pick_batch_chapter_index(chapters, "Editar qual capítulo?")
            if idx is None:
                continue
            updated = _ask_batch_chapter_fields(
                default_order=chapters[idx].ordem,
                defaults=chapters[idx],
            )
            if updated is not None:
                chapters[idx] = updated
            continue
        if escolha == "remove":
            idx = _pick_batch_chapter_index(chapters, "Remover qual capítulo?")
            if idx is not None:
                removed = chapters.pop(idx)
                console.print(f"[dim]Removido: {removed.nome}[/dim]")

    show_batch_step(
        3,
        "Revisar capítulos",
        "Confira nomes, ordens e pastas Bunny. Nenhuma Bunny pode se repetir.",
    )
    show_batch_chapters_table(chapters)
    errors = validate_batch_chapters(chapters, existing=existing)
    if errors:
        for err in errors:
            console.print(f"[red]• {err}[/red]")
        return None
    avisos = batch_reuse_warnings(chapters, existing)
    if avisos:
        console.print("[yellow]Atenção:[/yellow]")
        for aviso in avisos:
            console.print(f"  [yellow]•[/yellow] {aviso}")
        _gap()
    if not ask_yes_no("Capítulos do lote estão corretos?", default=True):
        return build_batch_chapters(
            suggested_order=suggested_order, existing=existing
        )
    return chapters


def _ask_batch_chapter_fields(*, default_order: int, defaults=None):
    from aula_uploader.batch import BatchChapterDraft

    nome = questionary.text(
        "Nome do capítulo:",
        default=defaults.nome if defaults else "",
        validate=lambda text: bool(text.strip()),
    ).ask()
    if nome is None:
        return None
    bunny_default = ""
    if defaults is not None:
        bunny_default = defaults.bunny_url or defaults.bunny_folder_id
    bunny_url = questionary.text(
        "URL ou ID da pasta Bunny:",
        default=bunny_default,
        instruction="A pasta deve já existir no Bunny.",
    ).ask()
    if bunny_url is None:
        return None
    try:
        bunny_id = parse_bunny_folder_id(bunny_url)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        return None
    order = questionary.text(
        "Ordem do capítulo:",
        default=str(defaults.ordem if defaults else default_order),
        validate=lambda text: text.isdigit() and int(text) > 0,
    ).ask()
    if order is None:
        return None
    return BatchChapterDraft(
        nome=nome.strip(),
        ordem=int(order),
        bunny_folder_id=bunny_id,
        bunny_url=bunny_url.strip(),
    )


def _pick_batch_chapter_index(chapters, prompt: str) -> int | None:
    escolha = questionary.select(
        prompt,
        choices=[
            questionary.Choice(
                f"#{idx} · ordem {c.ordem} · {c.nome}",
                value=str(idx - 1),
            )
            for idx, c in enumerate(chapters, start=1)
        ]
        + [questionary.Choice("Voltar", value="back")],
    ).ask()
    if not escolha or escolha == "back":
        return None
    return int(escolha)


def link_batch_folders(chapters) -> bool:
    """Pede pasta/ZIP manualmente para cada capítulo do lote."""
    from aula_uploader.batch import validate_batch_folders
    from aula_uploader.media import cleanup_temp, enrich_durations, resolve_source
    from aula_uploader.naming import listar_videos

    show_batch_step(
        5,
        "Vincular pastas de vídeo",
        "Escolha à mão a pasta ou ZIP de cada capítulo (ou pule vídeos).",
    )

    for chapter in chapters:
        # Limpa temp anterior se re-vincular.
        if chapter.temp_dir is not None:
            cleanup_temp(chapter.temp_dir)
            chapter.temp_dir = None

        console.print(
            Panel(
                f"[bold]{chapter.nome}[/bold]\n"
                f"Ordem {chapter.ordem} · Bunny {chapter.bunny_folder_id}"
                + (f" · ID {chapter.capitulo_id}" if chapter.capitulo_id else ""),
                title="Capítulo",
                border_style="cyan",
                padding=(0, 1),
            )
        )
        _gap()
        escolha = questionary.select(
            f"Vídeos para “{chapter.nome}”:",
            choices=[
                questionary.Choice("Informar pasta ou ZIP", value="path"),
                questionary.Choice("Pular vídeos neste capítulo", value="skip"),
            ],
        ).ask()
        if not escolha:
            return False
        if escolha == "skip":
            chapter.skip_videos = True
            chapter.pasta = None
            chapter.aulas = []
            console.print("[dim]Sem vídeos neste capítulo.[/dim]")
            _gap()
            continue

        while True:
            fonte = ask_source_path()
            try:
                pasta, temp_dir = resolve_source(fonte)
                aulas = listar_videos(pasta)
                if not aulas:
                    cleanup_temp(temp_dir)
                    console.print(f"[red]Nenhum vídeo em {pasta}[/red]")
                    continue
                enrich_durations(aulas)
                chapter.pasta = pasta
                chapter.fonte = str(fonte)
                chapter.aulas = aulas
                chapter.skip_videos = False
                chapter.temp_dir = temp_dir
                console.print(
                    f"[green]✓[/green] {len(aulas)} vídeo(s) em [bold]{pasta.name}[/bold]"
                )
                _gap()
                break
            except (FileNotFoundError, RuntimeError, OSError, NotADirectoryError) as exc:
                console.print(f"[red]{exc}[/red]")

    errors = validate_batch_folders(chapters)
    if errors:
        console.print("[red]Problemas no vínculo das pastas:[/red]")
        for err in errors:
            console.print(f"  • {err}")
        if not ask_yes_no("Tentar vincular de novo?", default=True):
            return False
        return link_batch_folders(chapters)

    show_batch_folders_table(chapters)
    return ask_yes_no("Vínculos capítulo ↔ pasta estão corretos?", default=True)


def show_batch_folders_table(chapters) -> None:
    table = _make_table("Capítulos ↔ pastas")
    table.add_column("Ordem", justify="right")
    table.add_column("Capítulo")
    table.add_column("Pasta")
    table.add_column("Vídeos", justify="right")
    for chapter in chapters:
        if chapter.skip_videos or chapter.pasta is None:
            pasta = "[dim]— (sem vídeos)[/dim]"
            qtd = "—"
        else:
            pasta = str(chapter.pasta)
            qtd = str(len(chapter.aulas))
        table.add_row(str(chapter.ordem), chapter.nome, pasta, qtd)
    console.print(table)
    _gap()


def show_batch_final_table(chapters) -> None:
    from aula_uploader.batch import batch_summary_rows

    table = _make_table("Conferência final do lote")
    table.add_column("Ordem", justify="right")
    table.add_column("Capítulo")
    table.add_column("Bunny")
    table.add_column("No portal")
    table.add_column("Pasta")
    table.add_column("Aulas")
    table.add_column("Ações")
    for row in batch_summary_rows(chapters):
        table.add_row(
            row["ordem"],
            row["capitulo"],
            row["bunny"],
            row["destino"],
            row["pasta"],
            row["aulas"],
            row["acoes"],
        )
    console.print(table)
    _gap()
