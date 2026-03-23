"""
ECM225 - Sistemas Operacionais
Projeto: Interpretador de Comandos em Python

Integrantes:
  - João Victor Pereira Couto  24.00115-5

Descrição:
  Shell simples que simula o comportamento básico de um interpretador de
  comandos Linux. Utiliza as funções do módulo os para gerenciamento de
  processos (fork, execvp, wait, etc.).
"""

import os
import sys


def exibir_prompt():
    """Exibe o prompt do shell e retorna o comando digitado pelo usuário."""
    try:
        # Exibe o prompt e lê a entrada do usuário
        comando = input("shell> ")
        return comando
    except EOFError:
        # Ctrl+D: fim de arquivo, encerra o shell
        return None


def analisar_comando(comando_str):
    """
    Divide a string do comando em uma lista de tokens (comando + argumentos).

    Parâmetros:
        comando_str (str): A string digitada pelo usuário.

    Retorno:
        list: Lista com o comando e seus argumentos.
    """
    # split() divide a string pelos espaços, retornando uma lista de tokens
    tokens = comando_str.strip().split()
    return tokens


def exibir_ajuda():
    """Exibe a mensagem de ajuda com os comandos internos e dicas de uso."""
    print("""
Comandos internos disponíveis:
  help          Exibe esta mensagem de ajuda
  exit / quit   Encerra o shell

Uso:
  Digite qualquer comando Linux seguido de seus argumentos.
  Exemplos:
    shell> ls -l
    shell> echo "Olá, Mundo!"
    shell> cat arquivo.txt
    shell> pwd
    shell> ps aux

Observações:
  - Pipes (|) e redirecionamentos (>, <) não são suportados.
  - Comandos internos do bash (como cd) não são suportados.
""")


def executar_comando(tokens):
    """
    Cria um processo filho com fork() e executa o comando com execvp() dentro dele.
    O processo pai aguarda o término do filho com wait().

    Parâmetros:
        tokens (list): Lista com o comando e seus argumentos.

    Retorno:
        int: Código de saída do comando executado pelo filho.
    """
    # Cria um novo processo filho duplicando o processo atual
    pid = os.fork()

    if pid == 0:
        # --- Processo Filho ---
        # O filho substitui sua imagem pelo comando solicitado usando execvp.
        # execvp procura o executável no PATH do sistema, assim como o shell real.
        try:
            os.execvp(tokens[0], tokens)
        except FileNotFoundError:
            # O comando não foi encontrado no PATH
            print(f"shell: {tokens[0]}: comando não encontrado", file=sys.stderr)
            os._exit(127)  # Código de saída 127 = comando não encontrado
        except PermissionError:
            # O arquivo existe mas não tem permissão de execução
            print(f"shell: {tokens[0]}: permissão negada", file=sys.stderr)
            os._exit(126)  # Código de saída 126 = permissão negada
        except Exception as e:
            # Outros erros ao tentar executar o comando
            print(f"shell: erro ao executar '{tokens[0]}': {e}", file=sys.stderr)
            os._exit(1)
    else:
        # --- Processo Pai ---
        # O pai aguarda o processo filho terminar antes de exibir o próximo prompt
        pid_filho, status = os.wait()

        # os.wait() retorna o status codificado; precisamos extrair o código de saída real.
        # WIFEXITED verifica se o filho terminou normalmente (não por sinal)
        if os.WIFEXITED(status):
            codigo_saida = os.WEXITSTATUS(status)
        else:
            # O filho foi encerrado por um sinal
            codigo_saida = -1

        # Exibe mensagem de erro se o comando falhou (código de saída diferente de 0)
        if codigo_saida != 0 and codigo_saida != 127 and codigo_saida != 126:
            print(f"shell: '{tokens[0]}' encerrou com código de erro {codigo_saida}",
                  file=sys.stderr)

        return codigo_saida


def main():
    """
    Função principal do shell.
    Exibe o prompt em loop, lê comandos, verifica saída e executa.
    """
    print("Shell simples em Python - ECM225")
    print("Digite 'help' para ver os comandos disponíveis ou 'exit' para sair.\n")

    while True:
        # 1. Leitura do comando
        comando_str = exibir_prompt()

        # Caso o usuário pressione Ctrl+D (EOF), encerra o shell
        if comando_str is None:
            print("\nSaindo do shell...")
            sys.exit(0)

        # Ignora linhas vazias (usuário apenas pressionou Enter)
        if not comando_str.strip():
            continue

        # 2. Análise/tokenização do comando
        tokens = analisar_comando(comando_str)

        # 3. Verifica comandos internos do shell
        if tokens[0] in ("exit", "quit"):
            print("Saindo do shell...")
            sys.exit(0)

        if tokens[0] == "help":
            exibir_ajuda()
            continue

        # 4. Execução do comando via fork + execvp + wait
        executar_comando(tokens)


if __name__ == "__main__":
    main()
