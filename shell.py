"""
ECM225 - Sistemas Operacionais
Projeto: Interpretador de Comandos em Python (Versão Avançada)

Integrantes:
  - João Victor Pereira Couto  24.00115-5

Descrição:
  Shell avançado que simula o comportamento de um interpretador de comandos
  Linux. Implementa recursos próximos ao bash, utilizando chamadas de sistema
  do módulo `os` para gerenciamento de processos (fork, execvp, wait, pipe,
  dup2, etc.).

Funcionalidades implementadas:
  - Prompt colorido com usuário, hostname e diretório atual
  - Histórico de comandos com navegação por setas (↑ ↓) via readline
  - Tab completion para arquivos, diretórios e comandos
  - Suporte a pipes encadeados:          ls -l | grep ".py" | wc -l
  - Redirecionamentos de I/O:            cmd > arq.txt  cmd >> arq.txt  cmd < arq.txt
  - Redirecionamento de stderr:          cmd 2> erros.txt  cmd &> tudo.txt
  - Execução em background:              sleep 10 &
  - Separação por ponto e vírgula:       ls ; pwd ; date
  - Execução condicional:                mkdir dir && cd dir   cmd_ruim || echo "falhou"
  - Expansão de variáveis de ambiente:   echo $HOME  echo ${PATH}
  - Variáveis especiais:                 $? (último código de saída)  $$ (PID do shell)  $! (PID do último bg)
  - Substituição de comandos:            echo "hoje é $(date)"
  - Expansão de globs:                   ls *.py  rm temp_?.txt
  - Aspas simples e duplas
  - Controle de jobs:                    jobs  fg [n]  bg [n]  kill %n
  - Sinais:                              Ctrl+C (SIGINT)  Ctrl+Z (SIGTSTP)
  - Aliases:                             alias ll='ls -la'  unalias ll
  - Variáveis de ambiente:               export VAR=valor  unset VAR  env
  - Comandos internos completos:
      cd, pwd, echo, exit, quit, help, history, alias, unalias,
      export, unset, env, type, which, jobs, fg, bg, kill, source, true, false

Conceitos de SO demonstrados:
  - fork()   : duplica o processo atual criando um filho
  - execvp() : substitui a imagem do processo filho pelo programa desejado
  - wait()   : o pai bloqueia até o filho terminar (sincronização)
  - pipe()   : cria um canal de comunicação unidirecional entre processos
  - dup2()   : redireciona descritores de arquivo (stdin/stdout/stderr)
  - setpgid(): cria grupos de processos para controle de jobs
  - tcsetpgrp(): transfere o terminal para um grupo de processos (fg)
  - kill()   : envia sinais para processos ou grupos
  - signal() : instala handlers para sinais
"""

import os
import sys
import glob
import shlex
import signal
import readline
import socket
import re
import subprocess
from typing import Optional


# ---------------------------------------------------------------------------
# Constantes de escape ANSI para colorir o terminal
# ---------------------------------------------------------------------------
RESET   = "\033[0m"
BOLD    = "\033[1m"
RED     = "\033[31m"
GREEN   = "\033[32m"
YELLOW  = "\033[33m"
BLUE    = "\033[34m"
MAGENTA = "\033[35m"
CYAN    = "\033[36m"
WHITE   = "\033[37m"


# ---------------------------------------------------------------------------
# Estado global do shell
# ---------------------------------------------------------------------------

# Aliases definidos pelo usuário: {'ll': 'ls -la', ...}
ALIASES: dict[str, str] = {}

# Lista de jobs em background: [(job_id, pid, comando, status), ...]
JOBS: list[dict] = []
_next_job_id = 1          # contador de job IDs

# PID do último processo executado em background ($!)
LAST_BG_PID: Optional[int] = None

# Código de saída do último comando ($?)
LAST_EXIT_CODE: int = 0

# Diretório anterior para "cd -"
PREV_DIR: str = os.getcwd()


# ---------------------------------------------------------------------------
# Configuração do readline (histórico e tab completion)
# ---------------------------------------------------------------------------

HIST_FILE = os.path.expanduser("~/.pyshell_history")

def _setup_readline() -> None:
    """
    Configura o readline para:
    - Persistir histórico entre sessões no arquivo ~/.pyshell_history
    - Navegar com as setas ↑ e ↓
    - Completar nomes de arquivo/diretório com Tab
    - Completar comandos internos e aliases com Tab
    """
    # Carrega histórico anterior (se existir)
    try:
        readline.read_history_file(HIST_FILE)
    except FileNotFoundError:
        pass

    readline.set_history_length(1000)

    # Ativa o completador personalizado
    readline.set_completer(_tab_completer)
    readline.set_completer_delims(" \t\n;|&><")
    readline.parse_and_bind("tab: complete")


def _tab_completer(text: str, state: int) -> Optional[str]:
    """
    Função de tab completion chamada pelo readline.

    Completa:
    1. Comandos internos do shell
    2. Aliases definidos
    3. Arquivos e diretórios no diretório atual ou com prefixo de caminho
    4. Executáveis no PATH (quando é o primeiro token do comando)
    """
    # Lista de possibilidades construída na primeira chamada (state == 0)
    if state == 0:
        _tab_completer.matches = []  # type: ignore

        builtins = list(BUILTINS.keys())
        aliases  = list(ALIASES.keys())

        # Verifica se estamos completando o primeiro token (comando) ou um argumento
        line = readline.get_line_buffer()
        tokens = line.lstrip().split()
        is_command = len(tokens) == 0 or (len(tokens) == 1 and not line.endswith(" "))

        if is_command:
            # Completa com builtins + aliases + executáveis do PATH
            candidates = builtins + aliases
            for directory in os.environ.get("PATH", "").split(":"):
                try:
                    for entry in os.listdir(directory):
                        full = os.path.join(directory, entry)
                        if os.access(full, os.X_OK) and not os.path.isdir(full):
                            candidates.append(entry)
                except (PermissionError, FileNotFoundError):
                    pass
        else:
            candidates = []

        # Completa sempre com caminhos de arquivo/diretório
        file_matches = glob.glob(text + "*")
        # Adiciona "/" em diretórios
        file_matches = [m + "/" if os.path.isdir(m) else m for m in file_matches]
        candidates += file_matches

        # Filtra candidatos que começam com o texto digitado
        _tab_completer.matches = [c for c in set(candidates) if c.startswith(text)]

    try:
        return _tab_completer.matches[state]
    except IndexError:
        return None


# ---------------------------------------------------------------------------
# Expansão de variáveis, globs e substituição de comandos
# ---------------------------------------------------------------------------

def _expand_variables(token: str) -> str:
    """
    Expande variáveis de ambiente e variáveis especiais dentro de um token.

    Variáveis suportadas:
    - $VAR ou ${VAR}  : variável de ambiente
    - $?              : código de saída do último comando
    - $$              : PID do shell atual
    - $!              : PID do último processo em background
    - $(cmd)          : substituição de comando

    Parâmetros:
        token (str): String que pode conter referências a variáveis.

    Retorno:
        str: Token com variáveis substituídas pelos seus valores.
    """
    # Substituição de comandos: $(cmd) ou `cmd`
    def replace_cmd_sub(match):
        cmd_str = match.group(1) or match.group(2)
        try:
            result = subprocess.run(
                cmd_str, shell=True, capture_output=True, text=True
            )
            return result.stdout.rstrip("\n")
        except Exception:
            return ""

    token = re.sub(r"\$\((.+?)\)|`(.+?)`", replace_cmd_sub, token)

    # Variáveis especiais
    token = token.replace("$$", str(os.getpid()))
    token = token.replace("$?", str(LAST_EXIT_CODE))
    token = token.replace("$!", str(LAST_BG_PID) if LAST_BG_PID else "")

    # ${VAR} e $VAR
    def replace_var(match):
        key = match.group(1) or match.group(2)
        return os.environ.get(key, "")

    token = re.sub(r"\$\{(\w+)\}|\$(\w+)", replace_var, token)
    return token


def _expand_globs(tokens: list[str]) -> list[str]:
    """
    Expande padrões glob (*, ?, [...]) em tokens da linha de comando.

    Se um padrão não corresponder a nenhum arquivo, mantém o token original
    (comportamento similar ao bash com nullglob desligado).

    Parâmetros:
        tokens (list[str]): Lista de tokens da linha de comando.

    Retorno:
        list[str]: Lista com globs expandidos.
    """
    result = []
    for token in tokens:
        if any(c in token for c in ("*", "?", "[")):
            matches = glob.glob(token)
            if matches:
                result.extend(sorted(matches))
            else:
                result.append(token)
        else:
            result.append(token)
    return result


def _tokenize(line: str) -> list[str]:
    """
    Tokeniza a linha de comando respeitando aspas simples e duplas.
    Após a tokenização, expande variáveis (exceto em aspas simples) e globs.

    Usa shlex para um parsing robusto de aspas e escapes.

    Parâmetros:
        line (str): Linha digitada pelo usuário.

    Retorno:
        list[str]: Lista de tokens prontos para execução.
    """
    try:
        lexer = shlex.shlex(line, posix=True)
        lexer.whitespace_split = True
        lexer.whitespace = " \t"
        raw_tokens = list(lexer)
    except ValueError as e:
        print(f"shell: erro de tokenização: {e}", file=sys.stderr)
        return []

    # Expande variáveis e globs
    expanded = [_expand_variables(t) for t in raw_tokens]
    expanded = _expand_globs(expanded)
    return expanded


# ---------------------------------------------------------------------------
# Análise da linha de comando (parser)
# ---------------------------------------------------------------------------

# Operadores que separam comandos
_COMMAND_SEPS = {";", "&&", "||"}
# Operadores de pipe
_PIPE_OP = "|"


def _split_by_operator(tokens: list[str], operators: set[str]) -> list[tuple[str, list[str]]]:
    """
    Divide uma lista de tokens pelo(s) operador(es) especificado(s).

    Retorno:
        list of (operador_que_precedeu, [tokens_do_comando])
        O primeiro elemento tem operador = "".
    """
    result = []
    current: list[str] = []
    op = ""

    for tok in tokens:
        if tok in operators:
            result.append((op, current))
            current = []
            op = tok
        else:
            current.append(tok)

    result.append((op, current))
    return result


def _parse_redirections(tokens: list[str]) -> tuple[list[str], dict]:
    """
    Extrai redirecionamentos de I/O da lista de tokens.

    Redirecionamentos suportados:
    - >  arquivo  : stdout para arquivo (trunca)
    - >> arquivo  : stdout para arquivo (acrescenta)
    - <  arquivo  : stdin de arquivo
    - 2> arquivo  : stderr para arquivo
    - &> arquivo  : stdout e stderr para arquivo

    Parâmetros:
        tokens (list[str]): Tokens que podem conter operadores de redirecionamento.

    Retorno:
        tuple: (tokens_sem_redirecionamentos, dict com redirecionamentos)
               Ex: {"stdout": ("arquivo.txt", "w"), "stdin": "entrada.txt", "stderr": "err.txt"}
    """
    clean_tokens: list[str] = []
    redirs: dict = {}

    i = 0
    while i < len(tokens):
        tok = tokens[i]

        if tok == ">" or tok == "1>":
            if i + 1 < len(tokens):
                redirs["stdout"] = (tokens[i + 1], "w")
                i += 2
            else:
                print("shell: redirecionamento '>' sem arquivo destino", file=sys.stderr)
                i += 1

        elif tok == ">>" or tok == "1>>":
            if i + 1 < len(tokens):
                redirs["stdout"] = (tokens[i + 1], "a")
                i += 2
            else:
                print("shell: redirecionamento '>>' sem arquivo destino", file=sys.stderr)
                i += 1

        elif tok == "<":
            if i + 1 < len(tokens):
                redirs["stdin"] = tokens[i + 1]
                i += 2
            else:
                print("shell: redirecionamento '<' sem arquivo origem", file=sys.stderr)
                i += 1

        elif tok == "2>":
            if i + 1 < len(tokens):
                redirs["stderr"] = (tokens[i + 1], "w")
                i += 2
            else:
                print("shell: redirecionamento '2>' sem arquivo destino", file=sys.stderr)
                i += 1

        elif tok == "&>":
            if i + 1 < len(tokens):
                redirs["stdout"] = (tokens[i + 1], "w")
                redirs["stderr"] = ("&1", "w")   # stderr → stdout
                i += 2
            else:
                print("shell: redirecionamento '&>' sem arquivo destino", file=sys.stderr)
                i += 1

        else:
            clean_tokens.append(tok)
            i += 1

    return clean_tokens, redirs


def _split_pipes(tokens: list[str]) -> list[list[str]]:
    """
    Divide uma lista de tokens por pipes ('|'), retornando segmentos.

    Parâmetros:
        tokens (list[str]): Tokens de um único comando (sem operadores ; && ||).

    Retorno:
        list[list[str]]: Lista de segmentos de comando separados por '|'.
    """
    segments: list[list[str]] = []
    current: list[str] = []
    for tok in tokens:
        if tok == _PIPE_OP:
            segments.append(current)
            current = []
        else:
            current.append(tok)
    segments.append(current)
    return segments


# ---------------------------------------------------------------------------
# Controle de jobs (processos em background)
# ---------------------------------------------------------------------------

def _add_job(pid: int, command: str) -> int:
    """
    Registra um novo job em background.

    Parâmetros:
        pid     (int): PID do processo filho em background.
        command (str): Representação textual do comando.

    Retorno:
        int: ID do job (número entre colchetes exibido ao usuário).
    """
    global _next_job_id
    job_id = _next_job_id
    _next_job_id += 1
    JOBS.append({"id": job_id, "pid": pid, "command": command, "status": "Running"})
    return job_id


def _reap_jobs() -> None:
    """
    Verifica jobs em background que terminaram (WNOHANG).
    Atualiza seus status sem bloquear o shell.

    Usa waitpid com WNOHANG para verificação não-bloqueante — o shell
    não precisa esperar por todos os filhos antes de exibir o próximo prompt.
    """
    for job in JOBS:
        if job["status"] == "Running":
            try:
                pid, status = os.waitpid(job["pid"], os.WNOHANG)
                if pid != 0:
                    if os.WIFEXITED(status):
                        job["status"] = f"Done ({os.WEXITSTATUS(status)})"
                    elif os.WIFSIGNALED(status):
                        job["status"] = f"Killed ({os.WTERMSIG(status)})"
                    print(f"\n[{job['id']}]+ {job['status']}   {job['command']}")
            except ChildProcessError:
                job["status"] = "Done"


def _get_job(spec: str) -> Optional[dict]:
    """
    Encontra um job pelo ID (%n), PID ou pelo mais recente (%+, %).

    Parâmetros:
        spec (str): Especificação do job ('%1', '1234', '%', '%+', '%-').

    Retorno:
        dict ou None: Dicionário do job ou None se não encontrado.
    """
    if not JOBS:
        return None

    if spec in ("%", "%+"):
        return JOBS[-1]
    if spec == "%-":
        return JOBS[-2] if len(JOBS) >= 2 else JOBS[-1]

    if spec.startswith("%"):
        try:
            jid = int(spec[1:])
            return next((j for j in JOBS if j["id"] == jid), None)
        except ValueError:
            return None

    # Busca por PID
    try:
        pid = int(spec)
        return next((j for j in JOBS if j["pid"] == pid), None)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Execução de comandos — fork/exec/wait
# ---------------------------------------------------------------------------

def _apply_redirections(redirs: dict) -> None:
    """
    Aplica redirecionamentos de I/O no processo filho usando dup2().

    dup2(oldfd, newfd) faz com que newfd passe a referenciar o mesmo
    arquivo que oldfd. Após isso, fechamos oldfd para não vazar descritores.

    Esta função é chamada dentro do fork() filho, antes do execvp().

    Parâmetros:
        redirs (dict): Dicionário gerado por _parse_redirections().
    """
    if "stdin" in redirs:
        fd = os.open(redirs["stdin"], os.O_RDONLY)
        os.dup2(fd, sys.stdin.fileno())   # stdin ← arquivo
        os.close(fd)

    if "stdout" in redirs:
        path, mode = redirs["stdout"]
        flags = os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if mode == "w" else os.O_APPEND)
        fd = os.open(path, flags, 0o644)
        os.dup2(fd, sys.stdout.fileno())  # stdout → arquivo
        os.close(fd)

    if "stderr" in redirs:
        path, _ = redirs["stderr"]
        if path == "&1":
            # 2>&1: redireciona stderr para onde stdout aponta agora
            os.dup2(sys.stdout.fileno(), sys.stderr.fileno())
        else:
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
            fd = os.open(path, flags, 0o644)
            os.dup2(fd, sys.stderr.fileno())  # stderr → arquivo
            os.close(fd)


def _exec_external(tokens: list[str], redirs: dict, background: bool) -> int:
    """
    Executa um comando externo usando fork() + execvp() + wait().

    Fluxo:
    1. fork() duplica o processo atual
    2. No filho: aplica redirecionamentos, define grupo de processos, execvp()
    3. No pai: aguarda o filho (wait) ou registra como job (background)

    Parâmetros:
        tokens     (list[str]): [comando, arg1, arg2, ...]
        redirs     (dict)     : Redirecionamentos de I/O
        background (bool)     : True se o comando deve rodar em background (&)

    Retorno:
        int: Código de saída do processo filho (0 se background).
    """
    global LAST_BG_PID

    pid = os.fork()

    if pid == 0:
        # ---------------------------------------------------------------
        # Processo Filho
        # ---------------------------------------------------------------
        # Cria um novo grupo de processos para este filho.
        # Isso é necessário para controle de jobs e para que Ctrl+C
        # não mate processos em background.
        os.setpgid(0, 0)

        if background:
            # Processo em background: desanexa do terminal
            # redireciona stdin para /dev/null se não houver outro redirecionamento
            if "stdin" not in redirs:
                fd = os.open("/dev/null", os.O_RDONLY)
                os.dup2(fd, sys.stdin.fileno())
                os.close(fd)

        # Aplica redirecionamentos de I/O usando dup2()
        try:
            _apply_redirections(redirs)
        except (FileNotFoundError, PermissionError, OSError) as e:
            print(f"shell: redirecionamento: {e}", file=sys.stderr)
            os._exit(1)

        # Substitui a imagem do processo pelo comando solicitado.
        # execvp() busca o executável no PATH, como o shell real.
        try:
            os.execvp(tokens[0], tokens)
        except FileNotFoundError:
            print(f"shell: {tokens[0]}: comando não encontrado", file=sys.stderr)
            os._exit(127)
        except PermissionError:
            print(f"shell: {tokens[0]}: permissão negada", file=sys.stderr)
            os._exit(126)
        except Exception as e:
            print(f"shell: erro ao executar '{tokens[0]}': {e}", file=sys.stderr)
            os._exit(1)

    else:
        # ---------------------------------------------------------------
        # Processo Pai
        # ---------------------------------------------------------------
        os.setpgid(pid, pid)   # garante que o grupo está criado antes do wait

        if background:
            # Não bloqueia; registra o job e retorna imediatamente
            job_id = _add_job(pid, " ".join(tokens))
            LAST_BG_PID = pid
            print(f"[{job_id}] {pid}")
            return 0
        else:
            # Foreground: dá o terminal ao grupo do filho (para Ctrl+C funcionar)
            try:
                os.tcsetpgrp(sys.stdin.fileno(), pid)
            except OSError:
                pass

            # Aguarda o filho terminar (bloqueante)
            _, status = os.waitpid(pid, 0)

            # Devolve o terminal ao shell
            try:
                os.tcsetpgrp(sys.stdin.fileno(), os.getpgrp())
            except OSError:
                pass

            # Extrai o código de saída real do status retornado por waitpid
            if os.WIFEXITED(status):
                return os.WEXITSTATUS(status)
            elif os.WIFSIGNALED(status):
                sig = os.WTERMSIG(status)
                if sig != signal.SIGINT:
                    print(f"\nshell: processo encerrado por sinal {sig}", file=sys.stderr)
                return 128 + sig
            elif os.WIFSTOPPED(status):
                # Ctrl+Z: processo parado → move para jobs
                sig = os.WSTOPSIG(status)
                job_id = _add_job(pid, " ".join(tokens))
                JOBS[-1]["status"] = "Stopped"
                print(f"\n[{job_id}]+ Stopped   {' '.join(tokens)}")
                return 148    # 128 + SIGTSTP
            return 0


def _exec_pipeline(segments: list[list[str]], background: bool) -> int:
    """
    Executa um pipeline de comandos conectados por pipes.

    Para cada par de comandos adjacentes, um pipe é criado com os.pipe().
    O stdout do comando N é conectado ao stdin do comando N+1 via dup2().

    Exemplo: ls -l | grep .py | wc -l
      - Processo 1 (ls -l): stdout → pipe[0→1]
      - Processo 2 (grep .py): stdin ← pipe[0], stdout → pipe[1→2]
      - Processo 3 (wc -l): stdin ← pipe[1], stdout → terminal

    Parâmetros:
        segments   (list[list[str]]): Lista de tokens por segmento do pipeline.
        background (bool)           : True se pipeline roda em background.

    Retorno:
        int: Código de saída do último processo do pipeline.
    """
    if len(segments) == 1:
        tokens, redirs = _parse_redirections(segments[0])
        if not tokens:
            return 0
        # Checa se é builtin antes de fazer fork
        if tokens[0] in ALIASES:
            tokens = _tokenize(ALIASES[tokens[0]]) + tokens[1:]
        if tokens and tokens[0] in BUILTINS:
            return _exec_builtin(tokens[0], tokens[1:], redirs)
        return _exec_external(tokens, redirs, background)

    # Pipeline com múltiplos comandos
    n = len(segments)
    pids: list[int] = []
    prev_read = -1    # fd de leitura do pipe anterior

    for i, seg in enumerate(segments):
        tokens, redirs = _parse_redirections(seg)
        if not tokens:
            if prev_read != -1:
                os.close(prev_read)
            continue

        # Expande alias
        if tokens[0] in ALIASES:
            tokens = _tokenize(ALIASES[tokens[0]]) + tokens[1:]

        is_last = (i == n - 1)

        if not is_last:
            # Cria pipe para conectar este processo ao próximo
            pipe_read, pipe_write = os.pipe()
        else:
            pipe_read, pipe_write = -1, -1

        pid = os.fork()

        if pid == 0:
            # ---- Filho ----
            os.setpgid(0, 0)

            # Conecta stdin ao pipe anterior (se houver)
            if prev_read != -1:
                os.dup2(prev_read, sys.stdin.fileno())
                os.close(prev_read)

            # Conecta stdout ao pipe seguinte (se não for o último)
            if not is_last:
                os.dup2(pipe_write, sys.stdout.fileno())
                os.close(pipe_write)
                if pipe_read != -1:
                    os.close(pipe_read)

            _apply_redirections(redirs)

            # Se for builtin, executa e sai
            if tokens[0] in BUILTINS:
                code = _exec_builtin(tokens[0], tokens[1:], redirs)
                os._exit(code)

            try:
                os.execvp(tokens[0], tokens)
            except FileNotFoundError:
                print(f"shell: {tokens[0]}: comando não encontrado", file=sys.stderr)
                os._exit(127)
            except PermissionError:
                print(f"shell: {tokens[0]}: permissão negada", file=sys.stderr)
                os._exit(126)
            except Exception as e:
                print(f"shell: erro ao executar '{tokens[0]}': {e}", file=sys.stderr)
                os._exit(1)

        else:
            # ---- Pai ----
            os.setpgid(pid, pid)
            pids.append(pid)

            # Fecha o fd do pipe anterior (filho já herdou)
            if prev_read != -1:
                os.close(prev_read)

            # Fecha a ponta de escrita (filho já herdou)
            if not is_last:
                os.close(pipe_write)
                prev_read = pipe_read
            else:
                prev_read = -1

    # Aguarda todos os processos do pipeline
    exit_code = 0
    for pid in pids:
        try:
            _, status = os.waitpid(pid, 0)
            if os.WIFEXITED(status):
                exit_code = os.WEXITSTATUS(status)
            elif os.WIFSIGNALED(status):
                exit_code = 128 + os.WTERMSIG(status)
        except ChildProcessError:
            pass

    return exit_code


# ---------------------------------------------------------------------------
# Comandos internos (builtins)
# ---------------------------------------------------------------------------

def _builtin_cd(args: list[str], _redirs: dict) -> int:
    """
    cd — muda o diretório de trabalho.

    Implementado como builtin porque uma chamada fork+exec não alteraria
    o diretório do processo pai (o shell). A mudança de diretório só tem
    efeito se executada no próprio processo do shell via os.chdir().

    Suporta:
    - cd          → vai para $HOME
    - cd ~        → vai para $HOME
    - cd -        → volta ao diretório anterior
    - cd /path    → vai para /path
    - cd ..       → sobe um nível
    """
    global PREV_DIR

    cur = os.getcwd()

    if not args or args[0] == "~":
        target = os.environ.get("HOME", "/")
    elif args[0] == "-":
        target = PREV_DIR
        print(target)   # bash exibe o novo diretório quando se usa cd -
    else:
        target = args[0]

    try:
        os.chdir(target)
        PREV_DIR = cur
        os.environ["PWD"] = os.getcwd()
        return 0
    except FileNotFoundError:
        print(f"shell: cd: {target}: Arquivo ou diretório não encontrado", file=sys.stderr)
        return 1
    except NotADirectoryError:
        print(f"shell: cd: {target}: Não é um diretório", file=sys.stderr)
        return 1
    except PermissionError:
        print(f"shell: cd: {target}: Permissão negada", file=sys.stderr)
        return 1


def _builtin_echo(args: list[str], redirs: dict) -> int:
    """
    echo — exibe texto na saída padrão.

    Flags:
    - -n  : não adiciona newline no final
    - -e  : interpreta sequências de escape (\\n, \\t, etc.)
    """
    no_newline = False
    interpret_escapes = False
    i = 0

    while i < len(args) and args[i].startswith("-"):
        flag = args[i]
        if "n" in flag:
            no_newline = True
        if "e" in flag:
            interpret_escapes = True
        if flag.lstrip("-") == "":
            break
        i += 1

    text = " ".join(args[i:])

    if interpret_escapes:
        text = text.encode().decode("unicode_escape")

    # Redireciona para arquivo se necessário
    out = sys.stdout
    file_opened = None
    if "stdout" in redirs:
        path, mode = redirs["stdout"]
        file_opened = open(path, mode)
        out = file_opened

    try:
        print(text, end="" if no_newline else "\n", file=out)
    finally:
        if file_opened:
            file_opened.close()

    return 0


def _builtin_export(args: list[str], _redirs: dict) -> int:
    """
    export — define ou lista variáveis de ambiente.

    export VAR=valor  : define VAR com valor
    export VAR        : exporta VAR (que já deve existir) para o ambiente
    export            : lista todas as variáveis exportadas
    """
    if not args:
        # Lista todas as variáveis de ambiente
        for key, val in sorted(os.environ.items()):
            print(f"declare -x {key}=\"{val}\"")
        return 0

    for arg in args:
        if "=" in arg:
            key, _, val = arg.partition("=")
            os.environ[key.strip()] = val
        else:
            # Apenas marca para export (variável já deve existir)
            if arg not in os.environ:
                print(f"shell: export: {arg}: não encontrada", file=sys.stderr)
    return 0


def _builtin_unset(args: list[str], _redirs: dict) -> int:
    """
    unset — remove variáveis de ambiente.

    unset VAR [VAR2 ...]
    """
    for var in args:
        try:
            del os.environ[var]
        except KeyError:
            pass   # Não é erro remover variável inexistente (bash também não reclamaria)
    return 0


def _builtin_env(args: list[str], _redirs: dict) -> int:
    """
    env — lista todas as variáveis de ambiente no formato VAR=valor.
    """
    for key, val in sorted(os.environ.items()):
        print(f"{key}={val}")
    return 0


def _builtin_pwd(_args: list[str], _redirs: dict) -> int:
    """
    pwd — imprime o diretório de trabalho atual.
    """
    print(os.getcwd())
    return 0


def _builtin_alias(args: list[str], _redirs: dict) -> int:
    """
    alias — define ou lista aliases.

    alias           : lista todos os aliases
    alias nome      : exibe o alias específico
    alias nome=cmd  : define um alias
    """
    if not args:
        for name, cmd in sorted(ALIASES.items()):
            print(f"alias {name}='{cmd}'")
        return 0

    for arg in args:
        if "=" in arg:
            name, _, cmd = arg.partition("=")
            ALIASES[name.strip()] = cmd.strip("'\"")
        else:
            if arg in ALIASES:
                print(f"alias {arg}='{ALIASES[arg]}'")
            else:
                print(f"shell: alias: {arg}: não encontrado", file=sys.stderr)
                return 1
    return 0


def _builtin_unalias(args: list[str], _redirs: dict) -> int:
    """
    unalias — remove aliases definidos.

    unalias nome [nome2 ...]
    unalias -a       : remove todos os aliases
    """
    if "-a" in args:
        ALIASES.clear()
        return 0
    for name in args:
        if name in ALIASES:
            del ALIASES[name]
        else:
            print(f"shell: unalias: {name}: não encontrado", file=sys.stderr)
            return 1
    return 0


def _builtin_history(args: list[str], _redirs: dict) -> int:
    """
    history — exibe o histórico de comandos.

    history [n]  : exibe os últimos n comandos
    history -c   : limpa o histórico
    history -w   : salva o histórico no arquivo
    """
    if "-c" in args:
        readline.clear_history()
        return 0
    if "-w" in args:
        readline.write_history_file(HIST_FILE)
        return 0

    n = readline.get_current_history_length()
    limit = n

    if args and args[0].lstrip("-").isdigit():
        limit = min(int(args[0]), n)

    start = n - limit
    for i in range(start, n):
        print(f"  {i + 1:4d}  {readline.get_history_item(i + 1)}")

    return 0


def _builtin_jobs(args: list[str], _redirs: dict) -> int:
    """
    jobs — lista jobs em background/stopped.

    jobs [-l]  : -l inclui PIDs
    """
    show_pid = "-l" in args
    for job in JOBS:
        marker = "+" if job == JOBS[-1] else "-" if len(JOBS) > 1 and job == JOBS[-2] else " "
        pid_str = f" {job['pid']}" if show_pid else ""
        print(f"[{job['id']}]{marker}{pid_str} {job['status']:<12} {job['command']}")
    return 0


def _builtin_fg(args: list[str], _redirs: dict) -> int:
    """
    fg [%n] — traz um job para o foreground.

    Envia SIGCONT para retomar um processo parado e transfere o terminal
    para o grupo de processos do job usando tcsetpgrp().
    """
    global LAST_EXIT_CODE

    spec = args[0] if args else "%+"
    job = _get_job(spec)

    if not job:
        print(f"shell: fg: {spec}: job não encontrado", file=sys.stderr)
        return 1

    pid = job["pid"]
    print(f"{job['command']}")

    try:
        os.tcsetpgrp(sys.stdin.fileno(), pid)
    except OSError:
        pass

    try:
        os.kill(pid, signal.SIGCONT)
        job["status"] = "Running"
        _, status = os.waitpid(pid, 0)
        JOBS.remove(job)
        LAST_EXIT_CODE = os.WEXITSTATUS(status) if os.WIFEXITED(status) else 1
    except ProcessLookupError:
        print(f"shell: fg: processo {pid} não existe mais", file=sys.stderr)
        JOBS.remove(job)
        return 1
    finally:
        try:
            os.tcsetpgrp(sys.stdin.fileno(), os.getpgrp())
        except OSError:
            pass

    return LAST_EXIT_CODE


def _builtin_bg(args: list[str], _redirs: dict) -> int:
    """
    bg [%n] — retoma um job parado em background.

    Envia SIGCONT para o grupo de processos do job sem transferir o terminal.
    """
    spec = args[0] if args else "%+"
    job = _get_job(spec)

    if not job:
        print(f"shell: bg: {spec}: job não encontrado", file=sys.stderr)
        return 1

    try:
        os.kill(-job["pid"], signal.SIGCONT)
        job["status"] = "Running"
        print(f"[{job['id']}]+ {job['command']} &")
        return 0
    except ProcessLookupError:
        print(f"shell: bg: processo {job['pid']} não existe mais", file=sys.stderr)
        JOBS.remove(job)
        return 1


def _builtin_kill(args: list[str], _redirs: dict) -> int:
    """
    kill [-signal] pid|%job — envia um sinal para um processo ou job.

    Sinais mais comuns:
    - SIGTERM (15, padrão): solicita encerramento gracioso
    - SIGKILL (9)         : força encerramento imediato (não pode ser ignorado)
    - SIGINT  (2)         : interrupção (equivalente a Ctrl+C)
    - SIGSTOP (19)        : pausa o processo
    - SIGCONT (18)        : retoma processo parado
    - SIGHUP  (1)         : hangup (geralmente reinicia o processo)

    Uso:
      kill %1          → envia SIGTERM ao job 1
      kill -9 1234     → envia SIGKILL ao PID 1234
      kill -SIGTERM %2 → envia SIGTERM ao job 2
      kill -l          → lista todos os sinais disponíveis
    """
    if not args or "-l" in args:
        # Lista sinais disponíveis
        sigs = [(s.name, s.value) for s in signal.Signals]
        for i, (name, val) in enumerate(sigs):
            print(f"  {val:2d}) {name:<12}", end="")
            if (i + 1) % 4 == 0:
                print()
        print()
        return 0

    sig_num = signal.SIGTERM
    i = 0

    if args[0].startswith("-"):
        sig_str = args[0][1:]
        i = 1
        try:
            sig_num = int(sig_str)
        except ValueError:
            sig_str = sig_str.upper()
            if not sig_str.startswith("SIG"):
                sig_str = "SIG" + sig_str
            try:
                sig_num = signal.Signals[sig_str].value
            except KeyError:
                print(f"shell: kill: {args[0]}: sinal inválido", file=sys.stderr)
                return 1

    for target in args[i:]:
        if target.startswith("%"):
            job = _get_job(target)
            if not job:
                print(f"shell: kill: {target}: job não encontrado", file=sys.stderr)
                return 1
            pid = -job["pid"]   # negativo → envia para o grupo inteiro
        else:
            try:
                pid = int(target)
            except ValueError:
                print(f"shell: kill: {target}: argumento inválido", file=sys.stderr)
                return 1

        try:
            os.kill(pid, sig_num)
        except ProcessLookupError:
            print(f"shell: kill: ({abs(pid)}) - Processo não existe", file=sys.stderr)
            return 1
        except PermissionError:
            print(f"shell: kill: ({abs(pid)}) - Permissão negada", file=sys.stderr)
            return 1

    return 0


def _builtin_type(args: list[str], _redirs: dict) -> int:
    """
    type — informa como um nome seria interpretado pelo shell.

    Verifica, na seguinte ordem:
    1. Builtin do shell
    2. Alias definido
    3. Executável no PATH
    """
    if not args:
        print("Uso: type nome [nome ...]", file=sys.stderr)
        return 1

    exit_code = 0
    for name in args:
        if name in BUILTINS:
            print(f"{name} é um builtin do shell")
        elif name in ALIASES:
            print(f"{name} é um alias para '{ALIASES[name]}'")
        else:
            found = False
            for directory in os.environ.get("PATH", "").split(":"):
                full = os.path.join(directory, name)
                if os.path.isfile(full) and os.access(full, os.X_OK):
                    print(f"{name} é {full}")
                    found = True
                    break
            if not found:
                print(f"shell: type: {name}: não encontrado", file=sys.stderr)
                exit_code = 1
    return exit_code


def _builtin_which(args: list[str], _redirs: dict) -> int:
    """
    which — localiza o caminho completo de um executável no PATH.
    """
    if not args:
        print("Uso: which comando [comando ...]", file=sys.stderr)
        return 1

    exit_code = 0
    for name in args:
        found = False
        for directory in os.environ.get("PATH", "").split(":"):
            full = os.path.join(directory, name)
            if os.path.isfile(full) and os.access(full, os.X_OK):
                print(full)
                found = True
                break
        if not found:
            print(f"shell: which: {name}: não encontrado", file=sys.stderr)
            exit_code = 1
    return exit_code


def _builtin_source(args: list[str], _redirs: dict) -> int:
    """
    source (ou .) — executa os comandos de um arquivo no contexto do shell atual.

    Diferente de executar o arquivo diretamente (que criaria um subshell),
    source roda cada linha no shell atual — por isso alterações de variáveis
    e aliases têm efeito no ambiente corrente.
    """
    if not args:
        print("Uso: source arquivo [args...]", file=sys.stderr)
        return 1

    path = args[0]
    try:
        with open(path) as f:
            lines = f.readlines()
    except FileNotFoundError:
        print(f"shell: source: {path}: arquivo não encontrado", file=sys.stderr)
        return 1
    except PermissionError:
        print(f"shell: source: {path}: permissão negada", file=sys.stderr)
        return 1

    last_code = 0
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        last_code = _executar_linha(line)
    return last_code


def _builtin_true(_args: list[str], _redirs: dict) -> int:
    """true — retorna sempre sucesso (código 0)."""
    return 0


def _builtin_false(_args: list[str], _redirs: dict) -> int:
    """false — retorna sempre falha (código 1)."""
    return 1


def _builtin_exit(args: list[str], _redirs: dict) -> int:
    """
    exit [código] — encerra o shell com o código fornecido (padrão: 0).
    """
    code = 0
    if args:
        try:
            code = int(args[0])
        except ValueError:
            print(f"shell: exit: {args[0]}: argumento numérico necessário", file=sys.stderr)
            code = 2
    readline.write_history_file(HIST_FILE)
    print("Saindo do shell...")
    sys.exit(code)


def _builtin_help(_args: list[str], _redirs: dict) -> int:
    """
    help — exibe referência de todos os recursos do shell.
    """
    print(f"""
{BOLD}{CYAN}╔══════════════════════════════════════════════════════════════════╗
║            ECM225 Shell — Referência de Comandos               ║
╚══════════════════════════════════════════════════════════════════╝{RESET}

{BOLD}{YELLOW}COMANDOS INTERNOS (BUILTINS){RESET}
  {GREEN}cd{RESET} [dir]            Muda o diretório de trabalho
                      cd -   → diretório anterior
                      cd     → $HOME
  {GREEN}pwd{RESET}                  Imprime o diretório atual
  {GREEN}echo{RESET} [-ne] [texto]   Exibe texto  (-n: sem newline  -e: interpreta escapes)
  {GREEN}export{RESET} [VAR=val]     Define ou lista variáveis de ambiente
  {GREEN}unset{RESET} VAR [...]      Remove variáveis de ambiente
  {GREEN}env{RESET}                  Lista todas as variáveis de ambiente
  {GREEN}alias{RESET} [nome=cmd]     Define ou lista aliases
  {GREEN}unalias{RESET} nome|{BOLD}-a{RESET}     Remove alias(es)
  {GREEN}history{RESET} [n|{BOLD}-c{RESET}|{BOLD}-w{RESET}]   Exibe, limpa ou salva o histórico
  {GREEN}type{RESET} nome [...]      Mostra como o shell interpretaria o nome
  {GREEN}which{RESET} cmd [...]      Localiza o executável no PATH
  {GREEN}source{RESET} arquivo       Executa arquivo no contexto atual (também: {GREEN}.{RESET})
  {GREEN}jobs{RESET} [{BOLD}-l{RESET}]           Lista jobs em background/stopped
  {GREEN}fg{RESET} [%n]             Traz job para foreground
  {GREEN}bg{RESET} [%n]             Retoma job parado em background
  {GREEN}kill{RESET} [-sig] pid|%n  Envia sinal para processo/job  (kill -l lista sinais)
  {GREEN}true{RESET} / {GREEN}false{RESET}         Retorna sempre sucesso / falha
  {GREEN}exit{RESET} [código]        Encerra o shell
  {GREEN}help{RESET}                 Este texto

{BOLD}{YELLOW}PIPES{RESET}
  cmd1 {BOLD}|{RESET} cmd2 {BOLD}|{RESET} cmd3    Conecta stdout de cmd1 ao stdin de cmd2, etc.
  Exemplo: {CYAN}ls -l | grep .py | wc -l{RESET}

{BOLD}{YELLOW}REDIRECIONAMENTOS{RESET}
  cmd {BOLD}>{RESET} arq            stdout → arquivo (sobrescreve)
  cmd {BOLD}>>{RESET} arq           stdout → arquivo (acrescenta)
  cmd {BOLD}<{RESET} arq            stdin  ← arquivo
  cmd {BOLD}2>{RESET} arq           stderr → arquivo
  cmd {BOLD}&>{RESET} arq           stdout + stderr → arquivo

{BOLD}{YELLOW}OPERADORES DE CONTROLE{RESET}
  cmd1 {BOLD}&&{RESET} cmd2          cmd2 só roda se cmd1 suceder (exit 0)
  cmd1 {BOLD}||{RESET} cmd2          cmd2 só roda se cmd1 falhar (exit ≠ 0)
  cmd1 {BOLD};{RESET}  cmd2          cmd2 sempre roda após cmd1
  cmd  {BOLD}&{RESET}               cmd roda em background

{BOLD}{YELLOW}EXPANSÕES{RESET}
  {BOLD}$VAR{RESET} ou {BOLD}${{VAR}}{RESET}    Expansão de variável de ambiente
  {BOLD}$?{RESET}               Código de saída do último comando
  {BOLD}$${RESET}               PID do shell atual
  {BOLD}$!{RESET}               PID do último processo em background
  {BOLD}$(cmd){RESET} ou {BOLD}`cmd`{RESET}  Substituição de comando
  {BOLD}*  ?  [...]{RESET}      Glob: expande padrões de arquivo

{BOLD}{YELLOW}ATALHOS DE TECLADO{RESET}
  ↑ / ↓            Navega no histórico de comandos
  Ctrl+R           Busca no histórico
  Tab              Completa comandos, arquivos e diretórios
  Ctrl+C           Interrompe o comando atual (SIGINT)
  Ctrl+Z           Pausa o comando atual (SIGTSTP) → use fg/bg para retomar
  Ctrl+D           Encerra o shell (EOF)
""")
    return 0


# ---------------------------------------------------------------------------
# Tabela de builtins
# ---------------------------------------------------------------------------

BUILTINS: dict = {
    "cd":      _builtin_cd,
    "pwd":     _builtin_pwd,
    "echo":    _builtin_echo,
    "export":  _builtin_export,
    "unset":   _builtin_unset,
    "env":     _builtin_env,
    "alias":   _builtin_alias,
    "unalias": _builtin_unalias,
    "history": _builtin_history,
    "type":    _builtin_type,
    "which":   _builtin_which,
    "source":  _builtin_source,
    ".":       _builtin_source,
    "jobs":    _builtin_jobs,
    "fg":      _builtin_fg,
    "bg":      _builtin_bg,
    "kill":    _builtin_kill,
    "true":    _builtin_true,
    "false":   _builtin_false,
    "exit":    _builtin_exit,
    "quit":    _builtin_exit,
    "help":    _builtin_help,
}


def _exec_builtin(name: str, args: list[str], redirs: dict) -> int:
    """
    Executa um builtin do shell.

    Parâmetros:
        name   (str)      : Nome do builtin.
        args   (list[str]): Argumentos (sem o nome do builtin).
        redirs (dict)     : Redirecionamentos de I/O.

    Retorno:
        int: Código de saída do builtin.
    """
    try:
        return BUILTINS[name](args, redirs)
    except SystemExit:
        raise   # deixa o exit() propagar
    except Exception as e:
        print(f"shell: {name}: erro inesperado: {e}", file=sys.stderr)
        return 1


# ---------------------------------------------------------------------------
# Motor de execução principal
# ---------------------------------------------------------------------------

def _executar_linha(linha: str) -> int:
    """
    Analisa e executa uma linha de comando completa.

    Fluxo de análise:
    1. Tokeniza a linha (respeitando aspas, variáveis e globs)
    2. Divide por operadores de controle (; && ||)
    3. Para cada segmento, verifica background (&)
    4. Divide por pipes (|)
    5. Verifica aliases e builtins
    6. Executa via fork+exec+wait

    Parâmetros:
        linha (str): Linha de comando digitada pelo usuário.

    Retorno:
        int: Código de saída do último comando executado.
    """
    global LAST_EXIT_CODE

    linha = linha.strip()
    if not linha or linha.startswith("#"):
        return 0

    # --- Tokenização ---
    tokens = _tokenize(linha)
    if not tokens:
        return 0

    # --- Divide por ; && || ---
    segments = _split_by_operator(tokens, {";", "&&", "||"})

    exit_code = 0
    for op, seg_tokens in segments:
        if not seg_tokens:
            continue

        # Avalia condições de execução
        if op == "&&" and exit_code != 0:
            continue   # cmd anterior falhou → pula
        if op == "||" and exit_code == 0:
            continue   # cmd anterior teve sucesso → pula

        # Detecta execução em background (último token é '&')
        background = False
        if seg_tokens[-1] == "&":
            background = True
            seg_tokens = seg_tokens[:-1]

        if not seg_tokens:
            continue

        # --- Divide por pipes ---
        pipe_segs = _split_pipes(seg_tokens)

        # --- Executa ---
        exit_code = _exec_pipeline(pipe_segs, background)
        LAST_EXIT_CODE = exit_code

    return exit_code


# ---------------------------------------------------------------------------
# Prompt do shell
# ---------------------------------------------------------------------------

def _gerar_prompt() -> str:
    """
    Gera a string do prompt colorida com:
    - usuário (verde se normal, vermelho se root)
    - hostname
    - diretório atual (~ para $HOME)
    - símbolo $ (normal) ou # (root)
    """
    try:
        user = os.environ.get("USER", os.getlogin())
    except OSError:
        user = "usuario"

    try:
        host = socket.gethostname().split(".")[0]
    except Exception:
        host = "localhost"

    home = os.environ.get("HOME", "")
    cwd = os.getcwd()
    if home and cwd.startswith(home):
        cwd = "~" + cwd[len(home):]

    is_root = os.getuid() == 0
    user_color = RED if is_root else GREEN
    symbol = "#" if is_root else "$"

    # Formato: usuario@host:diretório$
    # \001...\002 são necessários para o readline calcular o comprimento correto do prompt
    prompt = (
        f"\001{BOLD}{user_color}\002{user}"
        f"\001{RESET}\002@"
        f"\001{BOLD}{CYAN}\002{host}"
        f"\001{RESET}\002:"
        f"\001{BOLD}{BLUE}\002{cwd}"
        f"\001{RESET}{BOLD}\002{symbol} "
        f"\001{RESET}\002"
    )
    return prompt


# ---------------------------------------------------------------------------
# Tratamento de sinais
# ---------------------------------------------------------------------------

def _handler_sigint(_signum: int, _frame) -> None:
    """
    Handler para SIGINT (Ctrl+C).

    No shell interativo, Ctrl+C deve apenas cancelar a linha atual sem
    encerrar o shell. Imprimimos uma nova linha e o readline re-exibirá
    o prompt.
    """
    print()    # nova linha para não ficar "colado" no prompt
    # raise KeyboardInterrupt não é necessário; o readline já limpa a linha


def _handler_sigtstp(_signum: int, _frame) -> None:
    """
    Handler para SIGTSTP (Ctrl+Z).

    O shell em si não deve ser suspenso — apenas o processo filho em foreground.
    Este handler captura o sinal para evitar que o shell seja parado.
    """
    print(f"\n{YELLOW}shell: use 'jobs', 'fg' e 'bg' para gerenciar jobs{RESET}")


def _configurar_sinais() -> None:
    """
    Configura o tratamento de sinais para o processo do shell.

    O shell deve ignorar ou capturar sinais que normalmente afetariam
    processos em foreground, para continuar rodando mesmo quando o usuário
    pressiona Ctrl+C ou Ctrl+Z.
    """
    signal.signal(signal.SIGINT,  _handler_sigint)
    signal.signal(signal.SIGTSTP, _handler_sigtstp)
    signal.signal(signal.SIGTTOU, signal.SIG_IGN)   # ignora erros de tcsetpgrp
    signal.signal(signal.SIGTTIN, signal.SIG_IGN)   # ignora leitura de terminal em bg
    signal.signal(signal.SIGCHLD, signal.SIG_DFL)   # reaps padrão de filhos


# ---------------------------------------------------------------------------
# Função principal
# ---------------------------------------------------------------------------

def main() -> None:
    """
    Loop principal do shell (REPL: Read → Eval → Print → Loop).

    Etapas a cada iteração:
    1. Verifica jobs que terminaram (reap assíncrono)
    2. Exibe o prompt e lê o comando do usuário
    3. Adiciona ao histórico do readline
    4. Analisa e executa o comando
    5. Atualiza LAST_EXIT_CODE
    """
    _setup_readline()
    _configurar_sinais()

    # Aliases padrão (personalizáveis via ~/.pyshellrc no futuro)
    ALIASES.update({
        "ll":  "ls -la",
        "la":  "ls -A",
        "l":   "ls -CF",
        "cls": "clear",
        "..":  "cd ..",
        "...": "cd ../..",
    })

    print(f"{BOLD}{CYAN}ECM225 Shell{RESET} — Interpretador de Comandos em Python")
    print(f"PID do shell: {BOLD}{os.getpid()}{RESET}")
    print(f"Digite {BOLD}help{RESET} para ver todos os recursos disponíveis ou "
          f"{BOLD}exit{RESET} para sair.\n")

    while True:
        try:
            # Verifica e notifica sobre jobs encerrados
            _reap_jobs()

            # Lê comando (com histórico e tab completion via readline)
            linha = input(_gerar_prompt())

        except EOFError:
            # Ctrl+D: encerra o shell
            print("\nSaindo do shell...")
            readline.write_history_file(HIST_FILE)
            sys.exit(0)
        except KeyboardInterrupt:
            # Ctrl+C: cancela a linha atual, continua o loop
            print()
            continue

        linha = linha.strip()
        if not linha:
            continue

        # Adiciona ao histórico (readline.add_history evita duplicatas consecutivas)
        if (readline.get_current_history_length() == 0 or
                readline.get_history_item(readline.get_current_history_length()) != linha):
            readline.add_history(linha)

        # Executa a linha de comando
        try:
            _executar_linha(linha)
        except SystemExit:
            readline.write_history_file(HIST_FILE)
            raise
        except Exception as e:
            print(f"shell: erro interno: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
