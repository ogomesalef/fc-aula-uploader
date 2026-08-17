"""Interface interativa (TUI leve) para revisar e confirmar o plano."""

from __future__ import annotations

from pathlib import Path

import questionary
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from aula_uploader.media import format_bytes, format_duration
from aula_uploader.naming import AulaArquivo
from aula_uploader.ollama_client import detect_ollama, suggest_titles
from aula_uploader.plan import Acao, PlanoItem
from aula_uploader.portal_client import CapituloResumo
from aula_uploader.session import PORTAL_LABELS

console = Console()


def banner() -> None:
    console.print(
        Panel.fit(
            "[bold]aula-uploader[/bold]\n"
            "Sobe aulas (vídeos) em um capítulo já criado no portal.\n"
            "[dim]Capítulo e pasta Bunny devem existir antes.[/dim]",
            border_style="cyan",
        )
    )


def ask_portal() -> str:
    escolha = questionary.select(
        "Portal de destino:",
        choices=[
            questionary.Choice("Full Cycle (portal.fullcycle.com.br)", value="fullcycle"),
            questionary.Choice("DevOps Pro (portal.devopspro.com.br)", value="devops"),
        ],
    ).ask()
    if not escolha:
        raise SystemExit(1)
    return escolha


def ask_capitulo_url() -> str:
    valor = questionary.text(
        "Cole o link do capítulo (ou o ID):\n"
        "  ex.: https://portal.fullcycle.com.br/admin/curso/conteudo/299/capitulo"
    ).ask()
    if not valor:
        raise SystemExit(1)
    return valor.strip().strip("'\"")


def ask_source_path() -> Path:
    console.print(
        "[dim]Dica: no terminal do macOS/Linux você pode arrastar a pasta "
        "ou o .zip para esta janela.[/dim]"
    )
    valor = questionary.path(
        "Pasta com os vídeos ou arquivo .zip:"
    ).ask()
    if not valor:
        raise SystemExit(1)
    return Path(valor.strip().strip("'\"")).expanduser()


def ask_yes_no(prompt: str, *, default: bool = False) -> bool:
    return bool(
        questionary.confirm(prompt, default=default).ask()
    )


def show_environment(*, ollama_info=None) -> None:
    from aula_uploader.media import ffprobe_available

    table = Table(title="Ambiente", show_header=False)
    table.add_column("Item")
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


def show_destino(
    *,
    portal_key: str,
    capitulo: CapituloResumo,
    pasta: Path,
    total: int,
) -> None:
    table = Table(title="Destino no portal", show_header=False)
    table.add_column("Campo")
    table.add_column("Valor")
    table.add_row("Portal", PORTAL_LABELS.get(portal_key, portal_key))
    if capitulo.curso_nome or capitulo.curso_id:
        curso = capitulo.curso_nome or ""
        if capitulo.curso_id:
            curso = f"{curso} (ID {capitulo.curso_id})".strip()
        table.add_row("Curso", curso or "—")
    table.add_row("Capítulo", f"{capitulo.nome} (ID {capitulo.id})")
    table.add_row("Pasta/ZIP", str(pasta))
    table.add_row("Aulas", str(total))
    console.print(table)


def show_plan_table(plano: list[PlanoItem]) -> None:
    table = Table(title="Plano de upload")
    table.add_column("#", justify="right")
    table.add_column("Ordem", justify="right")
    table.add_column("Título")
    table.add_column("Arquivo")
    table.add_column("Tamanho", justify="right")
    table.add_column("Duração", justify="right")
    table.add_column("Ação")
    for item in plano:
        aula = item.aula
        marca = "*" if aula.ordem_inferida else ""
        table.add_row(
            str(aula.ordem),
            f"{aula.ordem}{marca}",
            aula.titulo,
            aula.path.name,
            format_bytes(aula.tamanho_bytes),
            format_duration(aula.duracao_segundos),
            item.acao.value,
        )
    console.print(table)
    console.print("[dim]* ordem inferida (sem número claro no nome)[/dim]")


def review_aulas(aulas: list[AulaArquivo]) -> list[AulaArquivo]:
    """Revisa nomes um a um, com opção de confiar no Ollama."""
    ollama = detect_ollama()
    show_environment(ollama_info=ollama)

    modo = questionary.select(
        "Como deseja revisar os nomes das aulas?",
        choices=[
            questionary.Choice("Revisar uma a uma (recomendado)", value="manual"),
            questionary.Choice(
                "Confiar na normalização automática e só confirmar o lote",
                value="auto",
            ),
            questionary.Choice(
                "Usar Ollama para sugerir nomes (se disponível)",
                value="ollama",
                disabled=None if ollama.reachable and ollama.models else "Ollama indisponível",
            ),
        ],
    ).ask()
    if not modo:
        raise SystemExit(1)

    if modo == "ollama":
        model = ollama.recommended
        if len(ollama.models) > 1:
            model = questionary.select(
                "Modelo Ollama:",
                choices=ollama.models,
                default=ollama.recommended,
            ).ask() or ollama.recommended
        assert model
        console.print(f"Consultando Ollama ({model}) só com nomes de arquivo...")
        try:
            suggestions = suggest_titles(
                [a.path.name for a in aulas], model=model, host=ollama.host
            )
            by_name = {str(s.get("arquivo")): s for s in suggestions}
            for aula in aulas:
                sug = by_name.get(aula.path.name)
                if not sug:
                    # tenta match parcial
                    for key, val in by_name.items():
                        if key and key in aula.path.name:
                            sug = val
                            break
                if sug and sug.get("titulo"):
                    aula.titulo = str(sug["titulo"])
                    if int(sug.get("ordem") or 0) > 0:
                        aula.ordem = int(sug["ordem"])
                        aula.ordem_inferida = False
            console.print("[green]Sugestões aplicadas. Revise abaixo.[/green]")
        except Exception as exc:  # noqa: BLE001
            console.print(f"[yellow]Ollama falhou ({exc}). Mantendo normalização local.[/yellow]")

    if modo == "auto" and not any(a.ordem_inferida for a in aulas):
        return aulas

    # Revisão item a item com setas (select) + edição.
    while True:
        _print_aulas(aulas)
        escolha = questionary.select(
            "Navegue com as setas. O que deseja fazer?",
            choices=[
                *[
                    questionary.Choice(
                        f"Editar #{a.ordem}: {a.titulo}  ←  {a.path.name}",
                        value=f"edit:{idx}",
                    )
                    for idx, a in enumerate(aulas)
                ],
                questionary.Choice("OK — nomes estão corretos", value="ok"),
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
    table = Table(title="Aulas (pré-visualização)")
    table.add_column("Ordem", justify="right")
    table.add_column("Título")
    table.add_column("Arquivo")
    table.add_column("Tamanho", justify="right")
    for a in aulas:
        table.add_row(
            f"{a.ordem}{'*' if a.ordem_inferida else ''}",
            a.titulo,
            a.path.name,
            format_bytes(a.tamanho_bytes),
        )
    console.print(table)


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


def confirm_upload(plano: list[PlanoItem], *, publicar: bool) -> bool:
    criar = sum(1 for p in plano if p.acao == Acao.CRIAR)
    enviar = sum(1 for p in plano if p.acao in {Acao.ENVIAR, Acao.FORCAR})
    pular = sum(1 for p in plano if p.acao == Acao.PULAR)
    status = "Publicado" if publicar else "Rascunho"
    console.print(
        f"Resumo: [cyan]{criar}[/cyan] criar · "
        f"[cyan]{enviar}[/cyan] enviar vídeo · "
        f"[cyan]{pular}[/cyan] pular · status [cyan]{status}[/cyan]"
    )
    return ask_yes_no("Confirma o upload no portal?", default=False)
