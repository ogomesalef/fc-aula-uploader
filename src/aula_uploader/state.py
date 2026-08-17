"""Estado local para retomar uploads."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from aula_uploader.session import state_dir


@dataclass
class ItemState:
    arquivo: str
    ordem: int
    titulo: str
    status: str = "pending"  # pending | done | skipped | failed
    conteudo_id: int | None = None
    erro: str = ""


@dataclass
class UploadState:
    portal: str
    capitulo_id: int
    pasta: str
    status_criacao: str = "0"
    force: bool = False
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    items: list[ItemState] = field(default_factory=list)

    @property
    def path(self) -> Path:
        return state_dir() / f"upload-{self.portal}-{self.capitulo_id}.json"

    def save(self) -> Path:
        self.updated_at = time.time()
        payload = {
            "portal": self.portal,
            "capitulo_id": self.capitulo_id,
            "pasta": self.pasta,
            "status_criacao": self.status_criacao,
            "force": self.force,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "items": [asdict(i) for i in self.items],
        }
        path = self.path
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
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
            status_criacao=data.get("status_criacao", "0"),
            force=bool(data.get("force", False)),
            created_at=float(data.get("created_at", time.time())),
            updated_at=float(data.get("updated_at", time.time())),
            items=items,
        )

    def mark(self, arquivo: str, status: str, *, conteudo_id: int | None = None, erro: str = "") -> None:
        for item in self.items:
            if item.arquivo == arquivo:
                item.status = status
                item.conteudo_id = conteudo_id
                item.erro = erro
                break
        self.save()
