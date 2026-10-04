# Política de segurança

## Como reportar uma vulnerabilidade

Use o **relato privado de vulnerabilidades** do GitHub: aba **Security → Report a vulnerability** deste
repositório. Não abra uma *issue* pública com detalhes de uma falha.

Este é um projeto pessoal, mantido sem prazo garantido, mas relatos de segurança são lidos com prioridade.

## O que o app faz (e o que ele se recusa a fazer)

O Baixe Livros consulta as APIs do Internet Archive e do Project Gutenberg e salva um arquivo no seu computador.
Como ele baixa arquivos de terceiros, as regras abaixo são intencionais e cobertas por testes:

- **Só domínios permitidos e só HTTPS:** `archive.org`, `gutenberg.org` e `gutendex.com` (e seus subdomínios).
  Qualquer outro endereço é recusado, inclusive `archive.org.evil.com`, `file:`, `javascript:` e `http:`.
- **Redirecionamentos são conferidos:** um redirecionamento para fora dos domínios permitidos é recusado antes
  de qualquer conexão.
- **Só tipos de livro** (`.pdf`, `.epub`, `.mobi`, `.azw`, `.azw3`, `.doc`, `.docx`, `.txt`, `.zip`, `.rar`) chegam
  à pasta final, e o conteúdo precisa bater com o tipo (PDF começa com `%PDF-`; EPUB/DOCX/ZIP com assinatura ZIP;
  RAR com `Rar!`). Executáveis e outros tipos são recusados e apagados.
- **Limites de tamanho:** respostas JSON até 5 MB e arquivos até 1 GiB.
- **Nada é executado:** "Abrir" só abre tipos de documento (nunca `.zip`/`.rar`/executáveis) e só dentro da
  pasta de livros, mesmo que o registro da biblioteca contenha um caminho diferente.
- **Consultas sem injeção:** o que você digita vira só letras e números minúsculos antes de ir às APIs ou ao
  SQLite, e os operadores `and`/`or`/`not` são descartados. As consultas SQL são parametrizadas.
- **Downloads isolados:** os parciais ficam em `.baixando/` (hash do endereço no nome) e só viram livro depois de
  validados. A retomada confere o `ETag`/`Last-Modified` (`If-Range`); se o arquivo mudou no servidor, recomeça.
- **Nomes de arquivo seguros:** vêm do título, sem caracteres inválidos, sem `../` e sem nomes reservados do
  Windows (`CON`, `NUL`…). Nada é sobrescrito.
- **Sem credenciais, contas ou dados pessoais:** o app não pede login e só envia às fontes o texto da sua busca
  e um `User-Agent` identificando o programa. O registro da biblioteca fica só no seu computador.

## Boas práticas ao usar

- Gere o `.exe` você mesmo a partir deste código (veja o `README.md`) ou use uma Release deste repositório e
  confira o SHA-256 publicado. Não confie em `.exe` de origem desconhecida.
- As versões das ferramentas ficam fixadas em `requirements-dev.txt`, e as ações do GitHub Actions são fixadas
  por hash de commit; o Dependabot propõe as atualizações.
- **Direitos autorais:** o app mostra a licença informada pela fonte, mas não a garante. A verificação é sua.
