Set WshShell = CreateObject("WScript.Shell") 
WshShell.Run "mb_local_server.exe /uninstall", 0
WScript.quit
