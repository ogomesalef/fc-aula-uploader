"""Ponto de entrada CLI: assistente, plan, upload, resume, logout, doctor."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console

from aula_uploader import __version__
from aula_uploader.catalog import CatalogStore
from aula_uploader.media import (
    cleanup_temp,
    enrich_durations,
    normalize_user_path,
    resolve_source,
)
from aula_uploader.naming import listar_videos
from aula_uploader.ollama_client import detect_ollama
from aula_uploader.plan import montar_plano, parse_capitulo_id
from aula_uploader.portal_client import CapituloResumo
from aula_uploader.runner import build_state, executar_plano
from aula_uploader.session import (
    PORTAL_LABELS,
    clear_all_sessions,
    ensure_authenticated,
    has_saved_session,
)
from aula_uploader.state import UploadState
from aula_uploader import tui

console = Console()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="aula-uploader",
        description=(
            "Sobe aulas (vídeos) em um capítulo já criado no portal "
            "Full Cycle ou DevOps Pro."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("doctor", help="Checa Python, ffprobe e Ollama")
    sub.add_parser("logout", help="Apaga sessões salvas localmente")

    p_assist = sub.add_parser("assistente", help="Fluxo interativo (padrão)")
    p_assist.add_argument("--recursivo", action="store_true")
    p_assist.add_argument(
        "--publicar",
        action="store_true",
        help="Cria aulas como Publicadas (padrão: Rascunho)",
    )
    p_assist.add_argument("--force", action="store_true")

    p_plan = sub.add_parser("plan", help="Mostra o plano sem enviar")
    _add_common(p_plan)

    p_upload = sub.add_parser("upload", help="Executa o upload (pede confirmação)")
    _add_common(p_upload)
    p_upload.add_argument("-y", "--yes", action="store_true", help="Não pedir confirmação")

    p_resume = sub.add_parser("resume", help="Retoma upload pendente/falho")
    p_resume.add_argument("--portal", required=True, choices=list(PORTAL_LABELS))
    p_resume.add_argument("--capitulo", required=True, type=parse_capitulo_id)

    args = parser.parse_args(argv)
    if not args.cmd:
        return cmd_assistente(argparse.Namespace(recursivo=False, publicar=False, force=False))

    if args.cmd == "doctor":
        return cmd_doctor()
    if args.cmd == "logout":
        return cmd_logout()
    if args.cmd == "assistente":
        return cmd_assistente(args)
    if args.cmd == "plan":
        return cmd_plan(args)
    if args.cmd == "upload":
        return cmd_upload(args)
    if args.cmd == "resume":
        return cmd_resume(args)
    parser.print_help()
    return 1


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--portal", required=True, choices=list(PORTAL_LABELS))
    p.add_argument("--capitulo", required=True, type=parse_capitulo_id)
    p.add_argument("--fonte", required=True, type=Path, help="Pasta ou .zip")
    p.add_argument("--recursivo", action="store_true")
    p.add_argument("--publicar", action="store_true")
    p.add_argument("--force", action="store_true")


def cmd_doctor() -> int:
    from aula_uploader.media import ffprobe_available

    tui.banner()
    console.print(f"aula-uploader {__version__}")
    console.print(f"Python: {sys.version.split()[0]}")
    console.print(f"ffprobe: {'ok' if ffprobe_available() else 'ausente (opcional)'}")
    info = detect_ollama()
    if info.reachable:
        console.print(f"Ollama: ok ({', '.join(info.models) or 'sem modelos'})")
        console.print(f"Modelo sugerido: {info.recommended}")
    elif info.installed:
        console.print("Ollama: instalado, mas API local não responde")
    else:
        console.print("Ollama: não instalado (opcional)")
    console.print(
        "Credenciais: use o mesmo usuário/senha do login administrativo "
        "em portal.fullcycle.com.br ou portal.devopspro.com.br "
        "(via .env ou prompt)."
    )
    return 0


def cmd_logout() -> int:
    removed = clear_all_sessions()
    if removed:
        console.print(f"Sessões removidas: {', '.join(removed)}")
    else:
        console.print("Nenhuma sessão salva.")
    return 0


def cmd_assistente(args: argparse.Namespace) -> int:
    portal = None
    temp_dir = None
    try:
        tui.show_step(
            1,
            "Portal e login",
            "Autentique primeiro. Assim falhamos cedo se a senha estiver errada.",
        )
        portal_key = tui.ask_portal()
        portal = tui.ask_login(portal_key)
        catalog = CatalogStore()

        tui.show_step(
            2,
            "Escolher o capítulo",
            "Crie um capítulo novo com uma pasta Bunny ou use um capítulo existente.",
        )
        target_mode = tui.ask_target_mode()

        if target_mode == "create":
            tui.show_step(
                2,
                "Criar capítulo",
                "Primeiro confirme o curso. A pasta Bunny já deve existir.",
            )
            while True:
                curso_id = tui.ask_curso_id()
                try:
                    curso = portal.inspect_curso(curso_id)
                    capitulos = portal.list_capitulos(curso_id)
                    # A consulta necessária para sugerir ordem já atualiza o catálogo.
                    catalog.upsert_course(curso, capitulos)
                    break
                except Exception as exc:  # noqa: BLE001
                    console.print(f"[red]Não foi possível ler o curso: {exc}[/red]")

            last_order = max((chapter.ordem for chapter in capitulos), default=0)
            tui.show_curso(curso, last_order=last_order if capitulos else None)
            if not tui.ask_yes_no("Este é o curso certo?", default=True):
                console.print("[yellow]Ok, informe outro curso.[/yellow]")
                while True:
                    curso_id = tui.ask_curso_id()
                    try:
                        curso = portal.inspect_curso(curso_id)
                        capitulos = portal.list_capitulos(curso_id)
                        catalog.upsert_course(curso, capitulos)
                        break
                    except Exception as exc:  # noqa: BLE001
                        console.print(f"[red]Não foi possível ler o curso: {exc}[/red]")
                last_order = max((chapter.ordem for chapter in capitulos), default=0)

            chapter_name, bunny_folder_id, chapter_order = tui.ask_new_chapter_details(
                last_order + 1
            )
            console.print(
                f"\nCapítulo: [bold]{chapter_name}[/bold]\n"
                f"Ordem: [bold]{chapter_order}[/bold]\n"
                f"Pasta Bunny (ID): [bold]{bunny_folder_id}[/bold]"
            )
            if not tui.ask_yes_no(
                "Criar este capítulo agora? Depois ele receberá as aulas.",
                default=True,
            ):
                console.print("[yellow]Operação cancelada. Nada foi criado.[/yellow]")
                return 0
            created = portal.create_capitulo(
                curso_id,
                chapter_name,
                chapter_order,
                bunny_folder_id=bunny_folder_id,
            )
            capitulo_id = created.id
            capitulo = CapituloResumo(
                id=created.id,
                nome=created.nome,
                curso_id=curso.id,
                curso_nome=curso.nome,
            )
            existentes = []
            console.print(
                f"[green]✓ Capítulo criado: {created.nome} (ID {created.id})[/green]"
            )
            catalog.sync_course_in_background(portal, curso_id)
        elif target_mode == "mapped":
            tui.show_step(
                2,
                "Capítulo já mapeado",
                "Ao escolher o curso, a lista de capítulos é atualizada no portal.",
            )
            selected = tui.ask_mapped_chapter(catalog, portal)
            if selected is None:
                console.print(
                    "[yellow]Use a opção de informar curso/capítulo para iniciar o mapeamento.[/yellow]"
                )
                return 0
            course_id, capitulo_id = selected
            try:
                capitulo = portal.inspect_capitulo(capitulo_id)
                existentes = portal.listar_conteudos_tabela(capitulo_id)
                catalog.upsert_chapter(capitulo)
            except Exception as exc:  # noqa: BLE001
                console.print(f"[red]Não foi possível abrir o capítulo mapeado: {exc}[/red]")
                console.print(
                    "[dim]Ele pode ter sido removido ou seu acesso pode ter mudado.[/dim]"
                )
                return 1
        else:
            tui.show_step(
                2,
                "Capítulo de destino",
                "Cole o link da lista de aulas do capítulo já criado no portal.",
            )
            while True:
                capitulo_id = tui.ask_capitulo_id()
                try:
                    capitulo = portal.inspect_capitulo(capitulo_id)
                    existentes = portal.listar_conteudos_tabela(capitulo_id)
                    catalog.upsert_chapter(capitulo)
                    if capitulo.curso_id is not None:
                        catalog.sync_course_in_background(portal, capitulo.curso_id)
                    break
                except Exception as exc:  # noqa: BLE001
                    console.print(
                        f"[red]Não foi possível ler o capítulo {capitulo_id}: {exc}[/red]"
                    )
                    console.print(
                        "[dim]Confira se o link é .../admin/curso/conteudo/<ID>/capitulo[/dim]"
                    )

            tui.show_destino(
                portal_key=portal_key,
                capitulo=capitulo,
                pasta=Path("—"),
                total=0,
            )
            if not tui.ask_yes_no("Este é o capítulo certo?", default=True):
                # Volta ao começo deste caminho sem refazer login.
                while True:
                    capitulo_id = tui.ask_capitulo_id()
                    try:
                        capitulo = portal.inspect_capitulo(capitulo_id)
                        existentes = portal.listar_conteudos_tabela(capitulo_id)
                        catalog.upsert_chapter(capitulo)
                        if capitulo.curso_id is not None:
                            catalog.sync_course_in_background(portal, capitulo.curso_id)
                        break
                    except Exception as exc:  # noqa: BLE001
                        console.print(f"[red]Ainda não deu: {exc}[/red]")

        tui.show_step(
            3,
            "Selecionar os vídeos",
            "Informe uma pasta ou um arquivo ZIP. O original não será alterado.",
        )
        while True:
            fonte = tui.ask_source_path()
            try:
                pasta, temp_dir = resolve_source(fonte)
                aulas = listar_videos(pasta, recursivo=bool(args.recursivo))
                if not aulas:
                    cleanup_temp(temp_dir)
                    temp_dir = None
                    console.print(f"[red]Nenhum vídeo encontrado em {pasta}[/red]")
                    console.print("[dim]Escolha outra pasta/.zip.[/dim]")
                    continue
                break
            except (FileNotFoundError, RuntimeError, OSError, NotADirectoryError) as exc:
                cleanup_temp(temp_dir)
                temp_dir = None
                console.print(f"[red]{exc}[/red]")
                console.print("[dim]Tente de novo.[/dim]")

        enrich_durations(aulas)
        aulas = tui.review_aulas(aulas)
        aulas.sort(key=lambda a: (a.ordem, a.path.name))

        # Destino já foi confirmado na etapa 2; aqui só consulta o que já existe.
        console.print("\n[dim]Consultando o capítulo no portal…[/dim]")
        try:
            portal.ensure_authenticated(log=lambda m: console.print(f"  {m}"))
            existentes = portal.listar_conteudos_tabela(capitulo_id)
            capitulo = portal.inspect_capitulo(capitulo_id)
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]Sessão inválida ou capítulo inacessível: {exc}[/red]")
            portal = tui.ask_login(portal_key)
            existentes = portal.listar_conteudos_tabela(capitulo_id)
            capitulo = portal.inspect_capitulo(capitulo_id)

        plano = montar_plano(aulas, existentes, force=bool(args.force))

        tui.show_step(
            5,
            "Revisar plano",
            "Confira títulos, ações (criar / enviar / pular) e como publicar.",
        )
        tui.show_destino(
            portal_key=portal_key,
            capitulo=capitulo,
            pasta=fonte,
            total=len(aulas),
        )
        tui.show_plan_table(plano)
        status = tui.ask_publish_status(force_publicar=bool(args.publicar))
        publicar = status == "1"
        if not tui.confirm_upload(plano, publicar=publicar):
            console.print("[yellow]Cancelado. Nada foi enviado.[/yellow]")
            return 0

        state = build_state(
            portal=portal_key,
            capitulo_id=capitulo_id,
            pasta=pasta,
            plano=plano,
            status_criacao=status,
            force=bool(args.force),
        )
        ok, pulados, falhas = tui.run_upload_screen(
            portal,
            capitulo_id=capitulo_id,
            plano=plano,
            state=state,
            status_criacao=status,
            portal_key=portal_key,
            capitulo=capitulo,
        )
        if falhas:
            return 1
        return 0
    finally:
        cleanup_temp(temp_dir)
        if portal is not None:
            try:
                portal.close()
            except Exception:  # noqa: BLE001
                pass


def cmd_plan(args: argparse.Namespace) -> int:
    return _run_noninteractive(args, execute=False, assume_yes=True)


def cmd_upload(args: argparse.Namespace) -> int:
    return _run_noninteractive(args, execute=True, assume_yes=bool(args.yes))


def _run_noninteractive(
    args: argparse.Namespace, *, execute: bool, assume_yes: bool
) -> int:
    temp_dir = None
    try:
        pasta, temp_dir = resolve_source(normalize_user_path(str(args.fonte)))
        aulas = listar_videos(pasta, recursivo=bool(args.recursivo))
        if not aulas:
            console.print("[red]Nenhum vídeo encontrado.[/red]")
            return 1
        enrich_durations(aulas)
        portal = ensure_authenticated(
            args.portal,
            log=lambda m: console.print(m),
            persist_session=False,
            use_saved_session=has_saved_session(args.portal),
        )
        capitulo = portal.inspect_capitulo(args.capitulo)
        existentes = portal.listar_conteudos_tabela(args.capitulo)
        plano = montar_plano(aulas, existentes, force=bool(args.force))
        tui.show_destino(
            portal_key=args.portal,
            capitulo=capitulo,
            pasta=normalize_user_path(str(args.fonte)),
            total=len(aulas),
        )
        tui.show_plan_table(plano)
        if not execute:
            console.print("[dim]Dry-run: nada foi enviado.[/dim]")
            return 0
        publicar = bool(args.publicar)
        if not assume_yes and not tui.confirm_upload(plano, publicar=publicar):
            console.print("Cancelado.")
            return 0
        status = "1" if publicar else "0"
        state = build_state(
            portal=args.portal,
            capitulo_id=args.capitulo,
            pasta=pasta,
            plano=plano,
            status_criacao=status,
            force=bool(args.force),
        )
        ok, pulados, falhas = executar_plano(
            portal,
            capitulo_id=args.capitulo,
            plano=plano,
            state=state,
            status_criacao=status,
            log=lambda m: console.print(m),
        )
        console.print(f"Concluído: {ok} ok · {pulados} pulados · {len(falhas)} falhas")
        return 1 if falhas else 0
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]{exc}[/red]")
        return 1
    finally:
        cleanup_temp(temp_dir)


def cmd_resume(args: argparse.Namespace) -> int:
    state = UploadState.load(args.portal, args.capitulo)
    if not state:
        console.print("[red]Nenhum estado salvo para este portal/capítulo.[/red]")
        return 1
    pending = [i for i in state.items if i.status in {"pending", "failed"}]
    console.print(f"Retomando {len(pending)} item(ns) pendente(s)/falho(s)...")
    if not pending:
        console.print("Nada a retomar.")
        return 0

    from aula_uploader.naming import AulaArquivo
    from aula_uploader.plan import Acao, PlanoItem

    pasta = Path(state.pasta)
    if not pasta.is_dir():
        console.print(f"[red]Pasta do estado não existe mais: {pasta}[/red]")
        return 1

    portal = ensure_authenticated(
        args.portal,
        log=lambda m: console.print(m),
        persist_session=False,
        use_saved_session=has_saved_session(args.portal),
    )
    existentes = {
        l.titulo.strip().lower(): l
        for l in portal.listar_conteudos_tabela(args.capitulo)
    }
    plano: list[PlanoItem] = []
    for item in state.items:
        if item.status not in {"pending", "failed"}:
            continue
        path = pasta / item.arquivo
        if not path.exists():
            # busca recursiva simples pelo nome
            matches = list(pasta.rglob(item.arquivo))
            if not matches:
                console.print(f"[red]Arquivo ausente: {item.arquivo}[/red]")
                continue
            path = matches[0]
        aula = AulaArquivo(
            path=path,
            ordem=item.ordem,
            titulo=item.titulo,
            tamanho_bytes=path.stat().st_size,
        )
        ex = existentes.get(item.titulo.strip().lower())
        if ex and ex.tem_video and not state.force:
            plano.append(PlanoItem(aula=aula, acao=Acao.PULAR, existente_id=ex.id))
        elif ex:
            plano.append(
                PlanoItem(
                    aula=aula,
                    acao=Acao.FORCAR if state.force else Acao.ENVIAR,
                    existente_id=ex.id,
                )
            )
        else:
            plano.append(PlanoItem(aula=aula, acao=Acao.CRIAR))

    ok, pulados, falhas = executar_plano(
        portal,
        capitulo_id=args.capitulo,
        plano=plano,
        state=state,
        status_criacao=state.status_criacao,
        log=lambda m: console.print(m),
        only_pending=True,
    )
    console.print(f"Concluído: {ok} ok · {pulados} pulados · {len(falhas)} falhas")
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(main())
