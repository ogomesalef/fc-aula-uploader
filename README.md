# aula-uploader

CLI interativa para **criar capítulos**, **normalizar nomes de aulas** e **enviar vídeos** a um portal administrativo.

A pasta de vídeo no Bunny precisa existir antes. O capítulo pode ser criado pela ferramenta ou reutilizado se já estiver no curso.

## Requisitos

- Python 3.10+ (recomendado 3.12)
- Usuário e senha de admin do portal
- Opcional: `ffprobe` (duração dos vídeos) e [Ollama](https://ollama.com) (sugestão local de títulos)

## Instalação

```bash
git clone https://github.com/ogomesalef/fc-aula-uploader.git
cd fc-aula-uploader
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Primeira execução

```bash
aula-uploader
```

Na primeira vez o assistente pede:

1. Qual portal usar
2. E-mail (usuário do admin)
3. Senha (não é exibida)

Você pode copiar `.env.example` para `.env` com usuário e senha. O assistente **não** faz login silencioso: pergunta se quer digitar agora ou usar o `.env`.

Sessão opcional fica só nesta máquina (`~/.config/aula-uploader/`). Para apagar:

```bash
aula-uploader logout
```

## O que o assistente faz

1. **Portal e login**
2. **Destino**
   - criar um capítulo (nome, ordem, URL da pasta Bunny)
   - usar um capítulo existente (link da lista de aulas)
   - usar um capítulo já mapeado neste computador (atualiza a lista no portal ao abrir o curso)
   - **lote:** vários capítulos de uma vez
3. **Vídeos** — pasta ou `.zip` (o original não é alterado)
4. **Nomes** — normalização local, Ollama ou edição manual; sempre dá para revisar
5. **Plano** — criar, enviar vídeo ou pular (aula já existe com vídeo)
6. **Upload** — progresso por aula; no fim, link do admin para conferir e menu para enviar mais ou encerrar

Aulas novas nascem como **rascunho**, salvo se você escolher publicar.

### Lote (vários capítulos)

1. Confirme o curso
2. Monte a lista: nome, ordem e URL Bunny de cada capítulo (Bunny não pode se repetir)
3. Revise
4. Capítulos com o **mesmo nome** no curso não são recriados — só entram os vídeos que ainda não estão lá
5. Vincule **à mão** a pasta/ZIP de cada capítulo
6. Revise nomes por capítulo
7. Uma escolha publicar/rascunho vale para o lote; o envio segue um capítulo por vez

## Formato dos vídeos

Extensões: `.mp4`, `.mov`, `.mkv`, `.avi`, `.m4v`, `.webm`.

O nome do arquivo sugere ordem e título (você confirma depois):

| Arquivo | Ordem | Título |
|---------|-------|--------|
| `9-segurança.mp4` | 9 | Segurança |
| `01 - Introdução.mp4` | 1 | Introdução |
| `9.1-O problema de segurança.mp4` | 1 | O Problema de Segurança |

Links úteis no admin:

- Curso (lista de capítulos): `.../admin/curso/capitulo/<ID>/curso`
- Capítulo (lista de aulas): `.../admin/curso/conteudo/<ID>/capitulo`

## Outros comandos

```bash
aula-uploader doctor
aula-uploader assistente
aula-uploader plan --portal <id> --capitulo 299 --fonte ./videos
aula-uploader upload --portal <id> --capitulo 299 --fonte ./aulas.zip
aula-uploader resume --portal <id> --capitulo 299
```

Os IDs de `--portal` aparecem em `aula-uploader --help`. Sem subcomando, abre o assistente.

## Segurança

- Credenciais e cookies **não** vão para o Git. Não faça commit de `.env`.
- Logs mascaram URLs assinadas e possíveis Access Keys.
- Só os hosts oficiais do portal são aceitos.
- Confirmação antes de enviar (exceto `--yes` na CLI).

## Licença

MIT
