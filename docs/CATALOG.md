# Catálogo de cursos, produtos e capítulos

O **aula-uploader** mantém um catálogo local para acelerar a escolha de curso, produto e capítulo na interface web e no assistente do terminal.

Esse catálogo guarda **somente metadados públicos de navegação** (IDs numéricos, nomes e ordem). Não inclui credenciais, cookies, URLs da Bunny com token, nem conteúdo dos vídeos.

## O que vem no repositório

O arquivo [`src/aula_uploader/data/seed_catalog.json`](../src/aula_uploader/data/seed_catalog.json) é a **semente inicial** enviada no Git. Ele já traz:

### Produtos

| ID | Nome | Portal |
|----|------|--------|
| `mba-eng-ia` | MBA em Engenharia de Software com IA | Full Cycle |
| `mba-arq` | MBA em Arquitetura Full Cycle | Full Cycle |
| `pos-techlead` | Pós-Graduação em Liderança Técnica | Full Cycle |
| `pos-go` | Pós-Graduação GoExpert | Full Cycle |
| `pos-aiops` | Pós-Graduação AIOps e IA na Engenharia de Cloud | DevOps Pro |
| `curso-full-cycle` | Curso Full Cycle | Full Cycle |
| `curso-goexpert` | Curso GoExpert | Full Cycle |
| `curso-devops` | Curso DevOps | DevOps Pro |

Os três últimos (`curso-full-cycle`, `curso-goexpert`, `curso-devops`) são **filtros de conveniência**: eles reagrupam cursos que já estão vinculados aos produtos acima, para facilitar a busca na interface.

### Cursos mapeados (com capítulos)

Dois cursos já vêm com capítulos completos no seed:

| ID | Curso | Capítulos |
|----|-------|-----------|
| **291** | Arquitetura na Era da IA | 9 capítulos (Introdução … Segurança) |
| **296** | Protocolos de Comunicação | 8 capítulos (Boas-vindas … Arquiteturas Multi-Agente) |

Além deles, o seed lista dezenas de outros cursos (Go, DDD, Kafka, Prompt Engineering, etc.) com vínculo a produto, **sem** capítulos pré-carregados — os capítulos aparecem depois que você sincroniza ou sobe a primeira aula.

## Onde fica no seu computador

Depois do primeiro uso, o catálogo **vivo** fica em:

```
~/.config/aula-uploader/catalog.json
```

Permissões restritas (`0600`). Esse arquivo **nunca** deve ir para o Git.

O seed do repositório é mesclado com o local: se você já mapeou algo na máquina, o local prevalece nos nomes e capítulos; os vínculos de produto do seed são preservados.

## Como o catálogo cresce

1. **Clone/pull** — você já recebe produtos, cursos e os capítulos dos cursos 291 e 296.
2. **Busca no portal** — na interface web, digite o ID ou o nome de um curso que ainda não está listado; a ferramenta consulta o portal e pergunta a quais produtos vincular.
3. **Depois do upload** — ao enviar aulas com sucesso, curso e capítulo entram no catálogo local automaticamente.
4. **Sincronização** — ao escolher um curso, a lista de capítulos pode ser atualizada a partir do portal.

## Contribuir com mapeamentos (pull request)

Se você mapeou um curso novo e quer compartilhar com o time **sem expor dados sensíveis**, edite apenas `seed_catalog.json`:

```json
{
  "id": 297,
  "nome": "Nome do curso",
  "produto_ids": ["mba-eng-ia"],
  "chapters": [
    { "id": 2300, "nome": "Introdução", "ordem": 1 }
  ]
}
```

**Pode incluir:** IDs numéricos do portal, nomes de curso/capítulo/produto, ordem dos capítulos.

**Não inclua:** senhas, cookies, URLs assinadas, IDs de pasta Bunny, tokens, e-mails de admin ou caminhos locais.

Antes de abrir o PR, rode:

```bash
python scripts/secret_scan.py
pytest -q tests/test_catalog.py
```

## Segurança

- O catálogo é **metadado de navegação**, não credencial.
- IDs de curso/capítulo são visíveis no admin do portal para quem tem acesso — o mesmo nível de exposição de um bookmark interno.
- Credenciais ficam em `.env` (gitignored) ou digitadas no login; sessões em `~/.config/aula-uploader/*.session.json` (gitignored).

Mais detalhes em [SECURITY.md](../SECURITY.md).
