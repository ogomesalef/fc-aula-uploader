# Segurança

## Uso responsável

- Use **suas** credenciais de admin do portal. Nunca compartilhe `.env` ou arquivos de sessão.
- Esta CLI automatiza ações que você já pode fazer no admin; quem tiver suas credenciais terá o mesmo poder.
- Confirmação explícita antes de qualquer envio, exceto quando você passa `--yes`.

## Credenciais e sessão

- A senha é lida do `.env` ou digitada no prompt (sem eco) e nunca é gravada em disco pela ferramenta.
- Nenhum comando faz login silencioso com o `.env`: o assistente pergunta, e `plan`/`upload`/`resume`
  exigem a flag explícita `--use-env`.
- A sessão **não** é persistida por padrão (a pergunta vem com "não" pré-selecionado).
  Se você optar por salvar, vai para
  `~/.config/aula-uploader/` com permissão `0600` e o diretório com `0700`.
- Só cookies de sessão do próprio host do portal são gravados — cookies de analytics ou de
  outros domínios são descartados.
- `aula-uploader logout` apaga todas as sessões salvas.
- A sincronização do catálogo roda em background reaproveitando os cookies, nunca a senha.

## Rede

- Só os hosts oficiais definidos em `ALLOWED_HOSTS` são aceitos, sempre em `https`.
- Redirects são seguidos manualmente e apenas dentro do host do portal; qualquer salto para
  outro domínio é recusado, então o cookie de sessão não vaza. Há limite de redirects.
- A verificação de sessão só aceita resposta `200` ou redirect que continue sob `/admin` no
  próprio portal; qualquer outra coisa conta como "não autenticado".
- Mensagens de erro trazem só o status HTTP: o corpo do admin pode conter tokens CSRF.
- Logs e o arquivo de estado mascaram URLs assinadas (query string) e possíveis Access Key IDs,
  inclusive quando a URL aparece dentro do texto de uma exceção.

## Arquivos locais

- ZIPs são extraídos com verificação de caminho (`zip slip`) e entradas de symlink são recusadas.
- Estado de upload e catálogo são gravados de forma atômica, com permissão `0600`.
- Os arquivos de origem nunca são modificados nem movidos.

## Proteção contra engano

- Títulos repetidos no mesmo lote são sinalizados antes do upload, porque o portal trataria os
  dois arquivos como a mesma aula e um vídeo sobrescreveria o outro. A confirmação, nesse caso,
  vem com "não" pré-selecionado.
- No lote, capítulos que já existem no curso são reaproveitados e a ferramenta avisa que a pasta
  Bunny digitada não será aplicada.

## Verificações automáticas

O CI roda `ruff check` (com as regras de segurança do bandit), a suíte de testes — incluindo zip
slip, symlink em ZIP, redirect para host externo, persistência de cookies e mascaramento de URL —
e um scanner de segredos em cada push e pull request. O scanner procura chaves AWS, cookies de
sessão e atribuições literais de senha/token, ignorando placeholders.

## Reportar um problema

Abra um issue privado ou contate o mantenedor. Não inclua credenciais no relato.
