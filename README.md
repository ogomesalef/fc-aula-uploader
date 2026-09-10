# aula-uploader

Ferramenta local para **criar capítulos**, **organizar vídeos** e **enviar aulas** aos portais Full Cycle e DevOps Pro.

O jeito recomendado de usar é a **interface web** (`aula-uploader web`): roda só neste computador (`127.0.0.1`), com login, escolha de curso/capítulo, compressão de vídeos grandes e acompanhamento do envio.

A pasta de vídeo no Bunny precisa existir antes. O capítulo pode ser criado pela ferramenta ou reutilizado se já estiver no curso.

## Requisitos

- macOS (testado) ou Linux
- Python 3.10+ (recomendado 3.12)
- Usuário e senha de **admin** do portal
- `ffmpeg` e `ffprobe` (`brew install ffmpeg`)
- Opcional: Go 1.22+ — compressão em paralelo; sem Go, usa ffmpeg um arquivo por vez

## Instalação rápida

```bash
git clone https://github.com/ogomesalef/fc-aula-uploader.git
cd fc-aula-uploader
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
brew install ffmpeg   # se ainda não tiver
```

Checagem:

```bash
aula-uploader doctor
```

## Subir a interface (uso do dia a dia)

```bash
aula-uploader web
```

Abre **http://127.0.0.1:8787/**

Só aceita acesso local. Se a aba não abrir sozinha:

```bash
aula-uploader web --no-browser
# depois abra http://127.0.0.1:8787/ no navegador
```

### Fluxo na web

1. **Portal** — Full Cycle ou DevOps Pro → login (marque *Salvar sessão* se quiser manter neste Mac)
2. **Produto / curso / capítulo** — filtre, busque ou crie capítulo (nome, ordem, URL da pasta Bunny)
3. **Vídeos** — arraste pasta, `.zip` ou arquivos; ajuste ordem e título
4. **Comprimir** (se precisar) — vídeos grandes demais para o portal
5. **Enviar** — acompanhe o status de cada aula

### Status do envio (o que cada etapa significa)

| Status na tela | O que está acontecendo |
|----------------|------------------------|
| **enviando** | Arquivo subindo (chunks → S3) |
| **salvando no portal** | URL sendo gravada no conteúdo do admin |
| **no Nivo** | Encode no Nivo; aguardando o link da aula |
| **pronta** | Link ok — aula utilizável no portal |

Salvar a URL S3 no conteúdo **já dispara** o Nivo. Não é um upload separado.

Enquanto só o Nivo processa, dá para preparar outro envio / outro capítulo. O progresso fica na faixa **Projetos** (Em andamento · Concluídos · Histórico).

Detalhes da web: [docs/WEB.md](docs/WEB.md).

## Vídeos grandes

| Limite | Valor |
|--------|-------|
| Alvo | 1,00 GB |
| Teto (portal recusa) | 1,15 GB |

- Acima de 1 GB: aviso + botão **Comprimir**
- Acima do teto: **Enviar** só libera depois de comprimir
- Compressão em 1080p (reduz 4K/1440p; não amplia 720p)
- Roda em segundo plano; se a interface cair, o worker continua

O conversor fica em `tools/videopack` (Go + ffmpeg) e compila na primeira vez em `~/.cache/aula-uploader/`.

## Formato dos arquivos

Extensões: `.mp4`, `.mov`, `.mkv`, `.avi`, `.m4v`, `.webm`.

O nome do arquivo sugere ordem e título (você confirma na tela):

| Arquivo | Ordem | Título |
|---------|-------|--------|
| `9-segurança.mp4` | 9 | Segurança |
| `01 - Introdução.mp4` | 1 | Introdução |

Aulas novas nascem como **rascunho**, salvo se marcar *Publicar agora*.

## Atualizar

```bash
cd fc-aula-uploader
git pull
source .venv/bin/activate
pip install -e .
```

## Assistente no terminal (opcional)

```bash
aula-uploader              # ou: aula-uploader assistente
aula-uploader logout       # apaga sessões salvas neste Mac
aula-uploader resume --portal fullcycle --capitulo 299
```

Credenciais opcionais em `.env` (copie de `.env.example`). Não faça commit do `.env`.

## Segurança

- Credenciais e cookies **não** vão para o Git
- Interface web só em `127.0.0.1`
- Só hosts oficiais do portal, sempre HTTPS
- Detalhes em [SECURITY.md](SECURITY.md)

## Desenvolvimento

```bash
pip install -e ".[dev]"
ruff check .
pytest -q
(cd tools/videopack && go test ./...)
```

## Licença

MIT
