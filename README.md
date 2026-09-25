# Baixe Livros

Aplicativo de janela (Windows) que busca um livro em [baixelivros.com.br](https://www.baixelivros.com.br/)
pelo título e baixa o arquivo para a pasta `Teste de Download`, criada ao lado do programa.

- Usa o Chrome em modo *headless* (sem janela) por trás; o Google Chrome precisa estar instalado.
- Tolera erros de digitação e acentuação: compara o título digitado com os resultados e usa também o
  "Você quis dizer" do próprio site.
- O resultado aparece só no Log: **verde** quando conclui, **vermelho** quando o livro não é encontrado
  (ou quando é encontrado, mas o download não está disponível).
- Botão **Parar busca** encerra o navegador na hora e limpa os arquivos temporários.
- Só arquivos de tipo de livro (PDF, EPUB, MOBI...) são aceitos; o arquivo é salvo com o título do livro.

## Executar

```bash
pip install -r requirements.txt
python baixar_livro.py
```

## Testes

```bash
pytest                    # unitários (rápidos, sem internet)
pytest -m integration     # usa o site real e o Chrome
```

## Gerar o .exe

```bash
pyinstaller --onefile --noconsole --name BaixeLivros --collect-all selenium baixar_livro.py
```

O executável fica em `dist/BaixeLivros.exe`; copie-o para onde quiser.

## Segurança

O app só aceita arquivos de tipo de livro, só segue links HTTPS e nunca executa o que baixa. Detalhes e como
reportar uma vulnerabilidade em [SECURITY.md](SECURITY.md).

## Observação

Os seletores da página de busca (`/search?q=…`, `li.bl-rs__item`…) dependem do layout do site. Se ele mudar,
o app mostra um erro claro em vez de "livro não encontrado", e o teste
`test_a_pagina_de_busca_ainda_tem_o_formato_esperado` (integração) aponta o que quebrou.
