# ECM225 Shell — Documentação Completa

**Disciplina:** ECM225 — Sistemas Operacionais  
**Projeto:** Interpretador de Comandos em Python (Versão Avançada)  
**Integrantes:** João Victor Pereira Couto — 24.00115-5

---

## Sumário

1. [Como Executar](#como-executar)
2. [Visão Geral da Arquitetura](#visão-geral-da-arquitetura)
3. [Conceitos de Sistemas Operacionais](#conceitos-de-sistemas-operacionais)
4. [Prompt do Shell](#prompt-do-shell)
5. [Comandos Internos (Builtins)](#comandos-internos-builtins)
6. [Pipes](#pipes)
7. [Redirecionamentos de I/O](#redirecionamentos-de-io)
8. [Execução em Background e Controle de Jobs](#execução-em-background-e-controle-de-jobs)
9. [Operadores de Controle](#operadores-de-controle)
10. [Expansão de Variáveis](#expansão-de-variáveis)
11. [Substituição de Comandos](#substituição-de-comandos)
12. [Expansão de Globs](#expansão-de-globs)
13. [Aliases](#aliases)
14. [Histórico e Tab Completion](#histórico-e-tab-completion)
15. [Sinais e Atalhos de Teclado](#sinais-e-atalhos-de-teclado)
16. [Exemplos Práticos](#exemplos-práticos)
17. [Fluxo Interno de Execução](#fluxo-interno-de-execução)
18. [Comparação com o Script Original](#comparação-com-o-script-original)

---

## Como Executar

**Pré-requisito:** Python 3.11 ou superior.

```bash
python3 shell.py
```

Ao iniciar, você verá:

```
ECM225 Shell — Interpretador de Comandos em Python
PID do shell: 12345
Digite help para ver todos os recursos disponíveis ou exit para sair.

usuario@hostname:~$
```

Para sair, use `exit`, `quit` ou pressione `Ctrl+D`.

---

## Visão Geral da Arquitetura

O shell é organizado em camadas bem definidas:

```
Entrada do usuário
       │
       ▼
┌─────────────────────┐
│  Tokenizador/Lexer  │  shlex + expansão de variáveis + globs
└─────────────────────┘
       │
       ▼
┌─────────────────────┐
│  Parser             │  divide por ; && || | &
└─────────────────────┘
       │
       ▼
┌─────────────────────┐
│  Executor           │  builtins ou fork+exec+wait
└─────────────────────┘
       │
       ▼
┌─────────────────────┐
│  Controle de Jobs   │  reap assíncrono, fg, bg
└─────────────────────┘
```

### Arquivos

| Arquivo | Descrição |
|---|---|
| `shell.py` | Código-fonte completo do shell |
| `~/.pyshell_history` | Histórico de comandos (criado automaticamente) |

---

## Conceitos de Sistemas Operacionais

Esta seção explica as chamadas de sistema utilizadas e o porquê de cada uma.

### `fork()` — Criação de Processos

```python
pid = os.fork()
```

`fork()` duplica o processo atual. O sistema operacional cria uma **cópia exata** do processo pai (shell), incluindo memória, descritores de arquivo abertos e variáveis. O retorno é:

- **No pai:** PID do filho (inteiro positivo)
- **No filho:** `0`

O filho herda tudo do pai, mas é um processo independente com seu próprio PID. Esta é a base de como qualquer shell cria processos filhos.

### `execvp()` — Substituição de Imagem do Processo

```python
os.execvp(tokens[0], tokens)
```

Após o `fork()`, o filho ainda é uma cópia do shell. `execvp()` **substitui completamente** a imagem do processo pelo executável desejado (ex: `/bin/ls`). O `v` significa que os argumentos são passados como lista; o `p` significa que o executável é buscado no `$PATH`.

Após `execvp()` ter sucesso, o código Python do filho **nunca mais executa** — o processo agora é o `ls`, o `grep`, etc.

### `wait()` / `waitpid()` — Sincronização entre Processos

```python
pid_filho, status = os.waitpid(pid, 0)
```

O processo pai **bloqueia** (fica suspenso) até que o filho termine. Isso é sincronização básica entre processos pai e filho. Sem `wait()`, o filho viraria um **processo zumbi** — terminado mas ainda ocupando entrada na tabela de processos do kernel.

O `status` retornado é um valor codificado. Para extrair o código de saída real:

```python
if os.WIFEXITED(status):
    codigo = os.WEXITSTATUS(status)   # saída normal
elif os.WIFSIGNALED(status):
    sinal = os.WTERMSIG(status)       # morto por sinal
elif os.WIFSTOPPED(status):
    sinal = os.WSTOPSIG(status)       # parado (Ctrl+Z)
```

### `pipe()` — Comunicação Entre Processos

```python
pipe_read, pipe_write = os.pipe()
```

`pipe()` cria um **canal de comunicação unidirecional** entre dois processos. Retorna dois descritores de arquivo:

- `pipe_write`: ponta de escrita — o processo escreve dados aqui
- `pipe_read`: ponta de leitura — o outro processo lê daqui

O kernel gerencia um buffer interno. Quando o buffer enche, o escritor bloqueia até o leitor consumir dados. Esta é a base dos pipes (`|`) do shell.

### `dup2()` — Redirecionamento de Descritores

```python
os.dup2(fd_arquivo, sys.stdout.fileno())
```

`dup2(oldfd, newfd)` faz com que `newfd` passe a referenciar o mesmo arquivo que `oldfd`. Após isso, tudo que o processo escrever em `stdout` (fd 1) irá para o arquivo, e não para o terminal.

Este é o mecanismo por trás de todos os redirecionamentos (`>`, `<`, `|`).

### `setpgid()` — Grupos de Processos

```python
os.setpgid(0, 0)    # filho cria seu próprio grupo
```

Todo processo pertence a um **grupo de processos**. O terminal envia sinais (como `SIGINT` ao pressionar Ctrl+C) para o **grupo de foreground** inteiro. Ao criar um novo grupo para cada comando, garantimos que:

- Ctrl+C mata o comando atual, não o shell
- Processos em background não recebem Ctrl+C

### `tcsetpgrp()` — Controle do Terminal

```python
os.tcsetpgrp(sys.stdin.fileno(), pid_filho)
```

Define qual grupo de processos está em **foreground** (ou seja, qual grupo "tem" o terminal). Quando o comando termina, o shell devolve o terminal para si mesmo:

```python
os.tcsetpgrp(sys.stdin.fileno(), os.getpgrp())
```

### `kill()` — Envio de Sinais

```python
os.kill(pid, signal.SIGTERM)
os.kill(-pid, signal.SIGCONT)   # negativo → envia para o grupo
```

`kill()` envia um sinal para um processo ou grupo. Um PID negativo envia o sinal para **todos os processos do grupo**.

### `waitpid()` com `WNOHANG` — Verificação Não-Bloqueante

```python
pid, status = os.waitpid(job["pid"], os.WNOHANG)
```

`WNOHANG` ("no hang") faz o `waitpid()` retornar imediatamente mesmo que o filho ainda esteja rodando. Retorna `(0, 0)` se o filho não terminou. Usado para verificar jobs em background sem bloquear o shell.

---

## Prompt do Shell

O prompt exibe informações do contexto atual:

```
usuario@hostname:~/projetos$
```

| Parte | Significado |
|---|---|
| `usuario` | Nome do usuário atual (verde) |
| `@hostname` | Nome do computador |
| `:~/projetos` | Diretório atual (`~` = home) |
| `$` | Usuário comum (`#` se root) |

---

## Comandos Internos (Builtins)

Builtins são comandos executados **diretamente no processo do shell**, sem `fork()`. Isso é necessário quando o comando precisa alterar o estado do próprio shell (como `cd`, que muda o diretório de trabalho).

Se `cd` fosse executado via fork+exec, mudaria o diretório do filho — que em seguida terminaria —, e o pai (o shell) continuaria no mesmo diretório.

### `cd` — Mudar de Diretório

```bash
cd              # vai para $HOME
cd /tmp         # vai para /tmp
cd ..           # sobe um nível
cd -            # volta ao diretório anterior
cd ~            # vai para $HOME
```

### `pwd` — Diretório Atual

```bash
pwd
# /home/usuario/projetos
```

### `echo` — Exibir Texto

```bash
echo Olá, mundo!          # Olá, mundo!
echo -n "sem newline"     # sem quebra de linha
echo -e "linha1\nlinha2"  # interpreta \n como quebra de linha
echo "PID: $$"            # exibe PID do shell
echo "Saída: $?"          # exibe código de saída anterior
```

### `export` — Variáveis de Ambiente

```bash
export PATH=/usr/bin:/bin    # define PATH
export DEBUG=1               # define DEBUG
export                       # lista todas as variáveis exportadas
```

### `unset` — Remover Variável

```bash
unset DEBUG
unset TEMP_VAR VAR2
```

### `env` — Listar Variáveis de Ambiente

```bash
env
# PATH=/usr/bin:/bin
# HOME=/home/usuario
# ...
```

### `alias` — Atalhos de Comandos

```bash
alias                    # lista todos os aliases
alias ll                 # exibe o alias 'll'
alias ll='ls -la'        # define alias
alias gs='git status'
```

Aliases padrão já definidos ao iniciar:

| Alias | Comando |
|---|---|
| `ll` | `ls -la` |
| `la` | `ls -A` |
| `l` | `ls -CF` |
| `cls` | `clear` |
| `..` | `cd ..` |
| `...` | `cd ../..` |

### `unalias` — Remover Alias

```bash
unalias ll
unalias -a       # remove todos
```

### `history` — Histórico de Comandos

```bash
history          # lista todos os comandos
history 10       # lista os últimos 10
history -c       # limpa o histórico
history -w       # salva no arquivo ~/.pyshell_history
```

### `type` — Tipo de Comando

```bash
type ls
# ls é /bin/ls
type cd
# cd é um builtin do shell
type ll
# ll é um alias para 'ls -la'
type comando_inexistente
# shell: type: comando_inexistente: não encontrado
```

### `which` — Localizar Executável

```bash
which python3
# /usr/bin/python3
which ls grep
# /bin/ls
# /bin/grep
```

### `source` (ou `.`) — Executar Script no Contexto Atual

```bash
source ~/.bashrc
source config.sh
. ./variaveis.sh
```

Diferente de executar o script diretamente, `source` roda cada linha no shell atual — portanto, variáveis definidas no script ficam disponíveis depois.

### `jobs` — Listar Jobs

```bash
jobs
# [1]+ Running   sleep 30
# [2]- Stopped   vim arquivo.txt

jobs -l          # inclui PIDs
# [1]+ 1234 Running   sleep 30
```

### `fg` — Trazer Job para Foreground

```bash
fg               # traz o job mais recente
fg %1            # traz o job 1
fg %2            # traz o job 2
```

### `bg` — Retomar Job em Background

```bash
bg               # retoma o job mais recente em background
bg %1
```

### `kill` — Enviar Sinal

```bash
kill %1           # envia SIGTERM ao job 1
kill 1234         # envia SIGTERM ao PID 1234
kill -9 1234      # envia SIGKILL (força encerramento)
kill -SIGSTOP %2  # pausa o job 2
kill -SIGCONT %2  # retoma o job 2
kill -l           # lista todos os sinais disponíveis
```

### `true` e `false`

```bash
true    # sempre retorna 0 (sucesso)
false   # sempre retorna 1 (falha)

# Úteis em condicionais:
false || echo "falhou como esperado"
true && echo "sucesso!"
```

### `exit` / `quit`

```bash
exit         # encerra o shell com código 0
exit 1       # encerra com código de erro 1
quit         # equivalente a exit
```

### `help`

```bash
help         # exibe referência completa de todos os recursos
```

---

## Pipes

Um pipe conecta o **stdout** de um comando ao **stdin** do próximo, usando `os.pipe()` e `os.dup2()` internamente.

```bash
ls -l | grep ".py"
ls -l | grep ".py" | wc -l
cat /etc/passwd | sort | uniq | head -5
echo "hello world" | tr '[:lower:]' '[:upper:]'
```

### Como funciona internamente

Para `ls | grep | wc`:

```
ls          grep         wc
stdout ──► stdin       
            stdout ──► stdin
                        stdout ──► terminal
```

O shell cria N−1 pipes para N comandos, usa `fork()` para criar N processos, e conecta os descritores com `dup2()`.

---

## Redirecionamentos de I/O

### Saída padrão (`stdout`)

```bash
ls -l > lista.txt          # sobrescreve
echo "novo" >> lista.txt   # acrescenta ao final
```

### Entrada padrão (`stdin`)

```bash
sort < nomes.txt            # lê de arquivo ao invés do teclado
wc -l < arquivo.txt
```

### Saída de erro (`stderr`)

```bash
ls /inexistente 2> erros.txt        # redireciona apenas stderr
ls /inexistente 2>> erros.txt       # acrescenta stderr
```

### Stdout e stderr juntos

```bash
make &> build.log           # stdout + stderr → arquivo
comando &> /dev/null        # descarta toda a saída
```

### Combinações

```bash
grep "padrão" arquivo.txt > resultado.txt 2> erros.txt
sort < entrada.txt > saida_ordenada.txt
```

### Implementação

Todos os redirecionamentos usam `os.open()` para abrir o arquivo e `os.dup2()` para substituir o descritor padrão no processo filho, antes do `execvp()`.

---

## Execução em Background e Controle de Jobs

### Executar em Background

Adicione `&` ao final do comando:

```bash
sleep 30 &
# [1] 1234

python3 script_demorado.py &
# [2] 1235

make all &> build.log &
# [3] 1236
```

O número entre colchetes é o **job ID**. O segundo número é o **PID**.

### Verificar Jobs

```bash
jobs
# [1]- Running   sleep 30
# [2]+ Running   python3 script_demorado.py
# [3]  Running   make all
```

O `+` indica o job mais recente; `-` indica o penúltimo.

### Trazer para Foreground

```bash
fg %1       # job 1 vem para o foreground
fg          # traz o job mais recente (marcado com +)
```

### Pausar e Retomar

```bash
# Enquanto um comando roda em foreground, pressione Ctrl+Z para pausar:
vim arquivo.txt
# ^Z
# [1]+ Stopped   vim arquivo.txt

bg %1       # retoma o vim em background
fg %1       # traz o vim de volta para foreground
```

### Matar Jobs

```bash
kill %1         # encerra o job 1
kill -9 %2      # força encerramento do job 2
```

### Notificação Automática

Quando um job em background termina, o shell notifica na próxima vez que exibir o prompt:

```
[1]+ Done (0)   sleep 5
usuario@host:~$
```

---

## Operadores de Controle

### Ponto e Vírgula (`;`) — Execução Sequencial

```bash
ls ; pwd ; date
mkdir novo_dir ; cd novo_dir ; pwd
```

O segundo comando sempre executa, independente do resultado do primeiro.

### E-lógico (`&&`) — Executa se o Anterior Teve Sucesso

```bash
mkdir projeto && cd projeto && git init
gcc programa.c -o programa && ./programa
make && make install
```

O segundo comando só executa se o primeiro retornar código 0 (sucesso).

### Ou-lógico (`||`) — Executa se o Anterior Falhou

```bash
ping -c 1 google.com || echo "Sem conexão!"
cat arquivo.txt || echo "Arquivo não encontrado"
[ -d backup ] || mkdir backup
```

O segundo comando só executa se o primeiro retornar código diferente de 0.

### Combinações

```bash
make && echo "Compilado com sucesso!" || echo "Erro na compilação"
cd /tmp && ls -la || echo "Não consegui entrar em /tmp"
```

---

## Expansão de Variáveis

### Variáveis de Ambiente

```bash
echo $HOME
echo $PATH
echo $USER

# Sintaxe com chaves (necessária para evitar ambiguidade):
echo ${HOME}/documentos
echo "Arquivo: ${NOME}_backup.txt"
```

### Definir Variáveis

```bash
export MEU_VAR=valor
export PORTA=8080
echo $MEU_VAR    # valor
echo $PORTA      # 8080
```

### Variáveis Especiais

| Variável | Significado | Exemplo |
|---|---|---|
| `$?` | Código de saída do último comando | `echo $?` → `0` |
| `$$` | PID do processo do shell | `echo $$` → `1234` |
| `$!` | PID do último processo em background | `sleep 5 & echo $!` |

```bash
ls /tmp
echo "Saiu com: $?"     # 0

ls /inexistente
echo "Saiu com: $?"     # 2

echo "PID do shell: $$"

sleep 10 &
echo "Job em background: $!"
```

---

## Substituição de Comandos

Substitui o resultado de um comando no lugar da expressão:

```bash
echo "Hoje é $(date)"
echo "Você está em: $(pwd)"
echo "Arquivos: $(ls | wc -l)"
BRANCH=$(git branch --show-current)
echo "Branch atual: $BRANCH"

# Também funciona com backticks (sintaxe antiga):
echo "Host: `hostname`"
```

O conteúdo entre `$()` é executado em subshell, e sua saída (sem a quebra de linha final) substitui a expressão.

---

## Expansão de Globs

Expande padrões para nomes de arquivo:

| Padrão | Significado |
|---|---|
| `*` | Qualquer sequência de caracteres |
| `?` | Exatamente um caractere qualquer |
| `[abc]` | Um dos caracteres listados |
| `[a-z]` | Um caractere no intervalo |
| `[!abc]` | Nenhum dos caracteres listados |

```bash
ls *.py              # todos os .py
ls arquivo?.txt      # arquivo1.txt, arquivoA.txt, etc.
ls [0-9]*.sh         # scripts que começam com dígito
rm temp_[0-9].txt    # remove temp_0.txt até temp_9.txt
echo src/**/*.ts     # todos os .ts dentro de src/
```

Se o padrão não corresponder a nenhum arquivo, ele é mantido literalmente (como no bash padrão).

---

## Aliases

Aliases são atalhos para comandos mais longos, armazenados somente na sessão atual.

```bash
# Definir
alias ll='ls -la'
alias gs='git status'
alias py='python3'
alias servidor='python3 -m http.server 8080'

# Usar
ll              # equivale a: ls -la
gs              # equivale a: git status

# Aliases aceitam argumentos adicionais:
ll /tmp         # equivale a: ls -la /tmp

# Listar
alias           # lista todos
alias ll        # lista apenas 'll'

# Remover
unalias gs
unalias -a     # remove todos
```

### Aliases Padrão

O shell já inicia com estes aliases definidos:

```bash
alias ll='ls -la'
alias la='ls -A'
alias l='ls -CF'
alias cls='clear'
alias ..='cd ..'
alias ...='cd ../..'
```

---

## Histórico e Tab Completion

### Histórico

O histórico é salvo automaticamente em `~/.pyshell_history` e carregado ao iniciar o shell.

```bash
history          # exibe todo o histórico numerado
history 20       # exibe os últimos 20 comandos
history -c       # limpa o histórico da sessão
history -w       # força salvar no arquivo
```

**Navegação:**
- `↑` — comando anterior
- `↓` — próximo comando
- `Ctrl+R` — busca interativa no histórico (começa a digitar para filtrar)

### Tab Completion

Pressione `Tab` para completar automaticamente:

```bash
# Comandos:
py[Tab]          → python3

# Arquivos e diretórios:
ls Do[Tab]       → ls Documentos/
cat she[Tab]     → cat shell.py

# Duplo Tab mostra todas as opções:
ls [Tab][Tab]    → lista todos os arquivos
```

---

## Sinais e Atalhos de Teclado

| Atalho | Sinal | Comportamento |
|---|---|---|
| `Ctrl+C` | `SIGINT` | Interrompe o comando em foreground; o shell continua |
| `Ctrl+Z` | `SIGTSTP` | Pausa o comando em foreground; use `fg`/`bg` para retomar |
| `Ctrl+D` | (EOF) | Encerra o shell (equivale a `exit`) |
| `Ctrl+R` | — | Busca no histórico |
| `Ctrl+L` | — | Limpa a tela |
| `Ctrl+A` | — | Vai ao início da linha |
| `Ctrl+E` | — | Vai ao fim da linha |
| `Ctrl+W` | — | Apaga palavra anterior |

### Como os Sinais são Tratados

O shell instala handlers para não ser encerrado pelos sinais que normalmente encerrariam processos em foreground:

```python
signal.signal(signal.SIGINT,  _handler_sigint)   # Ctrl+C: limpa linha, continua
signal.signal(signal.SIGTSTP, _handler_sigtstp)  # Ctrl+Z: avisa, não pausa o shell
signal.signal(signal.SIGTTOU, signal.SIG_IGN)    # ignora erros de terminal em bg
signal.signal(signal.SIGTTIN, signal.SIG_IGN)    # ignora leitura de terminal em bg
```

---

## Exemplos Práticos

### Processamento de Texto

```bash
# Contar linhas de código Python no projeto
find . -name "*.py" | xargs wc -l | sort -n

# Buscar e filtrar
cat /etc/passwd | grep "/bin/bash" | cut -d: -f1

# Top 5 arquivos maiores
ls -la | sort -k5 -n -r | head -5

# Substituir texto em todos os arquivos
grep -r "texto_antigo" . | cut -d: -f1 | xargs sed -i 's/texto_antigo/novo/g'
```

### Monitoramento de Processos

```bash
# Rodar monitoramento em background
watch -n 2 free -h &
# [1] 5678

# Ver jobs
jobs

# Encerrar quando não precisar mais
kill %1
```

### Redirecionamentos Avançados

```bash
# Compilar e salvar erros
gcc programa.c -o programa 2> erros_compilacao.txt
cat erros_compilacao.txt

# Descarte total (silencioso)
comando_barulhento > /dev/null 2>&1

# Logar stdout e stderr separados
./servidor.py > server.log 2> server_errors.log &
```

### Variáveis e Condicionais

```bash
# Verificar se um arquivo existe
ls arquivo.txt && echo "existe" || echo "não existe"

# Encadeamento condicional
git pull && npm install && npm run build && echo "Deploy pronto!"

# Usar variáveis
export PROJETO=meu_app
mkdir $PROJETO && cd $PROJETO && git init
echo "Projeto $PROJETO inicializado em $(pwd)"
```

### Source para Configuração

```bash
# Arquivo config.sh:
# export DEBUG=1
# export PORTA=3000
# alias servidor='python3 -m http.server $PORTA'

source config.sh
echo $DEBUG    # 1
servidor       # inicia servidor na porta 3000
```

---

## Fluxo Interno de Execução

### Para um comando simples (`ls -la`)

```
1. input("shell> ")                        → "ls -la"
2. _tokenize("ls -la")                     → ["ls", "-la"]
3. _split_by_operator(...)                 → [("", ["ls", "-la"])]
4. _split_pipes(["ls", "-la"])             → [["ls", "-la"]]
5. _parse_redirections(["ls", "-la"])      → (["ls", "-la"], {})
6. tokens[0] not in BUILTINS              → execução externa
7. os.fork()
   ├── Filho (pid == 0):
   │     os.setpgid(0, 0)                 → novo grupo de processos
   │     _apply_redirections({})          → nenhum redirecionamento
   │     os.execvp("ls", ["ls", "-la"])   → processo vira 'ls'
   └── Pai (pid > 0):
         os.setpgid(pid, pid)
         os.tcsetpgrp(stdin, pid)         → terminal vai para o filho
         os.waitpid(pid, 0)              → aguarda ls terminar
         os.tcsetpgrp(stdin, getpgrp())  → terminal volta para o shell
         retorna código de saída
8. LAST_EXIT_CODE = 0
9. próxima iteração do loop
```

### Para um pipeline (`ls | grep .py | wc -l`)

```
1. Tokeniza → ["ls", "|", "grep", ".py", "|", "wc", "-l"]
2. _split_pipes → [["ls"], ["grep", ".py"], ["wc", "-l"]]
3. Cria 2 pipes:
     pipe1: (r1, w1)
     pipe2: (r2, w2)
4. fork() × 3 processos:
     Filho 1 (ls):
       stdout → w1
       execvp("ls", ...)
     Filho 2 (grep):
       stdin  ← r1
       stdout → w2
       execvp("grep", ...)
     Filho 3 (wc):
       stdin  ← r2
       stdout → terminal
       execvp("wc", ...)
5. Pai: waitpid() para todos os filhos
6. Retorna código de saída do último (wc)
```

---

## Comparação com o Script Original

| Funcionalidade | Script Original | Shell Avançado |
|---|---|---|
| Execução de comandos externos | ✅ fork+execvp | ✅ fork+execvp |
| Código de saída | ✅ básico | ✅ completo (WIFEXITED, WIFSIGNALED, WIFSTOPPED) |
| Pipes (`\|`) | ❌ | ✅ encadeados |
| Redirecionamento `>` | ❌ | ✅ |
| Redirecionamento `>>` | ❌ | ✅ |
| Redirecionamento `<` | ❌ | ✅ |
| Redirecionamento `2>` | ❌ | ✅ |
| Redirecionamento `&>` | ❌ | ✅ |
| Background (`&`) | ❌ | ✅ |
| Controle de jobs (`jobs`, `fg`, `bg`) | ❌ | ✅ |
| Operador `&&` | ❌ | ✅ |
| Operador `\|\|` | ❌ | ✅ |
| Operador `;` | ❌ | ✅ |
| Builtin `cd` | ❌ | ✅ com `cd -` |
| Builtin `pwd` | ❌ | ✅ |
| Builtin `echo` | ❌ | ✅ com `-n` e `-e` |
| Builtin `export` / `unset` / `env` | ❌ | ✅ |
| Builtin `alias` / `unalias` | ❌ | ✅ |
| Builtin `history` | ❌ | ✅ |
| Builtin `type` / `which` | ❌ | ✅ |
| Builtin `jobs` / `fg` / `bg` / `kill` | ❌ | ✅ |
| Builtin `source` / `.` | ❌ | ✅ |
| Expansão de variáveis (`$VAR`) | ❌ | ✅ |
| Variáveis especiais (`$?`, `$$`, `$!`) | ❌ | ✅ |
| Substituição de comandos (`$(cmd)`) | ❌ | ✅ |
| Expansão de globs (`*.py`) | ❌ | ✅ |
| Histórico com setas ↑ ↓ | ❌ | ✅ persistente |
| Ctrl+R busca no histórico | ❌ | ✅ |
| Tab completion | ❌ | ✅ |
| Prompt colorido | ❌ | ✅ com usuário/host/dir |
| Grupos de processos (`setpgid`) | ❌ | ✅ |
| Controle do terminal (`tcsetpgrp`) | ❌ | ✅ |
| Tratamento de sinais | ❌ | ✅ SIGINT, SIGTSTP, SIGTTOU, SIGTTIN |
| Aspas simples e duplas | ❌ | ✅ |
| Aliases padrão | ❌ | ✅ |
| Reap assíncrono de jobs (`WNOHANG`) | ❌ | ✅ |
