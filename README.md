#  Simple Shell in Python

A simple Linux-like command interpreter developed in Python using low-level system calls.

##  Description
This project was developed for the *Operating Systems* course (ECM225).  
It simulates the basic behavior of a Unix/Linux shell using Python's `os` module.

The shell allows execution of system commands by creating child processes and managing them using classic OS primitives.

---

##  Features
- Execute Linux commands (e.g., `ls`, `pwd`, `echo`, `cat`)
- Process creation with `fork()`
- Command execution using `execvp()`
- Parent process synchronization with `wait()`
- Built-in commands:
  - `help` → show usage instructions
  - `exit` / `quit` → terminate the shell

---

##  Limitations
- No support for pipes (`|`)
- No input/output redirection (`>`, `<`)
- No built-in `cd` command

---

##  How to Run

Make sure you are on a Unix-like system (Linux or macOS), then run:

```bash
python3 shell.py
