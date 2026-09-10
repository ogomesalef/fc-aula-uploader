"""Estado local para retomar uploads."""

from __future__ import annotations

import json
import os
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path

from aula_uploader.session import state_dir


def _norm_nome(nome: str) -> str:
    return unicodedata.normalize("NFC", Path(nome).name)


@dataclass
class ItemState:
    arquivo: str
    ordem: int
    titulo: str
    status: str = "pending"  # pending | done | skipped | failed | processing
    conteudo_id: int | None = None
    erro: str = ""


@dataclass
class UploadState:
    portal: str
    capitulo_id: int
    pasta: str
    # Origem informada pelo usuário (pasta ou .zip). A `pasta` pode ser um
    # temporário de extração que já não existe na hora de retomar.
    fonte: str = ""
    status_criacao: str = "0"
    force: bool = False
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    items: list[ItemState] = field(default_factory=list)

    @property
    def path(self) -> Path:
        return state_dir() / f"upload-{self.portal}-{self.capitulo_id}.json"

    def save(self) -> Path:
        """Grava o progresso; escrita atômica para não corromper em Ctrl+C."""
        self.updated_at = time.time()
        payload = {
            "portal": self.portal,
            "capitulo_id": self.capitulo_id,
            "pasta": self.pasta,
            "fonte": self.fonte,
            "status_criacao": self.status_criacao,
            "force": self.force,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "items": [asdict(i) for i in self.items],
        }
        path = self.path
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        try:
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            try:
                tmp.chmod(0o600)
            except OSError:
                pass
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
        return path

    @classmethod
    def load(cls, portal: str, capitulo_id: int) -> UploadState | None:
        path = state_dir() / f"upload-{portal}-{capitulo_id}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        items = [ItemState(**item) for item in data.get("items", [])]
        return cls(
            portal=data["portal"],
            capitulo_id=int(data["capitulo_id"]),
            pasta=data.get("pasta", ""),
            fonte=data.get("fonte", ""),
            status_criacao=data.get("status_criacao", "0"),
            force=bool(data.get("force", False)),
            created_at=float(data.get("created_at", time.time())),
            updated_at=float(data.get("updated_at", time.time())),
            items=items,
        )

    def find_item(self, arquivo: str) -> ItemState | None:
        alvo = _norm_nome(arquivo)
        for item in self.items:
            if _norm_nome(item.arquivo) == alvo:
                return item
        return None

    def mark(
        self,
        arquivo: str,
        status: str,
        *,
        conteudo_id: int | None = None,
        erro: str = "",
    ) -> None:
        item = self.find_item(arquivo)
        if item is None:
            item = ItemState(
                arquivo=Path(arquivo).name,
                ordem=len(self.items) + 1,
                titulo=Path(arquivo).stem,
                status=status,
                conteudo_id=conteudo_id,
                erro=erro,
            )
            self.items.append(item)
        else:
            item.status = status
            if conteudo_id is not None:
                item.conteudo_id = conteudo_id
            item.erro = erro
        self.save()

    def ensure_plano(self, plano: list) -> None:
        """Garante que cada aula do plano atual existe no estado (não apaga as antigas)."""
        from aula_uploader.plan import Acao, PlanoItem

        mudou = False
        for item in plano:
            if not isinstance(item, PlanoItem):
                continue
            nome = item.aula.path.name
            existing = self.find_item(nome)
            if existing is None:
                self.items.append(
                    ItemState(
                        arquivo=nome,
                        ordem=item.aula.ordem,
                        titulo=item.aula.titulo,
                        status="skipped" if item.acao == Acao.PULAR else "pending",
                        conteudo_id=item.existente_id,
                    )
                )
                mudou = True
            elif item.existente_id and not existing.conteudo_id:
                existing.conteudo_id = int(item.existente_id)
                mudou = True
        if mudou:
            self.save()
