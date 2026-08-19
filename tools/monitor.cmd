@echo off
REM Live AgOps board. Double-click it, or run it from a terminal, and leave the
REM window open for the whole work session. Ctrl-C stops it.
REM
REM Why this exists as a .cmd rather than a Claude slash command: a slash
REM command cannot hold a live view. A Claude session runs a command, gets its
REM output and returns -- it has nowhere to keep repainting. The live board
REM needs a real terminal, so here it is.
REM
REM Optional argument: refresh seconds (default 2).
title AgOps monitor
cd /d "%~dp0.."
py tools\agops.py monitor --watch %1
if errorlevel 1 pause
