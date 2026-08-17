# aula-uploader

CLI para **criar aulas e enviar vídeos** para um **capítulo já criado** no portal administrativo da Full Cycle ou DevOps Pro.

## O que faz

1. Você cria o capítulo no portal (e a pasta no Bunny).
2. Cole o link do capítulo + a pasta (ou `.zip`) com os vídeos.
3. A ferramenta normaliza os nomes, mostra o plano e, após confirmação, sobe as aulas.

**Não faz (ainda):** criar capítulo, criar recurso no Bunny, subir vários capítulos de um curso.

## Requisitos

- Python 3.10+
- Acesso admin ao portal (mesmo usuário/senha do login em `portal.fullcycle.com.br` ou `portal.devopspro.com.br`)
- Opcional: `ffprobe` (duração dos vídeos) e [Ollama](https://ollama.com) (sugestão de nomes)

## Instalação

```bash
git clone https://github.com/ogomesalef/fc-aula-uploader.git
cd fc-aula-uploader
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Credenciais

Use o **mesmo usuário e senha** do portal administrativo.

```bash
cp .env.example .env
# edite PORTAL_USERNAME e PORTAL_PASSWORD
```

Ou deixe o `.env` vazio: o assistente pede usuário e senha no terminal (a senha não é exibida).

Sessões ficam só na sua máquina (`~/.config/aula-uploader/`), com permissão restrita. Para apagar:

```bash
aula-uploader logout
```

## Formato dos vídeos

Aceita pasta **ou** `.zip` (é descompactado em pasta temporária).

Extensões: `.mp4`, `.mov`, `.mkv`, `.avi`, `.m4v`, `.webm`.

O nome do arquivo define ordem e título:

| Arquivo | Ordem | Título |
|---------|-------|--------|
| `9-segurança.mp4` | 9 | Segurança |
| `01 - Introdução.mp4` | 1 | Introdução |
| `02_basico_03_docker_k8s_ed.mp4` | 3 | Docker K8s |

Você pode revisar/editar cada nome no assistente (setas + Enter). Se tiver Ollama, pode pedir sugestões e confirmar o lote.

## Uso

```bash
aula-uploader doctor          # checa ambiente
aula-uploader                 # assistente interativo
aula-uploader assistente
```

No assistente:

1. Escolha o portal (Full Cycle ou DevOps Pro)
2. Cole o link do capítulo: `.../admin/curso/conteudo/<ID>/capitulo`
3. Informe a pasta ou o `.zip` (pode arrastar no terminal)
4. Revise os nomes → confirme o destino → confirme o upload

Aulas novas são criadas como **Rascunho** por padrão.

### Linha de comando

```bash
aula-uploader plan --portal fullcycle --capitulo 299 --fonte ./videos
aula-uploader upload --portal fullcycle --capitulo 299 --fonte ./aulas.zip
aula-uploader resume --portal fullcycle --capitulo 299
```

## Segurança

- Credenciais e cookies **não** vão para o GitHub.
- Logs mascaram URLs S3 / possíveis Access Keys.
- Só aceita os domínios oficiais dos portais.
- Confirmação obrigatória antes de enviar (exceto `--yes`).

## Licença

MIT
