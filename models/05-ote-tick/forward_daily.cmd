@echo off
rem Daily OTE forward journal. Output: results\forward_ote.md
cd /d "%~dp0"
node forward.js >> results\forward_task_out.txt 2>&1
