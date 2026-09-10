"""Execução do plano de upload com estado retomável."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from aula_uploader.media import mask_text
from aula_uploader.plan import Acao, PlanoItem
from aula_uploader.portal_client import ConteudoUploadError, PortalClient
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


ProgressCb = Callable[[PlanoItem, str, str | None], None]
ChunkCb = Callable[[PlanoItem, int, int], None]


def _aplicar_fase_upload(
    *,
    nome: str,
    item: PlanoItem,
    conteudo_id: int,
    fase: str,
    state: UploadState,
    progress: ProgressCb | None,
    emit: Callable[[str], None],
) -> str:
    """Aplica resultado do upload. Retorna ok | processando."""
    if fase == "pronto":
        state.mark(nome, "done", conteudo_id=conteudo_id)
        emit("  OK — pronta para play")
        if progress:
            progress(item, "ok")
        return "ok"
    state.mark(nome, "processing", conteudo_id=conteudo_id)
    emit("  No Nivo — processando (pode seguir a próxima)")
    if progress:
        progress(item, "processando")
    return "processando"


def _aguardar_processamentos(
    portal: PortalClient,
    *,
    capitulo_id: int,
    state: UploadState,
    plano_por_arquivo: dict[str, PlanoItem],
    log: Callable[[str], None] | None,
    on_progress: ProgressCb | None,
    max_espera_s: float = 45 * 60,
    intervalo: float = 15.0,
) -> int:
    """Espera itens em processing ficarem prontos para play. Retorna quantos viraram ok."""

    def emit(msg: str) -> None:
        if log:
            log(msg)

    pendentes = [i for i in state.items if i.status == "processing" and i.conteudo_id]
    if not pendentes:
        return 0
    emit(f"Aguardando Nivo processar {len(pendentes)} aula(s)...")
    prontos = 0
    inicio = time.time()
    rodada = 0
    while pendentes and (time.time() - inicio) < max_espera_s:
        rodada += 1
        ainda: list = []
        for st in pendentes:
            assert st.conteudo_id is not None
            try:
                conteudo = portal.get_conteudo(st.conteudo_id)
            except Exception:  # noqa: BLE001
                conteudo = None
            tabela_ok = portal._conteudo_tem_video_na_tabela(  # noqa: SLF001
                capitulo_id, st.conteudo_id
            )
            play_ok = bool(
                conteudo is not None and portal.conteudo_pronto_para_play(conteudo)
            )
            tempo_ok = bool(
                conteudo is not None
                and conteudo.tempo not in ("", "00:00", "---")
                and tabela_ok
            )
            if play_ok or tempo_ok:
                state.mark(st.arquivo, "done", conteudo_id=st.conteudo_id)
                prontos += 1
                emit(f"  Pronta: {st.titulo}")
                plano_item = plano_por_arquivo.get(st.arquivo)
                if on_progress and plano_item is not None:
                    on_progress(plano_item, "ok")
            else:
                ainda.append(st)
        pendentes = ainda
        if not pendentes:
            break
        if rodada == 1 or rodada % 4 == 0:
            nomes = ", ".join(p.titulo for p in pendentes[:3])
            emit(f"  Ainda no Nivo: {nomes}" + ("…" if len(pendentes) > 3 else ""))
        time.sleep(intervalo)
    for st in pendentes:
        emit(
            f"  Ainda processando: {st.titulo} "
            f"(conteúdo {st.conteudo_id}) — confira depois no portal"
        )
        plano_item = plano_por_arquivo.get(st.arquivo)
        if on_progress and plano_item is not None:
            on_progress(plano_item, "processando")
    return prontos


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
    on_progress: ProgressCb | None = None,
    on_chunk: ChunkCb | None = None,
    wait_nivo: bool = True,
) -> tuple[int, int, list[tuple[str, str]]]:
    """Executa uploads. Retorna (ok, pulados, falhas).

    ``ok`` conta aulas prontas para play. Itens ainda no Nivo ficam
    ``processing`` (não entram em falhas).
    """

    def emit(msg: str) -> None:
        if log:
            log(msg)

    def progress(item: PlanoItem, phase: str, error: str | None = None) -> None:
        if on_progress:
            on_progress(item, phase, error)

    ok = 0
    pulados = 0
    falhas: list[tuple[str, str]] = []
    total = len(plano)
    plano_por_arquivo = {item.aula.path.name: item for item in plano}

    done_files = {
        item.arquivo
        for item in state.items
        if item.status in {"done", "skipped"}
    }

    for indice, item in enumerate(plano, start=1):
        nome = item.aula.path.name
        if only_pending and nome in done_files and item.acao != Acao.FORCAR:
            if any(s.arquivo == nome and s.status == "skipped" for s in state.items):
                pulados += 1
            else:
                ok += 1
            continue

        emit(f"[{indice}/{total}] {item.aula.titulo} ({item.acao.value})")
        progress(item, "start")

        def chunk_cb(atual: int, total_chunks: int, *, _item=item) -> None:
            if on_chunk:
                on_chunk(_item, atual, total_chunks)

        def status_cb(phase: str, *, _item=item, _nome=nome) -> None:
            # "conteudo:123" — ID criado no portal; grava antes do upload do arquivo.
            if isinstance(phase, str) and phase.startswith("conteudo:"):
                try:
                    cid = int(phase.split(":", 1)[1])
                except ValueError:
                    return
                _item.existente_id = cid
                state.mark(_nome, "pending", conteudo_id=cid)
                if on_progress:
                    on_progress(_item, "id_ready", None)
                return
            # salvando | processando — passos explícitos na UI.
            progress(_item, phase)

        try:
            if item.acao == Acao.PULAR:
                emit("  Já existe com vídeo — pulando.")
                state.mark(nome, "skipped", conteudo_id=item.existente_id)
                pulados += 1
                progress(item, "skip")
                continue

            if item.acao in {Acao.ENVIAR, Acao.FORCAR}:
                if item.existente_id is None:
                    raise RuntimeError(
                        f"Ação '{item.acao.value}' sem ID da aula existente"
                    )
                fase = portal.upload_aula_video(
                    item.existente_id,
                    item.aula.path,
                    capitulo_id=capitulo_id,
                    log=lambda m: emit(f"  {m}"),
                    chunk_timeout=chunk_timeout,
                    on_chunk=chunk_cb if on_chunk else None,
                    on_status=status_cb if on_progress else None,
                )
                resultado = _aplicar_fase_upload(
                    nome=nome,
                    item=item,
                    conteudo_id=item.existente_id,
                    fase=fase,
                    state=state,
                    progress=progress,
                    emit=emit,
                )
            else:
                novo_id, fase = portal.criar_aula_com_video(
                    capitulo_id,
                    item.aula.titulo,
                    item.aula.ordem,
                    item.aula.path,
                    status=status_criacao,
                    log=lambda m: emit(f"  {m}"),
                    chunk_timeout=chunk_timeout,
                    on_chunk=chunk_cb if on_chunk else None,
                    on_status=status_cb if on_progress else None,
                )
                resultado = _aplicar_fase_upload(
                    nome=nome,
                    item=item,
                    conteudo_id=novo_id,
                    fase=fase,
                    state=state,
                    progress=progress,
                    emit=emit,
                )
            if resultado == "ok":
                ok += 1
        except Exception as exc:  # noqa: BLE001 - relatório agregado
            msg = mask_text(str(exc))
            emit(f"  FALHA: {msg}")
            cid = item.existente_id
            if isinstance(exc, ConteudoUploadError) and exc.conteudo_id:
                cid = exc.conteudo_id
            state.mark(nome, "failed", conteudo_id=cid, erro=msg)
            falhas.append((item.aula.titulo, msg))
            progress(item, "fail", msg)
        finally:
            portal.save_session()

    if wait_nivo:
        prontos = _aguardar_processamentos(
            portal,
            capitulo_id=capitulo_id,
            state=state,
            plano_por_arquivo=plano_por_arquivo,
            log=log,
            on_progress=on_progress,
        )
        ok += prontos

    return ok, pulados, falhas
