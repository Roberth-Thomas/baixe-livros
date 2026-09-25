# Política de segurança

## Como reportar uma vulnerabilidade

Use o **relato privado de vulnerabilidades** do GitHub: aba **Security → Report a vulnerability** deste
repositório. Não abra uma *issue* pública com detalhes de uma falha.

Este é um projeto pessoal, mantido sem prazo garantido, mas relatos de segurança são lidos com prioridade.

## O que o app faz (e o que ele se recusa a fazer)

O Baixe Livros abre um Chrome sem janela, busca em `baixelivros.com.br` e salva um arquivo no seu computador.
Como ele baixa arquivos de um site de terceiros, as regras abaixo são intencionais e cobertas por testes:

- **Só tipos de livro** (`.pdf`, `.epub`, `.mobi`, `.azw`, `.azw3`, `.doc`, `.docx`, `.txt`, `.zip`, `.rar`) chegam à
  pasta final. Executáveis e outros tipos são recusados. Um PDF precisa começar com o cabeçalho `%PDF-`.
- **Só HTTPS.** As páginas de livro precisam ser do domínio do próprio site; o link do arquivo precisa ser
  `https` e a extensão é verificada no caminho da URL (não na query). `file:`, `javascript:` e `http:` são ignorados.
- **Nada é executado.** O botão "Abrir livro" só abre tipos de documento e nunca `.zip`/`.rar`.
- **Downloads isolados:** cada tentativa usa uma subpasta temporária própria, apagada ao final, então um
  download parcial ou de outro livro nunca vai parar na pasta de destino.
- **Nomes de arquivo seguros:** o nome vem do título do livro, sem caracteres inválidos, sem `../` e sem nomes
  reservados do Windows (`CON`, `NUL`…). Nada é sobrescrito.
- **Sem credenciais, contas ou dados pessoais:** o app não pede login, não envia dados a lugar nenhum além das
  buscas feitas ao site e não guarda histórico.

## Boas práticas ao usar

- Baixe o código deste repositório e gere o `.exe` você mesmo (veja o `README.md`); não confie em `.exe` de origem
  desconhecida.
- Mantenha o Google Chrome atualizado.
- As versões das dependências ficam fixadas em `requirements.txt` para builds reproduzíveis.
