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

Salvar a sessão é opcional e o padrão é **não salvar**. Se você aceitar, ela fica só nesta máquina (`~/.config/aula-uploader/`). Para apagar:

```bash
aula-uploader logout
```

Ao abrir o assistente (e no `doctor`), se o GitHub estiver à frente do clone, aparece um aviso. Não precisa avisar ninguém: a pessoa atualiza com:

```bash
git pull && pip install -e .
```

A checagem usa a rede uma vez a cada algumas horas e falha em silêncio se estiver offline.

## O que o assistente faz

1. **Portal e login**
2. **Destino**
   - criar um capítulo (nome, ordem, URL da pasta Bunny) — o curso pode ser escolhido na lista mapeada ou buscado por trecho do nome no portal
   - usar um capítulo existente (link da lista de aulas)
   - usar um capítulo já mapeado (lista com setas; digite para filtrar por trecho do nome, com ou sem acento). Já vêm *Arquitetura na Era da IA* e *Protocolos de Comunicação*. Um curso novo só entra nesta lista depois que você sobe uma aula nele
   - **lote:** vários capítulos de uma vez
3. **Vídeos** — pasta ou `.zip` (o original não é alterado)
4. **Nomes** — normalização local, Ollama ou edição manual; sempre dá para revisar
5. **Plano** — criar, enviar vídeo ou pular (aula já existe com vídeo); títulos repetidos no lote viram aviso antes do envio
6. **Upload** — progresso por aula; no fim, link do admin para conferir e menu para enviar mais ou encerrar

Aulas novas nascem como **rascunho**, salvo se você escolher publicar.

### Lote (vários capítulos)

1. Confirme o curso
2. Monte a lista: nome, ordem e URL Bunny de cada capítulo (Bunny não pode se repetir)
3. Revise
4. Capítulos com o **mesmo nome** no curso não são recriados — só entram os vídeos que ainda não estão lá (a Bunny digitada não é aplicada nesse caso, e a ferramenta avisa)
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
aula-uploader plan --portal 1 --capitulo 299 --fonte ./videos
aula-uploader upload --portal 1 --capitulo 299 --fonte ./aulas.zip --use-env
aula-uploader resume --portal 1 --capitulo 299 --use-env
```

Sem subcomando, abre o assistente. Em `--portal` valem `1`/`2` ou os slugs (`fullcycle`, `devops`); `aula-uploader --help` mostra o mapeamento.

Por padrão esses comandos **pedem usuário e senha no terminal**, mesmo com `.env` presente. Passe `--use-env` para autorizar o login a partir do `.env` — útil em script, explícito no histórico.

`resume` retoma o que ficou pendente ou falhou. Se a origem era um `.zip`, ele reabre o arquivo original — o diretório temporário da execução anterior não é necessário.

## Segurança

- Credenciais e cookies **não** vão para o Git. Não faça commit de `.env`.
- Logs mascaram URLs assinadas e possíveis Access Keys.
- Só os hosts oficiais do portal são aceitos, sempre por HTTPS.
- Redirects para fora do host do portal são bloqueados, então o cookie de sessão nunca sai dele.
- ZIPs são extraídos com verificação de caminho (`zip slip`) e recusam entradas de symlink.
- Confirmação antes de enviar (exceto `--yes` na CLI).

Detalhes em [SECURITY.md](SECURITY.md).

## Desenvolvimento

```bash
pip install -e ".[dev]"
ruff check .
pytest -q
python scripts/secret_scan.py
```

## Licença

MIT
