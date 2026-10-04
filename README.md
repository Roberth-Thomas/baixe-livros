# Baixe Livros

Aplicativo (Windows) que busca livros de **acesso aberto e domínio público** pelo título e os baixa para uma
pasta sua. As fontes são o [Internet Archive](https://archive.org/) e o [Project Gutenberg](https://www.gutenberg.org/),
ambos por API aberta — sem navegador por trás e sem dependências além do Python.

## O que faz

- **Só livros:** o Internet Archive mistura livros com documentos do governo, processos, periódicos, artigos e
  manuais; o programa descarta essas coleções e só aceita itens com cara de livro (veja `parece_livro` em
  `fontes.py`). O filtro é por coleção, então um jornal catalogado como "livro" ainda pode passar.
- **Fila em lote:** cole vários títulos (um por linha) e eles baixam em paralelo, com progresso por item,
  **Parar tudo** e **Repetir falhas**.
- **Tolera erros de digitação e acentos:** compara o título digitado com os resultados e, se nada servir, tenta
  buscas alternativas (`cazmurro` acha *Dom Casmurro*). O autor também ajuda a escolher.
- **Retomada:** se a conexão cair ou você parar, o download continua de onde parou.
- **Biblioteca pessoal (SQLite):** registra tudo o que foi baixado, evita baixar duas vezes (mesma obra ou mesmo
  conteúdo) e permite buscar nos seus livros por título ou autor.
- **Licença à vista:** o Log mostra a fonte e a licença de cada obra antes de baixar.
- **Resultado só no Log:** **verde** quando conclui; **vermelho** quando o livro não é encontrado ou o download falha.

## Executar

Janela:

```bash
python baixar_livro.py
```

Linha de comando (sem janela; retorna 0 se todos foram baixados, 1 caso contrário):

```bash
python baixar_livro.py "dom casmurro" "iracema" --paralelo 3 --pasta "D:\Livros"
```

Os livros e o registro da biblioteca (`biblioteca.sqlite3`) ficam na pasta `Teste de Download` ao lado do
programa, ou na indicada em `--pasta`.

O `.exe` é só a janela (não tem console, então não mostra texto no terminal); para ver a saída da linha de
comando, use `python baixar_livro.py`.

## Testes

```bash
pip install -r requirements-dev.txt
pytest                    # unitários (rápidos, sem internet; usam um servidor HTTP local de verdade)
pytest -m integration     # usa as APIs reais do Internet Archive e do Project Gutenberg
```

O CI roda os unitários a cada push (Windows e Linux) e a integração toda segunda-feira, para avisar se alguma
API mudar.

## Gerar o .exe

```bash
pyinstaller --onefile --noconsole --name BaixeLivros baixar_livro.py
```

O executável fica em `dist/BaixeLivros.exe`. Ao criar uma tag `v*`, o workflow `release.yml` gera o `.exe`,
calcula o SHA-256 e publica tudo numa Release.

## Como funciona

| Módulo | Papel |
|---|---|
| `titulos.py` | normalização e comparação tolerante de títulos |
| `fontes.py` | uma classe por fonte (`buscar` / `arquivos`); para somar outra, implemente a mesma interface |
| `rede.py` | cliente HTTP (só biblioteca padrão): domínios permitidos, tentativas, download com retomada |
| `arquivos.py` | nomes seguros, validação do arquivo baixado, publicação sem sobrescrever |
| `biblioteca.py` | registro SQLite |
| `nucleo.py` | busca → escolha do candidato → download → validação → registro |
| `fila.py` | fila em lote com trabalhadores paralelos |
| `baixar_livro.py` | janela (tkinter) e linha de comando |

## Direitos autorais

O programa baixa o que as fontes disponibilizam abertamente, e mostra a licença informada por cada uma. O
Internet Archive nem sempre informa a licença, e o que é domínio público varia de país para país. **Conferir se
você pode usar o livro é responsabilidade sua.**

## Segurança

Só aceita arquivos de tipo de livro, só segue HTTPS para domínios permitidos e nunca executa o que baixa.
Detalhes e como reportar uma vulnerabilidade em [SECURITY.md](SECURITY.md).

## Histórico

A versão 1 baixava do baixelivros.com.br com um Chrome automatizado. O site passou a bloquear acesso
automatizado (desafio da Cloudflare), então a versão 2 trocou a fonte por APIs abertas, sem contornar nenhuma
proteção.
