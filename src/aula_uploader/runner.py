"""Execução do plano de upload com estado retomável."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from aula_uploader.media import mask_text
from aula_uploader.plan import Acao, PlanoItem
from aula_uploader.portal_client import PortalClient
from aula_uploader.state import ItemState, UploadState


def build_state(
    *,
    portal: str,
    capitulo_id: int,
    pasta: Path,
    plano: list[PlanoItem],
    status_criacao: str,
    force: bool,
    fonte: str | Path = "",
) -> UploadState:
    state = UploadState(
        portal=portal,
        capitulo_id=capitulo_id,
        pasta=str(pasta),
        fonte=str(fonte) if fonte else str(pasta),
        status_criacao=status_criacao,
        force=force,
        items=[
            ItemState(
                arquivo=item.aula.path.name,
                ordem=item.aula.ordem,
                titulo=item.aula.titulo,
                status="skipped" if item.acao == Acao.PULAR else "pending",
                conteudo_id=item.existente_id,
            )
            for item in plano
        ],
    )
    state.save()
    return state


def executar_plano(
    portal: PortalClient,
    *,
    capitulo_id: int,
    plano: list[PlanoItem],
    state: UploadState,
    status_criacao: str = "0",
    chunk_timeout: float = 300.0,
    log: Callable[[str], None] | None = None,
    only_pending: bool = False,
) -> tuple[int, int, list[tuple[str, str]]]:
    """Executa uploads. Retorna (ok, pulados, falhas)."""

    def emit(msg: str) -> None:
        if log:
            log(msg)

    ok = 0
    pulados = 0
    falhas: list[tuple[str, str]] = []
    total = len(plano)

    done_files = {
        item.arquivo
        for item in state.items
        if item.status in {"done", "skipped"}
    }

    for indice, item in enumerate(plano, start=1):
        nome = item.aula.path.name
        if only_pending and nome in done_files and item.acao != Acao.FORCAR:
            # Já concluído em execução anterior.
            if any(s.arquivo == nome and s.status == "skipped" for s in state.items):
                pulados += 1
            else:
                ok += 1
            continue

        emit(f"[{indice}/{total}] {item.aula.titulo} ({item.acao.value})")
        try:
            if item.acao == Acao.PULAR:
                emit("  Já existe com vídeo — pulando.")
                state.mark(nome, "skipped", conteudo_id=item.existente_id)
                pulados += 1
                continue

            if item.acao in {Acao.ENVIAR, Acao.FORCAR}:
                if item.existente_id is None:
                    raise RuntimeError(
                        f"Ação '{item.acao.value}' sem ID da aula existente"
                    )
                portal.upload_aula_video(
                    item.existente_id,
                    item.aula.path,
                    capitulo_id=capitulo_id,
                    log=lambda m: emit(f"  {m}"),
                    chunk_timeout=chunk_timeout,
                )
                state.mark(nome, "done", conteudo_id=item.existente_id)
            else:
                novo_id = portal.criar_aula_com_video(
                    capitulo_id,
                    item.aula.titulo,
                    item.aula.ordem,
                    item.aula.path,
                    status=status_criacao,
                    log=lambda m: emit(f"  {m}"),
                    chunk_timeout=chunk_timeout,
                )
                state.mark(nome, "done", conteudo_id=novo_id)
            ok += 1
            emit("  OK")
        except Exception as exc:  # noqa: BLE001 - relatório agregado
            # mask_text: a exceção pode citar a URL assinada do S3.
            msg = mask_text(str(exc))
            emit(f"  FALHA: {msg}")
            state.mark(nome, "failed", conteudo_id=item.existente_id, erro=msg)
            falhas.append((item.aula.titulo, msg))
        finally:
            portal.save_session()

    return ok, pulados, falhas
