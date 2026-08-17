"""Ponto de entrada CLI: assistente, plan, upload, resume, logout, doctor."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console

from aula_uploader import __version__
from aula_uploader.media import (
    cleanup_temp,
    enrich_durations,
    resolve_source,
)
from aula_uploader.naming import listar_videos
from aula_uploader.ollama_client import detect_ollama
from aula_uploader.plan import montar_plano, parse_capitulo_id
from aula_uploader.runner import build_state, executar_plano
from aula_uploader.session import (
    PORTAL_LABELS,
    clear_all_sessions,
    ensure_authenticated,
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
    tui.banner()
    tui.show_environment()

    portal_key = tui.ask_portal()
    capitulo_raw = tui.ask_capitulo_url()
    try:
        capitulo_id = parse_capitulo_id(capitulo_raw)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        return 1

    fonte = tui.ask_source_path()
    temp_dir = None
    try:
        pasta, temp_dir = resolve_source(fonte)
    except (FileNotFoundError, RuntimeError, OSError) as exc:
        console.print(f"[red]{exc}[/red]")
        return 1

    try:
        aulas = listar_videos(pasta, recursivo=bool(args.recursivo))
        if not aulas:
            console.print(f"[red]Nenhum vídeo encontrado em {pasta}[/red]")
            return 1
        enrich_durations(aulas)
        aulas = tui.review_aulas(aulas)
        aulas.sort(key=lambda a: (a.ordem, a.path.name))

        console.print("Autenticando no portal...")
        portal = ensure_authenticated(portal_key, log=lambda m: console.print(f"  {m}"))
        capitulo = portal.inspect_capitulo(capitulo_id)
        existentes = portal.listar_conteudos_tabela(capitulo_id)
        plano = montar_plano(aulas, existentes, force=bool(args.force))

        tui.show_destino(
            portal_key=portal_key,
            capitulo=capitulo,
            pasta=fonte,
            total=len(aulas),
        )
        tui.show_plan_table(plano)

        publicar = bool(args.publicar) or tui.ask_yes_no(
            "Publicar as aulas novas? (Não = criar como Rascunho)",
            default=False,
        )
        if not tui.confirm_upload(plano, publicar=publicar):
            console.print("Cancelado. Nada foi enviado.")
            return 0

        status = "1" if publicar else "0"
        state = build_state(
            portal=portal_key,
            capitulo_id=capitulo_id,
            pasta=pasta,
            plano=plano,
            status_criacao=status,
            force=bool(args.force),
        )
        ok, pulados, falhas = executar_plano(
            portal,
            capitulo_id=capitulo_id,
            plano=plano,
            state=state,
            status_criacao=status,
            log=lambda m: console.print(m),
        )
        console.print(
            f"\nConcluído: {ok} ok · {pulados} pulados · {len(falhas)} falhas"
        )
        console.print(f"Estado salvo em: {state.path}")
        if falhas:
            for titulo, msg in falhas:
                console.print(f"  [red]✗ {titulo}: {msg}[/red]")
            console.print("Retome com: aula-uploader resume --portal ... --capitulo ...")
            return 1
        return 0
    finally:
        cleanup_temp(temp_dir)


def cmd_plan(args: argparse.Namespace) -> int:
    return _run_noninteractive(args, execute=False, assume_yes=True)


def cmd_upload(args: argparse.Namespace) -> int:
    return _run_noninteractive(args, execute=True, assume_yes=bool(args.yes))


def _run_noninteractive(
    args: argparse.Namespace, *, execute: bool, assume_yes: bool
) -> int:
    temp_dir = None
    try:
        pasta, temp_dir = resolve_source(Path(args.fonte))
        aulas = listar_videos(pasta, recursivo=bool(args.recursivo))
        if not aulas:
            console.print("[red]Nenhum vídeo encontrado.[/red]")
            return 1
        enrich_durations(aulas)
        portal = ensure_authenticated(args.portal, log=lambda m: console.print(m))
        capitulo = portal.inspect_capitulo(args.capitulo)
        existentes = portal.listar_conteudos_tabela(args.capitulo)
        plano = montar_plano(aulas, existentes, force=bool(args.force))
        tui.show_destino(
            portal_key=args.portal,
            capitulo=capitulo,
            pasta=Path(args.fonte),
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

    portal = ensure_authenticated(args.portal, log=lambda m: console.print(m))
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
