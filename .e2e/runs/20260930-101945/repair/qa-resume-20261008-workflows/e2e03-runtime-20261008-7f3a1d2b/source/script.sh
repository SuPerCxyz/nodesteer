#!/bin/sh
set -eu
printf "shell_cwd=%s\n" "$(pwd)"
printf "uid=%s\n" "$(id -u)"
printf "username=%s\n" "$(id -un)"
printf "parameter=%s\n" "${NODESTEER_PARAM_qa_input-}"
printf "environment=%s\n" "${QA_E2E_MARKER-}"
python3 -c 'import os,pwd; print("python_cwd="+os.getcwd()); print("python_uid="+str(os.getuid())); print("python_username="+pwd.getpwuid(os.getuid()).pw_name)'
