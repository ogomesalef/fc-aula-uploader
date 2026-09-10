# Interface web

A interface web roda **somente em `127.0.0.1`** — ninguém na rede consegue abrir. É a forma recomendada de enviar aulas: arrastar pastas, revisar títulos, comprimir vídeos grandes e acompanhar o progresso.

## Subir o servidor

Na pasta do projeto (com o venv ativado):

```bash
aula-uploader web
```

De **qualquer pasta**, usando o caminho completo do venv:

```bash
/Users/SEU_USUARIO/caminho/fc-aula-uploader/.venv/bin/aula-uploader web
```

Abre automaticamente: **http://127.0.0.1:8787/**

Opções:

```bash
aula-uploader web --port 8787          # porta (padrão 8787)
aula-uploader web --no-browser         # não abrir o navegador
```

Só uma instância por vez. Se aparecer “Já existe uma interface…”, feche a outra (Ctrl+C no terminal dela).

## Fluxo recomendado

1. **Portal** — Full Cycle ou DevOps Pro e login (marque *Salvar sessão* se quiser persistir neste Mac).
2. **Produto** — filtre por MBA, Pós, Curso Full Cycle, etc.
3. **Curso** — busque por nome ou ID; cursos ainda não mapeados podem ser vinculados na hora.
4. **Capítulo** — escolha um existente ou crie um novo (nome, ordem, URL da pasta Bunny).
5. **Vídeos** — arraste pasta, `.zip` ou arquivos; edite ordem e título; comprima se passar de 1 GB.
6. **Enviar** — acompanhe cada aula na tabela e a faixa **Projetos**.

### Status de cada aula

| Status | Significado |
|--------|-------------|
| enviando | Upload do arquivo (S3) |
| salvando no portal | Gravando a URL no conteúdo do admin |
| no Nivo | Encode; aguardando o link da aula |
| pronta | Pronto para play no portal |

Enquanto o Nivo processa, a interface libera para montar outro envio. Use **Projetos** (Em andamento / Concluídos / Histórico) para abrir, arquivar ou excluir do app (nunca apaga no portal).

## Vídeos grandes e compressão

| Limite | Valor |
|--------|-------|
| Alvo confortável | 1,00 GB |
| Aviso na tabela | acima de 1,00 GB |
| Teto rígido (portal recusa) | 1,15 GB |

- Vídeos acima de 1 GB aparecem marcados com botão **Comprimir**.
- Acima do teto, o botão **Enviar** fica bloqueado até comprimir.
- A compressão mantém **1080p** (reduz 4K/1440p; nunca amplia 720p).
- Roda **em segundo plano**: se a interface cair, o ffmpeg/worker continua. Ao reabrir, o app mostra o progresso.

O conversor (`tools/videopack`, Go + ffmpeg) compila na primeira vez em `~/.cache/aula-uploader/`.

## O que fica salvo localmente

| Item | Onde |
|------|------|
| Sessão do portal (se marcar *Salvar sessão*) | `~/.config/aula-uploader/*.session.json` |
| Catálogo de cursos | `~/.config/aula-uploader/catalog.json` |
| Progresso de upload / Nivo | `~/.config/aula-uploader/state/` e `~/.cache/aula-uploader/upload-*.json` |
| Binário videopack | `~/.cache/aula-uploader/videopack-*` |
| Compressão em andamento | `~/.cache/aula-uploader/convert-job.json` |

Nada disso vai para o Git.
