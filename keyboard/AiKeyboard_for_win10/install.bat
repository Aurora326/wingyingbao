@ECHO OFF
bcdedit.exe /set nointegritychecks on
bcdedit.exe /set testsigning on
cd /d %~dp0
devcon.exe install AiKeyboard.inf AiKeyboard
PAUSE