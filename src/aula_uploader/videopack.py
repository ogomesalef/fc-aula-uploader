"""Ponte para o conversor de vídeo em Go (``tools/videopack``).

O portal recusa arquivos grandes demais, então vídeos acima do limite precisam
ser recomprimidos antes do envio. O trabalho pesado fica no binário Go, que
roda vários ffmpeg em paralelo controlado e reporta progresso em NDJSON.

Se o Go não estiver instalado, tudo continua funcionando com um caminho de
reserva que chama o ffmpeg direto daqui, um arquivo de cada vez.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

# Limites de tamanho do portal. Alvo confortável e teto que nunca pode ser
# alcançado: acima disso o envio é recusado depois de uma subida longa.
ALVO_BYTES = 1024 * 1024 * 1024  # 1,00 GiB
TETO_BYTES = int(1.15 * 1024 * 1024 * 1024)  # 1,15 GiB
AVISO_BYTES = ALVO_BYTES  # a partir daqui a interface avisa e oferece converter

AUDIO_BPS = 192_000  # AAC stereo: bom pra aula, sem mexer no volume
SUFIXO_PADRAO = " (comprimido)"
JOBS_PADRAO = 1

_MIN_VIDEO_BPS = 900_000
_OVERHEAD = 0.985

EventCb = Callable[[dict], None]


class VideopackError(RuntimeError):
    """Falha ao preparar ou executar o conversor."""


@dataclass
class VideoInfo:
    arquivo: str
    nome: str = ""
    duracao: float = 0.0
    largura: int = 0
    altura: int = 0
    bitrate: int = 0
    codec: str = ""
    tamanho: int = 0
    erro: str = ""

    @property
    def resolucao(self) -> str:
        if self.largura and self.altura:
            return f"{self.largura}x{self.altura}"
        return ""


# --------------------------------------------------------------------------
# Localização e build do binário Go
# --------------------------------------------------------------------------


def _repo_go_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "tools" / "videopack"


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    path = Path(base) / "aula-uploader"
    path.mkdir(parents=True, exist_ok=True)
    return path


def go_disponivel() -> bool:
    return shutil.which("go") is not None


def ffmpeg_disponivel() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _hash_fontes(go_dir: Path) -> str:
    """Identidade do código Go, para recompilar só quando os fontes mudam."""
    h = hashlib.sha256()
    for arquivo in sorted(go_dir.glob("*.go")):
        if arquivo.name.endswith("_test.go"):
            continue
        h.update(arquivo.read_bytes())
    go_mod = go_dir / "go.mod"
    if go_mod.exists():
        h.update(go_mod.read_bytes())
    return h.hexdigest()[:16]


def binario_pronto() -> Path | None:
    """Binário já disponível, sem compilar nada."""
    do_env = os.environ.get("AULA_UPLOADER_VIDEOPACK")
    if do_env and Path(do_env).is_file():
        return Path(do_env)
    go_dir = _repo_go_dir()
    if go_dir.is_dir():
        alvo = cache_dir() / f"videopack-{_hash_fontes(go_dir)}"
        if alvo.is_file():
            return alvo
    no_path = shutil.which("videopack")
    return Path(no_path) if no_path else None


def garantir_binario(log: Callable[[str], None] | None = None) -> Path | None:
    """Devolve o binário, compilando na primeira vez. None se o Go não existir."""
    pronto = binario_pronto()
    if pronto is not None:
        return pronto
    go_dir = _repo_go_dir()
    if not go_dir.is_dir() or not go_disponivel():
        return None
    alvo = cache_dir() / f"videopack-{_hash_fontes(go_dir)}"
    if log:
        log("Preparando o conversor de vídeo (primeira vez, leva alguns segundos)…")
    go_bin = shutil.which("go")
    if go_bin is None:
        return None
    try:
        subprocess.run(  # noqa: S603 - argv fixo, sem shell
            [go_bin, "build", "-o", str(alvo), "."],
            cwd=str(go_dir),
            capture_output=True,
            text=True,
            timeout=300,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        detalhe = getattr(exc, "stderr", "") or str(exc)
        raise VideopackError(f"Não consegui compilar o conversor: {detalhe}") from exc
    alvo.chmod(0o700)
    # Versões antigas só ocupam espaço.
    for velho in cache_dir().glob("videopack-*"):
        if velho != alvo:
            velho.unlink(missing_ok=True)
    return alvo


# --------------------------------------------------------------------------
# Cálculo de bitrate (espelha bitrate.go, usado no caminho de reserva)
# --------------------------------------------------------------------------


def bitrate_para_alvo(
    alvo_bytes: int,
    duracao: float,
    audio_bps: int = AUDIO_BPS,
    origem_bps: int = 0,
) -> int:
    if duracao <= 0 or alvo_bytes <= 0:
        return _MIN_VIDEO_BPS
    total = int(alvo_bytes * 8 / duracao * _OVERHEAD)
    video = max(total - audio_bps, _MIN_VIDEO_BPS)
    if origem_bps > 0:
        video = min(video, origem_bps)
    return video


# --------------------------------------------------------------------------
# Probe
# --------------------------------------------------------------------------


def _probe_com_go(binario: Path, paths: list[Path]) -> list[VideoInfo]:
    proc = subprocess.run(  # noqa: S603 - argv fixo, sem shell
        [str(binario), "probe", *[str(p) for p in paths]],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    infos: list[VideoInfo] = []
    for linha in proc.stdout.splitlines():
        linha = linha.strip()
        if not linha:
            continue
        try:
            dados = json.loads(linha)
        except json.JSONDecodeError:
            continue
        infos.append(_info_de_dict(dados))
    return infos


def _info_de_dict(dados: dict) -> VideoInfo:
    return VideoInfo(
        arquivo=dados.get("arquivo", ""),
        nome=dados.get("nome", ""),
        duracao=float(dados.get("duracao") or 0),
        largura=int(dados.get("largura") or 0),
        altura=int(dados.get("altura") or 0),
        bitrate=int(dados.get("bitrate") or 0),
        codec=dados.get("codec", ""),
        tamanho=int(dados.get("tamanho") or 0),
        erro=dados.get("erro", ""),
    )


def _probe_com_ffprobe(path: Path) -> VideoInfo:
    info = VideoInfo(arquivo=str(path), nome=path.name)
    try:
        info.tamanho = path.stat().st_size
    except OSError:
        pass
    binario = shutil.which("ffprobe")
    if binario is None:
        info.erro = "ffprobe não encontrado"
        return info
    try:
        proc = subprocess.run(  # noqa: S603 - argv fixo, sem shell
            [
                binario,
                "-v",
                "error",
                "-show_entries",
                "stream=codec_name,codec_type,width,height:format=duration,size,bit_rate",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        dados = json.loads(proc.stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        info.erro = "não consegui ler o vídeo"
        return info
    for stream in dados.get("streams", []):
        if stream.get("codec_type") == "video" and not info.largura:
            info.largura = int(stream.get("width") or 0)
            info.altura = int(stream.get("height") or 0)
            info.codec = stream.get("codec_name", "")
    formato = dados.get("format", {})
    try:
        info.duracao = float(formato.get("duration") or 0)
        info.bitrate = int(float(formato.get("bit_rate") or 0))
    except (TypeError, ValueError):
        pass
    return info


def probe_muitos(paths: Iterable[Path]) -> dict[str, VideoInfo]:
    """Metadados de vários vídeos, indexados pelo caminho absoluto."""
    lista = [Path(p) for p in paths]
    if not lista:
        return {}
    binario = binario_pronto()
    infos: list[VideoInfo]
    if binario is not None:
        infos = _probe_com_go(binario, lista)
        if len(infos) == len(lista):
            return {info.arquivo: info for info in infos}
    infos = [_probe_com_ffprobe(p) for p in lista]
    return {info.arquivo: info for info in infos}


# --------------------------------------------------------------------------
# Conversão
# --------------------------------------------------------------------------


def caminho_de_saida(entrada: Path, sufixo: str = SUFIXO_PADRAO) -> Path:
    return entrada.parent / f"{entrada.stem}{sufixo}.mp4"


@dataclass
class Conversao:
    """Um lote de conversão. ``executar`` bloqueia; ``cancelar`` interrompe."""

    paths: list[Path]
    alvo_bytes: int = ALVO_BYTES
    teto_bytes: int = TETO_BYTES
    jobs: int = JOBS_PADRAO
    engine: str = "hw"
    sufixo: str = SUFIXO_PADRAO
    _proc: subprocess.Popen | None = field(default=None, init=False, repr=False)
    _cancelado: threading.Event = field(default_factory=threading.Event, init=False, repr=False)

    def cancelar(self) -> None:
        self._cancelado.set()
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.send_signal(signal.SIGTERM)
            except OSError:
                pass

    @property
    def cancelado(self) -> bool:
        return self._cancelado.is_set()

    def executar(self, on_event: EventCb, log: Callable[[str], None] | None = None) -> None:
        if not ffmpeg_disponivel():
            raise VideopackError(
                "Preciso do ffmpeg para converter vídeos. Instale com: brew install ffmpeg"
            )
        binario = garantir_binario(log=log)
        if binario is not None:
            self._executar_go(binario, on_event)
        else:
            if log:
                log("Go não encontrado: convertendo com ffmpeg, um vídeo por vez.")
            self._executar_ffmpeg(on_event)

    # -- caminho principal (Go) --------------------------------------------

    def _executar_go(self, binario: Path, on_event: EventCb) -> None:
        argv = [
            str(binario),
            "pack",
            "--target-bytes",
            str(self.alvo_bytes),
            "--max-bytes",
            str(self.teto_bytes),
            "--jobs",
            str(self.jobs),
            "--engine",
            self.engine,
            "--out-suffix",
            self.sufixo,
            "--audio-bps",
            str(AUDIO_BPS),
            *[str(p) for p in self.paths],
        ]
        proc = subprocess.Popen(  # noqa: S603 - argv fixo, sem shell
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._proc = proc
        assert proc.stdout is not None
        stderr_buf: list[str] = []

        def _drena_stderr() -> None:
            if proc.stderr is None:
                return
            stderr_buf.append(proc.stderr.read() or "")

        dreno = threading.Thread(target=_drena_stderr, daemon=True, name="videopack-stderr")
        dreno.start()
        for linha in proc.stdout:
            linha = linha.strip()
            if not linha:
                continue
            try:
                on_event(json.loads(linha))
            except json.JSONDecodeError:
                continue
        proc.wait()
        dreno.join(timeout=2)
        erro = "".join(stderr_buf).strip()
        if proc.returncode not in (0, 1) and not self.cancelado:
            raise VideopackError(erro or "o conversor terminou com erro")

    # -- caminho de reserva (ffmpeg direto) --------------------------------

    def _executar_ffmpeg(self, on_event: EventCb) -> None:
        on_event({"tipo": "inicio", "total": len(self.paths)})
        for path in self.paths:
            if self.cancelado:
                break
            info = _probe_com_ffprobe(path)
            if info.erro or info.duracao <= 0:
                on_event(
                    {
                        "tipo": "erro",
                        "arquivo": str(path),
                        "nome": path.name,
                        "erro": info.erro or "não consegui descobrir a duração do vídeo",
                    }
                )
                continue
            saida = caminho_de_saida(path, self.sufixo)
            on_event(
                {
                    "tipo": "arquivo-inicio",
                    "arquivo": str(path),
                    "nome": path.name,
                    "saida": str(saida),
                    "tamanho_antes": info.tamanho,
                    "largura": info.largura,
                    "altura": info.altura,
                    "duracao": info.duracao,
                }
            )
            try:
                self._encodar_um(info, saida, on_event)
            except VideopackError as exc:
                on_event(
                    {"tipo": "erro", "arquivo": str(path), "nome": path.name, "erro": str(exc)}
                )
        if self.cancelado:
            on_event({"tipo": "cancelado"})
        else:
            on_event({"tipo": "lote-fim", "total": len(self.paths)})

    def _encodar_um(self, info: VideoInfo, saida: Path, on_event: EventCb) -> None:
        bps = bitrate_para_alvo(self.alvo_bytes, info.duracao, AUDIO_BPS, info.bitrate)
        for tentativa in range(1, 4):
            self._rodar_ffmpeg(info, saida, bps, tentativa, on_event)
            if self.cancelado:
                return
            tamanho = saida.stat().st_size if saida.exists() else 0
            if tamanho and tamanho <= self.teto_bytes:
                largura, altura = info.largura, info.altura
                if largura > 1920:
                    altura = altura * 1920 // largura
                    largura = 1920
                on_event(
                    {
                        "tipo": "fim",
                        "arquivo": info.arquivo,
                        "nome": info.nome,
                        "saida": str(saida),
                        "tamanho": tamanho,
                        "tamanho_antes": info.tamanho,
                        "tentativa": tentativa,
                        "largura": largura,
                        "altura": altura,
                        "duracao": info.duracao,
                    }
                )
                return
            bps = max(int(bps * (self.alvo_bytes / max(tamanho, 1)) * 0.96), _MIN_VIDEO_BPS // 2)
        raise VideopackError("não consegui deixar o arquivo abaixo do limite")

    def _rodar_ffmpeg(
        self, info: VideoInfo, saida: Path, bps: int, tentativa: int, on_event: EventCb
    ) -> None:
        binario = shutil.which("ffmpeg")
        if binario is None:
            raise VideopackError("ffmpeg não encontrado")
        codec = "libx264" if self.engine == "x264" else "h264_videotoolbox"
        argv = [
            binario, "-y", "-nostdin", "-hide_banner",
            "-i", info.arquivo,
            "-map", "0:v:0", "-map", "0:a?",
            "-c:v", codec,
            "-b:v", str(bps),
            "-maxrate", str(int(bps * 1.35)),
            "-bufsize", str(bps * 2),
            "-vf", "scale=w='min(1920,iw)':h=-2",
            "-pix_fmt", "yuv420p",
            # Áudio: AAC 192k stereo. Sem filtro de volume — mantém o nível original.
            "-c:a", "aac", "-b:a", str(AUDIO_BPS), "-ac", "2", "-ar", "48000",
            "-af", "aresample=async=1:first_pts=0",
            "-movflags", "+faststart",
            "-progress", "pipe:1", "-nostats",
            str(saida),
        ]
        proc = subprocess.Popen(  # noqa: S603 - argv fixo, sem shell
            argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1
        )
        self._proc = proc
        assert proc.stdout is not None
        segundos = 0.0
        for linha in proc.stdout:
            chave, _, valor = linha.strip().partition("=")
            if chave in {"out_time_us", "out_time_ms"}:
                try:
                    segundos = int(valor) / 1_000_000
                except ValueError:
                    pass
            elif chave == "progress":
                pct = min(segundos / info.duracao * 100, 99.9) if info.duracao else 0
                on_event(
                    {
                        "tipo": "progresso",
                        "arquivo": info.arquivo,
                        "nome": info.nome,
                        "pct": pct,
                        "tentativa": tentativa,
                    }
                )
        proc.wait()
        if proc.returncode != 0 and not self.cancelado:
            saida.unlink(missing_ok=True)
            raise VideopackError("o ffmpeg falhou ao converter este arquivo")


def precisa_converter(tamanho: int) -> bool:
    return tamanho > AVISO_BYTES


def acima_do_teto(tamanho: int) -> bool:
    return tamanho >= TETO_BYTES
